"""Admin read of volunteer offers. Never appends to the event store or mutates projections."""

import math
from dataclasses import dataclass

from app.domain.requests import PAGE_SIZE
from app.repositories.admin_read import VolunteerOfferReadRepository, VolunteerOfferRow

ASSIGNMENT_FILTERS = ("all", "assigned", "unassigned")


@dataclass(frozen=True)
class OfferLink:
    request_id: str
    category: str
    city: str


@dataclass(frozen=True)
class VolunteerOffer:
    full_name: str
    city: str
    skills: tuple[str, ...]
    experience: str
    has_vehicle: bool
    availability_status: str
    current_active_tasks: int
    max_active_tasks: int
    assignments: tuple[OfferLink, ...]

    @property
    def assigned(self) -> bool:
        return bool(self.assignments)


@dataclass(frozen=True)
class VolunteerOffers:
    rows: tuple[VolunteerOffer, ...]
    page: int
    page_count: int
    assignment: str
    assignment_choices: tuple[str, ...] = ASSIGNMENT_FILTERS

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


class GetVolunteerOffersQuery:
    def __init__(self, reader: VolunteerOfferReadRepository) -> None:
        self._reader = reader

    def execute(self, *, page: int = 1, assignment: str = "all") -> VolunteerOffers:
        chosen = str(assignment or "all").strip().lower()
        if chosen not in ASSIGNMENT_FILTERS:
            chosen = "all"
        offers = tuple(_offer(row) for row in self._reader.list_offers())
        if chosen == "assigned":
            offers = tuple(item for item in offers if item.assigned)
        elif chosen == "unassigned":
            offers = tuple(item for item in offers if not item.assigned)
        total = len(offers)
        page_count = max(1, math.ceil(total / PAGE_SIZE)) if total else 1
        current = min(max(page, 1), page_count)
        start = (current - 1) * PAGE_SIZE
        return VolunteerOffers(
            rows=offers[start : start + PAGE_SIZE],
            page=current,
            page_count=page_count,
            assignment=chosen,
        )


def _offer(row: VolunteerOfferRow) -> VolunteerOffer:
    return VolunteerOffer(
        full_name=row.full_name,
        city=row.city,
        skills=row.skills,
        experience=row.experience,
        has_vehicle=row.has_vehicle,
        availability_status=row.availability_status,
        current_active_tasks=row.current_active_tasks,
        max_active_tasks=row.max_active_tasks,
        assignments=tuple(
            OfferLink(request_id=str(item.request_id), category=item.category, city=item.city)
            for item in row.assignments
        ),
    )
