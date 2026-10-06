"""Event store. SQL Server is the production implementation; tests can swap it."""

import threading
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import Connection, Engine, func, insert, select
from sqlalchemy.exc import IntegrityError

from app.domain.errors import ConcurrencyConflict
from app.domain.events import DomainEvent, payload_to_json
from app.repositories.db import make_engine
from app.repositories.tables import event_store

Projector = Callable[[Sequence[DomainEvent], Any], None]


class EventStore(Protocol):
    def load_stream(self, aggregate_id: UUID) -> list[DomainEvent]:
        """Return the aggregate's events in version order."""

    def append(
        self,
        aggregate_id: UUID,
        expected_version: int,
        events: Sequence[DomainEvent],
        *,
        projector: Projector | None = None,
    ) -> None:
        """Append events when the stream head is still ``expected_version``.

        A mismatched version, including a unique ``(aggregate_id, version)`` clash,
        raises ``ConcurrencyConflict`` (HTTP 409). ``projector`` runs in the same
        transaction and is rolled back with the append if it fails.
        """


class TransactionalEventStore(EventStore, Protocol):
    """An event store that can join a transaction the caller already opened."""

    def append_in(
        self,
        conn: Connection,
        aggregate_id: UUID,
        expected_version: int,
        events: Sequence[DomainEvent],
    ) -> None: ...

    def current_version(self, conn: Connection, aggregate_id: UUID) -> int: ...


def validate_append(
    aggregate_id: UUID,
    expected_version: int,
    events: Sequence[DomainEvent],
) -> None:
    if expected_version < 0:
        raise ValueError("expected_version must be >= 0")
    for offset, event in enumerate(events, start=1):
        if event.aggregate_id != aggregate_id:
            raise ValueError("event aggregate_id does not match the stream")
        if event.version != expected_version + offset:
            raise ValueError(
                f"event version {event.version} does not match expected {expected_version + offset}"
            )


def is_aggregate_version_conflict(exc: BaseException) -> bool:
    """True when the database rejected the insert on ``(aggregate_id, version)``."""
    message = str(getattr(exc, "orig", exc))
    if "UQ_event_store_aggregate_version" in message:
        return True
    lowered = message.lower()
    if "unique constraint failed" in lowered and "event_store.version" in lowered:
        return True
    return ("2627" in message or "2601" in message) and "version" in lowered


class InMemoryEventStore:
    """Process-local event store with the same version conflict rule as SQL Server."""

    def __init__(self) -> None:
        self._streams: dict[UUID, list[DomainEvent]] = {}
        self._lock = threading.Lock()

    def load_stream(self, aggregate_id: UUID) -> list[DomainEvent]:
        with self._lock:
            return list(self._streams.get(aggregate_id, []))

    def append(
        self,
        aggregate_id: UUID,
        expected_version: int,
        events: Sequence[DomainEvent],
        *,
        projector: Projector | None = None,
    ) -> None:
        if not events:
            return
        validate_append(aggregate_id, expected_version, events)
        pending = list(events)
        with self._lock:
            current = self._streams.get(aggregate_id, [])
            current_version = current[-1].version if current else 0
            if current_version != expected_version:
                raise ConcurrencyConflict(
                    f"aggregate {aggregate_id} expected version {expected_version} "
                    f"but found {current_version}"
                )
            if projector is not None:
                projector(pending, None)
            self._streams[aggregate_id] = [*current, *pending]


def _insert_row(event: DomainEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "aggregate_id": event.aggregate_id,
        "aggregate_type": event.aggregate_type,
        "event_type": event.event_type,
        "payload_json": payload_to_json(event.payload),
        "version": event.version,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "created_at": event.to_row()["created_at"],
    }


class SqlEventStore:
    """Append-only store on ``dbo.event_store`` (see ``db/schema.sql``). No update or delete method."""

    def __init__(self, database_url: str = "", engine: Engine | None = None) -> None:
        if engine is None and not database_url:
            raise ValueError("DATABASE_URL is required")
        self._database_url = database_url
        self._engine = engine

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = make_engine(self._database_url)
        return self._engine

    def load_stream(self, aggregate_id: UUID) -> list[DomainEvent]:
        stmt = (
            select(event_store)
            .where(event_store.c.aggregate_id == aggregate_id)
            .order_by(event_store.c.version)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(stmt).mappings()
            return [DomainEvent.from_row(row) for row in rows]

    def append(
        self,
        aggregate_id: UUID,
        expected_version: int,
        events: Sequence[DomainEvent],
        *,
        projector: Projector | None = None,
    ) -> None:
        if not events:
            return
        pending = list(events)
        with self.engine.begin() as connection:
            self.append_in(connection, aggregate_id, expected_version, pending)
            if projector is not None:
                projector(pending, connection)

    def append_in(
        self,
        conn: Connection,
        aggregate_id: UUID,
        expected_version: int,
        events: Sequence[DomainEvent],
    ) -> None:
        """Append inside the caller's transaction, so projections can commit atomically with it."""
        if not events:
            return
        validate_append(aggregate_id, expected_version, events)
        current_version = self.current_version(conn, aggregate_id)
        if current_version != expected_version:
            raise ConcurrencyConflict(
                f"aggregate {aggregate_id} expected version {expected_version} "
                f"but found {current_version}"
            )
        try:
            conn.execute(insert(event_store), [_insert_row(event) for event in events])
        except IntegrityError as exc:
            if is_aggregate_version_conflict(exc):
                raise ConcurrencyConflict(
                    f"aggregate {aggregate_id} version conflict at expected version {expected_version}"
                ) from exc
            raise

    def current_version(self, conn: Connection, aggregate_id: UUID) -> int:
        stmt = (
            select(func.coalesce(func.max(event_store.c.version), 0))
            .where(event_store.c.aggregate_id == aggregate_id)
            .with_hint(event_store, "WITH (UPDLOCK, HOLDLOCK)", "mssql")
        )
        return int(conn.execute(stmt).scalar() or 0)
