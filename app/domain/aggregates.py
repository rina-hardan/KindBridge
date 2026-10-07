"""Aggregate roots. Each stream has one aggregate id; version is the concurrency token."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from app.domain import events as ev
from app.domain.errors import ConcurrencyConflict, DomainError
from app.domain.events import DomainEvent

AGGREGATE_TYPES = ("User", "HelpRequest", "VolunteerProfile", "ExemptionLink")


def _apply_method_name(event_type: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(event_type):
        if char.isupper() and index:
            chars.append("_")
        chars.append(char.lower())
    return "apply_" + "".join(chars)


class AggregateRoot:
    """Base aggregate. Subclasses decide; they record events instead of mutating SQL."""

    aggregate_type: ClassVar[str] = ""

    def __init__(self, aggregate_id: UUID | None = None) -> None:
        if not self.aggregate_type:
            raise TypeError(f"{type(self).__name__} must define aggregate_type")
        self.aggregate_id = aggregate_id or uuid4()
        self.version = 0
        self._uncommitted: list[DomainEvent] = []

    @property
    def expected_version(self) -> int:
        """Version the store must still be at when the uncommitted events are appended."""
        return self.version - len(self._uncommitted)

    def uncommitted_events(self) -> list[DomainEvent]:
        return list(self._uncommitted)

    def mark_committed(self) -> None:
        self._uncommitted.clear()

    def raise_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            aggregate_id=self.aggregate_id,
            aggregate_type=self.aggregate_type,
            event_type=event_type,
            payload=payload,
            version=self.version + 1,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        self._apply_new(event)
        return event

    @classmethod
    def load(cls, aggregate_id: UUID, history: Sequence[DomainEvent]) -> "AggregateRoot":
        aggregate = cls(aggregate_id)
        for event in history:
            if event.aggregate_id != aggregate.aggregate_id:
                raise ValueError("history contains an event for a different aggregate")
            if event.aggregate_type != aggregate.aggregate_type:
                raise ValueError("history contains an event for a different aggregate type")
            if event.version != aggregate.version + 1:
                raise ValueError(
                    f"history version {event.version} does not follow {aggregate.version}"
                )
            aggregate._apply(event)
            aggregate.version = event.version
        return aggregate

    def _apply_new(self, event: DomainEvent) -> None:
        self._apply(event)
        self._uncommitted.append(event)
        self.version = event.version

    def _apply(self, event: DomainEvent) -> None:
        handler = getattr(self, _apply_method_name(event.event_type), None)
        if handler is not None:
            handler(event)
            return
        self.apply(event)

    def apply(self, event: DomainEvent) -> None:
        """Fallback when a subclass has no ``apply_<event_type>`` method."""
        return None


class User(AggregateRoot):
    aggregate_type = "User"


class HelpRequest(AggregateRoot):
    """Help-request stream. Propose is idempotent per ``match_attempt``."""

    aggregate_type = "HelpRequest"

    def __init__(self, aggregate_id: UUID | None = None) -> None:
        super().__init__(aggregate_id)
        self.status = ""
        self.match_attempt = 0
        self.concurrency_type = "UNKNOWN"
        self.completed_attempts: set[int] = set()

    def already_proposed(self, match_attempt: int) -> bool:
        return match_attempt in self.completed_attempts

    def record_match(self, match_attempt: int, payloads: list[tuple[str, dict[str, Any]]]) -> None:
        """Append classification and MatchesProposed or NoMatchFound for this attempt.

        A repeated attempt is a no-op. The caller commits the uncommitted events.
        """
        if match_attempt in self.completed_attempts:
            return
        if self.status != "PENDING_REVIEW":
            raise DomainError("Help request is not pending review")
        if match_attempt != self.match_attempt:
            raise ConcurrencyConflict(
                f"match_attempt {match_attempt} does not match the stream head {self.match_attempt}"
            )
        for event_type, payload in payloads:
            self.raise_event(event_type, payload)


class VolunteerProfile(AggregateRoot):
    aggregate_type = "VolunteerProfile"


class ExemptionLink(AggregateRoot):
    aggregate_type = "ExemptionLink"


class UserAggregate:
    """Decides which events a user registration or login produces."""

    @staticmethod
    def register(
        user_id: UUID,
        email: str,
        full_name: str,
        phone_encrypted: str,
        password_hash: str,
        volunteer_profile: dict[str, Any] | None,
        now: datetime,
    ) -> list[DomainEvent]:
        payloads: list[tuple[str, dict[str, Any]]] = [
            (ev.USER_REGISTERED, {"email": email, "full_name": full_name, "phone_encrypted": phone_encrypted}),
            (ev.CREDENTIAL_SET, {"password_hash": password_hash}),
        ]
        if volunteer_profile is not None:
            payloads.append((ev.VOLUNTEER_PROFILE_ENABLED, volunteer_profile))
        return UserAggregate._number(user_id, payloads, start_version=1, now=now)

    @staticmethod
    def bootstrap_admin(
        user_id: UUID,
        email: str,
        full_name: str,
        phone_encrypted: str,
        password_hash: str,
        now: datetime,
    ) -> list[DomainEvent]:
        payloads = [
            (ev.USER_REGISTERED, {"email": email, "full_name": full_name, "phone_encrypted": phone_encrypted}),
            (ev.CREDENTIAL_SET, {"password_hash": password_hash}),
            (ev.ADMIN_BOOTSTRAPPED, {}),
        ]
        return UserAggregate._number(user_id, payloads, start_version=1, now=now)

    @staticmethod
    def logged_in(user_id: UUID, current_version: int, now: datetime) -> DomainEvent:
        return UserAggregate._number(user_id, [(ev.USER_LOGGED_IN, {})], current_version + 1, now)[0]

    @staticmethod
    def _number(
        user_id: UUID,
        payloads: list[tuple[str, dict[str, Any]]],
        start_version: int,
        now: datetime,
    ) -> list[DomainEvent]:
        return [
            DomainEvent(
                aggregate_id=user_id,
                aggregate_type=ev.USER_AGGREGATE,
                event_type=event_type,
                payload=payload,
                version=start_version + offset,
                created_at=now,
            )
            for offset, (event_type, payload) in enumerate(payloads)
        ]
