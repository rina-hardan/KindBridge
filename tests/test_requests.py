"""Request list, detail, assignment actions, and the requester create/cancel flow."""

import json
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from app.commands.dtos import BootstrapAdminCommand
from app.domain.events import DomainEvent
from app.projections.match_projector import MatchProjector
from app.repositories.tables import event_store, exemption_links, help_requests, task_assignments, volunteer_profiles
from tests.conftest import REQUESTER, REQUESTER_PROFILE, VOLUNTEER, VOLUNTEER_PROFILE, csrf_post
from tests.test_account import save_requester
from tests.test_auth import bus, login, register


def _login_admin(app, client):
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))
    login(client, "root@kindbridge.org", "admin-password-123")


def _enable(client, payload):
    return csrf_post(client, "/api/me/volunteer", payload)


def _append(app, request_id, expected_version, event_type, payload, version):
    event = DomainEvent(
        aggregate_id=request_id,
        aggregate_type="HelpRequest",
        event_type=event_type,
        payload=payload,
        version=version,
        created_at=datetime(2026, 10, 2, 9, version),
    )
    app.extensions["kindbridge"].event_store.append(
        request_id, expected_version, [event], projector=MatchProjector()
    )


def _created_payload(requester_id, **overrides):
    payload = {
        "requester_id": str(requester_id),
        "city": "Haifa",
        "address": "12 Harbor Road",
        "category": "transport",
        "resource_type": "PHYSICAL_PRESENCE",
        "description": "Need a ride to the clinic",
        "urgency": "HIGH",
        "preferred_date": "2020-01-01",
        "required_skills": ["driving"],
        "requires_vehicle": False,
        "concurrency_type": "EXCLUSIVE",
        "match_attempt": 0,
    }
    payload.update(overrides)
    return payload


def _propose(app, request_id, volunteer_id, assignment_id, *, score=91, rationale="Nearby and can drive"):
    _append(
        app,
        request_id,
        1,
        "MatchesProposed",
        {
            "proposals": [
                {
                    "assignment_id": str(assignment_id),
                    "volunteer_id": str(volunteer_id),
                    "score": score,
                    "rationale": rationale,
                    "rank": 1,
                }
            ],
            "match_attempt": 0,
            "k": 1,
        },
        2,
    )


def _status(engine, request_id):
    with engine.connect() as conn:
        return conn.execute(select(help_requests.c.status).where(help_requests.c.id == request_id)).scalar_one()


def test_request_list_requires_login(client):
    response = client.get("/requests")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_request_detail_requires_login(client):
    response = client.get(f"/requests/{uuid4()}")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_admin_searches_every_request_and_a_requester_sees_only_their_own(app, client):
    first = register(client, REQUESTER)
    requester_id = first.get_json()["user_id"]
    second_payload = dict(REQUESTER, email="other@example.com", full_name="Other Person")
    second = register(client, second_payload)
    other_id = second.get_json()["user_id"]
    own_id = uuid4()
    other_request = uuid4()
    _append(app, own_id, 0, "HelpRequestCreated", _created_payload(requester_id, description="Dana clinic ride"), 1)
    _append(
        app,
        other_request,
        0,
        "HelpRequestCreated",
        _created_payload(other_id, description="Someone else needs help", city="Eilat", category="errand"),
        1,
    )
    _login_admin(app, client)

    admin_page = client.get("/requests")
    admin_body = admin_page.data.decode()
    filtered = client.get("/requests?city=Eilat&category=errand")

    assert admin_page.status_code == 200
    assert "Dana Levi" in admin_body
    assert "Other Person" in admin_body
    assert "ציון" not in admin_body
    assert f"/requests/{own_id}" in admin_body
    filtered_body = filtered.data.decode()
    assert "Other Person" in filtered_body
    assert "errand" in filtered_body
    assert "Dana Levi" not in filtered_body

    login(client, REQUESTER["email"], REQUESTER["password"])
    own_page = client.get("/requests").data.decode()
    forbidden = client.get(f"/requests/{other_request}")

    assert "Dana Levi" in own_page
    assert "Other Person" not in own_page
    assert forbidden.status_code == 403


