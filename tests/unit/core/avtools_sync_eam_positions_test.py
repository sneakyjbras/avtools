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
class DummyEAMPosition:
    equipment_no: str
    room: str | None = None
    position: str | None = None


@dataclass
class DummyCachedEAMPosition:
    equipment_no: str
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
# 1) Happy path
# ---------------------------------------------------------------------------


def test_sync_eam_positions_happy_path(monkeypatch):
    """
    EAMClient.get_number_av_positions returns N,
    get_positions_list called once with total=N,
    dbod_helper.get_all_eam_positions called once,
    _sync_entities wired correctly.
    """
    eam_positions = [
        DummyEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyEAMPosition("EQ-38", "ROOM-B", "P2"),
    ]
    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-OLD", "P1"),
        DummyCachedEAMPosition("EQ-39", "ROOM-C", "P3"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    class DummyEAMClient:
        instances: list[DummyEAMClient] = []

        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth
            self.calls: list[tuple[str, Any]] = []
            DummyEAMClient.instances.append(self)

        def get_number_av_positions(self) -> int:
            self.calls.append(("get_number_av_positions", None))
            return len(eam_positions)

        def get_positions_list(self, total: int) -> list[DummyEAMPosition]:
            self.calls.append(("get_positions_list", total))
            assert total == len(eam_positions)
            return eam_positions

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
    av.sync_eam_positions(auth)

    assert len(DummyEAMClient.instances) == 1
    client = DummyEAMClient.instances[0]
    assert client.auth is auth

    # Call order (part of ordering check)
    assert client.calls[0][0] == "get_number_av_positions"
    assert client.calls[1][0] == "get_positions_list"
    assert dbod.calls == ["get_all_eam_positions"]

    # _sync_entities wiring
    assert captured["api_items"] == eam_positions
    assert captured["cached_items"] == cached_positions
    assert captured["name"] == "EAM Positions"
    assert [captured["get_id"](p) for p in eam_positions] == [
        p.equipment_no for p in eam_positions
    ]
    assert captured["sync_func"] is dbod.sync_eam_positions


# ---------------------------------------------------------------------------
# 2) Zero positions from EAM
# ---------------------------------------------------------------------------


def test_sync_eam_positions_zero_total_calls_sync_entities(monkeypatch):
    """
    get_number_av_positions returns 0,
    get_positions_list called with 0,
    _sync_entities receives api_items = [] and cached_items = cache list.
    """
    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyCachedEAMPosition("EQ-38", "ROOM-B", "P2"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth
            self.calls: list[tuple[str, Any]] = []

        def get_number_av_positions(self) -> int:
            self.calls.append(("get_number_av_positions", None))
            return 0

        def get_positions_list(self, total: int) -> list[DummyEAMPosition]:
            self.calls.append(("get_positions_list", total))
            assert total == 0
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

    av.sync_eam_positions(HTTPBasicAuth("user", "pass"))

    assert dbod.calls == ["get_all_eam_positions"]
    assert captured["api_items"] == []
    assert captured["cached_items"] == cached_positions
    assert captured["name"] == "EAM Positions"


# ---------------------------------------------------------------------------
# 3) EAMClient raises when counting positions
# ---------------------------------------------------------------------------


def test_sync_eam_positions_count_error_propagates(monkeypatch):
    """
    get_number_av_positions raises RuntimeError,
    sync_eam_positions must propagate, and no DB/sync calls occur.
    """
    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth
            self.calls: list[str] = []

        def get_number_av_positions(self) -> int:
            self.calls.append("get_number_av_positions")
            raise RuntimeError("EAM positions count failure")

        def get_positions_list(self, total: int) -> list[DummyEAMPosition]:
            self.calls.append("get_positions_list")
            return []

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="EAM positions count failure"):
        av.sync_eam_positions(HTTPBasicAuth("user", "pass"))

    assert dbod.calls == []
    assert sync_called is False


# ---------------------------------------------------------------------------
# 4) EAMClient raises when fetching positions list
# ---------------------------------------------------------------------------


def test_sync_eam_positions_list_error_propagates(monkeypatch):
    """
    get_number_av_positions succeeds,
    get_positions_list raises RuntimeError,
    dbod_helper.get_all_eam_positions NOT called,
    _sync_entities NOT called.
    """
    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth
            self.calls: list[tuple[str, Any]] = []

        def get_number_av_positions(self) -> int:
            self.calls.append(("get_number_av_positions", None))
            return 2

        def get_positions_list(self, total: int) -> list[DummyEAMPosition]:
            self.calls.append(("get_positions_list", total))
            raise RuntimeError("EAM positions list failure")

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="EAM positions list failure"):
        av.sync_eam_positions(HTTPBasicAuth("user", "pass"))

    assert dbod.calls == []
    assert sync_called is False


# ---------------------------------------------------------------------------
# 5) dbod_helper.get_all_eam_positions raises
# ---------------------------------------------------------------------------


