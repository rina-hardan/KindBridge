"""One-time Gmail authorization: ``python -m scripts.gmail_auth``.

Writes the OAuth keys file from GMAIL_MCP_CLIENT_ID / GMAIL_MCP_CLIENT_SECRET into the
git-ignored token folder, then runs the Gmail MCP server's own consent flow. Your browser
opens, you approve "send email", and the token is stored in that folder. Nothing secret is
printed.
"""

import os
import socket
import subprocess
import sys

from app.config import load_settings
from app.infrastructure.notifier import (
    AUTH_SCOPE,
    CALLBACK_URL,
    GMAIL_MCP_PACKAGE,
    GmailSettings,
    NotifyError,
    npx_command,
    resolve_token_dir,
    server_environment,
    write_oauth_keys,
)

CALLBACK_PORT = 3000


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    settings = load_settings()
    client_id = settings["GMAIL_MCP_CLIENT_ID"]
    client_secret = settings["GMAIL_MCP_CLIENT_SECRET"]
    if not client_id or not client_secret:
        print("GMAIL_MCP_CLIENT_ID and GMAIL_MCP_CLIENT_SECRET must be set in your environment (.env).")
        return 2
    gmail = GmailSettings(client_id, client_secret, resolve_token_dir(settings["GMAIL_TOKEN_DIR"]))
    try:
        command = npx_command()
    except NotifyError as exc:
        print(f"{exc}. Install Node.js 18+ from https://nodejs.org and try again.")
        return 2
    if not _port_is_free(CALLBACK_PORT):
        print(f"Port {CALLBACK_PORT} is busy. Close the program using it, then run this again.")
        return 2

    write_oauth_keys(gmail)
    print(f"Authorizing '{AUTH_SCOPE}' (send only) with {GMAIL_MCP_PACKAGE}")
    print(f"Redirect URI used by the server: {CALLBACK_URL}")
    print("A browser window will open. Sign in with a Gmail account that is listed as a Test user.\n")
    result = subprocess.run(
        [command, "-y", GMAIL_MCP_PACKAGE, "auth", f"--scopes={AUTH_SCOPE}"],
        env={**os.environ, **server_environment(gmail)},
        check=False,
    )
    if result.returncode != 0 or not gmail.credentials_path.exists():
        print("\nAuthorization did not finish. Fix the message above and run the command again.")
        return 1
    print(f"\nDone. The token was saved under {gmail.token_dir} (git-ignored).")
    print("Next: python -m scripts.send_test_email --to <address of a KindBridge user>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
