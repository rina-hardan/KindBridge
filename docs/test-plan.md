# Test plan: KindBridge

Rule: every new HTTP endpoint has at least one success test and one unauthorized/forbidden test (`tests/`).

Stack: pytest + Flask test client + SQL Server test DB or LocalDB. Agent tests mock MCP.

## Auth

- Register requester and volunteer; reject duplicate email; reject admin self-register.
- Login sets HttpOnly cookie; bad password 401; lockout after 5 failures.
- Requester cannot GET another requester’s detail (403).
- Volunteer cannot POST `/api/requests/<id>/approve` (403).

## Requests and matching

- Submit creates `HelpRequestCreated` and projection `PENDING_REVIEW`.
- Propose with three eligible volunteers writes K≤3 `PROPOSED` and status `MATCH_PROPOSED`.
- Propose with empty pool → `NO_MATCH`.
- Reject blacklists those volunteer ids; second propose must not include them.
- Approve one: siblings `SUPERSEDED`; volunteer `current_active_tasks` +1; address visible to that volunteer only.
- Second concurrent approve → 409.
- Override requires reason; without reason 400.
- Retrigger supersedes open proposals and returns to `PENDING_REVIEW`; those volunteers stay eligible (SUPERSEDED is not a blacklist).

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

## Event sourcing

- Replay rebuilds identical `help_requests.status` and assignment counts.
- Query handler performing INSERT into `event_store` must fail a lint/architecture test (grep or import guard).

## Agent

- Tavily exception still produces a proposal if RAG+tools succeed (`web_lookup=skipped`).
- Idempotent propose: same `(request_id, match_attempt)` does not duplicate assignment rows.
- Profile update changes Chroma document (fake client assertion).
