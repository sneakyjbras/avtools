from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pydantic import BaseModel

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools


class DummyEAM(BaseModel):
    equipment_no: str
    serial_number: str
    eq_class: str
    manufacturer: str


class DummyLanDBDevice(BaseModel):
    equipment_no: str
    serial_number: str
    ip: str | None
    manufacturer: str


def make_avtools_for_tests() -> AVTools:
    # Bypass __init__ so we don't touch PostgresClient at all
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    return av


def test_get_landb_devices_returns_empty_when_no_eam_records():
    """If there are no EAM records, the method should return an empty list."""
    av = make_avtools_for_tests()
    session = object()

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=[],
            session=session,
            max_workers=4,
        )
    )

    assert result == []


def test_get_landb_devices_builds_and_filters_devices(monkeypatch):
    """
    _get_landb_devices should:
    - call LanDBClient.build_device_with_ip for each EAM record
    - filter out devices with missing serial_number or ip
    - ignore errors from the client
    """
    av = make_avtools_for_tests()
    session = object()

    eam_records = [
        # valid → should be kept
        DummyEAM(
            equipment_no="DEV-34",
            serial_number="SN-34",
            eq_class="EQ-34",
            manufacturer="MFG-34",
        ),
        # client returns None → should be skipped
        DummyEAM(
            equipment_no="DEV-38",
            serial_number="SN-38",
            eq_class="EQ-38",
            manufacturer="MFG-38",
        ),
        # missing serial_number in result → skipped
        DummyEAM(
            equipment_no="DEV-39",
            serial_number="SN-39",
            eq_class="EQ-39",
            manufacturer="MFG-39",
        ),
        # ip is None in result → skipped
        DummyEAM(
            equipment_no="DEV-404",
            serial_number="SN-404",
            eq_class="EQ-404",
            manufacturer="MFG-404",
        ),
        # client raises error → skipped
        DummyEAM(
            equipment_no="DEV-1911",
            serial_number="SN-1911",
            eq_class="EQ-1911",
            manufacturer="MFG-1911",
        ),
        # another valid → should be kept
        DummyEAM(
            equipment_no="DEV-2137",
            serial_number="SN-2137",
            eq_class="EQ-2137",
            manufacturer="MFG-2137",
        ),
    ]

    behavior_map: dict[str, str] = {
        "DEV-34": "ok",
        "DEV-38": "none",
        "DEV-39": "no_serial",
        "DEV-404": "no_ip",
        "DEV-1911": "error",
        "DEV-2137": "ok",
    }

    class DummyLanDBClient:
        def __init__(self, session: Any) -> None:
            self.session = session

        def build_device_with_ip(
            self,
            equipment_no: str,
            serial_number: str,
            eq_class: str,
            manufacturer: str,
        ) -> DummyLanDBDevice | None:
            mode = behavior_map[equipment_no]
            if mode == "ok":
                return DummyLanDBDevice(
                    equipment_no=equipment_no,
                    serial_number=serial_number,
                    ip=f"10.0.0.{equipment_no.split('-')[-1]}",
                    manufacturer=manufacturer,
                )
            if mode == "none":
                return None
            if mode == "no_serial":
                return DummyLanDBDevice(
                    equipment_no=equipment_no,
                    serial_number="",
                    ip="10.0.0.39",
                    manufacturer=manufacturer,
                )
            if mode == "no_ip":
                return DummyLanDBDevice(
                    equipment_no=equipment_no,
                    serial_number=serial_number,
                    ip=None,
                    manufacturer=manufacturer,
                )
            if mode == "error":
                raise RuntimeError("dummy LanDB error")
            raise AssertionError(f"Unexpected mode {mode!r}")

    # Patch the LanDBClient used inside avtools.core.av_tools
    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=list(eam_records),
            session=session,
            max_workers=3,
        )
    )

    # Only DEV-34 and DEV-2137 should survive all filters
    equipment_nos = [d.equipment_no for d in result]
    assert equipment_nos == ["DEV-34", "DEV-2137"]
    # sanity check: their ips are set
    assert all(d.ip is not None for d in result)


