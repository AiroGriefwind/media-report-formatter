#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="${REPO_DIR}/logs"
mkdir -p "${LOG_DIR}"

log() {
  echo "[$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S %Z')] $*"
}

log "Start auto-update in ${REPO_DIR}"
cd "${REPO_DIR}"

if ! command -v git >/dev/null 2>&1; then
  log "ERROR: git not found"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  log "ERROR: python3 not found"
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
log "Current branch: ${BRANCH}"

log "Pull latest code..."
git fetch --all --prune
git pull --rebase origin "${BRANCH}"

if [ ! -d ".venv" ]; then
  log "Create virtualenv .venv"
  python3 -m venv .venv
fi

log "Install/upgrade requirements..."
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
deactivate

chmod +x "${REPO_DIR}/run-once.sh" || true

START_MARK="# >>> wisers-cron-start >>>"
END_MARK="# <<< wisers-cron-end <<<"
CRON_TMP="$(mktemp)"
EXISTING_CRON="$(mktemp)"

crontab -l 2>/dev/null > "${EXISTING_CRON}" || true

awk -v s="${START_MARK}" -v e="${END_MARK}" '
  BEGIN {skip=0}
  $0==s {skip=1; next}
  $0==e {skip=0; next}
  skip==0 {print}
' "${EXISTING_CRON}" > "${CRON_TMP}"

{
  echo "${START_MARK}"
  echo "CRON_TZ=Asia/Hong_Kong"
  echo "0 * * * * /bin/bash \"${REPO_DIR}/run-once.sh\" >> \"${LOG_DIR}/cron.log\" 2>&1"
  echo "# Production schedule example (HKT 05:00):"
  echo "# 0 5 * * * /bin/bash \"${REPO_DIR}/run-once.sh\" >> \"${LOG_DIR}/cron.log\" 2>&1"
  echo "${END_MARK}"
} >> "${CRON_TMP}"

crontab "${CRON_TMP}"
rm -f "${CRON_TMP}" "${EXISTING_CRON}"

log "Cron installed: every hour at minute 0 (HKT)."
log "Done."
