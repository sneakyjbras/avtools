"""Tests for avtools.timeseries.encoder.

Covers:
- to_float best-effort conversion
- encode_all with no device_lookup (backward-compat: only equipmentno label)
- encode_all with device_lookup (enriched labels: building, room, eq_class, model,
  category, hostname)
- Partial device_lookup (missing entry falls back to equipmentno-only)
- Devices with None / empty metadata fields are omitted from labels
- Policy: firmware and power_status never appear in timeseries samples
"""

from __future__ import annotations

from dataclasses import dataclass


from avtools.snmp.client import PingResult, ProbeResult, QueryResult
from avtools.timeseries.encoder import encode_all, encode_ping, encode_probe, to_float
from avtools.timeseries import metrics as m


# ---------------------------------------------------------------------------
# Minimal device stub (only what PingResult / ProbeResult / QueryResult need)
# ---------------------------------------------------------------------------


@dataclass
class D:
    ip: str
    equipment_no: str


def _ping(dev: D, *, up: int = 1, rtt_ms: float | None = 1.0) -> PingResult:
    return PingResult(
        device=dev,  # type: ignore[arg-type]
        ip=dev.ip,
        equipmentno=dev.equipment_no,
        up=up,
        rtt_ms=rtt_ms,
        reason=None,
        attempts=1,
    )


def _probe(dev: D, *, up: int = 1) -> ProbeResult:
    return ProbeResult(
        device=dev,  # type: ignore[arg-type]
        ip=dev.ip,
        equipmentno=dev.equipment_no,
        up=up,
        eqclass="AVD",
        category="AV-PRO",
        sysdescr="X",
    )


def _query(dev: D, stats: dict) -> QueryResult:
    return QueryResult(
        device=dev,  # type: ignore[arg-type]
        ip=dev.ip,
        equipmentno=dev.equipment_no,
        query="projector",
        eqclass="AVD",
        category="AV-PRO",
        stats=stats,
    )


# ---------------------------------------------------------------------------
# to_float
# ---------------------------------------------------------------------------


def test_to_float_int():
    assert to_float(1) == 1.0


def test_to_float_string_numeric():
    assert to_float("12.34") == 12.34


def test_to_float_string_with_noise():
    assert to_float("lamp=55h") == 55.0


def test_to_float_no_digits():
    assert to_float("no-digits") is None


def test_to_float_none():
    assert to_float(None) is None


# ---------------------------------------------------------------------------
# encode_all — backward compatibility (no device_lookup)
# ---------------------------------------------------------------------------


def test_encode_all_no_lookup_only_equipmentno_label():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")
    samples = encode_ping([_ping(dev)], device_lookup=None)
    status = next(s for s in samples if s.name == m.PING_CHECK_STATUS)
    assert status.labels == {"equipmentno": "EQ1"}


def test_encode_all_produces_expected_metric_names():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")
    samples = encode_all(
        ping=[_ping(dev)],
        probe=[_probe(dev)],
        queries=[_query(dev, {"lamp_hours": "123", "uptime_seconds": 10, "firmware": "FW1"})],
    )
    names = {s.name for s in samples}
    assert m.PING_CHECK_STATUS in names
    assert m.PING_CHECK_RTT_MS in names
    assert m.SNMP_PROBE_STATUS in names
    assert m.PROJECTOR_QUERY_LAMP_HOURS in names
    assert m.PROJECTOR_QUERY_UPTIME_SECONDS in names


def test_encode_all_includes_projector_power_and_firmware():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")
    samples = encode_all(
        ping=[_ping(dev)],
        probe=[_probe(dev)],
        queries=[_query(dev, {"lamp_hours": "123", "firmware": "FW1", "power_status": "1"})],
    )
    by_name = {s.name: s for s in samples}
    # Power status is now published numerically.
    assert m.PROJECTOR_QUERY_POWER_STATUS in by_name
    assert by_name[m.PROJECTOR_QUERY_POWER_STATUS].value == 1.0
    # Firmware is published as an info gauge (value=1, firmware label).
    assert m.PROJECTOR_QUERY_FIRMWARE_INFO in by_name
    fw_sample = by_name[m.PROJECTOR_QUERY_FIRMWARE_INFO]
    assert fw_sample.value == 1
    assert fw_sample.labels.get("firmware") == "FW1"


