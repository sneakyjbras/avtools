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


def guardrail_labels(
    *,
    shard_index: int = 0,
    shard_total: int = 1,
    priority: str = m.PRIORITY_ALL,
) -> dict[str, str]:
    """Label set shared by every cycle-level (ALWAYS) guardrail metric.

    Two disambiguation labels are attached CONDITIONALLY, each omitted for its
    single-emitter default so the current unsharded/untiered series identity is
    unchanged:

      ``shard`` — when the fleet is split across N shards (``shard_total > 1``),
        every shard emits its own copy; without ``shard`` the N series collide on
        one identity and the SLO sums/counts across shards read garbage.

      ``tier``  — when the collection is split into per-priority CronJobs
        (``priority != "all"``), each of the critical/high/medium/low jobs emits
        the SAME ALWAYS guardrails every cycle. Without ``tier`` those 4 emitters
        collide on one identity exactly like the shard case. The value is the
        ``--priority`` token (e.g. "critical"). DOWNSTREAM: Grafana SLO/watchdog
        alerts MUST ``group by (tier)`` (and, when sharded, also ``shard``) so
        each tier's freshness/coverage is evaluated independently. The ``tier``
        label is deliberately NOT added to device metrics — device series stay
        tiered purely by *which* CronJob emits them, so cardinality does not
        multiply.

    Args:
        shard_index: This pod's shard index.
        shard_total: Total number of shards (1 = unsharded).
        priority:    The ``--priority`` token in force for this cycle.

    Returns:
        Label dict to attach to each guardrail sample.
    """
    labels: dict[str, str] = {}
    if shard_total > 1:
        labels["shard"] = str(shard_index)
    if priority != m.PRIORITY_ALL:
        labels["tier"] = priority
    return labels


def cycle_guardrail_samples(
    *,
    targeted: int,
    polled: int,
    shard_index: int = 0,
    shard_total: int = 1,
    priority: str = m.PRIORITY_ALL,
) -> list[MetricSample]:
    """Build the per-cycle coverage guardrail samples (targeted/polled/ratio).

    Single source of truth for the guardrail metric set, their values and their
    labels, so that a cycle which collects nothing can publish the very same
    series with zeros instead of dropping them. A guardrail series that VANISHES
    is worse than one that reads zero: Grafana's SLO alerts treat absence as
    health, so the fleet going to zero looked exactly like the fleet being fine.

    Args:
        targeted:    Devices targeted this cycle (0 on a no-targets cycle).
        polled:      Devices that produced a result this cycle.
        shard_index: This pod's shard index.
        shard_total: Total number of shards (1 = unsharded).
        priority:    The ``--priority`` token in force for this cycle.

    Returns:
        The three ``Priority.ALWAYS`` coverage samples. ``coverage_ratio`` is 0.0
        when nothing was targeted (undefined division, and "no targets" is not
        coverage).
    """
    labels = guardrail_labels(
        shard_index=shard_index,
        shard_total=shard_total,
        priority=priority,
    )
    ratio = round(polled / targeted, 4) if targeted > 0 else 0.0
    return [
        MetricSample(name=m.SNMP_DEVICES_TARGETED, value=targeted, labels=dict(labels)),
        MetricSample(name=m.SNMP_DEVICES_POLLED, value=polled, labels=dict(labels)),
        MetricSample(name=m.SNMP_COVERAGE_RATIO, value=ratio, labels=dict(labels)),
    ]


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
        priority: str = m.PRIORITY_ALL,
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
            priority:      Publish-time tier filter. ``"all"`` (default) publishes
                           every metric unchanged (today's behaviour). A tier
                           value (``"critical"``/``"high"``/``"medium"``/``"low"``)
                           publishes only that tier's device metrics plus the
                           ALWAYS guardrails. Collection is NOT affected — this is
                           a publish filter only.

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
        # These are cycle-level (not per-device) ALWAYS metrics; see
        # `cycle_guardrail_samples` / `guardrail_labels` for the metric set and the
        # conditional `shard` / `tier` labels. A cycle with no targets at all does
        # NOT come through here — the orchestrator publishes the same three series
        # with zeros itself, so they never disappear from MonIT.
        cycle_labels = guardrail_labels(
            shard_index=shard_index,
            shard_total=shard_total,
            priority=priority,
        )
        polled = len(ping_l)
        if targeted > 0:
            samples = list(samples) + cycle_guardrail_samples(
                targeted=targeted,
                polled=polled,
                shard_index=shard_index,
                shard_total=shard_total,
                priority=priority,
            )
        if cycle_duration_s is not None:
            samples = list(samples) + [
                MetricSample(
                    name=m.SNMP_CYCLE_DURATION_SECONDS,
                    value=round(cycle_duration_s, 3),
                    labels=dict(cycle_labels),
                ),
            ]

        # Publish-time PRIORITY filter (v1 = publish-filter only; collection is
        # unchanged). Under a tiered run we keep only the requested tier's metrics
        # plus the ALWAYS guardrails/heartbeats (which pass by definition); device
        # metrics of other tiers are dropped here. `all` (default) skips filtering
        # entirely so the untiered deployment's emitted set is byte-for-byte
        # unchanged. This is the single publish-side enforcement point of the tier
        # taxonomy declared in avtools.timeseries.metrics.
        if priority != m.PRIORITY_ALL:
            tier = m.Priority(priority)
            samples = [s for s in samples if m.should_publish(s.name, tier)]

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
