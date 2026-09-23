# KindBridge Cursor rule

Canonical file for Cursor: [`.cursor/rules/kindbridge-cqrs.mdc`](../.cursor/rules/kindbridge-cqrs.mdc)

This is the NFR-3 Rule (`alwaysApply: true`).

- Mutating Flask routes must dispatch a Command handler. Do not INSERT/UPDATE domain tables from a controller.
- Query handlers must not append to `event_store` and must not mutate projection tables.
- After a successful command, append immutable events with `event_id`, `aggregate_id`, `aggregate_type`, `event_type`, `payload_json`, `version`, `created_at`. Use optimistic concurrency on `version`.
- SQL access only through repository interfaces so Somee SQL Server can be swapped for LocalDB in tests.
- New HTTP endpoints ship with tests: happy path and 401/403.
- The matching agent runs as process `python -m agent.main`, not as a Flask thread in production.
- Product name is KindBridge (not AidSync).
