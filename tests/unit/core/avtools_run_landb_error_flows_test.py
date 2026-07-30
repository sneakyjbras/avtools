from __future__ import annotations

from dataclasses import dataclass
from typing import Any


from avtools.exception.errors import MassDeleteRefused, PostgresError, UtilsError


class DummyLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.exceptions: list[str] = []
        self.infos: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append(event)

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


# ---------------------------------------------------------------------------
# W2 — degraded LanDB fetch must be non-destructive AND honest
# ---------------------------------------------------------------------------


class _Row:
    """Minimal cached/API row exposing the id field used by _landb_get_id."""

    def __init__(self, equipmentno: str) -> None:
        self.equipmentno = equipmentno


def _degraded_av(
    *,
    api_rows: list[Any],
    cached_rows: list[Any],
) -> tuple[Any, DummyDB, list[dict[str, Any]]]:
    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    db._cache = list(cached_rows)
    av = _make_avtools(db)

    av._init_landb_rest_client = lambda **kwargs: None  # type: ignore[assignment]

    def fetch(eam: Any) -> list[Any]:
        # Mimic _get_landb_ipaddresses: some chunk failed, so the returned view
        # is only PARTIAL — the flag is the only thing that says so.
        av._landb_fetch_degraded = True
        return list(api_rows)

    av._get_landb_ipaddresses = fetch  # type: ignore[assignment]

    sync_calls: list[dict[str, Any]] = []

    def fake_sync_entities(**kwargs: Any) -> None:
        sync_calls.append(dict(kwargs))

    av._sync_entities = fake_sync_entities  # type: ignore[assignment]
    return av, db, sync_calls


def test_run_landb_degraded_fetch_suppresses_deletes() -> None:
    """The whole point: a partial fetch must not be reconciled destructively."""
    av, _db, sync_calls = _degraded_av(
        api_rows=[_Row("EQ-1")],
        cached_rows=[_Row("EQ-1"), _Row("EQ-2"), _Row("EQ-3")],
    )

    status = av.run_landb("id", "sec", "aud")

    # The sync still runs (inserts/updates) but with deletes disabled.
    assert len(sync_calls) == 1
    assert sync_calls[0]["allow_deletes"] is False
    assert status == "failed_landb_fetch_degraded"
    assert _end_status(av) == "failed_landb_fetch_degraded"
    assert "landb_fetch_degraded" in av.logger.errors


def test_run_landb_degraded_fetch_does_not_delete_end_to_end() -> None:
    """Same scenario through the real _sync_entities: zero rows deleted."""
    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    db._cache = [_Row("EQ-1"), _Row("EQ-2"), _Row("EQ-3")]
    av = _make_avtools(db)

    class _Sanitizer:
        def sanitize_text(self, v: Any) -> Any:
            return v

        def sanitize_dict_in_place(self, data: Any, *, compare_fields: Any = None) -> None:
            return

    av._eam_sanitizer = _Sanitizer()
    av._init_landb_rest_client = lambda **kwargs: None  # type: ignore[assignment]

    def fetch(eam: Any) -> list[Any]:
        av._landb_fetch_degraded = True
        return []  # every chunk failed -> nothing came back

    av._get_landb_ipaddresses = fetch  # type: ignore[assignment]

    applied: list[dict[str, Any]] = []

    def sync_landb_devices(**kwargs: Any) -> None:
        applied.append({k: list(v) for k, v in kwargs.items()})

    db.sync_landb_devices = sync_landb_devices  # type: ignore[assignment]

    status = av.run_landb("id", "sec", "aud")

    assert status == "failed_landb_fetch_degraded"
    assert len(applied) == 1
    # Pre-fix this deleted the entire fleet table.
    assert applied[0]["to_delete"] == []
    assert applied[0]["to_insert"] == []


def test_run_landb_healthy_fetch_still_deletes_and_reports_ok() -> None:
    """Non-degraded runs keep today's reconciliation behaviour."""
    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    db._cache = [_Row("EQ-1")]
    av = _make_avtools(db)

    av._init_landb_rest_client = lambda **kwargs: None  # type: ignore[assignment]
    av._get_landb_ipaddresses = lambda eam: [_Row("EQ-1")]  # type: ignore[assignment]

    sync_calls: list[dict[str, Any]] = []
    av._sync_entities = lambda **kwargs: sync_calls.append(dict(kwargs))  # type: ignore[assignment]

    status = av.run_landb("id", "sec", "aud")

    assert sync_calls[0]["allow_deletes"] is True
    assert status == "ok"
    assert _end_status(av) == "ok"
    assert "landb_fetch_degraded" not in av.logger.errors


def test_run_landb_degraded_flag_is_reset_between_runs() -> None:
    """A stale flag from a previous run must not fail a healthy one."""
    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    db._cache = []
    av = _make_avtools(db)
    av._landb_fetch_degraded = True  # leftover from an earlier degraded run

    av._init_landb_rest_client = lambda **kwargs: None  # type: ignore[assignment]
    av._get_landb_ipaddresses = lambda eam: [_Row("EQ-1")]  # type: ignore[assignment]
    av._sync_entities = lambda **kwargs: None  # type: ignore[assignment]

    assert av.run_landb("id", "sec", "aud") == "ok"


def test_run_landb_reports_mass_delete_refused() -> None:
    """The circuit breaker's raise is classified with its own status."""
    db = DummyDB()
    db._eam = [EAMRec(serial_number="S1", description="D1")]
    db._cache = [_Row("EQ-1")]
    av = _make_avtools(db)

    av._init_landb_rest_client = lambda **kwargs: None  # type: ignore[assignment]
    av._get_landb_ipaddresses = lambda eam: []  # type: ignore[assignment]
    av._sync_entities = lambda **kwargs: (_ for _ in ()).throw(MassDeleteRefused("nope"))  # type: ignore[assignment]

    status = av.run_landb("id", "sec", "aud")

    assert status == "failed_sync_mass_delete_refused"
    assert _end_status(av) == "failed_sync_mass_delete_refused"
    assert "landb_sync_mass_delete_refused" in av.logger.exceptions


def test_run_landb_returns_status_on_every_failure_path() -> None:
    """Every early return must hand its status back to the CLI, not None."""
    db = DummyDB()
    db._eam = PostgresError("db down")
    av = _make_avtools(db)

    assert av.run_landb("id", "sec", "aud") == "failed_load_eam_devices_postgres_error"

    db2 = DummyDB()
    db2._eam = []
    av2 = _make_avtools(db2)
    assert av2.run_landb("id", "sec", "aud") == "skipped_no_eam_devices"
