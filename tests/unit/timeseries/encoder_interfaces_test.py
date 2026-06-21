from __future__ import annotations

from dataclasses import dataclass

from avtools.snmp.client import InterfaceResult
from avtools.timeseries import metrics as m
from avtools.timeseries.encoder import encode_all, encode_interfaces


@dataclass
class _Dev:
    ip: str
    equipment_no: str


def _result():
    return InterfaceResult(
        device=_Dev("10.0.0.1", "CODEC1"),
        ip="10.0.0.1",
        equipmentno="CODEC1",
        interfaces=[
            {
                "ifindex": 1,
                "oper_status": 1,
                "ifdescr": "eth0",
                "in_octets": 123456,
                "out_octets": 654321,
                "in_errors": 0,
                "out_errors": 0,
            },
            {"ifindex": 3, "oper_status": 2, "ifdescr": "eth1", "in_errors": 5, "out_errors": 2},
        ],
    )


def test_encode_interfaces_per_interface_samples():
    by = {}
    for s in encode_interfaces([_result()]):
        by.setdefault(s.name, []).append(s)
    opers = {s.labels["ifindex"]: s.value for s in by[m.DEVICE_IF_OPER_STATUS]}
    assert opers == {"1": 1, "3": 2}
    # info metric carries ifdescr; numeric series do not
    infos = {s.labels["ifindex"]: s.labels["ifdescr"] for s in by[m.DEVICE_IF_INFO]}
    assert infos == {"1": "eth0", "3": "eth1"}
    assert "ifdescr" not in by[m.DEVICE_IF_OPER_STATUS][0].labels
    assert by[m.DEVICE_IF_OPER_STATUS][0].labels["equipmentno"] == "CODEC1"


def test_encode_interfaces_counters_optional_per_interface():
    by = {}
    for s in encode_interfaces([_result()]):
        by.setdefault(s.name, []).append(s)
    # idx 1 has octets, idx 3 does not
    octets = {s.labels["ifindex"]: s.value for s in by[m.DEVICE_IF_IN_OCTETS]}
    assert octets == {"1": 123456}
    errs = {s.labels["ifindex"]: s.value for s in by[m.DEVICE_IF_IN_ERRORS]}
    assert errs == {"1": 0, "3": 5}


def test_encode_all_includes_interfaces():
    samples = encode_all(interfaces=[_result()])
    assert any(s.name == m.DEVICE_IF_OPER_STATUS for s in samples)


def test_encode_interfaces_skips_missing_equipmentno():
    r = InterfaceResult(
        device=_Dev("1", ""), ip="1", equipmentno="", interfaces=[{"ifindex": 1, "oper_status": 1}]
    )
    assert encode_interfaces([r]) == []
