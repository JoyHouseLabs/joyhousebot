#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

uv run --frozen python -m compileall -q joyhousebot extensions/*/src
uv run --frozen python -m pytest
uv run --frozen python -m ruff check joyhousebot tests extensions/*/src
(cd apps/console && npm run typecheck && npm run build)
git diff --check

echo "pre_release_check: PASS"
