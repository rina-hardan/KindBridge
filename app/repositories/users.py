import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Connection, case, exists, false, func, select, true

from app.repositories.requests import as_date
from app.repositories.tables import (
    event_store,
    help_requests,
    requester_profiles,
    task_assignments,
    users,
    volunteer_profiles,
    volunteer_unavailability,
)


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
class VolunteerProfileRecord:
    profile_id: uuid.UUID
    primary_city: str
    has_vehicle: bool
    skills: tuple[str, ...]
    experience: str
    base_frequency: str
    preferred_time_from: str | None
    preferred_time_to: str | None
    preferred_weekdays: tuple[str, ...]
    availability_status: str
    max_active_tasks: int
    max_parallel_tasks: int
    is_enabled: bool


@dataclass(frozen=True)
class UnavailabilityRecord:
    period_id: uuid.UUID
    volunteer_id: uuid.UUID
    from_date: date
    until_date: date
    reason: str | None
    is_cancelled: bool


@dataclass(frozen=True)
class Contact:
    """Where a notification may go. Always read from the users projection, never from a request."""

    user_id: uuid.UUID
    email: str
    full_name: str


class UserRepository:
    """Read access to the users projection for authentication decisions."""

    def get_auth_record(self, conn: Connection, email: str) -> AuthRecord | None:
        # SQL Server rejects a bare EXISTS in the select list; CASE is valid on both dialects.
        has_enabled = case(
            (
                exists(
                    select(volunteer_profiles.c.id).where(
                        volunteer_profiles.c.user_id == users.c.id,
                        volunteer_profiles.c.is_enabled == true(),
                    )
                ),
                true(),
            ),
            else_=false(),
        )
        stmt = select(
            users.c.id,
            users.c.password_hash,
            users.c.full_name,
            users.c.is_admin,
            users.c.is_active,
            has_enabled.label("has_enabled_volunteer_profile"),
        ).where(users.c.email == email)
        row = conn.execute(stmt).first()
        if row is None:
            return None
        return AuthRecord(
            user_id=row.id,
            password_hash=row.password_hash,
            full_name=row.full_name,
            is_admin=bool(row.is_admin),
            is_active=bool(row.is_active),
            has_enabled_volunteer_profile=bool(row.has_enabled_volunteer_profile),
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
        stmt = select(volunteer_profiles.c.id).where(volunteer_profiles.c.user_id == user_id).limit(1)
        row = conn.execute(stmt).first()
        return None if row is None else row.id

    def list_volunteer_profiles(self, conn: Connection, user_id: uuid.UUID) -> tuple[VolunteerProfileRecord, ...]:
        rows = conn.execute(
            select(volunteer_profiles)
            .where(volunteer_profiles.c.user_id == user_id)
            .order_by(volunteer_profiles.c.id)
        )
        return tuple(_volunteer_profile_record(row) for row in rows)

    def volunteer_profile_ids_hidden_from_wanted(self, conn: Connection, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        """Profiles omitted from the want-to-do column.

        An assigned profile is omitted. A completed profile stays omitted until a
        later release of that same profile. A declined proposal does not count as a release.
        """
        rows = conn.execute(
            select(
                task_assignments.c.id,
                task_assignments.c.volunteer_id,
                task_assignments.c.status,
                task_assignments.c.updated_at,
                task_assignments.c.request_id,
            )
            .select_from(
                task_assignments.join(
                    volunteer_profiles, volunteer_profiles.c.id == task_assignments.c.volunteer_id
                )
            )
            .where(volunteer_profiles.c.user_id == user_id)
        ).all()
        if not rows:
            return frozenset()
        assigned: set[uuid.UUID] = set()
        latest_completed: dict[uuid.UUID, datetime] = {}
        profile_by_assignment: dict[uuid.UUID, uuid.UUID] = {}
        request_ids: set[uuid.UUID] = set()
        for row in rows:
            profile_by_assignment[row.id] = row.volunteer_id
            request_ids.add(row.request_id)
            if row.status == "ASSIGNED":
                assigned.add(row.volunteer_id)
            elif row.status == "COMPLETED":
                stamp = _naive(row.updated_at)
                previous = latest_completed.get(row.volunteer_id)
                if stamp is not None and (previous is None or stamp > previous):
                    latest_completed[row.volunteer_id] = stamp
        latest_release = _latest_release(conn, request_ids, profile_by_assignment)
        hidden = set(assigned)
        for profile_id, completed_at in latest_completed.items():
            released_at = latest_release.get(profile_id)
            if released_at is None or released_at <= completed_at:
                hidden.add(profile_id)
        return frozenset(hidden)

    def volunteer_profile(self, conn: Connection, user_id: uuid.UUID) -> VolunteerProfileRecord | None:
        profiles = self.list_volunteer_profiles(conn, user_id)
        return None if not profiles else profiles[0]

    def volunteer_profile_owned(
        self, conn: Connection, user_id: uuid.UUID, profile_id: uuid.UUID
    ) -> VolunteerProfileRecord | None:
        row = conn.execute(
            select(volunteer_profiles).where(
                volunteer_profiles.c.id == profile_id,
                volunteer_profiles.c.user_id == user_id,
            )
        ).first()
        return None if row is None else _volunteer_profile_record(row)

    def owns_volunteer_profile(self, conn: Connection, user_id: uuid.UUID, profile_id: uuid.UUID) -> bool:
        row = conn.execute(
            select(volunteer_profiles.c.id).where(
                volunteer_profiles.c.id == profile_id,
                volunteer_profiles.c.user_id == user_id,
            )
        ).first()
        return row is not None

    def unavailability_periods(self, conn: Connection, profile_id: uuid.UUID) -> tuple[UnavailabilityRecord, ...]:
        rows = conn.execute(
            select(volunteer_unavailability)
            .where(volunteer_unavailability.c.volunteer_id == profile_id)
            .where(volunteer_unavailability.c.is_cancelled == False)  # noqa: E712
            .order_by(volunteer_unavailability.c.from_date)
        )
        return tuple(_unavailability(row) for row in rows)

    def unavailability_period(self, conn: Connection, period_id: uuid.UUID) -> UnavailabilityRecord | None:
        row = conn.execute(
            select(volunteer_unavailability).where(volunteer_unavailability.c.id == period_id)
        ).first()
        return None if row is None else _unavailability(row)

    def assigned_task_dates(self, conn: Connection, profile_id: uuid.UUID) -> tuple[date | None, ...]:
        """Dates of tasks this volunteer currently holds. A null date means the task has no preferred date."""
        rows = conn.execute(
            select(help_requests.c.preferred_date)
            .select_from(
                task_assignments.join(help_requests, help_requests.c.id == task_assignments.c.request_id)
            )
            .where(task_assignments.c.volunteer_id == profile_id)
            .where(task_assignments.c.status == "ASSIGNED")
        )
        return tuple(as_date(row.preferred_date) for row in rows)

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


def _volunteer_profile_record(row) -> VolunteerProfileRecord:
    raw_skills = json.loads(row.skills_json or "[]")
    skills = tuple(str(item) for item in raw_skills) if isinstance(raw_skills, list) else ()
    return VolunteerProfileRecord(
        profile_id=row.id,
        primary_city=row.primary_city,
        has_vehicle=bool(row.has_vehicle),
        skills=skills,
        experience=row.experience,
        base_frequency=row.base_frequency,
        preferred_time_from=_hhmm(getattr(row, "preferred_time_from", None)),
        preferred_time_to=_hhmm(getattr(row, "preferred_time_to", None)),
        preferred_weekdays=_weekday_codes(getattr(row, "preferred_weekdays_json", None)),
        availability_status=row.availability_status,
        max_active_tasks=int(row.max_active_tasks),
        max_parallel_tasks=int(row.max_parallel_tasks),
        is_enabled=bool(row.is_enabled),
    )


def _hhmm(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value[:5]
    return value.strftime("%H:%M")


def _weekday_codes(value) -> tuple[str, ...]:
    if not value:
        return ()
    raw = json.loads(value) if isinstance(value, str) else value
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _latest_release(
    conn: Connection, request_ids: set[uuid.UUID], profile_by_assignment: dict[uuid.UUID, uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    if not request_ids:
        return {}
    events = conn.execute(
        select(event_store.c.payload_json, event_store.c.created_at)
        .where(event_store.c.aggregate_id.in_(request_ids))
        .where(event_store.c.event_type == "TaskReleased")
    ).all()
    latest: dict[uuid.UUID, datetime] = {}
    for event in events:
        payload = json.loads(event.payload_json or "{}")
        raw_id = payload.get("assignment_id")
        if not raw_id:
            continue
        profile_id = profile_by_assignment.get(uuid.UUID(str(raw_id)))
        stamp = _naive(event.created_at)
        if profile_id is None or stamp is None:
            continue
        previous = latest.get(profile_id)
        if previous is None or stamp > previous:
            latest[profile_id] = stamp
    return latest


def _unavailability(row) -> UnavailabilityRecord:
    return UnavailabilityRecord(
        period_id=row.id,
        volunteer_id=row.volunteer_id,
        from_date=as_date(row.from_date) or date.min,
        until_date=as_date(row.until_date) or date.min,
        reason=row.reason,
        is_cancelled=bool(row.is_cancelled),
    )
