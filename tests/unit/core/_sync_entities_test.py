from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

from avtools.core.av_tools import AVTools


class DummyModel(BaseModel):
    equipment_no: str
    value: int
    year: int | None = None
    note: str | None = None


def make_avtools_for_tests() -> AVTools:
    # Bypass __init__ so we don't touch PostgresClient at all
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    return av


def run_sync(
    api_items: list[DummyModel],
    cached_items: list[DummyModel],
) -> dict[str, Any]:
    """Helper to run _sync_entities and capture inserts/updates/deletes."""
    av = make_avtools_for_tests()
    captured: dict[str, Any] = {}

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["insert"] = to_insert
        captured["update"] = to_update
        captured["delete"] = to_delete

    av._sync_entities(
        api_items=api_items,
        cached_items=cached_items,
        get_id=lambda m: m.equipment_no,
        sync_func=fake_sync,
        name="Dummy",
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

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["update"] == []
    assert set(captured["delete"]) == {"DEV-34", "DEV-38"}


def test_sync_entities_multiple_inserts_and_deletes():
    """
    More complex set difference:
    - Some cached IDs disappear → deletes
    - Some new IDs appear → inserts
    - Overlap with identical record → no update
    """
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

    captured = run_sync(api_items, cached_items)

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
        DummyModel(
            equipment_no="DEV-34", value=404, year=1911
        ),  # same id, different value
        DummyModel(
            equipment_no="DEV-2137",
            value=2137,
            year=2025,
        ),  # new id → insert
    ]

    captured = run_sync(api_items, cached_items)

    # Insert: DEV-2137 only
    assert [m.equipment_no for m in captured["insert"]] == ["DEV-2137"]

    # Delete: DEV-39 only
    assert captured["delete"] == ["DEV-39"]

    # Update: DEV-34 with diff on "value"
    assert len(captured["update"]) == 1
    updated_model, changes = captured["update"][0]
    assert updated_model.equipment_no == "DEV-34"
    assert changes == {"value": 404}


def test_sync_entities_multiple_updates_only():
    """
    Scenario: API and cache have the same IDs, but several records are updated.
    We should get only updates, no inserts or deletes.
    """
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

    # No inserts or deletes
    assert captured["insert"] == []
    assert captured["delete"] == []

    # Two updates: DEV-34 and DEV-39 (order not guaranteed)
    assert len(captured["update"]) == 2
    changes_by_id = {
        model.equipment_no: changes for model, changes in captured["update"]
    }

    assert changes_by_id["DEV-34"] == {"value": 404, "note": "new-34"}
    assert changes_by_id["DEV-39"] == {"note": "new-39"}


# ---------------------------------------------------------------------------
# Optional-field semantics and duplicate IDs
# ---------------------------------------------------------------------------


def test_sync_entities_optional_field_unset_does_not_trigger_update():
    """
    If the new model omits an optional field that was set in the cache,
    _diff_models (exclude_unset=True) should treat it as unchanged.
    """
    cached_items = [
        DummyModel(
            equipment_no="DEV-34",
            value=34,
            year=2025,
            note="keep-me",
        )
    ]
    # note is omitted (not explicitly set to None)
    api_items = [
        DummyModel(
            equipment_no="DEV-34",
            value=34,
            year=2025,
        )
    ]

    captured = run_sync(api_items, cached_items)

    # Same ID, same value/year, note omitted → no diff → no update
    assert captured["insert"] == []
    assert captured["delete"] == []
    assert captured["update"] == []


def test_sync_entities_optional_field_set_to_none_triggers_update():
    """
    If the new model explicitly sets an optional field to None, and the cache has a value,
    this should be treated as a real change.
    """
    cached_items = [
        DummyModel(
            equipment_no="DEV-34",
            value=34,
            year=2025,
            note="keep-me",
        )
    ]
    api_items = [
        DummyModel(
            equipment_no="DEV-34",
            value=34,
            year=2025,
            note=None,
        )
    ]

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["delete"] == []
    assert len(captured["update"]) == 1

    updated_model, changes = captured["update"][0]
    assert updated_model.equipment_no == "DEV-34"
    # We explicitly changed note to None
    assert changes == {"note": None}


