# LangGraph Agent → Databricks App + Lakebase + Unity AI Gateway

A **template** for a simple LangChain + LangGraph agent that:

1. Runs as a **Databricks App** (FastAPI backend + minimal web UI).
2. **Persists conversation state in Lakebase** (Postgres) via LangGraph's `PostgresSaver` — durable memory across turns and restarts.
3. Routes all LLM calls through **Unity AI Gateway** (governance: rate limits, usage tracking, guardrails).
4. Can be **logged/registered as a governed agent in Unity AI Gateway** (UC model → serving endpoint) so it appears on the **Agents** inventory alongside your other agents.

The same LangGraph graph (`server/graph.py`) is reused everywhere, so there's one place to change agent behavior.

> **Consuming the agent from outside Databricks?** See **[USAGE.md](USAGE.md)** — it
> shows both ways: (1) call the FastAPI app directly (stateful, Lakebase memory) and
> (2) call the registered agent serving endpoint (governed, on the AI Gateway Agents
> inventory), with verified curl / Python examples.
>
> **Want to just test that both ways work?** Run `test_both_ways.py` — exact
> step-by-step instructions are in **[RUN_TEST.md](RUN_TEST.md)** (assumes no prior setup).

---

## What you edit to reuse this template

**Just one file for config:** copy `.env.example` → `.env` and fill it in. Every
workspace-specific value lives there. To change *what the agent does*, edit
`server/graph.py` (the tool + prompt). Nothing else needs touching.

```
.env.example        <- copy to .env, fill in  (THE config file)
server/graph.py     <- the agent: LLM + tools + prompt  (edit behavior here)
server/db.py        <- Lakebase pool            (rarely changes)
server/config.py    <- auth + env resolution    (rarely changes)
server/routes/chat.py  <- POST /api/chat        (rarely changes)
app.py              <- FastAPI entry            (rarely changes)
agent.py            <- MLflow ChatAgent wrapper (for gateway registration)
deploy_agent.py     <- LOG AGENT INTO AI GATEWAY  (see Part C)
app.yaml            <- Databricks App config (env for the deployed app)
static/index.html   <- no-build chat UI (works without npm)
```

---

## Prerequisites (one-time)

- A **serverless FEVM/Databricks workspace** (needed for Apps + Lakebase).
- Databricks CLI **v0.285.0+** (`brew upgrade databricks`).
- `uv` (Python package manager).
- Log in: `databricks auth login --profile <your-profile>`

> **Package access note:** if public PyPI is blocked on your machine, install via
> the Databricks proxy: prefix any `uv` command with
> `UV_INDEX_URL="https://pypi-proxy.cloud.databricks.com/simple"`.
> This template ships a no-build `static/` UI so **npm is not required**.

```bash
cp .env.example .env          # then edit .env
set -a; source .env; set +a   # load it into your shell
UV_INDEX_URL="https://pypi-proxy.cloud.databricks.com/simple" uv sync
```

---

## Part A — Provision Lakebase (the state store)

```bash
# 1. Create the Lakebase Autoscaling project (auto-creates production branch + primary endpoint)
databricks postgres create-project langgraph-sample \
  --json '{"spec": {"display_name": "LangGraph Sample"}}' --no-wait -p "$DATABRICKS_PROFILE"

# 2. Wait until READY / ACTIVE
databricks postgres list-branches  projects/langgraph-sample                    -p "$DATABRICKS_PROFILE" -o json | jq '.[].status.current_state'
databricks postgres list-endpoints projects/langgraph-sample/branches/production -p "$DATABRICKS_PROFILE" -o json | jq '.[].status.current_state'

# 3. Grab the host → put it in .env as PGHOST
databricks postgres list-endpoints projects/langgraph-sample/branches/production -p "$DATABRICKS_PROFILE" -o json | jq -r '.[0].status.hosts.host'

# 4. Create the app database (token = short-lived OAuth credential)
TOKEN=$(databricks postgres generate-database-credential projects/langgraph-sample/branches/production/endpoints/primary -p "$DATABRICKS_PROFILE" -o json | jq -r '.token')
PGPASSWORD=$TOKEN psql "host=$PGHOST port=5432 dbname=databricks_postgres user=$PGUSER sslmode=require" -c "CREATE DATABASE langgraph_app;"
```

**How the state gets "locked" in Lakebase:** `server/db.py` opens a psycopg pool whose
`OAuthConnection` mints a fresh Lakebase token per connection (via
`generate_database_credential`, recycled every 45 min before the 1-hour expiry).
`server/routes/chat.py` wraps the graph with LangGraph's `PostgresSaver`, so every turn's
checkpoint is written to Postgres keyed by `thread_id`. Same `thread_id` → memory persists.

---

## Part B — Run & test locally

```bash
set -a; source .env; set +a
uv run uvicorn app:app --reload --port 8000
curl -sX POST localhost:8000/api/setup        # create checkpoint tables (once)

# Two turns, same thread → proves Lakebase memory
TID=$(curl -sX POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"What is 23 * 19? Use the calculator tool."}' | tee /dev/stderr | jq -r .thread_id)
curl -sX POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d "{\"message\":\"What did I just ask?\",\"thread_id\":\"$TID\"}"
```

Verify checkpoints landed: `psql ... -c "SELECT count(*) FROM checkpoints;"`

---

