"""Encoder coverage for the PDU / codec / matrix SNMP query families."""

from __future__ import annotations

from dataclasses import dataclass

from avtools.snmp.client import QueryResult
from avtools.timeseries import metrics as m
from avtools.timeseries.encoder import encode_queries


@dataclass
class D:
    ip: str
    equipment_no: str


def _query(dev: D, query: str, stats: dict, *, eqclass: str) -> QueryResult:
    return QueryResult(
        device=dev,  # type: ignore[arg-type]
        ip=dev.ip,
        equipmentno=dev.equipment_no,
        query=query,
        eqclass=eqclass,
        category=None,
        stats=stats,
    )


def test_encode_pdu_emits_all_numeric_and_firmware_info():
    dev = D(ip="10.0.0.1", equipment_no="PDU1")
    stats = {
        "input_status": 0,
        "healthy": 1,
        "status_text": "normal",
        "active_power_watts": 1500.0,
        "line_current_amps": 12.34,
        "current_utilized_pct": 45.6,
        "voltage_volts": 230.0,
        "uptime_seconds": 3600.0,
        "firmware": "8.0.3",
    }
    samples = encode_queries([_query(dev, "pdu", stats, eqclass="AVS")])
    by_name = {s.name: s for s in samples}

    assert by_name[m.PDU_QUERY_INPUT_STATUS].value == 0.0
    assert by_name[m.PDU_QUERY_HEALTHY].value == 1.0
    assert by_name[m.PDU_QUERY_ACTIVE_POWER_WATTS].value == 1500.0
    assert by_name[m.PDU_QUERY_LINE_CURRENT_AMPS].value == 12.34
    assert by_name[m.PDU_QUERY_CURRENT_UTILIZED_PCT].value == 45.6
    assert by_name[m.PDU_QUERY_VOLTAGE_VOLTS].value == 230.0
    assert by_name[m.PDU_QUERY_UPTIME_SECONDS].value == 3600.0
    fw = by_name[m.PDU_QUERY_FIRMWARE_INFO]
    assert fw.value == 1
    assert fw.labels.get("firmware") == "8.0.3"
    # MIB-derived status label as an info metric
    st = by_name[m.PDU_QUERY_STATUS_INFO]
    assert st.value == 1
    assert st.labels.get("status") == "normal"
    # mandatory label always present
    assert by_name[m.PDU_QUERY_HEALTHY].labels.get("equipmentno") == "PDU1"


def test_encode_pdu_omits_missing_fields():
    dev = D(ip="10.0.0.1", equipment_no="PDU2")
    samples = encode_queries([_query(dev, "pdu", {"healthy": 0}, eqclass="AVS")])
    names = {s.name for s in samples}
    assert names == {m.PDU_QUERY_HEALTHY}


def test_encode_codec_uptime():
    dev = D(ip="10.0.0.2", equipment_no="VC1")
    samples = encode_queries([_query(dev, "codec", {"uptime_seconds": 590101.6}, eqclass="AVV")])
    by_name = {s.name: s for s in samples}
    assert by_name[m.CODEC_QUERY_UPTIME_SECONDS].value == 590101.6


def test_encode_matrix_uptime():
    dev = D(ip="10.0.0.3", equipment_no="MX1")
    samples = encode_queries([_query(dev, "matrix", {"uptime_seconds": 1.0}, eqclass="AVVS")])
    by_name = {s.name: s for s in samples}
    assert by_name[m.MATRIX_QUERY_UPTIME_SECONDS].value == 1.0


def test_encode_unknown_query_family_is_ignored():
    dev = D(ip="10.0.0.4", equipment_no="X1")
    samples = encode_queries([_query(dev, "sensor", {"uptime_seconds": 5}, eqclass="ZZ")])
    assert samples == []


def test_encode_pdu_power_quality_metrics():
    dev = D(ip="10.0.0.1", equipment_no="PDU9")
    stats = {
        "energy_kwh": 1234.5,
        "frequency_hz": 50.0,
        "power_factor": 0.95,
        "apparent_power_va": 1600.0,
        "out_of_balance_pct": 3.7,
    }
    samples = encode_queries([_query(dev, "pdu", stats, eqclass="AVS")])
    by_name = {s.name: s for s in samples}
    assert by_name[m.PDU_QUERY_ENERGY_KWH].value == 1234.5
    assert by_name[m.PDU_QUERY_FREQUENCY_HZ].value == 50.0
    assert by_name[m.PDU_QUERY_POWER_FACTOR].value == 0.95
    assert by_name[m.PDU_QUERY_APPARENT_POWER_VA].value == 1600.0
    assert by_name[m.PDU_QUERY_OUT_OF_BALANCE_PCT].value == 3.7


