from __future__ import annotations

import asyncio

import avtools.snmp.client as snmp_mod


class _Proc:
    def __init__(
        self, *, returncode: int, stdout: bytes = b"", stderr: bytes = b""
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


def test_ping_posix_once_parses_time_token(monkeypatch):
    async def fake_create_subprocess_exec(*args, **kwargs):
        out = b"64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=1.23 ms\n"
        return _Proc(returncode=0, stdout=out, stderr=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(snmp_mod.DeviceHandlerFactory, "get_handlers", lambda self: {})

    c = snmp_mod.SNMPClient(targets=[], ping_timeout_s=1)
    ok, rtt, reason, hint = asyncio.run(c._ping_posix_once("10.0.0.1"))
    assert ok is True
    assert rtt == 1.23
    assert reason is None
    assert hint is None


def test_ping_posix_once_parses_rtt_summary_avg(monkeypatch):
    async def fake_create_subprocess_exec(*args, **kwargs):
        out = (
            b"--- 10.0.0.1 ping statistics ---\n"
            b"1 packets transmitted, 1 received, 0% packet loss, time 0ms\n"
            b"rtt min/avg/max/mdev = 0.111/0.222/0.333/0.000 ms\n"
        )
        return _Proc(returncode=0, stdout=out)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(snmp_mod.DeviceHandlerFactory, "get_handlers", lambda self: {})

    c = snmp_mod.SNMPClient(targets=[], ping_timeout_s=1)
    ok, rtt, reason, hint = asyncio.run(c._ping_posix_once("10.0.0.1"))
    assert ok is True
    assert rtt == 0.222
    assert reason is None
    assert hint is None


def test_ping_posix_once_classifies_packet_loss(monkeypatch):
    async def fake_create_subprocess_exec(*args, **kwargs):
        out = (
            b"--- 10.0.0.2 ping statistics ---\n"
            b"1 packets transmitted, 0 received, 100% packet loss, time 0ms\n"
        )
        return _Proc(returncode=1, stdout=out)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(snmp_mod.DeviceHandlerFactory, "get_handlers", lambda self: {})

    c = snmp_mod.SNMPClient(targets=[], ping_timeout_s=1)
    ok, rtt, reason, hint = asyncio.run(c._ping_posix_once("10.0.0.2"))
    assert ok is False
    assert rtt is None
    assert reason == "packet_loss"
    assert hint is not None
    assert "packet loss" in hint.lower()
