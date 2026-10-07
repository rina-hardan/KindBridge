# Rules and skills: KindBridge

Course NFR 2: at least one skill from [Skills.sh](https://skills.sh).  
Course NFR 3: at least one project Skill and one Rule.

## 1. Imported skill (Skills.sh)

**Skill:** [`flask`](https://www.skills.sh/terminalskills/skills/flask) from `terminalskills/skills`.

```bash
npx skills add https://github.com/terminalskills/skills --skill flask
```

Installed at: `<FILL IN the folder the command created, for example .agents/skills/flask or .cursor/skills/flask>`.

Use it when scaffolding Flask blueprints, app factory, and tests. It is **not** a volunteer-matching skill; matching is the custom skill below.

Do not invent catalog names such as `semantic-data-matcher`.

## 2. Project skill (custom)

Path: [`.cursor/skills/volunteer-semantic-matching/SKILL.md`](../.cursor/skills/volunteer-semantic-matching/SKILL.md)

Skill name (frontmatter `name`): `volunteer-semantic-matching`. The title inside the file is `VolunteerSemanticMatchingSkill`.

The coding agent must follow that file when implementing `ProposeMatchCommand` or the Deep Agent graph.

## 3. Project rule (Cursor)

Path: [`.cursor/rules/kindbridge-cqrs.mdc`](../.cursor/rules/kindbridge-cqrs.mdc)

Always-on for this repo (`alwaysApply: true`): CQRS separation, append-only events, layering, repository interfaces, tests per endpoint, and agent-as-separate-process.

## 4. Operational business rules (runtime)

### Privacy

`users.phone` and `requester_profiles.emergency_contact_phone` are encrypted at rest with Fernet. The ciphertext is what is stored and what enters the event payload (`phone_encrypted`, `emergency_contact_phone_encrypted`). `full_name` is stored in plaintext.

`help_requests.address` and `requester_profiles.default_address` are stored in plaintext in the table and in the event payload. Queries hide a request address from a volunteer until that volunteer is `ASSIGNED` (`AssignmentApproved` or `AssignmentOverridden`). Admins can see address and phone. A requester does not see another requester's address or phone, and sees the assigned volunteer's phone only after `ASSIGNED`.

### Availability

Skip `INACTIVE`. A non-cancelled `volunteer_unavailability` period that covers `preferred_date` (or today, when the request has no date) excludes the volunteer. `UNKNOWN` concurrency is exclusive: `current_active_tasks >= max_active_tasks` excludes them. `PARALLEL_OK` uses `current_parallel_tasks` and `max_parallel_tasks`.

### Capacity and presence

Do not propose if over the concurrency cap. Two tasks may overlap only when both are `PARALLEL_OK`. See [agent-and-mcp.md](agent-and-mcp.md) and system-spec 4.4.

### Exemptions

`ExemptionLink` permanently excludes that volunteer for that requester. Creator: admin or the volunteer. No UI undelete in v1. Creating a link while an assignment is `ASSIGNED` is rejected (409); cancel or release first.

### Cancellation

Requester (or admin) may cancel any non-terminal request. If a volunteer is `ASSIGNED`, Gmail MCP notifies them and capacity is freed. No admin approval required.

### Matching blacklist (per request)

Volunteers `DECLINED` on a request (via reject-all or release) are not proposed again for **that** `help_request.id`. They remain eligible for other requests. `SUPERSEDED` proposals are not a blacklist.