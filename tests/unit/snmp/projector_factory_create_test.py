from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import avtools.snmp.factories.projector_factory as mod


@dataclass
class DummyDevice:
    ip: str = "192.0.2.1"
    manufacturer: str | None = None


class DummyLogger:
    def __init__(self) -> None:
        self.debug_calls: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **kwargs: Any) -> None:
        self.debug_calls.append((event, dict(kwargs)))


class DummyEpson:
    def __init__(self, ip: str, *, community: str = "public", port: int = 161) -> None:
        self.ip = ip
        self.community = community
        self.port = port


def test_projector_factory_returns_eps_handler_when_brand_missing(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(mod, "EpsonProjector", DummyEpson, raising=True)
    monkeypatch.setattr(mod, "logger", DummyLogger(), raising=True)

    dev = DummyDevice(manufacturer=None)
    handler = mod.ProjectorHandlerFactory(dev).create()

    assert isinstance(handler, DummyEpson)
    assert handler.ip == dev.ip
    assert handler.community == "public"
    assert handler.port == 161


def test_projector_factory_returns_eps_handler_for_epson_variants(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(mod, "EpsonProjector", DummyEpson, raising=True)
    monkeypatch.setattr(mod, "logger", DummyLogger(), raising=True)

    for brand in ["eps", " EPSON "]:
        dev = DummyDevice(manufacturer=brand)
        handler = mod.ProjectorHandlerFactory(dev).create()
        assert isinstance(handler, DummyEpson)


def test_projector_factory_returns_none_and_logs_for_unknown_manufacturer(
    monkeypatch: Any,
) -> None:
    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)
    monkeypatch.setattr(mod, "EpsonProjector", DummyEpson, raising=True)

    dev = DummyDevice(manufacturer="OTHER")
    handler = mod.ProjectorHandlerFactory(dev).create()

    assert handler is None
    assert any(ev == "unsupported_projector_manufacturer" for ev, _ in log.debug_calls)
