"""Help-request reads: admin search, request detail, and the owner's own list."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine

from app.domain.aggregates import OPEN_REQUEST_STATUSES
from app.domain.requests import OPEN_STATUSES, PAGE_SIZE, RequestListFilters
from app.repositories.request_read import REQUEST_STATUSES, URGENCIES, RequestReadRepository
from app.repositories.requests import HelpRequestRepository, as_date
from app.repositories.users import UserRepository
from app.security.encryption import FieldEncryptor


@dataclass(frozen=True)
class RequestListItem:
    request_id: str
    requester_name: str
    category: str
    city: str
    urgency: str
    status: str
    overdue: bool


@dataclass(frozen=True)
class OwnedRequestItem:
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
class RequestSearch:
    rows: tuple[RequestListItem, ...]
    page: int
    page_count: int
    total: int
    category: str
    city: str
    urgency: str
    status: str
    categories: tuple[str, ...]
    cities: tuple[str, ...]
    urgencies: tuple[str, ...]
    statuses: tuple[str, ...]

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


@dataclass(frozen=True)
class RequestPage:
    items: list[OwnedRequestItem]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class RequesterDefaults:
    city: str
    address: str


@dataclass(frozen=True)
class VolunteerCard:
    assignment_id: str
    volunteer_id: str
    user_id: str
    full_name: str
    city: str
    score_label: str | None
    rationale: str | None


@dataclass(frozen=True)
class HelpRequestDetails:
    request_id: str
    requester_id: UUID
    requester_name: str
    phone: str
    city: str
    address: str
    category: str
    urgency: str
    description: str
    status: str
    overdue: bool
    cards: tuple[VolunteerCard, ...]
    rejection_summary: tuple[tuple[str, int], ...]
    show_scores: bool
    can_approve: bool
    can_reject: bool
    can_override: bool
    can_retrigger: bool
    can_cancel: bool
    can_exempt: bool
    assigned: bool


class SearchHelpRequestsQuery:
    def __init__(self, reader: RequestReadRepository) -> None:
        self._reader = reader

    def execute(
        self,
        *,
        requester_id: UUID | None,
        category: str = "",
        city: str = "",
        urgency: str = "",
        status: str = "",
        page: int = 1,
        today: date | None = None,
    ) -> RequestSearch:
        today = today or date.today()
        snapshot = self._reader.search(
            requester_id=requester_id,
            category=category.strip()[:100],
            city=city.strip()[:100],
            urgency=urgency.strip(),
            status=status.strip(),
            page=max(page, 1),
            page_size=PAGE_SIZE,
        )
        return RequestSearch(
            rows=tuple(
                RequestListItem(
                    request_id=str(row.request_id),
                    requester_name=row.requester_name,
                    category=row.category,
                    city=row.city,
                    urgency=row.urgency,
                    status=row.status,
                    overdue=row.preferred_date is not None
                    and row.preferred_date < today
                    and row.status in OPEN_REQUEST_STATUSES,
                )
                for row in snapshot.rows
            ),
            page=snapshot.page,
            page_count=snapshot.page_count,
            total=snapshot.total,
            category=category.strip(),
            city=city.strip(),
            urgency=urgency.strip() if urgency.strip() in URGENCIES else "",
            status=status.strip() if status.strip() in REQUEST_STATUSES else "",
            categories=snapshot.categories,
            cities=snapshot.cities,
            urgencies=URGENCIES,
            statuses=REQUEST_STATUSES,
        )


class GetHelpRequestDetailsQuery:
    def __init__(self, reader: RequestReadRepository, encryptor: FieldEncryptor) -> None:
        self._reader = reader
        self._encryptor = encryptor

    def execute(self, request_id: UUID, *, is_admin: bool, today: date | None = None) -> HelpRequestDetails | None:
        today = today or date.today()
        row = self._reader.detail(request_id)
        if row is None:
            return None
        overdue = (
            row.preferred_date is not None
            and row.preferred_date < today
            and row.status in OPEN_REQUEST_STATUSES
        )
        proposed = [item for item in row.assignments if item.status == "PROPOSED"][:3]
        assigned = [item for item in row.assignments if item.status == "ASSIGNED"][:1]
        if row.status == "NO_MATCH":
            cards: tuple[VolunteerCard, ...] = ()
        elif row.status == "ASSIGNED":
            cards = tuple(_card(item) for item in assigned)
        elif is_admin and row.status == "MATCH_PROPOSED":
            cards = tuple(_card(item) for item in proposed)
        else:
            cards = ()
        summary = tuple(sorted(row.rejection_summary.items())) if row.status == "NO_MATCH" else ()
        open_request = row.status in OPEN_REQUEST_STATUSES
        return HelpRequestDetails(
            request_id=str(row.request_id),
            requester_id=row.requester_id,
            requester_name=row.requester_name,
            phone=self._encryptor.decrypt(row.phone_encrypted),
            city=row.city,
            address=row.address,
            category=row.category,
            urgency=row.urgency,
            description=row.description,
            status=row.status,
            overdue=overdue,
            cards=cards,
            rejection_summary=summary,
            show_scores=is_admin,
            can_approve=is_admin and row.status == "MATCH_PROPOSED" and bool(cards),
            can_reject=is_admin and row.status == "MATCH_PROPOSED" and bool(cards),
            can_override=is_admin and row.status in ("MATCH_PROPOSED", "NO_MATCH"),
            can_retrigger=is_admin and row.status in ("MATCH_PROPOSED", "NO_MATCH"),
            can_cancel=open_request,
            can_exempt=is_admin and (row.status == "ASSIGNED" or row.status in ("MATCH_PROPOSED", "NO_MATCH")),
            assigned=row.status == "ASSIGNED",
        )


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
            items=[_owned_item(row, today) for row in page_rows],
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


def _card(row) -> VolunteerCard:
    return VolunteerCard(
        assignment_id=str(row.assignment_id),
        volunteer_id=str(row.volunteer_id),
        user_id=str(row.user_id),
        full_name=row.full_name,
        city=row.city,
        score_label=_score_label(row.score),
        rationale=row.rationale or None,
    )


def _score_label(score: Decimal | None) -> str | None:
    if score is None:
        return None
    number = float(score)
    if number == int(number):
        return str(int(number))
    return f"{number:.1f}"


def _owned_item(row: dict, today: date) -> OwnedRequestItem:
    preferred = as_date(row["preferred_date"])
    status = str(row["status"])
    created = row["created_at"]
    if isinstance(created, datetime):
        created_text = created.date().isoformat()
    else:
        created_text = str(created)[:10]
    return OwnedRequestItem(
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
