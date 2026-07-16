# Plan: Simple LangGraph Agent → Databricks App + AI Gateway, state in Lakebase

## Context

You want a **very simple LangChain + LangGraph** app, deployed into your **FEVM CME "conde"** workspace, that:
1. Runs as a **Databricks App** (web UI + FastAPI backend).
2. Persists graph state / conversation checkpoints in a **Lakebase Postgres table** (Lakebase = "lake base post table").
3. Is also **registered/"locked" as an agent in AI Gateway** (Mosaic AI Gateway) — i.e. logged to MLflow, registered to Unity Catalog, and deployed to a Model Serving endpoint governed by AI Gateway (guardrails, rate limits, usage tracking).

The working directory `langgraph template app/` is currently **empty**, so we scaffold from scratch. Target workspace profile: **`Fevm-fevm-conde`**. Sample agent: a **LangGraph `create_react_agent` with one simple tool**, checkpointed to Lakebase. Scope: **build + deploy end-to-end** against the workspace.

This gives one LangGraph agent definition reused two ways: (a) served interactively behind a Databricks App with durable Lakebase state, and (b) governed/served as a UC-registered agent endpoint behind AI Gateway.

## Prerequisite (you run this once — interactive SSO)

The CLI upgraded to v1.0.0 and invalidated the cached token. Re-auth before deploy:

```
! databricks auth login --profile Fevm-fevm-conde
```

Confirm with `databricks current-user me -p Fevm-fevm-conde`. (I verified: CLI v1.0.0, `databricks postgres` subcommand present, `uv`/`node`/`npm`/`python3` installed.)

## Architecture

```
Browser ──► Databricks App (React SPA)
               │  /api/chat
               ▼
          FastAPI (app.py)  ──►  LangGraph create_react_agent
               │                    │  ChatOpenAI → AI_GATEWAY_URL (Databricks FM, e.g. claude-sonnet-4-5)
               │                    │  one tool (e.g. calculator / lookup)
               ▼                    ▼
      psycopg ConnectionPool ──► Lakebase Postgres  (LangGraph PostgresSaver checkpoints + app tables)
      (OAuth token per conn)

Parallel path — governed agent:
  agent.py (same graph, wrapped as MLflow ChatAgent)
     └► mlflow.pyfunc.log_model(resources=[DatabricksServingEndpoint(...)])
        └► mlflow.register_model → Unity Catalog (catalog.schema.langgraph_sample_agent)
           └► databricks.agents.deploy(...) → Model Serving endpoint
              └► serving_endpoints.update_config(ai_gateway={guardrails, rate_limits, usage_tracking})
```

## Project structure to create

```
langgraph template app/
├── app.yaml                  # Databricks App launch (uvicorn on :8000) + env
├── pyproject.toml            # uv-managed deps (native pyproject support, no requirements.txt)
├── .gitignore                # exclude node_modules/, .venv/, __pycache__ (NOT frontend/dist)
├── README.md                 # structure + deploy runbook
├── app.py                    # FastAPI entry: lifespan opens psycopg pool, mounts /api, serves SPA
├── server/
│   ├── config.py             # dual-mode auth: IS_DATABRICKS_APP detection, WorkspaceClient()
│   ├── db.py                 # OAuthConnection(psycopg.Connection) + ConnectionPool (max_lifetime=2700)
│   ├── graph.py              # LangGraph create_react_agent + PostgresSaver checkpointer + 1 tool
│   └── routes/chat.py        # POST /api/chat (sync def → threadpool), thread_id → Lakebase state
├── agent.py                  # MLflow ChatAgent wrapper around the SAME graph (for AI Gateway path)
├── deploy_agent.py           # log → register to UC → agents.deploy → configure ai_gateway block
└── frontend/                 # Vite React + TS minimal chat UI (built to frontend/dist)
```

## Key implementation details (reuse documented patterns)

**LangGraph agent** (`server/graph.py`) — the ReAct wrapper pattern from `agents.yaml` §9:
- `from langgraph.prebuilt import create_react_agent`
- LLM via LangChain OpenAI-compatible client pointed at the injected **`AI_GATEWAY_URL`** (`https://<workspace-id>.ai-gateway.cloud.databricks.com/mlflow/v1`), API key = app SP OAuth token. Model e.g. `databricks-claude-sonnet-4-5`.
- One simple `@tool` (e.g. `calculator(expression: str)`).
- `mlflow.langchain.autolog()` for tracing.

