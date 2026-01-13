from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools


class DummyLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []
        self.exception_messages: list[str] = []
        self.warning_messages: list[tuple[str, dict[str, Any]]] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.info_messages.append(str(msg))

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.error_messages.append(str(msg))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exception_messages.append(str(msg))

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        # structlog-style: msg is an event string + kwargs
        self.warning_messages.append((str(msg), dict(kwargs)))


@dataclass
class DummyEAMDevice:
    code: str
    serial_number: str | None = None
    class_code: str | None = None
    manufacturer_code: str | None = None
    department_code: str | None = None


@dataclass
class DummyCachedEAMDevice:
    code: str
    serial_number: str | None = None
    class_code: str | None = None
    manufacturer_code: str | None = None
    department_code: str | None = None


class DummyDBODHelper:
    def __init__(self, cached_devices: list[DummyCachedEAMDevice]) -> None:
        # Keep an internal copy; tests assert this is not mutated.
        self._cached_devices = list(cached_devices)
        self.calls: list[str] = []

    def get_all_eam_devices(self) -> list[DummyCachedEAMDevice]:
        self.calls.append("get_all_eam_devices")
        # Return a fresh list so container mutations won't hit internal list.
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
# EAM Equipment query stubs (Equipment.objects.use_grid(...))
# ---------------------------------------------------------------------------


class DummyQuery:
    def __init__(
        self,
        *,
        all_return: list[DummyEAMDevice],
        call_order: list[str] | None = None,
        filter_raises: Exception | None = None,
        limit_raises: Exception | None = None,
        all_raises: Exception | None = None,
    ) -> None:
        self._all_return = all_return
        self._call_order = call_order
        self._filter_raises = filter_raises
        self._limit_raises = limit_raises
        self._all_raises = all_raises
        self.calls: list[tuple[str, Any]] = []

    def filter(self, **kwargs: Any) -> DummyQuery:
        self.calls.append(("filter", dict(kwargs)))
        if self._call_order is not None:
            self._call_order.append("query.filter")
        if self._filter_raises is not None:
            raise self._filter_raises
        return self

    def limit(self, n: int) -> DummyQuery:
        self.calls.append(("limit", n))
        if self._call_order is not None:
            self._call_order.append("query.limit")
        if self._limit_raises is not None:
            raise self._limit_raises
        return self

    def all(self) -> list[DummyEAMDevice]:
        self.calls.append(("all", None))
        if self._call_order is not None:
            self._call_order.append("query.all")
        if self._all_raises is not None:
            raise self._all_raises
        return self._all_return


class DummyEquipmentObjects:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._query_factory: Any = None  # set per-test

    def use_grid(self, *, name: str) -> DummyQuery:
        self.calls.append(("use_grid", name))
        if self._query_factory is None:
            raise AssertionError("Test bug: query_factory not set")
        return self._query_factory(name)


class DummyEquipment:
    # This mimics eam_rest_client.Equipment having a class-level `objects` manager.
    objects = DummyEquipmentObjects()


def patch_equipment(monkeypatch: Any) -> DummyEquipmentObjects:
    """
    Patch avtools.core.av_tools.Equipment to our DummyEquipment, and return the objects manager.
    """
    monkeypatch.setattr(core, "Equipment", DummyEquipment, raising=True)
    return DummyEquipment.objects


# ---------------------------------------------------------------------------
# 1) Happy path
# ---------------------------------------------------------------------------


