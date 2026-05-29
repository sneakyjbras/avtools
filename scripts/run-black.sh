#!/usr/bin/env bash
# Run Black against Python source, tests, and Python helper scripts.
#
# Usage:
#   ./scripts/run-black.sh           # format in place
#   ./scripts/run-black.sh --check   # dry-run; exits non-zero if files need reformatting
#
# Any extra arguments are forwarded directly to Black.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if ! command -v poetry >/dev/null 2>&1; then
  echo "poetry is required to run Black in the canonical developer workflow." >&2
  echo "Install Poetry and run 'poetry install --with lint' first." >&2
  exit 1
fi

exec poetry run black "$@" src tests scripts
