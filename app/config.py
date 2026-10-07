"""Environment-backed settings. Secrets stay in the environment, never in code."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet
from dotenv import load_dotenv

MIN_BCRYPT_ROUNDS = 12
MIN_JWT_SECRET_LENGTH = 32


class ConfigError(RuntimeError):
    pass


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
    bcrypt_rounds = _int_setting(environ, "BCRYPT_ROUNDS", MIN_BCRYPT_ROUNDS)
    if bcrypt_rounds < MIN_BCRYPT_ROUNDS:
        bcrypt_rounds = MIN_BCRYPT_ROUNDS

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


@dataclass(frozen=True)
class Config:
    """Validated settings the web app needs to start."""

    database_url: str
    jwt_secret: str
    encryption_key: str
    jwt_ttl_minutes: int = 60
    bcrypt_rounds: int = MIN_BCRYPT_ROUNDS
    cookie_secure: bool = True
    lockout_max_failures: int = 5
    lockout_window_minutes: int = 15
    testing: bool = False
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    llm_provider: str = "openai"
    llm_model: str = ""
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: str = ""
    tavily_api_key: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ConfigError(f"Environment variable {name} is required")
            return value

        database_url = required("DATABASE_URL")
        jwt_secret = required("JWT_SECRET")
        if len(jwt_secret) < MIN_JWT_SECRET_LENGTH:
            raise ConfigError(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters")

        encryption_key = required("ENCRYPTION_KEY")
        try:
            Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ConfigError(
                "ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                'py -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ) from exc

        bcrypt_rounds = int(os.getenv("BCRYPT_ROUNDS", MIN_BCRYPT_ROUNDS))
        if bcrypt_rounds < MIN_BCRYPT_ROUNDS:
            raise ConfigError(f"BCRYPT_ROUNDS must be >= {MIN_BCRYPT_ROUNDS}")

        return cls(
            database_url=database_url,
            jwt_secret=jwt_secret,
            encryption_key=encryption_key,
            jwt_ttl_minutes=int(os.getenv("JWT_ACCESS_TTL_MINUTES", 60)),
            bcrypt_rounds=bcrypt_rounds,
            cookie_secure=os.getenv("COOKIE_SECURE", "true").lower() != "false",
            chroma_host=os.getenv("CHROMA_HOST", "localhost").strip() or "localhost",
            chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
            llm_provider=os.getenv("LLM_PROVIDER", "openai").strip() or "openai",
            llm_model=os.getenv("LLM_MODEL", "").strip(),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
            or "http://localhost:11434",
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
        )
