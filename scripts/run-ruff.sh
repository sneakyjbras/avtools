#!/usr/bin/env bash
# Run Ruff checks against Python source, tests, and Python helper scripts.
#
# Usage:
#   ./scripts/run-ruff.sh            # lint; exits non-zero on any violation
#   ./scripts/run-ruff.sh --fix      # auto-fix safe violations in place
#
# Any extra arguments are forwarded directly to Ruff.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if ! command -v poetry >/dev/null 2>&1; then
  echo "poetry is required to run Ruff in the canonical developer workflow." >&2
  echo "Install Poetry and run 'poetry install --with lint' first." >&2
  exit 1
fi

exec poetry run ruff check "$@" src tests scripts
