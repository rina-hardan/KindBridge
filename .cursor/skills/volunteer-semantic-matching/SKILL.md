---
name: volunteer-semantic-matching
description: Rank KindBridge volunteers for a help request using Chroma RAG, hard filters, travel/capacity MCP tools, and the documented score formula. Use when implementing ProposeMatchCommand, the Deep Agent graph, or match scoring.
---

# Volunteer semantic matching

## Inputs

- `request_id` (UUID)
- `category`, `city`, `urgency`, `description`
- `required_skills` (string array)
- `resource_type`, `requires_vehicle`
- `preferred_date`, `preferred_time_from`, `preferred_time_to`, `estimated_duration_min`

## Steps

1. Validate the payload; abort on schema errors (no events).
2. Embed the request text; query Chroma collection `volunteer_resumes` for top 15.
3. Hard-filter: exemptions, **DECLINED** on this request (not SUPERSEDED), INACTIVE, unavailable_until, vehicle, capacity/overlap via `CheckVolunteerCapacityTool`, travel via `CalculateTravelContextTool`.
4. Optionally call Tavily; on failure continue with `web_lookup=skipped`.
5. Score with the formula in `docs/agent-and-mcp.md`. Sort descending. Take K=3.
6. If zero remain, emit `NoMatchFound`. Else emit `MatchesProposed` with rationale ≤ 500 characters each.
7. Never assign, never send contact details, never increment capacity — HITL only.

## Outputs

- List of `{ volunteer_id, ai_score, ai_rationale, rank_in_batch }` or empty with `NO_MATCH`.
