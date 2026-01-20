from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pydantic.v1 import BaseModel

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools


class DummyEAM(BaseModel):
    # Must match what AVTools._get_landb_devices reads:
    #   rec.code, rec.serial_number, rec.class_code, rec.manufacturer_code
    code: str
    serial_number: str
    class_code: str
    manufacturer_code: str


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


def _dump_model(m: Any) -> dict[str, Any]:
    if hasattr(m, "dict"):
        return m.dict()
    return dict(vars(m))


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
            code="DEV-34",
            serial_number="SN-34",
            class_code="EQ-34",
            manufacturer_code="MFG-34",
        ),
        # client returns None → should be skipped
        DummyEAM(
            code="DEV-38",
            serial_number="SN-38",
            class_code="EQ-38",
            manufacturer_code="MFG-38",
        ),
        # missing serial_number in result → skipped
        DummyEAM(
            code="DEV-39",
            serial_number="SN-39",
            class_code="EQ-39",
            manufacturer_code="MFG-39",
        ),
        # ip is None in result → skipped
        DummyEAM(
            code="DEV-404",
            serial_number="SN-404",
            class_code="EQ-404",
            manufacturer_code="MFG-404",
        ),
        # client raises error → skipped
        DummyEAM(
            code="DEV-1911",
            serial_number="SN-1911",
            class_code="EQ-1911",
            manufacturer_code="MFG-1911",
        ),
        # another valid → should be kept
        DummyEAM(
            code="DEV-2137",
            serial_number="SN-2137",
            class_code="EQ-2137",
            manufacturer_code="MFG-2137",
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

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=list(eam_records),
            session=session,
            max_workers=3,
        )
    )

    equipment_nos = [d.equipment_no for d in result]
    assert equipment_nos == ["DEV-34", "DEV-2137"]
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
            code="DEV-34",
            serial_number="SN-34",
            class_code="EQ-34",
            manufacturer_code="MFG-34",
        ),
        DummyEAM(
            code="DEV-38",
            serial_number="SN-38",
            class_code="EQ-38",
            manufacturer_code="MFG-38",
        ),
        DummyEAM(
            code="DEV-39",
            serial_number="SN-39",
            class_code="EQ-39",
            manufacturer_code="MFG-39",
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
            code="DEV-14",
            serial_number="SN-14",
            class_code="EQ-14",
            manufacturer_code="MFG-14",
        ),
        DummyEAM(
            code="DEV-1978",
            serial_number="SN-1978",
            class_code="EQ-1978",
            manufacturer_code="MFG-1978",
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
            code="DEV-34",
            serial_number="SN-34",
            class_code="EQ-34",
            manufacturer_code="MFG-34",
        ),
        DummyEAM(
            code="DEV-38",
            serial_number="SN-38",
            class_code="EQ-38",
            manufacturer_code="MFG-38",
        ),
        DummyEAM(
            code="DEV-39",
            serial_number="SN-39",
            class_code="EQ-39",
            manufacturer_code="MFG-39",
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
            max_workers=10,
        )
    )

    equipment_nos = sorted(d.equipment_no for d in result)
    assert equipment_nos == ["DEV-34", "DEV-38", "DEV-39"]

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
            code="DEV-34",
            serial_number="SN-34",
            class_code="EQ-34",
            manufacturer_code="MFG-34",
        ),
        DummyEAM(
            code="DEV-38",
            serial_number="SN-38",
            class_code="EQ-38",
            manufacturer_code="MFG-38",
        ),
    ]

    before = [_dump_model(rec) for rec in eam_records]

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

    _ = asyncio.run(
        av._get_landb_devices(
            eam_records=eam_records,  # pass original list to detect mutation
            session=session,
            max_workers=2,
        )
    )

    after = [_dump_model(rec) for rec in eam_records]
    assert after == before


