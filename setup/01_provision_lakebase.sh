#!/usr/bin/env bash
# =============================================================================
# STEP 1 — Create the Lakebase (Postgres) database that stores agent memory.
#
# This is the code that CREATES Lakebase. Run it ONCE per environment.
# It is idempotent-ish: re-running is safe (it skips things that already exist).
#
# What it creates:
#   - a Lakebase Autoscaling PROJECT  (auto-creates a 'production' branch + 'primary' endpoint)
#   - a Postgres DATABASE inside it    (where LangGraph writes checkpoints)
#
# Everything is parameterised from .env — edit .env, never this script.
#
# Usage:
#   cp .env.example .env    # then edit
#   set -a; source .env; set +a
#   bash setup/01_provision_lakebase.sh
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
load_env   # load .env (or $ENV_FILE) so this runs standalone, not only pre-sourced

: "${DATABRICKS_PROFILE:?set DATABRICKS_PROFILE in .env}"
: "${LAKEBASE_PROJECT:?set LAKEBASE_PROJECT in .env (e.g. langgraph-sample)}"
: "${PGDATABASE:?set PGDATABASE in .env (e.g. langgraph_app)}"
P="$DATABRICKS_PROFILE"
BRANCH="projects/${LAKEBASE_PROJECT}/branches/production"
ENDPOINT="${BRANCH}/endpoints/primary"

echo "==> [1/4] Create Lakebase project '${LAKEBASE_PROJECT}' (skips if it exists)"
if databricks postgres get-project "projects/${LAKEBASE_PROJECT}" -p "$P" >/dev/null 2>&1; then
  echo "    project already exists — skipping"
else
  databricks postgres create-project "${LAKEBASE_PROJECT}" \
    --json "{\"spec\": {\"display_name\": \"${LAKEBASE_PROJECT}\"}}" --no-wait -p "$P"
fi

echo "==> [2/4] Wait for branch READY and endpoint ACTIVE"
until [ "$(databricks postgres list-branches "projects/${LAKEBASE_PROJECT}" -p "$P" -o json | jq -r '.[0].status.current_state')" = "READY" ]; do
  echo "    branch not ready yet..."; sleep 8
done
until [ "$(databricks postgres list-endpoints "${BRANCH}" -p "$P" -o json | jq -r '.[0].status.current_state')" = "ACTIVE" ]; do
  echo "    endpoint not active yet..."; sleep 8
done

HOST=$(databricks postgres list-endpoints "${BRANCH}" -p "$P" -o json | jq -r '.[0].status.hosts.host')
echo "    endpoint host: ${HOST}"

echo "==> [3/4] Create the '${PGDATABASE}' database (skips if it exists)"
TOKEN=$(databricks postgres generate-database-credential "${ENDPOINT}" -p "$P" -o json | jq -r '.token')
EMAIL=$(databricks current-user me -p "$P" -o json | jq -r '.userName')
# The default 'databricks_postgres' DB always exists; create ours inside it.
if PGPASSWORD="$TOKEN" psql "host=${HOST} port=5432 dbname=databricks_postgres user=${EMAIL} sslmode=require" \
     -tAc "SELECT 1 FROM pg_database WHERE datname='${PGDATABASE}'" | grep -q 1; then
  echo "    database '${PGDATABASE}' already exists — skipping"
else
  PGPASSWORD="$TOKEN" psql "host=${HOST} port=5432 dbname=databricks_postgres user=${EMAIL} sslmode=require" \
    -c "CREATE DATABASE ${PGDATABASE};"
fi

echo "==> [4/5] Write PGHOST + ENDPOINT_NAME back to .env (no manual editing)"
upsert_env PGHOST "${HOST}"
upsert_env ENDPOINT_NAME "${ENDPOINT}"

echo "==> [5/5] Create the LangGraph checkpoint tables (so the app never 500s on the first chat)"
# Mint a fresh DB credential and create the tables as the current user. The app SP
# is granted access afterwards by setup/02_grant_app_sp.sh.
PY="${REPO_ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
PGPASSWORD="$TOKEN" DB_USER="$EMAIL" PGHOST="$HOST" PGDATABASE="$PGDATABASE" \
  PGPORT="${PGPORT:-5432}" PGSSLMODE="${PGSSLMODE:-require}" \
  "$PY" "${REPO_ROOT}/setup/_create_checkpoint_tables.py"

echo ""
echo "==> Done. Lakebase provisioned, .env updated, checkpoint tables created."
echo "    PGHOST=${HOST}"
echo "    ENDPOINT_NAME=${ENDPOINT}"
