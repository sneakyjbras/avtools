from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append(event)

    def warning(self, event: str, **kwargs: Any) -> None:
        pass

    def error(self, event: str, **kwargs: Any) -> None:
        pass

    def exception(self, event: str, **kwargs: Any) -> None:
        pass


class DummySanitizer:
    def sanitize_text(self, s: str) -> str:
        return s


@dataclass
class Item:
    id: str
    val: int


class DummyReporter:
    last: ClassVar["DummyReporter | None"] = None

    def __init__(self, logger: Any, *, entity: str, sanitize_text: Any) -> None:
        self.entity = entity
        self.updated: list[str] = []
        self.deleted: list[str] = []
        self.added: list[str] = []
        self.emitted = False
        DummyReporter.last = self

    def record_updated(
        self, *, id: str, old_item: Any, new_item: Any, changes: dict[str, Any]
    ) -> None:
        self.updated.append(id)

    def record_deleted(self, *, id: str, item: Any) -> None:
        self.deleted.append(id)

    def record_added(self, *, id: str, item: Any) -> None:
        self.added.append(id)

    def emit(self) -> None:
        self.emitted = True


def test_sync_entities_emits_report_when_logs_enabled(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod
    from avtools.core.av_tools import AVTools

    monkeypatch.setattr(av_mod, "SyncReportLogger", DummyReporter)

    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av.logs = True
    av._eam_sanitizer = DummySanitizer()

    # Make _diff_models deterministic for this test.
    def diff(old: Item, new: Item) -> dict[str, Any]:
        return {} if old.val == new.val else {"val": new.val}

    av._diff_models = diff  # type: ignore[assignment]

    cached = [Item(id="A", val=1), Item(id="B", val=1), Item(id="DEL", val=9)]
    api = [Item(id="A", val=1), Item(id="B", val=2), Item(id="INS", val=3)]

    captured: dict[str, Any] = {}

    def sync_func(*, to_insert: Any, to_update: Any, to_delete: Any) -> None:
        captured["to_insert"] = list(to_insert)
        captured["to_update"] = list(to_update)
        captured["to_delete"] = list(to_delete)

    av._sync_entities(
        api_items=api,
        cached_items=cached,
        get_id=lambda it: it.id,
        sync_func=sync_func,
        name="Dummy",
    )

    assert [it.id for it in captured["to_insert"]] == ["INS"]
    assert [pair[0].id for pair in captured["to_update"]] == ["B"]
    assert captured["to_delete"] == ["DEL"]

    rep = DummyReporter.last
    assert rep is not None
    assert rep.updated == ["B"]
    assert rep.deleted == ["DEL"]
    assert rep.added == ["INS"]
    assert rep.emitted is True
    assert "sync_done" in av.logger.infos
