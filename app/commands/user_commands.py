import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from app.commands.bus import CommandBus
from app.commands.dtos import BootstrapAdminCommand, LoginCommand, RegisterUserCommand
from app.domain.aggregates import UserAggregate
from app.domain.errors import (
    AccountLocked,
    AdminAlreadyExists,
    ConcurrencyConflict,
    EmailAlreadyRegistered,
    InvalidCredentials,
)
from app.domain.roles import derive_roles
from app.projections.projectors import UserProjector
from app.repositories.event_store import TransactionalEventStore
from app.repositories.login_attempts import LoginAttemptRepository
from app.repositories.users import UserRepository
from app.security.encryption import FieldEncryptor
from app.security.passwords import PasswordHasher

Clock = Callable[[], datetime]
LOGIN_EVENT_RETRIES = 3


def utc_now() -> datetime:
    """Naive UTC, matching DATETIME2 columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class RegisterResult:
    user_id: uuid.UUID


@dataclass(frozen=True)
class LoginResult:
    user_id: uuid.UUID
    full_name: str
    roles: list[str]


class UserCommandHandlers:
    def __init__(
        self,
        engine: Engine,
        event_store: TransactionalEventStore,
        projector: UserProjector,
        users: UserRepository,
        attempts: LoginAttemptRepository,
        hasher: PasswordHasher,
        encryptor: FieldEncryptor,
        lockout_max_failures: int,
        lockout_window: timedelta,
        clock: Clock = utc_now,
    ):
        self._engine = engine
        self._event_store = event_store
        self._projector = projector
        self._users = users
        self._attempts = attempts
        self._hasher = hasher
        self._encryptor = encryptor
        self._max_failures = lockout_max_failures
        self._window = lockout_window
        self._clock = clock

    def register_on(self, bus: CommandBus) -> None:
        bus.register(RegisterUserCommand, self.register_user)
        bus.register(LoginCommand, self.login)
        bus.register(BootstrapAdminCommand, self.bootstrap_admin)

    def register_user(self, cmd: RegisterUserCommand) -> RegisterResult:
        now = self._clock()
        user_id = uuid.uuid4()
        volunteer_payload = None
        if cmd.volunteer_profile is not None:
            vp = cmd.volunteer_profile
            volunteer_payload = {
                "profile_id": str(uuid.uuid4()),
                "primary_city": vp.primary_city,
                "has_vehicle": vp.has_vehicle,
                "skills": list(vp.skills),
                "experience": vp.experience,
                "base_frequency": vp.base_frequency,
                "max_active_tasks": vp.max_active_tasks,
                "max_parallel_tasks": vp.max_parallel_tasks,
            }
        events = UserAggregate.register(
            user_id=user_id,
            email=cmd.email,
            full_name=cmd.full_name,
            phone_encrypted=self._encryptor.encrypt(cmd.phone),
            password_hash=self._hasher.hash(cmd.password),
            volunteer_profile=volunteer_payload,
            now=now,
        )
        try:
            with self._engine.begin() as conn:
                if self._users.email_exists(conn, cmd.email):
                    raise EmailAlreadyRegistered("This email is already registered")
                self._event_store.append_in(conn, user_id, 0, events)
                self._projector.apply(conn, events)
        except IntegrityError as exc:
            raise EmailAlreadyRegistered("This email is already registered") from exc
        return RegisterResult(user_id=user_id)

    def login(self, cmd: LoginCommand) -> LoginResult:
        now = self._clock()
        with self._engine.begin() as conn:
            failures = self._attempts.recent_failures(conn, cmd.email, now - self._window, self._max_failures)
            if len(failures) >= self._max_failures:
                unlock_at = failures[-1] + self._window
                raise AccountLocked(max(1, math.ceil((unlock_at - now).total_seconds())))

            record = self._users.get_auth_record(conn, cmd.email)
            password_ok = self._hasher.verify(cmd.password, record.password_hash if record else None)
            succeeded = password_ok and record is not None and record.is_active
            self._attempts.record(conn, cmd.email, now, succeeded)

        if not succeeded:
            raise InvalidCredentials("Invalid email or password")

        self._append_login_event(record.user_id, now)
        roles = derive_roles(record.is_admin, record.has_enabled_volunteer_profile)
        return LoginResult(user_id=record.user_id, full_name=record.full_name, roles=roles)

    def bootstrap_admin(self, cmd: BootstrapAdminCommand) -> RegisterResult:
        now = self._clock()
        user_id = uuid.uuid4()
        events = UserAggregate.bootstrap_admin(
            user_id=user_id,
            email=cmd.email,
            full_name=cmd.full_name,
            phone_encrypted=self._encryptor.encrypt(cmd.phone),
            password_hash=self._hasher.hash(cmd.password),
            now=now,
        )
        with self._engine.begin() as conn:
            if self._users.admin_exists(conn):
                raise AdminAlreadyExists("An admin account already exists")
            if self._users.email_exists(conn, cmd.email):
                raise EmailAlreadyRegistered("This email already belongs to a non-admin account")
            self._event_store.append_in(conn, user_id, 0, events)
            self._projector.apply(conn, events)
        return RegisterResult(user_id=user_id)

    def _append_login_event(self, user_id: uuid.UUID, now: datetime) -> None:
        """Audit only; concurrent logins of one user race on version, so retry instead of failing the login."""
        for _ in range(LOGIN_EVENT_RETRIES):
            try:
                with self._engine.begin() as conn:
                    version = self._event_store.current_version(conn, user_id)
                    self._event_store.append_in(conn, user_id, version, [UserAggregate.logged_in(user_id, version, now)])
                return
            except ConcurrencyConflict:
                continue
        raise ConcurrencyConflict("Could not record login, please retry")
