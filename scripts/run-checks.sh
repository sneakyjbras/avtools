#!/usr/bin/env bash
# Run local pre-commit quality checks for av-tools in three ordered steps:
#
#   1. Formatting check  — Black --check  (shows what would change; non-blocking)
#   2. Formatting apply  — Black          (applies changes in place)
#   3. Linting           — Ruff           (reports remaining violations)
#
# The check step runs first so any reformatting diff is visible in the terminal
# before Black applies it. Linting starts only after formatting is complete.
# A final summary reports the outcome of each step.
#
# Exit codes:
#   0  — formatting applied cleanly and linting passed
#   1  — formatting apply failed or linting found violations
#
# The formatting check (step 1) is informational only: a non-zero exit there
# means files needed reformatting, which step 2 then fixes. It does not by
# itself cause the script to exit with failure.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

if ! command -v poetry >/dev/null 2>&1; then
  echo "poetry is required. Install Poetry and run 'poetry install --with lint' first." >&2
  exit 1
fi

FORMAT_CHECK_STATUS=0
FORMAT_APPLY_STATUS=0
FORMAT_CHANGED="unknown"
LINT_STATUS=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

has_git_worktree() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1
}

run_step() {
  local label="$1"
  shift
  echo
  echo "==> ${label}"
  "$@"
}

# ---------------------------------------------------------------------------
# Step 1: Formatting check (Black --check)
#
# Exits non-zero when files need reformatting — that is expected here and
# does not abort the script.  The diff shown in the terminal tells the
# developer exactly what step 2 will change.
# ---------------------------------------------------------------------------

run_step "Formatting check: Black --check" \
  poetry run black --check src tests scripts \
  || FORMAT_CHECK_STATUS=$?

# ---------------------------------------------------------------------------
# Step 2: Formatting apply (Black)
#
# Snapshot git state before and after so we can report whether any files were
# actually modified.
# ---------------------------------------------------------------------------

before_diff="$(mktemp)"
if has_git_worktree; then
  git diff --binary > "${before_diff}"
fi

run_step "Formatting apply: Black" \
  poetry run black src tests scripts \
  || FORMAT_APPLY_STATUS=$?

if has_git_worktree; then
  after_diff="$(mktemp)"
  git diff --binary > "${after_diff}"
  if cmp -s "${before_diff}" "${after_diff}"; then
    FORMAT_CHANGED="no"
  else
    FORMAT_CHANGED="yes"
  fi
  rm -f "${before_diff}" "${after_diff}"
else
  rm -f "${before_diff}"
fi

echo
echo "==> Formatting finished. Starting linting."

# ---------------------------------------------------------------------------
# Step 3: Linting (Ruff)
# ---------------------------------------------------------------------------

run_step "Linting: Ruff" \
  poetry run ruff check src tests scripts \
  || LINT_STATUS=$?

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

format_check_summary() {
  if [[ "${FORMAT_CHECK_STATUS}" -eq 0 ]]; then
    echo "already formatted — no changes needed"
  else
    echo "reformatting was needed (Black applied changes; see diff above)"
  fi
}

format_apply_summary() {
  if [[ "${FORMAT_APPLY_STATUS}" -ne 0 ]]; then
    echo "failed"
    return
  fi
  case "${FORMAT_CHANGED}" in
    yes) echo "files were updated" ;;
    no)  echo "no changes applied" ;;
    *)   echo "completed (change detection unavailable outside git worktree)" ;;
  esac
}

lint_summary() {
  if [[ "${LINT_STATUS}" -eq 0 ]]; then
    echo "passed"
  else
    echo "failed"
  fi
}

echo
echo "Quality summary"
echo "---------------"
echo "Formatting check:  $(format_check_summary)"
echo "Formatting apply:  $(format_apply_summary)"
echo "Linting:           $(lint_summary)"

if has_git_worktree && git diff --quiet --exit-code; then
  echo "Working tree:      no tracked formatting changes"
elif has_git_worktree; then
  echo "Working tree:      tracked changes present; review with: git diff"
else
  echo "Working tree:      not checked"
fi

if [[ "${FORMAT_APPLY_STATUS}" -ne 0 || "${LINT_STATUS}" -ne 0 ]]; then
  echo
  echo "One or more checks failed. See the output above."
  exit 1
fi

echo
echo "All formatting and linting checks completed successfully."
