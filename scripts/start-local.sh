#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${JOYHOUSEBOT_CONFIG_PATH:-${ROOT_DIR}/config.json}"
# Local development only: fall back to the insecure-auth dev template when no
# real config.json exists. Production deployments must pass an explicit path.
if [[ -z "${JOYHOUSEBOT_CONFIG_PATH:-}" && ! -f "${CONFIG_PATH}" ]]; then
  CONFIG_PATH="${ROOT_DIR}/config.dev.json"
fi
API_HOST="${JOYHOUSEBOT_LOCAL_HOST:-127.0.0.1}"
API_PORT="${JOYHOUSEBOT_LOCAL_PORT:-18790}"
WORKER_COUNT="${JOYHOUSEBOT_LOCAL_WORKERS:-2}"
LOG_ROOT="${JOYHOUSEBOT_LOCAL_LOG_ROOT:-${HOME}/.joyhousebot/logs/local}"
RUN_STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_DIR="${LOG_ROOT}/${RUN_STAMP}"

LOCAL_PG_HOST="${JOYHOUSEBOT_LOCAL_PG_HOST:-127.0.0.1}"
LOCAL_PG_PORT="${JOYHOUSEBOT_LOCAL_PG_PORT:-15432}"
LOCAL_PG_USER="${JOYHOUSEBOT_LOCAL_PG_USER:-joyhousebot}"
LOCAL_PG_PASSWORD="${JOYHOUSEBOT_LOCAL_PG_PASSWORD:-joyhousebot-dev}"
LOCAL_PG_DATABASE="${JOYHOUSEBOT_LOCAL_PG_DATABASE:-joyhousebot}"
LOCAL_EXTENSION_PACKAGES="${JOYHOUSEBOT_LOCAL_EXTENSION_PACKAGES:-}"

CHILD_PIDS=()
CHILD_NAMES=()
TAIL_PID=""
STOPPING=0

info() {
  printf '[local] %s\n' "$*"
}

