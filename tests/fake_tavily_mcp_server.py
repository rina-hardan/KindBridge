"""A stand-in for the Tavily MCP server, for tests only. Speaks MCP over stdio, never touches the network.

The first argument is the jsonl file that records each ``tavily_search`` call.
The second argument is ``ok`` (default), ``fail``, ``hang``, or ``long``.
"""

import json
import os
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("tavily")


def _calls_path() -> Path:
    return Path(sys.argv[1])


def _mode() -> str:
    return sys.argv[2] if len(sys.argv) > 2 else "ok"


@server.tool()
def tavily_search(query: str) -> str:
    path = _calls_path()
    record = {
        "arguments": {"query": query},
        "has_api_key_env": bool(os.environ.get("TAVILY_API_KEY")),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    mode = _mode()
    if mode == "fail":
        raise RuntimeError("tavily backend exploded")
    if mode == "hang":
        time.sleep(30)
    if mode == "long":
        return "x" * 5000
    return "Buses stopped in the city centre.\nMunicipal emergency desk is open."


if __name__ == "__main__":
    server.run()
