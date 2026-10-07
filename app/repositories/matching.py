"""Read models for matching. Exemption volunteer_id is a user id; assignments use profile ids."""

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.engine import Connection

from app.domain.matching import (
    AssignedTask,
    RequestRecord,
    TimeSlot,
    UnavailabilityPeriod,
    VolunteerRecord,
)
from app.repositories.tables import (
    exemption_links,
    help_requests,
    requester_profiles,
    task_assignments,
    users,
    volunteer_profiles,
    volunteer_unavailability,
)


@dataclass(frozen=True)
class PendingRequest:
    request_id: UUID
    match_attempt: int


class SqlMatchingReader:
    def pending_requests(self, conn: Connection, limit: int = 10) -> list[PendingRequest]:
        urgency_order = case(
            (help_requests.c.urgency == "EMERGENCY", 0),
            (help_requests.c.urgency == "HIGH", 1),
            (help_requests.c.urgency == "NORMAL", 2),
            else_=3,
        )
        stmt = (
            select(help_requests.c.id, help_requests.c.match_attempt)
            .where(help_requests.c.status == "PENDING_REVIEW")
            .order_by(urgency_order, help_requests.c.created_at)
            .limit(limit)
        )
        return [
            PendingRequest(request_id=_uuid(row.id), match_attempt=int(row.match_attempt))
            for row in conn.execute(stmt)
        ]

    def load_request(self, conn: Connection, request_id: UUID) -> RequestRecord | None:
        row = conn.execute(select(help_requests).where(help_requests.c.id == request_id)).mappings().first()
        if row is None:
            return None
        return RequestRecord(
            request_id=_uuid(row["id"]),
            requester_id=_uuid(row["requester_id"]),
            city=row["city"],
            category=row["category"],
            resource_type=row["resource_type"],
            description=row["description"],
            urgency=row["urgency"],
            slot=TimeSlot(
                preferred_date=_date(row["preferred_date"]),
                time_from=_clock_time(row["preferred_time_from"]),
                time_to=_clock_time(row["preferred_time_to"]),
                duration_min=row["estimated_duration_min"],
            ),
            required_skills=tuple(_skills(row["required_skills_json"])),
            requires_vehicle=bool(row["requires_vehicle"]),
            concurrency_type=row["concurrency_type"],
        )

    def accessibility_notes(self, conn: Connection, requester_id: UUID) -> str | None:
        value = conn.execute(
            select(requester_profiles.c.accessibility_notes).where(requester_profiles.c.user_id == requester_id)
        ).scalar()
        if value is None or not str(value).strip():
            return None
        return str(value)

    def load_volunteers(self, conn: Connection, profile_ids: list[UUID]) -> dict[UUID, VolunteerRecord]:
        if not profile_ids:
            return {}
        stmt = (
            select(volunteer_profiles, users.c.is_active)
            .join(users, users.c.id == volunteer_profiles.c.user_id)
            .where(volunteer_profiles.c.id.in_(profile_ids))
        )
        found: dict[UUID, VolunteerRecord] = {}
        for row in conn.execute(stmt).mappings():
            profile_id = _uuid(row["id"])
            found[profile_id] = VolunteerRecord(
                profile_id=profile_id,
                user_id=_uuid(row["user_id"]),
                is_enabled=bool(row["is_enabled"]),
                is_active=bool(row["is_active"]),
                availability_status=row["availability_status"],
                primary_city=row["primary_city"],
                has_vehicle=bool(row["has_vehicle"]),
                skills=tuple(_skills(row["skills_json"])),
                base_frequency=row["base_frequency"],
                max_active_tasks=int(row["max_active_tasks"]),
                max_parallel_tasks=int(row["max_parallel_tasks"]),
                current_active_tasks=int(row["current_active_tasks"]),
                current_parallel_tasks=int(row["current_parallel_tasks"]),
            )
        return found

    def exemption_user_ids(self, conn: Connection, requester_id: UUID) -> frozenset[UUID]:
        """exemption_links.volunteer_id references users.id, not volunteer_profiles.id."""
        stmt = select(exemption_links.c.volunteer_id).where(exemption_links.c.requester_id == requester_id)
        return frozenset(_uuid(row.volunteer_id) for row in conn.execute(stmt))

    def declined_profile_ids(self, conn: Connection, request_id: UUID) -> frozenset[UUID]:
        stmt = select(task_assignments.c.volunteer_id).where(
            task_assignments.c.request_id == request_id,
            task_assignments.c.status == "DECLINED",
        )
        return frozenset(_uuid(row.volunteer_id) for row in conn.execute(stmt))

    def unavailability(self, conn: Connection, profile_ids: list[UUID]) -> tuple[UnavailabilityPeriod, ...]:
        if not profile_ids:
            return ()
        stmt = select(volunteer_unavailability).where(
            volunteer_unavailability.c.volunteer_id.in_(profile_ids),
            volunteer_unavailability.c.is_cancelled == False,  # noqa: E712
        )
        return tuple(
            UnavailabilityPeriod(
                volunteer_id=_uuid(row.volunteer_id),
                from_date=_date(row.from_date),
                until_date=_date(row.until_date),
            )
            for row in conn.execute(stmt)
            if _date(row.from_date) is not None and _date(row.until_date) is not None
        )

    def assigned_tasks(self, conn: Connection, profile_ids: list[UUID]) -> tuple[tuple[UUID, AssignedTask], ...]:
        if not profile_ids:
            return ()
        stmt = (
            select(
                task_assignments.c.volunteer_id,
                task_assignments.c.request_id,
                help_requests.c.concurrency_type,
                help_requests.c.preferred_date,
                help_requests.c.preferred_time_from,
                help_requests.c.preferred_time_to,
                help_requests.c.estimated_duration_min,
            )
            .join(help_requests, help_requests.c.id == task_assignments.c.request_id)
            .where(
                task_assignments.c.volunteer_id.in_(profile_ids),
                task_assignments.c.status == "ASSIGNED",
            )
        )
        tasks: list[tuple[UUID, AssignedTask]] = []
        for row in conn.execute(stmt):
            tasks.append(
                (
                    _uuid(row.volunteer_id),
                    AssignedTask(
                        concurrency_type=row.concurrency_type or "UNKNOWN",
                        slot=TimeSlot(
                            preferred_date=_date(row.preferred_date),
                            time_from=_clock_time(row.preferred_time_from),
                            time_to=_clock_time(row.preferred_time_to),
                            duration_min=row.estimated_duration_min,
                        ),
                    ),
                )
            )
        return tuple(tasks)


def _uuid(value) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _clock_time(value) -> time | None:
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


def _skills(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]
