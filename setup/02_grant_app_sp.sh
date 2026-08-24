#!/usr/bin/env bash
# =============================================================================
# STEP 2 — Give the App's service principal access to everything it needs.
#
# A Databricks App runs as its OWN service principal (SP), created automatically
# when you run `databricks apps create`. That SP must be granted:
#   (a) a Postgres ROLE on the Lakebase branch      -> so it can mint DB tokens
#   (b) table/schema GRANTs in the app database      -> so it can read/write checkpoints
#   (c) CAN_QUERY on the Unity AI Gateway endpoint    -> so its LLM calls are allowed
#
# Run this AFTER `databricks apps create` (step 3) so the SP exists.
#
# Usage:
#   set -a; source .env; set +a
#   APP_SP_CLIENT_ID=<from `databricks apps get <app>`> bash setup/02_grant_app_sp.sh
# =============================================================================
set -euo pipefail

: "${DATABRICKS_PROFILE:?}"; : "${LAKEBASE_PROJECT:?}"; : "${PGDATABASE:?}"
: "${UAIG_ENDPOINT:?}"; : "${APP_NAME:?set APP_NAME in .env}"
: "${APP_SP_CLIENT_ID:?pass APP_SP_CLIENT_ID=... (see: databricks apps get \$APP_NAME)}"
P="$DATABRICKS_PROFILE"
BRANCH="projects/${LAKEBASE_PROJECT}/branches/production"
ENDPOINT="${BRANCH}/endpoints/primary"
SP="$APP_SP_CLIENT_ID"

echo "==> (a) Create a Postgres role for the app SP on ${BRANCH}"
databricks postgres create-role "${BRANCH}" \
  --role-id "app-${APP_NAME}" \
  --json "{\"spec\": {\"identity_type\": \"SERVICE_PRINCIPAL\", \"postgres_role\": \"${SP}\", \"auth_method\": \"LAKEBASE_OAUTH_V1\"}}" \
  -p "$P" 2>&1 | grep -iE 'postgres_role|already|error' || true

echo "==> (b) Grant the SP privileges inside the '${PGDATABASE}' database"
HOST=$(databricks postgres list-endpoints "${BRANCH}" -p "$P" -o json | jq -r '.[0].status.hosts.host')
TOKEN=$(databricks postgres generate-database-credential "${ENDPOINT}" -p "$P" -o json | jq -r '.token')
EMAIL=$(databricks current-user me -p "$P" -o json | jq -r '.userName')
PGPASSWORD="$TOKEN" psql "host=${HOST} port=5432 dbname=${PGDATABASE} user=${EMAIL} sslmode=require" <<SQL
GRANT CONNECT ON DATABASE ${PGDATABASE} TO "${SP}";
GRANT USAGE, CREATE ON SCHEMA public TO "${SP}";
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO "${SP}";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "${SP}";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO "${SP}";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "${SP}";
SQL

echo "==> (c) Grant the SP CAN_QUERY on the Unity AI Gateway endpoint '${UAIG_ENDPOINT}'"
# Shared Databricks foundation-model system endpoints (e.g. databricks-claude-sonnet-5)
# have no permissionable `id` — you cannot (and need not) set an ACL on them; the app
# gets access via the CAN_QUERY app RESOURCE bound in setup/03_deploy_app.sh. Only
# custom/owned endpoints (e.g. from register_llm_gateway.py) expose an id to grant on.
EPID=$(databricks serving-endpoints get "${UAIG_ENDPOINT}" -p "$P" -o json | jq -r '.id // empty')
if [ -z "$EPID" ]; then
  echo "    '${UAIG_ENDPOINT}' is a system endpoint (no grantable id) — skipping direct ACL."
  echo "    The app's CAN_QUERY access comes from the app resource binding in step 03."
else
  databricks serving-endpoints update-permissions "${EPID}" \
    --json "{\"access_control_list\":[{\"service_principal_name\":\"${SP}\",\"permission_level\":\"CAN_QUERY\"}]}" \
    -p "$P" -o json | jq -r '.access_control_list[]? | "    \(.service_principal_name // .display_name): \(.all_permissions[0].permission_level)"'
fi

if [ -n "${MODEL_SERVICE:-}" ]; then
  echo "==> (d) Grant the SP access to the Unity AI Gateway MODEL SERVICE '${MODEL_SERVICE}'"
  # MODEL_SERVICE is a UC securable catalog.schema.name. The SP needs EXECUTE on it,
  # plus USE SCHEMA and USE CATALOG to resolve the name (UC reports missing traversal
  # as NOT_FOUND). USE CATALOG may already be covered by the 'account users' group; the
  # grant is attempted and a failure (no MANAGE on the catalog) is tolerated with a note.
  MS_CATALOG="${MODEL_SERVICE%%.*}"
  MS_REST="${MODEL_SERVICE#*.}"
  MS_SCHEMA="${MS_REST%%.*}"
  databricks api patch "/api/2.1/unity-catalog/permissions/model_service/${MODEL_SERVICE}" \
    --json "{\"changes\":[{\"principal\":\"${SP}\",\"add\":[\"EXECUTE\"]}]}" -p "$P" >/dev/null \
    && echo "    EXECUTE granted on model service"
  databricks api patch "/api/2.1/unity-catalog/permissions/schema/${MS_CATALOG}.${MS_SCHEMA}" \
    --json "{\"changes\":[{\"principal\":\"${SP}\",\"add\":[\"USE_SCHEMA\"]}]}" -p "$P" >/dev/null \
    && echo "    USE_SCHEMA granted on ${MS_CATALOG}.${MS_SCHEMA}"
  if databricks api patch "/api/2.1/unity-catalog/permissions/catalog/${MS_CATALOG}" \
       --json "{\"changes\":[{\"principal\":\"${SP}\",\"add\":[\"USE_CATALOG\"]}]}" -p "$P" >/dev/null 2>&1; then
    echo "    USE_CATALOG granted on ${MS_CATALOG}"
  else
    echo "    NOTE: could not grant USE_CATALOG on ${MS_CATALOG} (needs MANAGE). If the SP"
    echo "          isn't covered by 'account users', a catalog admin must grant USE CATALOG."
  fi
fi

echo "==> Done. The app SP can now: mint Lakebase tokens, read/write checkpoints, and call the gateway."
