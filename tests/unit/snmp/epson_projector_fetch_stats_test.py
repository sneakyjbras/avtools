from __future__ import annotations

from typing import Any

import avtools.snmp.handlers.projector as mod


class V:
    """Value wrapper supporting int-like and string-like conversions."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def __int__(self) -> int:
        return int(self.value)

    def __str__(self) -> str:
        return str(self.value)


def test_fetch_stats_returns_dict_and_parses_fields(monkeypatch: Any) -> None:
    proj = mod.EpsonProjector("192.0.2.1")

    # Mimic pysnmp var_binds structure: list of (oid, value)
    var_binds = [
        ("sysUpTime", V(12300)),  # timeticks => 123.00s
        ("fw", V("FW1")),
        ("lamp", V(42)),
        ("pwr", V("ON")),
    ]

    monkeypatch.setattr(
        proj,
        "_snmp_get",
        lambda *args, **kwargs: (None, None, 0, var_binds),
    )

    stats = proj.fetch_stats()

    assert stats["uptime_seconds"] == 123.0
    assert stats["firmware"] == "FW1"
    assert stats["lamp_hours"] == 42
    assert stats["power_status"] == "ON"


def test_fetch_stats_returns_empty_on_snmp_error(monkeypatch: Any) -> None:
    proj = mod.EpsonProjector("192.0.2.1")

    monkeypatch.setattr(proj, "_snmp_get", lambda *a, **k: ("timeout", None, 0, []))
    assert proj.fetch_stats() == {}

    monkeypatch.setattr(proj, "_snmp_get", lambda *a, **k: (None, "bad", 0, []))
    assert proj.fetch_stats() == {}
