from __future__ import annotations

from subprocess import CalledProcessError
from typing import Any

import avtools.snmp.client as snmp_mod


def test_ping_host_parses_time_token(monkeypatch: Any) -> None:
    monkeypatch.setattr(snmp_mod, "_system", lambda: "Linux", raising=True)

    out = "64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=2.34 ms"
    monkeypatch.setattr(snmp_mod, "check_output", lambda *a, **k: out, raising=True)

    client = object.__new__(snmp_mod.SNMPClient)
    assert client._ping_host("192.0.2.1", timeout=1) == 2.34


def test_ping_host_time_lt_is_parsed(monkeypatch: Any) -> None:
    monkeypatch.setattr(snmp_mod, "_system", lambda: "Linux", raising=True)

    out = "1 packets transmitted, 1 received, time<1 ms"
    monkeypatch.setattr(snmp_mod, "check_output", lambda *a, **k: out, raising=True)

    client = object.__new__(snmp_mod.SNMPClient)
    assert client._ping_host("192.0.2.1", timeout=1) == 1.0


def test_ping_host_returns_none_on_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(snmp_mod, "_system", lambda: "Linux", raising=True)

    def boom(*args: Any, **kwargs: Any):
        raise CalledProcessError(1, cmd=["ping"], output="")

    monkeypatch.setattr(snmp_mod, "check_output", boom, raising=True)

    client = object.__new__(snmp_mod.SNMPClient)
    assert client._ping_host("192.0.2.1", timeout=1) is None
