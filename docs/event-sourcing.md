# Event Sourcing: KindBridge

## 1. Rule

The event store is the source of truth. Projection tables (`users`, `help_requests`, `volunteer_profiles`, `task_assignments`, `exemption_links`, dashboard aggregates) are rebuildable **from events alone**. Therefore every field a projection needs (including the password hash and the encrypted phone) must be present in some event payload. Command handlers:

1. Load the aggregate by reading its event stream (or a snapshot + newer events).
2. Decide; if invalid, raise a domain error (no insert).
3. Append events in one SQL transaction with `expected_version`.
4. Run projectors in the same transaction (v1: synchronous projector; no separate broker).
5. After the transaction commits, run side effects (Chroma upsert/delete, Gmail). A side-effect failure never rolls back events.

Queries never `INSERT` into `event_store`.

## 2. `event_store` table (SQL Server)

| Field | Type | Constraints |
| :--- | :--- | :--- |
| `seq` | BIGINT | IDENTITY, UNIQUE index; global ordering for replay |
| `event_id` | UNIQUEIDENTIFIER | PK, default NEWSEQUENTIALID() |
| `aggregate_id` | UNIQUEIDENTIFIER | NOT NULL, indexed |
| `aggregate_type` | NVARCHAR(50) | `User`, `HelpRequest`, `VolunteerProfile`, `ExemptionLink` |
| `event_type` | NVARCHAR(80) | NOT NULL |
| `payload_json` | NVARCHAR(MAX) | NOT NULL |
| `version` | INT | NOT NULL; unique with `aggregate_id` |
| `correlation_id` | UNIQUEIDENTIFIER | Request/trace id |
| `causation_id` | UNIQUEIDENTIFIER | NULL or parent event_id |
| `created_at` | DATETIME2 | NOT NULL, UTC |

Optimistic concurrency: insert with `version = last + 1`; unique `(aggregate_id, version)` conflict -> 409.

`version` is per aggregate; `seq` is global. Replay uses `seq`.

## 3. Aggregates and events

### User

- `UserRegistered`: email, role, full_name, `phone_encrypted` (encrypted before it enters the payload).
- `CredentialSet`: `password_hash` (bcrypt). Stored in the event store because projections must be rebuildable; redacted in any log or debug dump.
- `UserLoggedIn`: audit.
- `UserDeactivated`.

Never put a plaintext password in any payload. Password hashes must not appear in logs.

### VolunteerProfile

- `VolunteerProfileUpdated`: skills, experience, city, vehicle, frequency. **Triggers Chroma upsert** (post-commit side effect).
- `VolunteerAvailabilityChanged`: status, unavailable_until. When the new status is `INACTIVE`, delete the volunteer vector from Chroma (post-commit side effect).

`current_active_tasks` is not an event. It is computed only in the projector from `AssignmentApproved`, `AssignmentOverridden`, `TaskCompleted`, `TaskReleased`, and `HelpRequestCancelled`.

### HelpRequest

- `HelpRequestCreated`: all request fields (city, address, category, resource_type, description, urgency, dates, required skills, requires_vehicle, requester_id).
- `MatchesProposed`: array of `{ volunteer_id, score, rationale, rank }`, `match_attempt`, `k`.
- `NoMatchFound`: `match_attempt`, `reason`.
- `MatchRetriggered`.
- `AssignmentApproved`: `assignment_id`, `volunteer_id`, `approved_by`.
- `AssignmentsRejected`: volunteer ids, reason.
- `AssignmentOverridden`: `assignment_id`, `volunteer_id`, `override_reason`, `approved_by`.
- `TaskCompleted`: `assignment_id`.
- `TaskReleased`: `assignment_id`, reason.
- `HelpRequestCancelled`: `cancelled_by`, reason.

Idempotent propose: if the stream already contains `MatchesProposed` or `NoMatchFound` for the same `match_attempt`, `ProposeMatchCommand` is a no-op.

### ExemptionLink

- `ExemptionLinkCreated`: volunteer_id, requester_id, created_by, reason.
- `aggregate_id` is a deterministic UUID derived from `(volunteer_id, requester_id)` (uuid5), so a duplicate link conflicts on version instead of creating a second stream.

## 4. Snapshots (optional v1)

If a stream exceeds 200 events, write `snapshots(aggregate_id, version, state_json)`. Agent and HTTP handlers load snapshot then remaining events. Skip in v1 unless time allows.

## 5. Rebuild

`python -m app.projections.rebuild` truncates projection tables and replays `event_store` `ORDER BY seq`. `version` is per aggregate and therefore does not give a global order across streams. Rebuild touches SQL projections only: it does not call Chroma or Gmail. Required for exams: demonstrate one rebuild.

## 6. What is not Event Sourcing

JWT sessions, CSRF secrets, and Chroma vectors are **not** event-sourced. The vector upsert is a post-commit side effect of `VolunteerProfileUpdated`; the volunteer résumé is the corpus. Request text (`HelpRequestCreated`) is embedded only at query time by the agent and is not stored in Chroma.