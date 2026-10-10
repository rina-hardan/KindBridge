"""Aggregate roots. Each stream has one aggregate id; version is the concurrency token."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4, uuid5

from app.domain import events as ev
from app.domain.errors import ConcurrencyConflict, DomainError
from app.domain.events import DomainEvent

AGGREGATE_TYPES = ("User", "HelpRequest", "VolunteerProfile", "ExemptionLink")


def _apply_method_name(event_type: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(event_type):
        if char.isupper() and index:
            chars.append("_")
        chars.append(char.lower())
    return "apply_" + "".join(chars)


class AggregateRoot:
    """Base aggregate. Subclasses decide; they record events instead of mutating SQL."""

    aggregate_type: ClassVar[str] = ""

    def __init__(self, aggregate_id: UUID | None = None) -> None:
        if not self.aggregate_type:
            raise TypeError(f"{type(self).__name__} must define aggregate_type")
        self.aggregate_id = aggregate_id or uuid4()
        self.version = 0
        self._uncommitted: list[DomainEvent] = []

    @property
    def expected_version(self) -> int:
        """Version the store must still be at when the uncommitted events are appended."""
        return self.version - len(self._uncommitted)

    def uncommitted_events(self) -> list[DomainEvent]:
        return list(self._uncommitted)

    def mark_committed(self) -> None:
        self._uncommitted.clear()

    def raise_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            aggregate_id=self.aggregate_id,
            aggregate_type=self.aggregate_type,
            event_type=event_type,
            payload=payload,
            version=self.version + 1,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        self._apply_new(event)
        return event

    @classmethod
    def load(cls, aggregate_id: UUID, history: Sequence[DomainEvent]) -> "AggregateRoot":
        aggregate = cls(aggregate_id)
        for event in history:
            if event.aggregate_id != aggregate.aggregate_id:
                raise ValueError("history contains an event for a different aggregate")
            if event.aggregate_type != aggregate.aggregate_type:
                raise ValueError("history contains an event for a different aggregate type")
            if event.version != aggregate.version + 1:
                raise ValueError(
                    f"history version {event.version} does not follow {aggregate.version}"
                )
            aggregate._apply(event)
            aggregate.version = event.version
        return aggregate

    def _apply_new(self, event: DomainEvent) -> None:
        self._apply(event)
        self._uncommitted.append(event)
        self.version = event.version

    def _apply(self, event: DomainEvent) -> None:
        handler = getattr(self, _apply_method_name(event.event_type), None)
        if handler is not None:
            handler(event)
            return
        self.apply(event)

    def apply(self, event: DomainEvent) -> None:
        """Fallback when a subclass has no ``apply_<event_type>`` method."""
        return None


class User(AggregateRoot):
    aggregate_type = "User"


OPEN_REQUEST_STATUSES = ("PENDING_REVIEW", "MATCH_PROPOSED", "NO_MATCH", "ASSIGNED")
_OVERRIDE_STATUSES = ("MATCH_PROPOSED", "NO_MATCH")


class ProposalState:
    """One proposed volunteer still tracked on the help-request stream."""

    def __init__(self, assignment_id: UUID, volunteer_id: UUID, status: str) -> None:
        self.assignment_id = assignment_id
        self.volunteer_id = volunteer_id
        self.status = status


class HelpRequest(AggregateRoot):
    """Help-request stream. Propose is idempotent per ``match_attempt``."""

    aggregate_type = "HelpRequest"

    def __init__(self, aggregate_id: UUID | None = None) -> None:
        super().__init__(aggregate_id)
        self.status = ""
        self.requester_id: UUID | None = None
        self.match_attempt = 0
        self.concurrency_type = "UNKNOWN"
        self.completed_attempts: set[int] = set()
        self.proposals: list[ProposalState] = []
        self.assigned_volunteer_id: UUID | None = None
        self.assigned_assignment_id: UUID | None = None

    def submit(self, payload: Mapping[str, Any], now: datetime) -> None:
        if self.version != 0:
            raise DomainError("Help request already exists")
        self._record(ev.HELP_REQUEST_CREATED, payload, now)

    def restore_lifecycle(self, history: Sequence[DomainEvent]) -> None:
        """Set ``status`` from the stream. Match attempts stay with the propose path."""
        self.status = _lifecycle_status(history)

    def apply_help_request_created(self, event: DomainEvent) -> None:
        raw = event.payload.get("requester_id")
        self.requester_id = UUID(str(raw)) if raw else None
        self.status = "PENDING_REVIEW"
        self.concurrency_type = str(event.payload.get("concurrency_type") or "UNKNOWN")
        if event.payload.get("match_attempt") is not None:
            self.match_attempt = int(event.payload["match_attempt"])

    def apply_help_request_cancelled(self, _event: DomainEvent) -> None:
        self.status = "CANCELLED"

    def _record(self, event_type: str, payload: Mapping[str, Any], now: datetime) -> None:
        event = DomainEvent(
            aggregate_id=self.aggregate_id,
            aggregate_type=self.aggregate_type,
            event_type=event_type,
            payload=dict(payload),
            version=self.version + 1,
            created_at=now,
        )
        self._apply_new(event)

    def already_proposed(self, match_attempt: int) -> bool:
        return match_attempt in self.completed_attempts

    def restore(self, history: Sequence[DomainEvent]) -> None:
        """Fold the stream into decision state. Safe to call again on the same history."""
        self.status = ""
        self.match_attempt = 0
        self.concurrency_type = "UNKNOWN"
        self.completed_attempts = set()
        self.requester_id = None
        self.proposals = []
        self.assigned_volunteer_id = None
        self.assigned_assignment_id = None
        for event in history:
            self._fold(event)

    def record_match(self, match_attempt: int, payloads: list[tuple[str, dict[str, Any]]]) -> None:
        """Append classification and MatchesProposed or NoMatchFound for this attempt.

        A repeated attempt is a no-op. The caller commits the uncommitted events.
        """
        if match_attempt in self.completed_attempts:
            return
        if self.status != "PENDING_REVIEW":
            raise DomainError("Help request is not pending review")
        if match_attempt != self.match_attempt:
            raise ConcurrencyConflict(
                f"match_attempt {match_attempt} does not match the stream head {self.match_attempt}"
            )
        for event_type, payload in payloads:
            self._fold(self.raise_event(event_type, payload))

    def approve(self, assignment_id: UUID, approved_by: UUID) -> UUID:
        """Choose one current proposal. Returns that proposal's volunteer profile id."""
        if self.status != "MATCH_PROPOSED":
            raise DomainError("Help request is not waiting for approval")
        chosen = next(
            (
                item
                for item in self.proposals
                if item.assignment_id == assignment_id and item.status == "PROPOSED"
            ),
            None,
        )
        if chosen is None:
            raise DomainError("Choose one proposed volunteer")
        self._fold(
            self.raise_event(
                "AssignmentApproved",
                {
                    "assignment_id": str(assignment_id),
                    "volunteer_id": str(chosen.volunteer_id),
                    "approved_by": str(approved_by),
                },
            )
        )
        return chosen.volunteer_id

    def reject_proposals(self, reason: str) -> None:
        if self.status != "MATCH_PROPOSED":
            raise DomainError("Help request is not waiting for approval")
        volunteer_ids = [str(item.volunteer_id) for item in self.proposals if item.status == "PROPOSED"]
        if not volunteer_ids:
            raise DomainError("There are no proposals to reject")
        self._fold(
            self.raise_event(
                "AssignmentsRejected",
                {"volunteer_ids": volunteer_ids, "reason": reason},
            )
        )

    def override(self, assignment_id: UUID, volunteer_id: UUID, reason: str, approved_by: UUID) -> None:
        if self.status not in _OVERRIDE_STATUSES:
            raise DomainError("Override is only available when matches were proposed or none were found")
        self._fold(
            self.raise_event(
                "AssignmentOverridden",
                {
                    "assignment_id": str(assignment_id),
                    "volunteer_id": str(volunteer_id),
                    "override_reason": reason,
                    "approved_by": str(approved_by),
                    "match_attempt": self.match_attempt,
                },
            )
        )

    def retrigger(self) -> None:
        if self.status not in _OVERRIDE_STATUSES:
            raise DomainError("Match can only be retriggered when matches were proposed or none were found")
        self._fold(self.raise_event("MatchRetriggered", {}))

    def complete(self, assignment_id: UUID) -> None:
        """The assigned volunteer finished the task. The request becomes terminal."""
        self._require_assigned(assignment_id)
        self._fold(self.raise_event("TaskCompleted", {"assignment_id": str(assignment_id)}))

    def release(self, assignment_id: UUID, reason: str) -> None:
        """The assigned volunteer cannot perform the task. It returns to review and they are declined on it."""
        self._require_assigned(assignment_id)
        self._fold(
            self.raise_event(
                "TaskReleased",
                {"assignment_id": str(assignment_id), "reason": reason},
            )
        )

    def _require_assigned(self, assignment_id: UUID) -> None:
        if self.status != "ASSIGNED" or self.assigned_assignment_id != assignment_id:
            raise DomainError("This task is not assigned")

    def cancel(self, cancelled_by: UUID, reason: str, now: datetime | None = None) -> None:
        if self.status not in OPEN_REQUEST_STATUSES:
            raise DomainError("This request can no longer be cancelled")
        payload = {"cancelled_by": str(cancelled_by), "reason": reason}
        if now is None:
            self._fold(self.raise_event("HelpRequestCancelled", payload))
            return
        self._record(ev.HELP_REQUEST_CANCELLED, payload, now)
        self._fold(self.uncommitted_events()[-1])

    def _fold(self, event: DomainEvent) -> None:
        payload = event.payload
        kind = event.event_type
        if kind == "HelpRequestCreated":
            self.status = "PENDING_REVIEW"
            self.concurrency_type = str(payload.get("concurrency_type") or "UNKNOWN")
            requester = payload.get("requester_id")
            if requester:
                self.requester_id = UUID(str(requester))
            if payload.get("match_attempt") is not None:
                self.match_attempt = int(payload["match_attempt"])
            return
        if kind == "ConcurrencyClassified" and payload.get("concurrency_type"):
            self.concurrency_type = str(payload["concurrency_type"])
            return
        if kind in ("MatchesProposed", "NoMatchFound"):
            attempt = int(payload["match_attempt"])
            self.completed_attempts.add(attempt)
            self.match_attempt = attempt + 1
            if kind == "MatchesProposed":
                self.status = "MATCH_PROPOSED"
                self.proposals = _proposals_from(payload)
            else:
                self.status = "NO_MATCH"
                self.proposals = []
            return
        if kind == "MatchRetriggered":
            self.status = "PENDING_REVIEW"
            _supersede_open(self.proposals)
            return
        if kind == "AssignmentsRejected":
            self.status = "PENDING_REVIEW"
            declined = {str(item) for item in payload.get("volunteer_ids") or []}
            for proposal in self.proposals:
                if proposal.status == "PROPOSED" and str(proposal.volunteer_id) in declined:
                    proposal.status = "DECLINED"
            return
        if kind == "AssignmentApproved":
            self.status = "ASSIGNED"
            chosen = UUID(str(payload["assignment_id"]))
            self.assigned_volunteer_id = UUID(str(payload["volunteer_id"]))
            self.assigned_assignment_id = chosen
            for proposal in self.proposals:
                if proposal.assignment_id == chosen:
                    proposal.status = "ASSIGNED"
                elif proposal.status == "PROPOSED":
                    proposal.status = "SUPERSEDED"
            return
        if kind == "AssignmentOverridden":
            self.status = "ASSIGNED"
            self.assigned_volunteer_id = UUID(str(payload["volunteer_id"]))
            self.assigned_assignment_id = UUID(str(payload["assignment_id"]))
            _supersede_open(self.proposals)
            return
        if kind == "TaskCompleted":
            self.status = "COMPLETED"
            finished = UUID(str(payload["assignment_id"]))
            for proposal in self.proposals:
                if proposal.assignment_id == finished:
                    proposal.status = "COMPLETED"
            self.assigned_volunteer_id = None
            self.assigned_assignment_id = None
            return
        if kind == "TaskReleased":
            self.status = "PENDING_REVIEW"
            released = UUID(str(payload["assignment_id"]))
            for proposal in self.proposals:
                if proposal.assignment_id == released:
                    proposal.status = "DECLINED"
            self.assigned_volunteer_id = None
            self.assigned_assignment_id = None
            return
        if kind == "HelpRequestCancelled":
            self.status = "CANCELLED"
            _supersede_open(self.proposals)
            self.assigned_volunteer_id = None
            self.assigned_assignment_id = None


