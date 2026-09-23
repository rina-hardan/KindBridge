# Event Sourcing: KindBridge

## 1. Rule

The event store is the source of truth. Projection tables (`users`, `help_requests`, `volunteer_profiles`, `task_assignments`, `exemption_links`, dashboard aggregates) are rebuildable. Command handlers:

1. Load the aggregate by reading its event stream (or a snapshot + newer events).
2. Decide; if invalid, raise a domain error (no insert).
3. Append events in one SQL transaction with `expected_version`.
4. Run projectors in the same transaction (v1: synchronous projector; no separate broker).

Queries never `INSERT` into `event_store`.

## 2. `event_store` table (SQL Server)

| Field | Type | Constraints |
| :--- | :--- | :--- |
| `event_id` | UNIQUEIDENTIFIER | PK, default NEWSEQUENTIALID() |
| `aggregate_id` | UNIQUEIDENTIFIER | NOT NULL, indexed |
| `aggregate_type` | NVARCHAR(50) | `User`, `HelpRequest`, `VolunteerProfile`, `ExemptionLink` |
| `event_type` | NVARCHAR(80) | NOT NULL |
| `payload_json` | NVARCHAR(MAX) | NOT NULL |
| `version` | INT | NOT NULL; unique with `aggregate_id` |
| `correlation_id` | UNIQUEIDENTIFIER | Request/trace id |
| `causation_id` | UNIQUEIDENTIFIER | NULL or parent event_id |
| `created_at` | DATETIME2 | NOT NULL, UTC |

Optimistic concurrency: insert with `version = last + 1`; unique `(aggregate_id, version)` conflict → 409.

## 3. Aggregates and events

### User

- `UserRegistered` — email, role, full_name (no password in payload; hash stays in projection only or a `CredentialSet` event with hash)
- `UserLoggedIn` — audit
- `UserDeactivated`

Password hashes must not appear in logs. Prefer `CredentialSet { password_hash }` as a dedicated event stored in DB but redacted in any debug dump.

### VolunteerProfile

- `VolunteerProfileUpdated` — skills, experience, city, vehicle, frequency → **triggers Chroma upsert**
- `VolunteerAvailabilityChanged` — status, unavailable_until
- `VolunteerCapacityChanged` — current_active_tasks (projector-derived; optional if projector computes from assignment events only)

Prefer computing `current_active_tasks` only in the projector from `AssignmentApproved` / `TaskCompleted` / `TaskReleased` / `HelpRequestCancelled`.

### HelpRequest

- `HelpRequestCreated`
- `MatchesProposed` — array of `{ volunteer_id, score, rationale, rank }`, `match_attempt`, `k`
- `NoMatchFound` — `match_attempt`, `reason`
- `MatchRetriggered`
- `AssignmentApproved` — `assignment_id`, `volunteer_id`, `approved_by`
- `AssignmentsRejected` — volunteer ids, reason
- `AssignmentOverridden` — `volunteer_id`, `override_reason`, `approved_by`
- `TaskCompleted`
- `TaskReleased`
- `HelpRequestCancelled`

### ExemptionLink

- `ExemptionLinkCreated` — volunteer_id, requester_id, created_by, reason

## 4. Snapshots (optional v1)

If a stream exceeds 200 events, write `snapshots(aggregate_id, version, state_json)`. Agent and HTTP handlers load snapshot then remaining events.

## 5. Rebuild

`python -m app.projections.rebuild` truncates projection tables and replays `event_store` ordered by `(created_at, version)`. Required for exams: demonstrate one rebuild.

## 6. What is not Event Sourcing

JWT sessions, CSRF secrets, and Chroma vectors are **not** event-sourced. Vector upsert is a side effect of `VolunteerProfileUpdated` / `HelpRequestCreated` (request text may also be embedded for RAG query, volunteer docs are the corpus).
