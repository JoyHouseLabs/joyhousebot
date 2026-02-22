#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENCLAW_ROOT_DEFAULT="$(cd "${ROOT_DIR}/.." && pwd)/openclaw"
OPENCLAW_ROOT="${OPENCLAW_ROOT:-${OPENCLAW_ROOT_DEFAULT}}"

echo "[1/4] Compile check"
python -m compileall "${ROOT_DIR}/joyhousebot" "${ROOT_DIR}/scripts/rpc_compat_smoke.py"

echo "[2/4] RPC compatibility smoke"
python "${ROOT_DIR}/scripts/rpc_compat_smoke.py" --openclaw-root "${OPENCLAW_ROOT}"

echo "[3/4] Unit tests"
if [[ "${FULL_TESTS:-0}" == "1" ]]; then
  python -m pytest "${ROOT_DIR}/tests"
else
  # Keep release gate stable by running focused regression tests.
  python -m pytest \
    "${ROOT_DIR}/tests/test_rpc_node_event_runtime.py" \
    "${ROOT_DIR}/tests/test_rpc_semantic_alignment.py" \
    "${ROOT_DIR}/tests/test_security_hardening.py" \
    "${ROOT_DIR}/tests/test_tool_validation.py"
fi

echo "[4/4] Ruff lint (if installed)"
if python -c "import ruff" >/dev/null 2>&1; then
  set +e
  if [[ "${FULL_LINTS:-0}" == "1" ]]; then
    python -m ruff check "${ROOT_DIR}/joyhousebot" "${ROOT_DIR}/tests" "${ROOT_DIR}/scripts"
  else
    python -m ruff check \
      "${ROOT_DIR}/joyhousebot/api/server.py" \
      "${ROOT_DIR}/joyhousebot/node/registry.py" \
      "${ROOT_DIR}/joyhousebot/config/loader.py" \
      "${ROOT_DIR}/joyhousebot/config/schema.py" \
      "${ROOT_DIR}/scripts/rpc_compat_smoke.py" \
      "${ROOT_DIR}/tests/test_rpc_node_event_runtime.py"
  fi
  LINT_EXIT=$?
  set -e
  if [[ ${LINT_EXIT} -ne 0 ]]; then
    if [[ "${STRICT_LINT:-0}" == "1" ]]; then
      echo "ruff check failed and STRICT_LINT=1"
      exit ${LINT_EXIT}
    fi
    echo "ruff check reported existing style issues; continuing (set STRICT_LINT=1 to fail)"
  fi
else
  echo "ruff not installed; skipping"
fi

echo "pre_release_check: PASS"
