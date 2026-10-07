"""Read help-request projections. Queries do not append events."""

import uuid
from datetime import date, datetime

from sqlalchemy import Connection, select

from app.domain.bilingual import city_key
from app.repositories.tables import help_requests


class HelpRequestRepository:
    def list_for_requester(self, conn: Connection, requester_id: uuid.UUID) -> list[dict]:
        stmt = (
            select(
                help_requests.c.id,
                help_requests.c.category,
                help_requests.c.city,
                help_requests.c.urgency,
                help_requests.c.status,
                help_requests.c.preferred_date,
                help_requests.c.description,
                help_requests.c.created_at,
            )
            .where(help_requests.c.requester_id == requester_id)
            .order_by(help_requests.c.created_at.desc(), help_requests.c.id)
        )
        return [dict(row) for row in conn.execute(stmt).mappings()]

    def matching_city(self, rows: list[dict], city: str | None) -> list[dict]:
        if not city:
            return rows
        wanted = city_key(city)
        return [row for row in rows if city_key(str(row["city"])) == wanted]


def as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
