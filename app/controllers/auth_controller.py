from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, url_for

from app.commands.dtos import LoginCommand, RegisterUserCommand
from app.security.auth import ACCESS_COOKIE, current_identity, require_auth, set_access_cookie
from app.security.csrf import CSRF_COOKIE, new_csrf_token

auth_bp = Blueprint("auth", __name__)


def _services():
    return current_app.extensions["kindbridge"]


def _session_response(user_id, roles, *, status: int = 200, rotate_csrf: bool = False, **extra):
    services = _services()
    token = services.tokens.issue(user_id, roles)
    response = jsonify(user_id=str(user_id), roles=roles, **extra)
    response.status_code = status
    set_access_cookie(
        response,
        token,
        max_age=int(services.tokens.ttl.total_seconds()),
        secure=services.config.cookie_secure,
    )
    if rotate_csrf:
        g.new_csrf_token = new_csrf_token()
    return response


@auth_bp.get("/login")
def login_page():
    if current_identity() is not None:
        return redirect(url_for("account.profile_page"))
    return render_template("auth/login.html")


@auth_bp.get("/register")
def register_page():
    if current_identity() is not None:
        return redirect(url_for("account.profile_page"))
    return render_template("auth/register.html")


@auth_bp.get("/api/auth/csrf")
def csrf_token():
    """For API clients; browsers also receive the cookie on any page load."""
    token = request.cookies.get(CSRF_COOKIE) or g.setdefault("new_csrf_token", new_csrf_token())
    return jsonify(csrf_token=token)


@auth_bp.post("/api/auth/register")
def register():
    command = RegisterUserCommand.from_payload(request.get_json(silent=True))
    result = _services().bus.dispatch(command)
    return _session_response(result.user_id, result.roles, status=201)


@auth_bp.post("/api/auth/login")
def login():
    command = LoginCommand.from_payload(request.get_json(silent=True))
    result = _services().bus.dispatch(command)
    return _session_response(result.user_id, result.roles, full_name=result.full_name, rotate_csrf=True)


@auth_bp.post("/api/auth/logout")
def logout():
    response = jsonify(ok=True)
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=_services().config.cookie_secure, samesite="Lax")
    g.new_csrf_token = new_csrf_token()
    return response


@auth_bp.get("/api/auth/me")
@require_auth()
def me():
    identity = current_identity()
    return jsonify(user_id=str(identity.user_id), roles=list(identity.roles))
