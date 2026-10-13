"""The Studio tools speak MCP. Matching tests use an in-process session; this file also launches stdio."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import insert

from agent.tools_mcp import McpStudioClient
from app.repositories.tables import help_requests, users, volunteer_profiles


def test_stdio_server_lists_both_tools_and_calculates_travel():
    client = McpStudioClient()
    try:
        names = client.tool_names()
        remote = client.travel("Haifa", "Tel Aviv", "FLEXIBLE_REMOTE")
        same = client.travel("חיפה", "Haifa", "PHYSICAL_PRESENCE")
        far = client.travel("Haifa", "Eilat", "PHYSICAL_PRESENCE")
    finally:
        client.close()

    assert names == {"CalculateTravelContextTool", "CheckVolunteerCapacityTool"}
    assert remote["feasibility"] == 1.0
    assert remote["estimated_km"] is None
    assert same["same_city"] is True
    assert same["feasibility"] == 1.0
    assert far["feasibility"] == 0.2
    assert far["same_city"] is False


def test_in_process_capacity_reads_the_same_engine_and_classifies_unknown(engine):
    requester = uuid4()
    profile_id = uuid4()
    request_id = uuid4()
    created = datetime(2026, 10, 6, 12, 0, 0)
    with engine.begin() as conn:
        conn.execute(
            insert(users).values(
                id=requester,
                email="requester@example.com",
                password_hash="hash",
                full_name="Requester",
                phone="cipher",
                city="Haifa",
                home_address="1 Harbor Rd",
                is_admin=False,
                is_active=True,
                created_at=created,
            )
        )
        conn.execute(
            insert(users).values(
                id=uuid4(),
                email="volunteer@example.com",
                password_hash="hash",
                full_name="Volunteer",
                phone="cipher",
                city="Haifa",
                home_address="2 Harbor Rd",
                is_admin=False,
                is_active=True,
                created_at=created,
            )
        )
        volunteer_user = conn.execute(users.select().where(users.c.email == "volunteer@example.com")).one()
        conn.execute(
            insert(volunteer_profiles).values(
                id=profile_id,
                user_id=volunteer_user.id,
                is_enabled=True,
                primary_city="Haifa",
                has_vehicle=False,
                skills_json="[]",
                experience="Translates letters",
                base_frequency="ON_DEMAND",
                availability_status="AVAILABLE",
                max_active_tasks=1,
                max_parallel_tasks=2,
                current_active_tasks=1,
                current_parallel_tasks=0,
            )
        )
        conn.execute(
            insert(help_requests).values(
                id=request_id,
                requester_id=requester,
                city="Haifa",
                address="encrypted",
                category="translation",
                resource_type="UNKNOWN",
                description="Translate a letter",
                urgency="NORMAL",
                frequency="ON_DEMAND",
                required_skills_json="[]",
                requires_vehicle=False,
                concurrency_type="UNKNOWN",
                status="PENDING_REVIEW",
                match_attempt=0,
                created_at=created,
            )
        )

    client = McpStudioClient(engine=engine)
    try:
        eligible = client.check_capacity(str(profile_id), str(request_id))
        missing = client.check_capacity(str(uuid4()), str(request_id))
    finally:
        client.close()

    assert eligible == {"eligible": True, "reason": None}
    assert missing == {"eligible": False, "reason": "capacity"}
