from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class DummyLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append(event)

    def info(self, event: str, **kwargs: Any) -> None:
        pass

    def error(self, event: str, **kwargs: Any) -> None:
        pass

    def exception(self, event: str, **kwargs: Any) -> None:
        pass


@dataclass
class EAMRec:
    code: str
    serial_number: str | None = None
    description: str | None = None
    department_code: str | None = None


class DummyQuery:
    def __init__(self, *, raise_on_filter: bool = False, raise_on_limit: bool = False) -> None:
        self.raise_on_filter = raise_on_filter
        self.raise_on_limit = raise_on_limit
        self.filtered_prefix: str | None = None
        self.limited_to: int | None = None

    def filter(self, **kwargs: Any) -> "DummyQuery":
        if self.raise_on_filter:
            raise RuntimeError("filter boom")
        self.filtered_prefix = kwargs.get("department_code__startswith")
        return self

    def limit(self, n: int) -> "DummyQuery":
        if self.raise_on_limit:
            raise RuntimeError("limit boom")
        self.limited_to = n
        return self

    def all(self) -> list[EAMRec]:
        return [EAMRec(code="EQ-1"), EAMRec(code="EQ-2")]


class DummyEquipment:
    class objects:
        @staticmethod
        def use_grid(name: str) -> DummyQuery:
            raise AssertionError("test must patch use_grid to a prepared DummyQuery")


class DummySanitizer:
    def clean_items(self, items: list[Any]) -> list[Any]:
        return items


class DummyDB:
    def get_all_eam_devices(self) -> list[Any]:
        return []

    def sync_eam_devices(self, **kwargs: Any) -> None:
        pass


def _make_avtools() -> Any:
    from avtools.core.av_tools import AVTools

    av = object.__new__(AVTools)
    av.logs = False
    av._landb_initialized = False
    av._eam_sanitizer = DummySanitizer()
    av.dbod_helper = DummyDB()
    av.logger = DummyLogger()
    return av


def test_sync_eam_devices_warns_when_department_filter_fails(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    q = DummyQuery(raise_on_filter=True)
    monkeypatch.setattr(DummyEquipment.objects, "use_grid", staticmethod(lambda name: q))
    monkeypatch.setattr(av_mod, "Equipment", DummyEquipment)

    av = _make_avtools()

    called: dict[str, Any] = {}

    def fake_sync_entities(
        *, api_items: Any, cached_items: Any, get_id: Any, sync_func: Any, name: str
    ) -> None:
        called["api"] = list(api_items)
        called["cached"] = list(cached_items)
        called["name"] = name

    av._sync_entities = fake_sync_entities  # type: ignore[assignment]

    av.sync_eam_devices(asset_grid="OSOBJA", department_code="AV", limit=None)

    assert "eam_asset_department_code_filter_failed" in av.logger.warnings
    assert called["name"] == "EAM Devices"
    assert len(called["api"]) == 2
    assert called["cached"] == []


def test_sync_eam_devices_warns_when_limit_fails(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    q = DummyQuery(raise_on_limit=True)
    monkeypatch.setattr(DummyEquipment.objects, "use_grid", staticmethod(lambda name: q))
    monkeypatch.setattr(av_mod, "Equipment", DummyEquipment)

    av = _make_avtools()

    called: dict[str, Any] = {}

    def fake_sync_entities(
        *, api_items: Any, cached_items: Any, get_id: Any, sync_func: Any, name: str
    ) -> None:
        called["api"] = list(api_items)

    av._sync_entities = fake_sync_entities  # type: ignore[assignment]

    av.sync_eam_devices(asset_grid="OSOBJA", department_code="", limit=123)

    assert "eam_limit_failed" in av.logger.warnings
    assert len(called["api"]) == 2
