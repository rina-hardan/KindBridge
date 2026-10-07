import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.commands.bus import Command
from app.domain.bilingual import split_skills
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
    city: str
    home_address: str

    @classmethod
    def from_payload(cls, data: Any) -> "RegisterUserCommand":
        if not isinstance(data, dict):
            raise ValidationError({"body": "Expected a JSON object"})
        errors: dict[str, str] = {}
        for key in FORBIDDEN_REGISTRATION_KEYS:
            if key in data:
                errors[key] = "Roles cannot be chosen at registration"
        if "volunteer_profile" in data:
            errors["volunteer_profile"] = "Volunteer details are saved from your account page"

        email = _email(data, errors)
        password = _password(data, errors)
        full_name = _text(data, "full_name", errors, max_len=200)
        phone = _phone(data, errors)
        city = _text(data, "city", errors, max_len=100)
        home_address = _text(data, "home_address", errors, max_len=255)

        if errors:
            raise ValidationError(errors)
        return cls(
            email=email,
            password=password,
            full_name=full_name,
            phone=phone,
            city=city,
            home_address=home_address,
        )


@dataclass(frozen=True)
class UpdateAccountDetailsCommand(Command):
    user_id: UUID
    full_name: str
    phone: str
    city: str | None
    home_address: str | None

    @classmethod
    def from_payload(cls, user_id: UUID, data: Any) -> "UpdateAccountDetailsCommand":
        if not isinstance(data, dict):
            raise ValidationError({"body": "Expected a JSON object"})
        errors: dict[str, str] = {}
        if "email" in data:
            errors["email"] = "Email cannot be changed"
        full_name = _text(data, "full_name", errors, max_len=200)
        phone = _phone(data, errors)
        city = _optional_text(data, "city", errors, max_len=100)
        home_address = _optional_text(data, "home_address", errors, max_len=255)
        if errors:
            raise ValidationError(errors)
        return cls(user_id=user_id, full_name=full_name, phone=phone, city=city, home_address=home_address)


@dataclass(frozen=True)
class UpdateRequesterProfileCommand(Command):
    user_id: UUID
    default_city: str
    default_address: str
    accessibility_notes: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None

    @classmethod
    def from_payload(cls, user_id: UUID, data: Any) -> "UpdateRequesterProfileCommand":
        if not isinstance(data, dict):
            raise ValidationError({"body": "Expected a JSON object"})
        errors: dict[str, str] = {}
        default_city = _text(data, "default_city", errors, max_len=100)
        default_address = _text(data, "default_address", errors, max_len=255)
        accessibility_notes = _optional_text(data, "accessibility_notes", errors, max_len=500)
        emergency_contact_name = _optional_text(data, "emergency_contact_name", errors, max_len=200)
        emergency_contact_phone = _optional_phone(data, "emergency_contact_phone", errors)
        if errors:
            raise ValidationError(errors)
        return cls(
            user_id=user_id,
            default_city=default_city,
            default_address=default_address,
            accessibility_notes=accessibility_notes,
            emergency_contact_name=emergency_contact_name,
            emergency_contact_phone=emergency_contact_phone,
        )


@dataclass(frozen=True)
class EnableVolunteerProfileCommand(Command):
    user_id: UUID
    profile: VolunteerProfileInput

    @classmethod
    def from_payload(cls, user_id: UUID, data: Any) -> "EnableVolunteerProfileCommand":
        if not isinstance(data, dict):
            raise ValidationError({"body": "Expected a JSON object"})
        errors: dict[str, str] = {}
        profile = _volunteer_profile(data, errors, prefix="")
        if errors:
            raise ValidationError(errors)
        assert profile is not None
        return cls(user_id=user_id, profile=profile)


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


def _optional_text(data: dict, field: str, errors: dict[str, str], max_len: int) -> str | None:
    if field not in data or data.get(field) is None:
        return None
    raw = data.get(field)
    if not isinstance(raw, str):
        errors[field] = "Must be text"
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > max_len:
        errors[field] = f"Must be at most {max_len} characters"
        return None
    return value


def _optional_phone(data: dict, field: str, errors: dict[str, str]) -> str | None:
    if field not in data or data.get(field) in (None, ""):
        return None
    raw = data.get(field)
    if not isinstance(raw, str) or not PHONE_RE.match(raw.strip()):
        errors[field] = "Enter a valid phone number"
        return None
    return raw.strip()


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


def _volunteer_profile(raw: Any, errors: dict[str, str], prefix: str = "volunteer_profile.") -> VolunteerProfileInput | None:
    if not isinstance(raw, dict):
        errors["volunteer_profile"] = "Expected an object"
        return None

    local: dict[str, str] = {}
    city = _text(raw, "primary_city", local, max_len=100)
    experience = _text(raw, "experience", local, max_len=10000)

    skills_raw = raw.get("skills", [])
    parts: list[str] = []
    if not isinstance(skills_raw, list):
        local["skills"] = "Skills must be a list of non-empty strings"
    else:
        for item in skills_raw:
            if not isinstance(item, str):
                local["skills"] = "Skills must be a list of non-empty strings"
                parts = []
                break
            parts.extend(split_skills(item))
        if "skills" not in local and not parts:
            local["skills"] = "Skills must be a list of non-empty strings"
    skills = tuple(dict.fromkeys(part.casefold() for part in parts))
    if "skills" not in local and (len(skills) > 30 or any(len(s) > 50 for s in skills)):
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
