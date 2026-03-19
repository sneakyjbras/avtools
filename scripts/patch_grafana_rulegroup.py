#!/usr/bin/env python3
import json
import copy
import sys
from pathlib import Path


# ---- PROD settings
PROD_FOLDER_UID = "beuar1of5bo5cf"
PROD_GROUP = "avtools-eam-dq-weekly"
PROD_RECEIVER = "AV Tools"
PROD_DS_UID = "ed690575-af6b-41b8-a72d-81f47592f349"
PROD_DASH_UID = "av_devices_dashboard"

# ---- QA settings
QA_FOLDER_UID = "7BZSQJX4z"
QA_GROUP = "avtools-eam-dq-weekly-qa"
QA_RECEIVER = "AV Test"
QA_DS_UID = "dfaue906qonpcf"
QA_DASH_UID = "av_devices_dashboard-qa"


def usage() -> None:
    print(
        "Usage:\n"
        "  patch_grafana_rulegroup.py <env> <src_json> <out_json>\n"
        "Where:\n"
        "  <env>      prod | qa\n"
        "  <src_json> path to avtools-eam-dq-weekly.rulegroup.PUT.json\n"
        "  <out_json> output patched payload\n",
        file=sys.stderr,
    )
    raise SystemExit(2)


def patch_payload(env: str, src_path: Path, out_path: Path) -> None:
    with src_path.open("r", encoding="utf-8") as f:
        base = json.load(f)

    if env == "prod":
        folder = PROD_FOLDER_UID
        group = PROD_GROUP
        receiver = PROD_RECEIVER
        target_ds = PROD_DS_UID
        dash_uid = PROD_DASH_UID
        uid_suffix = ""
        title_prefix = ""
    elif env == "qa":
        folder = QA_FOLDER_UID
        group = QA_GROUP
        receiver = QA_RECEIVER
        target_ds = QA_DS_UID
        dash_uid = QA_DASH_UID
        uid_suffix = "-qa"
        title_prefix = "QA - "
    else:
        raise SystemExit(f"Unknown env: {env!r} (expected 'prod' or 'qa')")

    payload = copy.deepcopy(base)

    # These two are for the rule-group payload wrapper
    payload["folderUid"] = folder
    payload["title"] = group

    for rule in payload.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue

        # IMPORTANT: avoid PK conflicts / re-sync weirdness
        rule.pop("id", None)

        # Folder + group placement
        rule["folderUID"] = folder
        rule["ruleGroup"] = group

        # UID handling (QA must be separate set)
        uid = rule.get("uid", "")
        if isinstance(uid, str):
            if uid_suffix:
                if not uid.endswith(uid_suffix):
                    rule["uid"] = uid + uid_suffix
            else:
                if uid.endswith("-qa"):
                    rule["uid"] = uid[:-3]

        # Title (optional, keeps QA visually distinct)
        title = rule.get("title", "")
        if isinstance(title, str):
            if title_prefix:
                if not title.startswith(title_prefix):
                    rule["title"] = title_prefix + title
            else:
                if title.startswith("QA - "):
                    rule["title"] = title[5:]

        # Receiver/contact point
        ns = rule.get("notification_settings") or {}
        if not isinstance(ns, dict):
            ns = {}
        ns["receiver"] = receiver
        rule["notification_settings"] = ns

        # Dashboard link: update only __dashboardUid__ (keep panel id)
        ann = rule.get("annotations") or {}
        if not isinstance(ann, dict):
            ann = {}
        if "__dashboardUid__" in ann:
            ann["__dashboardUid__"] = dash_uid
        rule["annotations"] = ann

        # Datasource patch: replace occurrences of PROD datasource uid
        for q in rule.get("data", []) or []:
            if not isinstance(q, dict):
                continue

            if q.get("datasourceUid") == PROD_DS_UID:
                q["datasourceUid"] = target_ds

            model = q.get("model") or {}
            if isinstance(model, dict):
                ds = model.get("datasource")
                if isinstance(ds, dict) and ds.get("uid") == PROD_DS_UID:
                    ds["uid"] = target_ds

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False, ensure_ascii=False)
        f.write("\n")


def main() -> None:
    if len(sys.argv) != 4:
        usage()

    env = sys.argv[1].strip()
    src = Path(sys.argv[2])
    out = Path(sys.argv[3])

    if not src.exists():
        raise SystemExit(f"Source JSON not found: {src}")

    patch_payload(env, src, out)


if __name__ == "__main__":
    main()
