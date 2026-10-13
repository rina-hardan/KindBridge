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
    preferred_time_from: str | None
    preferred_time_to: str | None
    preferred_weekdays: tuple[str, ...]
    availability_status: str
    display_availability: str
    max_active_tasks: int
    max_parallel_tasks: int
    periods: tuple[UnavailabilityView, ...]


# A want-to-do row stays off the volunteer screen while a placement is proposed,
# assigned to this profile, or already completed.
_HIDDEN_FROM_WANTED = ("PROPOSED", "ASSIGNED", "COMPLETED")


class GetMyProfilesQuery:
    """The signed-in person's volunteerings and the open unavailability periods on each."""

    def __init__(self, engine: Engine, users: UserRepository) -> None:
        self._engine = engine
        self._users = users

    def list_for(self, user_id: uuid.UUID, *, today: date | None = None) -> tuple[VolunteerProfileView, ...]:
        return self._listed(user_id, today or date.today(), wanted_only=False)

    def list_wanted(self, user_id: uuid.UUID, *, today: date | None = None) -> tuple[VolunteerProfileView, ...]:
        """Volunteerings still shown under "Volunteering you want to do"."""
        return self._listed(user_id, today or date.today(), wanted_only=True)

    def _listed(
        self, user_id: uuid.UUID, today: date, *, wanted_only: bool
    ) -> tuple[VolunteerProfileView, ...]:
        with self._engine.connect() as conn:
            profiles = self._users.list_volunteer_profiles(conn, user_id)
            if wanted_only:
                hidden = self._users.volunteer_profile_ids_with_status(conn, user_id, _HIDDEN_FROM_WANTED)
                profiles = tuple(profile for profile in profiles if profile.profile_id not in hidden)
            periods = {
                profile.profile_id: self._users.unavailability_periods(conn, profile.profile_id) for profile in profiles
            }
        return tuple(self._view(profile, periods[profile.profile_id], today) for profile in profiles)

    def execute(self, user_id: uuid.UUID, *, today: date | None = None) -> VolunteerProfileView | None:
        found = self.list_for(user_id, today=today)
        return None if not found else found[0]

    def execute_one(
        self, user_id: uuid.UUID, profile_id: uuid.UUID, *, today: date | None = None
    ) -> VolunteerProfileView | None:
        today = today or date.today()
        with self._engine.connect() as conn:
            profile = self._users.volunteer_profile_owned(conn, user_id, profile_id)
            if profile is None:
                return None
            periods = self._users.unavailability_periods(conn, profile.profile_id)
        return self._view(profile, periods, today)

    def _view(self, profile, periods, today: date) -> VolunteerProfileView:
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
            preferred_time_from=profile.preferred_time_from,
            preferred_time_to=profile.preferred_time_to,
            preferred_weekdays=profile.preferred_weekdays,
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
