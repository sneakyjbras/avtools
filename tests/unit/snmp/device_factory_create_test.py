from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import avtools.snmp.factories.device_factory as mod


@dataclass
class DummyDevice:
    eq_class: str | None
    ip: str | None = None
    equipment_no: str | None = None
    serial_number: str | None = None


class DummyFactory:
    def __init__(self, target: Any) -> None:
        self.target = target

    def create(self) -> str:
        return "handler"


def test_device_handler_factory_returns_none_for_unsupported_class(
    monkeypatch: Any,
) -> None:
    dev = DummyDevice(eq_class="UNKNOWN", ip="192.0.2.1")

    calls: list[dict[str, Any]] = []

    def warn(event: str, **kwargs: Any) -> None:
        calls.append({"event": event, **kwargs})

    monkeypatch.setattr(
        mod, "logger", type("L", (), {"warning": staticmethod(warn)})(), raising=True
    )

    out = mod.DeviceHandlerFactory(dev).create()
    assert out is None
    assert calls and calls[0]["event"] == "unsupported_device_class"


def test_device_handler_factory_uses_registry_and_instantiates_factory(
    monkeypatch: Any,
) -> None:
    dev = DummyDevice(eq_class="AVD", ip="192.0.2.1")

    monkeypatch.setattr(mod, "_FACTORY_REGISTRY", {"AVD": DummyFactory}, raising=True)

    out = mod.DeviceHandlerFactory(dev).create()
    assert out == "handler"
