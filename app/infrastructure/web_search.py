"""City-context lookup through the official Tavily MCP server.

``TavilySearch`` is an MCP client (official ``mcp`` SDK, stdio transport), in the same
style as ``GmailMcpNotifier``. It launches the pinned ``tavily-mcp`` package and calls
``tavily_search``. The API key is placed only in the child process environment.

A failure is raised. ``ProposeMatch`` catches it, records ``web_lookup=skipped``, and
keeps scoring with the travel tool and RAG.
"""

import asyncio
import os
import shutil
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import anyio

from app.config import Config

# Pinned on purpose, same as GMAIL_MCP_PACKAGE. Do not float on @latest.
# Tool name is tavily_search (underscore) in tavily-mcp@0.2.22.
TAVILY_MCP_PACKAGE = "tavily-mcp@0.2.22"
SEARCH_TOOL = "tavily_search"
DEFAULT_TIMEOUT_SECONDS = 8.0
RESULT_LIMIT = 4000
_REASON_LIMIT = 200


class TavilySearch:
    def __init__(
        self,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        *,
        launch: Callable[[], tuple[str, list[str]]] | None = None,
    ) -> None:
        """``launch`` replaces the server command. Tests point it at a local stdio server."""
        self._api_key = api_key
        self._timeout = timeout
        self._launch = launch or _launch_tavily_server

    def search(self, city: str, today: date) -> str:
        query = f"{city} transit disruption OR municipal emergency {today.isoformat()}"
        try:
            command, args = self._launch()
            return _run_sync(lambda: self._search_async(command, args, query))
        except Exception as exc:
            raise RuntimeError(self._scrub(exc)) from None

    async def _search_async(self, command: str, args: list[str], query: str) -> str:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=command,
            args=args,
            env={"TAVILY_API_KEY": self._api_key},
        )
        # Server stderr can echo request failures. Discard it so the key never reaches our logs.
        with open(os.devnull, "w", encoding="utf-8", errors="replace") as errlog:
            with anyio.fail_after(self._timeout):
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            SEARCH_TOOL,
                            {"query": query},
                            read_timeout_seconds=timedelta(seconds=self._timeout),
                        )
        text = _joined_text(result)
        if getattr(result, "isError", False):
            raise RuntimeError(text or "empty error from Tavily MCP server")
        return text

    def _scrub(self, error: BaseException) -> str:
        text = " ".join(str(error).split())
        if self._api_key:
            text = text.replace(self._api_key, "***")
        return f"{type(error).__name__}: {text}"[:_REASON_LIMIT]


def build_web_search(config: Config) -> TavilySearch | None:
    """Do not start a Tavily process in tests, or when no API key is configured."""
    if config.testing or not config.tavily_api_key:
        return None
    return TavilySearch(config.tavily_api_key)


def _joined_text(result: Any) -> str:
    parts: list[str] = []
    for part in getattr(result, "content", None) or []:
        if getattr(part, "type", "") != "text":
            continue
        text = str(getattr(part, "text", ""))
        if text:
            parts.append(text)
    return "\n".join(parts)[:RESULT_LIMIT]


def _launch_tavily_server() -> tuple[str, list[str]]:
    found = shutil.which("npx")
    if found is None:
        raise RuntimeError("Node.js (npx) was not found on PATH")
    return found, ["-y", TAVILY_MCP_PACKAGE]


def _run_sync(factory: Callable[[], Coroutine[Any, Any, str]]) -> str:
    """Run a coroutine from sync code, whether or not the calling thread already has a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()
