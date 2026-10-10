"""Volunteer complete/release, profile edits, and unavailability periods."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from app import create_app
from app.commands.dtos import BootstrapAdminCommand
from app.commands.request_commands import ReleaseTaskCommand, RequestCommandHandlers
from app.domain.events import DomainEvent
from app.infrastructure.notification_service import NoNotifications
from app.projections.match_projector import MatchProjector
from app.repositories.matching import SqlMatchingReader
from app.repositories.tables import event_store, task_assignments, volunteer_profiles, volunteer_unavailability
from app.repositories.users import UserRepository
from tests.conftest import REQUESTER, VOLUNTEER, VOLUNTEER_PROFILE, csrf_post
from tests.test_auth import login, register


class RecordingResumes:
    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.deleted: list[str] = []

    def upsert_resume(self, **kwargs) -> None:
        self.upserts.append(kwargs)

    def delete_resume(self, profile_id: str) -> None:
        self.deleted.append(profile_id)

    def query_resumes(self, text: str, *, top_n: int = 15) -> list:
        return []


class SpyNotifications(NoNotifications):
    def __init__(self) -> None:
        self.released: list[tuple] = []

    def task_released(self, request_id, volunteer_profile_id, reason) -> None:
        self.released.append((request_id, volunteer_profile_id, reason))


def _login_admin(app, client):
    bus = app.extensions["kindbridge"].bus
    bus.dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))
    login(client, "root@kindbridge.org", "admin-password-123")


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


def _assign(app, client):
    requester = register(client, REQUESTER)
    requester_id = requester.get_json()["user_id"]
    volunteer = register(client, VOLUNTEER)
    enabled = csrf_post(client, "/api/me/volunteer", VOLUNTEER_PROFILE)
    profile_id = UUID(enabled.get_json()["profile_id"])
    request_id = uuid4()
    assignment_id = uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _append(
        app,
        request_id,
        1,
        "MatchesProposed",
        {
            "proposals": [
                {
                    "assignment_id": str(assignment_id),
                    "volunteer_id": str(profile_id),
                    "score": 91,
                    "rationale": "Nearby and can drive",
                    "rank": 1,
                }
            ],
            "match_attempt": 0,
            "k": 1,
        },
        2,
    )
    _login_admin(app, client)
    approved = csrf_post(client, f"/api/requests/{request_id}/approve", {"assignment_id": str(assignment_id)})
    assert approved.status_code == 200
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])
    return {
        "requester_id": requester_id,
        "volunteer_id": volunteer.get_json()["user_id"],
        "profile_id": profile_id,
        "request_id": request_id,
        "assignment_id": assignment_id,
    }


def _profile_body(**overrides):
    body = {
        "primary_city": "Tel Aviv",
        "has_vehicle": False,
        "skills": ["first aid"],
        "experience": "Updated resume for clinic visits.",
        "base_frequency": "MONTHLY",
        "availability_status": "AVAILABLE",
        "max_active_tasks": 2,
        "max_parallel_tasks": 1,
    }
    body.update(overrides)
    return body


def _active(engine, profile_id):
    with engine.connect() as conn:
        return conn.execute(
            select(volunteer_profiles.c.current_active_tasks).where(volunteer_profiles.c.id == profile_id)
        ).scalar_one()


def _request_status(engine, request_id):
    from app.repositories.tables import help_requests

    with engine.connect() as conn:
        return conn.execute(select(help_requests.c.status).where(help_requests.c.id == request_id)).scalar_one()


def test_complete_and_release_require_login(client):
    assert csrf_post(client, f"/api/assignments/{uuid4()}/complete", {}).status_code == 401
    assert csrf_post(client, f"/api/assignments/{uuid4()}/release", {}).status_code == 401


def test_complete_forbidden_for_requester_and_other_volunteer(app, client):
    assigned = _assign(app, client)
    register(client, {**REQUESTER, "email": "other@example.com"})
    csrf_post(
        client,
        "/api/me/volunteer",
        {**VOLUNTEER_PROFILE, "experience": "Another volunteer who was not chosen."},
    )

    response = csrf_post(client, f"/api/assignments/{assigned['assignment_id']}/complete", {})

    assert response.status_code == 403
    login(client, REQUESTER["email"], REQUESTER["password"])
    assert csrf_post(client, f"/api/assignments/{assigned['assignment_id']}/release", {}).status_code == 403


def test_assigned_volunteer_completes_task_from_the_tasks_page(app, client, engine):
    assigned = _assign(app, client)

    page = client.get("/me/tasks")
    body = page.data.decode()

    assert page.status_code == 200
    assert "סיום משימה" in body
    assert "שחרור" in body
    assert "12 Harbor Road" in body
    assert f'data-complete="{assigned["assignment_id"]}"' in body

    response = csrf_post(client, f"/api/assignments/{assigned['assignment_id']}/complete", {})

    assert response.status_code == 200
    assert _request_status(engine, assigned["request_id"]) == "COMPLETED"
    assert _active(engine, assigned["profile_id"]) == 0
    with engine.connect() as conn:
        status = conn.execute(
            select(task_assignments.c.status).where(task_assignments.c.id == assigned["assignment_id"])
        ).scalar_one()
    assert status == "COMPLETED"
    hidden = client.get("/me/tasks").data.decode()
    assert "12 Harbor Road" not in hidden
    assert "••••" in hidden


def test_release_declines_the_volunteer_and_notifies(app, client, engine, clock):
    assigned = _assign(app, client)
    spy = SpyNotifications()
    RequestCommandHandlers(
        engine=engine,
        event_store=app.extensions["kindbridge"].event_store,
        reader=SqlMatchingReader(),
        projector=MatchProjector(),
        users=UserRepository(),
        clock=clock,
        notifications=spy,
    ).release(
        ReleaseTaskCommand(
            assignment_id=assigned["assignment_id"],
            actor_id=UUID(assigned["volunteer_id"]),
            reason="I cannot make that day",
        )
    )

    assert _request_status(engine, assigned["request_id"]) == "PENDING_REVIEW"
    assert _active(engine, assigned["profile_id"]) == 0
    with engine.connect() as conn:
        row = conn.execute(
            select(task_assignments.c.status, task_assignments.c.decline_reason).where(
                task_assignments.c.id == assigned["assignment_id"]
            )
        ).one()
    assert row.status == "DECLINED"
    assert row.decline_reason == "I cannot make that day"
    assert spy.released == [
        (assigned["request_id"], assigned["profile_id"], "I cannot make that day")
    ]
    page = client.get("/me/tasks").data.decode()
    assert "עדיין אין לך התנדבויות" in page
    assert f'data-release="{assigned["assignment_id"]}"' not in page


def test_volunteer_edits_profile_and_manages_unavailability(app, client, engine):
    register(client, VOLUNTEER)
    enabled = csrf_post(client, "/api/me/volunteer", VOLUNTEER_PROFILE)
    profile_id = UUID(enabled.get_json()["profile_id"])

    page = client.get("/me/profile")
    assert page.status_code == 302
    assert page.headers["Location"].endswith("/me/volunteer")
    form = client.get("/me/volunteer")
    assert form.status_code == 200
    body = form.data.decode()
    assert "עדכון זמינות" in body
    assert "Five years with Magen David Adom" in body
    assert "תאריכים שבהם אי אפשר לעזור" in body

    saved = csrf_post(client, "/api/profile", _profile_body())
    assert saved.status_code == 200
    with engine.connect() as conn:
        row = conn.execute(select(volunteer_profiles).where(volunteer_profiles.c.id == profile_id)).one()
        updated = conn.execute(
            select(event_store.c.event_type).where(event_store.c.event_type == "VolunteerProfileUpdated")
        ).first()
    assert row.primary_city == "Tel Aviv"
    assert row.availability_status == "AVAILABLE"
    assert row.experience == "Updated resume for clinic visits."
    assert bool(row.is_enabled) is True
    assert updated is not None

    added = csrf_post(
        client,
        "/api/me/unavailability",
        {"from_date": "2026-11-01", "until_date": "2026-11-10", "reason": "נסיעה"},
    )
    assert added.status_code == 201
    period_id = UUID(added.get_json()["unavailability_id"])
    listed = client.get("/api/me/profile").get_json()
    assert listed["periods"][0]["reason"] == "נסיעה"
    assert listed["primary_city"] == "Tel Aviv"

    cancelled = csrf_post(client, f"/api/me/unavailability/{period_id}/cancel", {})
    assert cancelled.status_code == 200
    with engine.connect() as conn:
        period = conn.execute(
            select(volunteer_unavailability).where(volunteer_unavailability.c.id == period_id)
        ).one()
    assert bool(period.is_cancelled) is True
    assert client.get("/api/me/profile").get_json()["periods"] == []


def test_unavailability_and_inactive_are_rejected_over_an_assigned_task(app, client, engine):
    assigned = _assign(app, client)

    covered = csrf_post(
        client,
        "/api/me/unavailability",
        {"from_date": "2020-01-01", "until_date": "2020-01-02"},
    )
    inactive = csrf_post(client, "/api/profile", _profile_body(availability_status="INACTIVE"))

    assert covered.status_code == 409
    assert inactive.status_code == 409
    with engine.connect() as conn:
        status = conn.execute(
            select(volunteer_profiles.c.availability_status).where(
                volunteer_profiles.c.id == assigned["profile_id"]
            )
        ).scalar_one()
        periods = conn.execute(select(volunteer_unavailability)).all()
    assert status == "AVAILABLE"
    assert periods == []

    released = csrf_post(
        client, f"/api/assignments/{assigned['assignment_id']}/release", {"reason": "cannot attend"}
    )
    assert released.status_code == 200
    assert csrf_post(client, "/api/profile", _profile_body(availability_status="INACTIVE")).status_code == 200


def test_profile_update_reembeds_and_inactive_deletes_the_resume(config, engine, clock):
    resumes = RecordingResumes()
    app = create_app(config=config, engine=engine, clock=clock, resumes=resumes)
    client = app.test_client()
    client.get("/api/auth/csrf")
    register(client, VOLUNTEER)
    enabled = csrf_post(client, "/api/me/volunteer", VOLUNTEER_PROFILE)
    profile_id = enabled.get_json()["profile_id"]

    saved = csrf_post(client, "/api/profile", _profile_body())

    assert saved.status_code == 200
    assert resumes.upserts[-1]["experience"] == "Updated resume for clinic visits."
    assert resumes.upserts[-1]["profile_id"] == profile_id
    assert resumes.deleted == []

    inactive = csrf_post(client, "/api/profile", _profile_body(availability_status="INACTIVE"))
    assert inactive.status_code == 200
    assert resumes.deleted == [profile_id]


def test_profile_endpoints_reject_a_requester(client):
    register(client, REQUESTER)

    assert client.get("/api/me/profile").status_code == 403
    assert csrf_post(client, "/api/profile", _profile_body()).status_code == 403
    assert csrf_post(client, "/api/me/unavailability", {"from_date": "2026-11-01", "until_date": "2026-11-02"}).status_code == 403
    assert client.get("/me/profile").status_code == 302
