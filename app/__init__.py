"""Flask application factory."""

import time
from dataclasses import dataclass
from datetime import timedelta

from flask import Flask, g, jsonify, request
from sqlalchemy import Engine

from app.commands.bus import CommandBus
from app.commands.exemption_commands import ExemptionCommandHandlers
from app.commands.match_commands import FallbackRationale, MatchCommandHandlers, TemplateRationale, _LlmSafety
from app.commands.request_commands import RequestCommandHandlers
from app.commands.user_commands import Clock, UserCommandHandlers, utc_now
from app.config import Config
from app.controllers.account_controller import account_bp
from app.controllers.admin_controller import admin_bp
from app.controllers.auth_controller import auth_bp
from app.controllers.requests_controller import requests_bp
from app.controllers.errors import register_error_handlers
from app.i18n import current_lang, localize, skill_label, translate
from app.infrastructure.llm import chat_from_settings
from app.infrastructure.notification_service import (
    NoNotifications,
    Notifications,
    NotificationService,
    background_runner,
    inline_runner,
)
from app.infrastructure.notifier import GmailMcpNotifier, Notifier, NullNotifier, build_notifier
from app.infrastructure.vector_store import ChromaVolunteerVectorStore, NullResumeIndex, ResumeVectorStore
from app.infrastructure.web_search import TavilySearch
from app.projections.exemption_projector import ExemptionProjector
from app.projections.match_projector import MatchProjector
from app.projections.projectors import UserProjector
from app.repositories.db import make_engine
from app.repositories.event_store import SqlEventStore
from app.repositories.login_attempts import LoginAttemptRepository
from app.repositories.matching import SqlMatchingReader
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
    resumes: ResumeVectorStore
    encryptor: FieldEncryptor
    notifier: Notifier


def _build_notifications(config: Config, engine: Engine, notifier: Notifier) -> Notifications:
    if isinstance(notifier, NullNotifier):
        return NoNotifications()
    # Real Gmail sends can take seconds, so they run on a worker thread. Tests use an inline runner.
    run = background_runner() if isinstance(notifier, GmailMcpNotifier) else inline_runner
    return NotificationService(
        engine,
        notifier,
        UserRepository(),
        SqlMatchingReader(),
        admin_notify_email=config.admin_notify_email,
        base_url=config.app_base_url,
        run=run,
    )


def build_services(
    config: Config,
    engine: Engine | None = None,
    clock: Clock = utc_now,
    resumes: ResumeVectorStore | None = None,
    notifier: Notifier | None = None,
) -> Services:
    engine = engine or make_engine(config.database_url)
    event_store = SqlEventStore(config.database_url, engine=engine)
    if resumes is None:
        resumes = (
            NullResumeIndex()
            if config.testing
            else ChromaVolunteerVectorStore(config.chroma_host, config.chroma_port)
        )
    bus = CommandBus(event_store)
    encryptor = FieldEncryptor(config.encryption_key)
    notifier = notifier if notifier is not None else build_notifier(config)
    notifications = _build_notifications(config, engine, notifier)
    UserCommandHandlers(
        engine=engine,
        event_store=event_store,
        projector=UserProjector(),
        users=UserRepository(),
        attempts=LoginAttemptRepository(),
        hasher=PasswordHasher(config.bcrypt_rounds),
        encryptor=encryptor,
        lockout_max_failures=config.lockout_max_failures,
        lockout_window=timedelta(minutes=config.lockout_window_minutes),
        resumes=resumes,
        clock=clock,
        resume_retry_delays=() if config.testing else (0.5, 1.0),
    ).register_on(bus)
    chat = None if config.testing else chat_from_settings(
        config.llm_provider, config.llm_model, config.openai_api_key, config.ollama_base_url
    )
    MatchCommandHandlers(
        engine=engine,
        event_store=event_store,
        reader=SqlMatchingReader(),
        projector=MatchProjector(),
        resumes=resumes,
        clock=clock,
        web=TavilySearch(config.tavily_api_key) if config.tavily_api_key and not config.testing else None,
        safety=_LlmSafety(chat) if chat is not None else None,
        rationale=FallbackRationale(chat) if chat is not None else TemplateRationale(),
        sleep=(lambda _seconds: None) if config.testing else time.sleep,
        notifications=notifications,
    ).register_on(bus)
    RequestCommandHandlers(
        engine=engine,
        event_store=event_store,
        reader=SqlMatchingReader(),
        projector=MatchProjector(),
        users=UserRepository(),
        clock=clock,
        notifications=notifications,
    ).register_on(bus)
    ExemptionCommandHandlers(engine=engine, event_store=event_store, projector=ExemptionProjector()).register_on(bus)
    return Services(
        config=config,
        engine=engine,
        event_store=event_store,
        bus=bus,
        tokens=TokenService(config.jwt_secret, config.jwt_ttl_minutes),
        resumes=resumes,
        encryptor=encryptor,
        notifier=notifier,
    )


def create_app(
    config: Config | None = None,
    engine: Engine | None = None,
    clock: Clock = utc_now,
    resumes: ResumeVectorStore | None = None,
    notifier: Notifier | None = None,
) -> Flask:
    """Build the KindBridge web app. Flask CLI calls this with no arguments."""
    config = config or Config.from_env()
    app = Flask(__name__)
    app.config["TESTING"] = config.testing
    app.json.ensure_ascii = False
    app.extensions["kindbridge"] = build_services(config, engine, clock, resumes, notifier)

    register_error_handlers(app)
    _install_csrf(app, config)
    _install_text_direction(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(requests_bp)
    return app


def _install_csrf(app: Flask, config: Config) -> None:
    """Double-submit cookie: unsafe requests must echo the kb_csrf cookie in the X-CSRF-Token header."""

    @app.before_request
    def enforce_csrf():
        if request.method in UNSAFE_METHODS:
            if not tokens_match(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)):
                return jsonify(error="csrf_failed", message=localize("Missing or invalid CSRF token")), 403
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
        from app.security.auth import current_identity

        lang = current_lang()
        return {
            "lang": lang,
            "text_dir": "rtl" if lang == "he" else "ltr",
            "identity": current_identity(),
            "t": translate,
            "skill_label": skill_label,
            "kb_text": {
                "genericError": translate("js.generic_error"),
                "networkError": translate("js.network_error"),
            },
        }
