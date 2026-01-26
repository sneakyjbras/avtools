from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from avtools.utils.sync_reporting import SyncReportLogger


class CapturingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.calls.append((event, dict(kwargs)))


@dataclass
class Item:
    serial_number: str | None = None
    foo: str | None = None


def sanitize_text(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def test_sync_report_logger_emits_stable_order_and_summary_lists() -> None:
    logger = CapturingLogger()
    rep = SyncReportLogger(logger, entity="device", sanitize_text=sanitize_text)

    rep.record_deleted(id="b", item=Item(serial_number="  S2  "))
    rep.record_deleted(id="a", item=Item(serial_number="S1"))
    rep.record_added(id="c", item=Item(serial_number=None))

    old = Item(serial_number="", foo="x")
    new = Item(serial_number="  S3  ", foo="y")
    rep.record_updated(
        id="d", old_item=old, new_item=new, changes={"serial_number": None, "foo": "y"}
    )

    rep.emit()

    # Deleted rows should be emitted sorted by id
    assert logger.calls[0][0] == "sync_report_deleted_row"
    assert logger.calls[0][1]["id"] == "a"
    assert logger.calls[1][1]["id"] == "b"

    # Added rows
    assert logger.calls[2][0] == "sync_report_added_row"
    assert logger.calls[2][1]["id"] == "c"

    # Updated rows include changed_fields (serial_number: '' vs None treated as same)
    assert logger.calls[3][0] == "sync_report_updated_row"
    assert logger.calls[3][1]["id"] == "d"
    assert logger.calls[3][1]["changed_fields"] == ["foo"]

    # Summary
    assert logger.calls[-1][0] == "sync_report_summary"
    summary = logger.calls[-1][1]
    assert summary["deleted"] == 2
    assert summary["added"] == 1
    assert summary["updated"] == 1

    # Serial lists should be sanitized and should skip None
    assert summary["deleted_serials"] == ["S1", "S2"]
    assert summary["added_serials"] == []
    assert summary["updated_serials"] == ["S3"]
