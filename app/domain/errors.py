"""Domain failures. Controllers map these to HTTP; they do not decide the status themselves."""


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(DomainError):
    status_code = 400
    code = "validation_error"


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class ConcurrencyConflict(DomainError):
    """Stream version does not match the caller's expected version. HTTP 409."""

    status_code = 409
    code = "conflict"