## Part C — Log the agent into Unity AI Gateway ⭐ (the important part)

This is what makes the agent a **governed, versioned asset on the Unity AI Gateway
Agents inventory**. `deploy_agent.py` does it in three steps:

1. **Log** the MLflow `ChatAgent` (wraps the same graph) with `code_paths=["server/"]`
   so the graph is packaged, and `resources=[DatabricksServingEndpoint(...)]` so the
   gateway endpoint it depends on is declared.
2. **Register** it to Unity Catalog as a model (`<catalog>.<schema>.langgraph_sample_agent`),
   with retry-on-transient-UC-error.
3. **Deploy** it with `databricks.agents.deploy()` → a Model Serving endpoint.
   **After this, the agent appears in the Unity AI Gateway → Agents tab automatically.**
   Governance (rate limits, guardrails, usage tracking) is then configured in the
   Unity AI Gateway UI. *(Note: the old `put_ai_gateway` API is not used for agent endpoints.)*

```bash
set -a; source .env; set +a
export DATABRICKS_CONFIG_PROFILE="$DATABRICKS_PROFILE"   # disambiguates MLflow auth if multiple profiles match your host

# create the UC schema once
databricks schemas create "$UC_SCHEMA" "$UC_CATALOG" -p "$DATABRICKS_PROFILE"

# log → register → deploy  (reads UC_CATALOG / UC_SCHEMA from .env)
uv run python deploy_agent.py
```

Confirm it's registered and on the Agents inventory:

```bash
databricks model-versions list "$UC_CATALOG.$UC_SCHEMA.langgraph_sample_agent" -p "$DATABRICKS_PROFILE" -o json | jq -r '.[] | "v\(.version) \(.status)"'
```
Then open **Serving → Unity AI Gateway → Agents** in the UI — your agent is listed there.

> **If registration times out:** it's re-runnable and won't re-log. The model is logged
> once to an MLflow experiment; to skip re-logging on a retry pass its id via
> `register_from_run.py --model-id <m-...>`. UC write timeouts (`UC-TKTLK`) are transient
> platform issues — just re-run.

---

## Part D — Deploy the app to Databricks Apps

```bash
set -a; source .env; set +a
EMAIL=$(databricks current-user me -p "$DATABRICKS_PROFILE" -o json | jq -r '.userName')
DEST="/Users/$EMAIL/langgraph-sample"

# 1. Fill app.yaml env (PGHOST/PGUSER/ENDPOINT_NAME/UAIG_ENDPOINT) with your values.
# 2. Create the app
databricks apps create langgraph-sample -p "$DATABRICKS_PROFILE"

# 3. Sync source (exclude heavy/dev dirs; do NOT ship uv.lock — proxy wheel URLs rotate)
databricks sync . "$DEST" --exclude node_modules --exclude .venv --exclude __pycache__ \
  --exclude .git --exclude frontend --exclude mlruns --exclude uv.lock -p "$DATABRICKS_PROFILE"

# 4. Authorize the app SP for the gateway endpoint (governed LLM calls)
databricks apps update langgraph-sample --json '{
  "resources": [{"name":"gateway_endpoint",
    "serving_endpoint":{"name":"databricks-claude-sonnet-5","permission":"CAN_QUERY"}}]
}' -p "$DATABRICKS_PROFILE"

# 5. Deploy + get URL
databricks apps deploy langgraph-sample --source-code-path "/Workspace/$DEST" -p "$DATABRICKS_PROFILE"
databricks apps get langgraph-sample -p "$DATABRICKS_PROFILE" | jq -r '.url'
```

Also attach the **Lakebase database** as an app resource (Compute → Apps → Edit → add
Database, "Can connect"), or set `PGHOST/PGUSER/PGPORT/PGDATABASE` explicitly in `app.yaml`
(this template uses the explicit form). Logs: append `/logz` to the app URL.

---

## Two ways to govern with Unity AI Gateway (pick per agent)

| | **App routes through gateway** (Part D) | **Register as UC agent** (Part C) |
|---|---|---|
| LLM calls governed | ✅ | ✅ |
| Cost | app compute only | + scale-to-zero serving endpoint |
| Shows on **Agents inventory** | as a caller (request-tagged) | ✅ as a first-class versioned agent |
| Use when | low-ops internal app | needs versioning/rollback + central inventory |

You do **not** need the serving endpoint just to get governance — routing via
`ChatDatabricks(use_ai_gateway=True)` is enough. Register as a UC agent (Part C) when you
want it on the managed Agents inventory with lifecycle + rollback.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Error installing packages` on deploy | Don't ship `uv.lock`; ship `requirements.txt` with pinned versions. |
| App 502 / `ImportError: ExecutionInfo` | LangGraph version skew — keep the pinned set in `requirements.txt`. |
| `cannot get token: multiple profiles match host` | Set `DATABRICKS_CONFIG_PROFILE`; MLflow URIs pinned to `databricks://<profile>`. |
| `CREATE INDEX CONCURRENTLY cannot run inside a transaction` | `/api/setup` opens the conn with `autocommit=True` (already handled). |
| Model registration times out (`UC-TKTLK`) | Transient UC issue — re-run `deploy_agent.py` (or `register_from_run.py --model-id`). |
| `temperature not supported` | Reasoning models (claude-sonnet-5) reject it — `build_llm()` omits it. |
