"""ProposeMatchCommand: retrieve 15, hard-filter, score, write the event."""

import json
from datetime import datetime, time
from uuid import uuid4

from sqlalchemy import insert, select

from app.commands.match_commands import MatchCommandHandlers, ProposeMatchCommand
from app.domain.events import DomainEvent
from app.domain.matching import (
    EXCLUSIVE,
    PARALLEL_OK,
    UNKNOWN,
    AssignedTask,
    FilterFacts,
    RequestRecord,
    ScoredVolunteer,
    TimeSlot,
    UnavailabilityPeriod,
    VolunteerRecord,
    default_concurrency,
    is_self_assignment,
    rank_proposals,
    rejection_reason,
    score_candidate,
    tasks_overlap,
)
from app.infrastructure.vector_store import ResumeHit, VectorStoreUnavailable
from app.projections.match_projector import MatchProjector
from app.repositories.event_store import SqlEventStore
from app.repositories.matching import SqlMatchingReader
from app.repositories.tables import (
    event_store,
    exemption_links,
    help_requests,
    task_assignments,
    users,
    volunteer_profiles,
)
from mcp_tools.calculate_travel_context import calculate_travel_context


class Catalog:
    def __init__(self, hits):
        self.hits = list(hits)
        self.calls = []

    def query_resumes(self, text, *, top_n=15):
        self.calls.append({"text": text, "top_n": top_n})
        return list(self.hits[:top_n])

    def upsert_resume(self, **kwargs):
        return None

    def delete_resume(self, profile_id):
        return None


class FailingCatalog(Catalog):
    def __init__(self):
        super().__init__([])

    def query_resumes(self, text, *, top_n=15):
        self.calls.append(top_n)
        raise VectorStoreUnavailable("chroma refused")


class BrokenWeb:
    def search(self, city, today):
        raise TimeoutError("tavily timed out")


def _hit(profile_id, similarity=1.0):
    return ResumeHit(profile_id=str(profile_id), similarity=similarity, document="resume", metadata={})


def _handler(engine, clock, resumes, web=None):
    return MatchCommandHandlers(
        engine=engine,
        event_store=SqlEventStore(engine=engine),
        reader=SqlMatchingReader(),
        projector=MatchProjector(),
        resumes=resumes,
        clock=clock,
        web=web,
        sleep=lambda _seconds: None,
    )


def _user(conn, user_id, email):
    conn.execute(
        insert(users).values(
            id=user_id,
            email=email,
            password_hash="hash",
            full_name="Test User",
            phone="cipher",
            is_admin=False,
            is_active=True,
            created_at=datetime(2026, 10, 6, 12, 0, 0),
        )
    )


def _profile(conn, profile_id, user_id, **overrides):
    values = dict(
        id=profile_id,
        user_id=user_id,
        is_enabled=True,
        primary_city="Haifa",
        has_vehicle=True,
        skills_json=json.dumps(["driving"]),
        experience="Helps with rides",
        base_frequency="ON_DEMAND",
        availability_status="AVAILABLE",
        max_active_tasks=1,
        max_parallel_tasks=2,
        current_active_tasks=0,
        current_parallel_tasks=0,
    )
    values.update(overrides)
    conn.execute(insert(volunteer_profiles).values(**values))


def _open_request(engine, request_id, requester_id, **overrides):
    payload = {
        "requester_id": str(requester_id),
        "city": "Haifa",
        "address": "encrypted-address",
        "category": "transport",
        "resource_type": "PHYSICAL_PRESENCE",
        "description": "Need a ride to the clinic",
        "urgency": "EMERGENCY",
        "preferred_date": "2026-10-20",
        "required_skills": ["driving"],
        "requires_vehicle": False,
        "concurrency_type": "EXCLUSIVE",
    }
    payload.update(overrides)
    event = DomainEvent(
        aggregate_id=request_id,
        aggregate_type="HelpRequest",
        event_type="HelpRequestCreated",
        payload=payload,
        version=1,
        created_at=datetime(2026, 10, 6, 12, 0),
    )
    SqlEventStore(engine=engine).append(request_id, 0, [event], projector=MatchProjector())


