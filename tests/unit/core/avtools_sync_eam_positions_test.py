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
class DummyEAMPosition:
    # New sync_eam_positions uses get_id=lambda p: p.code
    code: str
    room: str | None = None
    position: str | None = None


@dataclass
class DummyCachedEAMPosition:
    code: str
    room: str | None = None
    position: str | None = None


class DummyDBODHelper:
    def __init__(self, cached_positions: list[DummyCachedEAMPosition]) -> None:
        # Keep internal copy; tests assert this is not mutated.
        self._cached_positions = list(cached_positions)
        self.calls: list[str] = []

    def get_all_eam_positions(self) -> list[DummyCachedEAMPosition]:
        self.calls.append("get_all_eam_positions")
        # Return fresh list to detect external mutation separately.
        return list(self._cached_positions)

    def sync_eam_positions(
        self,
        *,
        to_insert: list[DummyEAMPosition],
        to_update: list[tuple[DummyEAMPosition, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        self.calls.append("sync_eam_positions")


def make_avtools_for_tests(
    cached_positions: list[DummyCachedEAMPosition],
) -> tuple[AVTools, DummyLogger, DummyDBODHelper]:
    av = object.__new__(AVTools)
    logger = DummyLogger()
    helper = DummyDBODHelper(cached_positions=cached_positions)
    av.logger = logger
    av.dbod_helper = helper
    return av, logger, helper


# ---------------------------------------------------------------------------
# GridQuery stub (sync_eam_positions now builds GridQuery(...).filter(...).limit(...).all())
# ---------------------------------------------------------------------------


class DummyGridQuery:
    instances: list[DummyGridQuery] = []

    # Per-test configuration knobs:
    raise_in_init: Exception | None = None
    filter_raises: Exception | None = None
    limit_raises: Exception | None = None
    all_raises: Exception | None = None
    all_return: list[DummyEAMPosition] = []
    call_order: list[str] | None = None

    def __init__(self, *, name: str, field_map: dict[str, str]) -> None:
        if DummyGridQuery.call_order is not None:
            DummyGridQuery.call_order.append("gridquery.init")

        if DummyGridQuery.raise_in_init is not None:
            raise DummyGridQuery.raise_in_init

        self.name = name
        self.field_map = field_map
        self.calls: list[tuple[str, Any]] = []
        DummyGridQuery.instances.append(self)

    def filter(self, **kwargs: Any) -> DummyGridQuery:
        self.calls.append(("filter", dict(kwargs)))
        if DummyGridQuery.call_order is not None:
            DummyGridQuery.call_order.append("query.filter")
        if DummyGridQuery.filter_raises is not None:
            raise DummyGridQuery.filter_raises
        return self

    def limit(self, n: int) -> DummyGridQuery:
        self.calls.append(("limit", n))
        if DummyGridQuery.call_order is not None:
            DummyGridQuery.call_order.append("query.limit")
        if DummyGridQuery.limit_raises is not None:
            raise DummyGridQuery.limit_raises
        return self

    def all(self) -> list[DummyEAMPosition]:
        self.calls.append(("all", None))
        if DummyGridQuery.call_order is not None:
            DummyGridQuery.call_order.append("query.all")
        if DummyGridQuery.all_raises is not None:
            raise DummyGridQuery.all_raises
        return DummyGridQuery.all_return


def patch_gridquery(monkeypatch: Any) -> None:
    monkeypatch.setattr(core, "GridQuery", DummyGridQuery, raising=True)


def reset_gridquery_state() -> None:
    DummyGridQuery.instances.clear()
    DummyGridQuery.raise_in_init = None
    DummyGridQuery.filter_raises = None
    DummyGridQuery.limit_raises = None
    DummyGridQuery.all_raises = None
    DummyGridQuery.all_return = []
    DummyGridQuery.call_order = None


# ---------------------------------------------------------------------------
# 1) Happy path
# ---------------------------------------------------------------------------


def test_sync_eam_positions_happy_path(monkeypatch):
    """
    GridQuery is built with name=position_grid and a field_map,
    filter called with department_code__startswith (default AV),
    query.all called once,
    dbod_helper.get_all_eam_positions called once,
    _sync_entities wired correctly (get_id uses .code).
    """
    reset_gridquery_state()

    eam_positions = [
        DummyEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyEAMPosition("EQ-38", "ROOM-B", "P2"),
    ]
    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-OLD", "P1"),
        DummyCachedEAMPosition("EQ-39", "ROOM-C", "P3"),
    ]

    DummyGridQuery.all_return = eam_positions
    patch_gridquery(monkeypatch)

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

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

    av.sync_eam_positions()  # defaults: position_grid OSOBJP, department_code AV, limit None

    assert len(DummyGridQuery.instances) == 1
    query = DummyGridQuery.instances[0]

    # Constructor args
    assert query.name == "OSOBJP"
    # Minimal sanity on field_map; don't over-couple, just validate key mappings exist
    assert query.field_map.get("code") == "equipmentno"
    assert query.field_map.get("department_code") == "department"

    # filter called with default department_code prefix
    assert ("filter", {"department_code__startswith": "AV"}) in query.calls
    # limit not called (limit None)
    assert all(c[0] != "limit" for c in query.calls)
    # all called
    assert ("all", None) in query.calls

    assert dbod.calls == ["get_all_eam_positions"]

    # _sync_entities wiring
    assert captured["api_items"] == eam_positions
    assert captured["cached_items"] == cached_positions
    assert captured["name"] == "EAM Positions"
    assert [captured["get_id"](p) for p in eam_positions] == [
        p.code for p in eam_positions
    ]
    assert captured["sync_func"] is dbod.sync_eam_positions

    # No warnings in happy path
    assert logger.warning_messages == []


# ---------------------------------------------------------------------------
# 2) Zero positions from EAM
# ---------------------------------------------------------------------------


def test_sync_eam_positions_zero_total_calls_sync_entities(monkeypatch):
    """
    query.all returns [],
    dbod_helper.get_all_eam_positions called,
    _sync_entities receives api_items = [] and cached_items = cache list.
    """
    reset_gridquery_state()

    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyCachedEAMPosition("EQ-38", "ROOM-B", "P2"),
    ]

    DummyGridQuery.all_return = []
    patch_gridquery(monkeypatch)

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

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

    av.sync_eam_positions()

    assert dbod.calls == ["get_all_eam_positions"]
    assert captured["api_items"] == []
    assert captured["cached_items"] == cached_positions
    assert captured["name"] == "EAM Positions"


# ---------------------------------------------------------------------------
# 3) "Count error" equivalent: GridQuery construction fails early
# ---------------------------------------------------------------------------


def test_sync_eam_positions_count_error_propagates(monkeypatch):
    """
    Old version: get_number_av_positions raises -> propagate.
    New equivalent: GridQuery(...) raises during construction -> propagate.
    No DB calls and no _sync_entities call.
    """
    reset_gridquery_state()

    cached_positions = [DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1")]
    DummyGridQuery.raise_in_init = RuntimeError("EAM positions query init failure")
    patch_gridquery(monkeypatch)

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="EAM positions query init failure"):
        av.sync_eam_positions()

    assert dbod.calls == []
    assert sync_called is False


# ---------------------------------------------------------------------------
# 4) "List error" equivalent: query.all fails
# ---------------------------------------------------------------------------


def test_sync_eam_positions_list_error_propagates(monkeypatch):
    """
    Old version: get_positions_list raises -> propagate.
    New equivalent: query.all raises -> propagate.
    dbod_helper.get_all_eam_positions NOT called,
    _sync_entities NOT called.
    """
    reset_gridquery_state()

    cached_positions = [DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1")]

    DummyGridQuery.all_raises = RuntimeError("EAM positions all() failure")
    patch_gridquery(monkeypatch)

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="EAM positions all\\(\\) failure"):
        av.sync_eam_positions()

    assert dbod.calls == []
    assert sync_called is False


# ---------------------------------------------------------------------------
# 5) dbod_helper.get_all_eam_positions raises
# ---------------------------------------------------------------------------


def test_sync_eam_positions_cache_error_propagates(monkeypatch):
    """
    query.all succeeds,
    dbod_helper.get_all_eam_positions raises RuntimeError,
    _sync_entities is NOT called, exception propagates.
    """
    reset_gridquery_state()

    DummyGridQuery.all_return = [DummyEAMPosition("EQ-34", "ROOM-A", "P1")]
    patch_gridquery(monkeypatch)

    cached_positions = [DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1")]
    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    def boom() -> list[DummyCachedEAMPosition]:
        raise RuntimeError("DB positions error")

    dbod.get_all_eam_positions = boom  # type: ignore[assignment]

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="DB positions error"):
        av.sync_eam_positions()

    assert sync_called is False


# ---------------------------------------------------------------------------
# 6) Call ordering
# ---------------------------------------------------------------------------


def test_sync_eam_positions_ordering(monkeypatch):
    """
    Ensure call order:
    GridQuery init -> filter (dept prefix) -> all ->
    dbod_helper.get_all_eam_positions -> _sync_entities.
    """
    reset_gridquery_state()

    eam_positions = [
        DummyEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyEAMPosition("EQ-38", "ROOM-B", "P2"),
    ]
    cached_positions = [DummyCachedEAMPosition("EQ-39", "ROOM-C", "P3")]

    call_order: list[str] = []
    DummyGridQuery.call_order = call_order
    DummyGridQuery.all_return = list(eam_positions)
    patch_gridquery(monkeypatch)

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    # Wrap DB call to record ordering
    original_get_all = dbod.get_all_eam_positions

    def wrapped_get_all() -> list[DummyCachedEAMPosition]:
        call_order.append("db.get_all_eam_positions")
        return original_get_all()

    dbod.get_all_eam_positions = wrapped_get_all  # type: ignore[assignment]

    captured: dict[str, Any] = {}

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        call_order.append("_sync_entities")
        captured["api_items"] = api_items
        captured["cached_items"] = cached_items

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.sync_eam_positions()

    assert call_order == [
        "gridquery.init",
        "query.filter",
        "query.all",
        "db.get_all_eam_positions",
        "_sync_entities",
    ]
    assert captured["api_items"] == eam_positions
    assert captured["cached_items"] == cached_positions


# ---------------------------------------------------------------------------
# 8) API list not mutated
# ---------------------------------------------------------------------------


def test_sync_eam_positions_api_list_not_mutated(monkeypatch):
    """
    query.all returns a list api_list.
    _sync_entities sees the same list object in the same order,
    and sync_eam_positions does not reorder or modify it.
    """
    reset_gridquery_state()

    api_list = [
        DummyEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyEAMPosition("EQ-38", "ROOM-B", "P2"),
        DummyEAMPosition("EQ-39", "ROOM-C", "P3"),
    ]
    DummyGridQuery.all_return = api_list  # return the actual list object
    patch_gridquery(monkeypatch)

    cached_positions: list[DummyCachedEAMPosition] = []
    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    seen_api_list_obj: list[Any] = []
    seen_api_ids: list[int] = []
    seen_api_codes: list[str] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        seen_api_list_obj.append(api_items)
        seen_api_ids.extend([id(p) for p in api_items])
        seen_api_codes.extend([p.code for p in api_items])

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    original_ids = [id(p) for p in api_list]
    original_codes = [p.code for p in api_list]

    av.sync_eam_positions()

    assert seen_api_list_obj[0] is api_list
    assert seen_api_ids == original_ids
    assert seen_api_codes == original_codes

    assert [id(p) for p in api_list] == original_ids
    assert [p.code for p in api_list] == original_codes


# ---------------------------------------------------------------------------
# 9) Cache list not mutated
# ---------------------------------------------------------------------------


def test_sync_eam_positions_cache_list_not_mutated(monkeypatch):
    """
    dbod_helper stores cached positions; after sync_eam_positions,
    its internal list remains unchanged, and _sync_entities sees a copy.
    """
    reset_gridquery_state()

    DummyGridQuery.all_return = []
    patch_gridquery(monkeypatch)

    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyCachedEAMPosition("EQ-38", "ROOM-B", "P2"),
    ]
    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    captured_cached_list_obj: list[Any] = []
    captured_cached_codes: list[str] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        captured_cached_list_obj.append(cached_items)
        captured_cached_codes.extend([p.code for p in cached_items])

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    internal_list_before = dbod._cached_positions
    internal_codes_before = [p.code for p in internal_list_before]

    av.sync_eam_positions()

    # _sync_entities saw same elements, but in a NEW list container
    assert captured_cached_codes == internal_codes_before
    assert captured_cached_list_obj[0] is not internal_list_before

    # internal cached list unchanged
    assert dbod._cached_positions is internal_list_before
    assert [p.code for p in dbod._cached_positions] == internal_codes_before
