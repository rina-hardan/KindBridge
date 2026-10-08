"""Projects ExemptionLinkCreated onto exemption_links."""

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, insert

from app.domain.events import DomainEvent
from app.repositories.tables import exemption_links


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class ExemptionProjector:
    def __call__(self, events: Sequence[DomainEvent], connection: Any) -> None:
        self.apply(events, connection)

    def apply(self, events: Sequence[DomainEvent], connection: Connection) -> None:
        for event in events:
            if event.event_type == "ExemptionLinkCreated":
                self._on_created(connection, event)

    def _on_created(self, conn: Connection, event: DomainEvent) -> None:
        payload = event.payload
        conn.execute(
            insert(exemption_links).values(
                volunteer_id=_uuid(payload["volunteer_id"]),
                requester_id=_uuid(payload["requester_id"]),
                created_by=_uuid(payload["created_by"]),
                reason=str(payload.get("reason") or "")[:300],
                created_at=_naive_utc(event.created_at),
            )
        )


def _uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
