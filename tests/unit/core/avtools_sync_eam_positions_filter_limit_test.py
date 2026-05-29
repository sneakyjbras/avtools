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
class PosRec:
    code: str
    description: str | None = None
    department_code: str | None = None
    hierarchy_location_code: str | None = None


class DummyGridQuery:
    def __init__(self, *, raise_on_filter: bool = False, raise_on_limit: bool = False) -> None:
        self.raise_on_filter = raise_on_filter
        self.raise_on_limit = raise_on_limit

    def filter(self, **kwargs: Any) -> "DummyGridQuery":
        if self.raise_on_filter:
            raise RuntimeError("filter boom")
        return self

    def limit(self, n: int) -> "DummyGridQuery":
        if self.raise_on_limit:
            raise RuntimeError("limit boom")
        return self

    def all(self) -> list[PosRec]:
        return [PosRec(code="P-1")]


class DummySanitizer:
    def clean_items(self, items: list[Any]) -> list[Any]:
        return items


class DummyDB:
    def get_all_eam_positions(self) -> list[Any]:
        return []

    def sync_eam_positions(self, **kwargs: Any) -> None:
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


def test_sync_eam_positions_warns_when_department_filter_fails(
    monkeypatch: Any,
) -> None:
    import avtools.core.av_tools as av_mod

    dummy = DummyGridQuery(raise_on_filter=True)
    monkeypatch.setattr(av_mod, "GridQuery", lambda **kwargs: dummy)

    av = _make_avtools()

    called: dict[str, Any] = {}

    def fake_sync_entities(
        *, api_items: Any, cached_items: Any, get_id: Any, sync_func: Any, name: str
    ) -> None:
        called["api"] = list(api_items)
        called["name"] = name

    av._sync_entities = fake_sync_entities  # type: ignore[assignment]

    av.sync_eam_positions(position_grid="OSOBJP", department_code="AV", limit=None)

    assert "eam_position_department_code_filter_failed" in av.logger.warnings
    assert called["name"] == "EAM Positions"
    assert len(called["api"]) == 1


def test_sync_eam_positions_warns_when_limit_fails(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    dummy = DummyGridQuery(raise_on_limit=True)
    monkeypatch.setattr(av_mod, "GridQuery", lambda **kwargs: dummy)

    av = _make_avtools()

    called: dict[str, Any] = {}

    def fake_sync_entities(
        *, api_items: Any, cached_items: Any, get_id: Any, sync_func: Any, name: str
    ) -> None:
        called["api"] = list(api_items)

    av._sync_entities = fake_sync_entities  # type: ignore[assignment]

    av.sync_eam_positions(position_grid="OSOBJP", department_code="", limit=7)

    assert "eam_position_limit_failed" in av.logger.warnings
    assert len(called["api"]) == 1
