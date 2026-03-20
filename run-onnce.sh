#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "${SCRIPT_DIR}/run-once.sh" "$@"
