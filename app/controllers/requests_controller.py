"""Help-request list, detail, submit, and the assignment actions on a request."""

from datetime import date, datetime, timezone
from uuid import UUID

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from app.commands.dtos import CancelRequestCommand, SubmitHelpRequestCommand
from app.commands.exemption_commands import CreateExemptionLinkCommand
from app.commands.request_commands import (
    ApproveAssignmentCommand,
    OverrideAssignmentCommand,
    RejectAssignmentCommand,
    RetriggerMatchCommand,
)
from app.domain.requests import parse_list_filters
from app.queries.request_queries import GetHelpRequestDetailsQuery, GetMyRequestsQuery, SearchHelpRequestsQuery
from app.queries.volunteer_queries import GetVolunteerDirectoryQuery
from app.repositories.matching import SqlMatchingReader
from app.repositories.request_read import RequestReadRepository
from app.repositories.requests import HelpRequestRepository
from app.security.auth import current_identity, require_auth

requests_bp = Blueprint("requests", __name__)


def _services():
    return current_app.extensions["kindbridge"]


def _page_arg() -> int:
    raw = request.args.get("page", "1")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _viewer():
    identity = current_identity()
    if identity is None:
        return None, redirect(url_for("auth.login_page"))
    return identity, None


def _today():
    return datetime.now(timezone.utc).date()


@requests_bp.get("/requests")
def search_page():
    identity, bounce = _viewer()
    if bounce is not None:
        return bounce
    services = _services()
    found = SearchHelpRequestsQuery(RequestReadRepository(services.engine)).execute(
        requester_id=None if identity.has_any("ADMIN") else identity.user_id,
        category=request.args.get("category", ""),
        city=request.args.get("city", ""),
        urgency=request.args.get("urgency", ""),
        status=request.args.get("status", ""),
        page=_page_arg(),
        today=date.today(),
    )
    return render_template("requests/list.html", search=found)


@requests_bp.get("/requests/<uuid:request_id>")
def detail_page(request_id: UUID):
    identity, bounce = _viewer()
    if bounce is not None:
        return bounce
    services = _services()
    is_admin = identity.has_any("ADMIN")
    details = GetHelpRequestDetailsQuery(RequestReadRepository(services.engine), services.encryptor).execute(
        request_id, is_admin=is_admin, today=date.today()
    )
    if details is None:
        return render_template("requests/missing.html"), 404
    if not is_admin and details.requester_id != identity.user_id:
        return render_template("admin/forbidden.html"), 403
    directory = ()
    if details.can_override:
        directory = GetVolunteerDirectoryQuery(services.engine, SqlMatchingReader()).execute(
            request_id, today=date.today()
        )
    return render_template("requests/detail.html", details=details, directory=directory, is_admin=is_admin)


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


@requests_bp.post("/api/requests/<uuid:request_id>/approve")
@require_auth("ADMIN")
def approve_request(request_id: UUID):
    identity = current_identity()
    command = ApproveAssignmentCommand.from_payload(request_id, identity.user_id, request.get_json(silent=True))
    _services().bus.dispatch(command)
    return jsonify(ok=True)


@requests_bp.post("/api/requests/<uuid:request_id>/reject")
@require_auth("ADMIN")
def reject_request(request_id: UUID):
    identity = current_identity()
    command = RejectAssignmentCommand.from_payload(request_id, identity.user_id, request.get_json(silent=True))
    _services().bus.dispatch(command)
    return jsonify(ok=True)


@requests_bp.post("/api/requests/<uuid:request_id>/override")
@require_auth("ADMIN")
def override_request(request_id: UUID):
    identity = current_identity()
    command = OverrideAssignmentCommand.from_payload(request_id, identity.user_id, request.get_json(silent=True))
    _services().bus.dispatch(command)
    return jsonify(ok=True)


@requests_bp.post("/api/requests/<uuid:request_id>/retrigger")
@require_auth("ADMIN")
def retrigger_request(request_id: UUID):
    identity = current_identity()
    command = RetriggerMatchCommand.from_payload(request_id, identity.user_id, request.get_json(silent=True))
    _services().bus.dispatch(command)
    return jsonify(ok=True)


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


@requests_bp.post("/api/exemptions")
@require_auth("ADMIN", "VOLUNTEER")
def create_exemption():
    identity = current_identity()
    command = CreateExemptionLinkCommand.from_payload(
        identity.user_id,
        identity.has_any("ADMIN"),
        request.get_json(silent=True),
    )
    _services().bus.dispatch(command)
    return jsonify(ok=True), 201
