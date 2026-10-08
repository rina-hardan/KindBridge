"""Admin volunteer picker: everyone who still passes the hard filter for one request."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Engine

from app.domain.matching import rejection_reason
from app.repositories.matching import SqlMatchingReader


@dataclass(frozen=True)
class DirectoryVolunteer:
    profile_id: str
    user_id: str
    full_name: str
    city: str


class GetVolunteerDirectoryQuery:
    def __init__(self, engine: Engine, reader: SqlMatchingReader) -> None:
        self._engine = engine
        self._reader = reader

    def execute(self, request_id: UUID, *, today: date | None = None) -> tuple[DirectoryVolunteer, ...]:
        today = today or date.today()
        with self._engine.connect() as conn:
            named = self._reader.load_named_volunteers(conn)
            view = self._reader.load_request(conn, request_id)
            if view is None or not named:
                return ()
            facts = self._reader.facts_for(conn, view, list(named), today)
        chosen = [
            DirectoryVolunteer(
                profile_id=str(item.volunteer.profile_id),
                user_id=str(item.volunteer.user_id),
                full_name=item.full_name,
                city=item.volunteer.primary_city,
            )
            for item in named.values()
            if rejection_reason(item.volunteer, facts) is None
        ]
        chosen.sort(key=lambda item: item.full_name)
        return tuple(chosen)
