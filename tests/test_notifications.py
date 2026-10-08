"""Gmail notifications: recipients, post-commit behaviour, failure isolation. No network."""

import asyncio
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, update

from app import create_app
from app.commands.match_commands import MatchCommandHandlers, ProposeMatchCommand
from app.config import Config, load_settings
from app.infrastructure import notifications as mails
from app.infrastructure.notification_service import NoNotifications, NotificationService
from app.infrastructure.notifier import (
    FakeNotifier,
    GmailMcpNotifier,
    GmailSettings,
    NotifyError,
    NullNotifier,
    PermanentNotifyError,
    _run_sync,
    build_notifier,
    mask_address,
)
from app.projections.match_projector import MatchProjector
from app.repositories.event_store import SqlEventStore
from app.repositories.matching import SqlMatchingReader
from app.repositories.tables import event_store, help_requests, users
from app.repositories.users import UserRepository
from tests.conftest import REQUESTER, REQUESTER_PROFILE, VOLUNTEER, VOLUNTEER_PROFILE, csrf_post
from tests.test_auth import register
from tests.test_matching import Catalog, _hit, _open_request, _user, _volunteer
from tests.test_requests import _append, _created_payload, _enable, _login_admin, _propose, _status

REQUESTER_MAIL = REQUESTER["email"].lower()
VOLUNTEER_MAIL = VOLUNTEER["email"].lower()
ADMIN_MAIL = "root@kindbridge.org"
CLIENT_ID = "client-id-123.apps.example"
CLIENT_SECRET = "super-secret-value-xyz"
BASE_URL = "https://kb.example"


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def app(config, engine, clock, notifier):
    return create_app(config=config, engine=engine, clock=clock, notifier=notifier)


def _assigned_world(app, client):
    """A requester, an enabled volunteer, and a request with one proposal. Returns the ids."""
    requester_id = register(client, REQUESTER).get_json()["user_id"]
    register(client, VOLUNTEER)
    profile_id = UUID(_enable(client, VOLUNTEER_PROFILE).get_json()["profile_id"])
    request_id, assignment_id = uuid4(), uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _propose(app, request_id, profile_id, assignment_id)
    _login_admin(app, client)
    return request_id, assignment_id, profile_id


# --- HTTP command side -------------------------------------------------------------------------


def test_approve_emails_the_volunteer_and_the_requester(app, client, notifier, engine):
    request_id, assignment_id, _ = _assigned_world(app, client)

    response = csrf_post(client, f"/api/requests/{request_id}/approve", {"assignment_id": str(assignment_id)})

    assert response.status_code == 200
    assert sorted(mail.to for mail in notifier.sent) == sorted([VOLUNTEER_MAIL, REQUESTER_MAIL])
    to_volunteer = notifier.to(VOLUNTEER_MAIL)[0]
    to_requester = notifier.to(REQUESTER_MAIL)[0]
    assert "שובצת" in to_volunteer.subject
    assert "/me/tasks" in to_volunteer.body
    assert "Yossi Cohen" in to_requester.body
    assert f"/requests/{request_id}" in to_requester.body
    everything = "\n".join(mail.body for mail in notifier.sent)
    # Address and phone are shown only inside the app, to the assigned volunteer.
    for private in (REQUESTER["home_address"], REQUESTER["phone"], VOLUNTEER["phone"], "12 Harbor Road"):
        assert private not in everything


def test_override_emails_the_volunteer_and_the_requester(app, client, notifier, engine):
    requester_id = register(client, REQUESTER).get_json()["user_id"]
    register(client, VOLUNTEER)
    profile_id = UUID(_enable(client, VOLUNTEER_PROFILE).get_json()["profile_id"])
    request_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _append(
        app,
        request_id,
        1,
        "NoMatchFound",
        {"match_attempt": 0, "reason": "no_eligible_volunteers", "rejection_summary": {"geography": 1}},
        2,
    )
    _login_admin(app, client)

    response = csrf_post(
        client,
        f"/api/requests/{request_id}/override",
        {"volunteer_id": str(profile_id), "reason": "dispatcher knows this volunteer"},
    )

    assert response.status_code == 200
    assert sorted(mail.to for mail in notifier.sent) == sorted([VOLUNTEER_MAIL, REQUESTER_MAIL])


