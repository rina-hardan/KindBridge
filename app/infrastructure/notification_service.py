"""Post-commit notifications: resolve recipients from the database, build the mail, send it.

Commands call this only after their events are committed. Nothing here raises into a
command, and every address comes from the ``users`` projection (never from a request body).
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import Engine

from app.domain.events import MATCHES_PROPOSED, NO_MATCH_FOUND
from app.infrastructure import notifications as mails
from app.infrastructure.notifier import Notifier
from app.repositories.matching import SqlMatchingReader
from app.repositories.users import Contact, UserRepository

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:5000"
_LANG = "he"
_REASON_LIMIT = 200

Runner = Callable[[Callable[[], None]], Any]


class Notifications(Protocol):
    def assignment_made(self, request_id: UUID, volunteer_profile_id: UUID) -> None: ...

    def cancelled_while_assigned(self, request_id: UUID, volunteer_profile_id: UUID) -> None: ...

    def task_released(self, request_id: UUID, volunteer_profile_id: UUID, reason: str | None) -> None: ...

    def match_outcome(self, request_id: UUID, event_type: str, payload: dict[str, Any]) -> None: ...


class NoNotifications:
    """Default for handlers built without a notifier (existing tests)."""

    def assignment_made(self, request_id: UUID, volunteer_profile_id: UUID) -> None:
        return None

    def cancelled_while_assigned(self, request_id: UUID, volunteer_profile_id: UUID) -> None:
        return None

    def task_released(self, request_id: UUID, volunteer_profile_id: UUID, reason: str | None) -> None:
        return None

    def match_outcome(self, request_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        return None


def inline_runner(job: Callable[[], None]) -> None:
    job()


def background_runner() -> Runner:
    """One worker thread, so a slow Gmail call never holds up an HTTP response or the agent poll."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notify")
    return pool.submit


