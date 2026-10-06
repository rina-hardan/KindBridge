"""Event store. SQL Server is the production implementation; tests can swap it."""

import threading
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from urllib.parse import quote_plus
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.domain.errors import ConcurrencyConflict
from app.domain.events import DomainEvent

Projector = Callable[[Sequence[DomainEvent], Any], None]

_SELECT_VERSION = text(
    """
    SELECT ISNULL(MAX(version), 0)
    FROM dbo.event_store WITH (UPDLOCK, HOLDLOCK)
    WHERE aggregate_id = :aggregate_id
    """
)

_INSERT_EVENT = text(
    """
    INSERT INTO dbo.event_store (
        event_id,
        aggregate_id,
        aggregate_type,
        event_type,
        payload_json,
        version,
        correlation_id,
        causation_id,
        created_at
    ) VALUES (
        :event_id,
        :aggregate_id,
        :aggregate_type,
        :event_type,
        :payload_json,
        :version,
        :correlation_id,
        :causation_id,
        :created_at
    )
    """
)

_SELECT_STREAM = text(
    """
    SELECT
        event_id,
        aggregate_id,
        aggregate_type,
        event_type,
        payload_json,
        version,
        correlation_id,
        causation_id,
        created_at
    FROM dbo.event_store
    WHERE aggregate_id = :aggregate_id
    ORDER BY version ASC
    """
)


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


def sqlalchemy_url_for(database_url: str) -> str:
    """Accept a SQLAlchemy URL or a raw ODBC connection string."""
    if "://" in database_url:
        return database_url
    return "mssql+pyodbc:///?odbc_connect=" + quote_plus(database_url)


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
    """True when SQL Server rejected the insert on ``(aggregate_id, version)``."""
    message = str(getattr(exc, "orig", exc))
    if "UQ_event_store_aggregate_version" in message:
        return True
    lowered = message.lower()
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


class SqlEventStore:
    """Append-only store on ``dbo.event_store`` (see ``db/schema.sql``)."""

    def __init__(self, database_url: str, engine: Engine | None = None) -> None:
        if engine is None and not database_url:
            raise ValueError("DATABASE_URL is required")
        self._database_url = database_url
        self._engine = engine

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(
                sqlalchemy_url_for(self._database_url),
                pool_pre_ping=True,
            )
        return self._engine

    def load_stream(self, aggregate_id: UUID) -> list[DomainEvent]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                _SELECT_STREAM,
                {"aggregate_id": str(aggregate_id)},
            ).mappings()
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
        validate_append(aggregate_id, expected_version, events)
        pending = list(events)
        try:
            with self.engine.begin() as connection:
                current_version = int(
                    connection.execute(
                        _SELECT_VERSION,
                        {"aggregate_id": str(aggregate_id)},
                    ).scalar()
                    or 0
                )
                if current_version != expected_version:
                    raise ConcurrencyConflict(
                        f"aggregate {aggregate_id} expected version {expected_version} "
                        f"but found {current_version}"
                    )
                for event in pending:
                    connection.execute(_INSERT_EVENT, event.to_row())
                if projector is not None:
                    projector(pending, connection)
        except IntegrityError as exc:
            if is_aggregate_version_conflict(exc):
                raise ConcurrencyConflict(
                    f"aggregate {aggregate_id} version conflict at expected version {expected_version}"
                ) from exc
            raise
