#!/usr/bin/env bash
# Copy A2UI static assets from an OpenClaw build into joyhousebot so that
# the gateway can serve /__openclaw__/a2ui (OpenClaw-compatible).
#
# Usage:
#   OPENCLAW_DIR=/path/to/openclaw ./scripts/sync-a2ui-from-openclaw.sh
#   ./scripts/sync-a2ui-from-openclaw.sh /path/to/openclaw
#
# In OpenClaw repo, run first: pnpm canvas:a2ui:bundle
# Then run this script. Target: joyhousebot/joyhousebot/static/a2ui/

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOYHOUSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="$JOYHOUSE_ROOT/joyhousebot/static/a2ui"

OPENCLAW_ROOT="${OPENCLAW_DIR:-${1:-}}"
if [[ -z "$OPENCLAW_ROOT" ]]; then
  echo "Usage: OPENCLAW_DIR=/path/to/openclaw $0" >&2
  echo "   or: $0 /path/to/openclaw" >&2
  exit 1
fi
SRC_DIR="$OPENCLAW_ROOT/src/canvas-host/a2ui"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "OpenClaw A2UI source dir not found: $SRC_DIR" >&2
  echo "In the OpenClaw repo run: pnpm canvas:a2ui:bundle" >&2
  exit 1
fi
for f in index.html a2ui.bundle.js; do
  if [[ ! -f "$SRC_DIR/$f" ]]; then
    echo "Missing $SRC_DIR/$f. Run in OpenClaw: pnpm canvas:a2ui:bundle" >&2
    exit 1
  fi
done

mkdir -p "$TARGET_DIR"
cp "$SRC_DIR/index.html" "$SRC_DIR/a2ui.bundle.js" "$TARGET_DIR/"
echo "Synced A2UI assets -> $TARGET_DIR"
