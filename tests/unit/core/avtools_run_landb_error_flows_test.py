from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from avtools.exception.errors import PostgresError, UtilsError


class DummyLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.exceptions: list[str] = []
        self.infos: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        pass

    def error(self, event: str, **kwargs: Any) -> None:
        self.errors.append(event)

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exceptions.append(event)


@dataclass
class EAMRec:
    serial_number: str | None = None
    description: str | None = None
    code: str = "EQ"


class DummyDB:
    def __init__(self) -> None:
        self._eam: list[Any] | Exception = []
        self._cache: list[Any] | Exception = []
        self.synced: bool = False

    def get_all_eam_devices(self) -> list[Any]:
        if isinstance(self._eam, Exception):
            raise self._eam
        return list(self._eam)

    def get_all_landb_devices(self) -> list[Any]:
        if isinstance(self._cache, Exception):
            raise self._cache
        return list(self._cache)

    def sync_landb_devices(self, **kwargs: Any) -> None:
        self.synced = True


def _make_avtools(db: DummyDB) -> Any:
    from avtools.core.av_tools import AVTools

    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av.logs = False
    av._landb_initialized = False
    av._eam_sanitizer = object()
    av.dbod_helper = db
    return av


def _end_status(av: Any) -> str:
    ends = [kw for (ev, kw) in av.logger.infos if ev == "avtools_run_landb_end"]
    assert ends
    return str(ends[-1].get("status"))


def test_run_landb_fails_when_loading_eam_devices_postgres_error() -> None:
    db = DummyDB()
    db._eam = PostgresError("db down")
    av = _make_avtools(db)

    av.run_landb("id", "sec", "aud")

    assert "landb_load_eam_devices_postgres_error" in av.logger.exceptions
    assert _end_status(av).startswith("failed_load_eam_devices_postgres_error")


def test_run_landb_fails_when_client_init_token_expired(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    class DummyTokenExpired(Exception):
        pass

    monkeypatch.setattr(av_mod, "TokenExpired", DummyTokenExpired)

    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    av = _make_avtools(db)

    av._init_landb_rest_client = lambda **kwargs: (_ for _ in ()).throw(DummyTokenExpired("expired"))  # type: ignore[assignment]
    av._get_landb_ipaddresses = lambda eam: (_ for _ in ()).throw(AssertionError("should not fetch"))  # type: ignore[assignment]

    av.run_landb("id", "sec", "aud")

    assert "landb_token_expired" in av.logger.errors
    assert _end_status(av) == "failed_client_init_token_expired"


def test_run_landb_fails_when_fetch_validation_error(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    class DummyValidationError(Exception):
        def errors(self) -> list[dict[str, Any]]:
            return [{"loc": ["x"], "msg": "bad", "type": "value_error"}]

    monkeypatch.setattr(av_mod, "DataAwareValidationError", DummyValidationError)

    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    av = _make_avtools(db)

    av._init_landb_rest_client = lambda **kwargs: None  # type: ignore[assignment]
    av._get_landb_ipaddresses = lambda eam: (_ for _ in ()).throw(DummyValidationError("bad"))  # type: ignore[assignment]

    av.run_landb("id", "sec", "aud")

    assert "landb_validation_error" in av.logger.errors
    assert _end_status(av) == "failed_fetch_validation_error"


def test_run_landb_fails_when_loading_cached_devices_postgres_error() -> None:
    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    db._cache = PostgresError("cache down")
    av = _make_avtools(db)

    av._init_landb_rest_client = lambda **kwargs: None  # type: ignore[assignment]
    av._get_landb_ipaddresses = lambda eam: [object()]  # type: ignore[assignment]

    av.run_landb("id", "sec", "aud")

    assert "landb_load_cached_devices_postgres_error" in av.logger.exceptions
    assert _end_status(av) == "failed_load_cached_devices_postgres_error"


def test_run_landb_fails_when_sync_utils_error() -> None:
    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    db._cache = []
    av = _make_avtools(db)

    av._init_landb_rest_client = lambda **kwargs: None  # type: ignore[assignment]
    av._get_landb_ipaddresses = lambda eam: [object()]  # type: ignore[assignment]
    av._sync_entities = lambda **kwargs: (_ for _ in ()).throw(UtilsError("boom"))  # type: ignore[assignment]

    av.run_landb("id", "sec", "aud")

    assert "landb_sync_utils_error" in av.logger.exceptions
    assert _end_status(av) == "failed_sync_utils_error"
