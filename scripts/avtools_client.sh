#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------- #
# avtools_client.sh                                                            #
# Poetry wrapper for AV Tools commands.                                        #
#                                                                              #
# Usage:
#   ./avtools_client.sh <command> [--logs] [additional CLI args]
#                                                                              #
# Commands:
#   run-eam, run-landb, snmp-timeseries
#                                                                              #
# Notes:
#   - snmp-timeseries publishes metrics to Prometheus (Mimir) via MONIT OTLP.
#   - Devices to probe are loaded from the Postgres table `landb_ipaddresses`
#     (columns: equipmentno, ipv4).
#   - Put secrets in a local .env file (not committed).
# ---------------------------------------------------------------------------- #

# Optional: define defaults (prefer using .env instead)
: "${MY_USERNAME:=}"
: "${MY_PASSWORD:=}"
: "${LANDB_CLIENT_ID:=}"
: "${LANDB_CLIENT_SECRET:=}"
: "${LANDB_CLIENT_QA_ID:=}"
: "${LANDB_CLIENT_QA_SECRET:=}"
: "${LANDB_AUDIENCE:=}"
: "${DATABASE_URL:=}"
: "${LANDB_TOKEN_FILE:=}"

# MONIT / OTLP (Prometheus)
: "${MONIT_TENANT:=avtools}"
: "${MONIT_PASSWORD:=QulkOZ41pFp4}"
: "${MONIT_OTLP_ENDPOINT:=monit-otlp.cern.ch:4316}"

# Concurrency
: "${THREADS:=8}"

# Load from .env if present
if [[ -f .env ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi

# Optional: ensure Requests sees CERN CAs (deployment may already set this)
export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 {run-eam|run-landb|snmp-timeseries} [options]" >&2
  exit 1
fi

COMMAND="$1"
shift

case "${COMMAND}" in

  run-eam)
    : "${MY_USERNAME:?Need to set MY_USERNAME}"
    : "${MY_PASSWORD:?Need to set MY_PASSWORD}"
    : "${DATABASE_URL:?Need to set DATABASE_URL}"
    poetry run avtools \
      --logs \
      --dbod-url "$DATABASE_URL" \
      run-eam \
        --username "$MY_USERNAME" \
        --password "$MY_PASSWORD" \
        "$@"
    ;;

  run-landb)
    : "${LANDB_CLIENT_ID:?Need to set LANDB_CLIENT_ID}"
    : "${LANDB_CLIENT_SECRET:?Need to set LANDB_CLIENT_SECRET}"
    : "${LANDB_AUDIENCE:?Need to set LANDB_AUDIENCE}"
    : "${DATABASE_URL:?Need to set DATABASE_URL}"
    poetry run avtools \
      --logs \
      --dbod-url "$DATABASE_URL" \
      run-landb \
        --client-id "$LANDB_CLIENT_ID" \
        --client-secret "$LANDB_CLIENT_SECRET" \
        --audience "$LANDB_AUDIENCE" \
        "$@"
    ;;

  snmp-timeseries)
    : "${MONIT_TENANT:?Need to set MONIT_TENANT}"
    : "${MONIT_PASSWORD:?Need to set MONIT_PASSWORD}"
    : "${DATABASE_URL:?Need to set DATABASE_URL}"
    : "${THREADS:=8}"
: "${MONIT_OTLP_ENDPOINT:=monit-otlp.cern.ch:4316}"
: "${MONIT_OTLP_INSECURE:=1}"

OTLP_SECURITY_FLAG=""
case "${MONIT_OTLP_INSECURE}" in
  1|true|TRUE|yes|YES|on|ON) OTLP_SECURITY_FLAG="--otlp-insecure" ;;
  0|false|FALSE|no|NO|off|OFF) OTLP_SECURITY_FLAG="--otlp-tls" ;;
  *) OTLP_SECURITY_FLAG="--otlp-insecure" ;;  # safest default for :4316 (plaintext)
esac


    poetry run avtools \
      --logs \
      --dbod-url "$DATABASE_URL" \
      snmp-timeseries \
        $OTLP_SECURITY_FLAG \
        --tenant "$MONIT_TENANT" \
        --password "$MONIT_PASSWORD" \
        --otlp-endpoint "$MONIT_OTLP_ENDPOINT" \
        --threads "$THREADS" \
        "$@"
    ;;

  *)
    echo "Unknown command: ${COMMAND}" >&2
    echo "Usage: $0 {run-eam|run-landb|snmp-timeseries} [options]" >&2
    exit 1
    ;;
esac