def test_cancel_from_assigned_emails_the_volunteer_only(app, client, notifier, engine):
    request_id, assignment_id, _ = _assigned_world(app, client)
    csrf_post(client, f"/api/requests/{request_id}/approve", {"assignment_id": str(assignment_id)})
    notifier.sent.clear()

    response = csrf_post(client, f"/api/requests/{request_id}/cancel", {})

    assert response.status_code == 200
    assert _status(engine, request_id) == "CANCELLED"
    assert [mail.to for mail in notifier.sent] == [VOLUNTEER_MAIL]
    assert "בוטלה" in notifier.sent[0].subject


def test_cancel_from_pending_sends_nothing(app, client, notifier, engine):
    owner_id = register(client, REQUESTER).get_json()["user_id"]
    request_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(owner_id), 1)

    response = csrf_post(client, f"/api/requests/{request_id}/cancel", {})

    assert response.status_code == 200
    assert _status(engine, request_id) == "CANCELLED"
    assert notifier.sent == []


def test_cancel_with_a_proposal_but_no_assignment_sends_nothing(app, client, notifier, engine):
    request_id, _, _ = _assigned_world(app, client)

    response = csrf_post(client, f"/api/requests/{request_id}/cancel", {})

    assert response.status_code == 200
    assert notifier.sent == []


def test_a_raising_notifier_never_rolls_back_the_approval(app, client, notifier, engine):
    request_id, assignment_id, _ = _assigned_world(app, client)
    notifier.raises = RuntimeError("gmail is down")

    response = csrf_post(client, f"/api/requests/{request_id}/approve", {"assignment_id": str(assignment_id)})

    assert response.status_code == 200
    assert _status(engine, request_id) == "ASSIGNED"
    with engine.connect() as conn:
        types = conn.execute(
            select(event_store.c.event_type).where(event_store.c.aggregate_id == request_id)
        ).scalars().all()
    assert "AssignmentApproved" in types


def test_a_raising_notifier_never_rolls_back_the_cancel(app, client, notifier, engine):
    request_id, assignment_id, _ = _assigned_world(app, client)
    csrf_post(client, f"/api/requests/{request_id}/approve", {"assignment_id": str(assignment_id)})
    notifier.raises = RuntimeError("gmail is down")

    response = csrf_post(client, f"/api/requests/{request_id}/cancel", {})

    assert response.status_code == 200
    assert _status(engine, request_id) == "CANCELLED"


def test_a_rejected_approval_sends_nothing(app, client, notifier):
    request_id, _, _ = _assigned_world(app, client)

    response = csrf_post(client, f"/api/requests/{request_id}/approve", {"assignment_id": str(uuid4())})

    assert response.status_code >= 400
    assert notifier.sent == []


# --- agent side: ProposeMatchCommand -----------------------------------------------------------


def _admin(conn, email=ADMIN_MAIL, *, active=True):
    conn.execute(
        insert(users).values(
            id=uuid4(),
            email=email,
            password_hash="hash",
            full_name="Admin Person",
            phone="cipher",
            city=None,
            home_address=None,
            is_admin=True,
            is_active=active,
            created_at=datetime(2026, 10, 6, 12, 0, 0),
        )
    )


def _service(engine, fake, **kwargs):
    return NotificationService(
        engine, fake, UserRepository(), SqlMatchingReader(), base_url=BASE_URL, **kwargs
    )


def _match_handler(engine, clock, resumes, notifications):
    return MatchCommandHandlers(
        engine=engine,
        event_store=SqlEventStore(engine=engine),
        reader=SqlMatchingReader(),
        projector=MatchProjector(),
        resumes=resumes,
        clock=clock,
        sleep=lambda _seconds: None,
        notifications=notifications,
    )


def _propose_match(handler, request_id, attempt=0):
    return handler.handle(ProposeMatchCommand(request_id=request_id, match_attempt=attempt))


