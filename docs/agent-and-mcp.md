# Background agent, RAG, and MCP: KindBridge

## 1. Process

`python -m agent.main` is a **separate process** from Flask (course functional req 5 / NFR 11). It must not run as a Flask background thread in production.

Poll every 15s:

```sql
SELECT TOP 10 id, match_attempt, status
FROM help_requests
WHERE status IN ('PENDING_REVIEW')
ORDER BY CASE urgency WHEN 'EMERGENCY' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 ELSE 3 END,
         created_at;
```

For each row, dispatch `ProposeMatchCommand` **once** per `match_attempt` using idempotency key `propose:{id}:{match_attempt}`.

## 2. Deep Agent graph

Implementation: Deep Agents (LangChain) or equivalent LangGraph supervisor with sub-agents. Nodes:

1. **Load**: request projection + required skills. If `concurrency_type` is `UNKNOWN`, classify and append `ConcurrencyClassified` before filtering. `FLEXIBLE_REMOTE` leans `PARALLEL_OK`; every other type, including a value that is still `UNKNOWN`, is `EXCLUSIVE`.
2. **Retrieve (RAG)**: embed `description + category + required_skills`, plus the requester's accessibility notes when they exist. Query Chroma collection `volunteer_resumes`, `top_n = 15`. This happens before the hard filter. Do not take the top 3 and then filter them.
3. **Hard filter** (system-spec 4.2), applied only to those 15: inactive (`is_enabled = 0`, `users.is_active = 0`, or `availability_status = INACTIVE`), self-assignment, exemption, `DECLINED` on this request, unavailability period, geography, vehicle, capacity, schedule overlap. Semantic similarity is not a rejection. A volunteer with similarity 0 who passes the filter is still ranked. There is no `SEMANTIC_FILTER_NO_OVERLAP`.
4. **Capacity tool (MCP Studio)**: `CheckVolunteerCapacityTool` on each survivor. `UNKNOWN` uses the exclusive cap (`current_active_tasks < max_active_tasks`). `PARALLEL_OK` uses `current_parallel_tasks < max_parallel_tasks`.
5. **Travel tool (MCP Studio)**: `CalculateTravelContextTool`.
6. **Web (Tavily MCP)**: city-level context (weather disruption, transit strike, municipal holiday). Timeout 8s; on failure skip.
7. **Score & write**: rank everyone still eligible, including score 0; take **K = 3**. Zero candidates -> `NoMatchFound` with `rejection_summary` (counts per check). Otherwise `MatchesProposed`. Both events go through `ProposeMatchCommand` (`app/commands/match_commands.py`). The agent does not approve or assign.

`exemption_links.volunteer_id` is `users.id`. `task_assignments.volunteer_id` and `volunteer_unavailability.volunteer_id` are `volunteer_profiles.id`. Comparing a profile id to an exemption row will not exclude anyone.

Schedule overlap (check 9): two tasks may overlap only when **both** are `PARALLEL_OK`. With time windows, compare the intervals on the same date. Without a window, two exclusive tasks (and `UNKNOWN`, which is exclusive) on the same date overlap; a `PARALLEL_OK` task is not blocked by the date alone.

HITL: graph **stops** after writing proposals. Approve/reject is only HTTP/admin.

### 2.1 LLM role and model

The score is computed by deterministic code (section 3), never by the LLM. The LLM is used for exactly two things:

1. Interpret the Tavily result and return `unsafe_travel: true|false` (structured JSON output).
2. Write the `ai_rationale` text (max 500 chars) from the already-computed score components.

The LLM never ranks candidates, never changes a score, and never dispatches commands; the graph does.

Model configuration (environment variables, documented in `.env.example`):

| Variable | Meaning |
| :--- | :--- |
| `LLM_PROVIDER` | `openai` or `ollama` |
| `LLM_MODEL` | Model name for the chosen provider |
| `OLLAMA_BASE_URL` | Only for `ollama` (for example a Docker container); default `http://localhost:11434` |

The chosen provider and model must be written in the project `README.md`.

If the LLM call fails or exceeds 10s: use a template rationale (top two score components + travel summary) and `unsafe_travel = false`. A failed LLM call never blocks proposals.

