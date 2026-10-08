"""Read models for help-request search and detail. Queries never write."""

import json
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Engine

from app.repositories.tables import event_store, help_requests, task_assignments, users, volunteer_profiles

REQUEST_STATUSES = ("PENDING_REVIEW", "MATCH_PROPOSED", "NO_MATCH", "ASSIGNED", "COMPLETED", "CANCELLED")
URGENCIES = ("EMERGENCY", "HIGH", "NORMAL", "LOW")
VISIBLE_ASSIGNMENT_STATUSES = ("PROPOSED", "ASSIGNED")


@dataclass(frozen=True)
class RequestListRow:
    request_id: uuid.UUID
    requester_id: uuid.UUID
    requester_name: str
    category: str
    city: str
    urgency: str
    status: str
    preferred_date: date | None


@dataclass(frozen=True)
class RequestSearchSnapshot:
    total: int
    page: int
    page_count: int
    rows: tuple[RequestListRow, ...]
    categories: tuple[str, ...]
    cities: tuple[str, ...]


@dataclass(frozen=True)
class AssignmentRow:
    assignment_id: uuid.UUID
    volunteer_id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    city: str
    score: Decimal | None
    rationale: str | None
    rank: int | None
    status: str


@dataclass(frozen=True)
class RequestDetailRow:
    request_id: uuid.UUID
    requester_id: uuid.UUID
    requester_name: str
    phone_encrypted: str
    city: str
    address: str
    category: str
    urgency: str
    description: str
    status: str
    preferred_date: date | None
    assignments: tuple[AssignmentRow, ...]
    rejection_summary: dict[str, int]


class RequestReadRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def search(
        self,
        *,
        requester_id: uuid.UUID | None,
        category: str,
        city: str,
        urgency: str,
        status: str,
        page: int,
        page_size: int,
    ) -> RequestSearchSnapshot:
        with self._engine.connect() as conn:
            scope = self._scope(requester_id, category, city, urgency, status)
            count_stmt = select(func.count()).select_from(help_requests)
            list_stmt = (
                select(
                    help_requests.c.id,
                    help_requests.c.requester_id,
                    users.c.full_name,
                    help_requests.c.category,
                    help_requests.c.city,
                    help_requests.c.urgency,
                    help_requests.c.status,
                    help_requests.c.preferred_date,
                )
                .join(users, users.c.id == help_requests.c.requester_id)
                .order_by(help_requests.c.created_at.desc())
            )
            if scope:
                count_stmt = count_stmt.where(*scope)
                list_stmt = list_stmt.where(*scope)
            total = int(conn.execute(count_stmt).scalar_one())
            page_count = max(1, math.ceil(total / page_size)) if total else 1
            page = min(max(page, 1), page_count)
            rows = conn.execute(list_stmt.offset((page - 1) * page_size).limit(page_size))
            return RequestSearchSnapshot(
                total=total,
                page=page,
                page_count=page_count,
                rows=tuple(_list_row(row) for row in rows),
                categories=self._distinct(conn, help_requests.c.category, requester_id),
                cities=self._distinct(conn, help_requests.c.city, requester_id),
            )

    def detail(self, request_id: uuid.UUID) -> RequestDetailRow | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    help_requests.c.id,
                    help_requests.c.requester_id,
                    users.c.full_name,
                    users.c.phone,
                    help_requests.c.city,
                    help_requests.c.address,
                    help_requests.c.category,
                    help_requests.c.urgency,
                    help_requests.c.description,
                    help_requests.c.status,
                    help_requests.c.preferred_date,
                )
                .join(users, users.c.id == help_requests.c.requester_id)
                .where(help_requests.c.id == request_id)
            ).first()
            if row is None:
                return None
            return RequestDetailRow(
                request_id=_uuid(row.id),
                requester_id=_uuid(row.requester_id),
                requester_name=row.full_name,
                phone_encrypted=row.phone,
                city=row.city,
                address=row.address,
                category=row.category,
                urgency=row.urgency,
                description=row.description,
                status=row.status,
                preferred_date=_date(row.preferred_date),
                assignments=self._assignments(conn, request_id),
                rejection_summary=self._rejection_summary(conn, request_id) if row.status == "NO_MATCH" else {},
            )

    def _scope(self, requester_id, category: str, city: str, urgency: str, status: str) -> list:
        clauses = []
        if requester_id is not None:
            clauses.append(help_requests.c.requester_id == requester_id)
        if category:
            clauses.append(help_requests.c.category == category)
        if city:
            clauses.append(help_requests.c.city == city)
        if urgency in URGENCIES:
            clauses.append(help_requests.c.urgency == urgency)
        if status in REQUEST_STATUSES:
            clauses.append(help_requests.c.status == status)
        return clauses

    def _distinct(self, conn: Connection, column, requester_id) -> tuple[str, ...]:
        stmt = select(column).distinct().order_by(column)
        if requester_id is not None:
            stmt = stmt.where(help_requests.c.requester_id == requester_id)
        return tuple(str(value) for (value,) in conn.execute(stmt) if value)

    def _assignments(self, conn: Connection, request_id: uuid.UUID) -> tuple[AssignmentRow, ...]:
        rows = conn.execute(
            select(
                task_assignments.c.id,
                task_assignments.c.volunteer_id,
                task_assignments.c.ai_score,
                task_assignments.c.ai_rationale,
                task_assignments.c.rank_in_batch,
                task_assignments.c.status,
                users.c.id.label("user_id"),
                users.c.full_name,
                volunteer_profiles.c.primary_city,
            )
            .select_from(
                task_assignments.join(
                    volunteer_profiles, volunteer_profiles.c.id == task_assignments.c.volunteer_id
                ).join(users, users.c.id == volunteer_profiles.c.user_id)
            )
            .where(task_assignments.c.request_id == request_id)
            .where(task_assignments.c.status.in_(VISIBLE_ASSIGNMENT_STATUSES))
            .order_by(task_assignments.c.rank_in_batch)
        )
        return tuple(
            AssignmentRow(
                assignment_id=_uuid(row.id),
                volunteer_id=_uuid(row.volunteer_id),
                user_id=_uuid(row.user_id),
                full_name=row.full_name,
                city=row.primary_city,
                score=None if row.ai_score is None else Decimal(str(row.ai_score)),
                rationale=row.ai_rationale,
                rank=None if row.rank_in_batch is None else int(row.rank_in_batch),
                status=row.status,
            )
            for row in rows
        )

    def _rejection_summary(self, conn: Connection, request_id: uuid.UUID) -> dict[str, int]:
        raw = conn.execute(
            select(event_store.c.payload_json)
            .where(event_store.c.aggregate_id == request_id)
            .where(event_store.c.event_type == "NoMatchFound")
            .order_by(event_store.c.version.desc())
            .limit(1)
        ).scalar()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        summary = payload.get("rejection_summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict):
            return {}
        counts: dict[str, int] = {}
        for key, value in summary.items():
            try:
                counts[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return counts


def _list_row(row) -> RequestListRow:
    return RequestListRow(
        request_id=_uuid(row.id),
        requester_id=_uuid(row.requester_id),
        requester_name=row.full_name,
        category=row.category,
        city=row.city,
        urgency=row.urgency,
        status=row.status,
        preferred_date=_date(row.preferred_date),
    )


def _uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
