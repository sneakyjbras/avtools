from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import avtools.snmp.client as snmp_mod


@dataclass
class DummyDevice:
    ip: str | None
    equipment_no: str | None = None


class DummyHandlerFactory:
    def __init__(self, target: Any) -> None:
        self.target = target

    def create(self):
        return None


async def inline_to_thread(fn, *args, **kwargs):
    # Mimic asyncio.to_thread but execute synchronously for deterministic tests.
    return fn(*args, **kwargs)


def test_collect_ping_builds_points_and_filters_alive_ip_list(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        snmp_mod, "DeviceHandlerFactory", DummyHandlerFactory, raising=True
    )
    monkeypatch.setattr(snmp_mod, "to_thread", inline_to_thread, raising=True)

    dev1 = DummyDevice(ip="192.0.2.1", equipment_no="EQ1")
    dev2 = DummyDevice(ip="192.0.2.2", equipment_no="EQ2")
    dev3 = DummyDevice(ip=None, equipment_no="EQ3")

    client = snmp_mod.SNMPClient(targets=[dev1, dev2, dev3], snmp_engine=None)

    # Simulate ping RTT for only dev1
    monkeypatch.setattr(
        client, "_ping_host", lambda ip, timeout: 5.0 if ip == "192.0.2.1" else None
    )

    points = asyncio.run(client.collect_ping())

    assert len(points) == 3

    # dev1 alive
    p1 = next(p for p in points if p["tags"].get("equipmentno") == "EQ1")
    assert p1["fields"]["status"] == 1
    assert p1["fields"]["rtt_ms"] == 5.0

    # dev2 down
    p2 = next(p for p in points if p["tags"].get("equipmentno") == "EQ2")
    assert p2["fields"]["status"] == 0
    assert "rtt_ms" not in p2["fields"]

    # dev3 has no IP -> status forced 0
    p3 = next(p for p in points if p["tags"].get("equipmentno") == "EQ3")
    assert p3["fields"]["status"] == 0
