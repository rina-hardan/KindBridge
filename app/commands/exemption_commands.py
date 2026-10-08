"""Exemption links. One stream per volunteer user and requester pair."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, select

from app.commands.bus import Command, CommandBus
from app.domain.aggregates import ExemptionLink
from app.domain.errors import ConcurrencyConflict, Forbidden, NotFound, ValidationError
from app.projections.exemption_projector import ExemptionProjector
from app.repositories.event_store import SqlEventStore
from app.repositories.tables import help_requests, task_assignments, users, volunteer_profiles


@dataclass(frozen=True)
class CreateExemptionLinkCommand(Command):
    volunteer_user_id: UUID
    requester_id: UUID
    created_by: UUID
    reason: str
    actor_is_admin: bool

    @classmethod
    def from_payload(
        cls, created_by: UUID, actor_is_admin: bool, data: Any
    ) -> "CreateExemptionLinkCommand":
        if not isinstance(data, dict):
            raise ValidationError({"body": "Expected a JSON object"})
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError({"reason": "This field is required"})
        cleaned = reason.strip()
        if len(cleaned) > 300:
            raise ValidationError({"reason": "Must be at most 300 characters"})
        return cls(
            volunteer_user_id=_uuid_field(data, "volunteer_id"),
            requester_id=_uuid_field(data, "requester_id"),
            created_by=created_by,
            reason=cleaned,
            actor_is_admin=actor_is_admin,
        )


class ExemptionCommandHandlers:
    def __init__(self, engine: Engine, event_store: SqlEventStore, projector: ExemptionProjector) -> None:
        self._engine = engine
        self._event_store = event_store
        self._projector = projector

    def register_on(self, bus: CommandBus) -> None:
        bus.register(CreateExemptionLinkCommand, self.create)

    def create(self, command: CreateExemptionLinkCommand) -> None:
        if not command.actor_is_admin and command.volunteer_user_id != command.created_by:
            raise Forbidden("Your role cannot perform this action")
        with self._engine.connect() as conn:
            volunteer = conn.execute(
                select(volunteer_profiles.c.id).where(volunteer_profiles.c.user_id == command.volunteer_user_id)
            ).first()
            if volunteer is None:
                raise NotFound("Volunteer was not found")
            requester = conn.execute(select(users.c.id).where(users.c.id == command.requester_id)).first()
            if requester is None:
                raise NotFound("User not found")
            assigned = conn.execute(
                select(func.count())
                .select_from(
                    task_assignments.join(
                        volunteer_profiles, volunteer_profiles.c.id == task_assignments.c.volunteer_id
                    ).join(help_requests, help_requests.c.id == task_assignments.c.request_id)
                )
                .where(volunteer_profiles.c.user_id == command.volunteer_user_id)
                .where(help_requests.c.requester_id == command.requester_id)
                .where(task_assignments.c.status == "ASSIGNED")
            ).scalar_one()
        if int(assigned):
            raise ConcurrencyConflict("Cancel or release the assignment before creating an exemption")
        stream_id = ExemptionLink.stream_id(command.volunteer_user_id, command.requester_id)
        if self._event_store.load_stream(stream_id):
            raise ConcurrencyConflict("An exemption already exists for this volunteer and requester")
        link = ExemptionLink(stream_id)
        link.raise_event(
            "ExemptionLinkCreated",
            {
                "volunteer_id": str(command.volunteer_user_id),
                "requester_id": str(command.requester_id),
                "created_by": str(command.created_by),
                "reason": command.reason,
            },
        )
        pending = link.uncommitted_events()
        self._event_store.append(stream_id, link.expected_version, pending, projector=self._projector)
        link.mark_committed()


def _uuid_field(data: dict, field: str) -> UUID:
    raw = data.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError({field: "This field is required"})
    try:
        return UUID(raw.strip())
    except ValueError:
        raise ValidationError({field: "Invalid input"}) from None
