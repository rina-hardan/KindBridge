"""Core foundation: config, aggregates, event store conflicts, command bus, HTTP 409."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.commands.bus import Command, CommandBus, CommandHandlerNotFound
from app.config import load_settings
from app.domain.aggregates import HelpRequest, User
from app.domain.errors import ConcurrencyConflict, Forbidden, Unauthorized
from app.domain.events import DomainEvent
from app.projections.projectors import UserProjector
from app.repositories.event_store import (
    InMemoryEventStore,
    SqlEventStore,
    is_aggregate_version_conflict,
)
from app.repositories.tables import users
from tests.conftest import csrf_post


def _registered(user_id: UUID | None = None, email: str = "ada@kindbridge.org") -> User:
    user = User(user_id or uuid4())
    user.raise_event(
        "UserRegistered",
        {
            "email": email,
            "full_name": "Ada",
            "phone_encrypted": "cipher",
            "city": "Haifa",
            "home_address": "1 Harbor Rd",
        },
    )
    return user


def test_load_settings_reads_env_and_keeps_bcrypt_cost_at_least_12():
    settings = load_settings(
        {
            "DATABASE_URL": "Driver={ODBC Driver 18 for SQL Server};SERVER=localhost",
            "JWT_SECRET": "test-secret",
            "BCRYPT_ROUNDS": "4",
            "CHROMA_PORT": "9000",
            "LLM_PROVIDER": "ollama",
        }
    )
    assert settings["DATABASE_URL"].startswith("Driver=")
    assert settings["JWT_SECRET"] == "test-secret"
    assert settings["SECRET_KEY"] == "test-secret"
    assert settings["BCRYPT_ROUNDS"] == 12
    assert settings["CHROMA_PORT"] == 9000
    assert settings["LLM_PROVIDER"] == "ollama"
    assert settings["JWT_ACCESS_TTL_MINUTES"] == 60


def test_event_rejects_plaintext_password_and_redacts_hash():
    with pytest.raises(ValueError, match="plaintext password"):
        DomainEvent(
            aggregate_id=uuid4(),
            aggregate_type="User",
            event_type="CredentialSet",
            payload={"password": "secret-value"},
            version=1,
        )

    event = DomainEvent(
        aggregate_id=uuid4(),
        aggregate_type="User",
        event_type="CredentialSet",
        payload={"password_hash": "hash-value", "email": "ada@kindbridge.org"},
        version=1,
    )
    text = repr(event)
    assert "hash-value" not in text
    assert "ada@kindbridge.org" in text
    assert event.to_row()["payload_json"].find("hash-value") != -1


def test_aggregate_versions_and_reloads_from_history():
    user = _registered()
    user.raise_event("CredentialSet", {"password_hash": "hashed"})
    assert user.version == 2
    assert user.expected_version == 0
    assert [event.version for event in user.uncommitted_events()] == [1, 2]

    restored = User.load(user.aggregate_id, user.uncommitted_events())
    assert restored.version == 2
    assert restored.expected_version == 2
    assert restored.uncommitted_events() == []


def test_apply_method_receives_matching_event():
    class TrackingRequest(HelpRequest):
        def __init__(self, aggregate_id=None):
            super().__init__(aggregate_id)
            self.created = None

        def apply_help_request_created(self, event: DomainEvent) -> None:
            self.created = event.payload["city"]

    request = TrackingRequest()
    request.raise_event("HelpRequestCreated", {"city": "Haifa"})
    assert request.created == "Haifa"


def test_in_memory_append_conflicts_on_stale_version():
    store = InMemoryEventStore()
    user = _registered()
    store.append(user.aggregate_id, user.expected_version, user.uncommitted_events())
    user.mark_committed()

    stale = _registered(user.aggregate_id, email="other@kindbridge.org")
    with pytest.raises(ConcurrencyConflict) as caught:
        store.append(stale.aggregate_id, stale.expected_version, stale.uncommitted_events())
    assert caught.value.status_code == 409

    stream = store.load_stream(user.aggregate_id)
    assert len(stream) == 1
    assert stream[0].payload["email"] == "ada@kindbridge.org"
    assert stream[0].version == 1


def test_command_bus_commits_and_second_writer_conflicts():
    store = InMemoryEventStore()
    bus = CommandBus(store)

    @dataclass(frozen=True)
    class RegisterUser(Command):
        user_id: UUID
        email: str

    @bus.handler(RegisterUser)
    def handle(command: RegisterUser):
        user = User(command.user_id)
        user.raise_event("UserRegistered", {"email": command.email, "full_name": "Ada"})
        bus.commit(user)
        return user.version

    user_id = uuid4()
    assert bus.dispatch(RegisterUser(user_id=user_id, email="ada@kindbridge.org")) == 1

    with pytest.raises(ConcurrencyConflict):
        bus.dispatch(RegisterUser(user_id=user_id, email="ada@kindbridge.org"))

    assert store.load_stream(user_id)[0].event_type == "UserRegistered"


def test_commit_conflict_leaves_events_uncommitted():
    store = InMemoryEventStore()
    bus = CommandBus(store)
    user_id = uuid4()
    first = _registered(user_id)
    bus.commit(first)

    stale = _registered(user_id, email="other@kindbridge.org")
    with pytest.raises(ConcurrencyConflict):
        bus.commit(stale)
    assert len(stale.uncommitted_events()) == 1


def test_commit_passes_events_then_connection_to_the_projector(engine):
    store = SqlEventStore(engine=engine)
    bus = CommandBus(store)
    user = _registered()
    user.raise_event("CredentialSet", {"password_hash": "hashed"})

    created_at = user.uncommitted_events()[0].created_at
    bus.commit(user, projector=UserProjector())

    with engine.connect() as conn:
        row = conn.execute(select(users)).one()
    assert row.created_at.tzinfo is None
    assert row.created_at == created_at.astimezone(timezone.utc).replace(tzinfo=None)
    assert row.email == "ada@kindbridge.org"
    assert row.full_name == "Ada"
    assert row.city == "Haifa"
    assert row.home_address == "1 Harbor Rd"
    assert row.password_hash == "hashed"
    assert store.load_stream(user.aggregate_id)[1].event_type == "CredentialSet"


def test_projector_failure_does_not_append():
    store = InMemoryEventStore()
    user = _registered()

    def fail(_events, _connection):
        raise RuntimeError("projector failed")

    with pytest.raises(RuntimeError, match="projector failed"):
        store.append(
            user.aggregate_id,
            user.expected_version,
            user.uncommitted_events(),
            projector=fail,
        )
    assert store.load_stream(user.aggregate_id) == []


def test_unknown_command_and_duplicate_handler():
    bus = CommandBus(InMemoryEventStore())

    @dataclass(frozen=True)
    class Ping(Command):
        pass

    def handle(_command: Ping) -> str:
        return "ok"

    bus.register(Ping, handle)
    with pytest.raises(ValueError, match="already registered"):
        bus.register(Ping, handle)
    assert bus.dispatch(Ping()) == "ok"

    @dataclass(frozen=True)
    class Missing(Command):
        pass

    with pytest.raises(CommandHandlerNotFound):
        bus.dispatch(Missing())


def test_version_conflict_detector_matches_unique_constraint_only():
    version_error = IntegrityError(
        "INSERT",
        {},
        Exception("Violation of UNIQUE KEY constraint 'UQ_event_store_aggregate_version'."),
    )
    primary_key_error = IntegrityError(
        "INSERT",
        {},
        Exception("Violation of PRIMARY KEY constraint 'PK_event_store'. Cannot insert duplicate key."),
    )
    assert is_aggregate_version_conflict(version_error) is True
    assert is_aggregate_version_conflict(primary_key_error) is False


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Connection:
    def __init__(self, version: int, on_insert):
        self._version = version
        self._on_insert = on_insert
        self.calls = 0

    def execute(self, _statement, _params=None):
        self.calls += 1
        if self.calls == 1:
            return _Result(self._version)
        self._on_insert()
        return _Result(None)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, connection: _Connection):
        self._connection = connection

    def begin(self):
        return self._connection


def test_sql_event_store_maps_unique_violation_to_409():
    def boom():
        raise IntegrityError(
            "INSERT",
            {},
            Exception("Violation of UNIQUE KEY constraint 'UQ_event_store_aggregate_version'."),
        )

    store = SqlEventStore("unused", engine=_Engine(_Connection(0, boom)))
    user = _registered()
    with pytest.raises(ConcurrencyConflict) as caught:
        store.append(user.aggregate_id, 0, user.uncommitted_events())
    assert caught.value.status_code == 409


def test_sql_event_store_rejects_stale_expected_version():
    store = SqlEventStore("unused", engine=_Engine(_Connection(2, lambda: None)))
    user = _registered()
    with pytest.raises(ConcurrencyConflict, match="found 2"):
        store.append(user.aggregate_id, 0, user.uncommitted_events())


def test_round_trip_row_preserves_event_fields():
    created_at = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    correlation_id = uuid4()
    event = DomainEvent(
        aggregate_id=uuid4(),
        aggregate_type="HelpRequest",
        event_type="HelpRequestCreated",
        payload={"city": "חיפה"},
        version=3,
        created_at=created_at,
        correlation_id=correlation_id,
    )
    restored = DomainEvent.from_row(event.to_row())
    assert restored.event_id == event.event_id
    assert restored.version == 3
    assert restored.payload["city"] == "חיפה"
    assert restored.correlation_id == correlation_id
    assert restored.created_at == created_at


def test_http_conflict_and_auth_errors(app):
    @app.post("/_conflict")
    def _conflict():
        raise ConcurrencyConflict("expected version 1 but found 2")

    @app.get("/_private")
    def _private():
        raise Unauthorized("login required")

    @app.get("/_forbidden")
    def _forbidden():
        raise Forbidden("volunteer cannot approve")

    client = app.test_client()
    client.get("/api/auth/csrf")

    conflict = csrf_post(client, "/_conflict")
    assert conflict.status_code == 409
    assert conflict.get_json()["error"] == "conflict"

    unauthorized = client.get("/_private")
    assert unauthorized.status_code == 401
    assert unauthorized.get_json()["error"] == "unauthorized"

    forbidden = client.get("/_forbidden")
    assert forbidden.status_code == 403
    assert forbidden.get_json()["error"] == "forbidden"

    services = app.extensions["kindbridge"]
    assert isinstance(services.bus, CommandBus)
    assert services.bus.event_store is services.event_store
    assert isinstance(services.event_store, SqlEventStore)


def test_production_factory_requires_database_and_jwt_secret(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app()

    monkeypatch.setenv("DATABASE_URL", "Driver={ODBC Driver 18 for SQL Server}")
    monkeypatch.setenv("JWT_SECRET", "")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app()
