# Rules and skills: KindBridge

Course NFR 2: at least one skill from [Skills.sh](https://skills.sh).  
Course NFR 3: at least one project Skill and one Rule.

## 1. Imported skill (Skills.sh)

**Skill:** [`flask`](https://www.skills.sh/terminalskills/skills/flask) from `terminalskills/skills`.

```bash
npx skills add https://github.com/terminalskills/skills --skill flask
```

Use it when scaffolding Flask blueprints, app factory, and tests. It is **not** a volunteer-matching skill; matching is the custom skill below.

Do not invent catalog names such as `semantic-data-matcher`.

## 2. Project skill (custom)

Path: [`.cursor/skills/volunteer-semantic-matching/SKILL.md`](../.cursor/skills/volunteer-semantic-matching/SKILL.md)

Identifier: `VolunteerSemanticMatchingSkill`.

The coding agent must follow that file when implementing `ProposeMatchCommand` or the Deep Agent graph.

## 3. Project rule (Cursor)

Path: [`.cursor/rules/kindbridge-cqrs.mdc`](../.cursor/rules/kindbridge-cqrs.mdc) (markdown copy: [`.cursor/rules/kindbridge-cqrs.md`](../.cursor/rules/kindbridge-cqrs.md)).

Always-on for this repo: CQRS separation, append-only events, repository interfaces, tests per endpoint.

## 4. Operational business rules (runtime)

### Privacy

Contact fields (`full_name` not secret, but `phone` and `address` are). Encrypted at rest. Exposed to the assigned volunteer only after `AssignmentApproved`. Admins see them. Requesters never see other requesters’ phone numbers.

### Availability

Skip `TEMPORARILY_UNAVAILABLE` while `now < unavailable_until`. Skip `INACTIVE`. Treat `BUSY` as `current_active_tasks >= max_active_tasks`.

### Capacity and presence

Do not propose if over cap. Two overlapping `PHYSICAL_PRESENCE` windows are forbidden. See [agent-and-mcp.md](agent-and-mcp.md).

### Exemptions

`ExemptionLink` permanently excludes that volunteer for that requester. Creator: admin or the volunteer. No UI undelete in v1.

### Cancellation

Requester (or admin) may cancel any non-terminal request. If a volunteer is `ASSIGNED`, Gmail MCP notifies them and capacity is freed. No admin approval required.

### Matching blacklist (per request)

Volunteers `DECLINED` on a request (reject-all, release, or individual future decline) are not proposed again for **that** `help_request.id`. They remain eligible for other requests.
