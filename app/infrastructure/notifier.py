"""Outgoing notifications. The rest of KindBridge only knows ``Notifier.send``.

``GmailMcpNotifier`` is an MCP client (official ``mcp`` SDK, stdio transport). For each
mail it launches the community Gmail MCP server ``@artymclabin/gmail-mcp`` through ``npx``
and calls its ``send_email`` tool. A notifier never raises into a command: a failed send is
logged as ``notify_failed`` (no secrets) and the events stay committed.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

import anyio

from app.config import Config

logger = logging.getLogger(__name__)

# Pinned: npx runs this code with access to the mailbox token. Bump it on purpose, not by accident.
GMAIL_MCP_PACKAGE = "@artymclabin/gmail-mcp@1.2.3"
SEND_TOOL = "send_email"
# Least privilege: the server may only send mail, never read or delete it.
AUTH_SCOPE = "gmail.send"
OAUTH_KEYS_FILE = "gcp-oauth.keys.json"
CREDENTIALS_FILE = "credentials.json"
SERVER_LOG_FILE = "server.log"
# The server's built-in loopback listener. A Desktop client accepts any http://localhost port.
CALLBACK_URL = "http://localhost:3000/oauth2callback"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.5

_ADDRESS = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")
_AUTH_MARKERS = ("invalid_grant", "invalid_client", "unauthorized_client", "refresh token", "login required")
_REASON_LIMIT = 200


class Notifier(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class NotifyError(RuntimeError):
    """A send failed. ``GmailMcpNotifier.send`` logs it and returns."""


class PermanentNotifyError(NotifyError):
    """Retrying cannot help (not authorized, no Node, bad address)."""


class NullNotifier:
    """Used when TESTING or when Gmail is not configured. Sends nothing."""

    def send(self, to: str, subject: str, body: str) -> None:
        return None


@dataclass(frozen=True)
class SentMail:
    to: str
    subject: str
    body: str


class FakeNotifier:
    """Records mail for tests. ``raises`` simulates a notifier that misbehaves."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.sent: list[SentMail] = []
        self.raises = raises

    def send(self, to: str, subject: str, body: str) -> None:
        if self.raises is not None:
            raise self.raises
        self.sent.append(SentMail(to=to, subject=subject, body=body))

    def to(self, address: str) -> list[SentMail]:
        return [mail for mail in self.sent if mail.to == address]


@dataclass(frozen=True)
class GmailSettings:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    token_dir: Path

    @classmethod
    def from_config(cls, config: Config) -> "GmailSettings":
        return cls(config.gmail_client_id, config.gmail_client_secret, resolve_token_dir(config.gmail_token_dir))

    @property
    def keys_path(self) -> Path:
        return self.token_dir / OAUTH_KEYS_FILE

    @property
    def credentials_path(self) -> Path:
        return self.token_dir / CREDENTIALS_FILE

    @property
    def log_path(self) -> Path:
        return self.token_dir / SERVER_LOG_FILE


