"""Read models for the admin dashboard. Queries never write."""

import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import case, func, select
from sqlalchemy.engine import Connection, Engine

from app.domain.aggregates import OPEN_REQUEST_STATUSES
from app.repositories.tables import help_requests, task_assignments, users, volunteer_profiles, volunteer_unavailability

CANDIDATE_STATUSES = ("PROPOSED", "ASSIGNED")


@dataclass(frozen=True)
class OpenRequestRow:
    request_id: uuid.UUID
    requester_name: str
    category: str
    city: str
    urgency: str
    status: str
    preferred_date: date | None
    candidate_count: int


@dataclass(frozen=True)
class VolunteerCapacityRow:
    availability_status: str
    current_active_tasks: int
    max_active_tasks: int
    temporarily_unavailable: bool


@dataclass(frozen=True)
class DashboardSnapshot:
    status_counts: dict[str, int]
    page: int
    page_count: int
    requests: tuple[OpenRequestRow, ...]
    volunteers: tuple[VolunteerCapacityRow, ...]


class AdminReadRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(self, *, page: int, page_size: int, today: date) -> DashboardSnapshot:
        with self._engine.connect() as conn:
            counts = self.status_counts(conn)
            total_open = sum(counts.values())
            page_count = max(1, math.ceil(total_open / page_size)) if total_open else 1
            page = min(max(page, 1), page_count)
            return DashboardSnapshot(
                status_counts=counts,
                page=page,
                page_count=page_count if total_open else 1,
                requests=self.open_requests(conn, offset=(page - 1) * page_size, limit=page_size),
                volunteers=self.volunteer_capacity(conn, today),
            )

    def status_counts(self, conn: Connection) -> dict[str, int]:
        counts = {status: 0 for status in OPEN_REQUEST_STATUSES}
        rows = conn.execute(
            select(help_requests.c.status, func.count())
            .where(help_requests.c.status.in_(OPEN_REQUEST_STATUSES))
            .group_by(help_requests.c.status)
        )
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts

    def open_requests(self, conn: Connection, *, offset: int, limit: int) -> tuple[OpenRequestRow, ...]:
        urgency_order = case(
            (help_requests.c.urgency == "EMERGENCY", 0),
            (help_requests.c.urgency == "HIGH", 1),
            (help_requests.c.urgency == "NORMAL", 2),
            else_=3,
        )
        candidate_count = (
            select(func.count())
            .select_from(task_assignments)
            .where(task_assignments.c.request_id == help_requests.c.id)
            .where(task_assignments.c.status.in_(CANDIDATE_STATUSES))
            .scalar_subquery()
        )
        rows = conn.execute(
            select(
                help_requests.c.id,
                users.c.full_name,
                help_requests.c.category,
                help_requests.c.city,
                help_requests.c.urgency,
                help_requests.c.status,
                help_requests.c.preferred_date,
                candidate_count.label("candidate_count"),
            )
            .join(users, users.c.id == help_requests.c.requester_id)
            .where(help_requests.c.status.in_(OPEN_REQUEST_STATUSES))
            .order_by(urgency_order, help_requests.c.created_at)
            .offset(offset)
            .limit(limit)
        )
        return tuple(
            OpenRequestRow(
                request_id=_uuid(row.id),
                requester_name=row.full_name,
                category=row.category,
                city=row.city,
                urgency=row.urgency,
                status=row.status,
                preferred_date=_date(row.preferred_date),
                candidate_count=int(row.candidate_count or 0),
            )
            for row in rows
        )

    def volunteer_capacity(self, conn: Connection, today: date) -> tuple[VolunteerCapacityRow, ...]:
        covering = (
            select(volunteer_unavailability.c.volunteer_id)
            .where(volunteer_unavailability.c.is_cancelled == False)  # noqa: E712
            .where(volunteer_unavailability.c.from_date <= today)
            .where(volunteer_unavailability.c.until_date >= today)
        )
        rows = conn.execute(
            select(
                volunteer_profiles.c.availability_status,
                volunteer_profiles.c.current_active_tasks,
                volunteer_profiles.c.max_active_tasks,
                volunteer_profiles.c.id.in_(covering).label("temporarily_unavailable"),
            )
            .join(users, users.c.id == volunteer_profiles.c.user_id)
            .where(volunteer_profiles.c.is_enabled == True)  # noqa: E712
            .where(users.c.is_active == True)  # noqa: E712
        )
        return tuple(
            VolunteerCapacityRow(
                availability_status=row.availability_status,
                current_active_tasks=int(row.current_active_tasks),
                max_active_tasks=int(row.max_active_tasks),
                temporarily_unavailable=bool(row.temporarily_unavailable),
            )
            for row in rows
        )


def _uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
