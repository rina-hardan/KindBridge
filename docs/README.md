# KindBridge specification (SDAD)

Canonical product name: **KindBridge**. Ignore older “AidSync” wording.

| Document | Purpose |
| :--- | :--- |
| [system-spec.md](system-spec.md) | Product, roles, entities, state machines, commands |
| [architecture.md](architecture.md) | Flask MVC + CQRS, stack, HTTP, deployment |
| [event-sourcing.md](event-sourcing.md) | Event store, events, rebuild |
| [agent-and-mcp.md](agent-and-mcp.md) | Deep Agent, RAG, score, MCP tools |
| [ui-ux.md](ui-ux.md) | Screens mapped to course 4.1–4.5 |
| [test-plan.md](test-plan.md) | Endpoint and edge-case tests |
| [rules-and-skills.md](rules-and-skills.md) | Skills.sh Flask skill, custom skill, Cursor rule |

Cursor rule: `/.cursor/rules/kindbridge-cqrs.mdc`  
Custom skill: `/.cursor/skills/volunteer-semantic-matching/SKILL.md`  
Imported skill: `npx skills add https://github.com/terminalskills/skills --skill flask`
