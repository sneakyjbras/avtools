#!/usr/bin/env bash
set -euo pipefail

GRAFANA_URL="${GRAFANA_URL:-https://monit-grafana.cern.ch}"
: "${GRAFANA_API_TOKEN:?ERROR: GRAFANA_API_TOKEN is not set}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASEDIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PATCHER="${SCRIPT_DIR}/patch_grafana_rulegroup.py"
SRC_JSON="${BASEDIR}/grafana/alerts/avtools-eam-dq-weekly.rulegroup.PUT.json"

# Targets
PROD_FOLDER_UID="beuar1of5bo5cf"
PROD_GROUP="avtools-eam-dq-weekly"

QA_FOLDER_UID="7BZSQJX4z"
QA_GROUP="avtools-eam-dq-weekly-qa"

usage() {
  echo "Usage: $0 {prod|qa|both}" >&2
  echo "       $0 put {prod|qa|both}" >&2
  exit 2
}

put_rulegroup() {
  local env="$1"
  local folder group receiver ds

  if [[ "$env" == "prod" ]]; then
    folder="$PROD_FOLDER_UID"
    group="$PROD_GROUP"
    receiver="AV Tools"
    ds="ed690575-af6b-41b8-a72d-81f47592f349"
  else
    folder="$QA_FOLDER_UID"
    group="$QA_GROUP"
    receiver="AV Test"
    ds="dfaue906qonpcf"
  fi

  local tmp_json tmp_body
  tmp_json="$(mktemp)"
  tmp_body="$(mktemp)"
  trap "rm -f '$tmp_json' '$tmp_body'" RETURN

  python3 "$PATCHER" "$env" "$SRC_JSON" "$tmp_json"

  local url="${GRAFANA_URL}/api/v1/provisioning/folder/${folder}/rule-groups/${group}"
  echo "PUT  ${url}"

  local http
  http="$(curl -sS -o "$tmp_body" -w "%{http_code}" -X PUT \
    -H "Authorization: Bearer ${GRAFANA_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary @"${tmp_json}" \
    "${url}")"

  if [[ "$http" =~ ^2 ]]; then
    echo "OK: synced ${env^^} rulegroup '${group}' (receiver: ${receiver}, datasource: ${ds})"
  else
    echo "ERROR: HTTP ${http}" >&2
    cat "$tmp_body" >&2
    exit 1
  fi
}

main() {
  local arg1="${1:-}"
  local arg2="${2:-}"

  # Support both calling conventions:
  #   sync_grafana_rulegroup.sh {prod|qa|both}          (direct CI call)
  #   sync_grafana_rulegroup.sh put {prod|qa|both}      (called via QA wrapper)
  local env
  if [[ "$arg1" == "put" && -n "$arg2" ]]; then
    env="$arg2"
  else
    env="$arg1"
  fi

  if [[ ! -f "$PATCHER" ]]; then
    echo "ERROR: patcher not found: $PATCHER" >&2
    exit 1
  fi
  if [[ ! -f "$SRC_JSON" ]]; then
    echo "ERROR: source JSON not found: $SRC_JSON" >&2
    exit 1
  fi

  case "$env" in
    prod) put_rulegroup prod ;;
    qa)   put_rulegroup qa ;;
    both) put_rulegroup qa; put_rulegroup prod ;;
    *) usage ;;
  esac
}

main "$@"