class NotificationService:
    def __init__(
        self,
        engine: Engine,
        notifier: Notifier,
        users: UserRepository,
        reader: SqlMatchingReader,
        *,
        admin_notify_email: str = "",
        base_url: str = DEFAULT_BASE_URL,
        run: Runner = inline_runner,
    ) -> None:
        self._engine = engine
        self._notifier = notifier
        self._users = users
        self._reader = reader
        self._admin_email = admin_notify_email.strip()
        self._base_url = base_url.rstrip("/")
        self._run = run

    # --- called by the command side, after commit -------------------------------------------

    def assignment_made(self, request_id: UUID, volunteer_profile_id: UUID) -> None:
        """AssignmentApproved and AssignmentOverridden: the volunteer and the requester."""
        self._submit("assignment_made", lambda: self._assignment_made(request_id, volunteer_profile_id))

    def cancelled_while_assigned(self, request_id: UUID, volunteer_profile_id: UUID) -> None:
        self._submit("cancelled", lambda: self._cancelled(request_id, volunteer_profile_id))

    def task_released(self, request_id: UUID, volunteer_profile_id: UUID, reason: str | None) -> None:
        """TaskReleased: the requester and the admin(s). Called by ReleaseTaskCommand after commit."""
        self._submit("task_released", lambda: self._task_released(request_id, volunteer_profile_id, reason))

    def match_outcome(self, request_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        """MatchesProposed or NoMatchFound, appended by the agent: the admin(s)."""
        self._submit("match_outcome", lambda: self._match_outcome(request_id, event_type, payload))

    # --- jobs ---------------------------------------------------------------------------------

    def _assignment_made(self, request_id: UUID, profile_id: UUID) -> None:
        with self._engine.connect() as conn:
            view = self._reader.load_request(conn, request_id)
            if view is None:
                return
            volunteer = self._users.volunteer_contact(conn, profile_id)
            requester = self._users.contact(conn, view.requester_id)
        info = _info(view)
        if volunteer is not None:
            mail = mails.assigned_to_volunteer(info, volunteer.full_name, f"{self._base_url}/me/tasks", _LANG)
            self._send(volunteer, mail)
        if requester is not None:
            mail = mails.assigned_to_requester(
                info,
                requester.full_name,
                volunteer.full_name if volunteer is not None else "",
                self._request_link(request_id),
                _LANG,
            )
            self._send(requester, mail)

    def _cancelled(self, request_id: UUID, profile_id: UUID) -> None:
        with self._engine.connect() as conn:
            view = self._reader.load_request(conn, request_id)
            if view is None:
                return
            volunteer = self._users.volunteer_contact(conn, profile_id)
        if volunteer is not None:
            self._send(volunteer, mails.cancelled_to_volunteer(_info(view), volunteer.full_name, _LANG))

    def _task_released(self, request_id: UUID, profile_id: UUID, reason: str | None) -> None:
        with self._engine.connect() as conn:
            view = self._reader.load_request(conn, request_id)
            if view is None:
                return
            volunteer = self._users.volunteer_contact(conn, profile_id)
            requester = self._users.contact(conn, view.requester_id)
            admins = self._admins(conn)
        info = _info(view)
        link = self._request_link(request_id)
        if requester is not None:
            self._send(requester, mails.released_to_requester(info, requester.full_name, link, _LANG))
        volunteer_name = volunteer.full_name if volunteer is not None else ""
        for admin in admins:
            self._send(admin, mails.released_to_admin(info, volunteer_name, reason, link, _LANG))

    def _match_outcome(self, request_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        if event_type not in (MATCHES_PROPOSED, NO_MATCH_FOUND):
            return
        with self._engine.connect() as conn:
            view = self._reader.load_request(conn, request_id)
            if view is None:
                return
            admins = self._admins(conn)
            candidates: list[mails.Candidate] = []
            if event_type == MATCHES_PROPOSED:
                candidates = self._candidates(conn, payload)
        info = _info(view)
        link = self._request_link(request_id)
        if event_type == MATCHES_PROPOSED:
            mail = mails.matches_proposed_to_admin(info, candidates, link, _LANG)
        else:
            summary = payload.get("rejection_summary")
            mail = mails.no_match_to_admin(info, summary if isinstance(summary, dict) else {}, link, _LANG)
        for admin in admins:
            self._send(admin, mail)

    # --- helpers ------------------------------------------------------------------------------

    def _candidates(self, conn, payload: dict[str, Any]) -> list[mails.Candidate]:
        proposals = [item for item in payload.get("proposals") or [] if isinstance(item, dict)]
        proposals.sort(key=lambda item: int(item.get("rank") or 0))
        ids = [UUID(str(item["volunteer_id"])) for item in proposals if item.get("volunteer_id")]
        names = self._users.volunteer_names(conn, ids)
        return [
            mails.Candidate(
                name=names.get(UUID(str(item["volunteer_id"])), "-"),
                score=float(item.get("score") or 0),
            )
            for item in proposals
            if item.get("volunteer_id")
        ]

    def _admins(self, conn) -> list[Contact]:
        if self._admin_email:
            chosen = self._users.admin_contact_by_email(conn, self._admin_email)
            if chosen is not None:
                return [chosen]
            logger.warning("admin_notify_email_is_not_an_active_admin; notifying every active admin instead")
        return self._users.active_admin_contacts(conn)

    def _request_link(self, request_id: UUID) -> str:
        return f"{self._base_url}/requests/{request_id}"

    def _send(self, contact: Contact, mail: mails.MailContent) -> None:
        try:
            self._notifier.send(contact.email, mail.subject, mail.body)
        except Exception as exc:
            logger.error("notify_failed step=send error=%s", _reason(exc))

    def _submit(self, step: str, job: Callable[[], None]) -> None:
        def guarded() -> None:
            try:
                job()
            except Exception as exc:
                logger.error("notify_failed step=%s error=%s", step, _reason(exc))

        try:
            self._run(guarded)
        except Exception as exc:
            logger.error("notify_failed step=%s error=%s", step, _reason(exc))


def _info(view) -> mails.RequestInfo:
    return mails.RequestInfo(
        request_id=str(view.request_id),
        category=view.category,
        city=view.city,
        urgency=view.urgency,
        preferred_date=view.slot.preferred_date,
    )


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:_REASON_LIMIT]
