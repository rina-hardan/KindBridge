"""A volunteer sees only their own ASSIGNED and COMPLETED tasks, on /me/tasks and /api/me/tasks."""

from uuid import UUID, uuid4

from sqlalchemy import select

from app.commands.dtos import BootstrapAdminCommand
from app.repositories.tables import help_requests, task_assignments, volunteer_profiles
from tests.conftest import REQUESTER, VOLUNTEER, VOLUNTEER_PROFILE
from tests.test_auth import bus, login, register
from tests.test_requests import _append, _created_payload, _enable, _propose

OTHER_VOLUNTEER = dict(VOLUNTEER, email="noa@example.com", full_name="Noa Bar", phone="054-1112222")
FUTURE = "2999-01-01"


def _volunteer(client, person=VOLUNTEER) -> UUID:
    register(client, person)
    return UUID(_enable(client, VOLUNTEER_PROFILE).get_json()["profile_id"])


def _requester(client) -> str:
    return register(client, REQUESTER).get_json()["user_id"]


def _assign(app, requester_id, profile_id, **overrides) -> tuple[UUID, UUID]:
    request_id, assignment_id = uuid4(), uuid4()
    payload = _created_payload(requester_id, preferred_date=FUTURE, **overrides)
    _append(app, request_id, 0, "HelpRequestCreated", payload, 1)
    _propose(app, request_id, profile_id, assignment_id)
    _append(
        app,
        request_id,
        2,
        "AssignmentApproved",
        {"assignment_id": str(assignment_id), "volunteer_id": str(profile_id), "approved_by": str(uuid4())},
        3,
    )
    return request_id, assignment_id


def _tasks(client, **query):
    response = client.get("/api/me/tasks", query_string=query)
    assert response.status_code == 200
    return response.get_json()


def test_tasks_page_and_api_require_login(client):
    page = client.get("/me/tasks")

    assert page.status_code == 302
    assert page.headers["Location"].endswith("/login")
    assert client.get("/api/me/tasks").status_code == 401


def test_tasks_api_is_forbidden_without_the_volunteer_role(client):
    register(client, REQUESTER)

    assert client.get("/api/me/tasks").status_code == 403


def test_tasks_api_is_forbidden_for_admin(app, client):
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))
    login(client, "root@kindbridge.org", "admin-password-123")

    assert client.get("/api/me/tasks").status_code == 403
    assert client.get("/me/tasks").headers["Location"].endswith("/me")


def test_new_volunteer_sees_an_empty_list(client):
    _volunteer(client)

    page = client.get("/me/tasks")

    assert page.status_code == 200
    assert "עדיין אין לך התנדבויות".encode() in page.data
    assert _tasks(client)["items"] == []


