"""Tests for SNMPObserverRouter.

Verifies:
- Numeric results → OTLP timeseries publisher
- Text results (firmware, power_status, sysDescr) → Postgres monitoring
- device_lookup is forwarded to encode_all, enriching per-device labels
- Missing device_lookup falls back gracefully (equipmentno-only labels)
- power_status is never exported to Prometheus
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from avtools.pipeline.snmp_router import SNMPObserverRouter
from avtools.snmp.client import PingResult, ProbeResult, QueryResult
from avtools.timeseries.metrics import PROJECTOR_QUERY_POWER_STATUS


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


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


def _make_router() -> tuple[SNMPObserverRouter, DummyTimeseriesPublisher, DummyPostgresMonitoring]:
    ts = DummyTimeseriesPublisher()
    pg = DummyPostgresMonitoring()
    router = SNMPObserverRouter(timeseries_publisher=ts, postgres_monitoring=pg)
    return router, ts, pg


def _dev(eq: str = "EQ1", ip: str = "10.0.0.1") -> DummyDevice:
    return DummyDevice(ip=ip, equipment_no=eq)


def _ping(dev: DummyDevice, *, up: int = 1, rtt_ms: float = 1.23) -> PingResult:
    return PingResult(
        device=dev,  # type: ignore[arg-type]
        ip=dev.ip,
        equipmentno=dev.equipment_no,
        up=up,
        rtt_ms=rtt_ms,
        reason=None,
        attempts=1,
    )


def _probe(dev: DummyDevice, *, sysdescr: str = "Linux 6.1") -> ProbeResult:
    return ProbeResult(
        device=dev,  # type: ignore[arg-type]
        ip=dev.ip,
        equipmentno=dev.equipment_no,
        up=1,
        eqclass="AVD",
        category="AV-PRO",
        sysdescr=sysdescr,
    )


def _query(dev: DummyDevice, stats: dict) -> QueryResult:
    return QueryResult(
        device=dev,  # type: ignore[arg-type]
        ip=dev.ip,
        equipmentno=dev.equipment_no,
        query="projector",
        eqclass="AVD",
        category="AV-PRO",
        stats=stats,
    )


# ---------------------------------------------------------------------------
# Routing policy
# ---------------------------------------------------------------------------


def test_router_routes_numeric_to_otlp_and_text_to_postgres():
    dev = _dev()
    router, ts, pg = _make_router()

    stats = router.process(
        ping=[_ping(dev)],
        probe=[_probe(dev)],
        queries=[_query(dev, {"firmware": "FW1", "power_status": "1", "uptime_seconds": 100})],
    )

    assert len(ts.published) == 1
    samples = ts.published[0]
    # Power status is now published numerically to OTLP (in addition to the
    # text snapshot persisted to Postgres below).
    assert any(getattr(s, "name", None) == PROJECTOR_QUERY_POWER_STATUS for s in samples)

    assert len(pg.projector_rows) == 1
    assert pg.projector_rows[0].equipment_no == "EQ1"
    assert pg.projector_rows[0].firmware == "FW1"
    assert pg.projector_rows[0].power_status == "1"

    assert len(pg.sysdescr_rows) == 1
    assert pg.sysdescr_rows[0].equipment_no == "EQ1"
    assert "Linux" in pg.sysdescr_rows[0].sysdescr

    assert stats.ping == 1
    assert stats.probe == 1
    assert stats.queries == 1
    assert stats.projector_text_rows == 1
    assert stats.device_sysdescr_rows == 1


# ---------------------------------------------------------------------------
# device_lookup forwarding
# ---------------------------------------------------------------------------


def test_router_forwards_device_lookup_to_encoder():
    """Labels from the lookup must appear on every published sample."""
    dev = _dev("EQ2", ip="10.0.0.2")
    lookup = {
        "EQ2": {
            "building": "BUILDING-A",
            "room": "ROOM-42",
            "eq_class": "PROJ",
            "model": "Epson EB-X",
            "category": "AV-PROJ",
            "hostname": "proj-42.cern.ch",
        }
    }
    router, ts, pg = _make_router()

    router.process(
        ping=[_ping(dev)],
        probe=[],
        queries=[],
        device_lookup=lookup,
    )

    assert len(ts.published) == 1
    for sample in ts.published[0]:
        lbl = dict(sample.labels)
        assert lbl["equipmentno"] == "EQ2"
        assert lbl["building"] == "BUILDING-A"
        assert lbl["room"] == "ROOM-42"
        assert lbl["eq_class"] == "PROJ"
        assert lbl["model"] == "Epson EB-X"
        assert lbl["category"] == "AV-PROJ"
        assert lbl["hostname"] == "proj-42.cern.ch"


def test_router_without_lookup_falls_back_to_equipmentno_only():
    dev = _dev("EQ3")
    router, ts, pg = _make_router()

    router.process(ping=[_ping(dev)], probe=[], queries=[])

    for sample in ts.published[0]:
        lbl = dict(sample.labels)
        assert set(lbl.keys()) == {"equipmentno"}


def test_router_partial_lookup_missing_device_falls_back():
    dev = _dev("EQ-MISSING")
    lookup = {"EQ-OTHER": {"building": "X"}}
    router, ts, pg = _make_router()

    router.process(ping=[_ping(dev)], probe=[], queries=[], device_lookup=lookup)

    for sample in ts.published[0]:
        assert "building" not in sample.labels


# ---------------------------------------------------------------------------
# Stat counters
# ---------------------------------------------------------------------------


def test_router_stat_counts_match_input_sizes():
    dev = _dev()
    router, ts, pg = _make_router()

    stats = router.process(
        ping=[_ping(dev), _ping(_dev("EQ2", "10.0.0.2"))],
        probe=[_probe(dev)],
        queries=[_query(dev, {"uptime_seconds": 60})],
    )

    assert stats.ping == 2
    assert stats.probe == 1
    assert stats.queries == 1