def _name(conn, user_id, full_name):
    conn.execute(update(users).where(users.c.id == user_id).values(full_name=full_name))


def test_matches_proposed_emails_the_admin_with_the_top_candidates(engine, clock):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn)
        (first_user, first), (second_user, second) = _volunteer(conn), _volunteer(conn)
        _name(conn, first_user, "Noa Strong")
        _name(conn, second_user, "Avi Weak")
    _open_request(engine, request_id, requester)
    resumes = Catalog([_hit(first, 0.95), _hit(second, 0.10)])

    result = _propose_match(_match_handler(engine, clock, resumes, _service(engine, fake)), request_id)

    assert result.outcome == "proposed"
    assert [mail.to for mail in fake.sent] == [ADMIN_MAIL]
    mail = fake.sent[0]
    assert mail.subject == "הצעת התאמה חדשה ממתינה לבדיקה"
    assert str(request_id) in mail.body
    assert "הסעות" in mail.body and "Haifa" in mail.body and "חירום" in mail.body
    assert mail.body.index("Noa Strong") < mail.body.index("Avi Weak")
    assert "ציון" in mail.body
    assert f"{BASE_URL}/requests/{request_id}" in mail.body


def test_no_match_emails_the_admin_with_the_rejection_summary(engine, clock):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn)
        _, far_away = _volunteer(conn, city="Eilat")
    _open_request(engine, request_id, requester)
    resumes = Catalog([_hit(far_away, 0.9)])

    result = _propose_match(_match_handler(engine, clock, resumes, _service(engine, fake)), request_id)

    assert result.outcome == "no_match"
    assert [mail.to for mail in fake.sent] == [ADMIN_MAIL]
    mail = fake.sent[0]
    assert "לא נמצאה התאמה" in mail.subject
    assert "עיר לא מתאימה: 1" in mail.body
    assert str(request_id) in mail.body


def test_a_second_poll_of_the_same_attempt_does_not_email_again(engine, clock):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn)
        _, profile = _volunteer(conn)
    _open_request(engine, request_id, requester)
    handler = _match_handler(engine, clock, Catalog([_hit(profile, 0.9)]), _service(engine, fake))

    first = _propose_match(handler, request_id)
    second = _propose_match(handler, request_id)

    assert first.outcome == "proposed"
    assert second.outcome == "noop"
    assert len(fake.sent) == 1


def test_a_proposal_that_fails_to_commit_sends_no_email(engine, clock):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn)
        _, profile = _volunteer(conn)
    _open_request(engine, request_id, requester)
    handler = _match_handler(engine, clock, Catalog([_hit(profile, 0.9)]), _service(engine, fake))

    with pytest.raises(Exception):
        _propose_match(handler, request_id, attempt=7)  # not the stream head: the event is not written

    assert fake.sent == []


def test_a_raising_notifier_never_rolls_back_the_proposal(engine, clock):
    fake = FakeNotifier(raises=RuntimeError("gmail is down"))
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn)
        _, profile = _volunteer(conn)
    _open_request(engine, request_id, requester)
    handler = _match_handler(engine, clock, Catalog([_hit(profile, 0.9)]), _service(engine, fake))

    result = _propose_match(handler, request_id)

    assert result.outcome == "proposed"
    with engine.connect() as conn:
        status = conn.execute(select(help_requests.c.status).where(help_requests.c.id == request_id)).scalar_one()
    assert status == "MATCH_PROPOSED"


def test_a_failure_while_resolving_recipients_never_reaches_the_command(engine, clock, caplog):
    class BrokenUsers(UserRepository):
        def active_admin_contacts(self, conn):
            raise RuntimeError("db hiccup")

    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn)
        _, profile = _volunteer(conn)
    _open_request(engine, request_id, requester)
    service = NotificationService(engine, fake, BrokenUsers(), SqlMatchingReader(), base_url=BASE_URL)
    handler = _match_handler(engine, clock, Catalog([_hit(profile, 0.9)]), service)

    with caplog.at_level(logging.ERROR):
        result = _propose_match(handler, request_id)

    assert result.outcome == "proposed"
    assert fake.sent == []
    assert "notify_failed" in caplog.text


