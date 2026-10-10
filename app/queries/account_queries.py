"""Read the signed-in person's account and whether each enrollment exists."""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Engine

from app.repositories.users import UserRepository
from app.security.encryption import FieldEncryptor


@dataclass(frozen=True)
class AccountView:
    user_id: uuid.UUID
    email: str
    full_name: str
    phone: str
    city: str | None
    home_address: str | None
    is_admin: bool
    has_requester_profile: bool
    has_volunteer_profile: bool


class GetMyAccountQuery:
    def __init__(self, engine: Engine, users: UserRepository, encryptor: FieldEncryptor) -> None:
        self._engine = engine
        self._users = users
        self._encryptor = encryptor

    def execute(self, user_id: uuid.UUID) -> AccountView | None:
        with self._engine.connect() as conn:
            account = self._users.get_account(conn, user_id)
            if account is None:
                return None
            return AccountView(
                user_id=account.user_id,
                email=account.email,
                full_name=account.full_name,
                phone=self._encryptor.decrypt(account.phone),
                city=account.city,
                home_address=account.home_address,
                is_admin=account.is_admin,
                has_requester_profile=self._users.requester_profile_id(conn, user_id) is not None,
                has_volunteer_profile=self._users.volunteer_profile_id(conn, user_id) is not None,
            )


@dataclass(frozen=True)
class UnavailabilityView:
    period_id: str
    from_date: str
    until_date: str
    reason: str | None


@dataclass(frozen=True)
class VolunteerProfileView:
    profile_id: str
    primary_city: str
    has_vehicle: bool
    skills: tuple[str, ...]
    skills_text: str
    experience: str
    base_frequency: str
    availability_status: str
    display_availability: str
    max_active_tasks: int
    max_parallel_tasks: int
    periods: tuple[UnavailabilityView, ...]


class GetMyProfilesQuery:
    """The signed-in person's volunteer profile and open unavailability periods."""

    def __init__(self, engine: Engine, users: UserRepository) -> None:
        self._engine = engine
        self._users = users

    def execute(self, user_id: uuid.UUID, *, today: date | None = None) -> VolunteerProfileView | None:
        today = today or date.today()
        with self._engine.connect() as conn:
            profile = self._users.volunteer_profile(conn, user_id)
            if profile is None:
                return None
            periods = self._users.unavailability_periods(conn, profile.profile_id)
        covering = any(item.from_date <= today <= item.until_date for item in periods)
        if profile.availability_status == "INACTIVE":
            display = "INACTIVE"
        elif covering:
            display = "TEMPORARILY_UNAVAILABLE"
        else:
            display = "AVAILABLE"
        return VolunteerProfileView(
            profile_id=str(profile.profile_id),
            primary_city=profile.primary_city,
            has_vehicle=profile.has_vehicle,
            skills=profile.skills,
            skills_text=", ".join(profile.skills),
            experience=profile.experience,
            base_frequency=profile.base_frequency,
            availability_status=profile.availability_status,
            display_availability=display,
            max_active_tasks=profile.max_active_tasks,
            max_parallel_tasks=profile.max_parallel_tasks,
            periods=tuple(
                UnavailabilityView(
                    period_id=str(item.period_id),
                    from_date=item.from_date.isoformat(),
                    until_date=item.until_date.isoformat(),
                    reason=item.reason,
                )
                for item in periods
            ),
        )
