"""Admin volunteer offers: enabled profiles, and whether each is ASSIGNED."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import insert

from app.commands.dtos import BootstrapAdminCommand
from app.repositories.tables import help_requests, task_assignments, users, volunteer_profiles
from tests.conftest import REQUESTER
from tests.test_auth import bus, login, register


def _login_admin(app, client):
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))
    login(client, "root@kindbridge.org", "admin-password-123")


def _person(conn, email, full_name, *, phone="050-SECRET-99", address="SECRET-HOME", active=True):
    user_id = uuid4()
    conn.execute(
        insert(users).values(
            id=user_id,
            email=email,
            password_hash="hash",
            full_name=full_name,
            phone=phone,
            city="חיפה",
            home_address=address,
            is_admin=False,
            is_active=active,
            created_at=datetime(2026, 10, 1, 8, 0),
        )
    )
    return user_id


def _volunteer(
    conn,
    user_id,
    *,
    city="תל אביב",
    skills='["נהיגה"]',
    experience="ניסיון נהיגה",
    has_vehicle=True,
    availability="AVAILABLE",
    active=0,
    max_active=1,
    enabled=True,
):
    profile_id = uuid4()
    conn.execute(
        insert(volunteer_profiles).values(
            id=profile_id,
            user_id=user_id,
            is_enabled=enabled,
            primary_city=city,
            has_vehicle=has_vehicle,
            skills_json=skills,
            experience=experience,
            base_frequency="ON_DEMAND",
            availability_status=availability,
            max_active_tasks=max_active,
            max_parallel_tasks=2,
            current_active_tasks=active,
            current_parallel_tasks=0,
        )
    )
    return profile_id


def _request(conn, requester_id, *, category, city, address="SECRET-REQUEST"):
    request_id = uuid4()
    conn.execute(
        insert(help_requests).values(
            id=request_id,
            requester_id=requester_id,
            series_id=None,
            city=city,
            address=address,
            category=category,
            resource_type="PHYSICAL_PRESENCE",
            description="תיאור פרטי",
            urgency="NORMAL",
            preferred_date=None,
            preferred_time_from=None,
            preferred_time_to=None,
            estimated_duration_min=None,
            required_skills_json="[]",
            requires_vehicle=False,
            concurrency_type="EXCLUSIVE",
            status="ASSIGNED",
            match_attempt=1,
            created_at=datetime(2026, 10, 2, 9, 0),
        )
    )
    return request_id


def _assignment(conn, request_id, volunteer_id, *, status):
    conn.execute(
        insert(task_assignments).values(
            id=uuid4(),
            request_id=request_id,
            volunteer_id=volunteer_id,
            ai_score=None,
            ai_rationale=None,
            rank_in_batch=None,
            match_attempt=1,
            approved_by=None,
            status=status,
            decline_reason=None,
            override_reason=None,
            updated_at=datetime(2026, 10, 2, 10, 0),
        )
    )


def _row(body: str, name: str) -> str:
    return body.split(f'data-volunteer="{name}"', 1)[1].split("</tr>", 1)[0]


def test_volunteer_offers_require_login(client):
    response = client.get("/admin/volunteers")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_volunteer_offers_reject_non_admin(client):
    register(client, REQUESTER)

    response = client.get("/admin/volunteers")

    assert response.status_code == 403
    assert "אין הרשאה".encode() in response.data


def test_admin_sees_assigned_and_unassigned_offers(app, client, engine):
    _login_admin(app, client)
    with engine.begin() as conn:
        requester_id = _person(conn, "dana@example.com", "דנה לוי", phone="050-OTHER", address="OTHER-HOME")
        noa_id = _person(conn, "noa@example.com", "נועה כהן")
        yossi_id = _person(conn, "yossi@example.com", "יוסי לוי", phone="050-YOSSI", address="YOSSI-HOME")
        proposed_id = _person(conn, "proposed@example.com", "רותם הצעה")
        finished_id = _person(conn, "finished@example.com", "גיל סיים")
        disabled_id = _person(conn, "disabled@example.com", "טל מושבת")
        inactive_id = _person(conn, "inactive@example.com", "עמית כבוי", active=False)
        noa = _volunteer(conn, noa_id, active=1, max_active=2)
        _volunteer(conn, yossi_id, has_vehicle=False, active=0, max_active=1)
        proposed = _volunteer(conn, proposed_id)
        finished = _volunteer(conn, finished_id)
        disabled = _volunteer(conn, disabled_id, enabled=False)
        _volunteer(conn, inactive_id)
        escort_id = _request(conn, requester_id, category="ליווי", city="חיפה")
        shop_id = _request(conn, requester_id, category="קניות", city="עכו")
        proposed_request = _request(conn, requester_id, category="שליחויות", city="נתניה")
        finished_request = _request(conn, requester_id, category="הוראה", city="באר שבע")
        hidden_request = _request(conn, requester_id, category="תרגום", city="אילת")
        _assignment(conn, escort_id, noa, status="ASSIGNED")
        _assignment(conn, shop_id, noa, status="ASSIGNED")
        _assignment(conn, proposed_request, proposed, status="PROPOSED")
        _assignment(conn, finished_request, finished, status="COMPLETED")
        _assignment(conn, hidden_request, disabled, status="ASSIGNED")

    page = client.get("/admin/volunteers")
    body = page.data.decode()

    assert page.status_code == 200
    assert "הצעות התנדבות" in body
    assert 'href="/admin/volunteers"' in body
    noa_row = _row(body, "נועה כהן")
    yossi_row = _row(body, "יוסי לוי")
    proposed_row = _row(body, "רותם הצעה")
    finished_row = _row(body, "גיל סיים")
    assert 'data-assignment="assigned"' in noa_row
    assert "משויך" in noa_row
    assert "לא משויך" not in noa_row
    assert str(escort_id) in noa_row
    assert str(shop_id) in noa_row
    assert f"/requests/{escort_id}" in noa_row
    assert f"/requests/{shop_id}" in noa_row
    assert "ליווי" in noa_row
    assert "חיפה" in noa_row
    assert "קניות" in noa_row
    assert "עכו" in noa_row
    assert "נהיגה" in noa_row
    assert "ניסיון נהיגה" in noa_row
    assert "1 / 2" in noa_row
    assert 'data-assignment="unassigned"' in yossi_row
    assert "לא משויך" in yossi_row
    assert "משויך" not in yossi_row.replace("לא משויך", "")
    assert 'data-assignment="unassigned"' in proposed_row
    assert str(proposed_request) not in proposed_row
    assert 'data-assignment="unassigned"' in finished_row
    assert str(finished_request) not in finished_row
    assert "טל מושבת" not in body
    assert "עמית כבוי" not in body
    assert "050-SECRET-99" not in body
    assert "SECRET-HOME" not in body
    assert "SECRET-REQUEST" not in body
    assert "תרגום" not in body

    assigned_only = client.get("/admin/volunteers", query_string={"assignment": "assigned"}).data.decode()
    assert "נועה כהן" in assigned_only
    assert "יוסי לוי" not in assigned_only
    assert "רותם הצעה" not in assigned_only
    assert "גיל סיים" not in assigned_only

    unassigned_only = client.get("/admin/volunteers", query_string={"assignment": "unassigned"}).data.decode()
    assert "נועה כהן" not in unassigned_only
    assert "יוסי לוי" in unassigned_only
    assert "רותם הצעה" in unassigned_only
    assert "גיל סיים" in unassigned_only
