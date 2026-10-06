"""Decisions, versions, and event application for User, HelpRequest, and VolunteerProfile."""

from uuid import UUID, uuid4

import pytest

from app.domain.aggregates import AGGREGATE_TYPES, HelpRequest, User, VolunteerProfile
from app.domain.events import DomainEvent

_USER_REGISTERED = {
    "email": "ada@kindbridge.org",
    "role": "VOLUNTEER",
    "full_name": "Ada",
    "phone_encrypted": "cipher",
}

# event_type, payload, apply_* method the aggregate must call
USER_EVENTS = [
    ("UserRegistered", _USER_REGISTERED, "apply_user_registered"),
    ("CredentialSet", {"password_hash": "bcrypt-hash"}, "apply_credential_set"),
    ("UserLoggedIn", {}, "apply_user_logged_in"),
    ("UserDeactivated", {}, "apply_user_deactivated"),
]

_REQUESTER_ID = str(uuid4())
_VOLUNTEER_ID = str(uuid4())
_ASSIGNMENT_ID = str(uuid4())
_APPROVER_ID = str(uuid4())

HELP_REQUEST_EVENTS = [
    (
        "HelpRequestCreated",
        {
            "city": "חיפה",
            "address": "1 Harbor Rd",
            "category": "errands",
            "resource_type": "PHYSICAL_PRESENCE",
            "description": "Grocery run",
            "urgency": "NORMAL",
            "dates": {"preferred_date": "2026-10-20"},
            "required_skills": ["driving"],
            "requires_vehicle": True,
            "requester_id": _REQUESTER_ID,
        },
        "apply_help_request_created",
    ),
    (
        "MatchesProposed",
        {
            "matches": [
                {
                    "volunteer_id": _VOLUNTEER_ID,
                    "score": 0.91,
                    "rationale": "nearby and has a vehicle",
                    "rank": 1,
                }
            ],
            "match_attempt": 1,
            "k": 1,
        },
        "apply_matches_proposed",
    ),
    (
        "AssignmentsRejected",
        {"volunteer_ids": [_VOLUNTEER_ID], "reason": "unavailable that day"},
        "apply_assignments_rejected",
    ),
    (
        "NoMatchFound",
        {"match_attempt": 2, "reason": "no volunteer passed the hard filter"},
        "apply_no_match_found",
    ),
    ("MatchRetriggered", {}, "apply_match_retriggered"),
    (
        "AssignmentOverridden",
        {
            "assignment_id": _ASSIGNMENT_ID,
            "volunteer_id": _VOLUNTEER_ID,
            "override_reason": "dispatcher knows this volunteer",
            "approved_by": _APPROVER_ID,
        },
        "apply_assignment_overridden",
    ),
    (
        "TaskReleased",
        {"assignment_id": _ASSIGNMENT_ID, "reason": "schedule conflict"},
        "apply_task_released",
    ),
    (
        "AssignmentApproved",
        {
            "assignment_id": _ASSIGNMENT_ID,
            "volunteer_id": _VOLUNTEER_ID,
            "approved_by": _APPROVER_ID,
        },
        "apply_assignment_approved",
    ),
    ("TaskCompleted", {"assignment_id": _ASSIGNMENT_ID}, "apply_task_completed"),
    (
        "HelpRequestCancelled",
        {"cancelled_by": _REQUESTER_ID, "reason": "no longer needed"},
        "apply_help_request_cancelled",
    ),
]

VOLUNTEER_PROFILE_EVENTS = [
    (
        "VolunteerProfileUpdated",
        {
            "skills": ["driving", "shopping"],
            "experience": "Weekly grocery runs in Haifa",
            "city": "Haifa",
            "vehicle": True,
            "frequency": "WEEKLY",
        },
        "apply_volunteer_profile_updated",
    ),
    (
        "VolunteerAvailabilityChanged",
        {"status": "TEMPORARILY_UNAVAILABLE", "unavailable_until": "2026-11-01T00:00:00+00:00"},
        "apply_volunteer_availability_changed",
    ),
    (
        "VolunteerAvailabilityChanged",
        {"status": "INACTIVE", "unavailable_until": None},
        "apply_volunteer_availability_changed",
    ),
]

STREAMS = [
    (User, USER_EVENTS),
    (HelpRequest, HELP_REQUEST_EVENTS),
    (VolunteerProfile, VOLUNTEER_PROFILE_EVENTS),
]


