#!/usr/bin/env bash
set -euo pipefail

#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------- #
# avtools_client.sh                                                             #
# Wrapper to invoke the avtools CLI via Poetry with parameters for InfluxDB 1.8 #
# and other sub-commands.                                                       #
#                                                                              #
# Usage:                                                                       #
#   ./avtools_client.sh <command> [--logs] [additional CLI args]               #
# Commands: run-eam, run-landb, snmp-influx                                     #
#                                                                              #
# Before running, export the required environment variables with your own values:
#
# Optionally, you can store these in a .env file in the same directory:
#   .env
#   MY_USERNAME=jsapinat
#   MY_PASSWORD=secret
#   DATABASE_URL=postgresql://...
#   ...
# and load them automatically below.
# ---------------------------------------------------------------------------- #

# Load from .env if present
if [[ -f .env ]]; then
  set -o allexport
  source .env
  set +o allexport
fi

# ensure the requests library sees your CERN CAs
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 {run-eam|run-landb|snmp-influx} [options]" >&2
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
    : "${LANDB_TOKEN_FILE:?Need to set LANDB_TOKEN_FILE}"
    : "${DATABASE_URL:?Need to set DATABASE_URL}"
    poetry run avtools \
	  --logs \
      --dbod-url "$DATABASE_URL" \
      run-landb \
        --token-file "$LANDB_TOKEN_FILE" \
        "$@"
    ;;

  snmp-influx)
    : "${INFLUX_HOST:?Need to set INFLUX_HOST}"
    : "${INFLUX_PORT:?Need to set INFLUX_PORT}"
    : "${INFLUX_USER:?Need to set INFLUX_USER}"
    : "${INFLUX_PASSWORD:?Need to set INFLUX_PASSWORD}"
    : "${INFLUX_DB:?Need to set INFLUX_DB}"
    : "${DATABASE_URL:?Need to set DATABASE_URL}"
	# default THREADS to 1 if not set
	: "${THREADS:=1}"
    poetry run avtools \
	  --logs \
      --dbod-url "$DATABASE_URL" \
      snmp-influx \
        --influx-host     "$INFLUX_HOST" \
        --influx-port     "$INFLUX_PORT" \
        --influx-user     "$INFLUX_USER" \
        --influx-password "$INFLUX_PASSWORD" \
        --influx-db       "$INFLUX_DB" \
		--threads		  "$THREADS" \
        "$@"
    ;;

  *)
    echo "Unknown command: ${COMMAND}" >&2
    echo "Usage: $0 {run-eam|run-landb|snmp-influx} [options]" >&2
    exit 1
    ;;
esac