## 3. Score formula (`ai_score` 0-100)

```
score = 100 * (
  0.45 * cosine_similarity_clipped   # 0..1 from Chroma
  + 0.20 * skill_overlap               # |intersection| / |required| after Hebrew/English aliases (1 if required empty)
  + 0.15 * travel_feasibility          # 1 if same city; 0.6 if tool distance < 40km; else 0.2
  + 0.10 * urgency_fit                 # EMERGENCY 1.0, HIGH 0.85, NORMAL 0.7, LOW 0.55 (availability already filtered)
  + 0.05 * vehicle_fit                 # 1 if not required or has_vehicle
  + 0.05 * frequency_bonus             # ON_DEMAND 1.0, WEEKLY 0.9, BIWEEKLY 0.8, MONTHLY 0.7
)
```

If `preferred_date` < today: multiply by `0.85`. Rationale <= 500 chars: top two score components + travel summary + `web_lookup=ok|skipped`.

Chroma returns cosine **distance**. Convert with `clip(1 - distance, 0, 1)` and feed that into the formula. Persist the 0–100 score. Never persist the distance, and never let the LLM replace the number.

## 4. Vector upsert

Collection `volunteer_resumes`: id = `volunteer_profile.id`, document = `experience + " " + skills_json`, metadata = `{user_id, primary_city, has_vehicle}`.

On `VolunteerProfileEnabled` and `VolunteerProfileUpdated`: upsert. On deactivate: delete the vector (`delete_resume`). Access only through `app/infrastructure/vector_store.py`, which embeds with `EMBEDDING_PROVIDER` (`openai` → `text-embedding-3-small`, `local` → `all-MiniLM-L6-v2`) and talks to the Chroma **HTTP** server. If `EMBEDDING_PROVIDER=openai` but `OPENAI_API_KEY` is empty or still the `.env.example` placeholder, use the local model instead of calling OpenAI. A configured key whose API then fails is not this fallback: retry 3 times and leave the request `PENDING_REVIEW`. Do not open `./chroma_db` or any other local Chroma directory from the agent or from Flask.

## 5. MCP Studio tools (NFR 10)

### 5.1 `CalculateTravelContextTool`

```json
{
  "name": "CalculateTravelContextTool",
  "description": "Estimate travel feasibility between volunteer primary city and request city.",
  "inputSchema": {
    "type": "object",
    "required": ["volunteer_city", "request_city", "resource_type"],
    "properties": {
      "volunteer_city": { "type": "string" },
      "request_city": { "type": "string" },
      "resource_type": { "type": "string", "enum": ["PHYSICAL_PRESENCE", "EQUIPMENT_LOAN", "FLEXIBLE_REMOTE"] }
    }
  }
}
```

Output: `{ "same_city": bool, "estimated_km": number | null, "feasibility": number, "note": string }`.  
If `FLEXIBLE_REMOTE`, return `feasibility = 1` without distance.

### 5.2 `CheckVolunteerCapacityTool`

```json
{
  "name": "CheckVolunteerCapacityTool",
  "description": "Reject volunteers over max_active_tasks or with overlapping PHYSICAL_PRESENCE windows.",
  "inputSchema": {
    "type": "object",
    "required": ["volunteer_id", "request_id"],
    "properties": {
      "volunteer_id": { "type": "string", "format": "uuid" },
      "request_id": { "type": "string", "format": "uuid" }
    }
  }
}
```

Output: `{ "eligible": bool, "reason": string | null }`. `reason` is `capacity` or `schedule_overlap`.

`volunteer_id` is `volunteer_profiles.id`. Capacity follows the stored concurrency type, and `UNKNOWN` is evaluated as `EXCLUSIVE`.

Overlap follows system-spec 4.4: allowed only when both the request and the assigned task are `PARALLEL_OK`. Windows (`preferred_time_from`/`to`, or `estimated_duration_min` from a start time) are compared on the same date. Without a window, two exclusive tasks on the same date overlap; a `PARALLEL_OK` task is not blocked by the date alone. `FLEXIBLE_REMOTE` does not change that rule; concurrency type does. Remote work still counts toward `max_parallel_tasks`.

## 6. External MCP (NFR 9)

