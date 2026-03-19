from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import avtools.snmp.factories.device_factory as mod


@dataclass
class DummyDevice:
    """Minimal CachedIPAddress stand-in for the handler factories."""

    eq_class: str | None
    ip: str | None
    equipment_no: str | None = None
    manufacturer: str | None = None


class DummyLogger:
    """Structlog-like logger stub used to avoid AttributeError in tests."""

    def __init__(self) -> None:
        self.debug_calls: list[tuple[str, dict[str, Any]]] = []
        self.warning_calls: list[tuple[str, dict[str, Any]]] = []
        self.exception_calls: list[tuple[str, dict[str, Any]]] = []
        self.info_calls: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **kwargs: Any) -> None:
        self.debug_calls.append((event, dict(kwargs)))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warning_calls.append((event, dict(kwargs)))

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exception_calls.append((event, dict(kwargs)))

    def info(self, event: str, **kwargs: Any) -> None:
        self.info_calls.append((event, dict(kwargs)))


class DummyFactory:
    """Specialized factory used to verify registry dispatch."""

    def __init__(self, target: Any) -> None:
        self.target = target

    def create(self) -> object:
        return {"handler": "ok", "ip": getattr(self.target, "ip", None)}


class BoomFactory:
    """Factory that raises to test generic fallback."""

    def __init__(self, target: Any) -> None:
        self.target = target

    def create(self) -> object:
        raise RuntimeError("boom")


def test_device_handler_factory_falls_back_to_generic_for_unsupported_class(
    monkeypatch: Any,
) -> None:
    """Unknown eq_class should yield a GenericSnmpHandler (probe still possible)."""

    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)

    dev = DummyDevice(eq_class="UNKNOWN", ip="192.0.2.1", equipment_no="EQ1")
    handlers = mod.DeviceHandlerFactory([dev]).get_handlers()

    assert "192.0.2.1" in handlers
    assert isinstance(handlers["192.0.2.1"], mod.GenericSnmpHandler)
    # A debug log is emitted for unsupported classes.
    assert any(ev == "unsupported_device_class" for ev, _ in log.debug_calls)


def test_device_handler_factory_uses_registry_and_instantiates_factory(
    monkeypatch: Any,
) -> None:
    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)
    monkeypatch.setattr(mod, "_FACTORY_REGISTRY", {"AVD": DummyFactory}, raising=True)

    dev = DummyDevice(eq_class="AVD", ip="192.0.2.9", equipment_no="EQ9")
    handlers = mod.DeviceHandlerFactory([dev]).get_handlers()
    assert handlers["192.0.2.9"] == {"handler": "ok", "ip": "192.0.2.9"}
    assert any(ev == "snmp_handlers_built" for ev, _ in log.info_calls)


def test_device_handler_factory_falls_back_to_generic_when_factory_raises(
    monkeypatch: Any,
) -> None:
    """If specialized handler creation blows up, we still return a generic handler."""

    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)
    monkeypatch.setattr(mod, "_FACTORY_REGISTRY", {"AVD": BoomFactory}, raising=True)

    dev = DummyDevice(eq_class="AVD", ip="192.0.2.10", equipment_no="EQ10")
    handlers = mod.DeviceHandlerFactory([dev]).get_handlers()
    assert isinstance(handlers["192.0.2.10"], mod.GenericSnmpHandler)
    assert any(ev == "handler_factory_failed" for ev, _ in log.exception_calls)


def test_device_handler_factory_skips_missing_ip_and_deduplicates(
    monkeypatch: Any,
) -> None:
    """Missing IPs are skipped; duplicate IPs are logged and ignored."""

    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)
    monkeypatch.setattr(mod, "_FACTORY_REGISTRY", {"AVD": DummyFactory}, raising=True)

    a = DummyDevice(eq_class="AVD", ip="", equipment_no="A")
    b = DummyDevice(eq_class="AVD", ip="192.0.2.20", equipment_no="B")
    c = DummyDevice(eq_class="AVD", ip="192.0.2.20", equipment_no="C")

    handlers = mod.DeviceHandlerFactory([a, b, c]).get_handlers()
    assert list(handlers) == ["192.0.2.20"]
    assert any(ev == "device_missing_ip" for ev, _ in log.debug_calls)
    assert any(ev == "duplicate_ip_in_targets" for ev, _ in log.warning_calls)
