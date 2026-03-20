#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="${REPO_DIR}/logs"
mkdir -p "${LOG_DIR}"

export TZ=Asia/Hong_Kong

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"
}

cd "${REPO_DIR}"

if [ ! -d ".venv" ]; then
  log "ERROR: .venv not found. Run auto-update.sh first."
  exit 1
fi

. .venv/bin/activate

LOCK_FILE="${LOG_DIR}/run-once.lock"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "Another run is still in progress. Skip this trigger."
  deactivate || true
  exit 0
fi

SCRIPT="scripts/local_author_editorial_workflow_check.py"
if [ ! -f "${SCRIPT}" ]; then
  log "ERROR: ${SCRIPT} not found."
  deactivate || true
  exit 1
fi

SECRETS_ARG=()
if [ -f "${REPO_DIR}/.streamlit/secrets.toml" ]; then
  SECRETS_ARG=(--secrets-toml "${REPO_DIR}/.streamlit/secrets.toml")
fi

AUTHOR_ARGS=()
if [ -n "${WISERS_AUTHORS:-}" ]; then
  IFS=',' read -r -a AUTHORS <<< "${WISERS_AUTHORS}"
  for author in "${AUTHORS[@]}"; do
    author_trimmed="$(echo "${author}" | xargs)"
    if [ -n "${author_trimmed}" ]; then
      AUTHOR_ARGS+=(--author "${author_trimmed}")
    fi
  done
fi

if [ ${#AUTHOR_ARGS[@]} -eq 0 ]; then
  AUTHOR_ARGS=(--author "李先知")
fi

log "Run workflow once..."
python "${SCRIPT}" \
  --headless \
  --no-stay-open \
  "${SECRETS_ARG[@]}" \
  "${AUTHOR_ARGS[@]}"

EXIT_CODE=$?
deactivate || true
log "Finished with exit_code=${EXIT_CODE}"
exit "${EXIT_CODE}"