- **Tavily:** search query `"{request_city} transit disruption OR municipal emergency {today_iso}"`. Used only as context in rationale, not as a hard filter unless the LLM flags `unsafe_travel` (then feasibility *= 0.5).
- **Gmail:** see 6.1. Mail is a post-commit side effect: it is sent after the events are committed, and a failed send never rolls back or blocks an event.

### 6.1 Gmail notifier design

Code lives behind one small interface, so commands never see MCP:

```python
class Notifier(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...
```

| Class | Used when |
| :--- | :--- |
| `GmailMcpNotifier` | `MAIL_ENABLED=true`, not testing, and `GMAIL_MCP_CLIENT_ID` / `GMAIL_MCP_CLIENT_SECRET` are set |
| `NullNotifier` | Everything else (tests, mail off, Gmail not configured) |
| `FakeNotifier` | Tests; records the sent messages |

**MCP server.** `GmailMcpNotifier` is an MCP client (official `mcp` Python SDK, stdio transport). For each mail it launches `npx -y @artymclabin/gmail-mcp@1.2.3` and calls the `send_email` tool with `{ to: [address], subject, body, mimeType: "text/plain" }`. This is the maintained fork of the unmaintained `GongRzhe/Gmail-MCP-Server`; it accepts a Desktop-type OAuth client, encodes Hebrew subjects (RFC 2047) and bodies (UTF-8), and can be limited to the `gmail.send` scope. The version is pinned on purpose. Node.js 18+ must be on `PATH`.

**Credentials.** The server reads a keys file and writes its token file; both live in `GMAIL_TOKEN_DIR` (default `.secrets/gmail`, git-ignored): `gcp-oauth.keys.json` is generated at runtime from the environment variables, and `credentials.json` is written by `python -m scripts.gmail_auth` (one-time browser consent, scope `gmail.send` only). The client id and secret are never passed on a command line, never logged, and not part of `repr(Config)`. In Google's *Testing* mode a refresh token expires after 7 days; the notifier then logs `notify_failed` with a hint to re-run the auth script.

**Behaviour.** Each send has a 10 s timeout and 3 attempts (backoff 0.5 s, then 1 s). A permanent problem (no stored token, expired token, malformed address, no Node) is not retried. After the last attempt the notifier logs `notify_failed` (masked address, scrubbed reason) and returns; it never raises into a command. In Flask and in the agent the service runs sends on one background worker thread, so an HTTP response or the agent poll is never held by Gmail. There is no durable retry queue in v1: a crash between commit and send loses that one mail.

**Recipients.** Only addresses that come from the `users` projection: the assigned volunteer (`volunteer_profiles.user_id`), the requester (`help_requests.requester_id`), and admins (`users.is_admin` and `is_active`). `ADMIN_NOTIFY_EMAIL` narrows the admin list to one address, and only if that address belongs to an active admin; otherwise every active admin is mailed.

**Copy.** Message text is built by a pure module (`app/infrastructure/notifications.py`), Hebrew by default and English on request, using the `app/i18n.py` category and urgency labels. Mails never contain a phone number or an address; the volunteer sees them on the tasks page after logging in.

| Event | Process | Recipients |
| :--- | :--- | :--- |
| `AssignmentApproved` | Flask | assigned volunteer and requester |
| `AssignmentOverridden` | Flask | assigned volunteer and requester |
| `HelpRequestCancelled`, only when the status was `ASSIGNED` | Flask | the assigned volunteer |
| `TaskReleased` | Flask | requester and admin(s). The notifier method `task_released` exists; no `ReleaseTaskCommand` calls it yet |
| `MatchesProposed` | **agent** | admin(s): "New match proposal waiting for review" with request id, category, city, urgency, the top candidates (name and `ai_score`), and a link to `/requests/<id>` |
| `NoMatchFound` | **agent** | admin(s): the same facts plus the `rejection_summary` |

**Idempotency of the admin mail.** `ProposeMatchCommand` is idempotent per `(request_id, match_attempt)`. The mail is sent only by the run that actually appended `MatchesProposed` or `NoMatchFound`; a repeated poll of the same attempt is a no-op and sends nothing. A proposal that fails to commit (for example a concurrency conflict) sends nothing.