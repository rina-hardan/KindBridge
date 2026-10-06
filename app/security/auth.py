from functools import wraps
from typing import Callable

from flask import current_app, g, jsonify, request

from app.security.tokens import Identity, InvalidToken, TokenService

ACCESS_COOKIE = "kb_access"


def _token_service() -> TokenService:
    return current_app.extensions["kindbridge"].tokens


def current_identity() -> Identity | None:
    if "identity" not in g:
        token = request.cookies.get(ACCESS_COOKIE)
        identity = None
        if token:
            try:
                identity = _token_service().verify(token)
            except InvalidToken:
                identity = None
        g.identity = identity
    return g.identity


def require_auth(*allowed_roles: str) -> Callable:
    """401 when not logged in; 403 when none of `allowed_roles` is in the JWT roles list."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            identity = current_identity()
            if identity is None:
                return jsonify(error="unauthorized", message="Login required"), 401
            if allowed_roles and not identity.has_any(*allowed_roles):
                return jsonify(error="forbidden", message="Your role cannot perform this action"), 403
            return view(*args, **kwargs)

        return wrapper

    return decorator
