from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, asdict

import structlog

from avtools.snmp.client import PingResult, ProbeResult, QueryResult
from avtools.timeseries.encoder import encode_all
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
    ping: int
    probe: int
    queries: int
    ts_samples: int
    projector_text_rows: int
    device_sysdescr_rows: int


class SNMPObserverRouter:
    """Observe SNMP collector outputs and route to sinks.

    Policy:
      - Numeric metrics (ping/probe/query numeric fields) -> Prometheus via OTLP
      - Text metrics (identity-like fields, e.g. firmware) -> Postgres monitoring tables
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
    ) -> SNMPRoutingStats:
        """Encode + publish a full SNMP pipeline batch.

        Args:
            ping: Ping stage results.
            probe: SNMP probe stage results.
            queries: Routed SNMP query stage results.

        Returns:
            SNMPRoutingStats with per-sink counters.

        Raises:
            SNMPObserverRouterError: On unexpected failures inside the router.

        Notes:
            This method does not swallow sink exceptions: failures from the
            OTLP publisher or Postgres monitoring client are propagated so the
            top-layer orchestrator can decide how to classify the run.
        """
        ping_l = list(ping)
        probe_l = list(probe)
        query_l = list(queries)

        # 1) Time-series (numeric) -> OTLP
        try:
            samples = encode_all(ping=ping_l, probe=probe_l, queries=query_l)
        except Exception as exc:
            raise SNMPObserverRouterError("Failed to encode SNMP samples") from exc
        if samples:
            self._ts.publish(samples)

        # 2) Text monitoring -> Postgres monitoring
        try:
            projector_records = projector_records_from_query_results(query_l)
        except Exception as exc:
            raise SNMPObserverRouterError(
                "Failed to build projector monitoring records"
            ) from exc
        if projector_records:
            self._pg.upsert_projector_monitoring(projector_records)

        try:
            device_sysdescr_records = device_sysdescr_records_from_probe_results(
                probe_l
            )
        except Exception as exc:
            raise SNMPObserverRouterError(
                "Failed to build sysdescr monitoring records"
            ) from exc
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