def _proposals_from(payload: Mapping[str, Any]) -> list[ProposalState]:
    items = payload.get("proposals") or payload.get("matches") or []
    found: list[ProposalState] = []
    if not isinstance(items, list):
        return found
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_assignment = item.get("assignment_id")
        raw_volunteer = item.get("volunteer_id")
        if not raw_assignment or not raw_volunteer:
            continue
        found.append(
            ProposalState(UUID(str(raw_assignment)), UUID(str(raw_volunteer)), "PROPOSED")
        )
    return found


def _supersede_open(proposals: list[ProposalState]) -> None:
    for proposal in proposals:
        if proposal.status == "PROPOSED":
            proposal.status = "SUPERSEDED"


def _lifecycle_status(history: Sequence[DomainEvent]) -> str:
    status = ""
    for event in history:
        kind = event.event_type
        if kind == ev.HELP_REQUEST_CREATED:
            status = "PENDING_REVIEW"
        elif kind == ev.MATCHES_PROPOSED:
            status = "MATCH_PROPOSED"
        elif kind == ev.NO_MATCH_FOUND:
            status = "NO_MATCH"
        elif kind in ("MatchRetriggered", "AssignmentsRejected", "TaskReleased"):
            status = "PENDING_REVIEW"
        elif kind in ("AssignmentApproved", "AssignmentOverridden"):
            status = "ASSIGNED"
        elif kind == "TaskCompleted":
            status = "COMPLETED"
        elif kind == ev.HELP_REQUEST_CANCELLED:
            status = "CANCELLED"
    return status


