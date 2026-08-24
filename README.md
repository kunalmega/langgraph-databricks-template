# LangGraph Agent on Databricks — App + Lakebase + Unity AI Gateway

A **reusable template** for a simple LangChain + LangGraph agent, deployed the Databricks way:

- 🧠 **LangGraph ReAct agent** with a tool — the whole agent is one small file (`server/graph.py`).
- 🌐 **Runs as a Databricks App** — FastAPI backend + a no-build web UI.
- 💾 **Remembers conversations in Lakebase** (Postgres) via LangGraph's `PostgresSaver` — memory survives restarts.
- 🛡️ **All LLM calls routed through Unity AI Gateway** — rate limits, usage tracking, guardrails.
- 📇 **Registerable as a governed agent** on the Unity AI Gateway **Agents** inventory (`deploy_agent.py`).

One agent definition (`server/graph.py`) is reused everywhere — change it once, everything updates.

> ### 👉 New here? Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first.
> It explains, in plain language and with exact file+line references: how a message flows
> through `app.py`, what `agent.py` is, **which model is used and how to swap it (one line)**,
> and the difference between routing the LLM through the gateway vs. registering the agent.

---

## Table of contents

1. [How it fits together](#1-how-it-fits-together)
2. [What you edit to reuse it](#2-what-you-edit-to-reuse-it)
3. [Prerequisites](#3-prerequisites-one-time)
4. [Build & deploy](#4-build--deploy) — [fast path](#the-fast-path--deploy-to-a-workspace-in-two-commands-) or manual
5. [Swapping the LLM](#5-swapping-the-llm)
6. [Using the agent (two ways)](#6-using-the-agent-two-ways)
7. [File map](#7-file-map)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. How it fits together

```
                 ┌──────────────── Databricks App (a container) ────────────────┐
  user  ──HTTP──►│  app.py (FastAPI)  ──►  routes/chat.py  ──►  graph.py (agent) │
                 │                              │                    │            │
                 │                              │                    └─► LLM ─────┼─► Unity AI Gateway ─► model
                 │                              ▼                                 │
                 │                        db.py (pool)  ──►  Lakebase (Postgres)  │  save/load memory
                 └──────────────────────────────────────────────────────────────┘

  Same agent (graph.py) is ALSO packaged by agent.py + deploy_agent.py and registered
  as a governed agent on the Unity AI Gateway "Agents" page (a Model Serving endpoint).
```

---

## 2. What you edit to reuse it

| To change… | Edit… |
|---|---|
| **All config** (workspace, LLM, Lakebase names) | `.env` — created for you by `setup/00_init_env.sh`, the one config file |
| **What the agent does** (tool + prompt) | `server/graph.py` |
| **Which LLM** | one value: `UAIG_ENDPOINT` (see [§5](#5-swapping-the-llm)) |

You **never hand-edit `app.yaml`** — it's generated from `app.yaml.template` + `.env` by
`setup/render_app_yaml.sh` (and gitignored, since it holds workspace values). The values
other steps discover — `PGHOST`, `ENDPOINT_NAME`, and the app service-principal id
(`PGUSER`) — are written back into `.env` automatically. Everything else rarely changes;
see the [file map](#7-file-map).

---

## 3. Prerequisites (one-time)

- A **serverless Databricks workspace** (needed for Apps + Lakebase — e.g. an FEVM workspace).
- **Databricks CLI v0.285.0+** — `brew upgrade databricks`
- **uv** (Python package manager) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Log in:** `databricks auth login --host <workspace-url> --profile <profile-name>`
- Verify: `databricks current-user me -p <profile>` prints your email.

```bash
cp .env.example .env                 # then edit .env with your values
set -a; source .env; set +a          # load .env into your shell (do this before each part)
UV_INDEX_URL="https://pypi-proxy.cloud.databricks.com/simple" uv sync
```

> **Blocked PyPI / npm?** On locked-down laptops public PyPI is blocked — always install
> through the Databricks proxy (`UV_INDEX_URL=...` above). The app ships a no-build
> `static/` UI, so **npm is never required**.

---

## 4. Build & deploy

### The fast path — deploy to a workspace in two commands 🚀

No manual file edits. `00_init_env.sh` writes your `.env`; `deploy_all.sh` provisions
Lakebase (and the checkpoint tables), creates the app, discovers + grants its service
principal, writes every derived value back to `.env`, renders `app.yaml`, and deploys.

```bash
databricks auth login --profile my-workspace          # once

# 1. Create .env for the target workspace (pass what you know; the rest have defaults)
DATABRICKS_PROFILE=my-workspace \
UC_CATALOG=main UC_SCHEMA=default \
UAIG_ENDPOINT=databricks-claude-sonnet-5 \
APP_NAME=langgraph-sample \
bash setup/00_init_env.sh

# 2. Provision + grant + render + deploy — one command, idempotent
bash setup/deploy_all.sh
```

`deploy_all.sh` prints the **App URL** at the end. To also register the governed **agent
endpoint** (Gateway → Agents inventory), run [Step 4](#step-4--register-the-agent-into-unity-ai-gateway-)
afterwards. To move to another workspace, just re-run `00_init_env.sh` with a different
`DATABRICKS_PROFILE` and run `deploy_all.sh` again.

> **Deploying an agent that runs OUTSIDE Databricks** (on EKS/AKS/GKE or any HTTP endpoint)?
> See [`external-agents/`](external-agents/) — it registers that agent onto the same Gateway
> **Agents** inventory (visibility + governance), independent of this app.

---

### The manual path — the 5 steps, explained

Prefer to run each piece yourself (or understand what `deploy_all.sh` does)? Run these in
order. Everything is parameterised from `.env`; `set -a; source .env; set +a` before each step.

### Step 1 — Create Lakebase (the memory store)

```bash
bash setup/01_provision_lakebase.sh
```
Creates the Lakebase project + database, **creates the LangGraph checkpoint tables**, and
**writes `PGHOST` + `ENDPOINT_NAME` back into `.env`** automatically (no manual paste).
Idempotent (safe to re-run). Re-source `.env` afterwards if you have it loaded in your shell.
*This is the code that creates Lakebase — no manual CLI needed.*

### Step 2 — Run & test locally

Step 1 already created the checkpoint tables, so you can run straight away:

```bash
uv run uvicorn app:app --reload --port 8000
curl -s localhost:8000/api/ready           # readiness: checks Lakebase + LLM config

# Two turns on the same thread → proves memory persists in Lakebase
TID=$(curl -sX POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"What is 23 * 19? Use the calculator tool."}' | tee /dev/stderr | jq -r .thread_id)
curl -sX POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d "{\"message\":\"What did I just ask?\",\"thread_id\":\"$TID\"}"
```
The second turn should recall the first. Memory lives in Lakebase (`PostgresSaver`),
keyed by `thread_id` — not in the app's RAM.

### Step 3 — Deploy the app + grant its service principal

A Databricks App runs as its **own service principal (SP)**, created when the app is
created. So it's a create → grant → deploy sequence (all three are one command via
`deploy_all.sh`; here's the manual form):

```bash
# 3a. Create the app + get its SP. Writes PGUSER=<app SP> into .env automatically.
bash setup/03_deploy_app.sh
#     -> note the printed "APP SP client id"

# 3b. Grant that SP everything it needs:
#     - a Postgres role on Lakebase (to mint DB tokens)
#     - table/schema GRANTs in the app DB (to read/write checkpoints)
#     - CAN_QUERY on the gateway endpoint (skipped for shared system endpoints —
#       those get access via the app resource binding instead)
APP_SP_CLIENT_ID=<printed-in-3a> bash setup/02_grant_app_sp.sh

# 3c. Redeploy so the app picks up the grants (app.yaml is re-rendered from .env for you)
bash setup/03_deploy_app.sh
```
`setup/02_grant_app_sp.sh` is the code that **gives the app SP all its access** — skipping
it is what causes `invalid_client` (Lakebase) or `403` (gateway). `PGUSER` and `app.yaml`
are handled for you — no manual edits. Logs: `databricks apps logs <app> -p <profile>`
(or append `/logz` to the app URL).

### Step 3.5 — (Optional) Own a governed LLM endpoint with fallback

By default `UAIG_ENDPOINT` points at a shared Databricks foundation-model endpoint —
governed, but you don't control its config. To get **model routing + fallback/swap** across
providers with your own guardrails/limits, create **your own** governed LLM endpoint:

```bash
# one-time: store an API token as a secret (you choose the scope/token)
databricks secrets create-scope llm -p "$DATABRICKS_PROFILE"
databricks secrets put-secret  llm pat -p "$DATABRICKS_PROFILE"     # value = a Databricks PAT
export LLM_TOKEN_SECRET='{{secrets/llm/pat}}'

uv run python register_llm_gateway.py     # creates GATEWAY_LLM_ENDPOINT: primary + fallback + guardrails + usage
```
Then set `UAIG_ENDPOINT=<GATEWAY_LLM_ENDPOINT>` in `.env` / `app.yaml` so the agent's LLM
calls flow through **your** endpoint (fallback on 429/5xx, PII guardrails, per-endpoint
rate limit, usage tracking). This is the "Model routing · Fallback and swap" box in the diagram.

### Step 4 — Register the agent into Unity AI Gateway ⭐

This makes the agent a **governed, versioned entity on the Gateway → Agents page**.

```bash
export DATABRICKS_CONFIG_PROFILE="$DATABRICKS_PROFILE"   # disambiguates MLflow auth
databricks schemas create "$UC_SCHEMA" "$UC_CATALOG" -p "$DATABRICKS_PROFILE"   # once
uv run python deploy_agent.py                            # reads UC_CATALOG / UC_SCHEMA / UAIG_ENDPOINT from .env
```
`deploy_agent.py` does three things (see its docstring): **log** the MLflow ChatAgent
(packaging `server/`, declaring the LLM endpoint as a dependency), **register** it to
Unity Catalog, and **deploy** it with `databricks.agents.deploy()`. After it finishes the
agent shows up under **Serving → Unity AI Gateway → Agents**.

Confirm:
```bash
databricks model-versions list "$UC_CATALOG.$UC_SCHEMA.langgraph_sample_agent" \
  -p "$DATABRICKS_PROFILE" -o json | jq -r '.[] | "v\(.version) \(.status)"'
```
> If UC registration times out (transient `UC-TKTLK`), just re-run `deploy_agent.py`. To
> skip re-logging on the retry, reuse the model it already logged — the script prints
> `MODEL_URI=...`; pass it back in: `MODEL_URI=models:/m-xxxx uv run python deploy_agent.py`.

### Step 5 — Verify both ways work

```bash
export DATABRICKS_PROFILE   # already set
.venv/bin/python test_both_ways.py    # tests the app AND the agent endpoint
```
See [`RUN_TEST.md`](RUN_TEST.md) for exact, no-assumptions run instructions (incl. a
service-principal / token-only variant, `test_api_sp.py`).

---

## 5. Swapping the LLM

The model is **one value** — the Unity AI Gateway endpoint name, `UAIG_ENDPOINT`.

| Where | How |
|---|---|
| Local run | edit `UAIG_ENDPOINT` in `.env` |
| Deployed app | edit `UAIG_ENDPOINT` in `app.yaml`, redeploy |

No code change. `server/graph.py` reads it and passes it to
`ChatDatabricks(model=..., use_ai_gateway=True)`, so every call is gateway-governed.

List what your workspace offers:
```bash
databricks serving-endpoints list -p <profile> -o json | jq -r '.[].name'
```
Examples: `databricks-claude-sonnet-5`, `databricks-claude-opus-4-8`, `databricks-gpt-5-5`,
`databricks-gemini-3-5-flash`. (Details in [`docs/ARCHITECTURE.md §4`](docs/ARCHITECTURE.md).)

---

## 6. Using the agent (two ways)

Full, verified curl/Python examples are in [`USAGE.md`](USAGE.md). In short:

| | **Way 1 — the App** | **Way 2 — the Agent endpoint** |
|---|---|---|
| URL | `<app-url>/api/chat` | `<host>/serving-endpoints/<agent-endpoint>/invocations` |
| Memory | ✅ Lakebase, per `thread_id` | ❌ stateless (you send history) |
| On the Gateway **Agents** page | as a caller (request-tagged) | ✅ first-class versioned agent |
| Cost | app compute | + scale-to-zero serving endpoint |
| Best for | interactive chat with memory | programmatic / governed consumption |

Both are just **token + URL** — no venv needed to *call* them. You don't need the serving
endpoint (Way 2 / Step 4) just for governance — routing (Step 3) already governs the LLM.
Register the agent (Step 4) when you want it inventoried, versioned, and independently callable.

---

## 7. File map

| File | What it is |
|---|---|
| `.env.example` | Copy to `.env` — the one config file (incl. the LLM choice). |
| `server/graph.py` | **The agent**: LLM + tools + ReAct graph. Edit behavior here. |
| `server/config.py` | Auth + which model (`get_serving_endpoint`). |
| `server/db.py` | Lakebase connection pool (mints DB tokens per connection). |
| `server/routes/chat.py` | `POST /api/chat` — runs the agent with Lakebase memory. |
| `app.py` | FastAPI web server; opens the pool, serves API + UI. |
| `agent.py` | Same agent wrapped as an MLflow ChatAgent (for the endpoint). |
| `deploy_agent.py` | Log → register → deploy the agent onto the Gateway Agents page. (Set `MODEL_URI=models:/m-...` to skip re-logging on a retry.) |
| `register_llm_gateway.py` | (Optional) Create your own governed LLM endpoint: primary + fallback model, guardrails, rate limit, usage. |
| `app.yaml.template` | Source for `app.yaml` — `${VAR}` tokens filled from `.env`. Edit this, not `app.yaml`. |
| `app.yaml` | **Generated** (gitignored) by `setup/render_app_yaml.sh`: the env vars the deployed app sees. |
| `static/index.html` | No-build chat UI (so npm isn't required). |
| `setup/00_init_env.sh` | **Creates `.env`** for a target workspace from the values you pass. |
| `setup/deploy_all.sh` | **One-command deploy**: provision → create app → grant → render → deploy. |
| `setup/01_provision_lakebase.sh` | **Creates Lakebase** (project + database + checkpoint tables); writes `PGHOST`/`ENDPOINT_NAME` to `.env`. |
| `setup/02_grant_app_sp.sh` | Grants the app SP all access (Postgres role, grants, gateway CAN_QUERY). |
| `setup/03_deploy_app.sh` | Render `app.yaml` + sync + deploy the app; writes `PGUSER` to `.env`. |
| `setup/render_app_yaml.sh` | Render `app.yaml` from `app.yaml.template` + `.env`. |
| `setup/_lib.sh` / `setup/_create_checkpoint_tables.py` | Shared helpers (env upsert; checkpoint-table creation). |
| `external-agents/` | Register an agent running OUTSIDE Databricks (EKS/AKS/GKE/HTTP) on the Gateway Agents inventory. |
| `test_both_ways.py` / `test_api_sp.py` | Test both consumption paths (SDK auth / SP token-only). |
| `docs/ARCHITECTURE.md` | How it all works, in plain language. |
| `USAGE.md` / `RUN_TEST.md` | How to call the agent / how to run the tests. |

---

## 8. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `Connection refused ... pypi.org` | Public PyPI blocked → use `UV_INDEX_URL="https://pypi-proxy.cloud.databricks.com/simple"`. |
| `Error installing packages` on deploy | Don't ship `uv.lock` (proxy wheel URLs rotate); ship `requirements.txt` with pinned versions. |
| App 502 / `ImportError: ExecutionInfo` | LangGraph version skew — keep the pinned set in `requirements.txt`. |
| App 500 + `relation "checkpoints" does not exist` | Checkpoint tables never created. `setup/01` now creates them; if you provisioned before this, re-run `setup/01_provision_lakebase.sh` (idempotent) or `POST /api/setup` with `ENABLE_SETUP_ROUTE=true`. |
| App 500 + `invalid_client` in `/logz` | App SP lost its access/credential → re-run `setup/02_grant_app_sp.sh`; if the SP secret was wiped, delete+recreate the app. |
| `'null' is not a valid Inference Endpoint ID` in `setup/02` | `UAIG_ENDPOINT` is a shared foundation-model system endpoint (no grantable id). Handled — step (c) skips the direct ACL; access comes from the app resource binding in `setup/03`. |
| `403 Forbidden` calling the app | Caller lacks **CAN_USE** on the app. |
| `403 Forbidden` calling the agent endpoint | Caller lacks **CAN_QUERY** on the serving endpoint. |
| `cannot get token: multiple profiles match host` | `export DATABRICKS_CONFIG_PROFILE=<profile>` (MLflow auth disambiguation). |
| `CREATE INDEX CONCURRENTLY cannot run inside a transaction` | Handled — `/api/setup` uses `autocommit=True`. |
| Model registration times out (`UC-TKTLK`) | Transient UC issue — re-run `deploy_agent.py` (reuse its printed `MODEL_URI=` to skip re-logging). |
| `temperature not supported` | Reasoning models (e.g. claude-sonnet-5) reject it — `build_llm()` omits it. |

---

*Template scaffolded for a Databricks serverless workspace. Contains workspace
identifiers (host, app URL, SP client id) — not secrets. Keep real `.env` files and OAuth
secrets out of git: both `.env` and the generated `app.yaml` are gitignored (edit
`app.yaml.template`, not `app.yaml`).*
