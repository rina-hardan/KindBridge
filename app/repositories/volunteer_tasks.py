"""Read model for the volunteer's own tasks. Reads only; never writes."""

import math
import uuid
from dataclasses import dataclass
from datetime import date
from collections.abc import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.engine import Engine

from app.repositories.requests import as_date
from app.repositories.tables import help_requests, task_assignments, users, volunteer_profiles

# A volunteer sees only the work they took on: the one they are doing and the ones they finished.
TASK_STATUSES = ("ASSIGNED", "COMPLETED")


@dataclass(frozen=True)
class VolunteerTaskRow:
    assignment_id: uuid.UUID
    request_id: uuid.UUID
    status: str
    category: str
    city: str
    urgency: str
    preferred_date: date | None
    description: str
    requester_name: str
    address: str
    phone_encrypted: str


@dataclass(frozen=True)
class VolunteerTaskSnapshot:
    total: int
    page: int
    page_count: int
    rows: tuple[VolunteerTaskRow, ...]


class VolunteerTaskRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_for_user(
        self, user_id: uuid.UUID, statuses: Sequence[str], page: int, page_size: int
    ) -> VolunteerTaskSnapshot:
        wanted = [status for status in statuses if status in TASK_STATUSES]
        scope = (volunteer_profiles.c.user_id == user_id, task_assignments.c.status.in_(wanted))
        source = task_assignments.join(
            volunteer_profiles, volunteer_profiles.c.id == task_assignments.c.volunteer_id
        ).join(help_requests, help_requests.c.id == task_assignments.c.request_id)
        with self._engine.connect() as conn:
            total = int(
                conn.execute(select(func.count()).select_from(source).where(*scope)).scalar_one()
            )
            page_count = max(1, math.ceil(total / page_size)) if total else 1
            page = min(max(page, 1), page_count)
            rows = conn.execute(
                select(
                    task_assignments.c.id.label("assignment_id"),
                    task_assignments.c.status,
                    help_requests.c.id.label("request_id"),
                    help_requests.c.category,
                    help_requests.c.city,
                    help_requests.c.urgency,
                    help_requests.c.preferred_date,
                    help_requests.c.description,
                    help_requests.c.address,
                    users.c.full_name,
                    users.c.phone,
                )
                .select_from(source.join(users, users.c.id == help_requests.c.requester_id))
                .where(*scope)
                .order_by(
                    case((task_assignments.c.status == "ASSIGNED", 0), else_=1),
                    task_assignments.c.updated_at.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            found = tuple(
                VolunteerTaskRow(
                    assignment_id=_uuid(row.assignment_id),
                    request_id=_uuid(row.request_id),
                    status=row.status,
                    category=row.category,
                    city=row.city,
                    urgency=row.urgency,
                    preferred_date=as_date(row.preferred_date),
                    description=row.description,
                    requester_name=row.full_name,
                    address=row.address,
                    phone_encrypted=row.phone,
                )
                for row in rows
            )
        return VolunteerTaskSnapshot(total=total, page=page, page_count=page_count, rows=found)


def _uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
