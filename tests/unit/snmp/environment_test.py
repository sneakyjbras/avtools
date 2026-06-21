from __future__ import annotations

import avtools.snmp.handlers.pdu as mod


def _ok(vb):
    return (None, None, 0, vb)


def test_sentry4_environment_celsius(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    monkeypatch.setattr(
        h,
        "_snmp_get",
        lambda oids, **k: (
            _ok([("s", 0)]) if "1.9.1.10.0" in oids[0] else _ok([("x", 0)] * len(oids))
        ),
    )
    walk = {
        "1.3.6.1.4.1.1718.4.1.9.3.1.1": {1: 251, 2: 268},  # 25.1, 26.8 C
        "1.3.6.1.4.1.1718.4.1.9.2.1.3": {1: "Inlet", 2: "Outlet"},
        "1.3.6.1.4.1.1718.4.1.10.3.1.1": {1: 42},
        "1.3.6.1.4.1.1718.4.1.10.2.1.3": {1: "RackHumid"},
    }
    monkeypatch.setattr(h, "_walk_indexed", lambda base, **k: walk.get(base, {}))
    env = h.fetch_environment()
    assert env["temperature"] == [
        {"index": 1, "celsius": 25.1, "name": "Inlet"},
        {"index": 2, "celsius": 26.8, "name": "Outlet"},
    ]
    assert env["humidity"] == [{"index": 1, "percent": 42.0, "name": "RackHumid"}]


def test_sentry4_environment_fahrenheit_normalized(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    monkeypatch.setattr(
        h,
        "_snmp_get",
        lambda oids, **k: (
            _ok([("s", 1)]) if "1.9.1.10.0" in oids[0] else _ok([("x", 0)] * len(oids))
        ),
    )
    walk = {
        "1.3.6.1.4.1.1718.4.1.9.3.1.1": {1: 770},  # 77.0 F -> 25.0 C
        "1.3.6.1.4.1.1718.4.1.9.2.1.3": {1: "Inlet"},
    }
    monkeypatch.setattr(h, "_walk_indexed", lambda base, **k: walk.get(base, {}))
    env = h.fetch_environment()
    assert env["temperature"][0]["celsius"] == 25.0


def test_no_sensors_returns_empty(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    monkeypatch.setattr(h, "_snmp_get", lambda oids, **k: _ok([("x", 0)] * len(oids)))
    monkeypatch.setattr(h, "_walk_indexed", lambda base, **k: {})
    assert h.fetch_environment() == {}


def test_sentry3_combined_table_per_sensor_scale(monkeypatch):
    h = mod.Sentry3Pdu("192.0.2.2")
    walk = {
        "1.3.6.1.4.1.1718.3.2.5.1.6": {1: 230},  # 23.0 C
        "1.3.6.1.4.1.1718.3.2.5.1.3": {1: "Probe1"},
        "1.3.6.1.4.1.1718.3.2.5.1.13": {1: 0},  # celsius
        "1.3.6.1.4.1.1718.3.2.5.1.10": {1: 55},
    }
    monkeypatch.setattr(h, "_walk_indexed", lambda base, **k: walk.get(base, {}))
    env = h.fetch_environment()
    assert env["temperature"] == [{"index": 1, "celsius": 23.0, "name": "Probe1"}]
    assert env["humidity"] == [{"index": 1, "percent": 55.0, "name": "Probe1"}]
