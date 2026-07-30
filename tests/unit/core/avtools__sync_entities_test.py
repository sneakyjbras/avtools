from __future__ import annotations

from typing import Any

import pytest
import structlog
from pydantic.v1 import BaseModel

from avtools.core.av_tools import SYNC_MAX_DELETE_FRACTION, AVTools
from avtools.exception.errors import MassDeleteRefused


class DummyModel(BaseModel):
    equipment_no: str
    value: int
    year: int | None = None
    note: str | None = None


class DummySanitizer:
    """Minimal sanitizer stub used by AVTools._sync_entities/_diff_models in tests."""

    def sanitize_text(self, v: Any) -> Any:
        return v

    def sanitize_dict_in_place(
        self,
        data: dict[str, Any],
        *,
        compare_fields: Any | None = None,
    ) -> None:
        # No-op for unit tests.
        return


def make_avtools_for_tests() -> AVTools:
    # Bypass __init__ so we don't touch PostgresClient at all.
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    av.logs = False
    av._eam_sanitizer = DummySanitizer()
    return av


def run_sync(
    api_items: list[DummyModel],
    cached_items: list[DummyModel],
    **kwargs: Any,
) -> dict[str, Any]:
    """Helper to run _sync_entities and capture inserts/updates/deletes.

    Extra kwargs (``allow_deletes`` / ``allow_mass_delete``) are forwarded. Tests
    that deliberately exercise the raw set-difference on a tiny fixture pass
    ``allow_mass_delete=True``, since deleting 2 of 3 rows trips the mass-delete
    circuit breaker by design.
    """
    av = make_avtools_for_tests()
    captured: dict[str, Any] = {}

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["insert"] = list(to_insert)
        captured["update"] = list(to_update)
        captured["delete"] = list(to_delete)

    av._sync_entities(
        api_items=api_items,
        cached_items=cached_items,
        get_id=lambda m: m.equipment_no,
        sync_func=fake_sync,
        name="Dummy",
        **kwargs,
    )
    return captured


# ---------------------------------------------------------------------------
# Core set-difference behaviour
# ---------------------------------------------------------------------------


def test_sync_entities_first_run_inserts_all():
    """If cache is empty, everything from API is inserted."""
    api_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=2025),
        DummyModel(equipment_no="DEV-38", value=38, year=1978),
    ]
    cached_items: list[DummyModel] = []

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == api_items
    assert captured["update"] == []
    assert captured["delete"] == []


def test_sync_entities_no_changes_results_in_no_ops():
    """When API and cache match exactly, no inserts/updates/deletes."""
    cached_items = [
        DummyModel(equipment_no="DEV-14", value=14, year=2025),
        DummyModel(equipment_no="DEV-1911", value=1911, year=1978),
    ]
    api_items = [
        DummyModel(equipment_no="DEV-14", value=14, year=2025),
        DummyModel(equipment_no="DEV-1911", value=1911, year=1978),
    ]

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["update"] == []
    assert captured["delete"] == []


def test_sync_entities_delete_when_api_empty():
    """If API returns nothing but cache has entries, all should be deleted."""
    cached_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=1978),
        DummyModel(equipment_no="DEV-38", value=38, year=1911),
    ]
    api_items: list[DummyModel] = []

    # Wiping the whole cache is only reachable with the circuit breaker bypassed.
    captured = run_sync(api_items, cached_items, allow_mass_delete=True)

    assert captured["insert"] == []
    assert captured["update"] == []
    assert set(captured["delete"]) == {"DEV-34", "DEV-38"}


def test_sync_entities_multiple_inserts_and_deletes():
    """Mixed set difference: deletes + inserts + overlaps (no update)."""
    cached_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=1911),
        DummyModel(equipment_no="DEV-38", value=38, year=1978),
        DummyModel(equipment_no="DEV-39", value=39, year=2025),
    ]
    api_items = [
        # Overlap, unchanged → no update
        DummyModel(equipment_no="DEV-38", value=38, year=1978),
        # New devices → inserts
        DummyModel(equipment_no="DEV-404", value=404, year=2025),
        DummyModel(equipment_no="DEV-2137", value=2137, year=1978),
    ]

    # 2 of 3 cached rows deleted is above SYNC_MAX_DELETE_FRACTION, so the raw
    # set-difference behaviour is asserted with the breaker explicitly bypassed.
    captured = run_sync(api_items, cached_items, allow_mass_delete=True)

    # Inserts: DEV-404 and DEV-2137 (order not guaranteed)
    insert_ids = {m.equipment_no for m in captured["insert"]}
    assert insert_ids == {"DEV-404", "DEV-2137"}

    # Deletes: DEV-34 and DEV-39 (order not guaranteed)
    assert set(captured["delete"]) == {"DEV-34", "DEV-39"}

    # No updates – DEV-38 is unchanged
    assert captured["update"] == []


