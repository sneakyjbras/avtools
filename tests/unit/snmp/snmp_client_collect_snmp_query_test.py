from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import avtools.snmp.client as snmp_mod


@dataclass
class DummyDevice:
    ip: str | None
    equipment_no: str


class DummyStats:
    def __init__(self, value: int) -> None:
        self.value = value

    def to_human(self) -> dict[str, str]:
        return {"Value": str(self.value)}


class DummyHandler:
    def __init__(self, value: int, *, raise_in_fetch: bool = False) -> None:
        self.value = value
        self.raise_in_fetch = raise_in_fetch

    def set_engine(self, engine: Any) -> None:
        pass

    def fetch_stats(self) -> DummyStats:
        if self.raise_in_fetch:
            raise RuntimeError("boom")
        return DummyStats(self.value)


async def inline_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def test_collect_snmp_query_builds_points_from_alive_devices(monkeypatch: Any) -> None:
    monkeypatch.setattr(snmp_mod, "to_thread", inline_to_thread, raising=True)

    client = object.__new__(snmp_mod.SNMPClient)
    client.device_map = {
        "192.0.2.1": DummyDevice(ip="192.0.2.1", equipment_no="EQ1"),
        "192.0.2.2": DummyDevice(ip="192.0.2.2", equipment_no="EQ2"),
    }
    client.handlers = {
        "192.0.2.1": DummyHandler(1),
        "192.0.2.2": DummyHandler(2, raise_in_fetch=True),
    }

    alive = [client.device_map["192.0.2.1"], client.device_map["192.0.2.2"]]

    points = asyncio.run(client.collect_snmp_query(alive=alive))

    # One device raises fetch error -> skipped
    assert len(points) == 1
    p = points[0]
    assert p["measurement"] == "snmp_query"
    assert p["tags"]["ip"] == "192.0.2.1"
    assert p["fields"] == {"Value": "1"}


def test_collect_snmp_query_calls_probe_when_alive_devices_none(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(snmp_mod, "to_thread", inline_to_thread, raising=True)

    client = object.__new__(snmp_mod.SNMPClient)
    client.device_map = {"192.0.2.1": DummyDevice(ip="192.0.2.1", equipment_no="EQ1")}
    client.handlers = {"192.0.2.1": DummyHandler(7)}

    async def fake_probe():
        # match (points, alive_devices) signature
        return [], [client.device_map["192.0.2.1"]]

    monkeypatch.setattr(client, "collect_snmp_probe", fake_probe, raising=True)

    points = asyncio.run(client.collect_snmp_query(alive=None))
    assert len(points) == 1
    assert points[0]["fields"] == {"Value": "7"}
