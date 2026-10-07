---
name: volunteer-semantic-matching
description: Implement KindBridge volunteer-to-request matching. Use when writing ProposeMatchCommand, the Deep Agent graph in agent/, Chroma retrieval over volunteer resumes, scoring, or the MCP tools that support matching.
---

# VolunteerSemanticMatchingSkill

Product name is **KindBridge** (not AidSync). Before writing code, read the relevant specs in `docs/` (`system-spec.md`, `architecture.md`, `event-sourcing.md`, `agent-and-mcp.md`). If code and specs disagree, the specs win; update them first.

Follow this skill whenever you implement or change `ProposeMatchCommand`, `agent/graph.py`, `app/infrastructure/vector_store.py`, or the MCP Studio tools. The full design is in `docs/agent-and-mcp.md`; this file is the checklist.

## 1. Resume document (what gets embedded)

- Collection: `volunteer_resumes`.
- id = `volunteer_profile.id`.
- document = `experience + " " + skills_json`.
- metadata = `{user_id, primary_city, has_vehicle}`.
- Upsert on `VolunteerProfileEnabled` and `VolunteerProfileUpdated`; delete the vector when the volunteer is deactivated.
- Access Chroma only through `app/infrastructure/vector_store.py`.

## 2. Matching flow (one request, one match_attempt)

Order is fixed. Do not filter the whole catalogue and then cut to 3.

1. **Load** the request projection and its required skills. When `concurrency_type` is `UNKNOWN`, classify it first (`FLEXIBLE_REMOTE` → `PARALLEL_OK`, otherwise `EXCLUSIVE`, which is also how an unclassified `UNKNOWN` is treated) and store `ConcurrencyClassified`.
2. **Retrieve (RAG):** embed `description + category + required_skills` (plus requester accessibility notes when present), query `volunteer_resumes` with `top_n = 15`.
3. **Hard filter** only those 15 (drop, do not score). Checks from system-spec 4.2: inactive, self-assignment, exemption, declined on this request, unavailability period, geography, vehicle, capacity, schedule overlap. Similarity is not a check. There is no `SEMANTIC_FILTER_NO_OVERLAP`.
4. **Capacity tool:** call `CheckVolunteerCapacityTool` for each remaining candidate; drop if `eligible = false`. `UNKNOWN` uses the `EXCLUSIVE` cap.
5. **Travel tool:** call `CalculateTravelContextTool`; use its `feasibility`.
6. **Web context (Tavily MCP):** city-level disruption query, timeout 8 s. On failure skip and set `web_lookup=skipped`. If the model flags `unsafe_travel`, multiply feasibility by 0.5.
7. **Score and write:** rank everyone who survived, including a score of 0. Keep K = 3. Zero candidates means `NoMatchFound` with a rejection summary (counts per check); otherwise `MatchesProposed`.

Stop after writing. Never approve or assign; that is the admin's decision.

Ids: `exemption_links.volunteer_id` is `users.id`. Assignments and unavailability use `volunteer_profiles.id`. Overlap is allowed only when both tasks are `PARALLEL_OK`.

## 3. Score formula (`ai_score`, 0-100)

```
score = 100 * (
  0.45 * cosine_similarity_clipped   # 0..1 from Chroma
  + 0.20 * skill_overlap             # |intersection| / |required| (1 if required empty)
  + 0.15 * travel_feasibility        # 1 same city; 0.6 if < 40 km; else 0.2
  + 0.10 * urgency_fit               # EMERGENCY 1.0, HIGH 0.85, NORMAL 0.7, LOW 0.55
  + 0.05 * vehicle_fit               # 1 if not required or has_vehicle
  + 0.05 * frequency_bonus           # ON_DEMAND 1.0, WEEKLY 0.9, BIWEEKLY 0.8, MONTHLY 0.7
)
```

If `preferred_date` is before today, multiply by 0.85. The score is computed by deterministic code, not by the LLM.

## 4. Role of the LLM

- Interprets Tavily results (is travel unsafe today?).
- Writes the `ai_rationale` (max 500 characters): top two score components, travel summary, and `web_lookup=ok|skipped`.
- Never outputs raw chain-of-thought and never changes the numeric score.

## 5. Invariants

- Idempotency key `propose:{request_id}:{match_attempt}`. A duplicate Propose is a no-op.
- `MatchesProposed` payload: array of `{volunteer_id, score, rationale, rank}`, plus `match_attempt` and `k`. `volunteer_id` is `volunteer_profiles.id`. Each proposal also carries `assignment_id` so rebuild does not invent keys.
- `NoMatchFound` payload: `match_attempt`, `reason`, and `rejection_summary` (counts per hard-filter reason).
- Dispatch through the command bus only; the agent never writes SQL directly. The handler lives in `app/commands/match_commands.py`.
- SUPERSEDED proposals are not a blacklist; only DECLINED volunteers are excluded for that request.

## 6. Tests to write with the feature

- Three eligible volunteers produce K <= 3 PROPOSED rows and status `MATCH_PROPOSED`.
- Empty pool produces `NO_MATCH`.
- Tavily exception still yields a proposal with `web_lookup=skipped`.
- Same `(request_id, match_attempt)` does not duplicate assignment rows.
- Profile update changes the Chroma document (fake client assertion).