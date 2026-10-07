from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from app import create_app
from app.config import Config
from app.repositories.tables import metadata
from app.security.csrf import CSRF_COOKIE, CSRF_HEADER


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 6, 12, 0, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite+pysqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # pysqlite needs manual BEGIN for SAVEPOINT (begin_nested) to work.
    @event.listens_for(eng, "connect")
    def _no_implicit_transactions(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(eng, "begin")
    def _explicit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def config() -> Config:
    return Config(
        database_url="sqlite://",
        jwt_secret="test-secret-" + "x" * 40,
        encryption_key=Fernet.generate_key().decode(),
        bcrypt_rounds=4,
        cookie_secure=False,
        testing=True,
    )


@pytest.fixture
def app(config, engine, clock):
    return create_app(config=config, engine=engine, clock=clock)


@pytest.fixture
def client(app):
    test_client = app.test_client()
    test_client.get("/api/auth/csrf")
    return test_client


def csrf_post(client, url: str, json: dict | None = None, headers: dict | None = None):
    cookie = client.get_cookie(CSRF_COOKIE)
    merged = {CSRF_HEADER: cookie.value if cookie else ""}
    merged.update(headers or {})
    return client.post(url, json=json if json is not None else {}, headers=merged)


REQUESTER = {
    "email": "Dana@Example.com",
    "password": "correct-horse-battery",
    "full_name": "Dana Levi",
    "phone": "+972-50-1234567",
    "city": "Haifa",
    "home_address": "12 Herzl Street",
}

VOLUNTEER = {
    "email": "yossi@example.com",
    "password": "volunteer-pass-123",
    "full_name": "Yossi Cohen",
    "phone": "052-7654321",
    "city": "Haifa",
    "home_address": "4 Allenby Street",
}

VOLUNTEER_PROFILE = {
    "primary_city": "Haifa",
    "has_vehicle": True,
    "skills": ["First Aid", "driving"],
    "experience": "Five years with Magen David Adom as a volunteer medic.",
    "base_frequency": "WEEKLY",
}

REQUESTER_PROFILE = {
    "default_city": "Haifa",
    "default_address": "12 Herzl Street",
    "accessibility_notes": "Ground floor only",
    "emergency_contact_name": "Noa Levi",
    "emergency_contact_phone": "050-1112233",
}