def _volunteer(conn, city="Haifa", vehicle=True, active_tasks=0, parallel_tasks=0, frequency="ON_DEMAND"):
    user_id = uuid4()
    profile_id = uuid4()
    _user(conn, user_id, f"{user_id}@example.com")
    _profile(
        conn,
        profile_id,
        user_id,
        primary_city=city,
        has_vehicle=vehicle,
        current_active_tasks=active_tasks,
        current_parallel_tasks=parallel_tasks,
        base_frequency=frequency,
    )
    return user_id, profile_id


def _propose(handler, request_id, match_attempt=0):
    return handler.handle(ProposeMatchCommand(request_id=request_id, match_attempt=match_attempt))


def _rows(engine, request_id):
    with engine.connect() as conn:
        request = conn.execute(select(help_requests).where(help_requests.c.id == request_id)).mappings().one()
        assignments = conn.execute(
            select(task_assignments).where(task_assignments.c.request_id == request_id)
        ).mappings().all()
        events = [
            DomainEvent.from_row(row)
            for row in conn.execute(
                select(event_store).where(event_store.c.aggregate_id == request_id).order_by(event_store.c.version)
            ).mappings()
        ]
    return request, assignments, events


def _request_record(**overrides):
    values = dict(
        request_id=uuid4(),
        requester_id=uuid4(),
        city="Haifa",
        category="transport",
        resource_type="PHYSICAL_PRESENCE",
        description="Need a ride",
        urgency="NORMAL",
        slot=TimeSlot(preferred_date=datetime(2026, 10, 20).date()),
        required_skills=("driving",),
        requires_vehicle=False,
        concurrency_type=EXCLUSIVE,
    )
    values.update(overrides)
    return RequestRecord(**values)


def _volunteer_record(**overrides):
    profile_id = overrides.pop("profile_id", uuid4())
    user_id = overrides.pop("user_id", uuid4())
    values = dict(
        profile_id=profile_id,
        user_id=user_id,
        is_enabled=True,
        is_active=True,
        availability_status="AVAILABLE",
        primary_city="Haifa",
        has_vehicle=True,
        skills=("driving",),
        base_frequency="WEEKLY",
        max_active_tasks=1,
        max_parallel_tasks=2,
        current_active_tasks=0,
        current_parallel_tasks=0,
    )
    values.update(overrides)
    return VolunteerRecord(**values)


def _facts(request, **overrides):
    values = dict(
        request=request,
        exemptions=frozenset(),
        declined_profile_ids=frozenset(),
        periods=(),
        assigned=(),
        today=datetime(2026, 10, 6).date(),
    )
    values.update(overrides)
    return FilterFacts(**values)