def test_sync_entities_inserts_and_deletes_and_updates():
    """Mixed case: one insert, one delete, one update."""
    cached_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=1911),  # will be updated
        DummyModel(equipment_no="DEV-39", value=39, year=1978),  # will be deleted
    ]

    api_items = [
        DummyModel(equipment_no="DEV-34", value=404, year=1911),
        DummyModel(equipment_no="DEV-2137", value=2137, year=2025),
    ]

    captured = run_sync(api_items, cached_items)

    assert {m.equipment_no for m in captured["insert"]} == {"DEV-2137"}
    assert set(captured["delete"]) == {"DEV-39"}

    # Update: DEV-34 with diff on "value"
    assert len(captured["update"]) == 1
    updated_model, changes = captured["update"][0]
    assert updated_model.equipment_no == "DEV-34"
    assert changes == {"value": 404}


def test_sync_entities_multiple_updates_only():
    """Same IDs, several records updated: only updates."""
    cached_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=2025, note="old-34"),
        DummyModel(equipment_no="DEV-38", value=38, year=1978, note="same-38"),
        DummyModel(equipment_no="DEV-39", value=39, year=1911, note="old-39"),
    ]
    api_items = [
        # value + note change → update
        DummyModel(equipment_no="DEV-34", value=404, year=2025, note="new-34"),
        # identical → no update
        DummyModel(equipment_no="DEV-38", value=38, year=1978, note="same-38"),
        # only note changes → update
        DummyModel(equipment_no="DEV-39", value=39, year=1911, note="new-39"),
    ]

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["delete"] == []

    assert len(captured["update"]) == 2
    changes_by_id = {model.equipment_no: changes for model, changes in captured["update"]}

    assert changes_by_id["DEV-34"] == {"value": 404, "note": "new-34"}
    assert changes_by_id["DEV-39"] == {"note": "new-39"}


# ---------------------------------------------------------------------------
# Optional-field semantics and duplicate IDs
# ---------------------------------------------------------------------------


def test_sync_entities_optional_field_unset_does_not_trigger_update():
    """Omitting an optional field should not trigger an update (exclude_unset=True)."""
    cached_items = [DummyModel(equipment_no="DEV-34", value=34, year=2025, note="keep-me")]

    # note is omitted (not explicitly set to None)
    api_items = [
        DummyModel(
            equipment_no="DEV-34",
            value=34,
            year=2025,
        )
    ]

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["delete"] == []
    assert captured["update"] == []


def test_sync_entities_optional_field_set_to_none_triggers_update():
    """Explicitly setting an optional field to None should trigger an update."""
    cached_items = [DummyModel(equipment_no="DEV-34", value=34, year=2025, note="keep-me")]
    api_items = [DummyModel(equipment_no="DEV-34", value=34, year=2025, note=None)]

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["delete"] == []
    assert len(captured["update"]) == 1

    updated_model, changes = captured["update"][0]
    assert updated_model.equipment_no == "DEV-34"
    assert changes == {"note": None}


def test_sync_entities_duplicate_ids_in_api_last_wins():
    """Duplicate IDs in the API: last one wins (api_map comprehension)."""
    cached_items = [DummyModel(equipment_no="DEV-34", value=34, year=1911)]
    api_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=1911),
        DummyModel(equipment_no="DEV-34", value=404, year=1911),
    ]

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["delete"] == []
    assert len(captured["update"]) == 1

    updated_model, changes = captured["update"][0]
    assert updated_model.equipment_no == "DEV-34"
    assert changes == {"value": 404}


