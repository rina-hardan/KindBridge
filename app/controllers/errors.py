from flask import Flask, jsonify

from app.commands.bus import CommandHandlerNotFound
from app.domain import errors
from app.i18n import localize


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(errors.DomainError)
    def handle_domain_error(exc: errors.DomainError):
        body = {"error": exc.code, "message": localize(exc.message)}
        if isinstance(exc, errors.ValidationError):
            body["fields"] = {field: localize(message) for field, message in exc.field_errors.items()}
        response = jsonify(body)
        response.status_code = exc.status_code
        if isinstance(exc, errors.AccountLocked):
            response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response

    @app.errorhandler(CommandHandlerNotFound)
    def handle_missing_command(exc: CommandHandlerNotFound):
        return jsonify(error="command_not_registered", message=str(exc)), 500
