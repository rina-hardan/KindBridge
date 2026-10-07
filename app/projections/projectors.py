import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Connection, insert, select, update

from app.domain import events as ev
from app.domain.events import DomainEvent
from app.repositories.tables import requester_profiles, users, volunteer_profiles


def _naive_utc(value: datetime) -> datetime:
    """DATETIME2 cannot store tzinfo. Keep the UTC clock time and drop the zone."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class UserProjector:
    """Builds the users, requester_profiles, and volunteer_profiles read models from User-stream events.

    ``apply(events, connection)`` matches ``EventStore.append`` and
    ``CommandBus.commit``, so a handler can pass the instance as ``projector=``.
    """

    def __init__(self) -> None:
        self._handlers = {
            ev.USER_REGISTERED: self._on_UserRegistered,
            ev.USER_DETAILS_UPDATED: self._on_UserDetailsUpdated,
            ev.CREDENTIAL_SET: self._on_CredentialSet,
            ev.ADMIN_BOOTSTRAPPED: self._on_AdminBootstrapped,
            ev.VOLUNTEER_PROFILE_ENABLED: self._on_VolunteerProfileEnabled,
            ev.REQUESTER_PROFILE_UPDATED: self._on_RequesterProfileUpdated,
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
                city=event.payload["city"],
                home_address=event.payload["home_address"],
                is_admin=False,
                is_active=True,
                created_at=_naive_utc(event.created_at),
            )
        )

    def _on_UserDetailsUpdated(self, conn: Connection, event: DomainEvent) -> None:
        payload = event.payload
        conn.execute(
            update(users)
            .where(users.c.id == event.aggregate_id)
            .values(
                full_name=payload["full_name"],
                phone=payload["phone_encrypted"],
                city=payload["city"],
                home_address=payload["home_address"],
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
        existing = conn.execute(
            select(volunteer_profiles.c.id).where(volunteer_profiles.c.user_id == event.aggregate_id)
        ).first()
        if existing is not None:
            conn.execute(
                update(volunteer_profiles)
                .where(volunteer_profiles.c.user_id == event.aggregate_id)
                .values(is_enabled=True)
            )
            return
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

    def _on_RequesterProfileUpdated(self, conn: Connection, event: DomainEvent) -> None:
        payload = event.payload
        values = {
            "default_city": payload["default_city"],
            "default_address": payload["default_address"],
            "accessibility_notes": payload["accessibility_notes"],
            "emergency_contact_name": payload["emergency_contact_name"],
            "emergency_contact_phone": payload["emergency_contact_phone_encrypted"],
            "updated_at": _naive_utc(event.created_at),
        }
        existing = conn.execute(
            select(requester_profiles.c.id).where(requester_profiles.c.user_id == event.aggregate_id)
        ).first()
        if existing is None:
            conn.execute(
                insert(requester_profiles).values(
                    id=uuid.UUID(payload["profile_id"]),
                    user_id=event.aggregate_id,
                    **values,
                )
            )
            return
        conn.execute(
            update(requester_profiles).where(requester_profiles.c.user_id == event.aggregate_id).values(**values)
        )
