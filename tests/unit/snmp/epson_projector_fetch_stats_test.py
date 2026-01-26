from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import avtools.snmp.handlers.projector as mod


@dataclass
class DummyDevice:
    ip: str = "192.0.2.1"
    community: str = "public"
    port: int = 161


class V:
    def __init__(self, value: Any) -> None:
        self.value = value

    def prettyPrint(self) -> str:
        return str(self.value)

    def __int__(self) -> int:
        return int(self.value)


class ErrorStatus:
    def __init__(self, msg: str) -> None:
        self.msg = msg

    def prettyPrint(self) -> str:
        return self.msg


def test_fetch_stats_returns_parsed_dataclass(monkeypatch: Any) -> None:
    # Provide varBinds in the same order as OIDS.values()
    var_binds = [
        ("oid1", V("123")),
        ("oid2", V('"FW1"')),
        ("oid3", V(42)),
        ("oid4", V("01 0000 0000 T1")),
    ]

    def fake_getcmd(*args: Any, **kwargs: Any):
        yield (None, None, 0, var_binds)

    monkeypatch.setattr(mod, "getCmd", fake_getcmd, raising=True)

    proj = mod.EpsonProjector(DummyDevice())
    proj.set_engine(object())

    stats = proj.fetch_stats()

    assert stats.uptime == "123"
    assert stats.firmware == "FW1"
    assert stats.lamp_hours == 42
    assert stats.power_status == "01 0000 0000 T1"


def test_fetch_stats_raises_on_error_indication(monkeypatch: Any) -> None:
    def fake_getcmd(*args: Any, **kwargs: Any):
        yield ("timeout", None, 0, [])

    monkeypatch.setattr(mod, "getCmd", fake_getcmd, raising=True)

    proj = mod.EpsonProjector(DummyDevice())
    proj.set_engine(object())

    with pytest.raises(RuntimeError):
        proj.fetch_stats()


def test_fetch_stats_raises_on_error_status_and_mentions_failed_key(
    monkeypatch: Any,
) -> None:
    def fake_getcmd(*args: Any, **kwargs: Any):
        # error_index 2 -> second OID key is "firmware"
        yield (None, ErrorStatus("noSuch"), 2, [])

    monkeypatch.setattr(mod, "getCmd", fake_getcmd, raising=True)

    proj = mod.EpsonProjector(DummyDevice())
    proj.set_engine(object())

    with pytest.raises(RuntimeError) as exc:
        proj.fetch_stats()

    assert "firmware" in str(exc.value)
