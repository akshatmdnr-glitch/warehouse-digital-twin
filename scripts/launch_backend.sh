#!/usr/bin/env bash
# Launch the production backend (FastAPI + SQLite).
#
#   scripts/launch_backend.sh
#   BACKEND_PORT=8091 BACKEND_AUTH_SECRET=... scripts/launch_backend.sh
#
# Environment overrides are documented in backend/config/backend.yaml.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"

export BACKEND_AUTH_SECRET="${BACKEND_AUTH_SECRET:-$(python3 -c 'import secrets;print(secrets.token_hex(16))')}"
export BACKEND_PORT="${BACKEND_PORT:-8090}"
export BACKEND_DB_PATH="${BACKEND_DB_PATH:-$ROOT/backend/data/warehouse.db}"
export BACKEND_LOG_FILE="${BACKEND_LOG_FILE:-$ROOT/backend/data/logs/warehouse.log}"

echo "Warehouse backend -> http://localhost:${BACKEND_PORT} (db: ${BACKEND_DB_PATH})"
exec python3 -m backend
