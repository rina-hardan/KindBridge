"""Admin dashboard: open-request counters, the queue, and volunteer capacity."""

from dataclasses import dataclass
from datetime import date

from app.domain.aggregates import OPEN_REQUEST_STATUSES
from app.repositories.admin_read import AdminReadRepository, VolunteerCapacityRow

PAGE_SIZE = 20


@dataclass(frozen=True)
class StatusCounts:
    pending_review: int
    match_proposed: int
    no_match: int
    assigned: int

    def as_rows(self) -> tuple[tuple[str, int], ...]:
        return (
            ("PENDING_REVIEW", self.pending_review),
            ("MATCH_PROPOSED", self.match_proposed),
            ("NO_MATCH", self.no_match),
            ("ASSIGNED", self.assigned),
        )


@dataclass(frozen=True)
class QueueRow:
    request_id: str
    requester_name: str
    category: str
    city: str
    urgency: str
    status: str
    overdue: bool
    candidate_count: int


@dataclass(frozen=True)
class CapacityGauges:
    available: int
    busy: int
    inactive: int

    @property
    def total(self) -> int:
        return self.available + self.busy + self.inactive

    def share(self, count: int) -> int:
        if self.total == 0:
            return 0
        return round(100 * count / self.total)


@dataclass(frozen=True)
class AdminDashboard:
    counts: StatusCounts
    requests: tuple[QueueRow, ...]
    gauges: CapacityGauges
    page: int
    page_count: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


class GetAdminDashboardQuery:
    def __init__(self, reader: AdminReadRepository) -> None:
        self._reader = reader

    def execute(self, *, page: int = 1, today: date | None = None) -> AdminDashboard:
        today = today or date.today()
        snapshot = self._reader.load(page=max(page, 1), page_size=PAGE_SIZE, today=today)
        counts = snapshot.status_counts
        return AdminDashboard(
            counts=StatusCounts(
                pending_review=counts.get("PENDING_REVIEW", 0),
                match_proposed=counts.get("MATCH_PROPOSED", 0),
                no_match=counts.get("NO_MATCH", 0),
                assigned=counts.get("ASSIGNED", 0),
            ),
            requests=tuple(
                QueueRow(
                    request_id=str(row.request_id),
                    requester_name=row.requester_name,
                    category=row.category,
                    city=row.city,
                    urgency=row.urgency,
                    status=row.status,
                    overdue=row.preferred_date is not None and row.preferred_date < today,
                    candidate_count=row.candidate_count,
                )
                for row in snapshot.requests
            ),
            gauges=_gauges(snapshot.volunteers),
            page=snapshot.page,
            page_count=snapshot.page_count,
        )


def _gauges(rows: tuple[VolunteerCapacityRow, ...]) -> CapacityGauges:
    available = busy = inactive = 0
    for row in rows:
        if row.availability_status == "INACTIVE":
            inactive += 1
            continue
        if row.current_active_tasks >= row.max_active_tasks:
            busy += 1
            continue
        if row.temporarily_unavailable:
            continue
        available += 1
    return CapacityGauges(available=available, busy=busy, inactive=inactive)


# Imported so a caller can see the four open statuses without a fifth OVERDUE bucket.
DASHBOARD_STATUSES = OPEN_REQUEST_STATUSES
