#!/usr/bin/env bash
set -euo pipefail

# All persistence tests run against PostgreSQL.  The database can be supplied
# by the caller; otherwise use the local Docker/Postgres defaults.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PORTHOUSE_TEST_POSTGRES_URL="${PORTHOUSE_TEST_POSTGRES_URL:-postgresql://porthouse:porthouse-dev@127.0.0.1:15432/porthouse_test}"

if command -v pg_isready >/dev/null 2>&1; then
  pg_isready -d "${PORTHOUSE_TEST_POSTGRES_URL}" >/dev/null
fi

cd "${ROOT_DIR}"
bash scripts/ensure-test-pgvector.sh
uv run --frozen python -m pytest -m postgres "$@"
