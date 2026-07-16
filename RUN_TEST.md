# How to run `test_both_ways.py` — exact steps

This tests the agent **both ways** (FastAPI app with Lakebase memory, and the registered
Agent serving endpoint). Follow these steps exactly. No prior knowledge assumed.

---

## Step 0 — One-time setup (skip if already done on this machine)

You need: the **Databricks CLI**, **uv**, and to be **logged in** to the workspace.

```bash
# 1. Install the Databricks CLI (if `databricks --version` fails)
brew install databricks       # macOS. Otherwise: https://docs.databricks.com/dev-tools/cli/install

# 2. Install uv (if `uv --version` fails)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Log in to the workspace (opens a browser). This creates the CLI profile.
databricks auth login --host https://fevm-fevm-cme-conde.cloud.databricks.com --profile Fevm-fevm-conde

# 4. Verify login works (should print your email):
databricks current-user me -p Fevm-fevm-conde | grep userName
```

---

## Step 1 — Go to the project folder

```bash
cd "/Users/kunal.gaurav/Documents/vibe/langgraph template app"
```
(Or wherever this template folder lives on the machine you're on.)

---

## Step 2 — Install dependencies (ONCE, into the project's .venv)

> ⚠️ Public PyPI may be blocked on Databricks laptops. Always install through the
> Databricks package proxy shown below — plain `uv sync` will fail with
> `Connection refused ... pypi.org`.

```bash
UV_INDEX_URL="https://pypi-proxy.cloud.databricks.com/simple" uv sync
```

This creates `.venv/` with everything the script needs. You only do this once (or again
if dependencies change).

---

## Step 3 — Run the test

> ⚠️ Do **not** use plain `uv run` — it tries to re-check PyPI (blocked) and fails, and it
> can also get confused if another virtualenv (`VIRTUAL_ENV`) is active in your shell.
> Run the project's venv Python **directly** — this always works:

```bash
export DATABRICKS_PROFILE=Fevm-fevm-conde
.venv/bin/python test_both_ways.py
```

That's it. No `source activate`, no `uv run` — just those two lines.

---

## What a successful run looks like

```
Profile: Fevm-fevm-conde
App URL: https://langgraph-sample-7474657767854090.aws.databricksapps.com
Agent endpoint: agents_fevm_cme_conde_catalog-langgraph_demo-langgraph_sample_a

=== Way 1: FastAPI App (stateful, Lakebase memory) ===
  turn 1 -> 21 * 3 = 63   (thread ...)
  turn 2 -> You asked ... 63 ...
  memory persisted across turns (Lakebase): PASS

=== Way 2: Registered Agent endpoint (governed, stateless) ===
  single turn -> 21 * 3 = 63
  multi turn (history supplied) -> You just asked ...

=== SUMMARY ===
  Way 1 (App): PASS
  Way 2 (Agent endpoint): PASS
```

The script exits with code `0` if both pass, `1` if either fails.

---

## If something goes wrong

| Error you see | What it means | Fix |
|---|---|---|
| `Connection refused ... pypi.org` | Public PyPI is blocked; you used plain `uv sync`/`uv run` | Use the proxy (Step 2) and run `.venv/bin/python ...` directly (Step 3) |
| `VIRTUAL_ENV=... does not match ... will be ignored` | Another venv is active in your shell | Harmless — ignore. Running `.venv/bin/python` directly sidesteps it entirely |
| `cannot get access token` / `run databricks auth login` | Not logged in, or token expired | Re-run Step 0.3: `databricks auth login --profile Fevm-fevm-conde` |
| `No such file or directory: .venv/bin/python` | Dependencies not installed | Run Step 2 first |
| `Fevm-... and ... match ... Use --profile` | Multiple CLI profiles match the host | Already handled by `DATABRICKS_PROFILE`; make sure Step 3's `export` ran |
| Way 1 fails but Way 2 passes | The Databricks App isn't running | `databricks apps get langgraph-sample -p Fevm-fevm-conde` → check `state`; redeploy if needed |
| Way 2 fails but Way 1 passes | The agent serving endpoint is down/scaled-in-error | `databricks serving-endpoints get agents_fevm_cme_conde_catalog-langgraph_demo-langgraph_sample_a -p Fevm-fevm-conde` → check `state.ready` |

---

## Alternative: token + URL only (no venv, service principal) — `test_api_sp.py`

If you just want to hit the APIs and have **no CLI / no project venv** — e.g. from a
backend service — use `test_api_sp.py`. It needs only `requests` and a service-principal
OAuth secret. Nothing else from this repo.

**One-time: create a service principal + OAuth secret** (an admin does this once):
```bash
# create an SP (or reuse an existing app SP) and generate a secret:
databricks service-principal-secrets-proxy create <sp-numeric-id> -p Fevm-fevm-conde
# note the returned `secret` (shown ONCE) and the SP's applicationId (= CLIENT_ID)
```
Grant that SP: **CAN_USE** on the app and **CAN_QUERY** on the serving endpoint.

**Run (any machine with Python + requests):**
```bash
pip install requests   # only dependency

export DATABRICKS_HOST="https://fevm-fevm-cme-conde.cloud.databricks.com"
export CLIENT_ID="<sp-application-id>"
export CLIENT_SECRET="<sp-oauth-secret>"
python test_api_sp.py
```

It fetches an OAuth token via client-credentials (no browser) and tests both ways.
✅ Verified working end-to-end with a service principal (both Way 1 and Way 2 PASS).

---

## Running on a different machine / as a different person

Everything above works for anyone with access, because:
- **Auth** comes from *their own* `databricks auth login` (Step 0.3) — no shared secrets.
- The **URLs/endpoint names** are baked into the script's defaults, but can be overridden
  without editing the file:
  ```bash
  export DATABRICKS_PROFILE=<their-profile>
  export APP_URL="https://<their-app>.aws.databricksapps.com"
  export AGENT_ENDPOINT="<their-agent-serving-endpoint-name>"
  .venv/bin/python test_both_ways.py
  ```
- For an **unattended service** (no browser login), replace Step 0.3 with a service
  principal: set `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`
  in the environment; the script's `WorkspaceClient` picks them up automatically. The
  service principal needs `CAN_QUERY` on the serving endpoint (Way 2) and access to the
  app (Way 1).
```
