"""Domain model: aggregates, events, and errors. No Flask, SQL, or Chroma imports."""

from app.domain.aggregates import (
    AGGREGATE_TYPES,
    AggregateRoot,
    ExemptionLink,
    HelpRequest,
    User,
    VolunteerProfile,
)
from app.domain.errors import (
    ConcurrencyConflict,
    DomainError,
    Forbidden,
    NotFound,
    Unauthorized,
    ValidationError,
)
from app.domain.events import DomainEvent, redact_payload

__all__ = [
    "AGGREGATE_TYPES",
    "AggregateRoot",
    "ConcurrencyConflict",
    "DomainError",
    "DomainEvent",
    "ExemptionLink",
    "Forbidden",
    "HelpRequest",
    "NotFound",
    "Unauthorized",
    "User",
    "ValidationError",
    "VolunteerProfile",
    "redact_payload",
]
