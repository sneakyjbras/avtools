from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import avtools.core.av_tools as av_mod
from avtools.core.av_tools import AVTools
from avtools.snmp.client import PingResult, ProbeResult, QueryResult


@dataclass
class DummyDevice:
    ip: str
    equipment_no: str


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

    def process(self, *, ping, probe, queries):
        self.calls.append(
            {"ping": list(ping), "probe": list(probe), "queries": list(queries)}
        )

        # Return something matching SNMPRoutingStats interface.
        class _Stats:
            ts_samples = 123

        return _Stats()


def make_avtools_for_tests() -> AVTools:
    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av.dbod_url = "postgres://dummy"
    return av


def test_run_snmp_timeseries_skips_when_no_targets(monkeypatch):
    av = make_avtools_for_tests()

    def fake_load(self):
        return []

    called_get = False

    async def fake_get_raw(self, devices, max_workers):
        nonlocal called_get
        called_get = True
        return ([], [], [])

    monkeypatch.setattr(
        AVTools, "_load_timeseries_targets_from_landb_ipaddresses", fake_load
    )
    monkeypatch.setattr(AVTools, "_get_snmp_raw", fake_get_raw)

    av.run_snmp_timeseries(
        otlp_endpoint="monit-otlp.cern.ch:4316",
        monit_tenant="t",
        monit_password="p",
        max_workers=2,
    )

    assert called_get is False
    assert any(
        "avtools_run_snmp_timeseries_end" == e[0]
        and e[1].get("status") == "skipped_no_targets"
        for e in av.logger.info_events
    )


def test_run_snmp_timeseries_routes_results(monkeypatch):
    av = make_avtools_for_tests()

    devices = [DummyDevice(ip="10.0.0.1", equipment_no="EQ1")]

    def fake_load(self):
        return devices

    async def fake_get_raw(self, devices_arg, max_workers):
        assert devices_arg == devices
        assert max_workers == 1
        ping = [
            PingResult(
                device=devices_arg[0],  # type: ignore[arg-type]
                ip=devices_arg[0].ip,
                equipmentno=devices_arg[0].equipment_no,
                up=1,
                rtt_ms=1.0,
                reason=None,
                attempts=1,
            )
        ]
        probe = [
            ProbeResult(
                device=devices_arg[0],  # type: ignore[arg-type]
                ip=devices_arg[0].ip,
                equipmentno=devices_arg[0].equipment_no,
                up=1,
                eqclass="AVD",
                category="AV-PRO",
                sysdescr="sysDescr",
            )
        ]
        queries = [
            QueryResult(
                device=devices_arg[0],  # type: ignore[arg-type]
                ip=devices_arg[0].ip,
                equipmentno=devices_arg[0].equipment_no,
                query="projector",
                eqclass="AVD",
                category="AV-PRO",
                stats={"firmware": "1.2.3"},
            )
        ]
        return (ping, probe, queries)

    monkeypatch.setattr(
        AVTools, "_load_timeseries_targets_from_landb_ipaddresses", fake_load
    )
    monkeypatch.setattr(AVTools, "_get_snmp_raw", fake_get_raw)

    # Patch constructor symbols used inside run_snmp_timeseries
    monkeypatch.setattr(
        av_mod, "OTLPMetricsPublisher", lambda **kw: DummyPublisher(**kw)
    )
    monkeypatch.setattr(av_mod, "PostgresMonitoringClient", DummyPostgresMonitoring)
    monkeypatch.setattr(av_mod, "SNMPObserverRouter", DummyRouter)

    av.run_snmp_timeseries(
        otlp_endpoint="monit-otlp.cern.ch:4316",
        monit_tenant="tenant",
        monit_password="pass",
        max_workers=1,
    )

    # Router should have been called once and end status ok.
    assert any(
        "avtools_run_snmp_timeseries_end" == e[0] and e[1].get("status") == "ok"
        for e in av.logger.info_events
    )