class VolunteerProfile(AggregateRoot):
    aggregate_type = "VolunteerProfile"


class ExemptionLink(AggregateRoot):
    """One stream per volunteer/requester pair. The id is uuid5 of that pair."""

    aggregate_type = "ExemptionLink"
    _NAMESPACE = UUID("6b1e1c4a-9a3e-4f0d-9c2a-1b7e5d0a8f31")

    @staticmethod
    def stream_id(volunteer_user_id: UUID, requester_id: UUID) -> UUID:
        return uuid5(ExemptionLink._NAMESPACE, f"{volunteer_user_id}:{requester_id}")


class UserAggregate:
    """Decides which events a user registration or login produces."""

    @staticmethod
    def register(
        user_id: UUID,
        email: str,
        full_name: str,
        phone_encrypted: str,
        city: str,
        home_address: str,
        password_hash: str,
        now: datetime,
    ) -> list[DomainEvent]:
        payloads: list[tuple[str, dict[str, Any]]] = [
            (
                ev.USER_REGISTERED,
                {
                    "email": email,
                    "full_name": full_name,
                    "phone_encrypted": phone_encrypted,
                    "city": city,
                    "home_address": home_address,
                },
            ),
            (ev.CREDENTIAL_SET, {"password_hash": password_hash}),
        ]
        return UserAggregate._number(user_id, payloads, start_version=1, now=now)

    @staticmethod
    def record(
        user_id: UUID,
        current_version: int,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> DomainEvent:
        return UserAggregate._number(user_id, [(event_type, payload)], current_version + 1, now)[0]

    @staticmethod
    def bootstrap_admin(
        user_id: UUID,
        email: str,
        full_name: str,
        phone_encrypted: str,
        password_hash: str,
        now: datetime,
    ) -> list[DomainEvent]:
        payloads = [
            (
                ev.USER_REGISTERED,
                {
                    "email": email,
                    "full_name": full_name,
                    "phone_encrypted": phone_encrypted,
                    "city": None,
                    "home_address": None,
                },
            ),
            (ev.CREDENTIAL_SET, {"password_hash": password_hash}),
            (ev.ADMIN_BOOTSTRAPPED, {}),
        ]
        return UserAggregate._number(user_id, payloads, start_version=1, now=now)

    @staticmethod
    def logged_in(user_id: UUID, current_version: int, now: datetime) -> DomainEvent:
        return UserAggregate._number(user_id, [(ev.USER_LOGGED_IN, {})], current_version + 1, now)[0]

    @staticmethod
    def _number(
        user_id: UUID,
        payloads: list[tuple[str, dict[str, Any]]],
        start_version: int,
        now: datetime,
    ) -> list[DomainEvent]:
        return [
            DomainEvent(
                aggregate_id=user_id,
                aggregate_type=ev.USER_AGGREGATE,
                event_type=event_type,
                payload=payload,
                version=start_version + offset,
                created_at=now,
            )
            for offset, (event_type, payload) in enumerate(payloads)
        ]
