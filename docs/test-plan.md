# Test plan: KindBridge

Rule: every new HTTP endpoint has at least one success test and one unauthorized/forbidden test (`tests/`).

Stack: pytest + Flask test client + SQL Server test DB or LocalDB. Agent tests mock MCP.

## Auth

- Register a person with residence city and home address only; reject volunteer fields, a missing residence, a duplicate email, and admin self-register. Admin seed leaves `city` and `home_address` null. The account page updates name, phone, city, and home address, and refuses an email change. The requester profile is saved from the account entry. The volunteer area opens without a profile. Add volunteering copies the residence city, stores one free-text narrative, a vehicle flag, and a frequency, and does not require a skill list.
- Login sets HttpOnly cookie; bad password 401; lockout after 5 failures.
- Requester cannot GET another requester’s detail (403).
- Volunteer cannot POST `/api/requests/<id>/approve` (403).

## Requests and matching

- Hebrew and English names of the same city pass the geography check; Hebrew and English names of the same skill count as overlap.
- Submit creates `HelpRequestCreated` and projection `PENDING_REVIEW`. There is no update endpoint.
- The owner's list defaults to open statuses. Cancel from `PENDING_REVIEW` sets `CANCELLED` and drops the row from that default list. Another requester canceling it receives 403.
- Propose with three eligible volunteers writes K≤3 `PROPOSED` and status `MATCH_PROPOSED`.
- Propose with empty pool → `NO_MATCH`.
- Reject blacklists those volunteer ids; second propose must not include them.
- Approve one: siblings `SUPERSEDED`; volunteer `current_active_tasks` +1; address visible to that volunteer only.
- Second concurrent approve → 409.
- Override requires reason; without reason 400.
- Retrigger supersedes open proposals and returns to `PENDING_REVIEW`; those volunteers stay eligible (SUPERSEDED is not a blacklist).
- The assigned volunteer completes a task: request `COMPLETED`, assignment `COMPLETED`, capacity decremented. Release returns the request to `PENDING_REVIEW`, marks that assignment `DECLINED`, frees capacity, and notifies the requester. Another person receives 403; a missing login receives 401.
- A volunteer edits their profile after joining. Saving `INACTIVE`, or adding an unavailability period that covers an assigned task's date, returns 409 until the task is released. Cancelling a period keeps the row with `is_cancelled = 1`.
- The tasks page opens without a volunteer profile. Its header is "Add new volunteering", the taken-on list can be empty, and the want-to-do column is empty until a profile exists. After a profile exists that column shows the narrative, and the page does not list skills. `POST /api/me/skills` still replaces the skill list, keeps the other résumé fields, and rejects an empty list. A missing login is 401; a requester is 403.

## Capacity and calendar

- Volunteer at `max_active_tasks` is ineligible (`CheckVolunteerCapacityTool`).
- Two `PHYSICAL_PRESENCE` windows overlapping → ineligible.
- Remote + physical: allowed if under max_active_tasks.
- `requires_vehicle` and `has_vehicle=0` → dropped in hard filter.
- `TEMPORARILY_UNAVAILABLE` until future timestamp → skipped; after timestamp job/agent treats as AVAILABLE.

## Exemptions and cancel

- Exemption pair never proposed.
- Cannot create exemption while ASSIGNED (409).
- Cancel from PENDING: no Gmail to volunteer.
- Cancel from ASSIGNED: capacity freed; volunteer notified (Gmail mocked).

## Notifications (`tests/test_notifications.py`, no network)

Tests use `FakeNotifier`; nothing talks to Gmail.

- Approve and override send two mails: the assigned volunteer and the requester. The mails hold no phone number or address.
- Cancel from `ASSIGNED` mails the volunteer only. Cancel from `PENDING_REVIEW`, or with an unapproved proposal, sends nothing. A rejected approve sends nothing.
- `MatchesProposed` mails every active admin (not inactive ones) with the request facts and the candidates in rank order; `NoMatchFound` adds the rejection summary. `ADMIN_NOTIFY_EMAIL` narrows the list only to an admin that exists in the database.
- A second `ProposeMatchCommand` for the same `(request_id, match_attempt)` is a no-op and sends no second mail. A proposal that fails to commit sends none.
- A notifier that raises, and a failure while resolving recipients, never raise into the command and never roll back events (approve, cancel, propose).
- Recipients come only from the database: an unknown volunteer profile or request produces no mail.
- `GmailMcpNotifier`: 3 attempts with backoff then `notify_failed` with no secret and a masked address; a permanent error is not retried; malformed addresses are never sent; nothing is launched without a stored token; works inside a running event loop.
- The real MCP client path (stdio, `send_email`, Hebrew text, server error text, expired token) runs against `tests/fake_gmail_mcp_server.py`, a local stand-in.
- `build_notifier` returns `NullNotifier` unless mail is enabled and configured; `Config.__repr__` hides the Gmail client id and secret.
- Manual, outside pytest: `python -m scripts.send_test_email --to <user email>` and the end-to-end check in the README.

## Event sourcing

- Replay rebuilds identical `help_requests.status` and assignment counts.
- Query handler performing INSERT into `event_store` must fail a lint/architecture test (grep or import guard).

## Agent

- Tavily exception still produces a proposal if RAG+tools succeed (`web_lookup=skipped`).
- Idempotent propose: same `(request_id, match_attempt)` does not duplicate assignment rows.
- Profile update changes Chroma document (fake client assertion).