fail() {
  printf '[local] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

cleanup() {
  local status=$?
  local pid
  if [[ "${STOPPING}" -eq 1 ]]; then
    return
  fi
  STOPPING=1
  trap - INT TERM EXIT
  [[ -n "${TAIL_PID}" ]] && kill "${TAIL_PID}" >/dev/null 2>&1 || true
  for pid in "${CHILD_PIDS[@]:-}"; do
    kill -TERM "${pid}" >/dev/null 2>&1 || true
  done
  for pid in "${CHILD_PIDS[@]:-}"; do
    wait "${pid}" >/dev/null 2>&1 || true
  done
  info "all local processes stopped; logs: ${LOG_DIR}"
  exit "${status}"
}

trap cleanup INT TERM EXIT

prepare_database() {
  local database_exists=""
  if [[ -n "${JOYHOUSE_DATABASE_URL:-}" ]]; then
    export JOYHOUSEBOT_DATABASE_URL="${JOYHOUSE_DATABASE_URL}"
    info "PostgreSQL: using shared JOYHOUSE_DATABASE_URL"
    return
  fi
  if [[ -n "${JOYHOUSEBOT_DATABASE_URL:-}" ]]; then
    export JOYHOUSE_DATABASE_URL="${JOYHOUSEBOT_DATABASE_URL}"
    info "PostgreSQL: using JOYHOUSEBOT_DATABASE_URL as shared connection"
    return
  fi

  export PGPASSWORD="${LOCAL_PG_PASSWORD}"
  pg_isready -h "${LOCAL_PG_HOST}" -p "${LOCAL_PG_PORT}" -U "${LOCAL_PG_USER}" >/dev/null 2>&1 || \
    fail "PostgreSQL is unavailable at ${LOCAL_PG_HOST}:${LOCAL_PG_PORT}"
  database_exists="$(psql \
    -h "${LOCAL_PG_HOST}" \
    -p "${LOCAL_PG_PORT}" \
    -U "${LOCAL_PG_USER}" \
    -d postgres \
    -Atqc "SELECT 1 FROM pg_database WHERE datname = '${LOCAL_PG_DATABASE}'" \
    2>/dev/null || true)"
  if [[ "${database_exists}" != "1" ]]; then
    info "PostgreSQL: creating database ${LOCAL_PG_DATABASE}"
    createdb \
      -h "${LOCAL_PG_HOST}" \
      -p "${LOCAL_PG_PORT}" \
      -U "${LOCAL_PG_USER}" \
      "${LOCAL_PG_DATABASE}"
  fi
  export JOYHOUSEBOT_DATABASE_URL="postgresql://${LOCAL_PG_USER}:${LOCAL_PG_PASSWORD}@${LOCAL_PG_HOST}:${LOCAL_PG_PORT}/${LOCAL_PG_DATABASE}"
  export JOYHOUSE_DATABASE_URL="${JOYHOUSEBOT_DATABASE_URL}"
  unset PGPASSWORD
  info "PostgreSQL: ${LOCAL_PG_HOST}:${LOCAL_PG_PORT}/${LOCAL_PG_DATABASE}"
}

start_role() {
  local name="$1"
  shift
  local log_file="${LOG_DIR}/${name}.log"
  "$@" >>"${log_file}" 2>&1 &
  CHILD_PIDS+=("$!")
  CHILD_NAMES+=("${name}")
  info "started ${name} (pid $!, log ${log_file})"
}

wait_for_api() {
  local attempt
  for attempt in $(seq 1 60); do
    if curl -fsS "http://${API_HOST}:${API_PORT}/readyz" >/dev/null 2>&1; then
      return
    fi
    sleep 0.25
  done
  fail "API did not become ready; inspect ${LOG_DIR}/api.log"
}

monitor_children() {
  local index
  local pid
  while true; do
    for index in "${!CHILD_PIDS[@]}"; do
      pid="${CHILD_PIDS[${index}]}"
      if ! kill -0 "${pid}" >/dev/null 2>&1; then
        wait "${pid}" || true
        fail "${CHILD_NAMES[${index}]} exited unexpectedly"
      fi
    done
    sleep 1
  done
}

install_local_extensions() {
  local extension_id
  local extension_path
  local extension_paths=()
  while IFS= read -r extension_id; do
    [[ "${extension_id}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || \
      fail "invalid extension id in ${CONFIG_PATH}: ${extension_id}"
    extension_path="${ROOT_DIR}/extensions/${extension_id}"
    [[ -f "${extension_path}/pyproject.toml" ]] || continue
    extension_paths+=("${extension_path}")
  done < <(jq -r '(.extensions.allowedIds // .extensions.enabled // []) | .[]' "${CONFIG_PATH}")

  if [[ -n "${LOCAL_EXTENSION_PACKAGES}" ]]; then
    local supplied=()
    IFS=':' read -r -a supplied <<< "${LOCAL_EXTENSION_PACKAGES}"
    extension_paths+=("${supplied[@]}")
  fi

  for extension_path in "${extension_paths[@]}"; do
    [[ -n "${extension_path}" ]] || continue
    [[ -f "${extension_path}/pyproject.toml" ]] || \
      fail "extension package is missing pyproject.toml: ${extension_path}"
    info "installing local extension: ${extension_path}"
    uv pip install --python "${ROOT_DIR}/.venv/bin/python" -e "${extension_path}" --quiet
  done
}

main() {
  local worker_index
  cd "${ROOT_DIR}"
  require_command uv
  require_command jq
  require_command pg_isready
  require_command psql
  require_command createdb
  require_command curl
  require_command lsof

  [[ -f "${CONFIG_PATH}" ]] || fail "config file not found: ${CONFIG_PATH}"
  [[ "${WORKER_COUNT}" =~ ^[1-9][0-9]*$ ]] || fail "JOYHOUSEBOT_LOCAL_WORKERS must be a positive integer"
  [[ "${WORKER_COUNT}" -le 32 ]] || fail "JOYHOUSEBOT_LOCAL_WORKERS must not exceed 32"
  [[ "${API_PORT}" =~ ^[1-9][0-9]*$ ]] || fail "JOYHOUSEBOT_LOCAL_PORT must be a valid port"
  [[ "${API_PORT}" -le 65535 ]] || fail "JOYHOUSEBOT_LOCAL_PORT must not exceed 65535"
  [[ "${LOCAL_PG_PORT}" =~ ^[1-9][0-9]*$ ]] || fail "JOYHOUSEBOT_LOCAL_PG_PORT must be a valid port"
  [[ "${LOCAL_PG_DATABASE}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "JOYHOUSEBOT_LOCAL_PG_DATABASE contains unsupported characters"
  [[ "${LOCAL_PG_USER}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "JOYHOUSEBOT_LOCAL_PG_USER contains unsupported characters"
  [[ "${LOCAL_PG_PASSWORD}" =~ ^[A-Za-z0-9._~-]+$ ]] || fail "set JOYHOUSE_DATABASE_URL when the local PostgreSQL password requires URL encoding"
  if lsof -nP -iTCP:"${API_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "TCP port ${API_PORT} is already in use"
  fi

  prepare_database
  mkdir -p "${LOG_DIR}"
  chmod 600 "${CONFIG_PATH}" 2>/dev/null || true

  info "synchronizing Python environment"
  # Keep the repository's development tools installed.  A plain `uv sync`
  # prunes optional dev dependencies and can make a later `uv run pytest`
  # resolve to an unrelated system executable.
  uv sync --frozen --extra dev --quiet
  install_local_extensions
  # Apply schema changes once before any runtime role starts. Starting every
  # role with auto-migration enabled can interleave DDL from a later process
  # with catalog bootstrap writes from an earlier one.
  JOYHOUSEBOT_AUTO_MIGRATE=true uv run joyhousebot check --config "${CONFIG_PATH}"
  uv run joyhousebot discover-extensions --config "${CONFIG_PATH}"
  export JOYHOUSEBOT_AUTO_MIGRATE=false

  start_role api uv run joyhousebot api \
    --surface combined --config "${CONFIG_PATH}" --host "${API_HOST}" --port "${API_PORT}"
  start_role scheduler uv run joyhousebot scheduler --config "${CONFIG_PATH}"
  for worker_index in $(seq 1 "${WORKER_COUNT}"); do
    start_role "worker-${worker_index}" uv run joyhousebot worker --config "${CONFIG_PATH}"
  done
  start_role channel-worker uv run joyhousebot channel-worker --config "${CONFIG_PATH}"

  wait_for_api
  info "ready: http://${API_HOST}:${API_PORT}/ui/"
  info "press Ctrl+C to stop API, Scheduler, Channel Worker and ${WORKER_COUNT} Agent Workers"
  tail -n 20 -F "${LOG_DIR}"/*.log &
  TAIL_PID="$!"
  monitor_children
}

main "$@"
