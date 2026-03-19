from __future__ import annotations

from dataclasses import dataclass

from avtools.snmp.client import PingResult, ProbeResult, QueryResult
from avtools.timeseries.encoder import encode_all, to_float
from avtools.timeseries import metrics as m


@dataclass
class D:
    ip: str
    equipment_no: str


def test_to_float_best_effort():
    assert to_float(1) == 1.0
    assert to_float("12.34") == 12.34
    assert to_float("lamp=55h") == 55.0
    assert to_float("no-digits") is None


def test_encode_all_encodes_numeric_only_and_skips_firmware_and_power():
    dev = D(ip="10.0.0.1", equipment_no="EQ1")

    ping = [
        PingResult(
            device=dev,  # type: ignore[arg-type]
            ip=dev.ip,
            equipmentno=dev.equipment_no,
            up=1,
            rtt_ms=1.0,
            reason=None,
            attempts=1,
        )
    ]
    probe = [
        ProbeResult(
            device=dev,  # type: ignore[arg-type]
            ip=dev.ip,
            equipmentno=dev.equipment_no,
            up=1,
            eqclass="AVD",
            category="AV-PRO",
            sysdescr="X",
        )
    ]
    queries = [
        QueryResult(
            device=dev,  # type: ignore[arg-type]
            ip=dev.ip,
            equipmentno=dev.equipment_no,
            query="projector",
            eqclass="AVD",
            category="AV-PRO",
            stats={
                "lamp_hours": "123",
                "uptime_seconds": 10,
                "firmware": "FW1",
                "power_status": "1",
            },
        )
    ]

    samples = encode_all(ping=ping, probe=probe, queries=queries)
    names = [s.name for s in samples]

    assert m.PING_CHECK_STATUS in names
    assert m.PING_CHECK_RTT_MS in names
    assert m.SNMP_PROBE_STATUS in names
    assert m.PROJECTOR_QUERY_LAMP_HOURS in names
    assert m.PROJECTOR_QUERY_UPTIME_SECONDS in names

    # Policy: no firmware/power status in timeseries encoder.
    assert m.PROJECTOR_QUERY_FIRMWARE_INFO not in names
    assert m.PROJECTOR_QUERY_POWER_STATUS not in names
