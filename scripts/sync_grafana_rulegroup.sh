#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------- #
# sync_grafana_rulegroup.sh                                                     #
#                                                                               #
# Publish AV Tools Grafana alert rule groups via the Alerting Provisioning      #
# "rule-group PUT" API.                                                         #
#                                                                               #
# Source of truth: every PROD payload under                                     #
#     grafana/alerts/*.rulegroup.PUT.json                                       #
# The QA variant is DERIVED at deploy time by patch_grafana_rulegroup.py        #
# (folder/group/uid/title/receiver/datasource/env-label substitutions).        #
# There is no hand-maintained QA JSON.                                          #
#                                                                               #
# Usage:                                                                        #
#   ./scripts/sync_grafana_rulegroup.sh {prod|qa|both}                          #
#   ./scripts/sync_grafana_rulegroup.sh put {prod|qa|both}   # via QA wrapper   #
#                                                                               #
# Required env:                                                                 #
#   GRAFANA_API_TOKEN   service-account token for the target org                #
# Optional env:                                                                 #
#   GRAFANA_URL         defaults to https://monit-grafana.cern.ch               #
# ---------------------------------------------------------------------------- #

GRAFANA_URL="${GRAFANA_URL:-https://monit-grafana.cern.ch}"
: "${GRAFANA_API_TOKEN:?ERROR: GRAFANA_API_TOKEN is not set}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASEDIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PATCHER="${SCRIPT_DIR}/patch_grafana_rulegroup.py"
ALERTS_DIR="${BASEDIR}/grafana/alerts"

usage() {
  echo "Usage: $0 {prod|qa|both}" >&2
  echo "       $0 put {prod|qa|both}" >&2
  exit 2
}

# Read a top-level string field from a JSON file without requiring jq.
json_field() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"
}

put_one() {
  local env="$1" src="$2"
  local tmp_json tmp_body group folder url http
  tmp_json="$(mktemp)"
  tmp_body="$(mktemp)"
  trap "rm -f '$tmp_json' '$tmp_body'" RETURN

  # Render the env-specific payload, then take the group/folder straight from
  # what the patcher wrote so the URL can never disagree with the body.
  python3 "$PATCHER" "$env" "$src" "$tmp_json"
  group="$(json_field "$tmp_json" title)"
  folder="$(json_field "$tmp_json" folderUid)"

  url="${GRAFANA_URL}/api/v1/provisioning/folder/${folder}/rule-groups/${group}"
  echo "PUT  ${url}   <- $(basename "$src")"

  http="$(curl -sS -o "$tmp_body" -w "%{http_code}" -X PUT \
    -H "Authorization: Bearer ${GRAFANA_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary @"${tmp_json}" \
    "${url}")"

  if [[ "$http" =~ ^2 ]]; then
    echo "OK:  ${env^^} group '${group}' synced"
  else
    echo "ERROR: HTTP ${http} for group '${group}'" >&2
    cat "$tmp_body" >&2
    echo >&2
    return 1
  fi
}

put_env() {
  local env="$1"
  local found=0 rc=0 f
  shopt -s nullglob
  for f in "${ALERTS_DIR}"/*.rulegroup.PUT.json; do
    found=1
    put_one "$env" "$f" || rc=1
  done
  shopt -u nullglob
  if [[ "$found" -eq 0 ]]; then
    echo "ERROR: no rule-group payloads found in ${ALERTS_DIR}" >&2
    return 1
  fi
  return "$rc"
}

main() {
  local arg1="${1:-}" arg2="${2:-}"

  # Support both calling conventions:
  #   sync_grafana_rulegroup.sh {prod|qa|both}        (direct CI call)
  #   sync_grafana_rulegroup.sh put {prod|qa|both}    (called via QA wrapper)
  local env
  if [[ "$arg1" == "put" && -n "$arg2" ]]; then
    env="$arg2"
  else
    env="$arg1"
  fi

  [[ -f "$PATCHER" ]] || { echo "ERROR: patcher not found: $PATCHER" >&2; exit 1; }

  case "$env" in
    prod) put_env prod ;;
    qa)   put_env qa ;;
    both) put_env qa; put_env prod ;;
    *) usage ;;
  esac
}

main "$@"
