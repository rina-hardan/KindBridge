"""MCP client for the KindBridge Studio server.

Production launches ``python -m mcp_tools.server`` over stdio, one session per proposal.
Tests pass an engine and talk to the same server in-process, because the test database
is in-memory SQLite and a child process cannot see it.
"""

import asyncio
import concurrent.futures
import json
import sys
import threading
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Engine

ROOT = Path(__file__).resolve().parents[1]
START_TIMEOUT_SECONDS = 30.0
CALL_TIMEOUT_SECONDS = 10.0
CLOSE_TIMEOUT_SECONDS = 5.0

_Job = tuple[Callable[[Any], Awaitable[Any]], concurrent.futures.Future]


class StudioTools(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def check_capacity(self, volunteer_id: str, request_id: str) -> dict: ...

    def travel(self, volunteer_city: str, request_city: str, resource_type: str) -> dict: ...


class McpStudioClient:
    def __init__(
        self,
        *,
        engine: Engine | None = None,
        database_url: str | None = None,
        launch: Callable[[], tuple[str, list[str]]] | None = None,
    ) -> None:
        """An engine selects the in-process session. Otherwise the client spawns the stdio server."""
        self._engine = engine
        self._database_url = database_url or ""
        self._launch = launch or _launch_studio_server
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[_Job | None] | None = None
        self._error: BaseException | None = None
        self._opened = False

    def open(self) -> None:
        with self._lock:
            if self._opened:
                return
            self._ready.clear()
            self._error = None
            thread = threading.Thread(target=self._thread_main, name="kindbridge-studio-mcp", daemon=True)
            self._thread = thread
            thread.start()
        if not self._ready.wait(START_TIMEOUT_SECONDS):
            self.close()
            raise RuntimeError("MCP studio server did not start")
        if self._error is not None:
            error = self._error
            self.close()
            raise RuntimeError(self._scrub(error)) from None
        with self._lock:
            if self._thread is thread:
                self._opened = True

    def close(self) -> None:
        with self._lock:
            thread = self._thread
            loop = self._loop
            queue = self._queue
            self._opened = False
            self._thread = None
            self._loop = None
            self._queue = None
        if loop is not None and queue is not None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except RuntimeError:
                pass
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(CLOSE_TIMEOUT_SECONDS)

    def tool_names(self) -> set[str]:
        async def factory(session: Any) -> set[str]:
            listed = await session.list_tools()
            return {tool.name for tool in listed.tools}

        names = self._invoke(factory)
        if not isinstance(names, set):
            raise RuntimeError("MCP studio server did not list its tools")
        return names

    def check_capacity(self, volunteer_id: str, request_id: str) -> dict:
        from mcp_tools.server import CAPACITY_TOOL

        return self._tool(CAPACITY_TOOL, {"volunteer_id": volunteer_id, "request_id": request_id})

    def travel(self, volunteer_city: str, request_city: str, resource_type: str) -> dict:
        from mcp_tools.server import TRAVEL_TOOL

        return self._tool(
            TRAVEL_TOOL,
            {
                "volunteer_city": volunteer_city,
                "request_city": request_city,
                "resource_type": resource_type,
            },
        )

    def _tool(self, name: str, arguments: dict[str, str]) -> dict:
        async def factory(session: Any) -> dict:
            result = await session.call_tool(name, arguments)
            return _object(result)

        payload = self._invoke(factory)
        if not isinstance(payload, dict):
            raise RuntimeError("MCP studio tool returned a non-object")
        return payload

    def _invoke(self, factory: Callable[[Any], Awaitable[Any]]) -> Any:
        self.open()
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None:
            raise RuntimeError("MCP studio server is not open")
        future: concurrent.futures.Future = concurrent.futures.Future()
        loop.call_soon_threadsafe(queue.put_nowait, (factory, future))
        try:
            return future.result(CALL_TIMEOUT_SECONDS)
        except Exception as exc:
            self.close()
            raise RuntimeError(self._scrub(exc)) from None

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            self._error = exc
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        self._queue = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        async with self._connect() as session:
            self._ready.set()
            while True:
                job = await self._queue.get()
                if job is None:
                    return
                factory, future = job
                try:
                    future.set_result(await factory(session))
                except Exception as exc:
                    future.set_exception(exc)

    @asynccontextmanager
    async def _connect(self):
        if self._engine is not None:
            from mcp.shared.memory import create_connected_server_and_client_session
            from mcp_tools.server import build_server

            async with create_connected_server_and_client_session(build_server(self._engine)) as session:
                yield session
            return
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        command, args = self._launch()
        params = StdioServerParameters(
            command=command,
            args=args,
            cwd=str(ROOT),
            env=_child_environment(self._database_url),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    def _scrub(self, error: BaseException) -> str:
        text = " ".join(str(error).split())
        if self._database_url:
            text = text.replace(self._database_url, "***")
        return f"{type(error).__name__}: {text}"[:400]


def _launch_studio_server() -> tuple[str, list[str]]:
    return sys.executable, ["-m", "mcp_tools.server"]


def _child_environment(database_url: str) -> dict[str, str]:
    from mcp.client.stdio import get_default_environment

    env = get_default_environment()
    if database_url:
        env["DATABASE_URL"] = database_url
    return env


def _object(result: Any) -> dict:
    if getattr(result, "isError", False):
        raise RuntimeError(_text(result) or "MCP studio tool failed")
    payload = json.loads(_text(result))
    if not isinstance(payload, dict):
        raise RuntimeError("MCP studio tool returned a non-object")
    return payload


def _text(result: Any) -> str:
    parts: list[str] = []
    for part in getattr(result, "content", None) or []:
        if getattr(part, "type", "") != "text":
            continue
        text = str(getattr(part, "text", ""))
        if text:
            parts.append(text)
    return "\n".join(parts)