def test_sync_entities_duplicate_ids_in_cache_last_cached_wins():
    """Duplicate IDs in the cache: last one wins (cache_map comprehension)."""
    cached_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=2025, note="first-version"),
        DummyModel(equipment_no="DEV-34", value=34, year=2025, note="second-version"),
    ]
    api_items = [DummyModel(equipment_no="DEV-34", value=34, year=2025, note="second-version")]

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["delete"] == []
    assert captured["update"] == []


def test_sync_entities_both_api_and_cache_empty_still_calls_sync():
    """Edge case: both API and cache empty → still calls sync_func with empties."""
    api_items: list[DummyModel] = []
    cached_items: list[DummyModel] = []

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["update"] == []
    assert captured["delete"] == []


# ---------------------------------------------------------------------------
# Logging behaviour
# ---------------------------------------------------------------------------


class StructuredLoggerRecorder:
    """Logger stub capturing structured structlog-style calls."""

    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.warnings: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[tuple[str, dict[str, Any]]] = []
        self.exceptions: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.infos.append((str(event), dict(kwargs)))

    def warning(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((str(event), dict(kwargs)))

    def error(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.errors.append((str(event), dict(kwargs)))

    def exception(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.exceptions.append((str(event), dict(kwargs)))


def test_sync_entities_logs_first_run_message():
    """First run (empty cache) should emit a sync_first_run structured log."""
    av = make_avtools_for_tests()

    api_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=2025),
        DummyModel(equipment_no="DEV-38", value=38, year=1978),
    ]
    cached_items: list[DummyModel] = []

    captured: dict[str, Any] = {}

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["insert"] = list(to_insert)
        captured["update"] = list(to_update)
        captured["delete"] = list(to_delete)

    logger = StructuredLoggerRecorder()
    av.logger = logger

    av._sync_entities(
        api_items=api_items,
        cached_items=cached_items,
        get_id=lambda m: m.equipment_no,
        sync_func=fake_sync,
        name="Dummy",
    )

    assert captured["insert"] == api_items
    assert captured["update"] == []
    assert captured["delete"] == []

    assert len(logger.infos) == 1
    event, kw = logger.infos[0]
    assert event == "sync_first_run"
    assert kw["entity"] == "Dummy"
    assert kw["inserted"] == 2
    assert kw["updated"] == 0
    assert kw["deleted"] == 0


def test_sync_entities_logs_summary_with_counts():
    """Non-first-run should emit a sync_done structured log with counts."""
    av = make_avtools_for_tests()

    cached_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=1911),
        DummyModel(equipment_no="DEV-38", value=38, year=1978),
        DummyModel(equipment_no="DEV-39", value=39, year=2025),
    ]
    api_items = [
        DummyModel(equipment_no="DEV-38", value=38, year=1978),
        DummyModel(equipment_no="DEV-404", value=404, year=2025),
        DummyModel(equipment_no="DEV-2137", value=2137, year=1978),
    ]

    captured: dict[str, Any] = {}

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["insert"] = list(to_insert)
        captured["update"] = list(to_update)
        captured["delete"] = list(to_delete)

    logger = StructuredLoggerRecorder()
    av.logger = logger

    av._sync_entities(
        api_items=api_items,
        cached_items=cached_items,
        get_id=lambda m: m.equipment_no,
        sync_func=fake_sync,
        name="Dummy",
        # 2 of 3 rows deleted would otherwise trip the mass-delete breaker.
        allow_mass_delete=True,
    )

    insert_ids = {m.equipment_no for m in captured["insert"]}
    assert insert_ids == {"DEV-404", "DEV-2137"}
    assert set(captured["delete"]) == {"DEV-34", "DEV-39"}
    assert captured["update"] == []

    assert len(logger.infos) == 1
    event, kw = logger.infos[0]
    assert event == "sync_done"
    assert kw["entity"] == "Dummy"
    assert kw["inserted"] == 2
    assert kw["updated"] == 0
    assert kw["deleted"] == 2
    assert "duration_s" in kw
    assert isinstance(kw["duration_s"], (int, float))


# ---------------------------------------------------------------------------
# Sync failure behaviour
# ---------------------------------------------------------------------------


