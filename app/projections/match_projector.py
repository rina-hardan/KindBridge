"""Projects help-request match events onto help_requests and task_assignments."""

import json
from collections.abc import Sequence
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, case, insert, select, update

from app.domain.events import DomainEvent
from app.repositories.tables import help_requests, task_assignments, volunteer_profiles


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class MatchProjector:
    def __init__(self) -> None:
        self._handlers = {
            "HelpRequestCreated": self._on_created,
            "ConcurrencyClassified": self._on_classified,
            "MatchesProposed": self._on_proposed,
            "NoMatchFound": self._on_no_match,
            "MatchRetriggered": self._on_retriggered,
            "AssignmentApproved": self._on_approved,
            "AssignmentsRejected": self._on_rejected,
            "AssignmentOverridden": self._on_overridden,
            "HelpRequestCancelled": self._on_cancelled,
        }

    def __call__(self, events: Sequence[DomainEvent], connection: Any) -> None:
        self.apply(events, connection)

    def apply(self, events: Sequence[DomainEvent], connection: Connection) -> None:
        for event in events:
            handler = self._handlers.get(event.event_type)
            if handler is not None:
                handler(connection, event)

    def _on_created(self, conn: Connection, event: DomainEvent) -> None:
        payload = event.payload
        skills = payload.get("required_skills", [])
        if isinstance(skills, str):
            skills_json = skills
        else:
            skills_json = json.dumps(list(skills), ensure_ascii=False)
        conn.execute(
            insert(help_requests).values(
                id=event.aggregate_id,
                requester_id=_uuid(payload["requester_id"]),
                series_id=_optional_uuid(payload.get("series_id")),
                city=payload.get("city") or "",
                address=payload.get("address") or "",
                category=payload.get("category") or "",
                resource_type=payload.get("resource_type") or "PHYSICAL_PRESENCE",
                description=payload.get("description") or "",
                urgency=payload.get("urgency") or "NORMAL",
                preferred_date=_date(payload.get("preferred_date")),
                preferred_time_from=_clock_time(payload.get("preferred_time_from")),
                preferred_time_to=_clock_time(payload.get("preferred_time_to")),
                estimated_duration_min=payload.get("estimated_duration_min"),
                required_skills_json=skills_json,
                requires_vehicle=bool(payload.get("requires_vehicle", False)),
                concurrency_type=payload.get("concurrency_type") or "UNKNOWN",
                status="PENDING_REVIEW",
                match_attempt=int(payload.get("match_attempt") or 0),
                created_at=_naive_utc(event.created_at),
            )
        )

    def _on_classified(self, conn: Connection, event: DomainEvent) -> None:
        kind = event.payload.get("concurrency_type")
        if not kind:
            return
        conn.execute(
            update(help_requests).where(help_requests.c.id == event.aggregate_id).values(concurrency_type=str(kind))
        )

    def _on_proposed(self, conn: Connection, event: DomainEvent) -> None:
        attempt = int(event.payload["match_attempt"])
        conn.execute(
            update(help_requests)
            .where(help_requests.c.id == event.aggregate_id)
            .values(status="MATCH_PROPOSED", match_attempt=attempt + 1)
        )
        items = event.payload.get("proposals") or event.payload.get("matches") or []
        stamp = _naive_utc(event.created_at)
        for item in items:
            conn.execute(
                insert(task_assignments).values(
                    id=_uuid(item["assignment_id"]),
                    request_id=event.aggregate_id,
                    volunteer_id=_uuid(item["volunteer_id"]),
                    ai_score=item["score"],
                    ai_rationale=str(item.get("rationale") or "")[:500],
                    rank_in_batch=int(item["rank"]),
                    match_attempt=attempt,
                    approved_by=None,
                    status="PROPOSED",
                    decline_reason=None,
                    override_reason=None,
                    updated_at=stamp,
                )
            )

    def _on_no_match(self, conn: Connection, event: DomainEvent) -> None:
        attempt = int(event.payload["match_attempt"])
        conn.execute(
            update(help_requests)
            .where(help_requests.c.id == event.aggregate_id)
            .values(status="NO_MATCH", match_attempt=attempt + 1)
        )

    def _on_retriggered(self, conn: Connection, event: DomainEvent) -> None:
        stamp = _naive_utc(event.created_at)
        _supersede_proposed(conn, event.aggregate_id, stamp)
        conn.execute(
            update(help_requests).where(help_requests.c.id == event.aggregate_id).values(status="PENDING_REVIEW")
        )

    def _on_approved(self, conn: Connection, event: DomainEvent) -> None:
        stamp = _naive_utc(event.created_at)
        assignment_id = _uuid(event.payload["assignment_id"])
        volunteer_id = _uuid(event.payload["volunteer_id"])
        conn.execute(
            update(task_assignments)
            .where(task_assignments.c.id == assignment_id)
            .values(status="ASSIGNED", approved_by=_uuid(event.payload["approved_by"]), updated_at=stamp)
        )
        _supersede_proposed(conn, event.aggregate_id, stamp)
        conn.execute(
            update(help_requests).where(help_requests.c.id == event.aggregate_id).values(status="ASSIGNED")
        )
        _shift_capacity(conn, volunteer_id, _concurrency(conn, event.aggregate_id), 1)

    def _on_rejected(self, conn: Connection, event: DomainEvent) -> None:
        stamp = _naive_utc(event.created_at)
        volunteer_ids = [_uuid(item) for item in event.payload.get("volunteer_ids") or []]
        if volunteer_ids:
            conn.execute(
                update(task_assignments)
                .where(task_assignments.c.request_id == event.aggregate_id)
                .where(task_assignments.c.status == "PROPOSED")
                .where(task_assignments.c.volunteer_id.in_(volunteer_ids))
                .values(status="DECLINED", decline_reason=event.payload.get("reason") or "", updated_at=stamp)
            )
        conn.execute(
            update(help_requests).where(help_requests.c.id == event.aggregate_id).values(status="PENDING_REVIEW")
        )

    def _on_overridden(self, conn: Connection, event: DomainEvent) -> None:
        stamp = _naive_utc(event.created_at)
        volunteer_id = _uuid(event.payload["volunteer_id"])
        _supersede_proposed(conn, event.aggregate_id, stamp)
        conn.execute(
            insert(task_assignments).values(
                id=_uuid(event.payload["assignment_id"]),
                request_id=event.aggregate_id,
                volunteer_id=volunteer_id,
                ai_score=None,
                ai_rationale=None,
                rank_in_batch=None,
                match_attempt=int(event.payload.get("match_attempt") or 0),
                approved_by=_uuid(event.payload["approved_by"]),
                status="ASSIGNED",
                decline_reason=None,
                override_reason=str(event.payload.get("override_reason") or "")[:500],
                updated_at=stamp,
            )
        )
        conn.execute(
            update(help_requests).where(help_requests.c.id == event.aggregate_id).values(status="ASSIGNED")
        )
        _shift_capacity(conn, volunteer_id, _concurrency(conn, event.aggregate_id), 1)

    def _on_cancelled(self, conn: Connection, event: DomainEvent) -> None:
        stamp = _naive_utc(event.created_at)
        assigned = conn.execute(
            select(task_assignments.c.volunteer_id)
            .where(task_assignments.c.request_id == event.aggregate_id)
            .where(task_assignments.c.status == "ASSIGNED")
        ).scalar()
        if assigned is not None:
            _shift_capacity(conn, _uuid(assigned), _concurrency(conn, event.aggregate_id), -1)
            conn.execute(
                update(task_assignments)
                .where(task_assignments.c.request_id == event.aggregate_id)
                .where(task_assignments.c.status == "ASSIGNED")
                .values(status="DECLINED", decline_reason=event.payload.get("reason") or "", updated_at=stamp)
            )
        _supersede_proposed(conn, event.aggregate_id, stamp)
        conn.execute(
            update(help_requests).where(help_requests.c.id == event.aggregate_id).values(status="CANCELLED")
        )


