#!/usr/bin/env bash
# =============================================================================
# Render app.yaml from app.yaml.template + .env (automates the manual placeholder
# editing). Substitutes ${VARS} from the environment using Python's string.Template
# (portable — no envsubst/gettext dependency).
#
# Called automatically by setup/03_deploy_app.sh, but you can run it standalone:
#   set -a; source .env; set +a; bash setup/render_app_yaml.sh
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

# Defaults for optional toggles so the template never renders an empty value.
: "${PGPORT:=5432}"; : "${PGSSLMODE:=require}"
: "${REQUIRE_CALLER_IDENTITY:=false}"; : "${ENABLE_SETUP_ROUTE:=false}"
# Agent memory (see memory/README.md). Long-term is OFF by default.
: "${MEMORY_SHORT_TERM:=postgres}"; : "${MEMORY_LONG_TERM:=none}"
: "${LAKEBASE_BRANCH:=production}"; : "${MEMORY_EMBEDDING_ENDPOINT:=}"; : "${MEMORY_EMBEDDING_DIMS:=}"
export PGPORT PGSSLMODE REQUIRE_CALLER_IDENTITY ENABLE_SETUP_ROUTE
export MEMORY_SHORT_TERM MEMORY_LONG_TERM LAKEBASE_BRANCH MEMORY_EMBEDDING_ENDPOINT MEMORY_EMBEDDING_DIMS LAKEBASE_PROJECT

: "${ENDPOINT_NAME:?}"; : "${PGHOST:?run setup/01 first}"; : "${PGUSER:?set by setup/03}"
: "${PGDATABASE:?}"; : "${UAIG_ENDPOINT:?}"; : "${AGENT_NAME:?}"

PY="${REPO_ROOT}/.venv/bin/python"; [ -x "$PY" ] || PY="python3"
"$PY" - "${REPO_ROOT}/app.yaml.template" "${REPO_ROOT}/app.yaml" <<'PY'
import os, sys
from string import Template
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    rendered = Template(f.read()).safe_substitute(os.environ)
with open(dst, "w") as f:
    f.write(rendered)
PY
echo "==> Rendered app.yaml (PGHOST=${PGHOST}, PGUSER=${PGUSER}, UAIG_ENDPOINT=${UAIG_ENDPOINT})"
