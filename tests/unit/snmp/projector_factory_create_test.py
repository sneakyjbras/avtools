from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import avtools.snmp.factories.projector_factory as mod


@dataclass
class DummyDevice:
    manufacturer: str
    ip: str = "192.0.2.1"


class DummyEpson:
    def __init__(self, target: Any) -> None:
        self.target = target


def test_projector_factory_returns_eps_handler_for_epson(monkeypatch: Any) -> None:
    monkeypatch.setattr(mod, "EpsonProjector", DummyEpson, raising=True)

    dev = DummyDevice(manufacturer=" EPS ")
    handler = mod.ProjectorHandlerFactory(dev).create()

    assert isinstance(handler, DummyEpson)
    assert handler.target is dev


def test_projector_factory_returns_none_and_logs_for_unknown_manufacturer(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def warning(*args: Any, **kwargs: Any) -> None:
        calls.append(("warning", args, kwargs))

    monkeypatch.setattr(
        mod, "logger", type("L", (), {"warning": staticmethod(warning)})(), raising=True
    )

    dev = DummyDevice(manufacturer="OTHER")
    handler = mod.ProjectorHandlerFactory(dev).create()

    assert handler is None
    assert calls  # warning was emitted
