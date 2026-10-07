"""Deterministic matching rules. No SQL, Flask, or Chroma.

Semantic similarity ranks volunteers who already passed the hard filter.
It is never a reason to drop someone, and a score of 0 stays in the batch.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

TOP_N = 15
PROPOSAL_LIMIT = 3

INACTIVE = "inactive"
SELF_ASSIGNMENT = "self_assignment"
EXEMPTION = "exemption"
DECLINED = "declined"
UNAVAILABLE = "unavailability"
GEOGRAPHY = "geography"
VEHICLE = "vehicle"
CAPACITY = "capacity"
SCHEDULE_OVERLAP = "schedule_overlap"

PARALLEL_OK = "PARALLEL_OK"
EXCLUSIVE = "EXCLUSIVE"
UNKNOWN = "UNKNOWN"

URGENCY_FIT = {"EMERGENCY": 1.0, "HIGH": 0.85, "NORMAL": 0.7, "LOW": 0.55}
FREQUENCY_BONUS = {"ON_DEMAND": 1.0, "WEEKLY": 0.9, "BIWEEKLY": 0.8, "MONTHLY": 0.7}
_PHYSICAL = frozenset({"PHYSICAL_PRESENCE", "EQUIPMENT_LOAN"})


@dataclass(frozen=True)
class TimeSlot:
    preferred_date: date | None
    time_from: time | None = None
    time_to: time | None = None
    duration_min: int | None = None


@dataclass(frozen=True)
class VolunteerRecord:
    profile_id: UUID
    user_id: UUID
    is_enabled: bool
    is_active: bool
    availability_status: str
    primary_city: str
    has_vehicle: bool
    skills: tuple[str, ...]
    base_frequency: str
    max_active_tasks: int
    max_parallel_tasks: int
    current_active_tasks: int
    current_parallel_tasks: int


@dataclass(frozen=True)
class RequestRecord:
    request_id: UUID
    requester_id: UUID
    city: str
    category: str
    resource_type: str
    description: str
    urgency: str
    slot: TimeSlot
    required_skills: tuple[str, ...]
    requires_vehicle: bool
    concurrency_type: str

    @property
    def preferred_date(self) -> date | None:
        return self.slot.preferred_date


@dataclass(frozen=True)
class UnavailabilityPeriod:
    volunteer_id: UUID
    from_date: date
    until_date: date


@dataclass(frozen=True)
class AssignedTask:
    concurrency_type: str
    slot: TimeSlot


@dataclass(frozen=True)
class FilterFacts:
    """Projection facts for one request. Exemption ids are user ids."""

    request: RequestRecord
    exemptions: frozenset[UUID]
    declined_profile_ids: frozenset[UUID]
    periods: tuple[UnavailabilityPeriod, ...]
    assigned: tuple[tuple[UUID, AssignedTask], ...]
    today: date


@dataclass(frozen=True)
class ScoredVolunteer:
    volunteer_id: UUID
    score: float
    similarity: float
    rationale: str


def is_self_assignment(volunteer_user_id: UUID, requester_id: UUID) -> bool:
    """True when this volunteer user created the request. Shared with approve and override."""
    return volunteer_user_id == requester_id


def effective_concurrency(concurrency_type: str) -> str:
    """UNKNOWN takes the exclusive capacity and overlap rules."""
    if concurrency_type == PARALLEL_OK:
        return PARALLEL_OK
    return EXCLUSIVE


def default_concurrency(resource_type: str) -> tuple[str, str]:
    """Stored classification used when the request is still UNKNOWN.

    FLEXIBLE_REMOTE leans PARALLEL_OK. PHYSICAL_PRESENCE leans EXCLUSIVE.
    Anything else, including an unclassified value, is EXCLUSIVE.
    """
    if resource_type == "FLEXIBLE_REMOTE":
        return PARALLEL_OK, "FLEXIBLE_REMOTE leans PARALLEL_OK"
    if resource_type == "PHYSICAL_PRESENCE":
        return EXCLUSIVE, "PHYSICAL_PRESENCE leans EXCLUSIVE"
    return EXCLUSIVE, "UNKNOWN is treated as EXCLUSIVE"


def request_query_text(
    description: str,
    category: str,
    required_skills: Sequence[str],
    accessibility_notes: str | None = None,
) -> str:
    """Text embedded for the volunteer_resumes query."""
    parts = [description.strip(), category.strip(), " ".join(required_skills).strip()]
    if accessibility_notes and accessibility_notes.strip():
        parts.append(accessibility_notes.strip())
    return " ".join(part for part in parts if part)


def cosine_similarity_from_distance(distance: float) -> float:
    """Chroma cosine distance is 1 - cosine similarity, clipped to 0..1."""
    similarity = 1.0 - float(distance)
    if similarity < 0.0:
        return 0.0
    if similarity > 1.0:
        return 1.0
    return similarity


def skill_overlap(required: Sequence[str], offered: Sequence[str]) -> float:
    if not required:
        return 1.0
    needed = {item.casefold() for item in required}
    have = {item.casefold() for item in offered}
    return len(needed & have) / len(needed)


def cities_match(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def score_candidate(
    *,
    similarity: float,
    skills: float,
    travel_feasibility: float,
    urgency: str,
    has_vehicle_fit: bool,
    frequency: str,
    preferred_date: date | None,
    today: date,
) -> tuple[float, dict[str, float]]:
    """Weighted ai_score in 0..100. The LLM does not supply this number."""
    parts = {
        "similarity": 0.45 * _unit(similarity),
        "skills": 0.20 * _unit(skills),
        "travel": 0.15 * _unit(travel_feasibility),
        "urgency": 0.10 * URGENCY_FIT.get(urgency, 0.55),
        "vehicle": 0.05 * (1.0 if has_vehicle_fit else 0.0),
        "frequency": 0.05 * FREQUENCY_BONUS.get(frequency, 0.0),
    }
    score = 100.0 * sum(parts.values())
    if preferred_date is not None and preferred_date < today:
        score *= 0.85
    return round(min(100.0, max(0.0, score)), 2), parts


def render_rationale(components: dict[str, float], travel_note: str, web_lookup: str) -> str:
    ranking = sorted(components.items(), key=lambda item: (-item[1], item[0]))
    lead = " and ".join(name for name, _value in ranking[:2])
    note = travel_note.strip() or "no travel note"
    return f"{lead} lead the score. Travel: {note} web_lookup={web_lookup}"[:500]


def rank_proposals(scored: Sequence[ScoredVolunteer]) -> list[ScoredVolunteer]:
    """Keep the top K. A score of 0 is still a proposal."""
    ordered = sorted(scored, key=lambda item: (-item.score, -item.similarity, str(item.volunteer_id)))
    return list(ordered[:PROPOSAL_LIMIT])


def rejection_reason(volunteer: VolunteerRecord, facts: FilterFacts) -> str | None:
    """First failing check in system-spec 4.2 order, or None when the volunteer stays."""
    request = facts.request
    if not volunteer.is_enabled or not volunteer.is_active or volunteer.availability_status == "INACTIVE":
        return INACTIVE
    if is_self_assignment(volunteer.user_id, request.requester_id):
        return SELF_ASSIGNMENT
    if volunteer.user_id in facts.exemptions:
        return EXEMPTION
    if volunteer.profile_id in facts.declined_profile_ids:
        return DECLINED
    on_date = request.preferred_date or facts.today
    for period in facts.periods:
        if period.volunteer_id == volunteer.profile_id and period.from_date <= on_date <= period.until_date:
            return UNAVAILABLE
    if request.resource_type in _PHYSICAL and not cities_match(volunteer.primary_city, request.city):
        return GEOGRAPHY
    if request.requires_vehicle and not volunteer.has_vehicle:
        return VEHICLE
    concurrency = effective_concurrency(request.concurrency_type)
    if _over_capacity(volunteer, concurrency):
        return CAPACITY
    if _overlaps_assigned(volunteer.profile_id, request.slot, concurrency, facts.assigned):
        return SCHEDULE_OVERLAP
    return None


def count_rejections(reasons: Sequence[str]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for reason in reasons:
        summary[reason] = summary.get(reason, 0) + 1
    return summary


def tasks_overlap(
    left: TimeSlot,
    left_concurrency: str,
    right: TimeSlot,
    right_concurrency: str,
) -> bool:
    """Overlap is allowed only when both tasks are PARALLEL_OK."""
    if effective_concurrency(left_concurrency) == PARALLEL_OK and effective_concurrency(right_concurrency) == PARALLEL_OK:
        return False
    if left.preferred_date is None or right.preferred_date is None or left.preferred_date != right.preferred_date:
        return False
    left_bounds = _bounds(left)
    right_bounds = _bounds(right)
    if left_bounds is not None and right_bounds is not None:
        return left_bounds[0] < right_bounds[1] and right_bounds[0] < left_bounds[1]
    if effective_concurrency(left_concurrency) == PARALLEL_OK or effective_concurrency(right_concurrency) == PARALLEL_OK:
        return False
    return True


def _over_capacity(volunteer: VolunteerRecord, concurrency: str) -> bool:
    if concurrency == PARALLEL_OK:
        return volunteer.current_parallel_tasks >= volunteer.max_parallel_tasks
    return volunteer.current_active_tasks >= volunteer.max_active_tasks


def _overlaps_assigned(
    profile_id: UUID,
    slot: TimeSlot,
    concurrency: str,
    assigned: Sequence[tuple[UUID, AssignedTask]],
) -> bool:
    for owner_id, task in assigned:
        if owner_id != profile_id:
            continue
        if tasks_overlap(slot, concurrency, task.slot, task.concurrency_type):
            return True
    return False


def _bounds(slot: TimeSlot) -> tuple[time, time] | None:
    if slot.time_from is None:
        return None
    if slot.time_to is not None:
        if slot.time_to <= slot.time_from:
            return None
        return slot.time_from, slot.time_to
    if slot.duration_min is None or slot.duration_min <= 0:
        return None
    start = datetime.combine(date(2000, 1, 1), slot.time_from)
    end = start + timedelta(minutes=slot.duration_min)
    if end.date() != start.date():
        return slot.time_from, time(23, 59, 59)
    if end.time() <= slot.time_from:
        return None
    return slot.time_from, end.time()


def _unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)
