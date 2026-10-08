"""A stand-in for the Gmail MCP server, for tests only. Speaks MCP over stdio, never touches the network.

It records every call in ``mailbox.jsonl`` next to GMAIL_CREDENTIALS_PATH. A subject of ``FAIL``
answers like the real server does on an API error; ``AUTH`` answers like an expired token.
"""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("gmail")


@server.tool()
def send_email(to: list[str], subject: str, body: str, mimeType: str = "text/plain") -> str:
    mailbox = Path(os.environ["GMAIL_CREDENTIALS_PATH"]).parent / "mailbox.jsonl"
    with mailbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"to": to, "subject": subject, "body": body}, ensure_ascii=False) + "\n")
    if subject == "FAIL":
        return "Error: backend exploded"
    if subject == "AUTH":
        return "Error: invalid_grant"
    return "Email sent successfully with ID: fake-1"


if __name__ == "__main__":
    server.run()
