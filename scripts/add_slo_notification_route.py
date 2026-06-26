#!/usr/bin/env python3
"""Safely add/update the SLO-family collapse route in the shared Grafana policy tree.

The Grafana provisioning API replaces the ENTIRE org policy tree on PUT, so we never
blind-PUT a hand-written file (that would delete other teams' routes). Instead we:

    1. GET  /api/v1/provisioning/policies        (the current live tree)
    2. upsert the av_alert_family=slo child route under the root .routes
    3. PUT  it back                              (only with --apply)

Idempotent: re-running replaces the existing slo route rather than duplicating it.

Env:
    GRAFANA_URL    e.g. https://monit-grafana.cern.ch
    GRAFANA_TOKEN  service-account token with alerting.provisioning write scope
    X_GRAFANA_ORG  org id (default 12)

Usage:
    python scripts/add_slo_notification_route.py            # dry-run: print merged tree
    python scripts/add_slo_notification_route.py --apply    # PUT the merged tree
    python scripts/add_slo_notification_route.py --env qa --apply   # receiver -> AV Test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

RECEIVER = {"prod": "AV Tools", "qa": "AV Test"}


def slo_route(env: str) -> dict:
    return {
        "receiver": RECEIVER[env],
        "object_matchers": [["av_alert_family", "=", "slo"]],
        "group_by": ["av_alert_family"],
        "group_wait": "30s",
        "group_interval": "5m",
        "repeat_interval": "2h",
        "continue": False,
    }


def _req(method: str, url: str, token: str, org: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Content-Type", "application/json")
    r.add_header("X-Grafana-Org-Id", org)
    with urllib.request.urlopen(r) as resp:  # noqa: S310 (internal CERN endpoint)
        return json.loads(resp.read() or "{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=("prod", "qa"), default="prod")
    ap.add_argument("--apply", action="store_true", help="PUT the merged tree (default: dry-run)")
    args = ap.parse_args()

    base = os.environ.get("GRAFANA_URL", "").rstrip("/")
    token = os.environ.get("GRAFANA_TOKEN", "")
    org = os.environ.get("X_GRAFANA_ORG", "12")
    if not base or not token:
        print("ERROR: set GRAFANA_URL and GRAFANA_TOKEN", file=sys.stderr)
        return 2

    url = f"{base}/api/v1/provisioning/policies"
    tree = _req("GET", url, token, org)

    routes = tree.setdefault("routes", [])
    # drop any existing slo route (idempotent upsert)
    routes[:] = [
        r
        for r in routes
        if not any(
            m[:2] == ["av_alert_family", "="] and (len(m) < 3 or m[2] == "slo")
            for m in r.get("object_matchers", [])
        )
    ]
    routes.insert(0, slo_route(args.env))

    print(json.dumps(tree, indent=2))
    if not args.apply:
        print("\n[dry-run] re-run with --apply to PUT the tree above.", file=sys.stderr)
        return 0

    _req("PUT", url, token, org, tree)
    print(f"\n[applied] SLO route upserted into org {org} ({args.env}).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
