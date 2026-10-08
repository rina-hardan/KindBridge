"""Admin dashboard: open-request counters, the queue, and volunteer capacity."""

from datetime import date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import insert

from app.commands.dtos import BootstrapAdminCommand
from app.repositories.tables import (
    help_requests,
    task_assignments,
    users,
    volunteer_profiles,
    volunteer_unavailability,
)
from tests.conftest import REQUESTER, csrf_post
from tests.test_auth import bus, login, register


def _login_admin(app, client):
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))
    login(client, "root@kindbridge.org", "admin-password-123")


def _person(conn, email, full_name):
    user_id = uuid4()
    conn.execute(
        insert(users).values(
            id=user_id,
            email=email,
            password_hash="hash",
            full_name=full_name,
            phone="cipher",
            city="חיפה",
            home_address="רחוב הרצל 1",
            is_admin=False,
            is_active=True,
            created_at=datetime(2026, 10, 1, 8, 0),
        )
    )
    return user_id


def _volunteer(conn, user_id, *, city="חיפה", availability="AVAILABLE", active=0, max_active=1):
    profile_id = uuid4()
    conn.execute(
        insert(volunteer_profiles).values(
            id=profile_id,
            user_id=user_id,
            is_enabled=True,
            primary_city=city,
            has_vehicle=True,
            skills_json='["נהיגה"]',
            experience="מתנדב",
            base_frequency="ON_DEMAND",
            availability_status=availability,
            max_active_tasks=max_active,
            max_parallel_tasks=2,
            current_active_tasks=active,
            current_parallel_tasks=0,
        )
    )
    return profile_id


def _request(conn, requester_id, *, description, status, urgency="NORMAL", preferred_date=None, created_at=None, category="ליווי"):
    request_id = uuid4()
    conn.execute(
        insert(help_requests).values(
            id=request_id,
            requester_id=requester_id,
            series_id=None,
            city="חיפה",
            address="SECRET-ADDRESS",
            category=category,
            resource_type="PHYSICAL_PRESENCE",
            description=description,
            urgency=urgency,
            preferred_date=preferred_date,
            preferred_time_from=None,
            preferred_time_to=None,
            estimated_duration_min=None,
            required_skills_json="[]",
            requires_vehicle=False,
            concurrency_type="EXCLUSIVE",
            status=status,
            match_attempt=1,
            created_at=created_at or datetime(2026, 10, 2, 9, 0),
        )
    )
    return request_id


def _assignment(conn, request_id, volunteer_id, *, status, score, rationale, rank):
    conn.execute(
        insert(task_assignments).values(
            id=uuid4(),
            request_id=request_id,
            volunteer_id=volunteer_id,
            ai_score=score,
            ai_rationale=rationale,
            rank_in_batch=rank,
            match_attempt=0,
            approved_by=None,
            status=status,
            decline_reason=None,
            override_reason=None,
            updated_at=datetime(2026, 10, 2, 10, 0),
        )
    )


def test_dashboard_requires_login(client):
    response = client.get("/dashboard")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_dashboard_rejects_non_admin(client):
    register(client, REQUESTER)

    response = client.get("/dashboard")

    assert response.status_code == 403
    assert "אין הרשאה".encode() in response.data


def test_admin_queue_counts_candidates_and_capacity(app, client, engine):
    _login_admin(app, client)
    today = date.today()
    with engine.begin() as conn:
        requester_id = _person(conn, "dana@example.com", "דנה לוי")
        later_id = _person(conn, "amir@example.com", "אמיר חדד")
        noa_id = _person(conn, "noa@example.com", "נועה כהן")
        yossi_id = _person(conn, "yossi@example.com", "יוסי לוי")
        inactive_id = _person(conn, "inactive@example.com", "רותם שקט")
        away_id = _person(conn, "away@example.com", "טל נעדר")
        noa = _volunteer(conn, noa_id)
        yossi = _volunteer(conn, yossi_id, city="תל אביב", active=1, max_active=1)
        _volunteer(conn, inactive_id, availability="INACTIVE")
        away = _volunteer(conn, away_id)
        conn.execute(
            insert(volunteer_unavailability).values(
                id=uuid4(),
                volunteer_id=away,
                from_date=today - timedelta(days=1),
                until_date=today + timedelta(days=1),
                reason=None,
                is_cancelled=False,
                created_at=datetime(2026, 10, 1, 8, 0),
            )
        )
        open_id = _request(
            conn,
            requester_id,
            description="ליווי לקופת חולים",
            status="MATCH_PROPOSED",
            urgency="NORMAL",
            preferred_date=date(2020, 1, 1),
            created_at=datetime(2026, 10, 3, 9, 0),
        )
        _request(
            conn,
            later_id,
            description="פינוי דחוף",
            status="PENDING_REVIEW",
            urgency="EMERGENCY",
            category="פינוי",
            created_at=datetime(2026, 10, 4, 9, 0),
        )
        _request(conn, requester_id, description="בקשה שנסגרה", status="COMPLETED")
        _assignment(conn, open_id, noa, status="PROPOSED", score=91, rationale="התאמה גבוהה לנהיגה", rank=1)
        _assignment(conn, open_id, yossi, status="SUPERSEDED", score=10, rationale="הצעה ישנה", rank=2)

    page = client.get("/dashboard")
    body = page.data.decode()

    assert page.status_code == 200
    assert "לוח בקרה" in body
    assert "דנה לוי" in body
    assert "אמיר חדד" in body
    assert "ליווי" in body
    assert "חיפה" in body
    assert "הוצעו התאמות" in body
    assert "באיחור" in body
    assert 'data-status="MATCH_PROPOSED">1' in body
    assert 'data-status="PENDING_REVIEW">1' in body
    assert 'data-status="NO_MATCH">0' in body
    assert 'data-status="ASSIGNED">0' in body
    assert 'data-candidates="1"' in body
    assert body.index("אמיר חדד") < body.index("דנה לוי")
    assert "נועה כהן" not in body
    assert "יוסי לוי" not in body
    assert "התאמה גבוהה לנהיגה" not in body
    assert "הצעה ישנה" not in body
    assert "בקשה שנסגרה" not in body
    assert "SECRET-ADDRESS" not in body
    assert 'data-gauge="available">1' in body
    assert 'data-gauge="busy">1' in body
    assert 'data-gauge="inactive">1' in body
    assert "/requests/" in body


def test_empty_dashboard_has_no_create_action(app, client):
    _login_admin(app, client)

    page = client.get("/dashboard")
    body = page.data.decode()

    assert page.status_code == 200
    assert "אין בקשות עזרה פתוחות." in body
    assert 'data-gauge="available">0' in body
    assert 'type="submit"' not in body
