from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from requests.auth import HTTPBasicAuth

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools


class DummyLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []
        self.exception_messages: list[str] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.info_messages.append(str(msg))

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.error_messages.append(str(msg))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exception_messages.append(str(msg))


@dataclass
class DummyEAMDevice:
    equipment_no: str
    serial_number: str | None = None
    eq_class: str | None = None
    manufacturer: str | None = None


@dataclass
class DummyCachedEAMDevice:
    equipment_no: str
    serial_number: str | None = None
    eq_class: str | None = None
    manufacturer: str | None = None


class DummyDBODHelper:
    def __init__(self, cached_devices: list[DummyCachedEAMDevice]) -> None:
        # Keep an internal copy; tests assert this is not mutated.
        self._cached_devices = list(cached_devices)
        self.calls: list[str] = []

    def get_all_eam_devices(self) -> list[DummyCachedEAMDevice]:
        self.calls.append("get_all_eam_devices")
        # Return a fresh list so any mutation won't hit our internal copy.
        return list(self._cached_devices)

    def sync_eam_devices(
        self,
        *,
        to_insert: list[DummyEAMDevice],
        to_update: list[tuple[DummyEAMDevice, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        self.calls.append("sync_eam_devices")


def make_avtools_for_tests(
    cached_devices: list[DummyCachedEAMDevice],
) -> tuple[AVTools, DummyLogger, DummyDBODHelper]:
    """Create an AVTools instance without invoking __init__."""
    av = object.__new__(AVTools)
    logger = DummyLogger()
    helper = DummyDBODHelper(cached_devices=cached_devices)
    av.logger = logger
    av.dbod_helper = helper
    return av, logger, helper


# ---------------------------------------------------------------------------
# 1) Happy path
# ---------------------------------------------------------------------------


def test_sync_eam_devices_happy_path(monkeypatch):
    """
    EAMClient.get_number_av_assets returns N,
    EAMClient.get_device_list called with total=N,
    dbod_helper.get_all_eam_devices called once,
    _sync_entities called with correct arguments.
    """
    eam_devices = [
        DummyEAMDevice("EQ-34", "SN34"),
        DummyEAMDevice("EQ-38", "SN38"),
    ]
    cached_devices = [
        DummyCachedEAMDevice("EQ-34", "SN34_OLD"),
        DummyCachedEAMDevice("EQ-39", "SN39"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    class DummyEAMClient:
        instances: list[DummyEAMClient] = []

        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth
            self.calls: list[tuple[str, Any]] = []
            DummyEAMClient.instances.append(self)

        def get_number_av_assets(self) -> int:
            self.calls.append(("get_number_av_assets", None))
            return len(eam_devices)

        def get_device_list(self, total: int) -> list[DummyEAMDevice]:
            self.calls.append(("get_device_list", total))
            # bullet 7: total must equal N
            assert total == len(eam_devices)
            return eam_devices

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    captured: dict[str, Any] = {}

    def fake_sync_entities(
        self,
        api_items,
        cached_items,
        get_id,
        sync_func,
        name: str,
    ) -> None:
        captured["api_items"] = api_items
        captured["cached_items"] = cached_items
        captured["get_id"] = get_id
        captured["sync_func"] = sync_func
        captured["name"] = name

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    auth = HTTPBasicAuth("user", "pass")
    av.sync_eam_devices(auth)

    # EAM client created once with given auth
    assert len(DummyEAMClient.instances) == 1
    client = DummyEAMClient.instances[0]
    assert client.auth is auth

    # EAM methods and DB helper call order (part of bullet 6)
    assert client.calls[0][0] == "get_number_av_assets"
    assert client.calls[1][0] == "get_device_list"
    assert dbod.calls == ["get_all_eam_devices"]

    # _sync_entities wiring
    assert captured["api_items"] == eam_devices
    assert captured["cached_items"] == cached_devices
    assert captured["name"] == "EAM Devices"
    assert [captured["get_id"](d) for d in eam_devices] == [
        d.equipment_no for d in eam_devices
    ]
    assert captured["sync_func"] is dbod.sync_eam_devices


# ---------------------------------------------------------------------------
# 2) Zero assets from EAM
# ---------------------------------------------------------------------------


def test_sync_eam_devices_zero_total_calls_sync_entities(monkeypatch):
    """
    get_number_av_assets returns 0,
    get_device_list called with 0,
    _sync_entities called with api_items = [] and cached_items = cache list.
    """
    cached_devices = [
        DummyCachedEAMDevice("EQ-34", "SN34"),
        DummyCachedEAMDevice("EQ-38", "SN38"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth
            self.calls: list[tuple[str, Any]] = []

        def get_number_av_assets(self) -> int:
            self.calls.append(("get_number_av_assets", None))
            return 0

        def get_device_list(self, total: int) -> list[DummyEAMDevice]:
            self.calls.append(("get_device_list", total))
            assert total == 0  # bullet 7
            return []

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    captured: dict[str, Any] = {}

    def fake_sync_entities(
        self,
        api_items,
        cached_items,
        get_id,
        sync_func,
        name: str,
    ) -> None:
        captured["api_items"] = api_items
        captured["cached_items"] = cached_items
        captured["name"] = name

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.sync_eam_devices(HTTPBasicAuth("user", "pass"))

    assert dbod.calls == ["get_all_eam_devices"]
    assert captured["api_items"] == []
    assert captured["cached_items"] == cached_devices
    assert captured["name"] == "EAM Devices"


# ---------------------------------------------------------------------------
# 3) EAMClient raises when counting assets
# ---------------------------------------------------------------------------


def test_sync_eam_devices_eam_count_error_propagates(monkeypatch):
    """
    get_number_av_assets raises RuntimeError,
    sync_eam_devices must propagate the exception,
    and nothing else is called.
    """
    cached_devices = [
        DummyCachedEAMDevice("EQ-34", "SN34"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth
            self.calls: list[str] = []

        def get_number_av_assets(self) -> int:
            self.calls.append("get_number_av_assets")
            raise RuntimeError("EAM count failure")

        def get_device_list(self, total: int) -> list[DummyEAMDevice]:
            self.calls.append("get_device_list")
            return []

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="EAM count failure"):
        av.sync_eam_devices(HTTPBasicAuth("user", "pass"))

    # no DB calls, no sync_entities call
    assert dbod.calls == []
    assert sync_called is False


# ---------------------------------------------------------------------------
# 4) EAMClient raises when fetching device list
# ---------------------------------------------------------------------------


def test_sync_eam_devices_eam_list_error_propagates(monkeypatch):
    """
    get_number_av_assets succeeds,
    get_device_list raises RuntimeError,
    dbod_helper.get_all_eam_devices NOT called,
    _sync_entities NOT called, and exception propagates.
    """
    cached_devices = [
        DummyCachedEAMDevice("EQ-34", "SN34"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth
            self.calls: list[tuple[str, Any]] = []

        def get_number_av_assets(self) -> int:
            self.calls.append(("get_number_av_assets", None))
            return 2

        def get_device_list(self, total: int) -> list[DummyEAMDevice]:
            self.calls.append(("get_device_list", total))
            raise RuntimeError("EAM list failure")

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="EAM list failure"):
        av.sync_eam_devices(HTTPBasicAuth("user", "pass"))

    assert dbod.calls == []
    assert sync_called is False


# ---------------------------------------------------------------------------
# 5) dbod_helper.get_all_eam_devices raises
# ---------------------------------------------------------------------------


def test_sync_eam_devices_cache_error_propagates(monkeypatch):
    """
    EAMClient methods succeed,
    dbod_helper.get_all_eam_devices raises RuntimeError,
    _sync_entities is NOT called, exception propagates.
    """
    cached_devices = [
        DummyCachedEAMDevice("EQ-34", "SN34"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth

        def get_number_av_assets(self) -> int:
            return 1

        def get_device_list(self, total: int) -> list[DummyEAMDevice]:
            return [DummyEAMDevice("EQ-34", "SN34")]

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    # Make dbod_helper.get_all_eam_devices raise
    def boom() -> list[DummyCachedEAMDevice]:
        raise RuntimeError("DB error")

    dbod.get_all_eam_devices = boom  # type: ignore[assignment]

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="DB error"):
        av.sync_eam_devices(HTTPBasicAuth("user", "pass"))

    assert sync_called is False


# ---------------------------------------------------------------------------
# 6) Call ordering (and re-check of total propagation)
# ---------------------------------------------------------------------------


def test_sync_eam_devices_ordering(monkeypatch):
    """
    Ensure call order:
    EAMClient.get_number_av_assets -> EAMClient.get_device_list ->
    dbod_helper.get_all_eam_devices -> _sync_entities.
    """
    eam_devices = [
        DummyEAMDevice("EQ-34", "SN34"),
        DummyEAMDevice("EQ-38", "SN38"),
    ]
    cached_devices = [
        DummyCachedEAMDevice("EQ-39", "SN39"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    call_order: list[str] = []

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth

        def get_number_av_assets(self) -> int:
            call_order.append("get_number_av_assets")
            return len(eam_devices)

        def get_device_list(self, total: int) -> list[DummyEAMDevice]:
            call_order.append("get_device_list")
            # bullet 7 again
            assert total == len(eam_devices)
            return list(eam_devices)

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    captured: dict[str, Any] = {}

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        call_order.append("_sync_entities")
        captured["api_items"] = api_items
        captured["cached_items"] = cached_items

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.sync_eam_devices(HTTPBasicAuth("user", "pass"))

    # EAM methods first
    assert call_order[0] == "get_number_av_assets"
    assert call_order[1] == "get_device_list"
    # DB helper then _sync_entities
    assert dbod.calls == ["get_all_eam_devices"]
    assert call_order[2] == "_sync_entities"

    assert captured["api_items"] == eam_devices
    assert captured["cached_items"] == cached_devices


# ---------------------------------------------------------------------------
# 8) API list not mutated
# ---------------------------------------------------------------------------


def test_sync_eam_devices_api_list_not_mutated(monkeypatch):
    """
    Dummy EAMClient returns a list api_list.
    _sync_entities sees the list unchanged (same objects in same order),
    and sync_eam_devices does not reorder or modify it.
    """
    api_list = [
        DummyEAMDevice("EQ-34", "SN34"),
        DummyEAMDevice("EQ-38", "SN38"),
        DummyEAMDevice("EQ-39", "SN39"),
    ]
    cached_devices: list[DummyCachedEAMDevice] = []

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth

        def get_number_av_assets(self) -> int:
            return len(api_list)

        def get_device_list(self, total: int) -> list[DummyEAMDevice]:
            assert total == len(api_list)
            # Return the actual list (not a copy) so we can detect mutation
            return api_list

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    seen_in_sync: list[DummyEAMDevice] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        seen_in_sync.extend(api_items)

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    # Snapshot original ordering & objects
    original_ids = [id(d) for d in api_list]
    original_eqs = [d.equipment_no for d in api_list]

    av.sync_eam_devices(HTTPBasicAuth("user", "pass"))

    # _sync_entities saw same objects in same order
    assert [id(d) for d in seen_in_sync] == original_ids
    assert [d.equipment_no for d in seen_in_sync] == original_eqs

    # sync_eam_devices did not mutate api_list
    assert [id(d) for d in api_list] == original_ids
    assert [d.equipment_no for d in api_list] == original_eqs


# ---------------------------------------------------------------------------
# 9) Cache list not mutated
# ---------------------------------------------------------------------------


def test_sync_eam_devices_cache_list_not_mutated(monkeypatch):
    """
    dbod_helper stores a cached list.
    After sync_eam_devices, its internal cached list is unchanged.
    _sync_entities sees a separate list copy.
    """
    cached_devices = [
        DummyCachedEAMDevice("EQ-34", "SN34"),
        DummyCachedEAMDevice("EQ-38", "SN38"),
    ]
    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth

        def get_number_av_assets(self) -> int:
            return 0

        def get_device_list(self, total: int) -> list[DummyEAMDevice]:
            return []

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    seen_cached: list[DummyCachedEAMDevice] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        seen_cached.extend(cached_items)

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    # Snapshot original helper's internal list
    original_internal_ids = [id(d) for d in dbod._cached_devices]
    original_internal_eqs = [d.equipment_no for d in dbod._cached_devices]

    av.sync_eam_devices(HTTPBasicAuth("user", "pass"))

    # _sync_entities saw a separate list copy (different container object)
    assert [d.equipment_no for d in seen_cached] == original_internal_eqs
    assert [id(d) for d in seen_cached] != original_internal_ids  # new list

    # Internal cached list unchanged
    assert [d.equipment_no for d in dbod._cached_devices] == original_internal_eqs
    assert [id(d) for d in dbod._cached_devices] == original_internal_ids
