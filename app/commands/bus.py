"""Command bus shared by Flask controllers and the agent process.

Mutating use cases dispatch a Command here. Handlers load an aggregate, decide,
and ``commit`` the new events. Queries do not use this bus.
"""

from collections.abc import Callable
from typing import Any

from app.domain.aggregates import AggregateRoot
from app.domain.events import DomainEvent
from app.repositories.event_store import EventStore, Projector


class Command:
    """Marker for a write-side request. One handler is registered per subclass."""


class CommandHandlerNotFound(Exception):
    def __init__(self, command_name: str) -> None:
        super().__init__(f"no handler registered for {command_name}")
        self.command_name = command_name


Handler = Callable[[Command], Any]


class CommandBus:
    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store
        self._handlers: dict[type[Command], Handler] = {}

    @property
    def event_store(self) -> EventStore:
        return self._event_store

    def register(self, command_type: type[Command], handler: Handler) -> None:
        if not isinstance(command_type, type) or not issubclass(command_type, Command):
            raise TypeError("command_type must be a Command subclass")
        if command_type in self._handlers:
            raise ValueError(f"handler already registered for {command_type.__name__}")
        self._handlers[command_type] = handler

    def handler(self, command_type: type[Command]) -> Callable[[Handler], Handler]:
        def decorator(func: Handler) -> Handler:
            self.register(command_type, func)
            return func

        return decorator

    def dispatch(self, command: Command) -> Any:
        if not isinstance(command, Command):
            raise TypeError("dispatch requires a Command")
        found = self._handlers.get(type(command))
        if found is None:
            raise CommandHandlerNotFound(type(command).__name__)
        return found(command)

    def commit(
        self,
        aggregate: AggregateRoot,
        projector: Projector | None = None,
    ) -> list[DomainEvent]:
        """Append the aggregate's uncommitted events at ``expected_version``.

        A stale version raises ``ConcurrencyConflict`` and leaves the events
        uncommitted so the caller can reload. The projector runs inside the
        store transaction.
        """
        pending = aggregate.uncommitted_events()
        if not pending:
            return []
        self._event_store.append(
            aggregate.aggregate_id,
            aggregate.expected_version,
            pending,
            projector=projector,
        )
        aggregate.mark_committed()
        return pending