def test_sync_eam_devices_happy_path(monkeypatch):
    """
    Equipment.objects.use_grid called with asset_grid,
    filter called with department_code__startswith (default AV),
    query.all called,
    dbod_helper.get_all_eam_devices called once,
    _sync_entities called with correct arguments and get_id uses .code.
    """
    eam_devices = [
        DummyEAMDevice(code="EQ-34", serial_number="SN34", department_code="AVX"),
        DummyEAMDevice(code="EQ-38", serial_number="SN38", department_code="AVY"),
    ]
    cached_devices = [
        DummyCachedEAMDevice(code="EQ-34", serial_number="SN34_OLD"),
        DummyCachedEAMDevice(code="EQ-39", serial_number="SN39"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    objects_mgr = patch_equipment(monkeypatch)

    def query_factory(grid_name: str) -> DummyQuery:
        assert grid_name == "OSOBJA"
        return DummyQuery(all_return=list(eam_devices))

    objects_mgr._query_factory = query_factory

    captured: dict[str, Any] = {}

    def fake_sync_entities(
        self, api_items, cached_items, get_id, sync_func, name: str
    ) -> None:
        captured["api_items"] = api_items
        captured["cached_items"] = cached_items
        captured["get_id"] = get_id
        captured["sync_func"] = sync_func
        captured["name"] = name

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.sync_eam_devices()  # defaults: asset_grid OSOBJA, department_code AV, limit None

    # use_grid called once with default asset_grid
    assert objects_mgr.calls == [("use_grid", "OSOBJA")]

    # DB call once
    assert dbod.calls == ["get_all_eam_devices"]

    # _sync_entities wiring
    assert captured["api_items"] == eam_devices
    assert captured["cached_items"] == cached_devices
    assert captured["name"] == "EAM Devices"
    assert [captured["get_id"](d) for d in eam_devices] == [d.code for d in eam_devices]
    assert captured["sync_func"] is dbod.sync_eam_devices

    # No warnings in happy path
    assert logger.warning_messages == []


# ---------------------------------------------------------------------------
# 2) Zero assets from EAM
# ---------------------------------------------------------------------------


def test_sync_eam_devices_zero_total_calls_sync_entities(monkeypatch):
    """
    query.all returns [],
    dbod_helper.get_all_eam_devices called,
    _sync_entities called with api_items=[] and cached_items=cache list.
    """
    cached_devices = [
        DummyCachedEAMDevice(code="EQ-34", serial_number="SN34"),
        DummyCachedEAMDevice(code="EQ-38", serial_number="SN38"),
    ]
    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    objects_mgr = patch_equipment(monkeypatch)

    def query_factory(grid_name: str) -> DummyQuery:
        assert grid_name == "OSOBJA"
        return DummyQuery(all_return=[])

    objects_mgr._query_factory = query_factory

    captured: dict[str, Any] = {}

    def fake_sync_entities(
        self, api_items, cached_items, get_id, sync_func, name: str
    ) -> None:
        captured["api_items"] = api_items
        captured["cached_items"] = cached_items
        captured["name"] = name

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.sync_eam_devices()

    assert dbod.calls == ["get_all_eam_devices"]
    assert captured["api_items"] == []
    assert captured["cached_items"] == cached_devices
    assert captured["name"] == "EAM Devices"
    assert logger.warning_messages == []


# ---------------------------------------------------------------------------
# 3) "EAM count error" equivalent: use_grid fails early
# ---------------------------------------------------------------------------


def test_sync_eam_devices_eam_count_error_propagates(monkeypatch):
    """
    In new implementation there is no 'count' call.
    Equivalent early failure: Equipment.objects.use_grid raises.
    Must propagate and nothing else is called.
    """
    cached_devices = [DummyCachedEAMDevice(code="EQ-34", serial_number="SN34")]
    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    objects_mgr = patch_equipment(monkeypatch)

    def query_factory(grid_name: str) -> DummyQuery:
        raise RuntimeError("EAM use_grid failure")

    objects_mgr._query_factory = query_factory

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="EAM use_grid failure"):
        av.sync_eam_devices()

    assert dbod.calls == []
    assert sync_called is False


# ---------------------------------------------------------------------------
# 4) "EAM list error" equivalent: query.all fails
# ---------------------------------------------------------------------------


def test_sync_eam_devices_eam_list_error_propagates(monkeypatch):
    """
    Equivalent to old 'get_device_list' failure:
    query.all raises.
    dbod_helper.get_all_eam_devices NOT called,
    _sync_entities NOT called, and exception propagates.
    """
    cached_devices = [DummyCachedEAMDevice(code="EQ-34", serial_number="SN34")]
    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    objects_mgr = patch_equipment(monkeypatch)

    def query_factory(grid_name: str) -> DummyQuery:
        assert grid_name == "OSOBJA"
        return DummyQuery(all_return=[], all_raises=RuntimeError("EAM all() failure"))

    objects_mgr._query_factory = query_factory

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="EAM all\\(\\) failure"):
        av.sync_eam_devices()

    assert dbod.calls == []
    assert sync_called is False


# ---------------------------------------------------------------------------
# 5) dbod_helper.get_all_eam_devices raises
# ---------------------------------------------------------------------------


def test_sync_eam_devices_cache_error_propagates(monkeypatch):
    """
    query.all succeeds,
    dbod_helper.get_all_eam_devices raises RuntimeError,
    _sync_entities is NOT called, exception propagates.
    """
    cached_devices = [DummyCachedEAMDevice(code="EQ-34", serial_number="SN34")]
    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    objects_mgr = patch_equipment(monkeypatch)

    def query_factory(grid_name: str) -> DummyQuery:
        assert grid_name == "OSOBJA"
        return DummyQuery(
            all_return=[DummyEAMDevice(code="EQ-34", serial_number="SN34")]
        )

    objects_mgr._query_factory = query_factory

    def boom() -> list[DummyCachedEAMDevice]:
        raise RuntimeError("DB error")

    dbod.get_all_eam_devices = boom  # type: ignore[assignment]

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="DB error"):
        av.sync_eam_devices()

    assert sync_called is False