def test_admin_detail_shows_score_rationale_and_contact(app, client):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    volunteer = register(client, VOLUNTEER)
    volunteer_id = volunteer.get_json()["user_id"]
    enabled = _enable(client, VOLUNTEER_PROFILE)
    profile_id = enabled.get_json()["profile_id"]
    extra = register(client, dict(VOLUNTEER, email="extra@example.com", full_name="Extra Volunteer"))
    login(client, "extra@example.com", VOLUNTEER["password"])
    _enable(client, VOLUNTEER_PROFILE)
    request_id = uuid4()
    assignment_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _propose(app, request_id, profile_id, assignment_id, rationale="Nearby and can drive")
    _login_admin(app, client)

    page = client.get(f"/requests/{request_id}")
    body = page.data.decode()

    assert page.status_code == 200
    assert "Dana Levi" in body
    assert "Haifa" in body
    assert "transport" in body
    assert "Need a ride to the clinic" in body
    assert "12 Harbor Road" in body
    assert REQUESTER["phone"] in body
    assert "באיחור" in body
    assert "Yossi Cohen" in body
    assert "ציון: 91" in body
    assert "Nearby and can drive" in body
    assert "Extra Volunteer" in body
    assert "similarity" not in body.lower()
    assert f'value="{profile_id}"' in body


def test_no_match_shows_summary_without_candidate_cards(app, client):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    request_id = uuid4()
    _append(
        app,
        request_id,
        0,
        "HelpRequestCreated",
        _created_payload(requester_id, city="Eilat", description="No one nearby"),
        1,
    )
    _append(
        app,
        request_id,
        1,
        "NoMatchFound",
        {"match_attempt": 0, "reason": "no_eligible_volunteers", "rejection_summary": {"geography": 2}},
        2,
    )
    _login_admin(app, client)

    page = client.get(f"/requests/{request_id}")
    body = page.data.decode()

    assert page.status_code == 200
    assert "אין מתנדבים זכאים" in body
    assert "עיר אחרת" in body
    assert ">2<" in body
    assert "הפעלה מחדש" in body
    assert "שיבוץ ידני" in body
    assert 'name="assignment"' not in body


def test_approve_requires_login(client):
    response = csrf_post(client, f"/api/requests/{uuid4()}/approve", {"assignment_id": str(uuid4())})

    assert response.status_code == 401


def test_approve_forbidden_for_requester(client):
    register(client, REQUESTER)

    response = csrf_post(client, f"/api/requests/{uuid4()}/approve", {"assignment_id": str(uuid4())})

    assert response.status_code == 403


def test_admin_approves_one_proposal(app, client, engine):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    register(client, VOLUNTEER)
    enabled = _enable(client, VOLUNTEER_PROFILE)
    profile_id = UUID(enabled.get_json()["profile_id"])
    request_id = uuid4()
    assignment_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _propose(app, request_id, profile_id, assignment_id)
    _login_admin(app, client)

    response = csrf_post(
        client, f"/api/requests/{request_id}/approve", {"assignment_id": str(assignment_id)}
    )

    assert response.status_code == 200
    assert _status(engine, request_id) == "ASSIGNED"
    with engine.connect() as conn:
        assignment = conn.execute(select(task_assignments).where(task_assignments.c.id == assignment_id)).one()
        active = conn.execute(
            select(volunteer_profiles.c.current_active_tasks).where(volunteer_profiles.c.id == profile_id)
        ).scalar_one()
    assert assignment.status == "ASSIGNED"
    assert active == 1


def test_reject_requires_login(client):
    response = csrf_post(client, f"/api/requests/{uuid4()}/reject", {"reason": "not a fit"})

    assert response.status_code == 401


def test_reject_forbidden_for_requester(client):
    register(client, REQUESTER)

    response = csrf_post(client, f"/api/requests/{uuid4()}/reject", {"reason": "not a fit"})

    assert response.status_code == 403


