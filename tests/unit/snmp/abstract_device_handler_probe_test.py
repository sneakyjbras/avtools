from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import avtools.snmp.handlers.abstract_device_handler as mod


class DummyHandler(mod.AbstractDeviceHandler):
    def fetch_stats(self) -> Mapping[str, Any]:
        return {}


def test_probe_returns_true_when_no_error_indication_or_status(
    monkeypatch: Any,
) -> None:
    def fake_getcmd(*args: Any, **kwargs: Any):
        yield (None, None, None, [])

    monkeypatch.setattr(mod, "getCmd", fake_getcmd, raising=True)

    h = DummyHandler(ip="192.0.2.1")
    assert h.probe() is True


def test_probe_returns_false_when_error_indication(monkeypatch: Any) -> None:
    def fake_getcmd(*args: Any, **kwargs: Any):
        yield ("timeout", None, None, [])

    monkeypatch.setattr(mod, "getCmd", fake_getcmd, raising=True)

    h = DummyHandler(ip="192.0.2.1")
    assert h.probe() is False


def test_probe_returns_false_when_error_status(monkeypatch: Any) -> None:
    def fake_getcmd(*args: Any, **kwargs: Any):
        yield (None, "badStatus", None, [])

    monkeypatch.setattr(mod, "getCmd", fake_getcmd, raising=True)

    h = DummyHandler(ip="192.0.2.1")
    assert h.probe() is False
