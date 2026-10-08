"""Help-request commands. Submit, cancel, and the assignment actions each append events."""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine

from app.commands.bus import Command, CommandBus
from app.commands.dtos import CancelRequestCommand, SubmitHelpRequestCommand
from app.commands.user_commands import Clock, utc_now
from app.domain.aggregates import HelpRequest
from app.domain.errors import ConcurrencyConflict, DomainError, Forbidden, NotFound, ValidationError
from app.domain.matching import capacity_or_overlap, is_self_assignment, rejection_reason
from app.projections.match_projector import MatchProjector
from app.repositories.event_store import SqlEventStore
from app.repositories.matching import SqlMatchingReader
from app.repositories.users import UserRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApproveAssignmentCommand(Command):
    request_id: UUID
    assignment_id: UUID
    approved_by: UUID

    @classmethod
    def from_payload(cls, request_id: UUID, approved_by: UUID, data: Any) -> "ApproveAssignmentCommand":
        body = _object(data)
        return cls(
            request_id=request_id,
            assignment_id=_uuid_field(body, "assignment_id"),
            approved_by=approved_by,
        )


@dataclass(frozen=True)
class RejectAssignmentCommand(Command):
    request_id: UUID
    reason: str
    rejected_by: UUID

    @classmethod
    def from_payload(cls, request_id: UUID, rejected_by: UUID, data: Any) -> "RejectAssignmentCommand":
        body = _object(data)
        return cls(request_id=request_id, reason=_required(body, "reason", 500), rejected_by=rejected_by)


@dataclass(frozen=True)
class OverrideAssignmentCommand(Command):
    request_id: UUID
    volunteer_id: UUID
    reason: str
    approved_by: UUID

    @classmethod
    def from_payload(cls, request_id: UUID, approved_by: UUID, data: Any) -> "OverrideAssignmentCommand":
        body = _object(data)
        reason = _required(body, "reason", 500)
        if len(reason) < 10:
            raise ValidationError({"reason": "Must be at least 10 characters"})
        return cls(
            request_id=request_id,
            volunteer_id=_uuid_field(body, "volunteer_id"),
            reason=reason,
            approved_by=approved_by,
        )


@dataclass(frozen=True)
class RetriggerMatchCommand(Command):
    request_id: UUID
    actor_id: UUID

    @classmethod
    def from_payload(cls, request_id: UUID, actor_id: UUID, data: Any) -> "RetriggerMatchCommand":
        if data is not None and not isinstance(data, dict):
            raise ValidationError({"body": "Expected a JSON object"})
        return cls(request_id=request_id, actor_id=actor_id)


@dataclass(frozen=True)
class SubmitHelpRequestResult:
    request_id: UUID


