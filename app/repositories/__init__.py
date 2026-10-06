"""Persistence behind interfaces. SQL does not leave this package."""

from app.repositories.event_store import (
    EventStore,
    InMemoryEventStore,
    SqlEventStore,
    is_aggregate_version_conflict,
)

__all__ = [
    "EventStore",
    "InMemoryEventStore",
    "SqlEventStore",
    "is_aggregate_version_conflict",
]