def test_assigned_task_shows_status_with_address_and_phone(app, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    request_id, assignment_id = _assign(app, requester_id, profile_id, description="Ride to the clinic")

    body = _tasks(client)
    page = client.get("/me/tasks")

    assert body["total"] == 1
    task = body["items"][0]
    assert task["assignment_id"] == str(assignment_id)
    assert task["request_id"] == str(request_id)
    assert task["status"] == "ASSIGNED"
    assert task["address"] == "12 Harbor Road"
    assert task["phone"] == REQUESTER["phone"]
    assert task["requester_name"] == REQUESTER["full_name"]
    assert task["overdue"] is False
    assert "שובץ".encode() in page.data
    assert b"12 Harbor Road" in page.data
    assert b"Ride to the clinic" in page.data


def test_completed_task_hides_address_and_phone(app, engine, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    request_id, assignment_id = _assign(app, requester_id, profile_id)
    _append(app, request_id, 3, "TaskCompleted", {"assignment_id": str(assignment_id)}, 4)

    body = _tasks(client)
    page = client.get("/me/tasks")

    task = body["items"][0]
    assert task["status"] == "COMPLETED"
    assert task["address"] is None
    assert task["phone"] is None
    assert "הושלם".encode() in page.data
    assert b"12 Harbor Road" not in page.data
    assert REQUESTER["phone"].encode() not in page.data
    with engine.connect() as conn:
        status = conn.execute(select(help_requests.c.status).where(help_requests.c.id == request_id)).scalar_one()
        active = conn.execute(
            select(volunteer_profiles.c.current_active_tasks).where(volunteer_profiles.c.id == profile_id)
        ).scalar_one()
    assert status == "COMPLETED"
    assert active == 0


def test_released_task_leaves_the_list(app, engine, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    request_id, assignment_id = _assign(app, requester_id, profile_id)
    _append(app, request_id, 3, "TaskReleased", {"assignment_id": str(assignment_id), "reason": "sick"}, 4)

    assert _tasks(client)["items"] == []
    with engine.connect() as conn:
        row = conn.execute(select(task_assignments).where(task_assignments.c.id == assignment_id)).one()
    assert row.status == "DECLINED"
    assert row.decline_reason == "sick"


def test_only_assigned_and_completed_are_listed(app, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    proposed_only = uuid4()
    _append(app, proposed_only, 0, "HelpRequestCreated", _created_payload(requester_id, preferred_date=FUTURE), 1)
    _propose(app, proposed_only, profile_id, uuid4())
    _assign(app, requester_id, profile_id)

    body = _tasks(client)

    assert [item["status"] for item in body["items"]] == ["ASSIGNED"]


def test_cancelled_assignment_is_not_listed(app, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    request_id, _ = _assign(app, requester_id, profile_id)
    _append(app, request_id, 3, "HelpRequestCancelled", {"cancelled_by": requester_id, "reason": "no need"}, 4)

    assert _tasks(client)["items"] == []


def test_a_volunteer_never_sees_another_volunteers_tasks(app, client):
    requester_id = _requester(client)
    first_profile = _volunteer(client)
    second_profile = _volunteer(client, OTHER_VOLUNTEER)
    _assign(app, requester_id, first_profile, description="First volunteer's job")
    _assign(app, requester_id, second_profile, description="Second volunteer's job")

    second = _tasks(client)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])
    first = _tasks(client)
    first_page = client.get("/me/tasks")

    assert [item["description"] for item in second["items"]] == ["Second volunteer's job"]
    assert [item["description"] for item in first["items"]] == ["First volunteer's job"]
    assert b"Second volunteer" not in first_page.data


def test_status_filter_and_ordering(app, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    done_request, done_assignment = _assign(app, requester_id, profile_id, description="Finished job")
    _append(app, done_request, 3, "TaskCompleted", {"assignment_id": str(done_assignment)}, 4)
    _assign(app, requester_id, profile_id, description="Current job")

    everything = _tasks(client)
    assigned = _tasks(client, status="ASSIGNED")
    completed = _tasks(client, status="COMPLETED")
    unknown = _tasks(client, status="CANCELLED")

    assert [item["status"] for item in everything["items"]] == ["ASSIGNED", "COMPLETED"]
    assert [item["description"] for item in assigned["items"]] == ["Current job"]
    assert [item["description"] for item in completed["items"]] == ["Finished job"]
    assert unknown["total"] == 2
    filtered = client.get("/me/tasks", query_string={"status": "COMPLETED"})
    assert "אין התנדבויות בסטטוס הזה".encode() not in filtered.data
    assert b"Finished job" in filtered.data
    assert b"Current job" not in filtered.data


def test_empty_filter_result_says_so(app, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    _assign(app, requester_id, profile_id)

    page = client.get("/me/tasks", query_string={"status": "COMPLETED"})

    assert page.status_code == 200
    assert "אין התנדבויות בסטטוס הזה".encode() in page.data


def test_past_date_on_assigned_task_is_overdue(app, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    request_id, assignment_id = uuid4(), uuid4()
    _append(app, request_id, 0, "HelpRequestCreated", _created_payload(requester_id), 1)
    _propose(app, request_id, profile_id, assignment_id)
    _append(
        app,
        request_id,
        2,
        "AssignmentApproved",
        {"assignment_id": str(assignment_id), "volunteer_id": str(profile_id), "approved_by": str(uuid4())},
        3,
    )

    assert _tasks(client)["items"][0]["overdue"] is True


def test_pagination_clamps_the_page(app, client):
    requester_id = _requester(client)
    profile_id = _volunteer(client)
    for index in range(21):
        _assign(app, requester_id, profile_id, description=f"Job {index}")

    second = _tasks(client, page=2)
    beyond = _tasks(client, page=99)
    junk = client.get("/api/me/tasks", query_string={"page": "abc"})

    assert second["total"] == 21
    assert len(second["items"]) == 1
    assert beyond["page"] == 2
    assert junk.status_code == 200
    assert junk.get_json()["page"] == 1