def _supersede_proposed(conn: Connection, request_id: UUID, stamp: datetime) -> None:
    conn.execute(
        update(task_assignments)
        .where(task_assignments.c.request_id == request_id)
        .where(task_assignments.c.status == "PROPOSED")
        .values(status="SUPERSEDED", updated_at=stamp)
    )


def _concurrency(conn: Connection, request_id: UUID) -> str:
    value = conn.execute(
        select(help_requests.c.concurrency_type).where(help_requests.c.id == request_id)
    ).scalar()
    return str(value or "UNKNOWN")


def _shift_capacity(conn: Connection, profile_id: UUID, concurrency: str, delta: int) -> None:
    if concurrency == "PARALLEL_OK":
        column = volunteer_profiles.c.current_parallel_tasks
        key = "current_parallel_tasks"
    else:
        column = volunteer_profiles.c.current_active_tasks
        key = "current_active_tasks"
    conn.execute(
        update(volunteer_profiles)
        .where(volunteer_profiles.c.id == profile_id)
        .values(**{key: case((column + delta < 0, 0), else_=column + delta)})
    )


def _uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _optional_uuid(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    return _uuid(value)


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _clock_time(value: Any) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = str(value)
    if len(text) == 5:
        text += ":00"
    return time.fromisoformat(text)
