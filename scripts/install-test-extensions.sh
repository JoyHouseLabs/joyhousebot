#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

[[ -x "${PYTHON_BIN}" ]] || {
  echo "missing ${PYTHON_BIN}; run 'uv sync --extra dev --frozen' first" >&2
  exit 1
}

arguments=()
for project in "${ROOT_DIR}"/extensions/*/pyproject.toml; do
  arguments+=(--editable "$(dirname "${project}")")
done

uv pip install --python "${PYTHON_BIN}" "${arguments[@]}"
