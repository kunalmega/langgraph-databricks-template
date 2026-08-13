#!/usr/bin/env bash
# =============================================================================
# Register an EXTERNALLY-HOSTED agent (running outside Databricks — e.g. on EKS,
# AKS, GKE, or any HTTP endpoint) into Unity Catalog as an EXTERNAL agent
# service, so it appears on the Unity AI Gateway "Agents" inventory and is
# governable (permissions, lineage, usage) alongside your Databricks-native
# agents.
#
# It's a TWO-part registration:
#   1. A Unity Catalog HTTP CONNECTION  -> where the agent lives + how to auth to
#      it (base URL + bearer token). This is the secure credential holder.
#   2. An AGENT SERVICE (type EXTERNAL) -> the governed entity that points at the
#      connection and describes the agent (base_path, system prompt).
#
# This is standalone: it does NOT touch the rest of the template.
#
# Usage:
#   cp external-agents/.env.example external-agents/.env   # then edit
#   set -a; source external-agents/.env; set +a
#   bash external-agents/register_external_agent.sh
#
# IMPORTANT (per docs, this feature is in BETA):
#   - Registration + permissions management WORK: the agent appears in the AI
#     Gateway "Agents" inventory and can be governed (permissions, lineage).
#   - RUNTIME INVOCATION IS NOT AVAILABLE YET: you CANNOT call the external agent
#     THROUGH the registered agent service. Callers still hit the external
#     endpoint directly for now. This is a Databricks limitation, not this script.
#   Docs: https://docs.databricks.com/aws/en/ai-gateway/agent-services
#
# The agent-services payload below is verified against those docs. The UC HTTP
# connection field names (options.host/port/base_path/bearer_token) follow the
# standard UC HTTP connection shape; if a field is rejected, check the current
# `databricks connections create` docs and tweak (the JSON blocks are small).
# =============================================================================
set -euo pipefail

# --- Required config (from external-agents/.env) -----------------------------
: "${DATABRICKS_PROFILE:?set DATABRICKS_PROFILE (a CLI profile: databricks auth login --profile <name>)}"
: "${UC_CATALOG:?set UC_CATALOG (e.g. main)}"
: "${UC_SCHEMA:?set UC_SCHEMA (e.g. default)}"
: "${CONNECTION_NAME:?set CONNECTION_NAME (e.g. my_agent_connection)}"
: "${AGENT_SERVICE_ID:?set AGENT_SERVICE_ID (e.g. support_agent)}"
: "${AGENT_HOST:?set AGENT_HOST (e.g. https://my-agent.eks.example.com — the external endpoint)}"

P="$DATABRICKS_PROFILE"
PORT="${AGENT_PORT:-443}"
BASE_PATH="${AGENT_BASE_PATH:-/v1/chat}"
SYSTEM_PROMPT="${AGENT_SYSTEM_PROMPT:-You are a helpful assistant.}"
COMMENT="${AGENT_COMMENT:-External agent registered from ${AGENT_HOST}}"
FQ_CONNECTION="${UC_CATALOG}.${UC_SCHEMA}.${CONNECTION_NAME}"

# The bearer token for the external agent. STRONGLY prefer a Databricks secret
# reference over a literal. Set exactly ONE of:
#   AGENT_BEARER_TOKEN         -> a literal token (fine for a quick test)
#   AGENT_BEARER_TOKEN_SECRET  -> "{{secrets/<scope>/<key>}}" (recommended)
TOKEN="${AGENT_BEARER_TOKEN_SECRET:-${AGENT_BEARER_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  echo "ERROR: set AGENT_BEARER_TOKEN_SECRET='{{secrets/scope/key}}' (recommended) or"
  echo "       AGENT_BEARER_TOKEN=<token> for the external agent's auth." >&2
  exit 1
fi

echo "==> [1/2] Create/refresh the Unity Catalog HTTP connection '${FQ_CONNECTION}'"
echo "    host=${AGENT_HOST}  port=${PORT}  base_path=${BASE_PATH}"

CONN_JSON=$(cat <<JSON
{
  "name": "${CONNECTION_NAME}",
  "connection_type": "HTTP",
  "options": {
    "host": "${AGENT_HOST}",
    "port": "${PORT}",
    "base_path": "${BASE_PATH}",
    "bearer_token": "${TOKEN}"
  },
  "comment": "${COMMENT}"
}
JSON
)

if databricks connections get "${CONNECTION_NAME}" -p "$P" >/dev/null 2>&1; then
  echo "    connection exists — updating options"
  databricks connections update "${CONNECTION_NAME}" \
    --json "{\"options\": {\"host\": \"${AGENT_HOST}\", \"port\": \"${PORT}\", \"base_path\": \"${BASE_PATH}\", \"bearer_token\": \"${TOKEN}\"}}" \
    -p "$P" >/dev/null
else
  echo "    creating connection"
  echo "$CONN_JSON" | databricks connections create --json @- -p "$P" >/dev/null
fi
echo "    connection ready: ${FQ_CONNECTION}"

echo "==> [2/2] Register the EXTERNAL agent service '${AGENT_SERVICE_ID}' in ${UC_CATALOG}.${UC_SCHEMA}"

AGENT_JSON=$(cat <<JSON
{
  "agent_service_type": "AGENT_SERVICE_TYPE_EXTERNAL",
  "comment": "${COMMENT}",
  "config": {
    "source_connection": {
      "name": "connections/${FQ_CONNECTION}"
    },
    "base_path": "${BASE_PATH}",
    "system_prompt": "${SYSTEM_PROMPT}"
  }
}
JSON
)

echo "$AGENT_JSON" | databricks api post \
  "/api/2.1/unity-catalog/agent-services?parent=schemas/${UC_CATALOG}.${UC_SCHEMA}&agent_service_id=${AGENT_SERVICE_ID}" \
  --json @- -p "$P"

echo ""
echo "==> Done. The external agent should now appear under"
echo "    Serving -> Unity AI Gateway -> Agents  (as ${UC_CATALOG}.${UC_SCHEMA}.${AGENT_SERVICE_ID})."
echo "    Configure governance (permissions, rate limits, usage) in the AI Gateway UI."