def test_every_active_admin_is_notified_but_not_inactive_ones(engine, clock):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn, "a@kindbridge.org")
        _admin(conn, "b@kindbridge.org")
        _admin(conn, "gone@kindbridge.org", active=False)
        _, profile = _volunteer(conn)
    _open_request(engine, request_id, requester)
    handler = _match_handler(engine, clock, Catalog([_hit(profile, 0.9)]), _service(engine, fake))

    _propose_match(handler, request_id)

    assert sorted(mail.to for mail in fake.sent) == ["a@kindbridge.org", "b@kindbridge.org"]


def test_admin_notify_email_narrows_the_list_to_that_admin(engine, clock):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn, "a@kindbridge.org")
        _admin(conn, "b@kindbridge.org")
        _, profile = _volunteer(conn)
    _open_request(engine, request_id, requester)
    service = _service(engine, fake, admin_notify_email="B@KindBridge.org")
    handler = _match_handler(engine, clock, Catalog([_hit(profile, 0.9)]), service)

    _propose_match(handler, request_id)

    assert [mail.to for mail in fake.sent] == ["b@kindbridge.org"]


def test_admin_notify_email_must_belong_to_an_admin_in_the_database(engine, clock):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn, "a@kindbridge.org")
        _, profile = _volunteer(conn)
    _open_request(engine, request_id, requester)
    service = _service(engine, fake, admin_notify_email="stranger@elsewhere.example")
    handler = _match_handler(engine, clock, Catalog([_hit(profile, 0.9)]), service)

    _propose_match(handler, request_id)

    assert [mail.to for mail in fake.sent] == ["a@kindbridge.org"]


# --- notifier method without a command yet -------------------------------------------------------


def test_task_released_emails_the_requester_and_the_admins(engine):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _admin(conn)
        volunteer_user, profile = _volunteer(conn)
        _name(conn, volunteer_user, "Yossi Cohen")
    _open_request(engine, request_id, requester)

    _service(engine, fake).task_released(request_id, profile, "sick")

    assert sorted(mail.to for mail in fake.sent) == sorted([ADMIN_MAIL, "requester@example.com"])
    to_admin = fake.to(ADMIN_MAIL)[0]
    assert "Yossi Cohen" in to_admin.body and "sick" in to_admin.body
    assert "sick" not in fake.to("requester@example.com")[0].body


def test_recipients_come_from_the_database_only(engine):
    fake = FakeNotifier()
    requester, request_id = uuid4(), uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
    _open_request(engine, request_id, requester)

    _service(engine, fake).assignment_made(request_id, uuid4())  # unknown volunteer profile
    _service(engine, fake).assignment_made(uuid4(), uuid4())  # unknown request

    assert [mail.to for mail in fake.sent] == ["requester@example.com"]


def test_default_handlers_do_nothing_without_a_notifier():
    NoNotifications().assignment_made(uuid4(), uuid4())
    NoNotifications().match_outcome(uuid4(), "MatchesProposed", {})


# --- message copy (pure) -------------------------------------------------------------------------

INFO = mails.RequestInfo("rid-1", "transport", "Haifa\nBcc: evil@example.com", "HIGH", date(2026, 10, 20))


def test_subject_is_one_line_even_if_the_city_contains_a_newline():
    mail = mails.assigned_to_volunteer(INFO, "Yossi\r\nBcc: x", "https://kb.example/me/tasks")

    assert "\n" not in mail.subject and "\r" not in mail.subject
    assert "\n" not in mail.body.splitlines()[0]


def test_copy_is_hebrew_by_default_and_english_on_request():
    he = mails.assigned_to_volunteer(INFO, "Yossi", "https://x")
    en = mails.assigned_to_volunteer(INFO, "Yossi", "https://x", lang="en")

    assert "שובצת" in he.subject and "Transport" in en.subject
    assert "2026-10-20" in he.body and "2026-10-20" in en.body