def resolve_token_dir(raw: str) -> Path:
    """Relative paths are relative to the project root, not to the process working directory."""
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def write_oauth_keys(settings: GmailSettings) -> Path:
    """Write the keys file the Gmail MCP server reads. It lives in a git-ignored folder."""
    settings.token_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "installed": {
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    settings.keys_path.write_text(json.dumps(document), encoding="utf-8")
    try:
        os.chmod(settings.keys_path, 0o600)
    except OSError:
        pass
    return settings.keys_path


def npx_command() -> str:
    found = shutil.which("npx")
    if found is None:
        raise PermanentNotifyError("Node.js (npx) was not found on PATH")
    return found


def _launch_gmail_server() -> tuple[str, list[str]]:
    return npx_command(), ["-y", GMAIL_MCP_PACKAGE]


def server_environment(settings: GmailSettings) -> dict[str, str]:
    """Only paths. The server reads the client id and secret from the keys file."""
    return {
        "GMAIL_OAUTH_PATH": str(settings.keys_path),
        "GMAIL_CREDENTIALS_PATH": str(settings.credentials_path),
    }


def mask_address(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class GmailMcpNotifier:
    def __init__(
        self,
        settings: GmailSettings,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff: float = DEFAULT_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        call: Callable[[str, str, str], None] | None = None,
        launch: Callable[[], tuple[str, list[str]]] | None = None,
    ) -> None:
        """``call`` replaces one whole attempt; ``launch`` replaces only the server command (tests)."""
        self._settings = settings
        self._timeout = timeout
        self._attempts = max(1, attempts)
        self._backoff = backoff
        self._sleep = sleep
        self._call = call or self._call_mcp
        self._launch = launch or _launch_gmail_server

    def send(self, to: str, subject: str, body: str) -> None:
        """Best effort. Never raises, so a mail problem cannot reach the command that committed."""
        try:
            self.deliver(to, subject, body)
        except Exception as exc:
            logger.error("notify_failed to=%s reason=%s", mask_address(to), self._scrub(exc))

    def deliver(self, to: str, subject: str, body: str) -> None:
        """Send with retries. Raises ``NotifyError`` after the last attempt (smoke test uses this)."""
        if not _ADDRESS.match(to or ""):
            raise PermanentNotifyError("invalid recipient address")
        last: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                self._call(to, subject, body)
                return
            except PermanentNotifyError:
                raise
            except Exception as exc:
                last = exc
                if attempt < self._attempts:
                    self._sleep(self._backoff * 2 ** (attempt - 1))
        raise NotifyError(f"gave up after {self._attempts} attempts: {self._scrub(last)}") from last

    def _call_mcp(self, to: str, subject: str, body: str) -> None:
        settings = self._settings
        if not settings.credentials_path.exists():
            raise PermanentNotifyError("Gmail is not authorized yet; run: python -m scripts.gmail_auth")
        write_oauth_keys(settings)
        command, args = self._launch()
        _run_sync(lambda: self._send_async(command, args, to, subject, body))

    async def _send_async(self, command: str, args: list[str], to: str, subject: str, body: str) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        settings = self._settings
        params = StdioServerParameters(command=command, args=args, env=server_environment(settings))
        arguments = {"to": [to], "subject": subject, "body": body, "mimeType": "text/plain"}
        # Server stderr goes to a git-ignored file, never to our logs.
        with open(settings.log_path, "w", encoding="utf-8", errors="replace") as errlog:
            with anyio.fail_after(self._timeout):
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            SEND_TOOL,
                            arguments,
                            read_timeout_seconds=timedelta(seconds=self._timeout),
                        )
        text = " ".join(
            str(getattr(part, "text", "")) for part in result.content if getattr(part, "type", "") == "text"
        )
        if not result.isError and "sent successfully" in text.lower():
            return
        reason = " ".join(text.split())[:_REASON_LIMIT] or "empty reply from Gmail MCP server"
        if any(marker in reason.lower() for marker in _AUTH_MARKERS):
            raise PermanentNotifyError(f"Gmail authorization problem; run: python -m scripts.gmail_auth ({reason})")
        raise NotifyError(reason)

    def _scrub(self, error: BaseException | None) -> str:
        text = " ".join(str(error or "unknown").split())
        for secret in (self._settings.client_secret, self._settings.client_id):
            if secret:
                text = text.replace(secret, "***")
        label = type(error).__name__ if error is not None else "Error"
        return f"{label}: {text}"[:_REASON_LIMIT]


def _run_sync(factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run a coroutine from sync code, whether or not the calling thread already has a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(factory())
        return
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(factory())).result()


def build_notifier(config: Config) -> Notifier:
    if config.testing or not config.mail_enabled:
        return NullNotifier()
    if not (config.gmail_client_id and config.gmail_client_secret):
        logger.warning("mail_enabled_but_gmail_client_missing; set GMAIL_MCP_CLIENT_ID and GMAIL_MCP_CLIENT_SECRET")
        return NullNotifier()
    return GmailMcpNotifier(GmailSettings.from_config(config))
