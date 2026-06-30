"""Tests for the OpenSearch logging contract producer (avtools.logsink) and the
SNMP failure-reason classifier."""

from __future__ import annotations

import json

import pytest

from avtools.logsink import bind_envelope, configure_logging, event_logger
from avtools.snmp.handlers.abstract_device_handler import classify_snmp_failure


def _read(path):
    return [json.loads(line) for line in open(path) if line.strip()]


def test_json_sink_emits_contract_envelope(tmp_path):
    """A logged event lands in the JSON file as a parseable line carrying the
    full envelope and the contract fields."""
    import structlog

    log_file = tmp_path / "avtools.jsonl"
    configure_logging(logs=False, log_file=str(log_file))
    bind_envelope("avtools", "prod", "itdcim/av")

    structlog.get_logger("avtools.core").info(
        "cycle_summary", targeted=1358, polled=1351, failed=7, duration_s=99.4
    )

    events = _read(log_file)
    cs = next(e for e in events if e["event"] == "cycle_summary")
    for field in (
        "timestamp",
        "level",
        "service",
        "host",
        "submitter_environment",
        "hostgroup",
        "cycle_id",
    ):
        assert cs.get(field), f"envelope field {field!r} missing"
    assert cs["service"] == "avtools"
    assert cs["submitter_environment"] == "prod"
    assert cs["targeted"] == 1358 and cs["polled"] == 1351 and cs["failed"] == 7


def test_per_device_failure_is_file_only_and_correlates(tmp_path, capsys):
    """snmp_probe_failure goes to the JSON file (OpenSearch) but NOT the console,
    and shares cycle_id with the cycle's other events."""
    import structlog

    log_file = tmp_path / "avtools.jsonl"
    configure_logging(logs=False, log_file=str(log_file))
    bind_envelope("avtools", "prod", "itdcim/av")

    structlog.get_logger("avtools.core").info(
        "cycle_summary", targeted=1, polled=0, failed=1, duration_s=1.0
    )
    event_logger().warning("snmp_probe_failure", equipmentno="EQ1", reason="timeout", ip="10.0.0.1")

    events = _read(log_file)
    pf = next(e for e in events if e["event"] == "snmp_probe_failure")
    cs = next(e for e in events if e["event"] == "cycle_summary")

    assert pf["equipmentno"] == "EQ1" and pf["reason"] == "timeout" and pf["ip"] == "10.0.0.1"
    assert pf["cycle_id"] == cs["cycle_id"], "cycle_id must correlate within a run"

    # file-only: the per-device event must not appear on the console/journal
    err = capsys.readouterr().err
    assert "snmp_probe_failure" not in err
    assert "cycle_summary" in err  # the normal event does reach the console


@pytest.mark.parametrize(
    "indication,status,expected",
    [
        ("No SNMP response received before timeout", 0, "timeout"),
        ("RequestTimedOut", 0, "timeout"),
        ("No route to host", 0, "host_unreachable"),
        ("Network is unreachable", 0, "network_unreachable"),
        ("unknownUserName", 0, "auth_failure"),
        ("wrongDigest", 0, "auth_failure"),
        (None, 5, "mib_error"),
        ("some novel pysnmp message", 0, "unknown"),
        (None, 0, "unknown"),
    ],
)
def test_classify_snmp_failure(indication, status, expected):
    assert classify_snmp_failure(indication, status) == expected