def test_get_landb_devices_creates_one_lanbd_client_per_task(monkeypatch):
    """
    For total N and max_workers W, _get_landb_devices creates
    num_tasks = min(N, W) LanDBClient instances (one per slice/task).
    """
    av = make_avtools_for_tests()
    session = object()

    # 5 records, max_workers=2 → num_tasks = 2
    eam_records = [
        DummyEAM(
            code=f"DEV-{i}",
            serial_number=f"SN-{i}",
            class_code=f"EQ-{i}",
            manufacturer_code=f"MFG-{i}",
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

    assert sorted(d.equipment_no for d in result) == sorted(r.code for r in eam_records)
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
            code="DEV-34",
            serial_number="SN-34",
            class_code="EQ-34",
            manufacturer_code="MFG-34",
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

    assert result == []
    assert any("Error fetching DEV-34" in msg for msg in logger.errors)


# ---------------------------------------------------------------------------
# Combined behaviour: old tests expected token-refresh *inside* _get_landb_devices.
# New implementation does NOT refresh tokens here; run_landb() calls _ensure_token
# before invoking _get_landb_devices(). These tests are updated 1:1 to assert
# that behavior (no ensure_token calls, no retries), while preserving names.
# ---------------------------------------------------------------------------


def test_get_landb_devices_401_triggers_ensure_token_and_retry_success(monkeypatch):
    """
    Updated behavior:
    - _get_landb_devices does NOT call _ensure_token and does NOT retry.
    - Authorization-like errors are logged and skipped.
    - Other records may still succeed.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    av.landb_cfg = object()

    ensure_calls: list[tuple[Any, Any]] = []

    def fake_ensure_token(self: AVTools, session: Any, cfg: Any) -> None:
        ensure_calls.append((session, cfg))

    monkeypatch.setattr(AVTools, "_ensure_token", fake_ensure_token)

    class UnauthorizedError(Exception):
        pass

    class DummySession:
        pass

    class DummyLanDBClient:
        calls: list[str] = []

        def __init__(self, session: DummySession) -> None:
            self.session = session

        def build_device_with_ip(
            self,
            equipment_no: str,
            serial_number: str,
            eq_class: str,
            manufacturer: str,
        ) -> DummyLanDBDevice:
            DummyLanDBClient.calls.append(equipment_no)
            if equipment_no == "DEV-401":
                raise UnauthorizedError("401 Unauthorized")
            return DummyLanDBDevice(
                equipment_no=equipment_no,
                serial_number=serial_number,
                ip="10.0.0.1",
                manufacturer=manufacturer,
            )

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    session = DummySession()
    eam_records = [
        DummyEAM(
            code="DEV-401",
            serial_number="SN-401",
            class_code="EQ-401",
            manufacturer_code="MFG-401",
        ),
        DummyEAM(
            code="DEV-OK",
            serial_number="SN-OK",
            class_code="EQ-OK",
            manufacturer_code="MFG-OK",
        ),
    ]

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=eam_records,
            session=session,
            max_workers=2,
        )
    )

    # Only the OK one survives (401 was skipped)
    assert [d.equipment_no for d in result] == ["DEV-OK"]

    # No token refresh inside _get_landb_devices
    assert ensure_calls == []

    # No retry: each record processed once
    assert sorted(DummyLanDBClient.calls) == ["DEV-401", "DEV-OK"]

    # Logged error for the unauthorized record
    assert any("Error fetching DEV-401" in msg for msg in logger.errors)


def test_get_landb_devices_401_triggers_ensure_token_but_retry_still_fails(monkeypatch):
    """
    Updated behavior:
    - No _ensure_token calls here.
    - No retries.
    - 401-like errors are logged and the device is skipped.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]
    av.landb_cfg = object()

    ensure_calls: list[tuple[Any, Any]] = []

    def fake_ensure_token(self: AVTools, session: Any, cfg: Any) -> None:
        ensure_calls.append((session, cfg))

    monkeypatch.setattr(AVTools, "_ensure_token", fake_ensure_token)

    class UnauthorizedError(Exception):
        pass

    class DummySession:
        pass

    class DummyLanDBClient:
        calls = 0

        def __init__(self, session: DummySession) -> None:
            self.session = session

        def build_device_with_ip(
            self,
            equipment_no: str,
            serial_number: str,
            eq_class: str,
            manufacturer: str,
        ) -> DummyLanDBDevice:
            DummyLanDBClient.calls += 1
            raise UnauthorizedError("401 Unauthorized (still)")

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    session = DummySession()
    eam_records = [
        DummyEAM(
            code="DEV-401F",
            serial_number="SN-401F",
            class_code="EQ-401F",
            manufacturer_code="MFG-401F",
        )
    ]

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=eam_records,
            session=session,
            max_workers=1,
        )
    )

    assert result == []
    assert ensure_calls == []
    assert DummyLanDBClient.calls == 1
    assert any("Error fetching DEV-401F" in msg for msg in logger.errors)