def test_no_match_copy_lists_reasons_most_common_first():
    mail = mails.no_match_to_admin(INFO, {"capacity": 1, "geography": 4, "mystery": 2}, "https://x")

    lines = [line for line in mail.body.splitlines() if line.startswith("- ")]
    assert lines == ["- עיר לא מתאימה: 4", "- mystery: 2", "- אין קיבולת: 1"]


def test_no_match_copy_handles_an_empty_summary():
    assert "- ללא" in mails.no_match_to_admin(INFO, {}, "https://x").body


# --- notifier: retry, timeout path, secrets --------------------------------------------------------


def _settings(tmp_path) -> GmailSettings:
    return GmailSettings(CLIENT_ID, CLIENT_SECRET, tmp_path / "gmail")


def _notifier(tmp_path, call, **kwargs):
    sleeps: list[float] = []
    notifier = GmailMcpNotifier(_settings(tmp_path), call=call, sleep=sleeps.append, **kwargs)
    return notifier, sleeps


def test_a_failing_send_is_retried_three_times_with_backoff_then_logged(tmp_path, caplog):
    attempts: list[str] = []

    def call(to, subject, body):
        attempts.append(to)
        raise RuntimeError(f"boom {CLIENT_SECRET} {CLIENT_ID}")

    notifier, sleeps = _notifier(tmp_path, call)

    with caplog.at_level(logging.ERROR):
        notifier.send("dana@example.com", "s", "b")  # must not raise

    assert len(attempts) == 3
    assert sleeps == [0.5, 1.0]
    assert "notify_failed" in caplog.text
    assert CLIENT_SECRET not in caplog.text and CLIENT_ID not in caplog.text
    assert "dana@example.com" not in caplog.text
    assert "d***@example.com" in caplog.text


def test_a_send_that_succeeds_on_the_second_attempt_is_sent_once(tmp_path):
    calls: list[int] = []

    def call(to, subject, body):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("slow")

    notifier, sleeps = _notifier(tmp_path, call)

    notifier.send("dana@example.com", "s", "b")

    assert len(calls) == 2 and sleeps == [0.5]


def test_a_permanent_failure_is_not_retried(tmp_path, caplog):
    calls: list[int] = []

    def call(to, subject, body):
        calls.append(1)
        raise PermanentNotifyError("not authorized")

    notifier, sleeps = _notifier(tmp_path, call)

    with caplog.at_level(logging.ERROR):
        notifier.send("dana@example.com", "s", "b")

    assert len(calls) == 1 and sleeps == []
    assert "notify_failed" in caplog.text


@pytest.mark.parametrize("address", ["", "no-at-sign", "a@b", "a@b.co,evil@x.co", "a b@c.co", "a@b.co\nBcc: x@y.z"])
def test_a_malformed_recipient_is_never_sent(tmp_path, address):
    calls: list[int] = []
    notifier, _ = _notifier(tmp_path, lambda *args: calls.append(1))

    notifier.send(address, "s", "b")

    assert calls == []


def test_deliver_raises_after_the_last_attempt_but_send_does_not(tmp_path):
    def call(to, subject, body):
        raise RuntimeError("nope")

    notifier, _ = _notifier(tmp_path, call)

    with pytest.raises(NotifyError):
        notifier.deliver("dana@example.com", "s", "b")


def test_without_a_stored_token_nothing_is_launched(tmp_path, caplog):
    launched: list[int] = []
    notifier = GmailMcpNotifier(_settings(tmp_path), launch=lambda: launched.append(1) or ("x", []))

    with caplog.at_level(logging.ERROR):
        notifier.send("dana@example.com", "s", "b")

    assert launched == []
    assert "scripts.gmail_auth" in caplog.text


def test_settings_repr_hides_the_secret(tmp_path):
    assert CLIENT_SECRET not in repr(_settings(tmp_path))
    assert CLIENT_ID not in repr(_settings(tmp_path))


def test_mask_address():
    assert mask_address("dana@example.com") == "d***@example.com"
    assert mask_address("nonsense") == "***"


