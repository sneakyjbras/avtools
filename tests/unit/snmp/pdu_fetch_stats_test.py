from __future__ import annotations

from typing import Any

import pytest

import avtools.snmp.handlers.pdu as mod
from avtools.snmp.mibs import MibResolver


def _ok(var_binds):
    """pysnmp-style (errInd, errStat, errIdx, varBinds) success tuple."""
    return (None, None, 0, var_binds)


@pytest.fixture
def no_mib(monkeypatch):
    """Force MIB-absent (hardcoded-fallback) mode."""
    monkeypatch.setattr(mod, "get_resolver", lambda: MibResolver({}))


@pytest.fixture(autouse=True)
def _no_sensor_walk(monkeypatch):
    """fetch_stats now also walks sensor tables — stub it so these unit tests
    (which don't test environment) make no network calls and run fast."""
    monkeypatch.setattr(mod.AbstractPdu, "_walk_indexed", lambda self, base, **k: {})


# --------------------------------------------------------------------------- #
# Sentry4 (PRO2 / CW-*)
# --------------------------------------------------------------------------- #
# GET order: sysUpTime, inputStatus, activePower, lineCurrent, currentUtilized,
#            phaseVoltage, firmware


def test_sentry4_mib_mode_parses_scales_and_labels(monkeypatch: Any) -> None:
    h = mod.Sentry4Pdu("192.0.2.1")
    vb = [
        ("u", 360000),
        ("s", 0),
        ("p", 1500),
        ("c", 1234),
        ("ut", 456),
        ("v", 2300),
        ("fw", "8.0.3"),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["input_status"] == 0
    assert s["healthy"] == 1
    assert s["status_text"] == "normal"  # from MIB enum
    assert s["active_power_watts"] == 1500.0
    assert s["line_current_amps"] == 12.34  # hundredth-Amps
    assert s["current_utilized_pct"] == 45.6  # tenth-percent
    assert s["voltage_volts"] == 230.0  # tenth-Volts
    assert s["uptime_seconds"] == 3600.0
    assert s["firmware"] == "8.0.3"


def test_sentry4_non_normal_status_label(monkeypatch: Any) -> None:
    h = mod.Sentry4Pdu("192.0.2.1")
    vb = [("u", 100), ("s", 12), ("p", 0), ("c", 0), ("ut", 0), ("v", 2300), ("fw", "")]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["healthy"] == 0
    assert s["status_text"] == "breakerTripped"  # code 12 resolved from MIB


def test_sentry4_fallback_mode_matches_scaling(no_mib, monkeypatch: Any) -> None:
    h = mod.Sentry4Pdu("192.0.2.1")
    vb = [
        ("u", 360000),
        ("s", 2),
        ("p", 1500),
        ("c", 1234),
        ("ut", 456),
        ("v", 2300),
        ("fw", "8.0.3"),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    # identical scaling without the MIB
    assert s["line_current_amps"] == 12.34
    assert s["voltage_volts"] == 230.0
    assert s["current_utilized_pct"] == 45.6
    # status label from the hardcoded fallback enum
    assert s["status_text"] == "purged"


def test_sentry4_snmp_error_returns_empty(monkeypatch: Any) -> None:
    h = mod.Sentry4Pdu("192.0.2.1")
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: ("timeout", None, 0, []))
    assert h.fetch_stats() == {}


# --------------------------------------------------------------------------- #
# Sentry3 (CDU / AA-*)
# --------------------------------------------------------------------------- #
# GET order: sysUpTime, infeedStatus, infeedLoadValue, infeedCapacity,
#            infeedVoltage, infeedPower, systemVersion


def test_sentry3_mib_mode_voltage_fix_and_derived_util(monkeypatch: Any) -> None:
    h = mod.Sentry3Pdu("192.0.2.2")
    # infeedVoltage raw 2300 -> 230.0 V (tenth-Volts); this was a 10x bug pre-MIB.
    vb = [
        ("u", 120000),
        ("s", 1),
        ("load", 850),
        ("cap", 16),
        ("v", 2300),
        ("p", 1900),
        ("fw", "7.0c"),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["input_status"] == 1
    assert s["healthy"] == 1
    assert s["status_text"] == "on"
    assert s["line_current_amps"] == 8.5
    assert s["voltage_volts"] == 230.0
    assert s["active_power_watts"] == 1900.0
    assert s["current_utilized_pct"] == 100.0 * 8.5 / 16  # derived
    assert "capacity_amps" not in s  # internal, dropped
    assert s["uptime_seconds"] == 1200.0
    assert s["firmware"] == "7.0c"


def test_sentry3_nocomm_label_and_no_util_without_capacity(monkeypatch: Any) -> None:
    h = mod.Sentry3Pdu("192.0.2.2")
    vb = [("u", 100), ("s", 6), ("load", 500), ("cap", 0), ("v", 2300), ("p", 0), ("fw", "")]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["healthy"] == 0
    assert s["status_text"] == "noComm"
    assert "current_utilized_pct" not in s


def test_sentry3_fallback_mode_voltage_scaling(no_mib, monkeypatch: Any) -> None:
    h = mod.Sentry3Pdu("192.0.2.2")
    vb = [("u", 100), ("s", 1), ("load", 850), ("cap", 16), ("v", 2300), ("p", 1900), ("fw", "x")]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["voltage_volts"] == 230.0  # fallback scale also 10
    assert s["status_text"] == "on"


# --------------------------------------------------------------------------- #
# Phase A — power-quality fields
# --------------------------------------------------------------------------- #


def test_sentry4_power_quality_fields(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    # order: uptime,status,active_power,line_current,util,voltage,firmware,
    #        energy,frequency,power_factor,apparent_power,out_of_balance
    vb = [
        ("u", 360000),
        ("s", 0),
        ("p", 1500),
        ("c", 1234),
        ("ut", 456),
        ("v", 2300),
        ("fw", "8.0.3"),
        ("e", 12345),
        ("f", 500),
        ("pf", 95),
        ("va", 1600),
        ("bal", 37),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["energy_kwh"] == 1234.5  # tenth-kWh
    assert s["frequency_hz"] == 50.0  # tenth-Hz
    assert s["power_factor"] == 0.95  # hundredths -> ratio
    assert s["apparent_power_va"] == 1600.0
    assert s["out_of_balance_pct"] == 3.7  # tenth-percent


def test_sentry3_power_quality_subset(monkeypatch):
    h = mod.Sentry3Pdu("192.0.2.2")
    # order: uptime,status,load,capacity,voltage,power,firmware,energy,power_factor
    vb = [
        ("u", 120000),
        ("s", 1),
        ("load", 850),
        ("cap", 16),
        ("v", 2300),
        ("p", 1900),
        ("fw", "7.0c"),
        ("e", 9999),
        ("pf", 88),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["energy_kwh"] == 999.9
    assert s["power_factor"] == 0.88
    # Sentry3 exposes neither of these
    assert "frequency_hz" not in s
    assert "apparent_power_va" not in s
    assert "out_of_balance_pct" not in s


def test_sentry4_power_quality_fallback_mode(no_mib, monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    vb = [
        ("u", 360000),
        ("s", 0),
        ("p", 1500),
        ("c", 1234),
        ("ut", 456),
        ("v", 2300),
        ("fw", "8.0.3"),
        ("e", 12345),
        ("f", 500),
        ("pf", 95),
        ("va", 1600),
        ("bal", 37),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    # hardcoded fallback scales match the MIB
    assert s["energy_kwh"] == 1234.5
    assert s["frequency_hz"] == 50.0
    assert s["power_factor"] == 0.95


# --------------------------------------------------------------------------- #
# Phase B — device-evaluated status enums + severity
# --------------------------------------------------------------------------- #


def test_sentry4_quality_status_severity(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    # ...status block at the tail: active_power=highAlarm(17), pf=highWarning(16), balance/load normal
    vb = [
        ("u", 360000),
        ("s", 0),
        ("p", 1500),
        ("c", 1234),
        ("ut", 456),
        ("v", 2300),
        ("fw", "8"),
        ("e", 12345),
        ("f", 500),
        ("pf", 95),
        ("va", 1600),
        ("bal", 37),
        ("aps", 17),
        ("pfs", 16),
        ("bals", 0),
        ("ls", 0),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["active_power_status"] == 17
    assert s["active_power_status_text"] == "highAlarm"
    assert s["power_factor_status_text"] == "highWarning"
    assert s["pq_severity"] == 2  # max(critical, warning)


def test_sentry4_quality_all_normal_severity_zero(monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    vb = [
        ("u", 360000),
        ("s", 0),
        ("p", 1500),
        ("c", 1234),
        ("ut", 456),
        ("v", 2300),
        ("fw", "8"),
        ("e", 12345),
        ("f", 500),
        ("pf", 95),
        ("va", 1600),
        ("bal", 37),
        ("aps", 0),
        ("pfs", 0),
        ("bals", 0),
        ("ls", 0),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["pq_severity"] == 0
    assert s["active_power_status_text"] == "normal"


def test_sentry4_noncomm_status_not_pq_alarm(monkeypatch):
    # noComm(10) is a sensor/comms state, not a power-quality alarm -> severity 0
    h = mod.Sentry4Pdu("192.0.2.1")
    vb = [
        ("u", 360000),
        ("s", 0),
        ("p", 1500),
        ("c", 1234),
        ("ut", 456),
        ("v", 2300),
        ("fw", "8"),
        ("e", 12345),
        ("f", 500),
        ("pf", 95),
        ("va", 1600),
        ("bal", 37),
        ("aps", 10),
        ("pfs", 0),
        ("bals", 0),
        ("ls", 0),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["active_power_status_text"] == "noComm"
    assert s["pq_severity"] == 0


def test_sentry3_load_status_severity(monkeypatch):
    h = mod.Sentry3Pdu("192.0.2.2")
    # load_status=overLoad(5) -> critical; uses Sentry3's own enum
    vb = [
        ("u", 120000),
        ("s", 1),
        ("load", 850),
        ("cap", 16),
        ("v", 2300),
        ("p", 1900),
        ("fw", "7"),
        ("e", 9999),
        ("pf", 88),
        ("ls", 5),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    assert s["load_status"] == 5
    assert s["load_status_text"] == "overLoad"
    assert s["pq_severity"] == 2


def test_quality_status_fallback_mode(no_mib, monkeypatch):
    h = mod.Sentry4Pdu("192.0.2.1")
    vb = [
        ("u", 360000),
        ("s", 0),
        ("p", 1500),
        ("c", 1234),
        ("ut", 456),
        ("v", 2300),
        ("fw", "8"),
        ("e", 12345),
        ("f", 500),
        ("pf", 95),
        ("va", 1600),
        ("bal", 37),
        ("aps", 20),
        ("pfs", 0),
        ("bals", 0),
        ("ls", 0),
    ]
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok(vb))
    s = h.fetch_stats()
    # fallback enum still decodes overLimit(20) and maps to critical
    assert s["active_power_status_text"] == "overLimit"
    assert s["pq_severity"] == 2
