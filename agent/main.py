"""KindBridge matching process. Run with ``python -m agent.main``.

Polls PENDING_REVIEW requests and dispatches ProposeMatchCommand. This process
does not approve or assign, and it does not open a local Chroma directory.
"""

import logging
import time

from app import build_services
from app.commands.match_commands import ProposeMatchCommand
from app.config import Config
from app.repositories.matching import SqlMatchingReader

logger = logging.getLogger(__name__)
POLL_SECONDS = 15


def process_pending(services, reader: SqlMatchingReader | None = None) -> int:
    reader = reader or SqlMatchingReader()
    with services.engine.connect() as conn:
        pending = reader.pending_requests(conn)
    for row in pending:
        try:
            services.bus.dispatch(
                ProposeMatchCommand(request_id=row.request_id, match_attempt=row.match_attempt)
            )
        except Exception:
            logger.exception("propose_failed request_id=%s", row.request_id)
    return len(pending)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    services = build_services(Config.from_env())
    while True:
        try:
            process_pending(services)
        except Exception:
            logger.exception("match_poll_failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
