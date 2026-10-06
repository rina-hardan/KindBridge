"""Flask application factory."""

from dataclasses import dataclass
from datetime import timedelta

from flask import Flask, g, jsonify, request
from sqlalchemy import Engine

from app.commands.bus import CommandBus
from app.commands.user_commands import Clock, UserCommandHandlers, utc_now
from app.config import Config
from app.controllers.auth_controller import auth_bp
from app.controllers.errors import register_error_handlers
from app.projections.projectors import UserProjector
from app.repositories.db import make_engine
from app.repositories.event_store import SqlEventStore
from app.repositories.login_attempts import LoginAttemptRepository
from app.repositories.users import UserRepository
from app.security.csrf import CSRF_COOKIE, CSRF_HEADER, UNSAFE_METHODS, new_csrf_token, tokens_match
from app.security.encryption import FieldEncryptor
from app.security.passwords import PasswordHasher
from app.security.tokens import TokenService


@dataclass
class Services:
    config: Config
    engine: Engine
    event_store: SqlEventStore
    bus: CommandBus
    tokens: TokenService


def build_services(config: Config, engine: Engine | None = None, clock: Clock = utc_now) -> Services:
    engine = engine or make_engine(config.database_url)
    event_store = SqlEventStore(config.database_url, engine=engine)
    bus = CommandBus(event_store)
    UserCommandHandlers(
        engine=engine,
        event_store=event_store,
        projector=UserProjector(),
        users=UserRepository(),
        attempts=LoginAttemptRepository(),
        hasher=PasswordHasher(config.bcrypt_rounds),
        encryptor=FieldEncryptor(config.encryption_key),
        lockout_max_failures=config.lockout_max_failures,
        lockout_window=timedelta(minutes=config.lockout_window_minutes),
        clock=clock,
    ).register_on(bus)
    return Services(
        config=config,
        engine=engine,
        event_store=event_store,
        bus=bus,
        tokens=TokenService(config.jwt_secret, config.jwt_ttl_minutes),
    )


def create_app(config: Config | None = None, engine: Engine | None = None, clock: Clock = utc_now) -> Flask:
    """Build the KindBridge web app. Flask CLI calls this with no arguments."""
    config = config or Config.from_env()
    app = Flask(__name__)
    app.config["TESTING"] = config.testing
    app.json.ensure_ascii = False
    app.extensions["kindbridge"] = build_services(config, engine, clock)

    register_error_handlers(app)
    _install_csrf(app, config)
    _install_text_direction(app)
    app.register_blueprint(auth_bp)
    return app


def _install_csrf(app: Flask, config: Config) -> None:
    """Double-submit cookie: unsafe requests must echo the kb_csrf cookie in the X-CSRF-Token header."""

    @app.before_request
    def enforce_csrf():
        if request.method in UNSAFE_METHODS:
            if not tokens_match(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)):
                return jsonify(error="csrf_failed", message="Missing or invalid CSRF token"), 403
        return None

    @app.after_request
    def issue_csrf_cookie(response):
        token = g.get("new_csrf_token")
        if token is None and CSRF_COOKIE not in request.cookies:
            token = new_csrf_token()
        if token is not None:
            response.set_cookie(
                CSRF_COOKIE,
                token,
                httponly=False,
                secure=config.cookie_secure,
                samesite="Lax",
                path="/",
            )
        return response


def _install_text_direction(app: Flask) -> None:
    @app.context_processor
    def text_direction():
        lang = request.accept_languages.best_match(["he", "en"]) or "en"
        return {"lang": lang, "text_dir": "rtl" if lang == "he" else "ltr"}