class LoggerRecorder:
    """Logger stub to ensure no summary log is emitted on sync failure."""

    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.exceptions: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.infos.append((str(event), dict(kwargs)))

    def exception(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.exceptions.append((str(event), dict(kwargs)))


def test_sync_entities_sync_func_exception_propagates_and_skips_summary_log():
    """If sync_func raises, exception propagates and sync_done is not logged."""
    av = make_avtools_for_tests()

    cached_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=1911),
        DummyModel(equipment_no="DEV-38", value=38, year=1978),
    ]
    api_items = [
        DummyModel(equipment_no="DEV-38", value=404, year=1978),
        DummyModel(equipment_no="DEV-404", value=404, year=2025),
    ]

    logger = LoggerRecorder()
    av.logger = logger

    sync_calls: list[dict[str, Any]] = []

    def failing_sync(*, to_insert, to_update, to_delete) -> None:
        sync_calls.append(
            {
                "insert": list(to_insert),
                "update": list(to_update),
                "delete": list(to_delete),
            }
        )
        raise RuntimeError("dummy sync failure")

    with pytest.raises(RuntimeError, match="dummy sync failure"):
        av._sync_entities(
            api_items=api_items,
            cached_items=cached_items,
            get_id=lambda m: m.equipment_no,
            sync_func=failing_sync,
            name="Dummy",
        )

    assert len(sync_calls) == 1

    # No info logs (sync_done happens after sync_func returns).
    assert logger.infos == []


# ---------------------------------------------------------------------------
# W2 — delete guards: allow_deletes + mass-delete circuit breaker
#
# `to_delete = cached_ids - api_ids` makes an empty/partial API result
# indistinguishable from "the whole fleet was decommissioned". These guards make
# a silent wipe impossible.
# ---------------------------------------------------------------------------


def _run_sync_capturing(
    api_items: list[DummyModel],
    cached_items: list[DummyModel],
    **kwargs: Any,
) -> tuple[dict[str, Any], StructuredLoggerRecorder, AVTools]:
    av = make_avtools_for_tests()
    logger = StructuredLoggerRecorder()
    av.logger = logger  # type: ignore[assignment]

    captured: dict[str, Any] = {}

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["insert"] = list(to_insert)
        captured["update"] = list(to_update)
        captured["delete"] = list(to_delete)

    av._sync_entities(
        api_items=api_items,
        cached_items=cached_items,
        get_id=lambda m: m.equipment_no,
        sync_func=fake_sync,
        name="Dummy",
        **kwargs,
    )
    return captured, logger, av


def _fleet(n: int, *, start: int = 0) -> list[DummyModel]:
    return [DummyModel(equipment_no=f"DEV-{i}", value=i) for i in range(start, start + n)]


