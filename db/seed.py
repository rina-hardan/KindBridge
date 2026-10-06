"""Create the first admin account (system-spec section 2: admins exist only via this seed).

Usage, from the repository root:
    python -m db.seed

Reads ADMIN_BOOTSTRAP_EMAIL (required), ADMIN_BOOTSTRAP_PASSWORD (prompted if missing),
ADMIN_BOOTSTRAP_NAME and ADMIN_BOOTSTRAP_PHONE (optional) from the environment or .env.
Refuses to run if any admin already exists.
"""

import getpass
import os
import sys

from dotenv import load_dotenv

from app import build_services
from app.commands.dtos import BootstrapAdminCommand
from app.config import Config, ConfigError
from app.domain.errors import AdminAlreadyExists, EmailAlreadyRegistered, ValidationError


def _password_from_env_or_prompt() -> str:
    password = os.getenv("ADMIN_BOOTSTRAP_PASSWORD")
    if password:
        return password
    first = getpass.getpass("Admin password (min 10 chars): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        sys.exit("Passwords do not match.")
    return first


def main() -> int:
    load_dotenv()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    email = os.getenv("ADMIN_BOOTSTRAP_EMAIL", "").strip()
    if not email:
        print("ADMIN_BOOTSTRAP_EMAIL is not set.", file=sys.stderr)
        return 1

    try:
        command = BootstrapAdminCommand.create(
            email=email,
            password=_password_from_env_or_prompt(),
            full_name=os.getenv("ADMIN_BOOTSTRAP_NAME", "KindBridge Admin"),
            phone=os.getenv("ADMIN_BOOTSTRAP_PHONE", ""),
        )
        result = build_services(config).bus.dispatch(command)
    except ValidationError as exc:
        for field, message in exc.field_errors.items():
            print(f"{field}: {message}", file=sys.stderr)
        return 1
    except (AdminAlreadyExists, EmailAlreadyRegistered) as exc:
        print(exc.message, file=sys.stderr)
        return 1

    print(f"Admin created: {command.email} (id {result.user_id})")
    print("Remove ADMIN_BOOTSTRAP_PASSWORD from .env now that the account exists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