class RequestCommandHandlers:
    def __init__(
        self,
        engine: Engine,
        event_store: SqlEventStore,
        reader: SqlMatchingReader,
        projector: MatchProjector,
        users: UserRepository,
        clock: Clock = utc_now,
    ) -> None:
        self._engine = engine
        self._event_store = event_store
        self._reader = reader
        self._projector = projector
        self._users = users
        self._clock = clock

    def register_on(self, bus: CommandBus) -> None:
        bus.register(SubmitHelpRequestCommand, self.submit)
        bus.register(ApproveAssignmentCommand, self.approve)
        bus.register(RejectAssignmentCommand, self.reject)
        bus.register(OverrideAssignmentCommand, self.override)
        bus.register(RetriggerMatchCommand, self.retrigger)
        bus.register(CancelRequestCommand, self.cancel)

    def submit(self, cmd: SubmitHelpRequestCommand) -> SubmitHelpRequestResult:
        now = self._clock()
        request = HelpRequest()
        request.submit(
            {
                "requester_id": str(cmd.requester_id),
                "series_id": None,
                "city": cmd.city,
                "address": cmd.address,
                "category": cmd.category,
                "resource_type": cmd.resource_type,
                "description": cmd.description,
                "urgency": cmd.urgency,
                "preferred_date": cmd.preferred_date,
                "preferred_time_from": cmd.preferred_time_from,
                "preferred_time_to": cmd.preferred_time_to,
                "estimated_duration_min": cmd.estimated_duration_min,
                "required_skills": list(cmd.required_skills),
                "requires_vehicle": cmd.requires_vehicle,
                "concurrency_type": "UNKNOWN",
                "match_attempt": 0,
            },
            now,
        )
        pending = request.uncommitted_events()
        with self._engine.begin() as conn:
            account = self._users.get_account(conn, cmd.requester_id)
            if account is None or not account.is_active:
                raise NotFound("User not found")
            if account.is_admin:
                raise Forbidden("An admin account cannot ask for help")
            if self._users.requester_profile_id(conn, cmd.requester_id) is None:
                raise Forbidden("Register to ask for help before submitting a request")
            self._event_store.append_in(conn, request.aggregate_id, 0, pending)
            self._projector.apply(pending, conn)
        request.mark_committed()
        return SubmitHelpRequestResult(request_id=request.aggregate_id)

    def approve(self, command: ApproveAssignmentCommand) -> None:
        request = self._load(command.request_id)
        profile_id = _proposed_volunteer(request, command.assignment_id)
        self._assert_can_approve(request, profile_id)
        request.approve(command.assignment_id, command.approved_by)
        self._commit(request)
        logger.info("gmail_notify_skipped request=%s volunteer=%s", request.aggregate_id, profile_id)

    def reject(self, command: RejectAssignmentCommand) -> None:
        request = self._load(command.request_id)
        request.reject_proposals(command.reason)
        self._commit(request)

    def override(self, command: OverrideAssignmentCommand) -> None:
        request = self._load(command.request_id)
        self._assert_can_override(request, command.volunteer_id)
        request.override(uuid4(), command.volunteer_id, command.reason, command.approved_by)
        self._commit(request)
        logger.info("gmail_notify_skipped request=%s volunteer=%s", request.aggregate_id, command.volunteer_id)

    def retrigger(self, command: RetriggerMatchCommand) -> None:
        request = self._load(command.request_id)
        request.retrigger()
        self._commit(request)

    def cancel(self, command: CancelRequestCommand) -> None:
        request = self._load(command.request_id)
        if not command.is_admin and request.requester_id != command.actor_id:
            raise Forbidden("Your role cannot perform this action")
        notify = request.status == "ASSIGNED"
        request.cancel(command.actor_id, command.reason, self._clock())
        self._commit(request)
        if notify:
            logger.info("gmail_notify_skipped request=%s", request.aggregate_id)

    def _load(self, request_id: UUID) -> HelpRequest:
        history = self._event_store.load_stream(request_id)
        if not history:
            raise NotFound("Help request was not found")
        request = HelpRequest.load(request_id, history)
        request.restore(history)
        return request

    def _commit(self, request: HelpRequest) -> None:
        pending = request.uncommitted_events()
        if not pending:
            return
        self._event_store.append(
            request.aggregate_id,
            request.expected_version,
            pending,
            projector=self._projector,
        )
        request.mark_committed()

    def _assert_can_approve(self, request: HelpRequest, profile_id: UUID) -> None:
        volunteer, facts = self._volunteer_facts(request, profile_id)
        if is_self_assignment(volunteer.user_id, facts.request.requester_id):
            raise DomainError("A volunteer cannot be assigned to their own request")
        if capacity_or_overlap(volunteer, facts):
            raise ConcurrencyConflict("The volunteer no longer has capacity for this request")

    def _assert_can_override(self, request: HelpRequest, profile_id: UUID) -> None:
        volunteer, facts = self._volunteer_facts(request, profile_id)
        if rejection_reason(volunteer, facts) is not None:
            raise DomainError("Volunteer does not pass the hard filter")

    def _volunteer_facts(self, request: HelpRequest, profile_id: UUID):
        with self._engine.connect() as conn:
            view = self._reader.load_request(conn, request.aggregate_id)
            if view is None:
                raise NotFound("Help request was not found")
            volunteers = self._reader.load_volunteers(conn, [profile_id])
            volunteer = volunteers.get(profile_id)
            if volunteer is None:
                raise NotFound("Volunteer was not found")
            facts = self._reader.facts_for(conn, view, [profile_id], self._clock().date())
        return volunteer, facts


def _proposed_volunteer(request: HelpRequest, assignment_id: UUID) -> UUID:
    for proposal in request.proposals:
        if proposal.assignment_id == assignment_id and proposal.status == "PROPOSED":
            return proposal.volunteer_id
    raise DomainError("Choose one proposed volunteer")


def _object(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValidationError({"body": "Expected a JSON object"})
    return data


def _uuid_field(data: dict, field: str) -> UUID:
    raw = data.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError({field: "This field is required"})
    try:
        return UUID(raw.strip())
    except ValueError:
        raise ValidationError({field: "Invalid input"}) from None


def _required(data: dict, field: str, max_len: int) -> str:
    raw = data.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError({field: "This field is required"})
    value = raw.strip()
    if len(value) > max_len:
        raise ValidationError({field: f"Must be at most {max_len} characters"})
    return value
