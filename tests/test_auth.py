import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.commands.dtos import BootstrapAdminCommand
from app.domain.errors import AdminAlreadyExists, EmailAlreadyRegistered
from app.repositories.tables import event_store, login_attempts, users, volunteer_profiles
from app.security.auth import ACCESS_COOKIE, require_auth
from tests.conftest import REQUESTER, VOLUNTEER, csrf_post


def register(client, payload):
    return csrf_post(client, "/api/auth/register", payload)


def login(client, email, password):
    return csrf_post(client, "/api/auth/login", {"email": email, "password": password})


def bus(app):
    return app.extensions["kindbridge"].bus


# --- Registration ---------------------------------------------------------------------------


def test_register_requester_creates_events_and_projection(client, engine):
    response = register(client, REQUESTER)

    assert response.status_code == 201
    user_id = response.get_json()["user_id"]
    with engine.connect() as conn:
        rows = conn.execute(select(event_store).order_by(event_store.c.version)).all()
        user = conn.execute(select(users)).one()
    assert [(r.event_type, r.version) for r in rows] == [("UserRegistered", 1), ("CredentialSet", 2)]
    assert str(rows[0].aggregate_id).replace("-", "") == user_id.replace("-", "")
    assert user.email == "dana@example.com"
    assert user.is_admin is False
    assert user.password_hash.startswith("$2")


def test_register_never_stores_plaintext_password_or_phone(client, engine):
    register(client, REQUESTER)

    with engine.connect() as conn:
        payloads = " ".join(conn.execute(select(event_store.c.payload_json)).scalars())
        stored_phone = conn.execute(select(users.c.phone)).scalar_one()
    assert REQUESTER["password"] not in payloads
    assert REQUESTER["phone"] not in payloads
    assert stored_phone != REQUESTER["phone"]


def test_register_volunteer_projects_profile(client, engine):
    response = register(client, VOLUNTEER)

    assert response.status_code == 201
    with engine.connect() as conn:
        profile = conn.execute(select(volunteer_profiles)).one()
        event_types = list(conn.execute(select(event_store.c.event_type).order_by(event_store.c.version)).scalars())
    assert event_types == ["UserRegistered", "CredentialSet", "VolunteerProfileEnabled"]
    assert profile.primary_city == "Haifa"
    assert json.loads(profile.skills_json) == ["first aid", "driving"]
    assert profile.max_active_tasks == 1
    assert profile.max_parallel_tasks == 2


def test_register_duplicate_email_is_case_insensitive(client):
    register(client, REQUESTER)
    duplicate = dict(REQUESTER, email="DANA@example.COM")

    response = register(client, duplicate)

    assert response.status_code == 409
    assert response.get_json()["error"] == "email_taken"


@pytest.mark.parametrize("key", ["is_admin", "role", "roles"])
def test_register_rejects_role_self_assignment(client, key):
    response = register(client, dict(REQUESTER, **{key: True}))

    assert response.status_code == 400
    assert key in response.get_json()["fields"]


def test_register_validates_fields(client):
    response = register(client, {"email": "not-an-email", "password": "short", "full_name": " ", "phone": "abc"})

    assert response.status_code == 400
    assert set(response.get_json()["fields"]) == {"email", "password", "full_name", "phone"}


def test_register_rejects_password_over_bcrypt_limit(client):
    response = register(client, dict(REQUESTER, password="a" * 73))

    assert response.status_code == 400
    assert "password" in response.get_json()["fields"]


def test_register_validates_volunteer_profile(client):
    payload = json.loads(json.dumps(VOLUNTEER))
    payload["volunteer_profile"]["base_frequency"] = "DAILY"
    payload["volunteer_profile"]["max_active_tasks"] = 0

    response = register(client, payload)

    fields = response.get_json()["fields"]
    assert response.status_code == 400
    assert "volunteer_profile.base_frequency" in fields
    assert "volunteer_profile.max_active_tasks" in fields


# --- CSRF -----------------------------------------------------------------------------------


def test_post_without_csrf_header_is_rejected(client):
    response = client.post("/api/auth/register", json=REQUESTER)

    assert response.status_code == 403
    assert response.get_json()["error"] == "csrf_failed"


def test_post_with_mismatched_csrf_header_is_rejected(client):
    response = csrf_post(client, "/api/auth/login", REQUESTER, headers={"X-CSRF-Token": "forged"})

    assert response.status_code == 403