def _recording(aggregate_cls, events):
    """Subclass that records which apply_* method ran and the version it observed."""

    class Recording(aggregate_cls):
        def __init__(self, aggregate_id=None):
            super().__init__(aggregate_id)
            self.applied: list[tuple[str, UUID, int]] = []

        def apply(self, event: DomainEvent) -> None:
            self.applied.append(("apply", event.event_id, self.version))

    def _bind(method_name):
        def handler(self, event: DomainEvent) -> None:
            assert event.version == self.version + 1
            self.applied.append((method_name, event.event_id, self.version))

        handler.__name__ = method_name
        return handler

    for _event_type, _payload, method_name in events:
        setattr(Recording, method_name, _bind(method_name))
    return Recording


@pytest.mark.parametrize("aggregate_cls", (User, HelpRequest, VolunteerProfile))
def test_new_aggregate_starts_at_version_zero(aggregate_cls):
    aggregate_id = uuid4()
    aggregate = aggregate_cls(aggregate_id)
    other = aggregate_cls()

    assert aggregate.aggregate_id == aggregate_id
    assert isinstance(other.aggregate_id, UUID)
    assert other.aggregate_id != aggregate_id
    assert aggregate.aggregate_type == aggregate_cls.aggregate_type
    assert aggregate.aggregate_type in AGGREGATE_TYPES
    assert aggregate.version == 0
    assert aggregate.expected_version == 0
    assert aggregate.uncommitted_events() == []


