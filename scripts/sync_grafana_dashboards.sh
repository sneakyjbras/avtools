#!/usr/bin/env bash
set -euo pipefail

# -------------------------------------------------------------------
# Grafana Dashboards Sync (MonIT)
#
# Syncs DASHBOARDS (panels live inside dashboards unless Library Panels).
#
# Repo layout:
#   BASE_DIR/
#     grafana/
#       dashboard/
#         prod/
#         qa/
#     scripts/
#       sync_grafana_dashboards.sh  (this file)
#
# Saves to: ../grafana/dashboard/{prod,qa} relative to BASE_DIR/scripts
#
# Filters:
#   - Dashboard TITLE must start with DASH_TITLE_PREFIX (default: "AV:")
#   - Dashboard UID   must start with DASH_UID_PREFIX   (default: "av_")
#
# File naming:
#   - Saved as <uid>.json (filename matches uid)
#
# Requires:
#   - env var GRAFANA_API_TOKEN set (Service Account token)
#
# Usage:
#   ./scripts/sync_grafana_dashboards.sh get [all|prod|qa]
#   ./scripts/sync_grafana_dashboards.sh put [all|prod|qa]
# -------------------------------------------------------------------

ACTION="${1:-}"
SCOPE="${2:-all}"   # all|prod|qa

if [[ "$ACTION" != "get" && "$ACTION" != "put" ]]; then
  echo "Usage: $0 {get|put} [all|prod|qa]"
  exit 2
fi

: "${GRAFANA_API_TOKEN:?Missing env var GRAFANA_API_TOKEN}"

# Anchor paths relative to BASE_DIR/scripts no matter where you run from
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$SCRIPT_DIR"

GRAFANA_URL="${GRAFANA_URL:-https://monit-grafana.cern.ch}"

# Folder UIDs from your URLs
PROD_FOLDER_UID="${PROD_FOLDER_UID:-beuar1of5bo5cf}"   # av-dashboard (prod)
QA_FOLDER_UID="${QA_FOLDER_UID:-7BZSQJX4z}"             # playground (qa)

# Always save to BASE_DIR/grafana/dashboard/{prod,qa}
PROD_DIR="${PROD_DIR:-../grafana/dashboard/prod}"
QA_DIR="${QA_DIR:-../grafana/dashboard/qa}"

# Filter rules
DASH_TITLE_PREFIX="${DASH_TITLE_PREFIX:-AV:}"  # title starts with "AV:" (often "AV: " also matches)
DASH_UID_PREFIX="${DASH_UID_PREFIX:-av_}"      # uid starts with "av_"

curl_json_to_file() {
  local url="$1"
  local out="$2"
  curl -sS --fail \
    -H "Authorization: Bearer ${GRAFANA_API_TOKEN}" \
    -H "Accept: application/json" \
    "$url" \
    -o "$out"
}

# Get folderId from folderUid by listing folders
folder_id_from_uid() {
  local uid="$1"
  local tmp
  tmp="$(mktemp)"
  curl_json_to_file "${GRAFANA_URL}/api/folders?limit=5000" "$tmp"

  python3 - "$tmp" "$uid" <<'PY'
import json, sys
path, uid = sys.argv[1], sys.argv[2]
folders = json.load(open(path, "r", encoding="utf-8"))
for f in folders:
    if f.get("uid") == uid:
        print(f["id"])
        raise SystemExit(0)
print(f"ERROR: Could not find folder uid={uid} in /api/folders output", file=sys.stderr)
raise SystemExit(1)
PY

  rm -f "$tmp"
}

# List dashboard UIDs in a folderId, filtered by TITLE prefix and UID prefix
list_dashboards_in_folder_id() {
  local folder_id="$1"
  local tmp
  tmp="$(mktemp)"
  curl_json_to_file "${GRAFANA_URL}/api/search?type=dash-db&limit=5000&folderIds=${folder_id}" "$tmp"

  python3 - "$tmp" "$DASH_TITLE_PREFIX" "$DASH_UID_PREFIX" <<'PY'
import json, sys
path, title_prefix, uid_prefix = sys.argv[1], sys.argv[2], sys.argv[3]
items = json.load(open(path, "r", encoding="utf-8"))

for it in items:
    title = (it.get("title") or "")
    uid = (it.get("uid") or "")
    if uid and title.startswith(title_prefix) and uid.startswith(uid_prefix):
        print(uid)
PY

  rm -f "$tmp"
}

