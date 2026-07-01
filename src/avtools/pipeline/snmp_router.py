"""Route SNMP collector outputs to their respective sinks.

Routing policy
--------------
Numeric metrics (ping status, RTT, SNMP probe status, projector lamp hours and
uptime) → Prometheus via OTLP/gRPC (``OTLPMetricsPublisher``).

Text / identity-like fields (firmware version, power status string, sysDescr)
→ Postgres monitoring tables (``PostgresMonitoringClient``).

Device-level label enrichment
------------------------------
Pass a ``device_lookup`` dict (``equipment_no`` → ``dict[str, str]``) built
from ``CachedIPAddress`` objects.  The lookup is forwarded to
``encode_all`` so that every MetricSample receives EAM/LanDB metadata labels
(building, room, eq_class, model, category, hostname) in addition to the
mandatory ``equipmentno`` label.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, asdict

import structlog

from avtools.snmp.client import InterfaceResult, PingResult, ProbeResult, QueryResult
from avtools.timeseries.encoder import encode_all, DeviceLookup
from avtools.timeseries.models import MetricSample
from avtools.timeseries import metrics as m
from avtools.timeseries.otlp_publisher import OTLPMetricsPublisher
from avtools.postgres.monitoring.client import PostgresMonitoringClient
from avtools.postgres.monitoring.codec import (
    projector_records_from_query_results,
    device_sysdescr_records_from_probe_results,
)
from avtools.exception.errors import SNMPObserverRouterError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SNMPRoutingStats:
    """Per-sink counters returned by :meth:`SNMPObserverRouter.process`."""

    ping: int
    probe: int
    queries: int
    ts_samples: int
    projector_text_rows: int
    device_sysdescr_rows: int


class SNMPObserverRouter:
    """Observe SNMP collector outputs and route to sinks.

    Args:
        timeseries_publisher: OTLP publisher for numeric gauge metrics.
        postgres_monitoring:  Postgres client for text / identity fields.
    """

    def __init__(
        self,
        *,
        timeseries_publisher: OTLPMetricsPublisher,
        postgres_monitoring: PostgresMonitoringClient,
    ) -> None:
        self._ts = timeseries_publisher
        self._pg = postgres_monitoring
        self.log = logger.bind(component="SNMPObserverRouter")

    def process(
        self,
        *,
        ping: Iterable[PingResult],
        probe: Iterable[ProbeResult],
        queries: Iterable[QueryResult],
        interfaces: Iterable[InterfaceResult] = (),
        device_lookup: DeviceLookup | None = None,
        targeted: int = 0,
        shard_index: int = 0,
        shard_total: int = 1,
        cycle_duration_s: float | None = None,
    ) -> SNMPRoutingStats:
        """Encode and publish one full SNMP pipeline batch.

        Args:
            ping:          Ping stage results.
            probe:         SNMP probe stage results.
            queries:       Routed SNMP query stage results.
            device_lookup: Optional mapping from ``equipment_no`` to a
                           pre-computed label dict (building, room, eq_class,
                           model, category, hostname).  Built from
                           ``CachedIPAddress`` objects by the orchestrator.

        Returns:
            :class:`SNMPRoutingStats` with per-sink counters.

        Raises:
            SNMPObserverRouterError: On unexpected failures inside the router.

        Notes:
            Sink exceptions from the OTLP publisher or Postgres monitoring
            client are propagated so the top-layer orchestrator can classify
            the run correctly.
        """
        ping_l = list(ping)
        probe_l = list(probe)
        query_l = list(queries)
        interface_l = list(interfaces)

        # 1) Timeseries (numeric) → OTLP, with device-level label enrichment.
        try:
            samples = encode_all(
                ping=ping_l,
                probe=probe_l,
                queries=query_l,
                interfaces=interface_l,
                device_lookup=device_lookup,
            )
        except Exception as exc:
            raise SNMPObserverRouterError("Failed to encode SNMP samples") from exc

        # Coverage guardrail: every targeted device should produce a ping result.
        # polled/targeted < 1.0 signals silent loss (e.g. over-concurrency / UDP drops).
        #
        # These are cycle-level (not per-device) metrics. When the fleet is split
        # across N shards, every shard emits its own copy, so they MUST carry a
        # `shard` label — otherwise the N series collide on one identity and the
        # SLO expressions (which sum/count across shards) read garbage. The label
        # is omitted for the single-emitter case (shard_total <= 1) so the current
        # unsharded deployment keeps its existing series identity unchanged.
        shard_labels: dict[str, str] = (
            {"shard": str(shard_index)} if shard_total > 1 else {}
        )
        polled = len(ping_l)
        if targeted > 0:
            ratio = polled / targeted
            samples = list(samples) + [
                MetricSample(name=m.SNMP_DEVICES_TARGETED, value=targeted, labels=dict(shard_labels)),
                MetricSample(name=m.SNMP_DEVICES_POLLED, value=polled, labels=dict(shard_labels)),
                MetricSample(name=m.SNMP_COVERAGE_RATIO, value=round(ratio, 4), labels=dict(shard_labels)),
            ]
        if cycle_duration_s is not None:
            samples = list(samples) + [
                MetricSample(
                    name=m.SNMP_CYCLE_DURATION_SECONDS,
                    value=round(cycle_duration_s, 3),
                    labels=dict(shard_labels),
                ),
            ]

        if samples:
            self._ts.publish(samples)

        # 2) Text monitoring → Postgres monitoring tables.
        try:
            projector_records = projector_records_from_query_results(query_l)
        except Exception as exc:
            raise SNMPObserverRouterError("Failed to build projector monitoring records") from exc
        if projector_records:
            self._pg.upsert_projector_monitoring(projector_records)

        try:
            device_sysdescr_records = device_sysdescr_records_from_probe_results(probe_l)
        except Exception as exc:
            raise SNMPObserverRouterError("Failed to build sysdescr monitoring records") from exc
        if device_sysdescr_records:
            self._pg.upsert_device_sysdescr_monitoring(device_sysdescr_records)

        stats = SNMPRoutingStats(
            ping=len(ping_l),
            probe=len(probe_l),
            queries=len(query_l),
            ts_samples=len(samples),
            projector_text_rows=len(projector_records),
            device_sysdescr_rows=len(device_sysdescr_records),
        )
        self.log.info("snmp_observer_router_done", **asdict(stats))
        return stats
