import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Connection, insert, update

from app.domain import events as ev
from app.domain.events import DomainEvent
from app.repositories.tables import users, volunteer_profiles


def _naive_utc(value: datetime) -> datetime:
    """DATETIME2 cannot store tzinfo. Keep the UTC clock time and drop the zone."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class UserProjector:
    """Builds the users and volunteer_profiles read models from User-stream events.

    ``apply(events, connection)`` matches ``EventStore.append`` and
    ``CommandBus.commit``, so a handler can pass the instance as ``projector=``.
    """

    def __init__(self) -> None:
        self._handlers = {
            ev.USER_REGISTERED: self._on_UserRegistered,
            ev.CREDENTIAL_SET: self._on_CredentialSet,
            ev.ADMIN_BOOTSTRAPPED: self._on_AdminBootstrapped,
            ev.VOLUNTEER_PROFILE_ENABLED: self._on_VolunteerProfileEnabled,
        }

    def __call__(self, events: Sequence[DomainEvent], connection: Any) -> None:
        self.apply(events, connection)

    def apply(self, events: Sequence[DomainEvent], connection: Connection) -> None:
        for event in events:
            handler = self._handlers.get(event.event_type)
            if handler is not None:
                handler(connection, event)

    def _on_UserRegistered(self, conn: Connection, event: DomainEvent) -> None:
        conn.execute(
            insert(users).values(
                id=event.aggregate_id,
                email=event.payload["email"],
                password_hash="",
                full_name=event.payload["full_name"],
                phone=event.payload["phone_encrypted"],
                is_admin=False,
                is_active=True,
                created_at=_naive_utc(event.created_at),
            )
        )

    def _on_CredentialSet(self, conn: Connection, event: DomainEvent) -> None:
        conn.execute(
            update(users).where(users.c.id == event.aggregate_id).values(password_hash=event.payload["password_hash"])
        )

    def _on_AdminBootstrapped(self, conn: Connection, event: DomainEvent) -> None:
        conn.execute(update(users).where(users.c.id == event.aggregate_id).values(is_admin=True))

    def _on_VolunteerProfileEnabled(self, conn: Connection, event: DomainEvent) -> None:
        p = event.payload
        conn.execute(
            insert(volunteer_profiles).values(
                id=uuid.UUID(p["profile_id"]),
                user_id=event.aggregate_id,
                is_enabled=True,
                primary_city=p["primary_city"],
                has_vehicle=p["has_vehicle"],
                skills_json=json.dumps(p["skills"], ensure_ascii=False),
                experience=p["experience"],
                base_frequency=p["base_frequency"],
                availability_status="AVAILABLE",
                max_active_tasks=p["max_active_tasks"],
                max_parallel_tasks=p["max_parallel_tasks"],
                current_active_tasks=0,
                current_parallel_tasks=0,
            )
        )