def test_sync_eam_positions_cache_error_propagates(monkeypatch):
    """
    EAMClient methods succeed,
    dbod_helper.get_all_eam_positions raises RuntimeError,
    _sync_entities is NOT called, exception propagates.
    """
    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth

        def get_number_av_positions(self) -> int:
            return 1

        def get_positions_list(self, total: int) -> list[DummyEAMPosition]:
            return [DummyEAMPosition("EQ-34", "ROOM-A", "P1")]

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    def boom() -> list[DummyCachedEAMPosition]:
        raise RuntimeError("DB positions error")

    dbod.get_all_eam_positions = boom  # type: ignore[assignment]

    sync_called = False

    def fake_sync_entities(self, *a, **kw) -> None:
        nonlocal sync_called
        sync_called = True

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    with pytest.raises(RuntimeError, match="DB positions error"):
        av.sync_eam_positions(HTTPBasicAuth("user", "pass"))

    assert sync_called is False


# ---------------------------------------------------------------------------
# 6) Call ordering
# ---------------------------------------------------------------------------


def test_sync_eam_positions_ordering(monkeypatch):
    """
    Ensure call order:
    get_number_av_positions -> get_positions_list ->
    dbod_helper.get_all_eam_positions -> _sync_entities.
    """
    eam_positions = [
        DummyEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyEAMPosition("EQ-38", "ROOM-B", "P2"),
    ]
    cached_positions = [
        DummyCachedEAMPosition("EQ-39", "ROOM-C", "P3"),
    ]

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    call_order: list[str] = []

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth

        def get_number_av_positions(self) -> int:
            call_order.append("get_number_av_positions")
            return len(eam_positions)

        def get_positions_list(self, total: int) -> list[DummyEAMPosition]:
            call_order.append("get_positions_list")
            assert total == len(eam_positions)
            return list(eam_positions)

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    captured: dict[str, Any] = {}

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        call_order.append("_sync_entities")
        captured["api_items"] = api_items
        captured["cached_items"] = cached_items

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.sync_eam_positions(HTTPBasicAuth("user", "pass"))

    assert call_order[0] == "get_number_av_positions"
    assert call_order[1] == "get_positions_list"
    assert dbod.calls == ["get_all_eam_positions"]
    assert call_order[2] == "_sync_entities"

    assert captured["api_items"] == eam_positions
    assert captured["cached_items"] == cached_positions


# ---------------------------------------------------------------------------
# 8) API list not mutated
# ---------------------------------------------------------------------------


def test_sync_eam_positions_api_list_not_mutated(monkeypatch):
    """
    Dummy EAMClient returns a list api_list.
    _sync_entities sees same objects in same order,
    and sync_eam_positions does not modify that list.
    """
    api_list = [
        DummyEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyEAMPosition("EQ-38", "ROOM-B", "P2"),
        DummyEAMPosition("EQ-39", "ROOM-C", "P3"),
    ]
    cached_positions: list[DummyCachedEAMPosition] = []

    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth

        def get_number_av_positions(self) -> int:
            return len(api_list)

        def get_positions_list(self, total: int) -> list[DummyEAMPosition]:
            assert total == len(api_list)
            return api_list  # hand out real list to detect mutation

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    seen_in_sync: list[DummyEAMPosition] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        seen_in_sync.extend(api_items)

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    original_ids = [id(p) for p in api_list]
    original_eqs = [p.equipment_no for p in api_list]

    av.sync_eam_positions(HTTPBasicAuth("user", "pass"))

    assert [id(p) for p in seen_in_sync] == original_ids
    assert [p.equipment_no for p in seen_in_sync] == original_eqs

    assert [id(p) for p in api_list] == original_ids
    assert [p.equipment_no for p in api_list] == original_eqs


# ---------------------------------------------------------------------------
# 9) Cache list not mutated
# ---------------------------------------------------------------------------


def test_sync_eam_positions_cache_list_not_mutated(monkeypatch):
    """
    dbod_helper stores cached positions; after sync_eam_positions,
    its internal list remains unchanged, and _sync_entities sees a copy.
    """
    cached_positions = [
        DummyCachedEAMPosition("EQ-34", "ROOM-A", "P1"),
        DummyCachedEAMPosition("EQ-38", "ROOM-B", "P2"),
    ]
    av, logger, dbod = make_avtools_for_tests(cached_positions=cached_positions)

    class DummyEAMClient:
        def __init__(self, auth: HTTPBasicAuth) -> None:
            self.auth = auth

        def get_number_av_positions(self) -> int:
            return 0

        def get_positions_list(self, total: int) -> list[DummyEAMPosition]:
            return []

    monkeypatch.setattr(core, "EAMClient", DummyEAMClient)

    seen_cached: list[DummyCachedEAMPosition] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        seen_cached.extend(cached_items)

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    original_ids = [id(p) for p in dbod._cached_positions]
    original_eqs = [p.equipment_no for p in dbod._cached_positions]

    av.sync_eam_positions(HTTPBasicAuth("user", "pass"))

    # copy seen by _sync_entities
    assert [p.equipment_no for p in seen_cached] == original_eqs
    assert [id(p) for p in seen_cached] != original_ids

    # internal list unchanged
    assert [p.equipment_no for p in dbod._cached_positions] == original_eqs
    assert [id(p) for p in dbod._cached_positions] == original_ids