def test_first_visit_receives_csrf_cookie(app):
    response = app.test_client().get("/login")

    assert "kb_csrf=" in response.headers.get("Set-Cookie", "")


def test_login_rotates_csrf_token(client):
    register(client, REQUESTER)
    before = client.get_cookie("kb_csrf").value

    login(client, REQUESTER["email"], REQUESTER["password"])

    assert client.get_cookie("kb_csrf").value != before


# --- Login and JWT cookie -------------------------------------------------------------------


def test_login_sets_httponly_jwt_cookie_with_roles(client, app):
    register(client, REQUESTER)

    response = login(client, "dana@example.com", REQUESTER["password"])

    assert response.status_code == 200
    assert response.get_json()["roles"] == ["REQUESTER"]
    access_header = next(h for h in response.headers.getlist("Set-Cookie") if h.startswith(ACCESS_COOKIE))
    assert "HttpOnly" in access_header
    assert "SameSite=Lax" in access_header
    identity = app.extensions["kindbridge"].tokens.verify(client.get_cookie(ACCESS_COOKIE).value)
    assert identity.roles == ("REQUESTER",)


def test_login_cookie_is_secure_when_configured(config, engine, clock):
    from dataclasses import replace

    from app import create_app

    secure_app = create_app(config=replace(config, cookie_secure=True), engine=engine, clock=clock)
    client = secure_app.test_client()
    https = "https://localhost"
    client.get("/api/auth/csrf", base_url=https)
    headers = {"X-CSRF-Token": client.get_cookie("kb_csrf", domain="localhost").value}
    client.post("/api/auth/register", json=REQUESTER, headers=headers, base_url=https)

    response = client.post(
        "/api/auth/login",
        json={"email": REQUESTER["email"], "password": REQUESTER["password"]},
        headers=headers,
        base_url=https,
    )

    access_header = next(h for h in response.headers.getlist("Set-Cookie") if h.startswith(ACCESS_COOKIE))
    assert "Secure" in access_header


def test_volunteer_gets_both_roles(client):
    register(client, VOLUNTEER)

    response = login(client, VOLUNTEER["email"], VOLUNTEER["password"])

    assert response.get_json()["roles"] == ["REQUESTER", "VOLUNTEER"]


def test_login_appends_audit_event(client, engine):
    register(client, REQUESTER)
    login(client, REQUESTER["email"], REQUESTER["password"])
    login(client, REQUESTER["email"], REQUESTER["password"])

    with engine.connect() as conn:
        rows = conn.execute(select(event_store.c.event_type, event_store.c.version).order_by(event_store.c.version)).all()
    assert rows[-2:] == [("UserLoggedIn", 3), ("UserLoggedIn", 4)]


def test_bad_password_returns_401(client):
    register(client, REQUESTER)

    response = login(client, REQUESTER["email"], "wrong-password-123")

    assert response.status_code == 401
    assert ACCESS_COOKIE not in response.headers.get("Set-Cookie", "")


def test_unknown_email_returns_same_401(client):
    response = login(client, "nobody@example.com", "whatever-password")

    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid_credentials"


def test_inactive_user_cannot_log_in(client, engine):
    register(client, REQUESTER)
    with engine.begin() as conn:
        conn.execute(update(users).values(is_active=False))

    response = login(client, REQUESTER["email"], REQUESTER["password"])

    assert response.status_code == 401


# --- Lockout --------------------------------------------------------------------------------


def test_lockout_after_five_failures_even_with_correct_password(client, clock):
    register(client, REQUESTER)
    for _ in range(5):
        assert login(client, REQUESTER["email"], "wrong-password-123").status_code == 401

    response = login(client, REQUESTER["email"], REQUESTER["password"])

    assert response.status_code == 429
    assert response.get_json()["error"] == "account_locked"
    assert int(response.headers["Retry-After"]) == 15 * 60


def test_lockout_expires_after_window(client, clock):
    register(client, REQUESTER)
    for _ in range(5):
        login(client, REQUESTER["email"], "wrong-password-123")

    clock.advance(minutes=15, seconds=1)
    response = login(client, REQUESTER["email"], REQUESTER["password"])

    assert response.status_code == 200


