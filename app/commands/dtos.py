import re
from dataclasses import dataclass
from typing import Any

from app.commands.bus import Command
from app.domain.errors import ValidationError
from app.security.passwords import password_problem

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\- ]{6,19}$")
BASE_FREQUENCIES = ("WEEKLY", "BIWEEKLY", "MONTHLY", "ON_DEMAND")
FORBIDDEN_REGISTRATION_KEYS = ("is_admin", "role", "roles")


@dataclass(frozen=True)
class VolunteerProfileInput:
    primary_city: str
    has_vehicle: bool
    skills: tuple[str, ...]
    experience: str
    base_frequency: str
    max_active_tasks: int
    max_parallel_tasks: int


@dataclass(frozen=True)
class RegisterUserCommand(Command):
    email: str
    password: str
    full_name: str
    phone: str
    volunteer_profile: VolunteerProfileInput | None = None

    @classmethod
    def from_payload(cls, data: Any) -> "RegisterUserCommand":
        if not isinstance(data, dict):
            raise ValidationError({"body": "Expected a JSON object"})
        errors: dict[str, str] = {}
        for key in FORBIDDEN_REGISTRATION_KEYS:
            if key in data:
                errors[key] = "Roles cannot be chosen at registration"

        email = _email(data, errors)
        password = _password(data, errors)
        full_name = _text(data, "full_name", errors, max_len=200)
        phone = _phone(data, errors)

        volunteer = None
        raw_volunteer = data.get("volunteer_profile")
        if raw_volunteer is not None:
            volunteer = _volunteer_profile(raw_volunteer, errors)

        if errors:
            raise ValidationError(errors)
        return cls(email=email, password=password, full_name=full_name, phone=phone, volunteer_profile=volunteer)


@dataclass(frozen=True)
class LoginCommand(Command):
    email: str
    password: str

    @classmethod
    def from_payload(cls, data: Any) -> "LoginCommand":
        if not isinstance(data, dict):
            raise ValidationError({"body": "Expected a JSON object"})
        email = data.get("email")
        password = data.get("password")
        errors = {}
        if not isinstance(email, str) or not email.strip():
            errors["email"] = "Email is required"
        if not isinstance(password, str) or not password:
            errors["password"] = "Password is required"
        if errors:
            raise ValidationError(errors)
        return cls(email=normalize_email(email), password=password)


@dataclass(frozen=True)
class BootstrapAdminCommand(Command):
    email: str
    password: str
    full_name: str
    phone: str

    @classmethod
    def create(cls, email: str, password: str, full_name: str, phone: str = "") -> "BootstrapAdminCommand":
        data = {"email": email, "password": password, "full_name": full_name, "phone": phone}
        errors: dict[str, str] = {}
        email = _email(data, errors)
        password = _password(data, errors)
        full_name = _text(data, "full_name", errors, max_len=200)
        phone = _phone(data, errors) if phone else ""
        if errors:
            raise ValidationError(errors)
        return cls(email=email, password=password, full_name=full_name, phone=phone)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _email(data: dict, errors: dict[str, str]) -> str:
    raw = data.get("email")
    if not isinstance(raw, str):
        errors["email"] = "Email is required"
        return ""
    email = normalize_email(raw)
    if len(email) > 255 or not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address"
    return email


def _password(data: dict, errors: dict[str, str]) -> str:
    raw = data.get("password")
    if not isinstance(raw, str):
        errors["password"] = "Password is required"
        return ""
    problem = password_problem(raw)
    if problem:
        errors["password"] = problem
    return raw


def _text(data: dict, field: str, errors: dict[str, str], max_len: int) -> str:
    raw = data.get(field)
    if not isinstance(raw, str) or not raw.strip():
        errors[field] = "This field is required"
        return ""
    value = raw.strip()
    if len(value) > max_len:
        errors[field] = f"Must be at most {max_len} characters"
    return value


def _phone(data: dict, errors: dict[str, str]) -> str:
    raw = data.get("phone")
    if not isinstance(raw, str) or not PHONE_RE.match(raw.strip()):
        errors["phone"] = "Enter a valid phone number"
        return ""
    return raw.strip()


def _int_in_range(raw: Any, field: str, default: int, low: int, high: int, errors: dict[str, str]) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or not low <= raw <= high:
        errors[field] = f"Must be a whole number between {low} and {high}"
        return default
    return raw


def _volunteer_profile(raw: Any, errors: dict[str, str]) -> VolunteerProfileInput | None:
    prefix = "volunteer_profile."
    if not isinstance(raw, dict):
        errors["volunteer_profile"] = "Expected an object"
        return None

    local: dict[str, str] = {}
    city = _text(raw, "primary_city", local, max_len=100)
    experience = _text(raw, "experience", local, max_len=10000)

    skills_raw = raw.get("skills", [])
    if not isinstance(skills_raw, list) or not all(isinstance(s, str) and s.strip() for s in skills_raw):
        local["skills"] = "Skills must be a list of non-empty strings"
        skills: tuple[str, ...] = ()
    else:
        skills = tuple(dict.fromkeys(s.strip().lower() for s in skills_raw))
        if len(skills) > 30 or any(len(s) > 50 for s in skills):
            local["skills"] = "At most 30 skills, each up to 50 characters"

    has_vehicle = raw.get("has_vehicle", False)
    if not isinstance(has_vehicle, bool):
        local["has_vehicle"] = "Must be true or false"
        has_vehicle = False

    frequency = raw.get("base_frequency")
    if frequency not in BASE_FREQUENCIES:
        local["base_frequency"] = f"Must be one of {', '.join(BASE_FREQUENCIES)}"

    max_active = _int_in_range(raw.get("max_active_tasks"), "max_active_tasks", 1, 1, 10, local)
    max_parallel = _int_in_range(raw.get("max_parallel_tasks"), "max_parallel_tasks", 2, 0, 10, local)

    errors.update({prefix + k: v for k, v in local.items()})
    if local:
        return None
    return VolunteerProfileInput(
        primary_city=city,
        has_vehicle=has_vehicle,
        skills=skills,
        experience=experience,
        base_frequency=frequency,
        max_active_tasks=max_active,
        max_parallel_tasks=max_parallel,
    )