@pytest.mark.parametrize("aggregate_cls,events", STREAMS)
def test_raise_event_records_stream_and_dispatches_apply_method(aggregate_cls, events):
    recording_cls = _recording(aggregate_cls, events)
    aggregate_id = uuid4()
    correlation_id = uuid4()
    causation_id = uuid4()
    aggregate = recording_cls(aggregate_id)

    raised = []
    for index, (event_type, payload, method_name) in enumerate(events, start=1):
        payload_copy = dict(payload)
        event = aggregate.raise_event(
            event_type,
            payload_copy,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        payload_copy["mutated"] = True
        raised.append(event)

        assert event is aggregate.uncommitted_events()[-1]
        assert event.aggregate_id == aggregate_id
        assert event.aggregate_type == aggregate_cls.aggregate_type
        assert event.event_type == event_type
        assert event.version == index
        assert event.correlation_id == correlation_id
        assert event.causation_id == causation_id
        assert "mutated" not in event.payload
        assert dict(event.payload) == payload
        assert aggregate.version == index
        assert aggregate.expected_version == 0
        assert aggregate.applied[-1] == (method_name, event.event_id, index - 1)

    assert [name for name, _event_id, _version in aggregate.applied] == [
        method_name for _event_type, _payload, method_name in events
    ]
    assert [event.version for event in aggregate.uncommitted_events()] == list(
        range(1, len(events) + 1)
    )


@pytest.mark.parametrize("aggregate_cls,events", STREAMS)
def test_callers_cannot_mutate_the_uncommitted_list(aggregate_cls, events):
    event_type, payload, _method_name = events[0]
    aggregate = aggregate_cls()
    aggregate.raise_event(event_type, payload)

    snapshot = aggregate.uncommitted_events()
    snapshot.clear()
    snapshot.append(None)

    remaining = aggregate.uncommitted_events()
    assert len(remaining) == 1
    assert remaining[0].event_type == event_type
    assert aggregate.version == 1
    assert aggregate.expected_version == 0


@pytest.mark.parametrize("aggregate_cls,events", STREAMS)
def test_mark_committed_keeps_version_and_next_event_continues_the_stream(aggregate_cls, events):
    aggregate = aggregate_cls()
    first_type, first_payload, _method = events[0]
    second_type, second_payload, _method = events[1]

    aggregate.raise_event(first_type, first_payload)
    aggregate.mark_committed()

    assert aggregate.uncommitted_events() == []
    assert aggregate.version == 1
    assert aggregate.expected_version == 1

    follow_up = aggregate.raise_event(second_type, second_payload)
    assert follow_up.version == 2
    assert aggregate.version == 2
    assert aggregate.expected_version == 1
    assert [event.event_type for event in aggregate.uncommitted_events()] == [second_type]


@pytest.mark.parametrize("aggregate_cls,events", STREAMS)
def test_load_replays_apply_methods_without_staging_events(aggregate_cls, events):
    recording_cls = _recording(aggregate_cls, events)
    aggregate_id = uuid4()
    draft = recording_cls(aggregate_id)
    for event_type, payload, _method_name in events:
        draft.raise_event(event_type, payload)
    history = draft.uncommitted_events()

    restored = recording_cls.load(aggregate_id, history)

    assert restored.aggregate_id == aggregate_id
    assert restored.version == len(events)
    assert restored.expected_version == len(events)
    assert restored.uncommitted_events() == []
    assert restored.applied == [
        (method_name, event.event_id, index)
        for index, ((_event_type, _payload, method_name), event) in enumerate(
            zip(events, history)
        )
    ]

    follow_up_type, follow_up_payload, follow_up_method = events[0]
    follow_up = restored.raise_event(follow_up_type, follow_up_payload)
    assert follow_up.version == len(events) + 1
    assert restored.expected_version == len(events)
    assert restored.applied[-1] == (follow_up_method, follow_up.event_id, len(events))


@pytest.mark.parametrize("aggregate_cls,events", STREAMS)
def test_unknown_event_uses_fallback_apply(aggregate_cls, events):
    class Fallback(aggregate_cls):
        def __init__(self, aggregate_id=None):
            super().__init__(aggregate_id)
            self.applied: list[str] = []

        def apply(self, event: DomainEvent) -> None:
            self.applied.append(event.event_type)

    event_type, payload, method_name = events[0]
    known_type, known_payload, _known_method = events[1]

    class Mixed(Fallback):
        pass

    setattr(
        Mixed,
        method_name,
        lambda self, event: self.applied.append(method_name),
    )

    aggregate = Mixed()
    aggregate.raise_event(event_type, payload)
    aggregate.raise_event(known_type, known_payload)
    assert aggregate.applied == [method_name, known_type]


@pytest.mark.parametrize("aggregate_cls,events", STREAMS)
def test_failed_apply_does_not_record_the_event(aggregate_cls, events):
    event_type, payload, method_name = events[0]
    later_type, later_payload, _later_method = events[1]

    class Rejecting(aggregate_cls):
        def __init__(self, aggregate_id=None):
            super().__init__(aggregate_id)
            self.applied: list[str] = []

        def apply(self, event: DomainEvent) -> None:
            self.applied.append(event.event_type)

    def reject(self, _event):
        raise ValueError("decision rejected")

    setattr(Rejecting, method_name, reject)

    aggregate = Rejecting()
    with pytest.raises(ValueError, match="decision rejected"):
        aggregate.raise_event(event_type, payload)

    assert aggregate.version == 0
    assert aggregate.uncommitted_events() == []

    recorded = aggregate.raise_event(later_type, later_payload)
    assert recorded.version == 1
    assert aggregate.version == 1
    assert [event.event_type for event in aggregate.uncommitted_events()] == [later_type]
    assert aggregate.applied == [later_type]


def test_failed_apply_leaves_earlier_user_events_in_place():
    class RejectDeactivation(User):
        def apply_user_deactivated(self, _event: DomainEvent) -> None:
            raise ValueError("already inactive")

    user = RejectDeactivation()
    user.raise_event("UserRegistered", _USER_REGISTERED)
    user.raise_event("CredentialSet", {"password_hash": "bcrypt-hash"})

    with pytest.raises(ValueError, match="already inactive"):
        user.raise_event("UserDeactivated", {})

    assert user.version == 2
    assert user.expected_version == 0
    assert [event.event_type for event in user.uncommitted_events()] == [
        "UserRegistered",
        "CredentialSet",
    ]


def test_help_request_apply_failure_on_replay_does_not_return_a_partial_aggregate():
    request = HelpRequest()
    request.raise_event(
        "HelpRequestCreated",
        {"city": "Haifa", "requester_id": _REQUESTER_ID},
    )
    request.raise_event(
        "HelpRequestCancelled",
        {"cancelled_by": _REQUESTER_ID, "reason": "withdrawn"},
    )
    history = request.uncommitted_events()

    class RejectCancel(HelpRequest):
        def apply_help_request_cancelled(self, _event: DomainEvent) -> None:
            raise ValueError("cannot cancel")

    with pytest.raises(ValueError, match="cannot cancel"):
        RejectCancel.load(request.aggregate_id, history)

    restored = RejectCancel.load(request.aggregate_id, history[:1])
    assert restored.version == 1
    assert restored.uncommitted_events() == []


def test_volunteer_profile_replay_sees_each_availability_change():
    recording_cls = _recording(VolunteerProfile, VOLUNTEER_PROFILE_EVENTS)
    profile = recording_cls()
    for event_type, payload, _method_name in VOLUNTEER_PROFILE_EVENTS:
        profile.raise_event(event_type, payload)

    restored = recording_cls.load(profile.aggregate_id, profile.uncommitted_events())
    statuses = [
        event.payload["status"]
        for event in profile.uncommitted_events()
        if event.event_type == "VolunteerAvailabilityChanged"
    ]
    assert statuses == ["TEMPORARILY_UNAVAILABLE", "INACTIVE"]
    assert [name for name, _event_id, _version in restored.applied].count(
        "apply_volunteer_availability_changed"
    ) == 2
    assert restored.version == 3


@pytest.mark.parametrize("aggregate_cls,events", STREAMS)
def test_load_rejects_a_foreign_aggregate_id(aggregate_cls, events):
    event_type, payload, _method_name = events[0]
    aggregate = aggregate_cls()
    event = aggregate.raise_event(event_type, payload)

    with pytest.raises(ValueError, match="event for a different aggregate$"):
        aggregate_cls.load(uuid4(), [event])


@pytest.mark.parametrize(
    "source_cls,target_cls",
    [
        (User, HelpRequest),
        (User, VolunteerProfile),
        (HelpRequest, User),
        (HelpRequest, VolunteerProfile),
        (VolunteerProfile, User),
        (VolunteerProfile, HelpRequest),
    ],
)
def test_load_rejects_a_different_aggregate_type(source_cls, target_cls):
    streams = dict(STREAMS)
    event_type, payload, _method_name = streams[source_cls][0]
    source = source_cls()
    event = source.raise_event(event_type, payload)

    with pytest.raises(ValueError, match="different aggregate type"):
        target_cls.load(source.aggregate_id, [event])


@pytest.mark.parametrize("aggregate_cls,events", STREAMS)
def test_load_rejects_a_version_gap(aggregate_cls, events):
    event_type, payload, _method_name = events[0]
    aggregate = aggregate_cls()
    first = aggregate.raise_event(event_type, payload)
    skipped = DomainEvent(
        aggregate_id=aggregate.aggregate_id,
        aggregate_type=aggregate.aggregate_type,
        event_type=events[1][0],
        payload=events[1][1],
        version=3,
        event_id=uuid4(),
    )

    with pytest.raises(ValueError, match="history version 3 does not follow 1"):
        aggregate_cls.load(aggregate.aggregate_id, [first, skipped])


@pytest.mark.parametrize("aggregate_cls", (User, HelpRequest, VolunteerProfile))
def test_load_rejects_history_that_does_not_start_at_version_one(aggregate_cls):
    aggregate_id = uuid4()
    event = DomainEvent(
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_cls.aggregate_type,
        event_type="UserRegistered"
        if aggregate_cls is User
        else "HelpRequestCreated"
        if aggregate_cls is HelpRequest
        else "VolunteerProfileUpdated",
        payload={},
        version=2,
    )

    with pytest.raises(ValueError, match="history version 2 does not follow 0"):
        aggregate_cls.load(aggregate_id, [event])


@pytest.mark.parametrize("aggregate_cls", (User, HelpRequest, VolunteerProfile))
def test_load_of_an_empty_stream_is_a_new_aggregate(aggregate_cls):
    aggregate_id = uuid4()
    restored = aggregate_cls.load(aggregate_id, [])

    assert restored.aggregate_id == aggregate_id
    assert restored.version == 0
    assert restored.expected_version == 0
    assert restored.uncommitted_events() == []


def test_user_credential_event_keeps_the_hash_out_of_repr():
    user = User()
    event = user.raise_event("CredentialSet", {"password_hash": "bcrypt-hash"})

    assert event.payload["password_hash"] == "bcrypt-hash"
    assert "bcrypt-hash" not in repr(event)


@pytest.mark.parametrize("aggregate_cls", (User, HelpRequest, VolunteerProfile))
def test_plaintext_password_is_rejected_before_the_event_is_recorded(aggregate_cls):
    aggregate = aggregate_cls()

    with pytest.raises(ValueError, match="plaintext password"):
        aggregate.raise_event("CredentialSet", {"profile": {"password": "secret-value"}})

    assert aggregate.version == 0
    assert aggregate.uncommitted_events() == []
