from __future__ import annotations

from dataclasses import dataclass

from avtools.postgres.monitoring.codec import device_sysdescr_records_from_probe_results
from avtools.snmp.client import ProbeResult


@dataclass
class DummyDevice:
    ip: str
    equipment_no: str
    eq_class: str | None = None
    category: str | None = None


def test_device_sysdescr_records_only_from_up_results_with_text():
    dev_ok = DummyDevice(ip="10.0.0.1", equipment_no="EQ1", eq_class="AVD", category="AV-PRO")
    dev_down = DummyDevice(ip="10.0.0.2", equipment_no="EQ2")

    results = [
        ProbeResult(
            device=dev_ok,  # type: ignore[arg-type]
            ip=dev_ok.ip,
            equipmentno=dev_ok.equipment_no,
            up=1,
            eqclass=dev_ok.eq_class,
            category=dev_ok.category,
            sysdescr=" Linux ",
        ),
        ProbeResult(
            device=dev_down,  # type: ignore[arg-type]
            ip=dev_down.ip,
            equipmentno=dev_down.equipment_no,
            up=0,
            eqclass=None,
            category=None,
            sysdescr="ignored",
        ),
    ]

    rows = device_sysdescr_records_from_probe_results(results)
    assert len(rows) == 1
    assert rows[0].equipment_no == "EQ1"
    assert rows[0].sysdescr == "Linux"
