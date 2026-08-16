#!/usr/bin/env bash
# Verify that a commit is reproducible as a release candidate. This is
# intentionally stricter than pre_release_check.sh: a release must never
# silently include local source, generated UI, or deployment changes.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

require_clean_worktree() {
  local changes
  changes="$(git status --porcelain=v1 --untracked-files=all)"
  if [[ -n "${changes}" ]]; then
    echo "release-candidate: blocked: the worktree is not clean." >&2
    echo "Commit, stash, or explicitly remove the following changes before packaging:" >&2
    echo "${changes}" >&2
    return 1
  fi
}

require_clean_worktree
git diff --check

echo "==> 1. Release candidate source is clean"
echo "commit: $(git rev-parse HEAD)"

echo "==> 2. Runtime, extensions, console, and schema checks"
bash scripts/pre_release_check.sh

# UI compilation is expected to be deterministic and checked in. Re-checking
# here catches an out-of-date static bundle before a wheel is made public.
require_clean_worktree

echo "==> 3. Build a wheel from this exact commit"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/porthouse-release.XXXXXX")"
cleanup() {
  rm -rf "${BUILD_DIR}"
}
trap cleanup EXIT

uv run --frozen --with build python -m build --wheel --outdir "${BUILD_DIR}"
WHEEL="$(find "${BUILD_DIR}" -maxdepth 1 -type f -name '*.whl' -print -quit)"
if [[ -z "${WHEEL}" ]]; then
  echo "release-candidate: blocked: wheel build produced no wheel." >&2
  exit 1
fi
echo "wheel: $(basename "${WHEEL}")"
shasum -a 256 "${WHEEL}"

echo "==> 4. Deployment composition parses"
# This is schema/interpolation validation, not a deployment. Supply obviously
# non-production placeholders so a clean checkout can be verified without
# exporting or printing operator secrets.
POSTGRES_PASSWORD="release-candidate-placeholder" \
PORTHOUSE_METRICS_TOKEN="release-candidate-placeholder" \
PORTHOUSE_AUTH_ENCRYPTION_KEY="release-candidate-placeholder" \
LLM_PROVIDER="release-candidate-provider" \
LLM_MODEL="release-candidate-model" \
docker compose -f docker-compose.runtime.yml config --quiet

require_clean_worktree
echo "release-candidate: PASS"
