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


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.errors: list[str] = []
        self.exceptions: list[str] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.infos.append(str(msg))

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.errors.append(str(msg))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exceptions.append(str(msg))


def make_avtools_for_tests() -> AVTools:
    # Bypass __init__ so we don't touch PostgresClient at all
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    return av


def test_get_landb_devices_returns_empty_when_no_eam_records():
    """
    If there are no EAM records, _get_landb_devices should log and return [].
    """
    av = make_avtools_for_tests()
    av.logger = DummyLogger()
    session = object()

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=[],
            session=session,
            max_workers=4,
        )
    )

    assert result == []
    # We logged a message about having no EAM records
    assert any(
        "No EAM records provided" in msg for msg in av.logger.infos  # type: ignore[attr-defined]
    )


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


def test_get_landb_devices_respects_total_count_and_logs(monkeypatch):
    """
    Smoke test with a small number of devices to ensure chunking doesn't break anything
    and that summary logging is done.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]
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

    # Logging: one "Fetching ..." and one "Fetched X of Y ..."
    assert any("Fetching 2 LanDB devices with 2 tasks." in msg for msg in logger.infos)
    assert any("Fetched 2 of 2 LanDB devices." in msg for msg in logger.infos)


def test_get_landb_devices_with_more_workers_than_records(monkeypatch):
    """
    If max_workers > number of EAM records, method should still process each
    record exactly once and return all valid devices (order not guaranteed).
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

    equipment_nos = sorted(d.equipment_no for d in result)
    assert equipment_nos == ["DEV-34", "DEV-38", "DEV-39"]

    # Every record must have been processed exactly once (order may vary)
    assert sorted(calls) == ["DEV-34", "DEV-38", "DEV-39"]
    assert len(calls) == len(eam_records)


def test_get_landb_devices_does_not_mutate_eam_records(monkeypatch):
    """
    _get_landb_devices must not mutate the input EAM records.
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
    ]

    # Take a deep-ish snapshot via model_dump()
    before = [rec.model_dump() for rec in eam_records]

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
                ip="10.0.0.1",
                manufacturer=manufacturer,
            )

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    # Pass the original list (not a copy) to detect mutations
    _ = asyncio.run(
        av._get_landb_devices(
            eam_records=eam_records,
            session=session,
            max_workers=2,
        )
    )

    after = [rec.model_dump() for rec in eam_records]
    assert after == before


def test_get_landb_devices_creates_one_lanbd_client_per_task(monkeypatch):
    """
    For total N and max_workers W, _get_landb_devices should create exactly
    num_tasks = min(N, W) LanDBClient instances (one per slice/task).
    """
    av = make_avtools_for_tests()
    session = object()

    # 5 records, max_workers=2 → num_tasks = 2
    eam_records = [
        DummyEAM(
            equipment_no=f"DEV-{i}",
            serial_number=f"SN-{i}",
            eq_class=f"EQ-{i}",
            manufacturer=f"MFG-{i}",
        )
        for i in range(5)
    ]

    class DummyLanDBClient:
        instances: list[DummyLanDBClient] = []

        def __init__(self, session: Any) -> None:
            self.session = session
            DummyLanDBClient.instances.append(self)

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
                ip="10.0.0.42",
                manufacturer=manufacturer,
            )

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=list(eam_records),
            session=session,
            max_workers=2,
        )
    )

    # All devices returned
    assert sorted(d.equipment_no for d in result) == sorted(
        r.equipment_no for r in eam_records
    )

    # Exactly min(total, max_workers) helpers instantiated
    assert len(DummyLanDBClient.instances) == 2
    assert all(client.session is session for client in DummyLanDBClient.instances)


def test_get_landb_devices_logs_error_when_client_raises(monkeypatch):
    """
    When LanDBClient.build_device_with_ip raises, _get_landb_devices must log an error
    and continue without propagating the exception.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]
    session = object()

    eam_records = [
        DummyEAM(
            equipment_no="DEV-34",
            serial_number="SN-34",
            eq_class="EQ-34",
            manufacturer="MFG-34",
        )
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
            raise RuntimeError("dummy LanDB error")

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=list(eam_records),
            session=session,
            max_workers=1,
        )
    )

    # No devices could be fetched
    assert result == []

    # Error was logged, but not raised
    assert any("Error fetching DEV-34" in msg for msg in logger.errors)
