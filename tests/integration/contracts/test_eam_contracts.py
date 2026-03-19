from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DummyEquipment:
    code: str


class FakeEquipmentObjects:
    """Minimal stand-in for eam_rest_client's queryset API (use_grid/filter/limit/all)."""

    def __init__(self, *, items: list[Any]) -> None:
        self._items = items
        self.use_grid_calls: list[dict[str, Any]] = []
        self.query: Any = None

    def use_grid(self, *, name: str):
        self.use_grid_calls.append({"name": name})

        class Q:
            def __init__(self, items: list[Any]) -> None:
                self.items = items
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def filter(self, **kwargs: Any):
                self.calls.append(("filter", dict(kwargs)))
                return self

            def limit(self, n: int):
                self.calls.append(("limit", {"n": n}))
                return self

            def all(self):
                self.calls.append(("all", {}))
                return list(self.items)

        self.query = Q(self._items)
        return self.query


def test_sync_eam_devices_builds_queryset_with_expected_calls(
    avtools_no_db: Any, monkeypatch: Any
) -> None:
    """Contract test: AVTools must use the EAM Equipment queryset API correctly."""

    import avtools.core.av_tools as av_mod

    fake_items = [DummyEquipment(code="AV-001"), DummyEquipment(code="AV-002")]
    fake_objects = FakeEquipmentObjects(items=fake_items)

    class FakeEquipment:
        objects = fake_objects

    # Patch the symbol as used by AVTools.
    monkeypatch.setattr(av_mod, "Equipment", FakeEquipment, raising=True)

    # Stub sanitizer: should be called, but we keep items unchanged.
    called = {"clean": 0}

    def clean_items(items: list[Any]) -> list[Any]:
        called["clean"] += 1
        return items

    avtools_no_db._eam_sanitizer.clean_items = clean_items  # type: ignore[assignment]

    # Stub cache and _sync_entities so we don't touch DB/diff logic.
    avtools_no_db.dbod_helper.get_all_eam_devices = lambda: []  # type: ignore[attr-defined]

    sync_args: dict[str, Any] = {}

    def fake_sync_entities(
        *,
        api_items: list[Any],
        cached_items: list[Any],
        get_id: Any,
        sync_func: Any,
        name: str,
    ) -> None:
        sync_args.update(
            {
                "api_items": api_items,
                "cached_items": cached_items,
                "name": name,
                "ids": [get_id(x) for x in api_items],
                "sync_func": sync_func,
            }
        )

    avtools_no_db._sync_entities = fake_sync_entities  # type: ignore[attr-defined]

    avtools_no_db.sync_eam_devices(asset_grid="OSOBJA", department_code="AV", limit=10)

    # 1) Equipment.objects.use_grid must be called with the grid name.
    assert fake_objects.use_grid_calls == [{"name": "OSOBJA"}]

    # 2) Query chain must include filter + limit + all.
    assert fake_objects.query is not None
    assert ("filter", {"department_code__startswith": "AV"}) in fake_objects.query.calls
    assert ("limit", {"n": 10}) in fake_objects.query.calls
    assert fake_objects.query.calls[-1][0] == "all"

    # 3) Sanitizer must run.
    assert called["clean"] == 1

    # 4) _sync_entities must receive the items and use code as id.
    assert sync_args["name"] == "EAM Devices"
    assert sync_args["api_items"] == fake_items
    assert sync_args["cached_items"] == []
    assert sync_args["ids"] == ["AV-001", "AV-002"]


def test_sync_eam_positions_gridquery_field_map_contract(
    avtools_no_db: Any, monkeypatch: Any
) -> None:
    """Contract test: positions sync must keep the OSOBJP field_map stable."""

    import avtools.core.av_tools as av_mod

    captured: dict[str, Any] = {}

    class FakeGridQuery:
        def __init__(
            self, *, name: str, field_map: dict[str, str], model: Any, grid_type: str
        ) -> None:
            captured["name"] = name
            captured["field_map"] = dict(field_map)
            captured["model"] = model
            captured["grid_type"] = grid_type
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def filter(self, **kwargs: Any):
            self.calls.append(("filter", dict(kwargs)))
            return self

        def limit(self, n: int):
            self.calls.append(("limit", {"n": n}))
            return self

        def all(self):
            self.calls.append(("all", {}))
            return []

    monkeypatch.setattr(av_mod, "GridQuery", FakeGridQuery, raising=True)

    # Avoid DB access.
    avtools_no_db.dbod_helper.get_all_eam_positions = lambda: []  # type: ignore[attr-defined]

    # Stub _sync_entities.
    avtools_no_db._sync_entities = lambda **kwargs: None  # type: ignore[attr-defined]

    avtools_no_db.sync_eam_positions(
        position_grid="OSOBJP", department_code="AV", limit=5
    )

    assert captured["name"] == "OSOBJP"
    assert captured["grid_type"] == "LIST"

    assert captured["field_map"] == {
        "assigned_to": "assignedto",
        "alias": "alias",
        "category_code": "category",
        "class_code": "class",
        "code": "equipmentno",
        "comission_date": "commissiondate",
        "department_code": "department",
        "description": "equipmentdesc",
        "hierarchy_asset_code": "parentasset",
        "hierarchy_location_code": "location",
        "out_of_service": "outofservice",
        "primary_system": "primarysystem",
        "production": "production",
        "status_desc": "assetstatus_display",
        "variable2": "variable2",
    }
