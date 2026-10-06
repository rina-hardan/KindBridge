import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, exists, select

from app.repositories.tables import users, volunteer_profiles


@dataclass(frozen=True)
class AuthRecord:
    user_id: uuid.UUID
    password_hash: str
    full_name: str
    is_admin: bool
    is_active: bool
    has_enabled_volunteer_profile: bool


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
        return conn.execute(select(exists().where(users.c.email == email))).scalar()

    def admin_exists(self, conn: Connection) -> bool:
        return conn.execute(select(exists().where(users.c.is_admin.is_(True)))).scalar()
