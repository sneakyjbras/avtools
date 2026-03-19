from __future__ import annotations

import avtools.snmp.handlers.abstract_device_handler as mod


class DummyHandler(mod.AbstractDeviceHandler):
    def fetch_stats(self):
        return {}


class _Val:
    def __init__(self, s: str):
        self.s = s

    def prettyPrint(self):
        return self.s


class _VB:
    def __init__(self, v):
        self.v = v

    def __getitem__(self, idx):
        if idx == 1:
            return self.v
        raise IndexError


def test_probe_true_when_no_error(monkeypatch):
    def fake_getCmd(*args, **kwargs):
        return iter([(None, 0, 0, [("oid", _Val("123"))])])

    monkeypatch.setattr(mod, "getCmd", fake_getCmd)

    h = DummyHandler("127.0.0.1")
    assert h.probe() is True


def test_probe_false_on_error(monkeypatch):
    def fake_getCmd(*args, **kwargs):
        return iter([("boom", 0, 0, [])])

    monkeypatch.setattr(mod, "getCmd", fake_getCmd)

    h = DummyHandler("127.0.0.1")
    assert h.probe() is False


def test_fetch_sysdescr_parses_tuple_varbind(monkeypatch):
    def fake_getCmd(*args, **kwargs):
        return iter([(None, 0, 0, [("oid", _Val("Linux"))])])

    monkeypatch.setattr(mod, "getCmd", fake_getCmd)

    h = DummyHandler("127.0.0.1")
    assert h.fetch_sysdescr() == "Linux"


def test_fetch_sysdescr_parses_indexable_varbind(monkeypatch):
    def fake_getCmd(*args, **kwargs):
        return iter([(None, 0, 0, [_VB(_Val("Cisco"))])])

    monkeypatch.setattr(mod, "getCmd", fake_getCmd)

    h = DummyHandler("127.0.0.1")
    assert h.fetch_sysdescr() == "Cisco"


def test_fetch_sysdescr_returns_none_on_error_status(monkeypatch):
    def fake_getCmd(*args, **kwargs):
        return iter([(None, 1, 0, [("oid", _Val("X"))])])

    monkeypatch.setattr(mod, "getCmd", fake_getCmd)

    h = DummyHandler("127.0.0.1")
    assert h.fetch_sysdescr() is None
