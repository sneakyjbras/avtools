#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AVTOOLS_SCRIPT="$SCRIPT_DIR/avtools_client.sh"

# Optional: log file
LOG_FILE="$SCRIPT_DIR/snmp_influx.log"

# Infinite loop
while true; do
  echo "[$(date)] Running snmp-influx..." | tee -a "$LOG_FILE"

  "$AVTOOLS_SCRIPT" snmp-influx "$@" >> "$LOG_FILE" 2>&1

  echo "[$(date)] Completed. Sleeping for 5 minutes..." | tee -a "$LOG_FILE"
  sleep 90
done