def test_attempts_while_locked_do_not_extend_lock(client, clock, engine):
    register(client, REQUESTER)
    for _ in range(5):
        login(client, REQUESTER["email"], "wrong-password-123")

    clock.advance(minutes=10)
    login(client, REQUESTER["email"], "wrong-password-123")
    with engine.connect() as conn:
        recorded = conn.execute(select(login_attempts)).all()

    assert len(recorded) == 5


def test_successful_login_resets_failure_count(client):
    register(client, REQUESTER)
    for _ in range(4):
        login(client, REQUESTER["email"], "wrong-password-123")
    assert login(client, REQUESTER["email"], REQUESTER["password"]).status_code == 200
    for _ in range(4):
        login(client, REQUESTER["email"], "wrong-password-123")

    assert login(client, REQUESTER["email"], REQUESTER["password"]).status_code == 200


def test_lockout_is_per_email(client):
    register(client, REQUESTER)
    register(client, VOLUNTEER)
    for _ in range(5):
        login(client, REQUESTER["email"], "wrong-password-123")

    assert login(client, VOLUNTEER["email"], VOLUNTEER["password"]).status_code == 200


# --- Session endpoints and role checks ------------------------------------------------------


def test_me_requires_login(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_identity_after_login(client):
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.get_json()["roles"] == ["REQUESTER", "VOLUNTEER"]


def test_expired_token_is_rejected(client, app):
    tokens = app.extensions["kindbridge"].tokens
    import uuid

    expired = tokens.issue(uuid.uuid4(), ["REQUESTER"], now=datetime.now(timezone.utc) - timedelta(hours=2))
    client.set_cookie(ACCESS_COOKIE, expired)

    assert client.get("/api/auth/me").status_code == 401


def test_tampered_token_is_rejected(client):
    register(client, REQUESTER)
    login(client, REQUESTER["email"], REQUESTER["password"])
    token = client.get_cookie(ACCESS_COOKIE).value
    header, payload, signature = token.split(".")
    client.set_cookie(ACCESS_COOKIE, f"{header}.{payload}.{signature[::-1]}")

    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_session(client):
    register(client, REQUESTER)
    login(client, REQUESTER["email"], REQUESTER["password"])

    assert csrf_post(client, "/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


@pytest.fixture
def probe_client(app):
    @app.get("/_probe/admin-only")
    @require_auth("ADMIN")
    def admin_only():
        return {"ok": True}

    test_client = app.test_client()
    test_client.get("/api/auth/csrf")
    return test_client


def test_non_admin_is_forbidden_from_admin_route(probe_client):
    register(probe_client, VOLUNTEER)
    login(probe_client, VOLUNTEER["email"], VOLUNTEER["password"])

    assert probe_client.get("/_probe/admin-only").status_code == 403


def test_anonymous_gets_401_on_admin_route(probe_client):
    assert probe_client.get("/_probe/admin-only").status_code == 401


# --- Admin bootstrap ------------------------------------------------------------------------


def test_bootstrap_admin_gets_exclusive_admin_role(app, probe_client):
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))

    response = login(probe_client, "root@kindbridge.org", "admin-password-123")

    assert response.get_json()["roles"] == ["ADMIN"]
    assert probe_client.get("/_probe/admin-only").status_code == 200


def test_bootstrap_admin_runs_only_once(app):
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))

    with pytest.raises(AdminAlreadyExists):
        bus(app).dispatch(BootstrapAdminCommand.create("other@kindbridge.org", "admin-password-123", "Other"))


def test_bootstrap_admin_refuses_existing_user_email(app, client):
    register(client, REQUESTER)

    with pytest.raises(EmailAlreadyRegistered):
        bus(app).dispatch(BootstrapAdminCommand.create(REQUESTER["email"], "admin-password-123", "Root"))


def test_bootstrap_admin_writes_admin_event(app, engine):
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))

    with engine.connect() as conn:
        event_types = list(conn.execute(select(event_store.c.event_type).order_by(event_store.c.version)).scalars())
    assert event_types == ["UserRegistered", "CredentialSet", "AdminBootstrapped"]


# --- Views ----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/login", "/register"])
def test_auth_pages_render(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert b"KindBridge" in response.data


def test_hebrew_browser_gets_rtl_layout(client):
    response = client.get("/login", headers={"Accept-Language": "he-IL,he;q=0.9"})

    assert b'dir="rtl"' in response.data
    assert b"bootstrap.rtl.min.css" in response.data
