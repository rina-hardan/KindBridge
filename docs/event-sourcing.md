# Event Sourcing: KindBridge

## 1. Rule

The event store is the source of truth. Projection tables (`users`, `requester_profiles`, `volunteer_profiles`, `volunteer_unavailability`, `help_requests`, `task_assignments`, `exemption_links`, dashboard aggregates) are rebuildable **from events alone**. Therefore every field a projection needs (including the password hash and the encrypted phone) must be present in some event payload. Command handlers:

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
| `aggregate_type` | NVARCHAR(50) | `User`, `HelpRequest`, `ExemptionLink` |
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

- `UserRegistered`: `email`, `full_name`, `phone_encrypted` (encrypted before it enters the payload), `city`, `home_address`. `city` and `home_address` are plaintext. Public registration requires both and does not include a volunteer or requester profile. The admin seed writes both as `null`. No `role` field. Public registration never sets `is_admin`; roles are derived from `is_admin` and an enabled volunteer profile.
- `UserDetailsUpdated`: `UpdateAccountDetailsCommand`. Payload: `full_name`, `phone_encrypted`, `city`, `home_address`. No `email`. The projector updates those `users` columns and leaves email, password, and `is_admin` unchanged. `city` and `home_address` stay required for a non-admin and may be null for an admin.
- `CredentialSet`: `password_hash` (bcrypt). Appended on the same User stream immediately after `UserRegistered`. Stored in the event store because projections must be rebuildable; redacted in any log or debug dump.
- `AdminBootstrapped`: empty payload. Appended only by the one-time admin seed, after `UserRegistered` and `CredentialSet`. The projector sets `users.is_admin = 1`. Public registration never emits this event, and admin status is never granted or removed through the app.

Volunteer profile, requester profile, and unavailability events are appended on this same User stream. There is no separate `VolunteerProfile` stream and no `VolunteerAvailabilityChanged` event. `INACTIVE` is a field of `VolunteerProfileUpdated`. Date ranges are the two unavailability events below.

- `VolunteerProfileEnabled`: appended by `EnableVolunteerProfileCommand` from "Add volunteering", and again when that command turns a disabled profile back on. Public registration does not emit it. Payload: `profile_id`, `primary_city`, `has_vehicle`, `skills`, `experience`, `base_frequency`, `max_active_tasks`, `max_parallel_tasks`. `primary_city` is the residence city when the form omits it. `skills` may be an empty list; the narrative is `experience`. Omitted capacity fields are stored as `max_active_tasks = 1` and `max_parallel_tasks = 2`. The projector inserts `volunteer_profiles` with `is_enabled = 1` and `availability_status = AVAILABLE`. If that `user_id` already has a profile, it sets `is_enabled = 1` on the existing row instead of inserting a second one. After the transaction commits, upsert the résumé into Chroma (`experience` + space + `skills_json`). A Chroma failure does not roll the event back.
- `VolunteerProfileUpdated`: `UpdateVolunteerProfileCommand` from the volunteering form, and `UpdateVolunteerSkillsCommand`. The volunteering form sends vehicle, free text, and frequency. Omitted city, capacity, and availability status stay as they are; an omitted or empty skill list is stored as `[]`. The skills command copies every other payload field from the current profile and replaces `skills` only; an empty list is rejected. It is not called from the tasks page. Payload: `profile_id`, `primary_city`, `has_vehicle`, `skills`, `experience`, `base_frequency`, `availability_status` (`AVAILABLE` or `INACTIVE`), `max_active_tasks`, `max_parallel_tasks`. No `unavailable_until`. The projector updates those columns and does not change `is_enabled` or the task counters. After commit: if `availability_status` is `INACTIVE`, delete the Chroma vector; otherwise upsert the résumé. `UpdateVolunteerProfileCommand` rejects with 409 while the volunteer holds an `ASSIGNED` task and the new status is `INACTIVE`. Changing skills does not.
- `VolunteerProfileDisabled`: `DisableVolunteerProfileCommand`. Payload: `profile_id`. The projector sets `is_enabled = 0` and keeps the row. After commit, delete the Chroma vector. The command rejects with 409 while the volunteer holds an `ASSIGNED` task.
- `VolunteerUnavailabilityAdded`: `AddVolunteerUnavailabilityCommand`. Payload: `unavailability_id`, `volunteer_id` (`volunteer_profiles.id`), `from_date`, `until_date` (`until_date >= from_date`), `reason` (nullable). The projector inserts `volunteer_unavailability` with `is_cancelled = 0`. The command rejects with 409 when the period covers the date of an `ASSIGNED` task.
- `VolunteerUnavailabilityCancelled`: `CancelVolunteerUnavailabilityCommand`. Payload: `unavailability_id`. The projector sets `is_cancelled = 1`. The row stays so a rebuild can replay the cancel; it is not deleted.
- `RequesterProfileUpdated`: `UpdateRequesterProfileCommand`. Payload: `profile_id`, `default_city`, `default_address`, `accessibility_notes`, `emergency_contact_name`, `emergency_contact_phone_encrypted` (nullable). `default_address` is plaintext. `emergency_contact_phone` is Fernet-encrypted before it enters the payload. The projector upserts `requester_profiles`.
- `UserLoggedIn`: audit.
- `UserDeactivated`.

Never put a plaintext password in any payload. Password hashes must not appear in logs.

`current_active_tasks` and `current_parallel_tasks` are not events. The projector computes them from `AssignmentApproved`, `AssignmentOverridden`, `TaskCompleted`, `TaskReleased`, and `HelpRequestCancelled`.

### HelpRequest

- `HelpRequestCreated`: `requester_id`, `city`, `address`, `category`, `resource_type`, `description`, `urgency`, `preferred_date`, `preferred_time_from`, `preferred_time_to`, `estimated_duration_min`, `required_skills`, `requires_vehicle`, `concurrency_type` (`UNKNOWN` at submit), `match_attempt` (`0`). `series_id` stays null. `address` is plaintext in the payload; queries hide it until the request is `ASSIGNED`. v1 does not emit `HelpRequestUpdated`; the owner cancels and submits a new request.
- `MatchesProposed`: array of `{ volunteer_id, score, rationale, rank }`, `match_attempt`, `k`.
- `NoMatchFound`: `match_attempt`, `reason`, `rejection_summary` (counts per hard-filter reason).
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

JWT sessions, CSRF secrets, and Chroma vectors are **not** event-sourced. The vector upsert is a post-commit side effect of `VolunteerProfileEnabled` and of `VolunteerProfileUpdated` when `availability_status` is `AVAILABLE`. `VolunteerProfileDisabled` and `VolunteerProfileUpdated` with `availability_status = INACTIVE` delete that vector after commit. The volunteer résumé is the corpus. Request text (`HelpRequestCreated`) is embedded only at query time by the agent and is not stored in Chroma.