def test_get_landb_devices_respects_total_count_and_logs(monkeypatch):
    """
    Smoke test with a small number of devices to ensure chunking doesn't break anything.
    We don't assert logs here, just that all valid devices are returned.
    """
    av = make_avtools_for_tests()
    session = object()

    eam_records = [
        DummyEAM(
            equipment_no="DEV-14",
            serial_number="SN-14",
            eq_class="EQ-14",
            manufacturer="MFG-14",
        ),
        DummyEAM(
            equipment_no="DEV-1978",
            serial_number="SN-1978",
            eq_class="EQ-1978",
            manufacturer="MFG-1978",
        ),
    ]

    class DummyLanDBClient:
        def __init__(self, session: Any) -> None:
            self.session = session

        def build_device_with_ip(
            self,
            equipment_no: str,
            serial_number: str,
            eq_class: str,
            manufacturer: str,
        ) -> DummyLanDBDevice:
            return DummyLanDBDevice(
                equipment_no=equipment_no,
                serial_number=serial_number,
                ip="10.0.0.2025",
                manufacturer=manufacturer,
            )

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=list(eam_records),
            session=session,
            max_workers=4,
        )
    )

    equipment_nos = [d.equipment_no for d in result]
    assert equipment_nos == ["DEV-14", "DEV-1978"]
    assert all(d.ip == "10.0.0.2025" for d in result)


def test_get_landb_devices_all_invalid_returns_empty(monkeypatch):
    """
    If every LanDBClient call returns an invalid device (None, no serial, no ip),
    the final result should be an empty list.
    """
    av = make_avtools_for_tests()
    session = object()

    eam_records = [
        DummyEAM(
            equipment_no="DEV-34",
            serial_number="SN-34",
            eq_class="EQ-34",
            manufacturer="MFG-34",
        ),
        DummyEAM(
            equipment_no="DEV-38",
            serial_number="SN-38",
            eq_class="EQ-38",
            manufacturer="MFG-38",
        ),
        DummyEAM(
            equipment_no="DEV-39",
            serial_number="SN-39",
            eq_class="EQ-39",
            manufacturer="MFG-39",
        ),
    ]

    behavior_map: dict[str, str] = {
        "DEV-34": "none",
        "DEV-38": "no_serial",
        "DEV-39": "no_ip",
    }

    class DummyLanDBClient:
        def __init__(self, session: Any) -> None:
            self.session = session

        def build_device_with_ip(
            self,
            equipment_no: str,
            serial_number: str,
            eq_class: str,
            manufacturer: str,
        ) -> DummyLanDBDevice | None:
            mode = behavior_map[equipment_no]
            if mode == "none":
                return None
            if mode == "no_serial":
                return DummyLanDBDevice(
                    equipment_no=equipment_no,
                    serial_number="",
                    ip="10.0.0.39",
                    manufacturer=manufacturer,
                )
            if mode == "no_ip":
                return DummyLanDBDevice(
                    equipment_no=equipment_no,
                    serial_number=serial_number,
                    ip=None,
                    manufacturer=manufacturer,
                )
            raise AssertionError(f"Unexpected mode {mode!r}")

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=list(eam_records),
            session=session,
            max_workers=2,
        )
    )

    assert result == []


def test_get_landb_devices_with_more_workers_than_records(monkeypatch):
    """
    If max_workers is greater than the number of EAM records, the method should still
    process each record exactly once and return all valid devices.
    """
    av = make_avtools_for_tests()
    session = object()

    eam_records = [
        DummyEAM(
            equipment_no="DEV-34",
            serial_number="SN-34",
            eq_class="EQ-34",
            manufacturer="MFG-34",
        ),
        DummyEAM(
            equipment_no="DEV-38",
            serial_number="SN-38",
            eq_class="EQ-38",
            manufacturer="MFG-38",
        ),
        DummyEAM(
            equipment_no="DEV-39",
            serial_number="SN-39",
            eq_class="EQ-39",
            manufacturer="MFG-39",
        ),
    ]

    calls: list[str] = []

    class DummyLanDBClient:
        def __init__(self, session: Any) -> None:
            self.session = session

        def build_device_with_ip(
            self,
            equipment_no: str,
            serial_number: str,
            eq_class: str,
            manufacturer: str,
        ) -> DummyLanDBDevice:
            calls.append(equipment_no)
            return DummyLanDBDevice(
                equipment_no=equipment_no,
                serial_number=serial_number,
                ip="10.0.0.2025",
                manufacturer=manufacturer,
            )

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=list(eam_records),
            session=session,
            max_workers=10,  # more workers than records
        )
    )

    equipment_nos = [d.equipment_no for d in result]
    assert equipment_nos == ["DEV-34", "DEV-38", "DEV-39"]
    # Every record must have been processed exactly once
    assert calls == ["DEV-34", "DEV-38", "DEV-39"]
