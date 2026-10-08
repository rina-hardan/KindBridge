"""Rebuild SQL read models from the event store.

Usage, from the repository root:
    python -m app.projections.rebuild

Empties the projection tables and replays ``event_store`` ordered by ``seq``.
``version`` is per aggregate and is not a global order. SQL Server will not
``TRUNCATE`` a table that is still the target of a foreign key, so the tables
are cleared with ``DELETE`` in dependent-first order.

Rebuild touches SQL projections only. It does not call Chroma or Gmail, and it
does not modify ``event_store`` or the non-event-sourced ``login_attempts`` table.
"""

import sys
from typing import Protocol

from dotenv import load_dotenv
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.engine import RowMapping

from app.config import Config, ConfigError
from app.domain.events import DomainEvent
from app.projections.exemption_projector import ExemptionProjector
from app.projections.match_projector import MatchProjector
from app.projections.projectors import UserProjector
from app.repositories.db import make_engine

# Child tables first. ``login_attempts`` is not event-sourced and is not listed.
_PROJECTION_TABLES = (
    "task_assignments",
    "volunteer_unavailability",
    "help_requests",
    "request_series",
    "exemption_links",
    "requester_profiles",
    "volunteer_profiles",
    "users",
)

_EVENT_COLUMNS = (
    "event_id",
    "aggregate_id",
    "aggregate_type",
    "event_type",
    "payload_json",
    "version",
    "correlation_id",
    "causation_id",
    "created_at",
)


class ReadModelProjector(Protocol):
    def apply(self, events: list[DomainEvent], conn: Connection) -> None: ...


def rebuild(engine: Engine, projectors: list[ReadModelProjector] | None = None) -> int:
    """Clear projection tables and apply every event in ``seq`` order.

    Returns the number of events replayed.
    """
    if projectors is None:
        projectors = [UserProjector(), MatchProjector(), ExemptionProjector()]
    with engine.begin() as conn:
        _clear_projections(conn)
        events = _load_events(conn)
        for projector in projectors:
            projector.apply(events, conn)
    return len(events)


def main() -> int:
    load_dotenv()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    count = rebuild(make_engine(config.database_url))
    print(f"Rebuilt read models from {count} events.")
    return 0


def _clear_projections(conn: Connection) -> None:
    present = {name.lower() for name in _table_names(conn)}
    qualified = conn.dialect.name == "mssql"
    for table in _PROJECTION_TABLES:
        if table not in present:
            continue
        target = f"dbo.{table}" if qualified else table
        conn.execute(text(f"DELETE FROM {target}"))


def _load_events(conn: Connection) -> list[DomainEvent]:
    columns = ", ".join(_EVENT_COLUMNS)
    rows = conn.execute(text(f"SELECT {columns} FROM event_store ORDER BY {_replay_order(conn)}")).mappings()
    return [DomainEvent.from_row(_mapping(row)) for row in rows]


def _replay_order(conn: Connection) -> str:
    """Global ``seq`` on SQL Server. Tests create ``event_store`` without ``seq``."""
    if _has_column(conn, "event_store", "seq"):
        return "seq"
    if conn.dialect.name == "sqlite":
        return "rowid"
    raise RuntimeError("event_store.seq is required to replay events in global order")


def _table_names(conn: Connection) -> list[str]:
    inspector = inspect(conn)
    names = list(inspector.get_table_names())
    if conn.dialect.name == "mssql":
        names.extend(inspector.get_table_names(schema="dbo"))
    return names


def _has_column(conn: Connection, table: str, column: str) -> bool:
    inspector = inspect(conn)
    columns = inspector.get_columns(table)
    if not columns and conn.dialect.name == "mssql":
        columns = inspector.get_columns(table, schema="dbo")
    return any(col["name"].lower() == column.lower() for col in columns)


def _mapping(row: RowMapping) -> dict[str, object]:
    return {key: row[key] for key in _EVENT_COLUMNS}


if __name__ == "__main__":
    raise SystemExit(main())
