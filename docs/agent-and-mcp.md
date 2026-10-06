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

1. **Load**: request projection + required skills.
2. **Retrieve (RAG)**: embed request `description + category + required_skills`; query Chroma collection `volunteer_resumes`, `top_n = 15`.
3. **Hard filter**: drop INACTIVE, TEMPORARILY_UNAVAILABLE (until), BUSY/capacity, missing vehicle, exemption, already DECLINED on this request, same person as requester.
4. **Capacity tool (MCP Studio)**: `CheckVolunteerCapacityTool`.
5. **Travel tool (MCP Studio)**: `CalculateTravelContextTool`.
6. **Web (Tavily MCP)**: city-level context (weather disruption, transit strike, municipal holiday). Timeout 8s; on failure skip.
7. **Score & write**: rank remaining; take **K = 3**; if zero -> `NoMatchFound`; else `MatchesProposed`.

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
  + 0.20 * skill_overlap               # |intersection| / |required| (1 if required empty)
  + 0.15 * travel_feasibility          # 1 if same city; 0.6 if tool distance < 40km; else 0.2
  + 0.10 * urgency_fit                 # EMERGENCY 1.0, HIGH 0.85, NORMAL 0.7, LOW 0.55 (availability already filtered)
  + 0.05 * vehicle_fit                 # 1 if not required or has_vehicle
  + 0.05 * frequency_bonus             # ON_DEMAND 1.0, WEEKLY 0.9, BIWEEKLY 0.8, MONTHLY 0.7
)
```

If `preferred_date` < today: multiply by `0.85`. Rationale <= 500 chars: top two score components + travel summary + `web_lookup=ok|skipped`.

## 4. Vector upsert

Collection `volunteer_resumes`: id = `volunteer_profile.id`, document = `experience + " " + skills_json`, metadata = `{user_id, primary_city, has_vehicle}`.

On `VolunteerProfileEnabled` and `VolunteerProfileUpdated`: upsert. On deactivate: delete vector. Access only through `app/infrastructure/vector_store.py`.

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

Output: `{ "eligible": bool, "reason": string | null }`.

Overlap: if both the new request and an ASSIGNED task have `resource_type = PHYSICAL_PRESENCE` and date/time windows intersect (null window = whole `preferred_date` or, if date null, treat as conflict if another PHYSICAL_PRESENCE is ASSIGNED that calendar day). `FLEXIBLE_REMOTE` does not conflict with physical tasks for overlap, but still counts toward `max_active_tasks`.

## 6. External MCP (NFR 9)

- **Tavily:** search query `"{request_city} transit disruption OR municipal emergency {today_iso}"`. Used only as context in rationale, not as a hard filter unless the LLM flags `unsafe_travel` (then feasibility *= 0.5).
- **Gmail:** send on `AssignmentApproved`, `AssignmentOverridden`, `HelpRequestCancelled` (if volunteer was assigned), `TaskReleased` (notify admin). These events are produced by HTTP commands, so notifications are sent by the command side (Flask process), not by the agent. Failures are retried; they do not roll back events.