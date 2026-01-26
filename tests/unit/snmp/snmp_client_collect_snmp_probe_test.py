from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import avtools.snmp.client as snmp_mod


@dataclass
class DummyDevice:
    ip: str | None
    equipment_no: str


class DummyHandler:
    def __init__(self, ok: bool, *, raise_in_probe: bool = False) -> None:
        self.ok = ok
        self.raise_in_probe = raise_in_probe
        self.engine = None

    def set_engine(self, engine: Any) -> None:
        self.engine = engine

    def probe(self) -> bool:
        if self.raise_in_probe:
            raise RuntimeError("boom")
        return self.ok


class Factory:
    """Return a handler based on target IP."""

    def __init__(self, target: DummyDevice) -> None:
        self.target = target

    def create(self):
        if self.target.ip == "192.0.2.1":
            return DummyHandler(True)
        if self.target.ip == "192.0.2.2":
            return DummyHandler(False)
        if self.target.ip == "192.0.2.3":
            return DummyHandler(True, raise_in_probe=True)
        return None


async def inline_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def test_collect_snmp_probe_returns_alive_devices_and_points(monkeypatch: Any) -> None:
    monkeypatch.setattr(snmp_mod, "DeviceHandlerFactory", Factory, raising=True)
    monkeypatch.setattr(snmp_mod, "to_thread", inline_to_thread, raising=True)

    dev1 = DummyDevice(ip="192.0.2.1", equipment_no="EQ1")
    dev2 = DummyDevice(ip="192.0.2.2", equipment_no="EQ2")
    dev3 = DummyDevice(ip="192.0.2.3", equipment_no="EQ3")

    client = snmp_mod.SNMPClient(targets=[dev1, dev2, dev3], snmp_engine=None)

    points, alive = asyncio.run(client.collect_snmp_probe())

    assert {d.equipment_no for d in alive} == {"EQ1"}

    # Probe points are created for all devices that have handlers & IPs
    # even if probe fails.
    assert len(points) == 3
    by_eq = {p["tags"].get("equipmentno"): p for p in points}
    assert by_eq["EQ1"]["fields"]["status"] == 1
    assert by_eq["EQ2"]["fields"]["status"] == 0
    assert by_eq["EQ3"]["fields"]["status"] == 0