def test_sync_entities_duplicate_ids_in_api_last_wins():
    """
    If API accidentally contains duplicate IDs, the last one in the sequence
    should win (dict comprehension semantics).
    """
    cached_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=1911),
    ]
    api_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=1911),  # same as cache
        DummyModel(
            equipment_no="DEV-34", value=404, year=1911
        ),  # later conflicting record
    ]

    captured = run_sync(api_items, cached_items)

    # No inserts or deletes, just one update
    assert captured["insert"] == []
    assert captured["delete"] == []
    assert len(captured["update"]) == 1

    updated_model, changes = captured["update"][0]
    assert updated_model.equipment_no == "DEV-34"
    # Last API entry "wins" and causes the update
    assert changes == {"value": 404}


def test_sync_entities_duplicate_ids_in_cache_last_cached_wins():
    """
    If cache contains duplicate IDs, the last cached record should be the baseline
    for diffing (dict comprehension semantics for cache_map).
    In this scenario, the API matches the *last* cached record, so no update occurs.
    """
    cached_items = [
        DummyModel(
            equipment_no="DEV-34",
            value=34,
            year=2025,
            note="first-version",
        ),
        DummyModel(
            equipment_no="DEV-34",
            value=34,
            year=2025,
            note="second-version",
        ),
    ]
    # API matches the last cached version exactly
    api_items = [
        DummyModel(
            equipment_no="DEV-34",
            value=34,
            year=2025,
            note="second-version",
        )
    ]

    captured = run_sync(api_items, cached_items)

    # Because the last cached record matches, there should be no update
    assert captured["insert"] == []
    assert captured["delete"] == []
    assert captured["update"] == []


def test_sync_entities_both_api_and_cache_empty_still_calls_sync():
    """
    Edge case: both API and cache are empty.
    We should still call sync_func once with all-empty lists.
    """
    api_items: list[DummyModel] = []
    cached_items: list[DummyModel] = []

    captured = run_sync(api_items, cached_items)

    assert captured["insert"] == []
    assert captured["update"] == []
    assert captured["delete"] == []


# ---------------------------------------------------------------------------
# Logging behaviour
# ---------------------------------------------------------------------------


def test_sync_entities_logs_first_run_message():
    """
    When cache is empty (first run), _sync_entities should log a first-run summary
    with correct inserted/updated/deleted counts.
    """
    av = make_avtools_for_tests()
    api_items = [
        DummyModel(equipment_no="DEV-34", value=34, year=2025),
        DummyModel(equipment_no="DEV-38", value=38, year=1978),
    ]
    cached_items: list[DummyModel] = []

    captured: dict[str, Any] = {}
    logs: list[str] = []

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["insert"] = to_insert
        captured["update"] = to_update
        captured["delete"] = to_delete

    def fake_info(msg: str) -> None:  # type: ignore[override]
        logs.append(msg)

    av.logger.info = fake_info  # type: ignore[assignment]

    av._sync_entities(
        api_items=api_items,
        cached_items=cached_items,
        get_id=lambda m: m.equipment_no,
        sync_func=fake_sync,
        name="Dummy",
    )

    # Sanity on data paths
    assert captured["insert"] == api_items
    assert captured["update"] == []
    assert captured["delete"] == []

    # Exactly one log line with first-run message and correct counts
    assert len(logs) == 1
    expected = "Dummy sync (first run): inserted=2, updated=0, deleted=0"
    assert logs[0] == expected


def test_sync_entities_logs_summary_with_counts():
    """
    For non-first-run cases, _sync_entities should log a completion summary
    with inserted/updated/deleted counts.
    """
    av = make_avtools_for_tests()

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

    captured: dict[str, Any] = {}
    logs: list[str] = []

    def fake_sync(*, to_insert, to_update, to_delete) -> None:
        captured["insert"] = to_insert
        captured["update"] = to_update
        captured["delete"] = to_delete

    def fake_info(msg: str) -> None:  # type: ignore[override]
        logs.append(msg)

    av.logger.info = fake_info  # type: ignore[assignment]

    av._sync_entities(
        api_items=api_items,
        cached_items=cached_items,
        get_id=lambda m: m.equipment_no,
        sync_func=fake_sync,
        name="Dummy",
    )

    # Data sanity: 2 inserts, 0 updates, 2 deletes
    insert_ids = {m.equipment_no for m in captured["insert"]}
    assert insert_ids == {"DEV-404", "DEV-2137"}
    assert set(captured["delete"]) == {"DEV-34", "DEV-39"}
    assert captured["update"] == []

    # Exactly one log line with "completed in" and correct counts
    assert len(logs) == 1
    msg = logs[0]

    assert msg.startswith("Dummy sync completed in ")
    assert "inserted=2" in msg
    assert "updated=0" in msg
    assert "deleted=2" in msg
