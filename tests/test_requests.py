"""Help-request list, create, and cancel. There is no edit."""

import json
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from app.commands.dtos import BootstrapAdminCommand
from app.repositories.tables import event_store, help_requests, task_assignments, volunteer_profiles
from tests.conftest import REQUESTER, REQUESTER_PROFILE, csrf_post
from tests.test_account import save_requester
from tests.test_auth import bus, login, register


def request_body(**overrides):
    body = {
        "category": "errands",
        "urgency": "NORMAL",
        "description": "Grocery run for the week",
        "required_skills": ["נהיגה"],
        "resource_type": "PHYSICAL_PRESENCE",
        "requires_vehicle": True,
        "preferred_date": "2026-10-20",
        "preferred_time_from": "09:00",
        "preferred_time_to": "11:00",
        "estimated_duration_min": 90,
        "city": "Haifa",
        "address": "12 Herzl Street",
    }
    body.update(overrides)
    return body


def enroll(client, person=None):
    register(client, person or REQUESTER)
    save_requester(client)


def submit(client, **overrides):
    return csrf_post(client, "/api/requests", request_body(**overrides))


def test_request_endpoints_require_login(client):
    assert client.get("/api/me/requests").status_code == 401
    assert csrf_post(client, "/api/requests", request_body()).status_code == 401
    assert csrf_post(client, f"/api/requests/{uuid4()}/cancel", {}).status_code == 401
    assert client.get("/me/requests/new").headers["Location"].endswith("/login")


def test_submit_without_requester_profile_is_forbidden(client):
    register(client, REQUESTER)

    response = submit(client)

    assert response.status_code == 403


def test_admin_cannot_submit(app):
    client = app.test_client()
    client.get("/api/auth/csrf")
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))
    login(client, "root@kindbridge.org", "admin-password-123")

    assert submit(client).status_code == 403


def test_new_request_page_is_prefilled_from_the_profile(client):
    enroll(client)

    page = client.get("/me/requests")
    form = client.get("/me/requests/new")

    assert "אי אפשר לערוך".encode() in page.data
    assert "בקשה חדשה".encode() in page.data
    assert "עריכה".encode() not in page.data
    assert form.status_code == 200
    assert b'value="Haifa"' in form.data
    assert "12 Herzl Street".encode() in form.data
    assert "שליחויות".encode() in form.data
    assert "במקרה חירום רפואי".encode() in form.data


def test_submit_creates_a_pending_request_and_lists_it(client, engine):
    enroll(client)

    created = submit(client, description="קניות לשבוע", preferred_date="2020-01-01")
    other = submit(client, category="transport", city="Tel Aviv", description="Ride to the clinic", preferred_date="2099-01-01", required_skills=[])

    assert created.status_code == 201
    request_id = created.get_json()["id"]
    with engine.connect() as conn:
        row = conn.execute(select(help_requests).where(help_requests.c.id == UUID(request_id))).one()
        payload = json.loads(
            conn.execute(
                select(event_store.c.payload_json).where(
                    event_store.c.aggregate_id == UUID(request_id),
                    event_store.c.event_type == "HelpRequestCreated",
                )
            ).scalar_one()
        )
    assert row.status == "PENDING_REVIEW"
    assert row.category == "errands"
    assert bool(row.requires_vehicle) is True
    assert json.loads(row.required_skills_json) == ["driving"]
    assert payload["required_skills"] == ["driving"]
    assert payload["address"] == "12 Herzl Street"
    assert payload["description"] == "קניות לשבוע"
    assert "password" not in payload

    hebrew_city = client.get("/api/me/requests?city=" + "חיפה")
    errands = client.get("/api/me/requests?category=errands")
    transport = client.get("/api/me/requests?category=transport&urgency=HIGH")

    assert hebrew_city.status_code == 200
    body = hebrew_city.get_json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == request_id
    assert body["items"][0]["overdue"] is True
    assert body["items"][0]["can_cancel"] is True
    assert errands.get_json()["total"] == 1
    assert transport.get_json()["total"] == 0
    assert other.status_code == 201


def test_invalid_window_does_not_create_a_request(client, engine):
    enroll(client)

    response = submit(client, preferred_time_from="15:00", preferred_time_to="09:00")

    assert response.status_code == 400
    assert "preferred_time_to" in response.get_json()["fields"]
    with engine.connect() as conn:
        assert conn.execute(select(help_requests)).first() is None


def test_cancel_leaves_the_open_list_and_another_requester_cannot_cancel(client, app, engine):
    enroll(client)
    created = submit(client)
    request_id = created.get_json()["id"]

    other = app.test_client()
    other.get("/api/auth/csrf")
    register(other, dict(REQUESTER, email="other@example.com", full_name="Noa Levi"))
    save_requester(other, dict(REQUESTER_PROFILE, default_city="Tel Aviv"))
    assert csrf_post(other, f"/api/requests/{request_id}/cancel", {}).status_code == 403

    cancelled = csrf_post(client, f"/api/requests/{request_id}/cancel", {})

    assert cancelled.status_code == 200
    assert client.get("/api/me/requests").get_json()["total"] == 0
    listed = client.get("/api/me/requests?status=CANCELLED").get_json()
    assert listed["total"] == 1
    assert listed["items"][0]["can_cancel"] is False
    assert csrf_post(client, f"/api/requests/{request_id}/cancel", {}).status_code == 400
    with engine.connect() as conn:
        status = conn.execute(select(help_requests.c.status)).scalar_one()
        event_type = conn.execute(
            select(event_store.c.event_type).where(event_store.c.event_type == "HelpRequestCancelled")
        ).scalar_one()
    assert status == "CANCELLED"
    assert event_type == "HelpRequestCancelled"


def test_cancel_frees_an_assigned_volunteer(client, engine):
    enroll(client)
    request_id = UUID(submit(client).get_json()["id"])
    volunteer_id = uuid4()
    stamp = datetime(2026, 10, 6, 12, 0, 0)
    with engine.begin() as conn:
        conn.execute(
            insert(volunteer_profiles).values(
                id=volunteer_id,
                user_id=uuid4(),
                is_enabled=True,
                primary_city="Haifa",
                has_vehicle=True,
                skills_json="[]",
                experience="Weekly grocery runs",
                base_frequency="WEEKLY",
                availability_status="AVAILABLE",
                max_active_tasks=1,
                max_parallel_tasks=2,
                current_active_tasks=1,
                current_parallel_tasks=0,
            )
        )
        conn.execute(
            insert(task_assignments).values(
                id=uuid4(),
                request_id=request_id,
                volunteer_id=volunteer_id,
                ai_score=80,
                ai_rationale="nearby",
                rank_in_batch=1,
                match_attempt=0,
                approved_by=None,
                status="PROPOSED",
                decline_reason=None,
                override_reason=None,
                updated_at=stamp,
            )
        )
        conn.execute(
            insert(task_assignments).values(
                id=uuid4(),
                request_id=request_id,
                volunteer_id=volunteer_id,
                ai_score=None,
                ai_rationale=None,
                rank_in_batch=None,
                match_attempt=0,
                approved_by=None,
                status="ASSIGNED",
                decline_reason=None,
                override_reason=None,
                updated_at=stamp,
            )
        )

    assert csrf_post(client, f"/api/requests/{request_id}/cancel", {}).status_code == 200
    with engine.connect() as conn:
        statuses = set(conn.execute(select(task_assignments.c.status)).scalars())
        active = conn.execute(select(volunteer_profiles.c.current_active_tasks)).scalar_one()
    assert statuses == {"SUPERSEDED", "DECLINED"}
    assert active == 0
