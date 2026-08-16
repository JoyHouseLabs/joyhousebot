#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

# Extension integration tests assert installed distribution entry points. Keep
# Core-only validation separate; a release candidate validates the complete
# supported extension surface from the checked-in source packages.
bash scripts/install-test-extensions.sh
bash scripts/ensure-test-pgvector.sh
bash scripts/test-extension-host.sh
uv run --frozen python scripts/verify-node-runtime-lock.py
uv run --frozen python -m compileall -q joyhousebot extensions/*/src
uv run --frozen python -m pytest
uv run --frozen python -m ruff check joyhousebot tests extensions/*/src
(cd apps/console && npm run typecheck && npm run build)
git diff --check

echo "pre_release_check: PASS"
