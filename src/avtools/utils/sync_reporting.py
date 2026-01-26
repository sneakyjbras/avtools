"""Sync reporting helper for compact structured logs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Row:
    """Internal representation of a change row for the sync report."""

    id: str
    serial_number: str | None
    changed_fields: list[str] = field(default_factory=list)


class SyncReportLogger:
    """Collect and emit a shell-friendly sync report.

    - Emits one log line per deleted/added/updated row.
    - Emits one compact summary line at the end.

    This keeps AVTools' core sync logic clean and avoids huge nested log payloads.
    """

    def __init__(
        self,
        logger: Any,
        *,
        entity: str,
        sanitize_text: Callable[[Any], str | None],
    ) -> None:
        """Create a sync report accumulator.

        Args:
            logger: Structlog-compatible logger instance.
            entity: Entity name used in emitted log records (e.g. ``"eam_device"``).
            sanitize_text: Function used to normalize serial numbers for logs.

        Returns:
            None.
        """
        self._logger = logger
        self._entity = entity
        self._sanitize_text = sanitize_text

        self._deleted: list[_Row] = []
        self._added: list[_Row] = []
        self._updated: list[_Row] = []

    def record_deleted(self, *, id: str, item: Any) -> None:
        """Record a deleted row.

        Args:
            id: Stable identifier for the row.
            item: Domain object that was deleted (used only to extract serial_number).

        Returns:
            None.
        """
        self._deleted.append(
            _Row(
                id=id,
                serial_number=self._sanitize_text(getattr(item, "serial_number", None)),
            )
        )

    def record_added(self, *, id: str, item: Any) -> None:
        """Record an added row.

        Args:
            id: Stable identifier for the row.
            item: Domain object that was added (used only to extract serial_number).

        Returns:
            None.
        """
        self._added.append(
            _Row(
                id=id,
                serial_number=self._sanitize_text(getattr(item, "serial_number", None)),
            )
        )

    def record_updated(
        self,
        *,
        id: str,
        old_item: Any,
        new_item: Any,
        changes: dict[str, Any],
    ) -> None:
        """Record an updated row.

        Args:
            id: Stable identifier for the row.
            old_item: Previous version of the domain object.
            new_item: Updated version of the domain object.
            changes: Dict of fields deemed different by the diff algorithm.

        Returns:
            None.
        """
        # changes only includes fields deemed different by AVTools. We still
        # double-check old vs new for cleanliness and to build changed_fields.
        changed_fields: list[str] = []
        for k, new_v in changes.items():
            old_v = getattr(old_item, k, None)
            if old_v == "":
                old_v = None
            if new_v == "":
                new_v = None
            if old_v != new_v:
                changed_fields.append(k)

        self._updated.append(
            _Row(
                id=id,
                serial_number=self._sanitize_text(
                    getattr(new_item, "serial_number", None)
                ),
                changed_fields=sorted(changed_fields),
            )
        )

    def emit(self) -> None:
        """Emit structured log lines for all recorded changes.

        Returns:
            None.

        Notes:
            Rows are sorted by id before emission to keep logs diff-friendly.
        """
        # Stable ordering keeps logs readable and diff-friendly.
        self._deleted.sort(key=lambda r: r.id)
        self._added.sort(key=lambda r: r.id)
        self._updated.sort(key=lambda r: r.id)

        for row in self._deleted:
            self._logger.info(
                "sync_report_deleted_row",
                entity=self._entity,
                id=row.id,
                serial_number=row.serial_number,
            )

        for row in self._added:
            self._logger.info(
                "sync_report_added_row",
                entity=self._entity,
                id=row.id,
                serial_number=row.serial_number,
            )

        for row in self._updated:
            self._logger.info(
                "sync_report_updated_row",
                entity=self._entity,
                id=row.id,
                serial_number=row.serial_number,
                changed_fields=row.changed_fields,
            )

        self._logger.info(
            "sync_report_summary",
            entity=self._entity,
            deleted=len(self._deleted),
            added=len(self._added),
            updated=len(self._updated),
            deleted_serials=[r.serial_number for r in self._deleted if r.serial_number],
            added_serials=[r.serial_number for r in self._added if r.serial_number],
            updated_serials=[r.serial_number for r in self._updated if r.serial_number],
        )
