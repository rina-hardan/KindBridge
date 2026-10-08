"""Smoke test: ``python -m scripts.send_test_email --to <address>``.

Sends one real mail through the Gmail MCP server and reports whether it worked. It ignores
MAIL_ENABLED on purpose, so you can check the setup before switching mail on. By default the
address must belong to a KindBridge user, the same rule the application follows.
"""

import argparse
import sys

from app.config import load_settings
from app.infrastructure.notifier import GmailMcpNotifier, GmailSettings, NotifyError, resolve_token_dir
from app.repositories.db import make_engine
from app.repositories.users import UserRepository

SUBJECT = "KindBridge: בדיקת שליחת מייל"
BODY = (
    "שלום,\n"
    "זו הודעת בדיקה מ-KindBridge. אם קיבלת אותה, שליחת המיילים דרך Gmail MCP עובדת.\n\n"
    "Hello, this is a KindBridge test message. If you can read it, Gmail MCP sending works."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send one test e-mail through the Gmail MCP server.")
    parser.add_argument("--to", required=True, help="recipient; must be the e-mail of a KindBridge user")
    parser.add_argument(
        "--allow-unlisted",
        action="store_true",
        help="skip the check that the address belongs to a KindBridge user",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    if not settings["GMAIL_MCP_CLIENT_ID"] or not settings["GMAIL_MCP_CLIENT_SECRET"]:
        print("GMAIL_MCP_CLIENT_ID and GMAIL_MCP_CLIENT_SECRET must be set in your environment (.env).")
        return 2
    address = args.to.strip().lower()

    if not args.allow_unlisted:
        if not settings["DATABASE_URL"]:
            print("DATABASE_URL is not set, so the user check cannot run. Use --allow-unlisted to skip it.")
            return 2
        with make_engine(settings["DATABASE_URL"]).connect() as conn:
            known = UserRepository().email_exists(conn, address)
        if not known:
            print("That address is not a KindBridge user. Pick a registered user, or pass --allow-unlisted.")
            return 2

    gmail = GmailSettings(
        settings["GMAIL_MCP_CLIENT_ID"],
        settings["GMAIL_MCP_CLIENT_SECRET"],
        resolve_token_dir(settings["GMAIL_TOKEN_DIR"]),
    )
    try:
        GmailMcpNotifier(gmail).deliver(address, SUBJECT, BODY)
    except NotifyError as exc:
        print(f"FAILED: {exc}")
        print(f"Server log (no secrets): {gmail.log_path}")
        return 1
    print("OK: the message was handed to Gmail. Check the inbox of the recipient.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