get_dashboard_wrapper_to_tmp() {
  local uid="$1"
  local tmp="$2"
  curl_json_to_file "${GRAFANA_URL}/api/dashboards/uid/${uid}" "$tmp"
}

extract_dashboard_to_file() {
  local wrapper_file="$1"
  local out_file="$2"
  python3 - "$wrapper_file" "$out_file" <<'PY'
import json, sys
wrapper_path, out_path = sys.argv[1], sys.argv[2]
wrapper = json.load(open(wrapper_path, "r", encoding="utf-8"))
dash = wrapper.get("dashboard")
if not isinstance(dash, dict):
    raise SystemExit("ERROR: /api/dashboards/uid returned no 'dashboard' object")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(dash, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY
}

build_dashboard_post_payload() {
  local dash_file="$1"
  local folder_uid="$2"
  local out_payload="$3"
  python3 - "$dash_file" "$folder_uid" "$out_payload" <<'PY'
import json, sys
dash_file, folder_uid, out_payload = sys.argv[1], sys.argv[2], sys.argv[3]
dash = json.load(open(dash_file, "r", encoding="utf-8"))

# Import-friendly adjustments
dash["id"] = None
dash.setdefault("uid", dash.get("uid"))
dash["version"] = 0

payload = {
  "dashboard": dash,
  "folderUid": folder_uid,
  "overwrite": True,
  "message": "GitLab sync"
}

with open(out_payload, "w", encoding="utf-8") as f:
  json.dump(payload, f, indent=2, ensure_ascii=False)
  f.write("\n")
PY
}

post_dashboard_payload() {
  local payload_file="$1"
  curl -sS --fail -X POST \
    -H "Authorization: Bearer ${GRAFANA_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary @"$payload_file" \
    "${GRAFANA_URL}/api/dashboards/db" >/dev/null
}

sync_get_folder() {
  local folder_uid="$1"
  local out_dir="$2"

  local folder_id
  folder_id="$(folder_id_from_uid "$folder_uid")"

  mkdir -p "$out_dir"
  echo "Fetching dashboards (title startswith '${DASH_TITLE_PREFIX}', uid startswith '${DASH_UID_PREFIX}') from folderUid=$folder_uid (folderId=$folder_id) -> $out_dir"

  while read -r duid; do
    [[ -z "$duid" ]] && continue
    echo "  GET dashboard uid=$duid"
    tmp="$(mktemp)"
    get_dashboard_wrapper_to_tmp "$duid" "$tmp"
    # filename = uid
    extract_dashboard_to_file "$tmp" "${out_dir}/${duid}.json"
    rm -f "$tmp"
  done < <(list_dashboards_in_folder_id "$folder_id")
}

sync_put_folder() {
  local folder_uid="$1"
  local in_dir="$2"

  if [[ ! -d "$in_dir" ]]; then
    echo "Nothing to upload: $in_dir does not exist"
    return 0
  fi

  echo "Uploading dashboards from $in_dir -> folderUid=$folder_uid (only files whose uid startswith '${DASH_UID_PREFIX}')"

  shopt -s nullglob
  for f in "$in_dir"/*.json; do
    base="$(basename "$f")"
    uid="${base%.json}"

    # Only push dashboards matching uid prefix
    if [[ "$uid" != "${DASH_UID_PREFIX}"* ]]; then
      continue
    fi

    echo "  PUT dashboard uid=$uid from file=$f"
    payload="$(mktemp)"
    build_dashboard_post_payload "$f" "$folder_uid" "$payload"
    post_dashboard_payload "$payload"
    rm -f "$payload"
  done
  shopt -u nullglob
}

if [[ "$ACTION" == "get" ]]; then
  [[ "$SCOPE" == "all" || "$SCOPE" == "prod" ]] && sync_get_folder "$PROD_FOLDER_UID" "$PROD_DIR"
  [[ "$SCOPE" == "all" || "$SCOPE" == "qa" ]]   && sync_get_folder "$QA_FOLDER_UID" "$QA_DIR"
  exit 0
fi

if [[ "$ACTION" == "put" ]]; then
  [[ "$SCOPE" == "all" || "$SCOPE" == "prod" ]] && sync_put_folder "$PROD_FOLDER_UID" "$PROD_DIR"
  [[ "$SCOPE" == "all" || "$SCOPE" == "qa" ]]   && sync_put_folder "$QA_FOLDER_UID" "$QA_DIR"
  exit 0
fi
