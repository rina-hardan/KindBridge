from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from app.commands.dtos import (
    EnableVolunteerProfileCommand,
    UpdateAccountDetailsCommand,
    UpdateRequesterProfileCommand,
)
from app.domain.requests import ALL_STATUSES, CATEGORIES, RESOURCE_TYPES, URGENCIES
from app.i18n import localize
from app.queries.account_queries import AccountView, GetMyAccountQuery
from app.queries.request_queries import GetRequesterDefaultsQuery
from app.repositories.users import UserRepository
from app.security.auth import current_identity, require_auth, set_access_cookie

account_bp = Blueprint("account", __name__)


def _services():
    return current_app.extensions["kindbridge"]


def _account_query() -> GetMyAccountQuery:
    services = _services()
    return GetMyAccountQuery(services.engine, UserRepository(), services.encryptor)


def _account_or_login():
    identity = current_identity()
    if identity is None:
        return None, redirect(url_for("auth.login_page"))
    account = _account_query().execute(identity.user_id)
    if account is None:
        return None, redirect(url_for("auth.login_page"))
    return account, None


def _redirect_admin(account: AccountView):
    if account.is_admin:
        return redirect(url_for("account.profile_page"))
    return None


@account_bp.get("/me")
def profile_page():
    account, bounce = _account_or_login()
    if bounce is not None:
        return bounce
    return render_template("account/profile.html", account=account)


@account_bp.get("/api/me/account")
@require_auth()
def account_api():
    identity = current_identity()
    account = _account_query().execute(identity.user_id)
    if account is None:
        return jsonify(error="not_found", message=localize("User not found")), 404
    return jsonify(
        email=account.email,
        full_name=account.full_name,
        phone=account.phone,
        city=account.city,
        home_address=account.home_address,
        is_admin=account.is_admin,
        has_requester_profile=account.has_requester_profile,
        has_volunteer_profile=account.has_volunteer_profile,
    )


@account_bp.post("/api/me/account")
@require_auth()
def update_account():
    identity = current_identity()
    command = UpdateAccountDetailsCommand.from_payload(identity.user_id, request.get_json(silent=True))
    _services().bus.dispatch(command)
    return jsonify(ok=True)


@account_bp.get("/me/requester")
def requester_join_page():
    account, bounce = _account_or_login()
    if bounce is not None:
        return bounce
    admin = _redirect_admin(account)
    if admin is not None:
        return admin
    if account.has_requester_profile:
        return redirect(url_for("account.requests_page"))
    return render_template("account/requester_join.html", account=account)


@account_bp.post("/api/me/requester")
@require_auth("REQUESTER")
def update_requester():
    identity = current_identity()
    command = UpdateRequesterProfileCommand.from_payload(identity.user_id, request.get_json(silent=True))
    result = _services().bus.dispatch(command)
    return jsonify(profile_id=str(result.profile_id)), 201 if result.created else 200


@account_bp.get("/me/volunteer")
def volunteer_join_page():
    account, bounce = _account_or_login()
    if bounce is not None:
        return bounce
    admin = _redirect_admin(account)
    if admin is not None:
        return admin
    if account.has_volunteer_profile:
        return redirect(url_for("account.tasks_page"))
    return render_template("account/volunteer_join.html", account=account)


@account_bp.post("/api/me/volunteer")
@require_auth("REQUESTER")
def enable_volunteer():
    identity = current_identity()
    command = EnableVolunteerProfileCommand.from_payload(identity.user_id, request.get_json(silent=True))
    result = _services().bus.dispatch(command)
    services = _services()
    token = services.tokens.issue(identity.user_id, result.roles)
    response = jsonify(profile_id=str(result.profile_id), roles=result.roles)
    set_access_cookie(
        response,
        token,
        max_age=int(services.tokens.ttl.total_seconds()),
        secure=services.config.cookie_secure,
    )
    return response, 201


@account_bp.get("/me/requests")
def requests_page():
    account, bounce = _account_or_login()
    if bounce is not None:
        return bounce
    admin = _redirect_admin(account)
    if admin is not None:
        return admin
    if not account.has_requester_profile:
        return redirect(url_for("account.requester_join_page"))
    return render_template(
        "account/requests_home.html",
        categories=CATEGORIES,
        urgencies=URGENCIES,
        statuses=ALL_STATUSES,
    )


@account_bp.get("/me/requests/new")
def new_request_page():
    account, bounce = _account_or_login()
    if bounce is not None:
        return bounce
    admin = _redirect_admin(account)
    if admin is not None:
        return admin
    if not account.has_requester_profile:
        return redirect(url_for("account.requester_join_page"))
    defaults = GetRequesterDefaultsQuery(_services().engine, UserRepository()).execute(account.user_id)
    if defaults is None:
        return redirect(url_for("account.requester_join_page"))
    return render_template(
        "account/request_new.html",
        defaults=defaults,
        categories=CATEGORIES,
        urgencies=URGENCIES,
        resource_types=RESOURCE_TYPES,
    )


@account_bp.get("/me/tasks")
def tasks_page():
    account, bounce = _account_or_login()
    if bounce is not None:
        return bounce
    admin = _redirect_admin(account)
    if admin is not None:
        return admin
    if not account.has_volunteer_profile:
        return redirect(url_for("account.volunteer_join_page"))
    return render_template("account/tasks_home.html")
