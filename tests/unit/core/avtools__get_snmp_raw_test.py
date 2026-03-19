from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import avtools.core.av_tools as av_mod
from avtools.core.av_tools import AVTools
from avtools.snmp.client import PingResult, ProbeResult, QueryResult


@dataclass
class DummyDevice:
    """Minimal stand-in for CachedIPAddress used by SNMP pipeline."""

    ip: str
    equipment_no: str
    eq_class: str | None = None
    category: str | None = None


class DummyLogger:
    def __init__(self) -> None:
        self.info_events: list[tuple[str, dict[str, Any]]] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.info_events.append((str(msg), dict(kwargs)))


def make_avtools_for_tests() -> AVTools:
    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    return av


@dataclass
class SNMPScenario:
    alive_after_ping: set[str] | None = None
    alive_after_probe: set[str] | None = None


def make_dummy_snmp_client(scenario: SNMPScenario):
    """Factory returning a Dummy SNMPClient class bound to scenario."""

    class DummySNMPClient:
        probe_called_with: list[str] = []
        query_called_with: list[str] = []

        def __init__(self, targets: list[DummyDevice]) -> None:
            self.targets = list(targets)

        async def collect_ping(self):
            alive_ips = (
                scenario.alive_after_ping
                if scenario.alive_after_ping is not None
                else {d.ip for d in self.targets}
            )
            results: list[PingResult] = []
            alive: list[DummyDevice] = []
            for d in self.targets:
                up = 1 if d.ip in alive_ips else 0
                if up == 1:
                    alive.append(d)
                results.append(
                    PingResult(
                        device=d,  # type: ignore[arg-type]
                        ip=d.ip,
                        equipmentno=d.equipment_no,
                        up=up,
                        rtt_ms=None,
                        reason=None if up == 1 else "down",
                        attempts=1,
                    )
                )
            return results, alive

        async def collect_snmp_probe(self, devices: list[DummyDevice]):
            # Record every device IP we were asked to probe.
            type(self).probe_called_with.extend([d.ip for d in devices])

            alive_ips = (
                scenario.alive_after_probe
                if scenario.alive_after_probe is not None
                else {d.ip for d in devices}
            )
            results: list[ProbeResult] = []
            alive: list[DummyDevice] = []
            for d in devices:
                up = 1 if d.ip in alive_ips else 0
                if up == 1:
                    alive.append(d)
                results.append(
                    ProbeResult(
                        device=d,  # type: ignore[arg-type]
                        ip=d.ip,
                        equipmentno=d.equipment_no,
                        up=up,
                        eqclass=d.eq_class,
                        category=d.category,
                        sysdescr="dummy" if up == 1 else None,
                    )
                )
            return results, alive

        async def collect_snmp_queries(self, devices: list[DummyDevice]):
            # Record every device IP we were asked to query.
            type(self).query_called_with.extend([d.ip for d in devices])

            out: list[QueryResult] = []
            for d in devices:
                out.append(
                    QueryResult(
                        device=d,  # type: ignore[arg-type]
                        ip=d.ip,
                        equipmentno=d.equipment_no,
                        query="projector",
                        eqclass=d.eq_class,
                        category=d.category,
                        stats={"uptime_seconds": 123},
                    )
                )
            return out

    return DummySNMPClient


def test_get_snmp_raw_no_devices_returns_empty(monkeypatch):
    av = make_avtools_for_tests()

    # Should not touch SNMPClient at all.
    DummySNMPClient = make_dummy_snmp_client(SNMPScenario())
    monkeypatch.setattr(av_mod, "SNMPClient", DummySNMPClient)

    ping, probe, query = asyncio.run(av._get_snmp_raw([], max_workers=4))
    assert ping == []
    assert probe == []
    assert query == []


def test_get_snmp_raw_probe_runs_on_all_targets_even_if_ping_none_alive(monkeypatch):
    """Policy: SNMP probe runs on ALL targets (ICMP may be blocked)."""

    devices = [
        DummyDevice(
            ip="10.0.0.1", equipment_no="EQ1", eq_class="AVD", category="AV-PRO"
        ),
        DummyDevice(
            ip="10.0.0.2", equipment_no="EQ2", eq_class="AVD", category="AV-PRO"
        ),
        DummyDevice(
            ip="10.0.0.3", equipment_no="EQ3", eq_class="AVD", category="AV-PRO"
        ),
    ]

    scenario = SNMPScenario(
        alive_after_ping=set(),  # nobody alive after ping
        alive_after_probe={"10.0.0.2", "10.0.0.3"},
    )
    DummySNMPClient = make_dummy_snmp_client(scenario)
    monkeypatch.setattr(av_mod, "SNMPClient", DummySNMPClient)

    av = make_avtools_for_tests()
    ping_results, probe_results, query_results = asyncio.run(
        av._get_snmp_raw(devices, max_workers=2)
    )

    assert len(ping_results) == 3
    assert len(probe_results) == 3

    # Probe must be called with *all* devices, regardless of ping outcome.
    assert set(DummySNMPClient.probe_called_with) == {
        "10.0.0.1",
        "10.0.0.2",
        "10.0.0.3",
    }

    # Query should only run on SNMP-alive subset.
    assert set(DummySNMPClient.query_called_with) == {"10.0.0.2", "10.0.0.3"}
    assert len(query_results) == 2


def test_get_snmp_raw_no_snmp_alive_yields_no_queries(monkeypatch):
    devices = [
        DummyDevice(ip="10.0.0.10", equipment_no="EQ10"),
        DummyDevice(ip="10.0.0.11", equipment_no="EQ11"),
    ]

    scenario = SNMPScenario(
        alive_after_ping={"10.0.0.10", "10.0.0.11"},
        alive_after_probe=set(),
    )

    DummySNMPClient = make_dummy_snmp_client(scenario)
    monkeypatch.setattr(av_mod, "SNMPClient", DummySNMPClient)

    av = make_avtools_for_tests()
    ping_results, probe_results, query_results = asyncio.run(
        av._get_snmp_raw(devices, max_workers=8)
    )

    assert len(ping_results) == 2
    assert len(probe_results) == 2
    assert query_results == []
    assert DummySNMPClient.query_called_with == []
