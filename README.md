# KindBridge

KindBridge is a civic assistance matching platform: requesters submit help requests, volunteers offer skills and availability, and a background Deep Agent proposes matches for admin review.

## Stack

- **Web:** Python 3.11+, Flask 3, Jinja2 + JSON APIs
- **Architecture:** MVC + CQRS + Event Sourcing
- **DB:** SQL Server (Somee); ChromaDB as a sidecar vector store
- **Agent:** separate OS process (`python -m agent.main`), Deep Agents / LangGraph
- **MCP:** Tavily Search, Gmail, plus custom MCP Studio tools
- **Auth:** JWT cookies
- **Roles:** Requester, Volunteer, Admin (dispatcher)

## LLM

- Provider: `<openai | ollama>` (`LLM_PROVIDER`)
- Model: `<model name>` (`LLM_MODEL`)
- Embeddings: `<text-embedding-3-small | all-MiniLM-L6-v2>`

The LLM only interprets Tavily results and writes the match rationale. Scores are computed by deterministic code (see [docs/agent-and-mcp.md](docs/agent-and-mcp.md) section 2.1).

## Documentation

| Document | Purpose |
| :--- | :--- |
| [docs/README.md](docs/README.md) | Specification index (SDAD) |
| [docs/system-spec.md](docs/system-spec.md) | Product, roles, entities, state machines, commands |
| [docs/architecture.md](docs/architecture.md) | Stack, folder layout, HTTP, deployment |
| [docs/event-sourcing.md](docs/event-sourcing.md) | Event store, events, rebuild |
| [docs/agent-and-mcp.md](docs/agent-and-mcp.md) | Deep Agent, RAG, score, MCP tools |
| [docs/ui-ux.md](docs/ui-ux.md) | Screens mapped to course 4.1-4.5 |
| [docs/test-plan.md](docs/test-plan.md) | Endpoint and edge-case tests |
| [docs/rules-and-skills.md](docs/rules-and-skills.md) | Skills.sh Flask skill, custom skill, Cursor rule |

Authoritative folder layout: [docs/architecture.md](docs/architecture.md) section 2.

## Coding-agent configuration

| Kind | Path / command |
| :--- | :--- |
| Project rule | [`.cursor/rules/kindbridge-cqrs.mdc`](.cursor/rules/kindbridge-cqrs.mdc) |
| Custom skill | [`.cursor/skills/volunteer-semantic-matching/SKILL.md`](.cursor/skills/volunteer-semantic-matching/SKILL.md) |
| Imported skill | `npx skills add https://github.com/terminalskills/skills --skill flask` (installed at `<FILL IN path>`) |

## Local run

**One-time setup**

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` (`copy` on Windows CMD, `cp` on macOS/Linux) and fill in the values.

Create the schema by running `db/schema.sql` on the SQL Server database, then create the first admin (uses `ADMIN_BOOTSTRAP_EMAIL`):

```bash
python db/seed.py
```

**Run (three terminals, activate the venv in each)**

```bash
chroma run --path ./chroma_data     # terminal 1: vector DB
flask --app app run                 # terminal 2: web app
python -m agent.main                # terminal 3: matching agent
```

**Tests**

```bash
pytest
```

## Team

_TODO: add team members._