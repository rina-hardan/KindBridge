"""Owner's help-request list and the defaults that pre-fill a new request."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine

from app.domain.requests import OPEN_STATUSES, PAGE_SIZE, RequestListFilters
from app.repositories.requests import HelpRequestRepository, as_date
from app.repositories.users import UserRepository


@dataclass(frozen=True)
class RequestListItem:
    id: uuid.UUID
    category: str
    city: str
    urgency: str
    status: str
    preferred_date: str | None
    description: str
    created_at: str
    overdue: bool
    can_cancel: bool


@dataclass(frozen=True)
class RequestPage:
    items: list[RequestListItem]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class RequesterDefaults:
    city: str
    address: str


class GetMyRequestsQuery:
    def __init__(self, engine: Engine, requests: HelpRequestRepository) -> None:
        self._engine = engine
        self._requests = requests

    def execute(self, requester_id: uuid.UUID, filters: RequestListFilters, today: date) -> RequestPage:
        with self._engine.connect() as conn:
            rows = self._requests.list_for_requester(conn, requester_id)
        rows = self._requests.matching_city(rows, filters.city)
        if filters.statuses is not None:
            allowed = set(filters.statuses)
            rows = [row for row in rows if row["status"] in allowed]
        if filters.category is not None:
            rows = [row for row in rows if row["category"] == filters.category]
        if filters.urgency is not None:
            rows = [row for row in rows if row["urgency"] == filters.urgency]
        total = len(rows)
        start = (filters.page - 1) * PAGE_SIZE
        page_rows = rows[start : start + PAGE_SIZE]
        return RequestPage(
            items=[_item(row, today) for row in page_rows],
            page=filters.page,
            page_size=PAGE_SIZE,
            total=total,
        )


class GetRequesterDefaultsQuery:
    def __init__(self, engine: Engine, users: UserRepository) -> None:
        self._engine = engine
        self._users = users

    def execute(self, user_id: uuid.UUID) -> RequesterDefaults | None:
        with self._engine.connect() as conn:
            found = self._users.requester_defaults(conn, user_id)
        if found is None:
            return None
        city, address = found
        return RequesterDefaults(city=city or "", address=address or "")


def _item(row: dict, today: date) -> RequestListItem:
    preferred = as_date(row["preferred_date"])
    status = str(row["status"])
    created = row["created_at"]
    if isinstance(created, datetime):
        created_text = created.date().isoformat()
    else:
        created_text = str(created)[:10]
    return RequestListItem(
        id=row["id"] if isinstance(row["id"], uuid.UUID) else uuid.UUID(str(row["id"])),
        category=str(row["category"]),
        city=str(row["city"]),
        urgency=str(row["urgency"]),
        status=status,
        preferred_date=None if preferred is None else preferred.isoformat(),
        description=str(row["description"]),
        created_at=created_text,
        overdue=preferred is not None and preferred < today and status in OPEN_STATUSES,
        can_cancel=status in OPEN_STATUSES,
    )