def test_admin_rejects_all_proposals(app, client, engine):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    register(client, VOLUNTEER)
    enabled = _enable(client, VOLUNTEER_PROFILE)
    profile_id = enabled.get_json()["profile_id"]
    request_id = uuid4()
    assignment_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _propose(app, request_id, profile_id, assignment_id)
    _login_admin(app, client)

    response = csrf_post(client, f"/api/requests/{request_id}/reject", {"reason": "none of these can make it"})

    assert response.status_code == 200
    assert _status(engine, request_id) == "PENDING_REVIEW"
    with engine.connect() as conn:
        status = conn.execute(
            select(task_assignments.c.status).where(task_assignments.c.id == assignment_id)
        ).scalar_one()
    assert status == "DECLINED"


def test_override_requires_login(client):
    response = csrf_post(
        client,
        f"/api/requests/{uuid4()}/override",
        {"volunteer_id": str(uuid4()), "reason": "dispatcher knows them"},
    )

    assert response.status_code == 401


def test_override_forbidden_for_requester(client):
    register(client, REQUESTER)

    response = csrf_post(
        client,
        f"/api/requests/{uuid4()}/override",
        {"volunteer_id": str(uuid4()), "reason": "dispatcher knows them"},
    )

    assert response.status_code == 403


def test_admin_override_assigns_without_a_score(app, client, engine):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    register(client, VOLUNTEER)
    enabled = _enable(client, VOLUNTEER_PROFILE)
    profile_id = UUID(enabled.get_json()["profile_id"])
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
    assert _status(engine, request_id) == "ASSIGNED"
    with engine.connect() as conn:
        row = conn.execute(
            select(task_assignments).where(task_assignments.c.request_id == request_id)
        ).one()
    assert row.status == "ASSIGNED"
    assert row.ai_score is None
    assert row.ai_rationale is None
    page = client.get(f"/requests/{request_id}").data.decode()
    assert "—" in page
    assert "Yossi Cohen" in page


def test_retrigger_requires_login(client):
    response = csrf_post(client, f"/api/requests/{uuid4()}/retrigger", {})

    assert response.status_code == 401


def test_retrigger_forbidden_for_requester(client):
    register(client, REQUESTER)

    response = csrf_post(client, f"/api/requests/{uuid4()}/retrigger", {})

    assert response.status_code == 403


def test_admin_retrigger_returns_request_to_review(app, client, engine):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    register(client, VOLUNTEER)
    enabled = _enable(client, VOLUNTEER_PROFILE)
    profile_id = enabled.get_json()["profile_id"]
    request_id = uuid4()
    assignment_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _propose(app, request_id, profile_id, assignment_id)
    _login_admin(app, client)

    response = csrf_post(client, f"/api/requests/{request_id}/retrigger", {})

    assert response.status_code == 200
    assert _status(engine, request_id) == "PENDING_REVIEW"
    with engine.connect() as conn:
        status = conn.execute(
            select(task_assignments.c.status).where(task_assignments.c.id == assignment_id)
        ).scalar_one()
    assert status == "SUPERSEDED"


def test_cancel_requires_login(client):
    response = csrf_post(client, f"/api/requests/{uuid4()}/cancel", {})

    assert response.status_code == 401


def test_cancel_forbidden_for_someone_else(app, client):
    owner = register(client, REQUESTER)
    request_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(owner.get_json()["user_id"]), 1)
    register(client, dict(REQUESTER, email="other@example.com", full_name="Other Person"))

    response = csrf_post(client, f"/api/requests/{request_id}/cancel", {})

    assert response.status_code == 403


