"""Fixtures for HTTP contract integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


class DummyLogger:
    """Tiny logger capturing warnings/info for assertions."""

    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.warnings: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, dict(kwargs)))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, dict(kwargs)))

    def error(self, event: str, **kwargs: Any) -> None:
        self.errors.append((event, dict(kwargs)))

    def exception(self, event: str, **kwargs: Any) -> None:
        # For these tests we treat exception logging like error.
        self.errors.append((event, dict(kwargs)))


class DummySanitizer:
    """EAMTextSanitizer stand-in (contract tests don't care about cleaning)."""

    def clean_items(self, items: list[Any]) -> list[Any]:
        return items


class DummyDB:
    """PostgresClient stand-in to keep contract tests DB-free."""

    def get_all_eam_devices(self) -> list[Any]:
        return []

    def get_all_eam_positions(self) -> list[Any]:
        return []

    def get_all_landb_devices(self) -> list[Any]:
        return []

    def sync_eam_devices(self, **kwargs: Any) -> None:
        return None

    def sync_eam_positions(self, **kwargs: Any) -> None:
        return None

    def sync_landb_devices(self, **kwargs: Any) -> None:
        return None


@pytest.fixture()
def avtools_no_db() -> Any:
    """Create an AVTools instance without initializing the real Postgres client."""

    from avtools.core.av_tools import AVTools

    av = object.__new__(AVTools)
    av.dbod_url = "postgresql://unused"
    av.dbod_helper = DummyDB()
    av.logs = False
    av.logger = DummyLogger()
    av._landb_initialized = False
    av._eam_sanitizer = DummySanitizer()
    return av
