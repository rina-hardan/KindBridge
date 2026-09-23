---
description: KindBridge CQRS, event sourcing, and testing invariants for all Python changes
globs: "**/*.py"
alwaysApply: true
---

# KindBridge implementation rule

- Mutating Flask routes must dispatch a Command handler. Do not INSERT/UPDATE domain tables from a controller.
- Query handlers must not append to `event_store` and must not mutate projection tables.
- After a successful command, append immutable events with `event_id`, `aggregate_id`, `aggregate_type`, `event_type`, `payload_json`, `version`, `created_at`. Use optimistic concurrency on `version`.
- SQL access only through repository interfaces so Somee SQL Server can be swapped for LocalDB in tests.
- New HTTP endpoints ship with tests: happy path and 401/403.
- The matching agent runs as process `python -m agent.main`, not as a Flask thread in production.
- Follow `docs/system-spec.md`, `docs/architecture.md`, `docs/event-sourcing.md`, and `docs/agent-and-mcp.md` when names conflict with older AidSync drafts — KindBridge wins.