# ---------------------------------------------------------------------------
# 6) Call ordering
# ---------------------------------------------------------------------------


def test_sync_eam_devices_ordering(monkeypatch):
    """
    Ensure call order:
    Equipment.objects.use_grid -> query.filter (dept prefix) ->
    query.all -> dbod_helper.get_all_eam_devices -> _sync_entities.
    """
    eam_devices = [DummyEAMDevice(code="EQ-34"), DummyEAMDevice(code="EQ-38")]
    cached_devices = [DummyCachedEAMDevice(code="EQ-39")]

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    call_order: list[str] = []

    objects_mgr = patch_equipment(monkeypatch)

    def query_factory(grid_name: str) -> DummyQuery:
        call_order.append("objects.use_grid")
        return DummyQuery(all_return=list(eam_devices), call_order=call_order)

    objects_mgr._query_factory = query_factory

    captured: dict[str, Any] = {}

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        call_order.append("_sync_entities")
        captured["api_items"] = api_items
        captured["cached_items"] = cached_items

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    # Patch DB call ordering into call_order
    original_get_all = dbod.get_all_eam_devices

    def wrapped_get_all() -> list[DummyCachedEAMDevice]:
        call_order.append("db.get_all_eam_devices")
        return original_get_all()

    dbod.get_all_eam_devices = wrapped_get_all  # type: ignore[assignment]

    av.sync_eam_devices()

    assert call_order == [
        "objects.use_grid",
        "query.filter",
        "query.all",
        "db.get_all_eam_devices",
        "_sync_entities",
    ]
    assert captured["api_items"] == eam_devices
    assert captured["cached_items"] == cached_devices


# ---------------------------------------------------------------------------
# 8) API list not mutated
# ---------------------------------------------------------------------------


def test_sync_eam_devices_api_list_not_mutated(monkeypatch):
    """
    query.all returns a list api_list.
    _sync_entities sees the same list object in the same order,
    and sync_eam_devices does not reorder or modify it.
    """
    api_list = [
        DummyEAMDevice(code="EQ-34"),
        DummyEAMDevice(code="EQ-38"),
        DummyEAMDevice(code="EQ-39"),
    ]
    cached_devices: list[DummyCachedEAMDevice] = []

    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    objects_mgr = patch_equipment(monkeypatch)

    def query_factory(grid_name: str) -> DummyQuery:
        q = DummyQuery(all_return=api_list)
        return q

    objects_mgr._query_factory = query_factory

    seen_api_list_obj: list[Any] = []
    seen_api_ids: list[int] = []
    seen_api_codes: list[str] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        # Capture container identity and element identity/order
        seen_api_list_obj.append(api_items)
        seen_api_ids.extend([id(d) for d in api_items])
        seen_api_codes.extend([d.code for d in api_items])

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    original_ids = [id(d) for d in api_list]
    original_codes = [d.code for d in api_list]

    av.sync_eam_devices()

    # _sync_entities saw same container and same objects in same order
    assert seen_api_list_obj[0] is api_list
    assert seen_api_ids == original_ids
    assert seen_api_codes == original_codes

    # sync_eam_devices did not mutate api_list
    assert [id(d) for d in api_list] == original_ids
    assert [d.code for d in api_list] == original_codes


# ---------------------------------------------------------------------------
# 9) Cache list not mutated (container)
# ---------------------------------------------------------------------------


def test_sync_eam_devices_cache_list_not_mutated(monkeypatch):
    """
    dbod_helper stores an internal cached list.
    After sync_eam_devices, its internal cached list container is unchanged,
    and _sync_entities receives a different list container (fresh list).
    """
    cached_devices = [
        DummyCachedEAMDevice(code="EQ-34"),
        DummyCachedEAMDevice(code="EQ-38"),
    ]
    av, logger, dbod = make_avtools_for_tests(cached_devices=cached_devices)

    objects_mgr = patch_equipment(monkeypatch)

    def query_factory(grid_name: str) -> DummyQuery:
        return DummyQuery(all_return=[])

    objects_mgr._query_factory = query_factory

    captured_cached_list_obj: list[Any] = []
    captured_cached_codes: list[str] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        captured_cached_list_obj.append(cached_items)
        captured_cached_codes.extend([d.code for d in cached_items])

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    internal_list_before = dbod._cached_devices
    internal_codes_before = [d.code for d in internal_list_before]

    av.sync_eam_devices()

    # _sync_entities saw same elements, but in a NEW list container
    assert captured_cached_codes == internal_codes_before
    assert captured_cached_list_obj[0] is not internal_list_before

    # Internal cached list unchanged
    assert dbod._cached_devices is internal_list_before
    assert [d.code for d in dbod._cached_devices] == internal_codes_before