def test_admin_cancel_assigned_request_frees_capacity(app, client, engine):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    register(client, VOLUNTEER)
    enabled = _enable(client, VOLUNTEER_PROFILE)
    profile_id = UUID(enabled.get_json()["profile_id"])
    request_id = uuid4()
    assignment_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _propose(app, request_id, profile_id, assignment_id)
    _login_admin(app, client)
    csrf_post(client, f"/api/requests/{request_id}/approve", {"assignment_id": str(assignment_id)})
    assigned_page = client.get(f"/requests/{request_id}").data.decode()

    response = csrf_post(client, f"/api/requests/{request_id}/cancel", {})

    assert "המתנדב יקבל הודעה" in assigned_page
    assert "ביטול בקשה" in assigned_page
    assert response.status_code == 200
    assert _status(engine, request_id) == "CANCELLED"
    with engine.connect() as conn:
        assignment = conn.execute(select(task_assignments.c.status).where(task_assignments.c.id == assignment_id)).scalar_one()
        active = conn.execute(
            select(volunteer_profiles.c.current_active_tasks).where(volunteer_profiles.c.id == profile_id)
        ).scalar_one()
    assert assignment == "DECLINED"
    assert active == 0


def test_owner_can_cancel_own_request(app, client, engine):
    owner = register(client, REQUESTER)
    request_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(owner.get_json()["user_id"]), 1)

    response = csrf_post(client, f"/api/requests/{request_id}/cancel", {})

    assert response.status_code == 200
    assert _status(engine, request_id) == "CANCELLED"


def test_exemption_requires_login(client):
    response = csrf_post(
        client,
        "/api/exemptions",
        {"volunteer_id": str(uuid4()), "requester_id": str(uuid4()), "reason": "asked not to be matched"},
    )

    assert response.status_code == 401


def test_exemption_forbidden_for_requester(client):
    register(client, REQUESTER)

    response = csrf_post(
        client,
        "/api/exemptions",
        {"volunteer_id": str(uuid4()), "requester_id": str(uuid4()), "reason": "asked not to be matched"},
    )

    assert response.status_code == 403


def test_admin_creates_exemption_from_a_request(app, client, engine):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    volunteer = register(client, VOLUNTEER)
    volunteer_id = volunteer.get_json()["user_id"]
    _enable(client, VOLUNTEER_PROFILE)
    request_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _append(
        app,
        request_id,
        1,
        "NoMatchFound",
        {"match_attempt": 0, "reason": "no_eligible_volunteers", "rejection_summary": {}},
        2,
    )
    _login_admin(app, client)

    response = csrf_post(
        client,
        "/api/exemptions",
        {
            "volunteer_id": volunteer_id,
            "requester_id": requester_id,
            "reason": "They asked not to be matched",
        },
    )

    assert response.status_code == 201
    with engine.connect() as conn:
        row = conn.execute(select(exemption_links)).one()
    assert str(row.volunteer_id).replace("-", "") == volunteer_id.replace("-", "")
    assert str(row.requester_id).replace("-", "") == requester_id.replace("-", "")


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
    assert body["items"][0]["status"] == "PENDING_REVIEW"
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


def test_a_requester_sees_only_their_own_requests(client, app):
    enroll(client)
    own_id = submit(client).get_json()["id"]

    other = app.test_client()
    other.get("/api/auth/csrf")
    register(other, dict(REQUESTER, email="other@example.com", full_name="Noa Levi"))
    save_requester(other, dict(REQUESTER_PROFILE, default_city="Tel Aviv"))
    other_id = submit(other, description="Noa's own errand", city="Tel Aviv").get_json()["id"]

    mine = client.get("/api/me/requests?status=all").get_json()
    theirs = other.get("/api/me/requests?status=all").get_json()
    detail = client.get(f"/requests/{own_id}")

    assert [item["id"] for item in mine["items"]] == [own_id]
    assert mine["items"][0]["status"] == "PENDING_REVIEW"
    assert [item["id"] for item in theirs["items"]] == [other_id]
    assert detail.status_code == 200
    assert "ממתין לבדיקה".encode() in detail.data
    assert "חזרה לבקשות".encode() in detail.data
    assert other.get(f"/requests/{own_id}").status_code == 403
    assert "ממתין לבדיקה".encode() in client.get("/me/requests").data


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