from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import avtools.snmp.client as snmp_mod


@dataclass
class DummyDevice:
    equipment_no: str = "EQ1"
    serial_number: str = "SN1"
    eq_class: str = "AVD"
    manufacturer: str = "EPS"
    ip: str | None = "192.0.2.1"


class DummyHandlerFactory:
    def __init__(self, target: Any) -> None:
        self.target = target

    def create(self):
        return None


def test_build_point_includes_non_empty_tags_and_fields(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        snmp_mod, "DeviceHandlerFactory", DummyHandlerFactory, raising=True
    )

    dev = DummyDevice()
    client = snmp_mod.SNMPClient(targets=[dev], snmp_engine=None)

    point = client._build_point("m", dev, {"x": 1})

    assert point["measurement"] == "m"
    assert point["fields"] == {"x": 1}

    # Tags are best-effort resolved via TAG_KEYS
    assert point["tags"]["equipmentno"] == "EQ1"
    assert point["tags"]["serialnumber"] == "SN1"
    assert point["tags"]["eqclass"] == "AVD"
    assert point["tags"]["manufacturer"] == "EPS"
    assert point["tags"]["ip"] == "192.0.2.1"
