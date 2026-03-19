from __future__ import annotations

from types import MethodType
from typing import Any

import avtools.core.av_tools as av_mod
from avtools.core.av_tools import AVTools
from avtools.exception.errors import PostgresError, UtilsError


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[str] = []
        self.exceptions: list[str] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, dict(kwargs)))

    def error(self, event: str, **kwargs: Any) -> None:
        self.errors.append(event)

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exceptions.append(event)


def _make_av() -> AVTools:
    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av.logs = False
    return av


def _end_status(av: AVTools) -> str:
    ends = [kw for (ev, kw) in av.logger.infos if ev == "avtools_run_eam_end"]
    assert ends
    return str(ends[-1]["status"])


def test_run_eam_marks_completed_with_errors_when_device_sync_fails(
    monkeypatch: Any,
) -> None:
    """If devices sync fails, positions sync still runs and status is completed_with_errors."""

    av = _make_av()

    monkeypatch.setattr(av_mod, "register_credentials", lambda **kw: None)

    def boom_devices(self: AVTools, **kwargs: Any) -> None:
        raise PostgresError("db")

    def ok_positions(self: AVTools, **kwargs: Any) -> None:
        return None

    av.sync_eam_devices = MethodType(boom_devices, av)  # type: ignore[assignment]
    av.sync_eam_positions = MethodType(ok_positions, av)  # type: ignore[assignment]

    av.run_eam("u", "p")

    assert _end_status(av) == "completed_with_errors"
    assert "eam_devices_postgres_error" in av.logger.exceptions


def test_run_eam_marks_completed_with_errors_when_position_sync_fails(
    monkeypatch: Any,
) -> None:
    av = _make_av()

    monkeypatch.setattr(av_mod, "register_credentials", lambda **kw: None)

    def ok_devices(self: AVTools, **kwargs: Any) -> None:
        return None

    def boom_positions(self: AVTools, **kwargs: Any) -> None:
        raise UtilsError("boom")

    av.sync_eam_devices = MethodType(ok_devices, av)  # type: ignore[assignment]
    av.sync_eam_positions = MethodType(boom_positions, av)  # type: ignore[assignment]

    av.run_eam("u", "p")

    assert _end_status(av) == "completed_with_errors"
    assert "eam_positions_utils_error" in av.logger.exceptions