**Lakebase state** — LangGraph `langgraph.checkpoint.postgres.PostgresSaver` bound to the psycopg pool from `server/db.py`. This is what makes conversation state durable in a Lakebase table (`checkpointer.setup()` creates the LangGraph checkpoint tables on first run). Follows the official Apps `server/db.py` pattern from `databricks-apps/SKILL.md`:
- `OAuthConnection(psycopg.Connection)` whose `connect()` mints a fresh token via `w.postgres.generate_database_credential(endpoint=os.environ["ENDPOINT_NAME"])`.
- `ConnectionPool(open=False, max_lifetime=2700)`; opened in FastAPI `lifespan` with `pool.open(wait=True, timeout=30.0)`.
- Route handlers are **sync `def`** (psycopg sync → FastAPI threadpool). `%s` param style.

**app.yaml** — Option B (attach Lakebase + serving endpoint as App resources in UI so `PGHOST/PGUSER/PGPORT/PGDATABASE` auto-inject); `command` runs `uvicorn app:app --host 0.0.0.0 --port 8000`; env carries `ENDPOINT_NAME`, `SERVING_ENDPOINT`, `AI_GATEWAY_URL`.

**AI Gateway agent** (`agent.py` + `deploy_agent.py`) — from `agents.yaml` §5/§9 + `ai_gateway.yaml`:
- `agent.py`: `mlflow.pyfunc.ChatAgent` subclass whose `predict()` calls `graph.invoke(...)` and returns `ChatAgentResponse`; `mlflow.models.set_model(...)`.
- `deploy_agent.py`:
  - `mlflow.set_registry_uri("databricks-uc")`
  - `mlflow.pyfunc.log_model(python_model="agent.py", pip_requirements=[...langgraph, databricks-langchain...], resources=[DatabricksServingEndpoint("databricks-claude-sonnet-4-5")])`
  - `mlflow.register_model(model_uri, name="<catalog>.<schema>.langgraph_sample_agent")`
  - `from databricks import agents; agents.deploy(model_name=..., model_version=1, scale_to_zero=True)`
  - `w.serving_endpoints.update_config(name=..., ai_gateway={guardrails: {input/output pii BLOCK}, rate_limits: [...], usage_tracking_config: {enabled: True}})` — this is the "lock as agent in AI Gateway" step.

## Provisioning / deploy sequence (I run these against `Fevm-fevm-conde`)

1. **Scaffold** — `uv init`; `uv add fastapi uvicorn "psycopg[binary,pool]" databricks-sdk langgraph langchain langchain-openai databricks-langchain "langgraph-checkpoint-postgres" mlflow databricks-agents pydantic`; Vite React frontend.
2. **Lakebase** — `databricks postgres create-project langgraph-sample ... -p Fevm-fevm-conde`; wait for branch READY + endpoint ACTIVE; capture host + `ENDPOINT_NAME`; `CREATE DATABASE langgraph_app;` and let `PostgresSaver.setup()` create checkpoint tables (verify with a `\dt`).
3. **App** — `databricks apps create langgraph-sample -p Fevm-fevm-conde`; `databricks sync` (excluding node_modules/.venv); build frontend + import dist; add **Database** (Can connect) + **Model serving endpoint** (Can query) resources in UI; `databricks apps deploy ... -p Fevm-fevm-conde`; `databricks apps get` for URL.
4. **AI Gateway agent** — pick UC `catalog.schema` (confirm with you); run `deploy_agent.py`; apply `ai_gateway` config block; verify endpoint READY.

## Verification (end-to-end)

- **Local**: `uv run uvicorn app:app` with `-p Fevm-fevm-conde`; POST `/api/chat` twice with same `thread_id`; confirm the second call remembers context (proves Lakebase checkpointing). Inspect Lakebase: `psql ... -c "SELECT * FROM checkpoints LIMIT 5;"`.
- **App**: open the App URL, chat in the UI, confirm tool call + memory across turns; check `<app-url>/logz` for errors.
- **AI Gateway agent**: query the serving endpoint (SDK or curl through the Gateway URL); confirm a response, then verify governance is active — a row appears in **`system.ai_gateway.usage`** (canonical Gateway-routed audit table; ~10–30 min lag) and the endpoint's `GET /api/2.0/serving-endpoints/<name>` shows the `ai_gateway` block.

## Open items to confirm before/while executing

- Unity Catalog `catalog.schema` for the registered agent (I'll ask once auth is live and I can list catalogs).
- Exact foundation model name available in this workspace (I'll list serving endpoints; default `databricks-claude-sonnet-4-5`).

## Notes / caveats

- No standalone `fevm` provisioning skill is installed locally; the `Fevm-fevm-conde` workspace already exists (profile present), so no new workspace provisioning is needed — must be a **serverless** FEVM workspace for Apps + Lakebase.
- Use **Lakebase Autoscaling** tier (`databricks postgres`), OAuth tokens expire in 1h → `max_lifetime=2700` on the pool.
- If LangChain tool-calling emits OpenAI-only fields (e.g. `strict`) that the Gateway rejects against Claude, apply the documented httpx request-hook workaround to strip them.
