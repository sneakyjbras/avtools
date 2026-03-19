from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import avtools.snmp.handlers.abstract_device_handler as mod


class DummyHandler(mod.AbstractDeviceHandler):
    def fetch_stats(self) -> Mapping[str, Any]:
        return {}


class _Val:
    def __init__(self, s: str) -> None:
        self._s = s

    def prettyPrint(self) -> str:
        return self._s


def test_fetch_sysdescr_happy_path_parses_value(monkeypatch: Any) -> None:
    """fetch_sysdescr should parse the first varBind value and trim it."""

    def fake_getcmd(*args: Any, **kwargs: Any):
        yield (None, None, 0, [("oid", _Val("  my descr  "))])

    monkeypatch.setattr(mod, "getCmd", fake_getcmd, raising=True)
    h = DummyHandler(ip="192.0.2.1")

    assert h.fetch_sysdescr() == "my descr"


def test_fetch_sysdescr_returns_none_on_error_or_empty(monkeypatch: Any) -> None:
    def err_ind(*args: Any, **kwargs: Any):
        yield ("timeout", None, 0, [])

    monkeypatch.setattr(mod, "getCmd", err_ind, raising=True)
    h = DummyHandler(ip="192.0.2.1")
    assert h.fetch_sysdescr() is None

    def empty_vb(*args: Any, **kwargs: Any):
        yield (None, None, 0, [])

    monkeypatch.setattr(mod, "getCmd", empty_vb, raising=True)
    assert h.fetch_sysdescr() is None
