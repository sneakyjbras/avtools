#!/usr/bin/env python3
"""Render a PROD rule-group PUT payload for a target environment (prod | qa).

The repository keeps a single PROD source-of-truth JSON per rule group under
``grafana/alerts/*.rulegroup.PUT.json``.  The QA variant is *derived* from it at
deploy time by this script — there is no hand-maintained QA file to drift.

Per environment this applies a fixed, deterministic set of substitutions:

  * folder UID           (prod ``beuar1of5bo5cf``  ->  qa ``7BZSQJX4z`` / playground)
  * rule-group name      (``<base>``               ->  ``<base>-qa``)
  * rule uid suffix      (``""``                   ->  ``-qa``)
  * rule title prefix    (``""``                   ->  ``QA - ``)
  * notification receiver(``AV Tools``             ->  ``AV Test``)
  * dashboard link uid   (``av_devices_dashboard`` ->  ``av_devices_dashboard-qa``)
  * Postgres datasource  (prod uid                 ->  qa uid)
  * PromQL env matcher    ``submitter_environment="prod"`` -> ``..."qa"``

The Prometheus datasource UID is shared across environments and is intentionally
left untouched.  Running with ``prod`` reverses every substitution, so the
transform is idempotent and round-trips cleanly.

Usage:
  patch_grafana_rulegroup.py <env> <src_json> <out_json>
    <env>      prod | qa
    <src_json> path to a *.rulegroup.PUT.json (PROD source of truth)
    <out_json> output patched payload
"""
import copy
import json
import re
import sys
from pathlib import Path

# ---- PROD settings
PROD_FOLDER_UID = "beuar1of5bo5cf"
PROD_RECEIVER = "AV Tools"
PROD_DS_UID = "ed690575-af6b-41b8-a72d-81f47592f349"
PROD_DASH_UID = "av_devices_dashboard"

# ---- QA settings
QA_FOLDER_UID = "7BZSQJX4z"  # "playground" folder
QA_RECEIVER = "AV Test"
QA_DS_UID = "dfaue906qonpcf"
QA_DASH_UID = "av_devices_dashboard-qa"

# Postgres datasource UIDs across environments (the Prometheus UID is shared and
# never rewritten). Matching either side keeps the env transform bijective.
_PG_DS_UIDS = {PROD_DS_UID, QA_DS_UID}

# ---- env-independent conventions
QA_UID_SUFFIX = "-qa"
QA_TITLE_PREFIX = "QA - "

# Receiver/contact-point mapping prod -> qa. Applied PER RULE (preserving a rule's
# own route) rather than forcing every rule onto one receiver, so dedicated routes
# (e.g. the SLO route) survive the transform. Bijective: the reverse map recovers prod.
RECEIVER_MAP = {
    PROD_RECEIVER: QA_RECEIVER,  # "AV Tools" -> "AV Test"
    "AV SLO": "AV SLO Test",  # dedicated SLO route
}
_RECEIVER_MAP_REV = {v: k for k, v in RECEIVER_MAP.items()}


def _map_receiver(current: str, env: str) -> str:
    """Map a rule's receiver to the target env, bijectively (unknown -> unchanged)."""
    prod_name = _RECEIVER_MAP_REV.get(current, current)  # normalise qa->prod
    return prod_name if env == "prod" else RECEIVER_MAP.get(prod_name, prod_name)


# PromQL label matcher  submitter_environment="prod" | "qa"
_ENV_LABEL_RE = re.compile(r'(submitter_environment\s*=\s*")(prod|qa)(")')


def usage() -> None:
    print(__doc__, file=sys.stderr)
    raise SystemExit(2)


def _base_group(title: str) -> str:
    """Return the PROD base group name (strip a trailing ``-qa`` if present)."""
    title = title or ""
    if title.endswith(QA_UID_SUFFIX):
        return title[: -len(QA_UID_SUFFIX)]
    return title


def patch_payload(env: str, src_path: Path, out_path: Path) -> None:
    with src_path.open("r", encoding="utf-8") as f:
        base = json.load(f)

    if env == "prod":
        folder = PROD_FOLDER_UID
        target_ds = PROD_DS_UID
        dash_uid = PROD_DASH_UID
        env_label = "prod"
        uid_suffix = ""
        title_prefix = ""
    elif env == "qa":
        folder = QA_FOLDER_UID
        target_ds = QA_DS_UID
        dash_uid = QA_DASH_UID
        env_label = "qa"
        uid_suffix = QA_UID_SUFFIX
        title_prefix = QA_TITLE_PREFIX
    else:
        raise SystemExit(f"Unknown env: {env!r} (expected 'prod' or 'qa')")

    payload = copy.deepcopy(base)

    # Group name is derived from the source payload's own title (NOT hardcoded),
    # so this script works unchanged for every rule group in grafana/alerts/.
    base_group = _base_group(payload.get("title", ""))
    group = base_group + uid_suffix

    # Rule-group payload wrapper
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

        # UID handling (QA must be a separate set)
        uid = rule.get("uid", "")
        if isinstance(uid, str):
            if uid_suffix:
                if not uid.endswith(uid_suffix):
                    rule["uid"] = uid + uid_suffix
            else:
                if uid.endswith(QA_UID_SUFFIX):
                    rule["uid"] = uid[: -len(QA_UID_SUFFIX)]

        # Title (keeps QA visually distinct)
        title = rule.get("title", "")
        if isinstance(title, str):
            if title_prefix:
                if not title.startswith(title_prefix):
                    rule["title"] = title_prefix + title
            else:
                if title.startswith(QA_TITLE_PREFIX):
                    rule["title"] = title[len(QA_TITLE_PREFIX) :]

        # Receiver/contact point (per-rule map, preserves dedicated routes like SLO)
        ns = rule.get("notification_settings") or {}
        if not isinstance(ns, dict):
            ns = {}
        ns["receiver"] = _map_receiver(ns.get("receiver", PROD_RECEIVER), env)
        rule["notification_settings"] = ns

        # Dashboard link: update only __dashboardUid__ (keep panel id)
        ann = rule.get("annotations") or {}
        if not isinstance(ann, dict):
            ann = {}
        if "__dashboardUid__" in ann:
            ann["__dashboardUid__"] = dash_uid
        rule["annotations"] = ann

        # Per-query datasource + PromQL env-label patches
        for q in rule.get("data", []) or []:
            if not isinstance(q, dict):
                continue

            # Postgres datasource UID (Prometheus UID is shared -> left as-is).
            # Match either environment's Postgres UID so the transform is
            # bijective and self-healing regardless of the input's current env.
            if q.get("datasourceUid") in _PG_DS_UIDS:
                q["datasourceUid"] = target_ds

            model = q.get("model") or {}
            if isinstance(model, dict):
                ds = model.get("datasource")
                if isinstance(ds, dict) and ds.get("uid") in _PG_DS_UIDS:
                    ds["uid"] = target_ds

                # PromQL: pin submitter_environment to the target env
                expr = model.get("expr")
                if isinstance(expr, str) and "submitter_environment" in expr:
                    model["expr"] = _ENV_LABEL_RE.sub(
                        lambda m: m.group(1) + env_label + m.group(3), expr
                    )

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
