#!/usr/bin/env bash
# =============================================================================
# STEP 3 — Deploy the FastAPI agent as a Databricks App.
#
# Creates the app (which creates its service principal), uploads the code, binds
# the Unity AI Gateway endpoint as a CAN_QUERY resource, and deploys.
#
# IMPORTANT ordering:
#   1. run this until it prints the app SP client id
#   2. run setup/02_grant_app_sp.sh with that APP_SP_CLIENT_ID
#   3. re-run this script (or `databricks apps deploy ...`) to finish
#
# Usage:
#   set -a; source .env; set +a
#   bash setup/03_deploy_app.sh
# =============================================================================
set -euo pipefail

: "${DATABRICKS_PROFILE:?}"; : "${APP_NAME:?}"
P="$DATABRICKS_PROFILE"
EMAIL=$(databricks current-user me -p "$P" -o json | jq -r '.userName')
DEST="/Users/${EMAIL}/${APP_NAME}"

echo "==> [1/4] Create the app (skips if it exists) — this creates its service principal"
if databricks apps get "$APP_NAME" -p "$P" >/dev/null 2>&1; then
  echo "    app exists — skipping create"
else
  databricks apps create "$APP_NAME" --description "LangGraph agent template" -p "$P" >/dev/null
fi
SP=$(databricks apps get "$APP_NAME" -p "$P" -o json | jq -r '.service_principal_client_id')
echo "    APP SP client id: ${SP}"
echo "    -> if you haven't yet, run: APP_SP_CLIENT_ID=${SP} bash setup/02_grant_app_sp.sh"

echo "==> [2/4] Sync source to the workspace (never ship uv.lock — proxy wheel URLs rotate)"
databricks sync . "$DEST" \
  --exclude node_modules --exclude .venv --exclude __pycache__ \
  --exclude .git --exclude frontend --exclude mlruns --exclude uv.lock -p "$P"

echo "==> [3/4] Bind the Unity AI Gateway endpoint as a CAN_QUERY app resource"
databricks apps update "$APP_NAME" --json "{
  \"resources\": [{\"name\": \"gateway_endpoint\",
    \"serving_endpoint\": {\"name\": \"${UAIG_ENDPOINT}\", \"permission\": \"CAN_QUERY\"}}]
}" -p "$P" >/dev/null

echo "==> [4/4] Deploy"
databricks apps deploy "$APP_NAME" --source-code-path "/Workspace${DEST}" -p "$P" \
  | jq -r '"    " + .status.state + ": " + .status.message'
echo "    App URL: $(databricks apps get "$APP_NAME" -p "$P" -o json | jq -r '.url')"
