"""Domain failures. Controllers map these to HTTP; they do not decide the status themselves."""


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__


class ValidationError(DomainError):
    status_code = 400
    code = "validation_error"

    def __init__(self, errors: dict[str, str] | str) -> None:
        if isinstance(errors, dict):
            super().__init__("Invalid input")
            self.field_errors = errors
        else:
            super().__init__(errors)
            self.field_errors = {}


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


class InvalidCredentials(Unauthorized):
    code = "invalid_credentials"


class AccountLocked(DomainError):
    status_code = 429
    code = "account_locked"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many failed login attempts")
        self.retry_after_seconds = retry_after_seconds


class EmailAlreadyRegistered(DomainError):
    status_code = 409
    code = "email_taken"


class AdminAlreadyExists(DomainError):
    status_code = 409
    code = "admin_exists"


class ProfileAlreadyEnabled(DomainError):
    """A volunteer profile already exists for this person."""

    status_code = 409
    code = "profile_exists"
