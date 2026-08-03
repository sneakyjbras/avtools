"""Heartbeat publishing for the single-shot inventory sync jobs.

run-eam and run-landb are Postgres-only inventory reconcilers — they do not run the
SNMP timeseries pipeline and otherwise publish nothing to Prometheus. To make their
liveness observable (and alertable on the same Mimir/Grafana path as everything else),
each emits a single "last successful run" timestamp gauge at the end of a successful
sync. The freshness alarm is then age-based: ``time() - <metric> > threshold``.

This is deliberately minimal: one publisher, one sample, best-effort. A heartbeat
failure must never fail the sync itself.
"""

from __future__ import annotations

import time

import structlog

from avtools.timeseries.models import MetricSample
from avtools.timeseries.otlp_publisher import (
    DEFAULT_ENCODING,
    DEFAULT_PROTOCOL,
    OTLPMetricsPublisher,
)

_log = structlog.get_logger(__name__)


def publish_heartbeat(
    metric_name: str,
    *,
    otlp_endpoint: str,
    tenant: str,
    password: str,
    service_name: str,
    otlp_ca_file: str | None,
    otlp_insecure: bool,
    environment: str,
    hostgroup: str,
    availability_zone: str,
    otlp_protocol: str = DEFAULT_PROTOCOL,
    otlp_encoding: str = DEFAULT_ENCODING,
) -> None:
    """Publish a single ``metric_name = now`` gauge via OTLP (best-effort).

    Carries the same global deployment labels as the timeseries pipeline (so
    ``submitter_environment`` is present and the prod→qa filter / patcher work).
    Any failure is logged and swallowed — the heartbeat must not break the sync.
    """
    # Same global label set as the SNMP timeseries pipeline (see av_tools.py).
    metric_labels: dict[str, str] = {
        "job": service_name,
        "submitter_environment": environment,
        "toplevel_hostgroup": "itdcim",
        "submitter_hostgroup": hostgroup,
        "region": "cern",
        "availability_zone": availability_zone,
    }
    try:
        publisher = OTLPMetricsPublisher(
            endpoint=otlp_endpoint,
            tenant=tenant,
            password=password,
            service_name=service_name,
            ca_file=otlp_ca_file,
            insecure=otlp_insecure,
            protocol=otlp_protocol,
            encoding=otlp_encoding,
            metric_labels=metric_labels,
        )
        now = time.time()
        publisher.publish([MetricSample(name=metric_name, value=now, labels={})])
        _log.info("heartbeat_published", metric=metric_name, timestamp=now)
    except Exception as exc:  # never fail the sync on a heartbeat error
        _log.warning("heartbeat_publish_failed", metric=metric_name, error=str(exc))
