#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${JOYHOUSEBOT_CONFIG_PATH:-${ROOT_DIR}/config.json}"
LEGACY_CONFIG_PATH="${JOYHOUSEBOT_LEGACY_CONFIG_PATH:-${HOME}/.joyhousebot/config.json}"
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
LOCAL_PLUGIN_PACKAGES="${JOYHOUSEBOT_LOCAL_PLUGIN_PACKAGES:-}"

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

resolve_llm_credentials() {
  local legacy_key=""
  local referenced_variable=""

  if [[ -n "${LLM_API_KEY:-}" ]]; then
    export LLM_PROVIDER="${LLM_PROVIDER:-anthropic}"
    info "LLM credentials: LLM_API_KEY environment (${LLM_PROVIDER})"
    return
  fi

  if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    export LLM_PROVIDER="${LLM_PROVIDER:-openrouter}"
    export LLM_API_KEY="${OPENROUTER_API_KEY}"
    info "LLM credentials: OPENROUTER_API_KEY environment (${LLM_PROVIDER})"
    return
  fi

  if [[ -f "${LEGACY_CONFIG_PATH}" ]]; then
    legacy_key="$(jq -er '.providers.openrouter.apiKey // .providers.openrouter.api_key // empty | strings | select(length > 0)' "${LEGACY_CONFIG_PATH}" 2>/dev/null || true)"
  fi
  [[ -n "${legacy_key}" ]] || fail "no LLM key found; export LLM_API_KEY or OPENROUTER_API_KEY"

  if [[ "${legacy_key}" == env://* ]]; then
    referenced_variable="${legacy_key#env://}"
    [[ -n "${referenced_variable}" ]] || fail "empty env reference in ${LEGACY_CONFIG_PATH}"
    legacy_key="${!referenced_variable:-}"
    [[ -n "${legacy_key}" ]] || fail "${referenced_variable} referenced by legacy config is unset"
  fi

  export LLM_PROVIDER="${LLM_PROVIDER:-openrouter}"
  export LLM_API_KEY="${legacy_key}"
  chmod go-rwx "${LEGACY_CONFIG_PATH}" 2>/dev/null || true
  legacy_key=""
  info "LLM credentials: migrated in-memory from legacy OpenRouter config (${LLM_PROVIDER})"
}

prepare_database() {
  local database_exists=""
  if [[ -n "${JOYHOUSEBOT_DATABASE_URL:-}" ]]; then
    info "PostgreSQL: using JOYHOUSEBOT_DATABASE_URL"
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

install_local_plugins() {
  local plugin_path
  local plugin_paths=()
  [[ -n "${LOCAL_PLUGIN_PACKAGES}" ]] || return

  IFS=':' read -r -a plugin_paths <<< "${LOCAL_PLUGIN_PACKAGES}"
  for plugin_path in "${plugin_paths[@]}"; do
    [[ -n "${plugin_path}" ]] || continue
    [[ -f "${plugin_path}/pyproject.toml" ]] || \
      fail "plugin package is missing pyproject.toml: ${plugin_path}"
    info "installing local capability plugin: ${plugin_path}"
    uv pip install --python "${ROOT_DIR}/.venv/bin/python" -e "${plugin_path}" --quiet
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
  [[ "${LOCAL_PG_PASSWORD}" =~ ^[A-Za-z0-9._~-]+$ ]] || fail "set JOYHOUSEBOT_DATABASE_URL when the local PostgreSQL password requires URL encoding"
  if lsof -nP -iTCP:"${API_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "TCP port ${API_PORT} is already in use"
  fi

  resolve_llm_credentials
  prepare_database
  mkdir -p "${LOG_DIR}"
  chmod 600 "${CONFIG_PATH}" 2>/dev/null || true

  info "synchronizing Python environment"
  # Keep the repository's development tools installed.  A plain `uv sync`
  # prunes optional dev dependencies and can make a later `uv run pytest`
  # resolve to an unrelated system executable.
  uv sync --frozen --extra dev --quiet
  install_local_plugins
  uv run joyhousebot check --config "${CONFIG_PATH}"

  start_role api uv run joyhousebot api \
    --surface combined --config "${CONFIG_PATH}" --host "${API_HOST}" --port "${API_PORT}"
  start_role scheduler uv run joyhousebot scheduler --config "${CONFIG_PATH}"
  for worker_index in $(seq 1 "${WORKER_COUNT}"); do
    start_role "worker-${worker_index}" uv run joyhousebot worker --config "${CONFIG_PATH}"
  done

  wait_for_api
  info "ready: http://${API_HOST}:${API_PORT}/ui/"
  info "press Ctrl+C to stop API, Scheduler and ${WORKER_COUNT} Workers"
  tail -n 20 -F "${LOG_DIR}"/*.log &
  TAIL_PID="$!"
  monitor_children
}

main "$@"
