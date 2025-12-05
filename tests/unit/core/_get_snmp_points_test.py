from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import structlog

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools


@dataclass
class DummyDevice:
    """Minimal stand-in for LanDBDevice. We only need an IP attribute."""

    ip: str


@dataclass
class SNMPScenario:
    """
    Controls DummySNMPClient behaviour for a test.

    - alive_after_ping: IPs considered alive after ping. None = all targets alive.
    - alive_after_probe: IPs considered alive after probe. None = all current targets.
    - raise_on_ping/probe/query: simulate exceptions at each stage.
    - query_metric_value: value stored in query points.
    """

    alive_after_ping: set[str] | None = None
    alive_after_probe: set[str] | None = None
    raise_on_ping: bool = False
    raise_on_probe: bool = False
    raise_on_query: bool = False
    query_metric_value: int = 1


def make_avtools_for_tests() -> AVTools:
    # Bypass __init__ so we don't create a real PostgresClient
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    return av


def make_dummy_snmp_client_factory(scenario: SNMPScenario):
    """Return a DummySNMPClient class bound to the given scenario."""

    class DummySNMPClient:
        def __init__(self, targets: Iterable[DummyDevice]) -> None:
            # _get_snmp_points sets/overwrites .targets between stages
            self.targets = list(targets)

        async def collect_ping(self):
            if scenario.raise_on_ping:
                raise RuntimeError("ping error")

            alive_ips = (
                scenario.alive_after_ping
                if scenario.alive_after_ping is not None
                else {d.ip for d in self.targets}
            )

            points = []
            for dev in self.targets:
                status = 1 if dev.ip in alive_ips else 0
                points.append(
                    {
                        "measurement": "ping",
                        "tags": {"ip": dev.ip},
                        "fields": {"status": status},
                    }
                )
            return points

        async def collect_snmp_probe(self):
            if scenario.raise_on_probe:
                raise RuntimeError("probe error")

            # After ping, self.targets has been shrunk to ok IPs
            if scenario.alive_after_probe is None:
                alive_devices = list(self.targets)
            else:
                alive_devices = [
                    d for d in self.targets if d.ip in scenario.alive_after_probe
                ]

            points = []
            for dev in alive_devices:
                points.append(
                    {
                        "measurement": "probe",
                        "tags": {"ip": dev.ip},
                        "fields": {"uptime": 1},
                    }
                )
            return points, alive_devices

        async def collect_snmp_query(self, devices: list[DummyDevice]):
            if scenario.raise_on_query:
                raise RuntimeError("query error")

            points = []
            for dev in devices:
                points.append(
                    {
                        "measurement": "query",
                        "tags": {"ip": dev.ip},
                        "fields": {"value": scenario.query_metric_value},
                    }
                )
            return points

    return DummySNMPClient


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------


def test_get_snmp_points_no_devices_returns_empty():
    av = make_avtools_for_tests()

    points = asyncio.run(av._get_snmp_points(devices=[], max_workers=4))

    assert points == []


def test_get_snmp_points_ping_no_alive_means_only_ping_points(monkeypatch):
    """
    Ping stage marks all devices as down (status=0) → no alive IPs,
    so there should be no probe/query points.
    """
    av = make_avtools_for_tests()

    devices = [
        DummyDevice(ip="10.0.0.34"),
        DummyDevice(ip="10.0.0.38"),
        DummyDevice(ip="10.0.0.39"),
    ]

    scenario = SNMPScenario(alive_after_ping=set())  # nobody alive
    DummySNMPClient = make_dummy_snmp_client_factory(scenario)
    monkeypatch.setattr(core, "SNMPClient", DummySNMPClient)

    points = asyncio.run(av._get_snmp_points(devices=devices, max_workers=4))

    ping_pts = [p for p in points if p["measurement"] == "ping"]
    probe_pts = [p for p in points if p["measurement"] == "probe"]
    query_pts = [p for p in points if p["measurement"] == "query"]

    assert len(ping_pts) == len(devices)
    assert all(p["fields"]["status"] == 0 for p in ping_pts)
    assert probe_pts == []
    assert query_pts == []


def test_get_snmp_points_probe_no_alive_means_no_query_points(monkeypatch):
    """
    Ping marks all devices as alive, but probe yields no alive devices,
    so SNMP query should have no points.
    """
    av = make_avtools_for_tests()

    devices = [
        DummyDevice(ip="10.0.0.14"),
        DummyDevice(ip="10.0.0.404"),
    ]

    scenario = SNMPScenario(
        alive_after_ping={d.ip for d in devices},  # all alive after ping
        alive_after_probe=set(),  # nobody alive after probe
    )
    DummySNMPClient = make_dummy_snmp_client_factory(scenario)
    monkeypatch.setattr(core, "SNMPClient", DummySNMPClient)

    points = asyncio.run(av._get_snmp_points(devices=devices, max_workers=2))

    ping_pts = [p for p in points if p["measurement"] == "ping"]
    probe_pts = [p for p in points if p["measurement"] == "probe"]
    query_pts = [p for p in points if p["measurement"] == "query"]

    assert len(ping_pts) == len(devices)
    # probe may be empty; key assertion is that there is no query
    assert query_pts == []