def test_get_landb_devices_network_error_does_not_trigger_token_refresh(monkeypatch):
    """
    Updated behavior:
    - Network-like errors are logged and skipped.
    - _ensure_token is not called here.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]
    av.landb_cfg = object()

    ensure_calls: list[tuple[Any, Any]] = []

    def fake_ensure_token(self: AVTools, session: Any, cfg: Any) -> None:
        ensure_calls.append((session, cfg))

    monkeypatch.setattr(AVTools, "_ensure_token", fake_ensure_token)

    class NetworkError(Exception):
        pass

    class DummySession:
        pass

    class DummyLanDBClient:
        def __init__(self, session: DummySession) -> None:
            self.session = session

        def build_device_with_ip(
            self,
            equipment_no: str,
            serial_number: str,
            eq_class: str,
            manufacturer: str,
        ) -> DummyLanDBDevice:
            raise NetworkError("connection reset by peer")

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    session = DummySession()
    eam_records = [
        DummyEAM(
            code="DEV-NET",
            serial_number="SN-NET",
            class_code="EQ-NET",
            manufacturer_code="MFG-NET",
        )
    ]

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=eam_records,
            session=session,
            max_workers=1,
        )
    )

    assert result == []
    assert ensure_calls == []
    assert any("Error fetching DEV-NET" in msg for msg in logger.errors)


def test_get_landb_devices_calls_ensure_token_before_requests_when_no_token(
    monkeypatch,
):
    """
    Updated behavior:
    - _get_landb_devices does not call _ensure_token (run_landb does).
    - We assert ensure_token is NOT invoked and the session can still be used.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]
    av.landb_cfg = object()

    ensure_calls: list[tuple[Any, Any]] = []

    class DummyAuth:
        def __init__(self) -> None:
            self.token: str | None = None

    class TokenSession:
        def __init__(self) -> None:
            self.auth = DummyAuth()

    def fake_ensure_token(self: AVTools, session: TokenSession, cfg: Any) -> None:
        ensure_calls.append((session, cfg))
        session.auth.token = "fresh-token"

    monkeypatch.setattr(AVTools, "_ensure_token", fake_ensure_token)

    class DummyLanDBClient:
        instances: list[DummyLanDBClient] = []

        def __init__(self, session: TokenSession) -> None:
            # In new behavior, token is not set here (run_landb would do it)
            assert session.auth.token is None
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
                ip="10.0.0.10",
                manufacturer=manufacturer,
            )

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    session = TokenSession()
    eam_records = [
        DummyEAM(
            code="DEV-TOKEN",
            serial_number="SN-TOKEN",
            class_code="EQ-TOKEN",
            manufacturer_code="MFG-TOKEN",
        )
    ]

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=eam_records,
            session=session,
            max_workers=1,
        )
    )

    assert [d.equipment_no for d in result] == ["DEV-TOKEN"]
    assert ensure_calls == []
    assert len(DummyLanDBClient.instances) == 1
    assert DummyLanDBClient.instances[0].session is session


def test_get_landb_devices_handles_malformed_refresh_token_gracefully(monkeypatch):
    """
    Updated behavior:
    - _get_landb_devices does not refresh tokens and does not retry.
    - Errors are logged and the function returns [].
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]
    av.landb_cfg = object()

    ensure_calls: list[tuple[Any, Any]] = []

    class DummyAuth:
        def __init__(self) -> None:
            self.token: str | None = None

    class TokenSession:
        def __init__(self) -> None:
            self.auth = DummyAuth()

    def fake_ensure_token(self: AVTools, session: TokenSession, cfg: Any) -> None:
        ensure_calls.append((session, cfg))
        # token remains None (malformed refresh)

    monkeypatch.setattr(AVTools, "_ensure_token", fake_ensure_token)

    class UnauthorizedError(Exception):
        pass

    class DummyLanDBClient:
        calls = 0

        def __init__(self, session: TokenSession) -> None:
            self.session = session

        def build_device_with_ip(
            self,
            equipment_no: str,
            serial_number: str,
            eq_class: str,
            manufacturer: str,
        ) -> DummyLanDBDevice:
            DummyLanDBClient.calls += 1
            raise UnauthorizedError("401 Unauthorized (malformed token)")

    monkeypatch.setattr(core, "LanDBClient", DummyLanDBClient)

    session = TokenSession()
    eam_records = [
        DummyEAM(
            code="DEV-BADTOKEN",
            serial_number="SN-BADTOKEN",
            class_code="EQ-BADTOKEN",
            manufacturer_code="MFG-BADTOKEN",
        )
    ]

    result = asyncio.run(
        av._get_landb_devices(
            eam_records=eam_records,
            session=session,
            max_workers=1,
        )
    )

    assert result == []
    assert ensure_calls == []
    assert DummyLanDBClient.calls == 1
    assert logger.errors or logger.exceptions
