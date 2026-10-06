"""Flask application factory."""

from flask import Flask, jsonify

from app.commands.bus import CommandBus, CommandHandlerNotFound
from app.config import Config, missing_production_settings
from app.domain.errors import DomainError
from app.repositories.event_store import InMemoryEventStore, SqlEventStore


def create_app(config_overrides: dict | None = None) -> Flask:
    """Build the KindBridge web app. Flask CLI calls this with no arguments."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    app.json.ensure_ascii = False

    if not app.config.get("TESTING"):
        missing = missing_production_settings(app.config)
        if missing:
            names = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variables: {names}")

    event_store = _build_event_store(app)
    command_bus = CommandBus(event_store)
    app.extensions["event_store"] = event_store
    app.extensions["command_bus"] = command_bus

    _register_error_handlers(app)
    return app


def _build_event_store(app: Flask):
    configured = app.config.get("EVENT_STORE")
    if configured is not None:
        return configured
    if app.config.get("TESTING"):
        return InMemoryEventStore()
    return SqlEventStore(app.config["DATABASE_URL"])


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(DomainError)
    def handle_domain_error(err: DomainError):
        return jsonify(error=err.code, message=err.message), err.status_code

    @app.errorhandler(CommandHandlerNotFound)
    def handle_missing_command(err: CommandHandlerNotFound):
        return jsonify(error="command_not_registered", message=str(err)), 500