# ---------------------------------------------------------------------------
# encode_all — with device_lookup (enriched labels)
# ---------------------------------------------------------------------------


_FULL_LOOKUP: dict[str, dict[str, str]] = {
    "EQ1": {
        "building": "BUILDING-A",
        "room": "ROOM-101",
        "eq_class": "PROJ",
        "model": "Epson EB-L1505U",
        "category": "AV-PROJ",
        "hostname": "projector-101.cern.ch",
    }
}


def test_encode_ping_with_lookup_adds_device_labels():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")
    samples = encode_ping([_ping(dev)], device_lookup=_FULL_LOOKUP)
    status = next(s for s in samples if s.name == m.PING_CHECK_STATUS)
    assert status.labels["equipmentno"] == "EQ1"
    assert status.labels["building"] == "BUILDING-A"
    assert status.labels["room"] == "ROOM-101"
    assert status.labels["eq_class"] == "PROJ"
    assert status.labels["model"] == "Epson EB-L1505U"
    assert status.labels["category"] == "AV-PROJ"
    assert status.labels["hostname"] == "projector-101.cern.ch"


def test_encode_probe_with_lookup_adds_device_labels():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")
    samples = encode_probe([_probe(dev)], device_lookup=_FULL_LOOKUP)
    assert samples[0].labels["building"] == "BUILDING-A"
    assert samples[0].labels["room"] == "ROOM-101"


def test_encode_all_with_lookup_projector_query_has_device_labels():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")
    samples = encode_all(
        queries=[_query(dev, {"lamp_hours": 500, "uptime_seconds": 3600})],
        device_lookup=_FULL_LOOKUP,
    )
    lamp = next(s for s in samples if s.name == m.PROJECTOR_QUERY_LAMP_HOURS)
    assert lamp.labels["building"] == "BUILDING-A"
    assert lamp.labels["room"] == "ROOM-101"
    assert lamp.value == 500.0


def test_encode_all_lookup_missing_device_falls_back_to_equipmentno_only():
    dev = D(ip="10.0.0.1", equipment_no="EQ-UNKNOWN")
    samples = encode_ping([_ping(dev)], device_lookup=_FULL_LOOKUP)
    status = next(s for s in samples if s.name == m.PING_CHECK_STATUS)
    assert list(status.labels.keys()) == ["equipmentno"]
    assert status.labels["equipmentno"] == "EQ-UNKNOWN"


def test_encode_ping_rtt_shares_same_labels_as_status():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")
    samples = encode_ping([_ping(dev, rtt_ms=42.0)], device_lookup=_FULL_LOOKUP)
    status = next(s for s in samples if s.name == m.PING_CHECK_STATUS)
    rtt = next(s for s in samples if s.name == m.PING_CHECK_RTT_MS)
    assert status.labels == rtt.labels


def test_encode_ping_no_rtt_when_offline():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")
    samples = encode_ping([_ping(dev, up=0, rtt_ms=None)])
    names = [s.name for s in samples]
    assert m.PING_CHECK_STATUS in names
    assert m.PING_CHECK_RTT_MS not in names


# ---------------------------------------------------------------------------
# Partial device metadata: None / empty values are omitted from labels
# ---------------------------------------------------------------------------


def test_encode_ping_with_partial_metadata_omits_none_fields():
    dev = D(ip="10.0.0.1", equipment_no="EQ2")
    lookup: dict[str, dict[str, str]] = {
        "EQ2": {"building": "BUILDING-B"}  # room, eq_class, etc. absent
    }
    samples = encode_ping([_ping(dev)], device_lookup=lookup)
    status = next(s for s in samples if s.name == m.PING_CHECK_STATUS)
    assert status.labels["building"] == "BUILDING-B"
    assert "room" not in status.labels
    assert "eq_class" not in status.labels
    assert "model" not in status.labels


def test_encode_all_skips_result_with_empty_equipmentno():
    dev = D(ip="10.0.0.1", equipment_no="")
    samples = encode_all(ping=[_ping(dev)], probe=[_probe(dev)])
    assert samples == []
