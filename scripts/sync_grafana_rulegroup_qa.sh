#!/usr/bin/env bash
set -euo pipefail

# Convenience wrapper: deploy the rule group to QA with QA datasource + receiver.
#
# Usage:
#   ./scripts/sync_grafana_rulegroup_qa.sh get
#   ./scripts/sync_grafana_rulegroup_qa.sh put
#
# Required env:
#   GRAFANA_API_TOKEN   (QA service-account token)

ACTION="${1:-}"
if [[ "$ACTION" != "get" && "$ACTION" != "put" ]]; then
  echo "Usage: $0 {get|put}" >&2
  exit 2
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$SCRIPT_DIR"

# Enforce your QA defaults (can be overridden if needed)
export QA_DS_UID="${QA_DS_UID:-dfaue906qonpcf}"
export QA_RECEIVER="${QA_RECEIVER:-AV Test}"
export QA_FOLDER_UID="${QA_FOLDER_UID:-7BZSQJX4z}"

# Make QA rules clearly distinct
export QA_UID_SUFFIX="${QA_UID_SUFFIX:--qa}"
export QA_TITLE_PREFIX="${QA_TITLE_PREFIX:-QA - }"

# Make the QA rulegroup name distinct in Grafana (visible in Alert Rules UI)
export RULEGROUP_BASE="${RULEGROUP_BASE:-avtools-eam-dq-weekly}"
export RULEGROUP_QA="${RULEGROUP_QA:-${RULEGROUP_BASE}-qa}"

exec ./sync_grafana_rulegroup.sh "$ACTION" qa