def test_run_sync_works_inside_a_running_event_loop():
    seen: list[str] = []

    async def work():
        seen.append("ran")

    async def caller():
        _run_sync(work)

    asyncio.run(caller())
    _run_sync(work)

    assert seen == ["ran", "ran"]


# --- the real MCP client path against a local fake server -----------------------------------------


def _fake_server_notifier(tmp_path, **kwargs):
    gmail = _settings(tmp_path)
    gmail.token_dir.mkdir(parents=True)
    gmail.credentials_path.write_text("{}", encoding="utf-8")
    server = Path(__file__).with_name("fake_gmail_mcp_server.py")
    notifier = GmailMcpNotifier(
        gmail, launch=lambda: (sys.executable, [str(server)]), sleep=lambda _s: None, timeout=30, **kwargs
    )
    return notifier, gmail.token_dir / "mailbox.jsonl"


def test_the_mcp_client_calls_send_email_with_hebrew_text(tmp_path):
    notifier, mailbox = _fake_server_notifier(tmp_path)

    notifier.deliver("dana@example.com", "שובצת לבקשה", "שלום דנה")

    sent = [json.loads(line) for line in mailbox.read_text(encoding="utf-8").splitlines()]
    assert sent == [{"to": ["dana@example.com"], "subject": "שובצת לבקשה", "body": "שלום דנה"}]
    keys = json.loads((tmp_path / "gmail" / "gcp-oauth.keys.json").read_text(encoding="utf-8"))
    assert keys["installed"]["client_id"] == CLIENT_ID


def test_a_server_error_text_is_a_failure_and_is_retried(tmp_path):
    notifier, mailbox = _fake_server_notifier(tmp_path)

    with pytest.raises(NotifyError, match="backend exploded"):
        notifier.deliver("dana@example.com", "FAIL", "b")

    assert len(mailbox.read_text(encoding="utf-8").splitlines()) == 3


def test_an_expired_token_is_a_permanent_failure(tmp_path):
    notifier, mailbox = _fake_server_notifier(tmp_path)

    with pytest.raises(PermanentNotifyError, match="gmail_auth"):
        notifier.deliver("dana@example.com", "AUTH", "b")

    assert len(mailbox.read_text(encoding="utf-8").splitlines()) == 1


# --- configuration ----------------------------------------------------------------------------------


def _config(**overrides) -> Config:
    values = dict(
        database_url="sqlite://",
        jwt_secret="x" * 40,
        encryption_key="k",
        mail_enabled=True,
        gmail_client_id=CLIENT_ID,
        gmail_client_secret=CLIENT_SECRET,
    )
    values.update(overrides)
    return Config(**values)


def test_build_notifier_is_null_unless_mail_is_enabled_and_configured():
    assert isinstance(build_notifier(_config(testing=True)), NullNotifier)
    assert isinstance(build_notifier(_config(mail_enabled=False)), NullNotifier)
    assert isinstance(build_notifier(_config(gmail_client_secret="")), NullNotifier)
    assert isinstance(build_notifier(_config()), GmailMcpNotifier)


def test_config_repr_never_contains_the_gmail_secret():
    assert CLIENT_SECRET not in repr(_config()) and CLIENT_ID not in repr(_config())


def test_load_settings_reads_the_mail_settings():
    settings = load_settings(
        {
            "MAIL_ENABLED": "True",
            "ADMIN_NOTIFY_EMAIL": "boss@kindbridge.org",
            "GMAIL_MCP_CLIENT_ID": "id",
            "GMAIL_MCP_CLIENT_SECRET": "secret",
        }
    )

    assert settings["MAIL_ENABLED"] is True
    assert settings["ADMIN_NOTIFY_EMAIL"] == "boss@kindbridge.org"
    assert settings["GMAIL_TOKEN_DIR"] == ".secrets/gmail"
    assert load_settings({})["MAIL_ENABLED"] is False
    assert load_settings({"MAIL_ENABLED": "no"})["MAIL_ENABLED"] is False


def test_the_default_app_never_builds_a_real_notifier(config, engine, clock):
    app = create_app(config=config, engine=engine, clock=clock)

    assert isinstance(app.extensions["kindbridge"].notifier, NullNotifier)
