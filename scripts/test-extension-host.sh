#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${REPO_ROOT}/sdks/node"
ECHO_HOST_DIR="${REPO_ROOT}/hosts/node/fixtures/echo-host"
SUPERVISOR_DIR="${REPO_ROOT}/hosts/node/supervisor"
DEVICE_HOST_DIR="${REPO_ROOT}/hosts/node/device-host"
OPENCLI_DIR="${REPO_ROOT}/extensions/capability-opencli"

npm --prefix "${SDK_DIR}" ci
npm --prefix "${SDK_DIR}" run typecheck
npm --prefix "${SDK_DIR}" test

npm --prefix "${ECHO_HOST_DIR}" ci
npm --prefix "${ECHO_HOST_DIR}" run typecheck
npm --prefix "${ECHO_HOST_DIR}" run build

npm --prefix "${SUPERVISOR_DIR}" ci
npm --prefix "${SUPERVISOR_DIR}" run typecheck
npm --prefix "${SUPERVISOR_DIR}" test

npm --prefix "${DEVICE_HOST_DIR}" ci
npm --prefix "${DEVICE_HOST_DIR}" run typecheck
npm --prefix "${DEVICE_HOST_DIR}" test

# OpenCLI is pinned by npm integrity. Ignore dependency lifecycle scripts during
# validation; Desktop provisioning performs any reviewed Browser Bridge setup
# explicitly after package verification.
npm --prefix "${OPENCLI_DIR}" ci --ignore-scripts
npm --prefix "${OPENCLI_DIR}" run typecheck
npm --prefix "${OPENCLI_DIR}" test

cd "${REPO_ROOT}"
uv run --frozen python -m pytest -q \
  tests/test_extension_host_contract.py \
  tests/test_node_extension_host_integration.py \
  tests/test_device_host_transport.py