def test_sync_entities_allow_deletes_false_suppresses_every_delete():
    """A degraded upstream view must never delete, however large the diff."""
    cached_items = _fleet(10)
    api_items: list[DummyModel] = []

    captured, logger, _av = _run_sync_capturing(api_items, cached_items, allow_deletes=False)

    assert captured["delete"] == []
    assert captured["insert"] == []
    assert captured["update"] == []

    suppressed = [kw for ev, kw in logger.warnings if ev == "sync_deletes_suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["would_delete"] == 10
    assert suppressed[0]["cached"] == 10


def test_sync_entities_allow_deletes_false_still_applies_inserts_and_updates():
    """Freshness is kept even when deletes are withheld."""
    cached_items = [
        DummyModel(equipment_no="DEV-0", value=0),
        DummyModel(equipment_no="DEV-1", value=1),
    ]
    api_items = [
        DummyModel(equipment_no="DEV-0", value=999),  # update
        DummyModel(equipment_no="DEV-NEW", value=5),  # insert
    ]

    captured, _logger, _av = _run_sync_capturing(api_items, cached_items, allow_deletes=False)

    assert {m.equipment_no for m in captured["insert"]} == {"DEV-NEW"}
    assert len(captured["update"]) == 1
    # DEV-1 is absent from the partial API view but must survive.
    assert captured["delete"] == []


def test_sync_entities_mass_delete_is_refused_and_marks_the_run_failed():
    """Deleting most of the cache is refused, logged, and raises."""
    cached_items = _fleet(10)
    api_items = [
        DummyModel(equipment_no="DEV-0", value=999),  # update
        DummyModel(equipment_no="DEV-NEW", value=1),  # insert
    ]

    av = make_avtools_for_tests()
    logger = StructuredLoggerRecorder()
    av.logger = logger  # type: ignore[assignment]

    captured: dict[str, Any] = {}

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["insert"] = list(to_insert)
        captured["update"] = list(to_update)
        captured["delete"] = list(to_delete)

    with pytest.raises(MassDeleteRefused):
        av._sync_entities(
            api_items=api_items,
            cached_items=cached_items,
            get_id=lambda m: m.equipment_no,
            sync_func=fake_sync,
            name="Dummy",
        )

    # Inserts/updates were applied; the 9 deletes were NOT.
    assert {m.equipment_no for m in captured["insert"]} == {"DEV-NEW"}
    assert len(captured["update"]) == 1
    assert captured["delete"] == []

    refused = [kw for ev, kw in logger.errors if ev == "sync_mass_delete_refused"]
    assert len(refused) == 1
    assert refused[0]["would_delete"] == 9
    assert refused[0]["cached"] == 10
    assert refused[0]["delete_fraction"] == 0.9
    assert refused[0]["max_delete_fraction"] == SYNC_MAX_DELETE_FRACTION

    # The diff is still fully observable before the raise.
    assert any(ev == "sync_done" for ev, _ in logger.infos)


def test_sync_entities_mass_delete_can_be_bypassed_explicitly():
    """A deliberate mass decommission is still possible, opt-in only."""
    cached_items = _fleet(10)
    api_items: list[DummyModel] = []

    captured, logger, _av = _run_sync_capturing(api_items, cached_items, allow_mass_delete=True)

    assert len(captured["delete"]) == 10
    assert [ev for ev, _ in logger.errors] == []


def test_sync_entities_delete_exactly_at_threshold_is_allowed():
    """The breaker trips ABOVE the fraction, not at it (50% of 4 = 2 deletes)."""
    cached_items = _fleet(4)
    api_items = _fleet(2)  # DEV-0, DEV-1 survive; DEV-2, DEV-3 deleted

    captured, logger, _av = _run_sync_capturing(api_items, cached_items)

    assert set(captured["delete"]) == {"DEV-2", "DEV-3"}
    assert [ev for ev, _ in logger.errors] == []


def test_sync_entities_delete_just_over_threshold_is_refused():
    """3 of 5 cached rows (60%) is above the threshold."""
    cached_items = _fleet(5)
    api_items = _fleet(2)

    av = make_avtools_for_tests()
    logger = StructuredLoggerRecorder()
    av.logger = logger  # type: ignore[assignment]

    captured: dict[str, Any] = {}

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["delete"] = list(to_delete)

    with pytest.raises(MassDeleteRefused):
        av._sync_entities(
            api_items=api_items,
            cached_items=cached_items,
            get_id=lambda m: m.equipment_no,
            sync_func=fake_sync,
            name="Dummy",
        )

    assert captured["delete"] == []


def test_sync_entities_breaker_also_protects_the_eam_reconcilers():
    """_sync_entities is shared: EAM Devices/Positions/Rooms get the same guard."""
    for entity in ("EAM Devices", "EAM Positions", "EAM Rooms"):
        av = make_avtools_for_tests()
        logger = StructuredLoggerRecorder()
        av.logger = logger  # type: ignore[assignment]

        captured: dict[str, Any] = {}

        def fake_sync(*, to_insert, to_update, to_delete) -> None:
            captured["delete"] = list(to_delete)

        with pytest.raises(MassDeleteRefused):
            av._sync_entities(
                api_items=[],
                cached_items=_fleet(10),
                get_id=lambda m: m.equipment_no,
                sync_func=fake_sync,
                name=entity,
            )

        assert captured["delete"] == []
        assert [kw["entity"] for ev, kw in logger.errors if ev == "sync_mass_delete_refused"] == [
            entity
        ]


def test_sync_entities_first_run_is_unaffected_by_the_guards():
    """Empty cache: everything inserted, no deletes to guard, no raise."""
    api_items = _fleet(5)

    captured, logger, _av = _run_sync_capturing(api_items, [], allow_deletes=False)

    assert captured["insert"] == api_items
    assert captured["delete"] == []
    assert any(ev == "sync_first_run" for ev, _ in logger.infos)
