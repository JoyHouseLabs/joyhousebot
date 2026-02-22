#!/usr/bin/env bash
# Build the config UI and copy into the Python package so it is included in the wheel.
# Run from repo root: ./scripts/build-ui.sh
# Then: hatch build  (or pip install -e .)

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND="$ROOT/frontend"
STATIC="$ROOT/joyhousebot/static/ui"

cd "$FRONTEND"
if ! command -v npm &>/dev/null; then
  echo "npm not found; skip frontend build. Install Node.js to build the UI." >&2
  exit 0
fi
npm ci --prefer-offline --no-audit
npm run build

rm -rf "$STATIC"
mkdir -p "$STATIC"
cp -r dist/* "$STATIC"
echo "Built UI -> $STATIC"
