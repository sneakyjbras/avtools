from __future__ import annotations

import avtools.snmp.handlers.pdu as mod


def _ok(vb):
    return (None, None, 0, vb)


def test_sentry4_outlets_state_normalized_and_scaled(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    walk = {
        "1.3.6.1.4.1.1718.4.1.8.3.1.1": {1: 1, 2: 2, 3: 0},  # on / off / unknown
        "1.3.6.1.4.1.1718.4.1.8.3.1.3": {1: 250, 2: 0, 3: 120},  # /100 A
        "1.3.6.1.4.1.1718.4.1.8.3.1.7": {1: 575, 2: 0, 3: 280},  # W
        "1.3.6.1.4.1.1718.4.1.8.3.1.14": {1: 120000, 2: 5, 3: 9000},  # Wh
        "1.3.6.1.4.1.1718.4.1.8.2.1.3": {1: "Server-A", 2: "Spare", 3: "Switch"},
    }
    monkeypatch.setattr(h, "_walk_indexed", lambda base, **k: walk.get(base, {}))
    outs = h.fetch_outlets()
    assert outs[0] == {
        "index": 1,
        "state": 1,
        "current": 2.5,
        "power": 575.0,
        "energy": 120000.0,
        "name": "Server-A",
    }
    assert outs[1]["state"] == 0  # off
    assert outs[2]["state"] == 2  # unknown -> other


def test_sentry3_outlets_state_map(monkeypatch):
    h = mod.Sentry3Pdu("192.0.2.2")
    walk = {
        "1.3.6.1.4.1.1718.3.2.3.1.5": {1: 1, 2: 0, 3: 5},  # on / off / onError
        "1.3.6.1.4.1.1718.3.2.3.1.7": {1: 300, 2: 0, 3: 150},
        "1.3.6.1.4.1.1718.3.2.3.1.14": {1: 690, 2: 0, 3: 340},
        "1.3.6.1.4.1.1718.3.2.3.1.18": {1: 50000, 2: 0, 3: 7000},
        "1.3.6.1.4.1.1718.3.2.3.1.3": {1: "A", 2: "B", 3: "C"},
    }
    monkeypatch.setattr(h, "_walk_indexed", lambda base, **k: walk.get(base, {}))
    outs = h.fetch_outlets()
    assert [o["state"] for o in outs] == [1, 0, 2]  # onError -> other
    assert outs[0]["current"] == 3.0


def test_no_outlets_returns_empty(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    monkeypatch.setattr(h, "_walk_indexed", lambda base, **k: {})
    assert h.fetch_outlets() == []


def test_collect_outlets_flag_gates_fetch_stats(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    h.COLLECT_OUTLETS = False
    monkeypatch.setattr(h, "_snmp_get", lambda oids, **k: _ok([("x", 0)] * len(oids)))
    monkeypatch.setattr(
        h, "_walk_indexed", lambda base, **k: {1: 1}
    )  # would yield outlets if walked
    assert "outlets" not in h.fetch_stats()


def test_partial_outlet_columns(monkeypatch):
    # state present but energy column empty -> energy omitted, others present
    h = mod.Sentry4Pdu("192.0.2.1")
    walk = {
        "1.3.6.1.4.1.1718.4.1.8.3.1.1": {1: 1},
        "1.3.6.1.4.1.1718.4.1.8.3.1.3": {1: 250},
    }
    monkeypatch.setattr(h, "_walk_indexed", lambda base, **k: walk.get(base, {}))
    outs = h.fetch_outlets()
    assert outs[0]["state"] == 1 and outs[0]["current"] == 2.5
    assert "energy" not in outs[0] and "power" not in outs[0]
