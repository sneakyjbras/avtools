from __future__ import annotations

from avtools.snmp.mibs import MibResolver, get_resolver


def _sample():
    return MibResolver(
        {
            "Sentry4-MIB": {
                "st4LineCurrent": {
                    "oid": "1.3.6.1.4.1.1718.4.1.4.3.1.3",
                    "scale": 100.0,
                    "unit": "A",
                },
                "st4InputCordStatus": {
                    "oid": "1.3.6.1.4.1.1718.4.1.3.3.1.2",
                    "scale": 1.0,
                    "enum": {"0": "normal", "12": "breakerTripped"},
                },
                "st4SystemFirmwareVersion": {"oid": "1.3.6.1.4.1.1718.4.1.1.1.3", "scale": 1.0},
            }
        }
    )


def test_available():
    r = _sample()
    assert r.available("Sentry4-MIB") is True
    assert r.available("Sentry3-MIB") is False
    assert MibResolver({}).available("Sentry4-MIB") is False


def test_oid_with_indices():
    r = _sample()
    assert r.oid("Sentry4-MIB", "st4LineCurrent", 1, 1, 1) == "1.3.6.1.4.1.1718.4.1.4.3.1.3.1.1.1"
    assert r.oid("Sentry4-MIB", "st4SystemFirmwareVersion", 0) == "1.3.6.1.4.1.1718.4.1.1.1.3.0"
    assert r.oid("Sentry4-MIB", "missing") is None
    assert r.oid("Nope-MIB", "x") is None


def test_scale_and_unit():
    r = _sample()
    assert r.scale("Sentry4-MIB", "st4LineCurrent") == 100.0
    assert r.unit("Sentry4-MIB", "st4LineCurrent") == "A"
    assert r.scale("Sentry4-MIB", "missing") is None


def test_enum_keys_are_ints():
    r = _sample()
    e = r.enum("Sentry4-MIB", "st4InputCordStatus")
    assert e == {0: "normal", 12: "breakerTripped"}
    assert r.enum("Sentry4-MIB", "st4LineCurrent") is None


def test_packaged_metadata_loads():
    """The committed compiled metadata should load and resolve known OIDs."""
    r = get_resolver()
    assert r.available("Sentry4-MIB")
    assert r.available("Sentry3-MIB")
    # spot-check an OID + scale + enum from the real compiled MIBs
    assert r.oid("Sentry4-MIB", "st4LineCurrent", 1, 1, 1) == "1.3.6.1.4.1.1718.4.1.4.3.1.3.1.1.1"
    assert r.scale("Sentry4-MIB", "st4LineCurrent") == 100.0
    assert r.scale("Sentry3-MIB", "infeedVoltage") == 10.0  # the corrected scaling
    assert r.enum("Sentry3-MIB", "infeedStatus")[1] == "on"
