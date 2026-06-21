from __future__ import annotations

from avtools.snmp.handlers import parsing as p


def test_as_int_variants():
    assert p.as_int(42) == 42
    assert p.as_int("42") == 42
    assert p.as_int("12.9") == 129 or p.as_int("12.9") == 12  # digits-only fallback
    assert p.as_int("-7") == -7
    assert p.as_int("none") is None
    assert p.as_int(None) is None


def test_as_float_variants():
    assert p.as_float(1.5) == 1.5
    assert p.as_float("1.5") == 1.5
    assert p.as_float("230") == 230.0
    assert p.as_float("-3.2") == -3.2
    assert p.as_float("abc") is None
    assert p.as_float(None) is None


def test_values_from_varbinds():
    vb = [("oid1", 10), ("oid2", "x")]
    assert p.values_from_varbinds(vb) == [10, "x"]
    # tolerates malformed entries
    assert p.values_from_varbinds([("only-one",)]) == [None]


def test_ticks_to_seconds():
    assert p.ticks_to_seconds(360000) == 3600.0
    assert p.ticks_to_seconds(0) == 0.0
    assert p.ticks_to_seconds(None) is None


def test_scaled():
    assert p.scaled(1234, 100.0) == 12.34
    assert p.scaled(2300, 10.0) == 230.0
    assert p.scaled("abc", 100.0) is None
    # divisor of zero returns the parsed value unscaled
    assert p.scaled(5, 0) == 5.0


def test_non_empty_str_filters_sentinels():
    assert p.non_empty_str("8.0.3") == "8.0.3"
    assert p.non_empty_str("  ") is None
    assert p.non_empty_str(None) is None
    assert p.non_empty_str("No Such Instance currently exists at this OID") is None
    assert p.non_empty_str("No Such Object currently exists at this OID") is None
    assert p.non_empty_str("No more variables left in this MIB View") is None
