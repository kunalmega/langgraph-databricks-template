#!/usr/bin/env bash
# =============================================================================
# Shared helpers for the setup/ scripts. Sourced, not run directly.
#   source "$(dirname "$0")/_lib.sh"
# =============================================================================

# Absolute path to the repo root (parent of setup/).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

# upsert_env KEY VALUE [FILE]
# Set KEY=VALUE in the .env file: replace the line if KEY exists, else append.
# Keeps the file as the single source of truth so derived values (PGHOST, the app
# SP client id, ...) are written back automatically instead of by hand.
upsert_env() {
  local key="$1" value="$2" file="${3:-$ENV_FILE}"
  touch "$file"
  if grep -qE "^${key}=" "$file"; then
    # Use a temp file for portable in-place edit (macOS/Linux sed differ).
    local tmp; tmp="$(mktemp)"
    grep -vE "^${key}=" "$file" > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    mv "$tmp" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
  echo "    .env: ${key}=${value}"
}

# load_env [FILE] — export every KEY=VALUE from the .env file into the shell.
load_env() {
  local file="${1:-$ENV_FILE}"
  [ -f "$file" ] || { echo "ERROR: $file not found. Run setup/00_init_env.sh first." >&2; return 1; }
  set -a; # shellcheck disable=SC1090
  source "$file"; set +a
}
