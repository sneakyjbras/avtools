"""Sentry error tracking for AV Tools (CERN Sentry SaaS, https://cern.sentry.io).

Design goals:

* **Safe to ship disabled.** ``init_sentry()`` is a no-op unless ``SENTRY_DSN`` is
  set, and degrades gracefully if ``sentry-sdk`` is not installed (it is an
  optional ``[sentry]`` extra). Nothing changes for the monolith until a DSN is
  provided.
* **Batch-appropriate.** AV Tools runs as short-lived CronJob/systemd processes,
  so we capture unhandled exceptions and tag each run with its environment,
  release, hostgroup and shard index. Tracing defaults to off.
* **Data scrubbing (CERN enrolment requirement).** Events carry device IPs and
  equipment numbers, which are access-controlled. ``_before_send`` masks those
  fields in tags/extra/contexts before anything leaves the process.
"""

from __future__ import annotations

import os
from typing import Any

# Field names that must never leave the process in the clear.
_SENSITIVE_KEYS = {
    "ip",
    "device_ip",
    "target_ip",
    "equipmentno",
    "equipment_no",
    "equipment_number",
    "community",
    "snmp_community",
}
_MASK = "[scrubbed]"


def _scrub_mapping(obj: Any) -> Any:
    """Recursively mask sensitive keys in dict/list structures."""
    if isinstance(obj, dict):
        return {
            k: (_MASK if k.lower() in _SENSITIVE_KEYS else _scrub_mapping(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub_mapping(v) for v in obj]
    return obj


def _before_send(event: dict, hint: dict) -> dict:  # noqa: ARG001 (hint kept for API)
    for section in ("tags", "extra", "contexts", "request"):
        if section in event and event[section] is not None:
            event[section] = _scrub_mapping(event[section])
    return event


def init_sentry() -> bool:
    """Initialise Sentry if configured. Returns True when actually enabled.

    Reads configuration from the environment (all optional):

    * ``SENTRY_DSN`` — enables Sentry when present (from the K8s Secret / IT-PW).
    * ``SENTRY_ENVIRONMENT`` / ``AVTOOLS_ENVIRONMENT`` — ``qa`` | ``prod``.
    * ``SENTRY_RELEASE`` — e.g. ``avtools@<image-tag>`` or the git SHA.
    * ``SENTRY_TRACES_SAMPLE_RATE`` — default ``0.0`` (errors only).
    * ``AVTOOLS_HOSTGROUP`` / ``JOB_COMPLETION_INDEX`` — attached as tags.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:  # optional extra not installed
        return False

    try:
        rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0"))
    except ValueError:
        rate = 0.0

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT") or os.getenv("AVTOOLS_ENVIRONMENT", "prod"),
        release=os.getenv("SENTRY_RELEASE"),
        traces_sample_rate=rate,
        send_default_pii=False,
        before_send=_before_send,
    )

    tags = {
        "hostgroup": os.getenv("AVTOOLS_HOSTGROUP", ""),
        "shard_index": os.getenv("JOB_COMPLETION_INDEX", ""),
        "shard_total": os.getenv("SHARD_TOTAL", ""),
    }
    for key, value in tags.items():
        if value:
            sentry_sdk.set_tag(key, value)

    return True