def test_three_eligible_volunteers_are_proposed(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        profiles = [_volunteer(conn)[1] for _ in range(3)]
    _open_request(engine, request_id, requester)
    resumes = Catalog([_hit(profile, similarity) for profile, similarity in zip(profiles, (0.2, 0.9, 0.5))])

    result = _propose(_handler(engine, clock, resumes), request_id)

    request, assignments, events = _rows(engine, request_id)
    assert result.outcome == "proposed"
    assert result.proposed == 3
    assert request["status"] == "MATCH_PROPOSED"
    assert request["match_attempt"] == 1
    assert resumes.calls[0]["top_n"] == 15
    assert "Need a ride to the clinic" in resumes.calls[0]["text"]
    assert "transport" in resumes.calls[0]["text"]
    assert "driving" in resumes.calls[0]["text"]
    assert [row["status"] for row in assignments] == ["PROPOSED", "PROPOSED", "PROPOSED"]
    assert sorted(row["rank_in_batch"] for row in assignments) == [1, 2, 3]
    assert all(0 <= float(row["ai_score"]) <= 100 for row in assignments)
    assert all(float(row["ai_score"]) != similarity for row, similarity in zip(assignments, (0.2, 0.9, 0.5)))
    proposed = [event for event in events if event.event_type == "MatchesProposed"]
    assert len(proposed) == 1
    assert proposed[0].payload["match_attempt"] == 0
    assert proposed[0].payload["k"] == 3
    assert {event.event_type for event in events} <= {
        "HelpRequestCreated",
        "MatchesProposed",
        "ConcurrencyClassified",
        "NoMatchFound",
    }


def test_score_uses_weights_and_overdue_penalty(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _user_id, profile_id = _volunteer(conn)
    _open_request(engine, request_id, requester, preferred_date="2026-10-01")

    result = _propose(_handler(engine, clock, Catalog([_hit(profile_id, 1.0)])), request_id)

    _request, assignments, _events = _rows(engine, request_id)
    assert result.outcome == "proposed"
    assert float(assignments[0]["ai_score"]) == 85.0
    assert "web_lookup=skipped" in assignments[0]["ai_rationale"]


def test_later_retrieved_volunteers_survive_when_the_top_hits_fail(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        rejected = [_volunteer(conn, vehicle=False)[1] for _ in range(3)]
        kept = [_volunteer(conn, vehicle=True)[1] for _ in range(2)]
        outsider = _volunteer(conn, vehicle=True)[1]
    _open_request(engine, request_id, requester, requires_vehicle=True)
    hits = [_hit(profile, 0.99) for profile in rejected] + [_hit(profile, 0.1) for profile in kept]
    resumes = Catalog(hits)

    result = _propose(_handler(engine, clock, resumes), request_id)

    _request, assignments, events = _rows(engine, request_id)
    assert result.outcome == "proposed"
    assert result.proposed == 2
    assert {row["volunteer_id"] for row in assignments} == set(kept)
    assert outsider not in {row["volunteer_id"] for row in assignments}
    assert not any(event.event_type == "NoMatchFound" for event in events)
    assert resumes.calls[0]["top_n"] == 15


def test_empty_pool_writes_no_match_with_rejection_summary(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _volunteer(conn, vehicle=False)
    _open_request(engine, request_id, requester, requires_vehicle=True)

    result = _propose(_handler(engine, clock, Catalog([])), request_id)

    request, assignments, events = _rows(engine, request_id)
    assert result.outcome == "no_match"
    assert request["status"] == "NO_MATCH"
    assert assignments == []
    found = next(event for event in events if event.event_type == "NoMatchFound")
    assert found.payload["reason"] == "no_eligible_volunteers"
    assert found.payload["rejection_summary"] == {}
    assert found.payload["match_attempt"] == 0


def test_tavily_failure_still_proposes_with_skipped_lookup(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        profile_id = _volunteer(conn)[1]
    _open_request(engine, request_id, requester)

    result = _propose(_handler(engine, clock, Catalog([_hit(profile_id)]), web=BrokenWeb()), request_id)

    _request, assignments, _events = _rows(engine, request_id)
    assert result.outcome == "proposed"
    assert float(assignments[0]["ai_score"]) == 100.0
    assert "web_lookup=skipped" in assignments[0]["ai_rationale"]


def test_same_match_attempt_does_not_duplicate_rows(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        profile_id = _volunteer(conn)[1]
    _open_request(engine, request_id, requester)
    handler = _handler(engine, clock, Catalog([_hit(profile_id)]))

    first = _propose(handler, request_id)
    second = _propose(handler, request_id)

    _request, assignments, events = _rows(engine, request_id)
    assert first.outcome == "proposed"
    assert second.outcome == "noop"
    assert len(assignments) == 1
    assert [event.event_type for event in events].count("MatchesProposed") == 1


def test_chroma_outage_leaves_the_request_pending(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
    _open_request(engine, request_id, requester)
    resumes = FailingCatalog()

    result = _propose(_handler(engine, clock, resumes), request_id)

    request, assignments, events = _rows(engine, request_id)
    assert result.outcome == "deferred"
    assert request["status"] == "PENDING_REVIEW"
    assert assignments == []
    assert [event.event_type for event in events] == ["HelpRequestCreated"]
    assert resumes.calls == [15, 15, 15]


def test_exemption_uses_the_user_id(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    blocked_user = uuid4()
    blocked_profile = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        _user(conn, blocked_user, "blocked@example.com")
        _profile(conn, blocked_profile, blocked_user)
        open_user, open_profile = _volunteer(conn)
        conn.execute(
            insert(exemption_links).values(
                volunteer_id=blocked_user,
                requester_id=requester,
                created_by=requester,
                reason="asked not to be matched",
                created_at=datetime(2026, 10, 6, 12, 0, 0),
            )
        )
    _open_request(engine, request_id, requester)

    result = _propose(
        _handler(engine, clock, Catalog([_hit(blocked_profile, 0.99), _hit(open_profile, 0.4)])),
        request_id,
    )

    _request, assignments, events = _rows(engine, request_id)
    assert result.outcome == "proposed"
    assert [row["volunteer_id"] for row in assignments] == [open_profile]
    assert blocked_user != blocked_profile
    found = [event for event in events if event.event_type == "NoMatchFound"]
    assert found == []
    assert open_user != blocked_user


def test_unknown_concurrency_is_exclusive_for_capacity(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        profile_id = _volunteer(conn, active_tasks=1)[1]
    _open_request(engine, request_id, requester, concurrency_type="UNKNOWN")

    result = _propose(_handler(engine, clock, Catalog([_hit(profile_id)])), request_id)

    request, assignments, events = _rows(engine, request_id)
    assert result.outcome == "no_match"
    assert request["status"] == "NO_MATCH"
    assert request["concurrency_type"] == EXCLUSIVE
    assert assignments == []
    classified = next(event for event in events if event.event_type == "ConcurrencyClassified")
    assert classified.payload["concurrency_type"] == EXCLUSIVE
    found = next(event for event in events if event.event_type == "NoMatchFound")
    assert found.payload["rejection_summary"] == {"capacity": 1}


def test_exclusive_tasks_on_the_same_date_overlap(engine, clock):
    requester = uuid4()
    request_id = uuid4()
    other_request = uuid4()
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        profile_id = _volunteer(conn)[1]
        conn.execute(
            insert(help_requests).values(
                id=other_request,
                requester_id=requester,
                city="Haifa",
                address="encrypted",
                category="errands",
                resource_type="PHYSICAL_PRESENCE",
                description="Already assigned",
                urgency="NORMAL",
                preferred_date=datetime(2026, 10, 20).date(),
                required_skills_json="[]",
                requires_vehicle=False,
                concurrency_type=EXCLUSIVE,
                status="ASSIGNED",
                match_attempt=1,
                created_at=datetime(2026, 10, 1, 9, 0, 0),
            )
        )
        conn.execute(
            insert(task_assignments).values(
                id=uuid4(),
                request_id=other_request,
                volunteer_id=profile_id,
                match_attempt=1,
                status="ASSIGNED",
                updated_at=datetime(2026, 10, 1, 9, 0, 0),
            )
        )
    _open_request(engine, request_id, requester, preferred_date="2026-10-20")

    result = _propose(_handler(engine, clock, Catalog([_hit(profile_id)])), request_id)

    _request, assignments, events = _rows(engine, request_id)
    assert result.outcome == "no_match"
    assert assignments == []
    found = next(event for event in events if event.event_type == "NoMatchFound")
    assert found.payload["rejection_summary"] == {"schedule_overlap": 1}


def test_pending_poll_orders_emergency_first(engine):
    low = uuid4()
    urgent = uuid4()
    done = uuid4()
    requester = uuid4()
    stamp = datetime(2026, 10, 6, 8, 0, 0)
    with engine.begin() as conn:
        _user(conn, requester, "requester@example.com")
        for request_id, urgency, status, created_at in (
            (low, "NORMAL", "PENDING_REVIEW", stamp),
            (urgent, "EMERGENCY", "PENDING_REVIEW", datetime(2026, 10, 6, 11, 0, 0)),
            (done, "EMERGENCY", "MATCH_PROPOSED", stamp),
        ):
            conn.execute(
                insert(help_requests).values(
                    id=request_id,
                    requester_id=requester,
                    city="Haifa",
                    address="encrypted",
                    category="transport",
                    resource_type="FLEXIBLE_REMOTE",
                    description="check",
                    urgency=urgency,
                    required_skills_json="[]",
                    requires_vehicle=False,
                    concurrency_type="PARALLEL_OK",
                    status=status,
                    match_attempt=0,
                    created_at=created_at,
                )
            )
        pending = SqlMatchingReader().pending_requests(conn)

    assert [row.request_id for row in pending] == [urgent, low]


def test_hard_filter_rules_and_score_formula():
    volunteer = _volunteer_record()
    request = _request_record()
    assert rejection_reason(volunteer, _facts(request)) is None
    assert is_self_assignment(volunteer.user_id, volunteer.user_id)
    assert not is_self_assignment(volunteer.user_id, request.requester_id)

    exempted = _facts(request, exemptions=frozenset({volunteer.user_id}))
    assert rejection_reason(volunteer, exempted) == "exemption"
    wrong_id = _facts(request, exemptions=frozenset({volunteer.profile_id}))
    assert rejection_reason(volunteer, wrong_id) is None

    busy = _volunteer_record(current_active_tasks=1)
    unknown = _request_record(concurrency_type=UNKNOWN)
    assert rejection_reason(busy, _facts(unknown)) == "capacity"
    parallel = _request_record(concurrency_type=PARALLEL_OK, resource_type="FLEXIBLE_REMOTE")
    assert rejection_reason(busy, _facts(parallel)) is None

    same_day = TimeSlot(preferred_date=datetime(2026, 10, 20).date())
    exclusive_task = AssignedTask(concurrency_type=EXCLUSIVE, slot=same_day)
    assert tasks_overlap(same_day, EXCLUSIVE, same_day, EXCLUSIVE)
    assert tasks_overlap(same_day, UNKNOWN, same_day, EXCLUSIVE)
    assert not tasks_overlap(same_day, PARALLEL_OK, same_day, PARALLEL_OK)
    window = TimeSlot(
        preferred_date=same_day.preferred_date,
        time_from=time(9, 0),
        time_to=time(11, 0),
    )
    other_window = TimeSlot(
        preferred_date=same_day.preferred_date,
        time_from=time(10, 0),
        time_to=time(12, 0),
    )
    assert tasks_overlap(window, EXCLUSIVE, other_window, PARALLEL_OK)
    assert rejection_reason(
        volunteer,
        _facts(request, assigned=((volunteer.profile_id, exclusive_task),)),
    ) == "schedule_overlap"

    away = _volunteer_record(primary_city="Tel Aviv")
    assert rejection_reason(away, _facts(request)) == "geography"
    remote = _request_record(resource_type="FLEXIBLE_REMOTE", concurrency_type=PARALLEL_OK)
    assert rejection_reason(away, _facts(remote)) is None

    period = UnavailabilityPeriod(
        volunteer_id=volunteer.profile_id,
        from_date=datetime(2026, 10, 20).date(),
        until_date=datetime(2026, 10, 22).date(),
    )
    assert rejection_reason(volunteer, _facts(request, periods=(period,))) == "unavailability"

    score, parts = score_candidate(
        similarity=1,
        skills=1,
        travel_feasibility=1,
        urgency="EMERGENCY",
        has_vehicle_fit=True,
        frequency="ON_DEMAND",
        preferred_date=datetime(2026, 10, 20).date(),
        today=datetime(2026, 10, 6).date(),
    )
    assert score == 100
    assert parts["similarity"] == 0.45
    overdue, _parts = score_candidate(
        similarity=1,
        skills=1,
        travel_feasibility=1,
        urgency="EMERGENCY",
        has_vehicle_fit=True,
        frequency="ON_DEMAND",
        preferred_date=datetime(2026, 10, 1).date(),
        today=datetime(2026, 10, 6).date(),
    )
    assert overdue == 85
    zero = ScoredVolunteer(volunteer_id=volunteer.profile_id, score=0, similarity=0, rationale="none")
    assert rank_proposals([zero]) == [zero]
    assert default_concurrency("FLEXIBLE_REMOTE")[0] == PARALLEL_OK
    assert default_concurrency("PHYSICAL_PRESENCE")[0] == EXCLUSIVE

    remote_travel = calculate_travel_context("Haifa", "Tel Aviv", "FLEXIBLE_REMOTE")
    assert remote_travel["feasibility"] == 1
    assert calculate_travel_context("Haifa", "Haifa", "PHYSICAL_PRESENCE")["feasibility"] == 1
    assert calculate_travel_context("Haifa", "Akko", "PHYSICAL_PRESENCE", distance_km=30)["feasibility"] == 0.6
    assert calculate_travel_context("Haifa", "Eilat", "PHYSICAL_PRESENCE")["feasibility"] == 0.2
