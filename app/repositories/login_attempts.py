from datetime import datetime

from sqlalchemy import Connection, func, insert, select

from app.repositories.tables import login_attempts


class LoginAttemptRepository:
    """Not event-sourced (system-spec 3.10)."""

    def record(self, conn: Connection, email: str, attempted_at: datetime, succeeded: bool) -> None:
        conn.execute(insert(login_attempts).values(email=email, attempted_at=attempted_at, succeeded=succeeded))

    def recent_failures(self, conn: Connection, email: str, since: datetime, limit: int) -> list[datetime]:
        """Failures after `since` and after the latest success, newest first."""
        last_success = conn.execute(
            select(func.max(login_attempts.c.attempted_at)).where(
                login_attempts.c.email == email, login_attempts.c.succeeded.is_(True)
            )
        ).scalar()
        lower_bound = max(since, last_success) if last_success else since
        stmt = (
            select(login_attempts.c.attempted_at)
            .where(
                login_attempts.c.email == email,
                login_attempts.c.succeeded.is_(False),
                login_attempts.c.attempted_at > lower_bound,
            )
            .order_by(login_attempts.c.attempted_at.desc())
            .limit(limit)
        )
        return list(conn.execute(stmt).scalars())
