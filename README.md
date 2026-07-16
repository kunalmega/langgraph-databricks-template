# LangGraph Agent → Databricks App + Lakebase + Unity AI Gateway

A **template** for a simple LangChain + LangGraph agent that:

1. Runs as a **Databricks App** (FastAPI backend + minimal web UI).
2. **Persists conversation state in Lakebase** (Postgres) via LangGraph's `PostgresSaver` — durable memory across turns and restarts.
3. Routes all LLM calls through **Unity AI Gateway** (governance: rate limits, usage tracking, guardrails).
4. Can be **logged/registered as a governed agent in Unity AI Gateway** (UC model → serving endpoint) so it appears on the **Agents** inventory alongside your other agents.

The same LangGraph graph (`server/graph.py`) is reused everywhere, so there's one place to change agent behavior.

> **New here / handed this repo? Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first.**
> It explains in plain language how a message flows through `app.py`, what `agent.py`
> does, which model is used and **how to swap the LLM (one line)**, and the difference
> between routing the LLM through the gateway vs registering the agent on the Gateway
> Agents page — with the exact file+line to look at for each.

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
.env.example        <- copy to .env, fill in  (THE config file — includes LLM choice)
server/graph.py     <- the agent: LLM + tools + prompt  (edit behavior here)
server/db.py        <- Lakebase pool            (rarely changes)
server/config.py    <- auth + which model       (rarely changes)
server/routes/chat.py  <- POST /api/chat        (rarely changes)
app.py              <- FastAPI web server       (rarely changes)
agent.py            <- MLflow ChatAgent wrapper (for gateway registration)
deploy_agent.py     <- LOG AGENT INTO AI GATEWAY  (see Part C)
app.yaml            <- Databricks App config (env for the deployed app)
static/index.html   <- no-build chat UI (works without npm)
setup/01_provision_lakebase.sh  <- CREATES Lakebase (project + database)
setup/02_grant_app_sp.sh        <- grants the app SP all access it needs
setup/03_deploy_app.sh          <- creates + syncs + deploys the app
docs/ARCHITECTURE.md            <- how it all works, in plain language
```

**To swap the LLM:** change the single value `UAIG_ENDPOINT` in `.env` (local) or
`app.yaml` (deployed). No code change. See [docs/ARCHITECTURE.md §4](docs/ARCHITECTURE.md).

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

## Part A — Provision Lakebase (the state store) — **one script**

This is the code that **creates** Lakebase. It creates the project + database and prints
the two values you paste back into `.env`:

```bash
set -a; source .env; set +a
bash setup/01_provision_lakebase.sh
# -> prints PGHOST and ENDPOINT_NAME. Paste both into .env, then re-source it:
set -a; source .env; set +a
```

(The script is idempotent — safe to re-run; it skips anything that already exists. It
wraps the `databricks postgres create-project` / `create database` CLI calls so you don't
run them by hand.)

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

## Part D — Deploy the app to Databricks Apps — **scripts**

Deploying involves three ordered steps (the app SP must exist before you can grant it):

```bash
set -a; source .env; set +a

# 1. Create the app + sync + bind gateway resource. Prints the app SP client id.
bash setup/03_deploy_app.sh
#    -> copy the printed "APP SP client id"

# 2. Grant that SP everything it needs (Postgres role + table grants + gateway CAN_QUERY)
APP_SP_CLIENT_ID=<paste-from-step-1> bash setup/02_grant_app_sp.sh

# 3. Set PGUSER=<app SP client id> in app.yaml, then re-run the deploy to pick it up
bash setup/03_deploy_app.sh
```

`setup/02_grant_app_sp.sh` is the code that **provides all access to the app's service
principal** — the Postgres role, the table/schema grants, and gateway `CAN_QUERY` — the
things that otherwise fail with `invalid_client` / 403. Logs: append `/logz` to the app URL.

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
