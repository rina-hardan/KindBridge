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

    def _on_cancelled(self, conn: Connection, event: DomainEvent) -> None:
        request_id = event.aggregate_id
        concurrency = conn.execute(
            select(help_requests.c.concurrency_type).where(help_requests.c.id == request_id)
        ).scalar()
        conn.execute(update(help_requests).where(help_requests.c.id == request_id).values(status="CANCELLED"))
        stamp = _naive_utc(event.created_at)
        reason = str(event.payload.get("reason") or "")[:500] or None
        conn.execute(
            update(task_assignments)
            .where(task_assignments.c.request_id == request_id, task_assignments.c.status == "PROPOSED")
            .values(status="SUPERSEDED", updated_at=stamp)
        )
        assigned_ids = list(
            conn.execute(
                select(task_assignments.c.volunteer_id).where(
                    task_assignments.c.request_id == request_id,
                    task_assignments.c.status == "ASSIGNED",
                )
            ).scalars()
        )
        if not assigned_ids:
            return
        conn.execute(
            update(task_assignments)
            .where(task_assignments.c.request_id == request_id, task_assignments.c.status == "ASSIGNED")
            .values(status="DECLINED", decline_reason=reason, updated_at=stamp)
        )
        for volunteer_id in assigned_ids:
            _free_capacity(conn, volunteer_id, concurrency)


def _free_capacity(conn: Connection, volunteer_id: Any, concurrency: Any) -> None:
    if concurrency == "PARALLEL_OK":
        column = volunteer_profiles.c.current_parallel_tasks
        values = {
            "current_parallel_tasks": case((column > 0, column - 1), else_=0),
        }
    else:
        column = volunteer_profiles.c.current_active_tasks
        values = {
            "current_active_tasks": case((column > 0, column - 1), else_=0),
        }
    conn.execute(update(volunteer_profiles).where(volunteer_profiles.c.id == volunteer_id).values(**values))


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
