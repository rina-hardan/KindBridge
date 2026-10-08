import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, func, select, true

from app.repositories.tables import requester_profiles, users, volunteer_profiles


@dataclass(frozen=True)
class AuthRecord:
    user_id: uuid.UUID
    password_hash: str
    full_name: str
    is_admin: bool
    is_active: bool
    has_enabled_volunteer_profile: bool


@dataclass(frozen=True)
class AccountRecord:
    user_id: uuid.UUID
    email: str
    full_name: str
    phone: str
    city: str | None
    home_address: str | None
    is_admin: bool
    is_active: bool


@dataclass(frozen=True)
class Contact:
    """Where a notification may go. Always read from the users projection, never from a request."""

    user_id: uuid.UUID
    email: str
    full_name: str


class UserRepository:
    """Read access to the users projection for authentication decisions."""

    def get_auth_record(self, conn: Connection, email: str) -> AuthRecord | None:
        stmt = (
            select(
                users.c.id,
                users.c.password_hash,
                users.c.full_name,
                users.c.is_admin,
                users.c.is_active,
                volunteer_profiles.c.is_enabled,
            )
            .select_from(users.outerjoin(volunteer_profiles, volunteer_profiles.c.user_id == users.c.id))
            .where(users.c.email == email)
        )
        row = conn.execute(stmt).first()
        if row is None:
            return None
        return AuthRecord(
            user_id=row.id,
            password_hash=row.password_hash,
            full_name=row.full_name,
            is_admin=bool(row.is_admin),
            is_active=bool(row.is_active),
            has_enabled_volunteer_profile=bool(row.is_enabled),
        )

    def email_exists(self, conn: Connection, email: str) -> bool:
        stmt = select(users.c.id).where(users.c.email == email).limit(1)
        return conn.execute(stmt).first() is not None

    def get_account(self, conn: Connection, user_id: uuid.UUID) -> AccountRecord | None:
        stmt = select(
            users.c.id,
            users.c.email,
            users.c.full_name,
            users.c.phone,
            users.c.city,
            users.c.home_address,
            users.c.is_admin,
            users.c.is_active,
        ).where(users.c.id == user_id)
        row = conn.execute(stmt).first()
        if row is None:
            return None
        return AccountRecord(
            user_id=row.id,
            email=row.email,
            full_name=row.full_name,
            phone=row.phone,
            city=row.city,
            home_address=row.home_address,
            is_admin=bool(row.is_admin),
            is_active=bool(row.is_active),
        )

    def requester_defaults(self, conn: Connection, user_id: uuid.UUID) -> tuple[str | None, str | None] | None:
        stmt = select(requester_profiles.c.default_city, requester_profiles.c.default_address).where(
            requester_profiles.c.user_id == user_id
        )
        row = conn.execute(stmt).first()
        if row is None:
            return None
        return row.default_city, row.default_address

    def requester_profile_id(self, conn: Connection, user_id: uuid.UUID) -> uuid.UUID | None:
        stmt = select(requester_profiles.c.id).where(requester_profiles.c.user_id == user_id)
        row = conn.execute(stmt).first()
        return None if row is None else row.id

    def volunteer_profile_id(self, conn: Connection, user_id: uuid.UUID) -> uuid.UUID | None:
        stmt = select(volunteer_profiles.c.id).where(volunteer_profiles.c.user_id == user_id)
        row = conn.execute(stmt).first()
        return None if row is None else row.id

    def contact(self, conn: Connection, user_id: uuid.UUID) -> Contact | None:
        row = conn.execute(
            select(users.c.id, users.c.email, users.c.full_name).where(
                users.c.id == user_id, users.c.is_active == true()
            )
        ).first()
        return None if row is None else Contact(row.id, row.email, row.full_name)

    def volunteer_contact(self, conn: Connection, profile_id: uuid.UUID) -> Contact | None:
        """``profile_id`` is ``volunteer_profiles.id``; the address comes from the owning user."""
        row = conn.execute(
            select(users.c.id, users.c.email, users.c.full_name)
            .select_from(volunteer_profiles.join(users, users.c.id == volunteer_profiles.c.user_id))
            .where(volunteer_profiles.c.id == profile_id, users.c.is_active == true())
        ).first()
        return None if row is None else Contact(row.id, row.email, row.full_name)

    def volunteer_names(self, conn: Connection, profile_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not profile_ids:
            return {}
        rows = conn.execute(
            select(volunteer_profiles.c.id, users.c.full_name)
            .select_from(volunteer_profiles.join(users, users.c.id == volunteer_profiles.c.user_id))
            .where(volunteer_profiles.c.id.in_(profile_ids))
        )
        return {row.id: row.full_name for row in rows}

    def active_admin_contacts(self, conn: Connection) -> list[Contact]:
        rows = conn.execute(
            select(users.c.id, users.c.email, users.c.full_name)
            .where(users.c.is_admin == true(), users.c.is_active == true())
            .order_by(users.c.email)
        )
        return [Contact(row.id, row.email, row.full_name) for row in rows]

    def admin_contact_by_email(self, conn: Connection, email: str) -> Contact | None:
        """ADMIN_NOTIFY_EMAIL only counts when it belongs to an active admin in the database."""
        row = conn.execute(
            select(users.c.id, users.c.email, users.c.full_name).where(
                func.lower(users.c.email) == email.strip().lower(),
                users.c.is_admin == true(),
                users.c.is_active == true(),
            )
        ).first()
        return None if row is None else Contact(row.id, row.email, row.full_name)

    def admin_exists(self, conn: Connection) -> bool:
        stmt = select(users.c.id).where(users.c.is_admin == true()).limit(1)
        return conn.execute(stmt).first() is not None
