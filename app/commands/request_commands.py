"""Submit and cancel a help request. Both append to the HelpRequest stream."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import Engine

from app.commands.bus import CommandBus
from app.commands.dtos import CancelRequestCommand, SubmitHelpRequestCommand
from app.commands.user_commands import Clock, utc_now
from app.domain.aggregates import HelpRequest
from app.domain.errors import Forbidden, NotFound
from app.projections.match_projector import MatchProjector
from app.repositories.event_store import TransactionalEventStore
from app.repositories.users import UserRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmitHelpRequestResult:
    request_id: uuid.UUID


class RequestCommandHandlers:
    def __init__(
        self,
        engine: Engine,
        event_store: TransactionalEventStore,
        projector: MatchProjector,
        users: UserRepository,
        clock: Clock = utc_now,
    ) -> None:
        self._engine = engine
        self._event_store = event_store
        self._projector = projector
        self._users = users
        self._clock = clock

    def register_on(self, bus: CommandBus) -> None:
        bus.register(SubmitHelpRequestCommand, self.submit)
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
        with self._engine.begin() as conn:
            account = self._users.get_account(conn, cmd.requester_id)
            if account is None or not account.is_active:
                raise NotFound("User not found")
            if account.is_admin:
                raise Forbidden("An admin account cannot ask for help")
            if self._users.requester_profile_id(conn, cmd.requester_id) is None:
                raise Forbidden("Register to ask for help before submitting a request")
            pending = request.uncommitted_events()
            self._event_store.append_in(conn, request.aggregate_id, 0, pending)
            self._projector.apply(pending, conn)
        request.mark_committed()
        return SubmitHelpRequestResult(request_id=request.aggregate_id)

    def cancel(self, cmd: CancelRequestCommand) -> None:
        now = self._clock()
        history = self._event_store.load_stream(cmd.request_id)
        if not history:
            raise NotFound("Help request was not found")
        request = HelpRequest.load(cmd.request_id, history)
        request.restore_lifecycle(history)
        if not cmd.is_admin and request.requester_id != cmd.actor_id:
            raise Forbidden("Your role cannot perform this action")
        was_assigned = request.status == "ASSIGNED"
        request.cancel(cmd.actor_id, cmd.reason, now)
        pending = request.uncommitted_events()
        self._event_store.append(
            request.aggregate_id,
            request.expected_version,
            pending,
            projector=self._projector,
        )
        request.mark_committed()
        if was_assigned:
            logger.info("assigned help request %s cancelled; volunteer notification is not sent yet", cmd.request_id)
