"""Closed vocabulary and list filters for a help request.

The category list is the one in architecture.md section 3.1. Skills stay in
``bilingual.skill_key`` so Hebrew and English names of the same skill match.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.errors import ValidationError

CATEGORIES = (
    "errands",
    "transport",
    "shopping",
    "companionship",
    "home_help",
    "childcare",
    "tutoring",
    "translation",
    "first_aid",
)
RESOURCE_TYPES = ("PHYSICAL_PRESENCE", "EQUIPMENT_LOAN", "FLEXIBLE_REMOTE")
URGENCIES = ("LOW", "NORMAL", "HIGH", "EMERGENCY")
OPEN_STATUSES = ("PENDING_REVIEW", "MATCH_PROPOSED", "NO_MATCH", "ASSIGNED")
ALL_STATUSES = OPEN_STATUSES + ("COMPLETED", "CANCELLED")
PAGE_SIZE = 20


@dataclass(frozen=True)
class RequestListFilters:
    """``statuses is None`` means every status, including cancelled and completed."""

    statuses: tuple[str, ...] | None
    category: str | None
    urgency: str | None
    city: str | None
    page: int


def parse_list_filters(data: Mapping[str, str]) -> RequestListFilters:
    errors: dict[str, str] = {}
    status = (data.get("status") or "open").strip()
    if status == "open":
        statuses: tuple[str, ...] | None = OPEN_STATUSES
    elif status == "all":
        statuses = None
    elif status in ALL_STATUSES:
        statuses = (status,)
    else:
        errors["status"] = "Must be one of open, all, " + ", ".join(ALL_STATUSES)
        statuses = OPEN_STATUSES

    category = _optional_choice(data, "category", CATEGORIES, errors)
    urgency = _optional_choice(data, "urgency", URGENCIES, errors)
    city = _optional_city(data, errors)
    page = _page(data, errors)
    if errors:
        raise ValidationError(errors)
    return RequestListFilters(statuses=statuses, category=category, urgency=urgency, city=city, page=page)


def _optional_choice(data: Mapping[str, str], field: str, allowed: tuple[str, ...], errors: dict[str, str]) -> str | None:
    raw = data.get(field)
    if raw is None or str(raw).strip() == "":
        return None
    value = str(raw).strip()
    if value not in allowed:
        errors[field] = f"Must be one of {', '.join(allowed)}"
        return None
    return value


def _optional_city(data: Mapping[str, str], errors: dict[str, str]) -> str | None:
    raw = data.get("city")
    if raw is None or str(raw).strip() == "":
        return None
    value = str(raw).strip()
    if len(value) > 100:
        errors["city"] = "Must be at most 100 characters"
        return None
    return value


def _page(data: Mapping[str, str], errors: dict[str, str]) -> int:
    raw = data.get("page") or "1"
    text = str(raw).strip()
    if not text.isdigit() or not 1 <= int(text) <= 1000:
        errors["page"] = "Must be a whole number between 1 and 1000"
        return 1
    return int(text)