def test_encode_pdu_status_enums_and_severity():
    dev = D(ip="10.0.0.1", equipment_no="PDU7")
    stats = {
        "active_power_status": 17,
        "active_power_status_text": "highAlarm",
        "power_factor_status": 0,
        "power_factor_status_text": "normal",
        "load_status": 4,
        "load_status_text": "loadHigh",
        "pq_severity": 2,
    }
    samples = encode_queries([_query(dev, "pdu", stats, eqclass="AVS")])
    by_name = {s.name: s for s in samples}
    assert by_name[m.PDU_QUERY_ACTIVE_POWER_STATUS].value == 17
    assert by_name[m.PDU_QUERY_PQ_SEVERITY].value == 2
    assert by_name[m.PDU_QUERY_ACTIVE_POWER_STATUS_INFO].labels.get("status") == "highAlarm"
    assert by_name[m.PDU_QUERY_LOAD_STATUS_INFO].labels.get("status") == "loadHigh"
    # normal still emits its info label
    assert by_name[m.PDU_QUERY_POWER_FACTOR_STATUS_INFO].labels.get("status") == "normal"


def test_encode_pdu_environment_sensors():
    dev = D(ip="10.0.0.1", equipment_no="PDU5")
    stats = {
        "environment": {
            "temperature": [
                {"index": 1, "celsius": 25.1, "name": "Inlet"},
                {"index": 2, "celsius": 26.8, "name": "Outlet"},
            ],
            "humidity": [{"index": 1, "percent": 42.0, "name": "RackHumid"}],
        }
    }
    by = {}
    for s in encode_queries([_query(dev, "pdu", stats, eqclass="AVS")]):
        by.setdefault(s.name, []).append(s)
    temps = {s.labels["sensorindex"]: s.value for s in by[m.PDU_QUERY_TEMPERATURE_C]}
    assert temps == {"1": 25.1, "2": 26.8}
    hums = {s.labels["sensorindex"]: s.value for s in by[m.PDU_QUERY_HUMIDITY_PCT]}
    assert hums == {"1": 42.0}
    # info metric carries sensor name + kind; numeric series carry neither
    kinds = {
        (s.labels["sensorindex"], s.labels["kind"]): s.labels["sensor"]
        for s in by[m.PDU_QUERY_SENSOR_INFO]
    }
    assert kinds[("1", "temperature")] == "Inlet"
    assert kinds[("1", "humidity")] == "RackHumid"
    assert "sensor" not in by[m.PDU_QUERY_TEMPERATURE_C][0].labels


def test_encode_pdu_outlets():
    dev = D(ip="10.0.0.1", equipment_no="PDU3")
    stats = {
        "outlets": [
            {
                "index": 1,
                "state": 1,
                "current": 2.5,
                "power": 575.0,
                "energy": 120000.0,
                "name": "Server-A",
            },
            {"index": 2, "state": 0, "current": 0.0, "power": 0.0, "energy": 5.0, "name": "Spare"},
        ]
    }
    by = {}
    for s in encode_queries([_query(dev, "pdu", stats, eqclass="AVS")]):
        by.setdefault(s.name, []).append(s)
    states = {s.labels["outlet"]: s.value for s in by[m.PDU_QUERY_OUTLET_STATE]}
    assert states == {"1": 1, "2": 0}
    currents = {s.labels["outlet"]: s.value for s in by[m.PDU_QUERY_OUTLET_CURRENT_AMPS]}
    assert currents == {"1": 2.5, "2": 0.0}
    names = {s.labels["outlet"]: s.labels["name"] for s in by[m.PDU_QUERY_OUTLET_INFO]}
    assert names == {"1": "Server-A", "2": "Spare"}
    # name rides on info only, not the numeric series
    assert "name" not in by[m.PDU_QUERY_OUTLET_POWER_WATTS][0].labels
