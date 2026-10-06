"""Environment-backed settings. Secrets stay in the environment, never in code."""

import os
from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv

_REQUIRED_PRODUCTION = ("DATABASE_URL", "JWT_SECRET")


def _get(environ: Mapping[str, str], name: str, default: str = "") -> str:
    value = environ.get(name)
    if value is None or value == "":
        return default
    return value


def _int_setting(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = _get(environ, name, str(default))
    return int(raw)


def load_settings(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read KindBridge settings from the process environment (or an explicit mapping)."""
    if environ is None:
        load_dotenv()
        environ = os.environ

    jwt_secret = _get(environ, "JWT_SECRET")
    bcrypt_rounds = _int_setting(environ, "BCRYPT_ROUNDS", 12)
    if bcrypt_rounds < 12:
        bcrypt_rounds = 12

    return {
        "TESTING": False,
        "DATABASE_URL": _get(environ, "DATABASE_URL"),
        "JWT_SECRET": jwt_secret,
        "SECRET_KEY": jwt_secret,
        "ENCRYPTION_KEY": _get(environ, "ENCRYPTION_KEY"),
        "JWT_ACCESS_TTL_MINUTES": _int_setting(environ, "JWT_ACCESS_TTL_MINUTES", 60),
        "BCRYPT_ROUNDS": bcrypt_rounds,
        "ADMIN_BOOTSTRAP_EMAIL": _get(environ, "ADMIN_BOOTSTRAP_EMAIL"),
        "ADMIN_BOOTSTRAP_PASSWORD": _get(environ, "ADMIN_BOOTSTRAP_PASSWORD"),
        "LLM_PROVIDER": _get(environ, "LLM_PROVIDER", "openai"),
        "LLM_MODEL": _get(environ, "LLM_MODEL"),
        "OLLAMA_BASE_URL": _get(environ, "OLLAMA_BASE_URL", "http://localhost:11434"),
        "OPENAI_API_KEY": _get(environ, "OPENAI_API_KEY"),
        "EMBEDDING_PROVIDER": _get(environ, "EMBEDDING_PROVIDER", "openai"),
        "CHROMA_HOST": _get(environ, "CHROMA_HOST", "localhost"),
        "CHROMA_PORT": _int_setting(environ, "CHROMA_PORT", 8000),
        "TAVILY_API_KEY": _get(environ, "TAVILY_API_KEY"),
        "GMAIL_MCP_CLIENT_ID": _get(environ, "GMAIL_MCP_CLIENT_ID"),
        "GMAIL_MCP_CLIENT_SECRET": _get(environ, "GMAIL_MCP_CLIENT_SECRET"),
    }


class Config:
    """Flask config object. Attributes are filled from the environment at import."""

    TESTING = False


for _name, _value in load_settings().items():
    setattr(Config, _name, _value)


def missing_production_settings(settings: Mapping[str, Any]) -> list[str]:
    return [name for name in _REQUIRED_PRODUCTION if not settings.get(name)]
