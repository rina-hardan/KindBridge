from datetime import datetime, timezone
from uuid import UUID

from flask import Blueprint, current_app, jsonify, request

from app.commands.dtos import CancelRequestCommand, SubmitHelpRequestCommand
from app.domain.requests import parse_list_filters
from app.queries.request_queries import GetMyRequestsQuery
from app.repositories.requests import HelpRequestRepository
from app.security.auth import current_identity, require_auth

requests_bp = Blueprint("requests", __name__)


def _services():
    return current_app.extensions["kindbridge"]


def _today():
    return datetime.now(timezone.utc).date()


@requests_bp.get("/api/me/requests")
@require_auth("REQUESTER")
def my_requests():
    identity = current_identity()
    filters = parse_list_filters(request.args.to_dict(flat=True))
    page = GetMyRequestsQuery(_services().engine, HelpRequestRepository()).execute(identity.user_id, filters, _today())
    return jsonify(
        items=[
            {
                "id": str(item.id),
                "category": item.category,
                "city": item.city,
                "urgency": item.urgency,
                "status": item.status,
                "preferred_date": item.preferred_date,
                "description": item.description,
                "created_at": item.created_at,
                "overdue": item.overdue,
                "can_cancel": item.can_cancel,
            }
            for item in page.items
        ],
        page=page.page,
        page_size=page.page_size,
        total=page.total,
    )


@requests_bp.post("/api/requests")
@require_auth("REQUESTER")
def submit_request():
    identity = current_identity()
    command = SubmitHelpRequestCommand.from_payload(identity.user_id, request.get_json(silent=True))
    result = _services().bus.dispatch(command)
    return jsonify(id=str(result.request_id)), 201


@requests_bp.post("/api/requests/<uuid:request_id>/cancel")
@require_auth()
def cancel_request(request_id: UUID):
    identity = current_identity()
    command = CancelRequestCommand.from_payload(
        request_id,
        identity.user_id,
        identity.has_any("ADMIN"),
        request.get_json(silent=True),
    )
    _services().bus.dispatch(command)
    return jsonify(ok=True)
