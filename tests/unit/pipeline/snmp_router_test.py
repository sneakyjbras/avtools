from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from avtools.pipeline.snmp_router import SNMPObserverRouter
from avtools.snmp.client import PingResult, ProbeResult, QueryResult
from avtools.timeseries.metrics import PROJECTOR_QUERY_POWER_STATUS


@dataclass
class DummyDevice:
    ip: str
    equipment_no: str


class DummyTimeseriesPublisher:
    def __init__(self) -> None:
        self.published: list[list[Any]] = []

    def publish(self, samples: list[Any]) -> None:
        self.published.append(list(samples))


class DummyPostgresMonitoring:
    def __init__(self) -> None:
        self.projector_rows: list[Any] = []
        self.sysdescr_rows: list[Any] = []

    def upsert_projector_monitoring(self, rows: list[Any]) -> None:
        self.projector_rows.extend(rows)

    def upsert_device_sysdescr_monitoring(self, rows: list[Any]) -> None:
        self.sysdescr_rows.extend(rows)


def test_snmp_router_routes_numeric_to_otlp_and_text_to_postgres():
    dev = DummyDevice(ip="10.0.0.1", equipment_no="EQ1")

    ping = [
        PingResult(
            device=dev,  # type: ignore[arg-type]
            ip=dev.ip,
            equipmentno=dev.equipment_no,
            up=1,
            rtt_ms=1.23,
            reason=None,
            attempts=1,
        )
    ]
    probe = [
        ProbeResult(
            device=dev,  # type: ignore[arg-type]
            ip=dev.ip,
            equipmentno=dev.equipment_no,
            up=1,
            eqclass="AVD",
            category="AV-PRO",
            sysdescr="Linux 6.1",
        )
    ]
    queries = [
        QueryResult(
            device=dev,  # type: ignore[arg-type]
            ip=dev.ip,
            equipmentno=dev.equipment_no,
            query="projector",
            eqclass="AVD",
            category="AV-PRO",
            # power_status intentionally numeric-like: if encoder mistakenly exports it,
            # we catch it by asserting it does NOT appear in samples.
            stats={"firmware": "FW1", "power_status": "1", "uptime_seconds": 100},
        )
    ]

    ts = DummyTimeseriesPublisher()
    pg = DummyPostgresMonitoring()

    router = SNMPObserverRouter(timeseries_publisher=ts, postgres_monitoring=pg)
    stats = router.process(ping=ping, probe=probe, queries=queries)

    # Timeseries samples were published.
    assert len(ts.published) == 1
    samples = ts.published[0]

    # Ensure power_status is NOT exported to Prometheus timeseries.
    assert all(
        getattr(s, "name", None) != PROJECTOR_QUERY_POWER_STATUS for s in samples
    )

    # Text monitoring in Postgres.
    assert len(pg.projector_rows) == 1
    assert pg.projector_rows[0].equipment_no == "EQ1"
    assert pg.projector_rows[0].firmware == "FW1"
    assert pg.projector_rows[0].power_status == "1"

    assert len(pg.sysdescr_rows) == 1
    assert pg.sysdescr_rows[0].equipment_no == "EQ1"
    assert "Linux" in pg.sysdescr_rows[0].sysdescr

    # Stats counts.
    assert stats.ping == 1
    assert stats.probe == 1
    assert stats.queries == 1
    assert stats.projector_text_rows == 1
    assert stats.device_sysdescr_rows == 1
