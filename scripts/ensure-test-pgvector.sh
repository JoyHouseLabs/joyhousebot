#!/usr/bin/env bash
set -euo pipefail

# HNSW tests must never silently run against a plain PostgreSQL image.  This
# script only writes to the explicit test database and fails before pytest when
# the server image does not provide pgvector.
TEST_DATABASE_URL="${PORTHOUSE_TEST_POSTGRES_URL:-postgresql://porthouse:porthouse-dev@127.0.0.1:15432/porthouse_test}"

case "${TEST_DATABASE_URL}" in
  */porthouse_test|*/porthouse_test\?*) ;;
  *)
    echo "refusing to enable pgvector outside porthouse_test" >&2
    exit 2
    ;;
esac

command -v psql >/dev/null 2>&1 || {
  echo "psql is required to validate pgvector in the PostgreSQL test database" >&2
  exit 2
}

psql "${TEST_DATABASE_URL}" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null
psql "${TEST_DATABASE_URL}" -Atqc "SELECT 'pgvector=' || extversion FROM pg_extension WHERE extname='vector';"
