#!/usr/bin/env bash
# =============================================================================
# ONE-COMMAND deploy of the Databricks App to a workspace. Automates every manual
# edit: provisions Lakebase, creates the app, discovers the app SP, grants it,
# writes all derived values (PGHOST, ENDPOINT_NAME, PGUSER) back to .env, renders
# app.yaml, and deploys. Idempotent — safe to re-run.
#
# Prereqs:
#   1. databricks auth login --profile <name>
#   2. bash setup/00_init_env.sh   (with DATABRICKS_PROFILE, UC_*, UAIG_ENDPOINT, ...)
#
# Then:
#   bash setup/deploy_all.sh
#
# NOTE: this deploys the APP. To also register the agent as a governed serving
# endpoint on the Gateway Agents inventory, run `uv run python deploy_agent.py`
# afterwards (it reads UC_CATALOG / UC_SCHEMA / UAIG_ENDPOINT from .env).
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
load_env

: "${DATABRICKS_PROFILE:?set it via setup/00_init_env.sh}"; : "${APP_NAME:?}"
P="$DATABRICKS_PROFILE"

echo "########## [1/4] Provision Lakebase + create checkpoint tables ##########"
bash "${REPO_ROOT}/setup/01_provision_lakebase.sh"
load_env   # pick up PGHOST + ENDPOINT_NAME that step 01 just wrote

echo "########## [2/4] Create the app + discover its service principal ##########"
if ! databricks apps get "$APP_NAME" -p "$P" >/dev/null 2>&1; then
  databricks apps create "$APP_NAME" --description "LangGraph agent template" -p "$P" >/dev/null
fi
SP=$(databricks apps get "$APP_NAME" -p "$P" -o json | jq -r '.service_principal_client_id')
upsert_env PGUSER "$SP"; export PGUSER="$SP"

echo "########## [3/4] Grant the app SP (Postgres role, table grants, gateway access) ##########"
APP_SP_CLIENT_ID="$SP" bash "${REPO_ROOT}/setup/02_grant_app_sp.sh"

echo "########## [4/4] Render app.yaml + sync + deploy ##########"
bash "${REPO_ROOT}/setup/03_deploy_app.sh"

echo ""
echo "########## Done. App deployed. ##########"
echo "    App URL: $(databricks apps get "$APP_NAME" -p "$P" -o json | jq -r '.url')"
echo "    Optional next: uv run python deploy_agent.py   (register the governed agent endpoint)"
