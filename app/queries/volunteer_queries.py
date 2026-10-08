"""Volunteer reads: the admin volunteer picker, and a volunteer's own tasks."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Engine

from app.domain.matching import rejection_reason
from app.domain.requests import PAGE_SIZE
from app.repositories.matching import SqlMatchingReader
from app.repositories.volunteer_tasks import TASK_STATUSES, VolunteerTaskRepository
from app.security.encryption import FieldEncryptor


@dataclass(frozen=True)
class DirectoryVolunteer:
    profile_id: str
    user_id: str
    full_name: str
    city: str


class GetVolunteerDirectoryQuery:
    def __init__(self, engine: Engine, reader: SqlMatchingReader) -> None:
        self._engine = engine
        self._reader = reader

    def execute(self, request_id: UUID, *, today: date | None = None) -> tuple[DirectoryVolunteer, ...]:
        today = today or date.today()
        with self._engine.connect() as conn:
            named = self._reader.load_named_volunteers(conn)
            view = self._reader.load_request(conn, request_id)
            if view is None or not named:
                return ()
            facts = self._reader.facts_for(conn, view, list(named), today)
        chosen = [
            DirectoryVolunteer(
                profile_id=str(item.volunteer.profile_id),
                user_id=str(item.volunteer.user_id),
                full_name=item.full_name,
                city=item.volunteer.primary_city,
            )
            for item in named.values()
            if rejection_reason(item.volunteer, facts) is None
        ]
        chosen.sort(key=lambda item: item.full_name)
        return tuple(chosen)


@dataclass(frozen=True)
class VolunteerTaskItem:
    assignment_id: str
    request_id: str
    status: str
    category: str
    city: str
    urgency: str
    preferred_date: str | None
    description: str
    requester_name: str
    overdue: bool
    # Address and phone belong to an ASSIGNED task only. For anything else they are None, never masked text.
    address: str | None
    phone: str | None


@dataclass(frozen=True)
class VolunteerTaskPage:
    items: tuple[VolunteerTaskItem, ...]
    page: int
    page_size: int
    page_count: int
    total: int
    status: str
    statuses: tuple[str, ...]

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


class GetVolunteerTasksQuery:
    """The signed-in volunteer's own ASSIGNED and COMPLETED tasks. The person comes from the token, never the URL."""

    def __init__(self, tasks: VolunteerTaskRepository, encryptor: FieldEncryptor) -> None:
        self._tasks = tasks
        self._encryptor = encryptor

    def execute(
        self, user_id: UUID, *, status: str = "", page: int = 1, today: date | None = None
    ) -> VolunteerTaskPage:
        today = today or date.today()
        chosen = status.strip().upper()
        if chosen not in TASK_STATUSES:
            chosen = ""
        snapshot = self._tasks.list_for_user(
            user_id, (chosen,) if chosen else TASK_STATUSES, max(page, 1), PAGE_SIZE
        )
        return VolunteerTaskPage(
            items=tuple(self._item(row, today) for row in snapshot.rows),
            page=snapshot.page,
            page_size=PAGE_SIZE,
            page_count=snapshot.page_count,
            total=snapshot.total,
            status=chosen,
            statuses=TASK_STATUSES,
        )

    def _item(self, row, today: date) -> VolunteerTaskItem:
        active = row.status == "ASSIGNED"
        return VolunteerTaskItem(
            assignment_id=str(row.assignment_id),
            request_id=str(row.request_id),
            status=row.status,
            category=row.category,
            city=row.city,
            urgency=row.urgency,
            preferred_date=None if row.preferred_date is None else row.preferred_date.isoformat(),
            description=row.description,
            requester_name=row.requester_name,
            overdue=active and row.preferred_date is not None and row.preferred_date < today,
            address=row.address if active else None,
            phone=self._encryptor.decrypt(row.phone_encrypted) if active else None,
        )