def test_get_snmp_points_query_points_merged_with_ping_and_probe(monkeypatch):
    """
    Happy path: ping → probe → query all succeed. All points should be
    present in the merged list with consistent structure.
    """
    av = make_avtools_for_tests()

    devices = [
        DummyDevice(ip="10.0.0.1911"),
        DummyDevice(ip="10.0.0.1978"),
        DummyDevice(ip="10.0.0.2137"),
    ]

    scenario = SNMPScenario(
        alive_after_ping=None,  # all targets alive
        alive_after_probe=None,  # all targets survive probe
        query_metric_value=2025,
    )
    DummySNMPClient = make_dummy_snmp_client_factory(scenario)
    monkeypatch.setattr(core, "SNMPClient", DummySNMPClient)

    points = asyncio.run(av._get_snmp_points(devices=devices, max_workers=2))

    ping_pts = [p for p in points if p["measurement"] == "ping"]
    probe_pts = [p for p in points if p["measurement"] == "probe"]
    query_pts = [p for p in points if p["measurement"] == "query"]

    assert len(ping_pts) == len(devices)
    assert len(probe_pts) == len(devices)
    assert len(query_pts) == len(devices)

    # Check structure and tags
    for p in points:
        assert "measurement" in p
        assert "tags" in p and "ip" in p["tags"]
        assert "fields" in p and isinstance(p["fields"], dict)

    # Query points should have the configured value
    assert all(p["fields"]["value"] == 2025 for p in query_pts)


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


def test_get_snmp_points_ping_exception_returns_empty(monkeypatch):
    """
    If the ping stage raises, the worker should catch and log the exception
    and return an empty list for that chunk.
    """
    av = make_avtools_for_tests()

    devices = [DummyDevice(ip="10.0.0.34"), DummyDevice(ip="10.0.0.38")]
    scenario = SNMPScenario(raise_on_ping=True)
    DummySNMPClient = make_dummy_snmp_client_factory(scenario)
    monkeypatch.setattr(core, "SNMPClient", DummySNMPClient)

    points = asyncio.run(av._get_snmp_points(devices=devices, max_workers=2))

    assert points == []


def test_get_snmp_points_probe_exception_returns_only_ping_points(monkeypatch):
    """
    If probe raises, ping points should still be present, but no probe/query points.
    """
    av = make_avtools_for_tests()

    devices = [DummyDevice(ip="10.0.0.34"), DummyDevice(ip="10.0.0.38")]
    scenario = SNMPScenario(
        alive_after_ping={d.ip for d in devices},  # all alive after ping
        raise_on_probe=True,
    )
    DummySNMPClient = make_dummy_snmp_client_factory(scenario)
    monkeypatch.setattr(core, "SNMPClient", DummySNMPClient)

    points = asyncio.run(av._get_snmp_points(devices=devices, max_workers=1))

    ping_pts = [p for p in points if p["measurement"] == "ping"]
    probe_pts = [p for p in points if p["measurement"] == "probe"]
    query_pts = [p for p in points if p["measurement"] == "query"]

    assert len(ping_pts) == len(devices)
    assert probe_pts == []
    assert query_pts == []


def test_get_snmp_points_query_exception_returns_ping_and_probe_only(monkeypatch):
    """
    If query raises, ping and probe points should still be present,
    but no query points.
    """
    av = make_avtools_for_tests()

    devices = [DummyDevice(ip="10.0.0.34"), DummyDevice(ip="10.0.0.38")]
    scenario = SNMPScenario(
        alive_after_ping={d.ip for d in devices},
        alive_after_probe=None,
        raise_on_query=True,
    )
    DummySNMPClient = make_dummy_snmp_client_factory(scenario)
    monkeypatch.setattr(core, "SNMPClient", DummySNMPClient)

    points = asyncio.run(av._get_snmp_points(devices=devices, max_workers=2))

    ping_pts = [p for p in points if p["measurement"] == "ping"]
    probe_pts = [p for p in points if p["measurement"] == "probe"]
    query_pts = [p for p in points if p["measurement"] == "query"]

    assert len(ping_pts) == len(devices)
    assert len(probe_pts) == len(devices)
    assert query_pts == []


# ---------------------------------------------------------------------------
# Chunking / max_workers behaviour
# ---------------------------------------------------------------------------


def test_get_snmp_points_chunking_with_various_workers(monkeypatch):
    """
    Ensure that for different max_workers values, all devices are processed
    exactly once for ping, and we don't lose or duplicate devices.
    """
    av = make_avtools_for_tests()

    devices = [DummyDevice(ip=f"10.0.0.{i}") for i in range(1, 11)]
    scenario = SNMPScenario(
        alive_after_ping=None,  # treat all as alive
        alive_after_probe=None,
    )
    DummySNMPClient = make_dummy_snmp_client_factory(scenario)
    monkeypatch.setattr(core, "SNMPClient", DummySNMPClient)

    for workers in (1, 2, 3, 4, 8, 16):
        points = asyncio.run(av._get_snmp_points(devices=devices, max_workers=workers))
        ping_pts = [p for p in points if p["measurement"] == "ping"]

        # Exactly one ping point per device, regardless of chunking
        assert len(ping_pts) == len(devices)
        seen_ips = {p["tags"]["ip"] for p in ping_pts}
        assert seen_ips == {d.ip for d in devices}


def test_get_snmp_points_points_shape_is_consistent(monkeypatch):
    """
    Final sanity check: merged points preserve consistent structure with tags/fields.
    """
    av = make_avtools_for_tests()

    devices = [
        DummyDevice(ip="10.0.0.34"),
        DummyDevice(ip="10.0.0.38"),
        DummyDevice(ip="10.0.0.39"),
    ]
    scenario = SNMPScenario(
        alive_after_ping=None,
        alive_after_probe=None,
        query_metric_value=34,
    )
    DummySNMPClient = make_dummy_snmp_client_factory(scenario)
    monkeypatch.setattr(core, "SNMPClient", DummySNMPClient)

    points = asyncio.run(av._get_snmp_points(devices=devices, max_workers=3))

    assert points  # not empty
    for p in points:
        assert isinstance(p, dict)
        assert "measurement" in p
        assert "tags" in p and isinstance(p["tags"], dict)
        assert "ip" in p["tags"]
        assert "fields" in p and isinstance(p["fields"], dict)
