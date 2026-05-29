"""Tests for AVTools.run_snmp_timeseries — happy paths.

Verifies:
- Skips when no LanDB targets are found.
- Routes results through the router on success.
- Passes metric_labels (Layer 2) to OTLPMetricsPublisher.
- Passes device_lookup (per-device labels) to router.process.
- Accepts the three new CLI-level params: submitter_environment, submitter_hostgroup,
  availability_zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import avtools.core.av_tools as av_mod
from avtools.core.av_tools import AVTools
from avtools.snmp.client import PingResult, ProbeResult, QueryResult


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class DummyDevice:
    ip: str
    equipment_no: str
    building: str | None = "BUILDING-A"
    room: str | None = "ROOM-1"
    eq_class: str | None = "PROJ"
    model: str | None = "Epson EB-L"
    category: str | None = "AV-PROJ"
    hostname: str | None = "proj.cern.ch"


class DummyLogger:
    def __init__(self) -> None:
        self.info_events: list[tuple[str, dict[str, Any]]] = []
        self.exception_events: list[tuple[str, dict[str, Any]]] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.info_events.append((str(msg), dict(kwargs)))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exception_events.append((str(msg), dict(kwargs)))


class DummyPublisher:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)
        self.published: list[Any] = []

    def publish(self, samples: list[Any]) -> None:
        self.published.append(list(samples))


class DummyPostgresMonitoring:
    def __init__(self, dbod_url: str) -> None:
        self.dbod_url = dbod_url


class DummyRouter:
    def __init__(self, *, timeseries_publisher: Any, postgres_monitoring: Any) -> None:
        self.ts = timeseries_publisher
        self.pg = postgres_monitoring
        self.calls: list[dict[str, Any]] = []

    def process(self, *, ping, probe, queries, device_lookup=None):
        self.calls.append(
            {
                "ping": list(ping),
                "probe": list(probe),
                "queries": list(queries),
                "device_lookup": device_lookup,
            }
        )

        class _Stats:
            ts_samples = 123

        return _Stats()


captured_router: DummyRouter | None = None


def make_avtools() -> AVTools:
    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av.dbod_url = "postgres://dummy"
    return av


# ---------------------------------------------------------------------------
# Helpers for patching _get_snmp_raw
# ---------------------------------------------------------------------------


def _make_snmp_results(devices):
    dev = devices[0]
    ping = [
        PingResult(
            device=dev,  # type: ignore[arg-type]
            ip=dev.ip,
            equipmentno=dev.equipment_no,
            up=1,
            rtt_ms=1.0,
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
            sysdescr="sysDescr",
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
            stats={"firmware": "1.2.3"},
        )
    ]
    return ping, probe, queries


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_skips_when_no_targets(monkeypatch):
    av = make_avtools()
    monkeypatch.setattr(AVTools, "_load_timeseries_targets_from_landb_ipaddresses", lambda self: [])

    called_get = False

    async def fake_get_raw(self, devices, max_workers):
        nonlocal called_get
        called_get = True
        return ([], [], [])

    monkeypatch.setattr(AVTools, "_get_snmp_raw", fake_get_raw)

    av.run_snmp_timeseries(
        otlp_endpoint="monit-otlp.cern.ch:4316",
        monit_tenant="t",
        monit_password="p",
        max_workers=2,
    )

    assert called_get is False
    assert any(
        ev == "avtools_run_snmp_timeseries_end" and kw.get("status") == "skipped_no_targets"
        for ev, kw in av.logger.info_events
    )


def test_routes_results_and_ends_ok(monkeypatch):
    av = make_avtools()
    devices = [DummyDevice(ip="10.0.0.1", equipment_no="EQ1")]

    monkeypatch.setattr(
        AVTools, "_load_timeseries_targets_from_landb_ipaddresses", lambda self: devices
    )

    async def fake_get_raw(self, devices_arg, max_workers):
        return _make_snmp_results(devices_arg)

    monkeypatch.setattr(AVTools, "_get_snmp_raw", fake_get_raw)

    routers_created: list[DummyRouter] = []

    def make_router(**kw):
        r = DummyRouter(**kw)
        routers_created.append(r)
        return r

    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", lambda **kw: DummyPublisher(**kw))
    monkeypatch.setattr(av_mod, "PostgresMonitoringClient", DummyPostgresMonitoring)
    monkeypatch.setattr(av_mod, "SNMPObserverRouter", make_router)

    av.run_snmp_timeseries(
        otlp_endpoint="monit-otlp.cern.ch:4316",
        monit_tenant="tenant",
        monit_password="pass",
        max_workers=1,
    )

    assert any(
        ev == "avtools_run_snmp_timeseries_end" and kw.get("status") == "ok"
        for ev, kw in av.logger.info_events
    )
    assert len(routers_created) == 1
    assert routers_created[0].calls[0]["device_lookup"] is not None


def test_metric_labels_passed_to_publisher(monkeypatch):
    """Layer 2: OTLPMetricsPublisher must receive the correct metric_labels dict."""
    av = make_avtools()
    devices = [DummyDevice(ip="10.0.0.1", equipment_no="EQ1")]

    monkeypatch.setattr(
        AVTools, "_load_timeseries_targets_from_landb_ipaddresses", lambda self: devices
    )

    async def fake_get_raw(self, devices_arg, max_workers):
        return ([], [], [])

    monkeypatch.setattr(AVTools, "_get_snmp_raw", fake_get_raw)

    publishers_created: list[DummyPublisher] = []

    def make_publisher(**kw):
        p = DummyPublisher(**kw)
        publishers_created.append(p)
        return p

    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", make_publisher)
    monkeypatch.setattr(av_mod, "PostgresMonitoringClient", DummyPostgresMonitoring)
    monkeypatch.setattr(av_mod, "SNMPObserverRouter", DummyRouter)

    av.run_snmp_timeseries(
        otlp_endpoint="x:1",
        monit_tenant="t",
        monit_password="p",
        service_name="avtools",
        submitter_environment="qa",
        submitter_hostgroup="itdcim/av-qa",
        availability_zone="cern-geneva-a",
    )

    assert len(publishers_created) == 1
    labels = publishers_created[0].kwargs.get("metric_labels", {})
    assert labels["job"] == "avtools"
    assert labels["submitter_environment"] == "qa"
    assert labels["toplevel_hostgroup"] == "itdcim"
    assert labels["submitter_hostgroup"] == "itdcim/av-qa"
    assert labels["region"] == "cern"
    assert labels["availability_zone"] == "cern-geneva-a"


def test_device_lookup_built_from_cached_ip_addresses(monkeypatch):
    """Per-device label dict must be built from CachedIPAddress fields."""
    av = make_avtools()
    devices = [
        DummyDevice(
            ip="10.0.0.1",
            equipment_no="EQ1",
            building="BUILDING-X",
            room="ROOM-99",
            eq_class="PROJ",
            model="BenQ LX980",
            category="AV-PROJ",
            hostname="benq-99.cern.ch",
        )
    ]

    monkeypatch.setattr(
        AVTools, "_load_timeseries_targets_from_landb_ipaddresses", lambda self: devices
    )

    async def fake_get_raw(self, devices_arg, max_workers):
        return ([], [], [])

    monkeypatch.setattr(AVTools, "_get_snmp_raw", fake_get_raw)

    routers_created: list[DummyRouter] = []

    def make_router(**kw):
        r = DummyRouter(**kw)
        routers_created.append(r)
        return r

    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", lambda **kw: DummyPublisher(**kw))
    monkeypatch.setattr(av_mod, "PostgresMonitoringClient", DummyPostgresMonitoring)
    monkeypatch.setattr(av_mod, "SNMPObserverRouter", make_router)

    av.run_snmp_timeseries(otlp_endpoint="x:1", monit_tenant="t", monit_password="p")

    lookup = routers_created[0].calls[0]["device_lookup"]
    assert "EQ1" in lookup
    eq1 = lookup["EQ1"]
    assert eq1["building"] == "BUILDING-X"
    assert eq1["room"] == "ROOM-99"
    assert eq1["eq_class"] == "PROJ"
    assert eq1["model"] == "BenQ LX980"
    assert eq1["category"] == "AV-PROJ"
    assert eq1["hostname"] == "benq-99.cern.ch"


def test_device_with_no_equipment_no_excluded_from_lookup(monkeypatch):
    """Devices without an equipment_no must not appear in the lookup."""
    av = make_avtools()

    @dataclass
    class DevNoEq:
        ip: str
        equipment_no: str | None = None
        building: str | None = "B"
        room: str | None = None
        eq_class: str | None = None
        model: str | None = None
        category: str | None = None
        hostname: str | None = None

    devices = [DevNoEq(ip="10.0.0.1")]

    monkeypatch.setattr(
        AVTools, "_load_timeseries_targets_from_landb_ipaddresses", lambda self: devices
    )

    async def fake_get_raw(self, devices_arg, max_workers):
        return ([], [], [])

    monkeypatch.setattr(AVTools, "_get_snmp_raw", fake_get_raw)

    routers_created: list[DummyRouter] = []

    def make_router(**kw):
        r = DummyRouter(**kw)
        routers_created.append(r)
        return r

    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", lambda **kw: DummyPublisher(**kw))
    monkeypatch.setattr(av_mod, "PostgresMonitoringClient", DummyPostgresMonitoring)
    monkeypatch.setattr(av_mod, "SNMPObserverRouter", make_router)

    av.run_snmp_timeseries(
        otlp_endpoint="x:1",
        monit_tenant="t",
        monit_password="p",
    )

    # The device had no equipment_no so the lookup is empty, but the run
    # still completes — devices were loaded (len=1) so we don't skip.
    assert any(
        ev == "avtools_run_snmp_timeseries_end" and kw.get("status") == "ok"
        for ev, kw in av.logger.info_events
    )
    assert len(routers_created) == 1
    assert routers_created[0].calls[0]["device_lookup"] == {}
