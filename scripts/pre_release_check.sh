#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

uv run --frozen python -m compileall -q joyhousebot
uv run --frozen python -m pytest
uv run --frozen python -m ruff check joyhousebot tests
(cd frontend && npm run typecheck && npm run build)
git diff --check

echo "pre_release_check: PASS"
