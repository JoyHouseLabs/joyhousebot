#!/usr/bin/env bash
set -euo pipefail

# All persistence tests run against PostgreSQL.  The database can be supplied
# by the caller; otherwise use the local Docker/Postgres defaults.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export JOYHOUSEBOT_TEST_POSTGRES_URL="${JOYHOUSEBOT_TEST_POSTGRES_URL:-${JOYHOUSEBOT_DATABASE_URL:-postgresql://postgres:postgres@127.0.0.1:5432/joyhousebot_test}}"

if command -v pg_isready >/dev/null 2>&1; then
  pg_isready -d "${JOYHOUSEBOT_TEST_POSTGRES_URL}" >/dev/null
fi

cd "${ROOT_DIR}"
python -m pytest -m postgres "$@"
