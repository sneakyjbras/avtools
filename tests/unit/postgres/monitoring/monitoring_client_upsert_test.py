from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

import avtools.postgres.monitoring.client as mod
from avtools.exception.errors import PostgresMonitoringClientError
from avtools.postgres.monitoring.models.projector import ProjectorMonitoring
from avtools.postgres.monitoring.models.sysdescr import DeviceSysDescrMonitoring


class DummySession:
    def __init__(self, *, fail_execute: bool = False, fail_merge: bool = False) -> None:
        self.fail_execute = fail_execute
        self.fail_merge = fail_merge
        self.executed: list[Any] = []
        self.merged: list[Any] = []
        self.committed = 0
        self.rolled_back = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, stmt):
        if self.fail_execute:
            raise SQLAlchemyError("boom")
        self.executed.append(stmt)

    def merge(self, obj):
        if self.fail_merge:
            raise SQLAlchemyError("boom")
        self.merged.append(obj)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class DummyLogger:
    def __init__(self) -> None:
        self.exception_calls: list[str] = []
        self.info_calls: list[str] = []

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exception_calls.append(event)

    def info(self, event: str, **kwargs: Any) -> None:
        self.info_calls.append(event)


def _make_client(session: DummySession) -> mod.PostgresMonitoringClient:
    c = object.__new__(mod.PostgresMonitoringClient)
    c.postgres_url = "postgres://dummy"
    c._session_maker = lambda: session  # type: ignore[assignment]
    return c


def test_upsert_projector_monitoring_empty_returns_zero() -> None:
    c = _make_client(DummySession())
    assert c.upsert_projector_monitoring([]) == 0


def test_upsert_projector_monitoring_insert_path(monkeypatch: Any) -> None:
    """Exercise the PostgreSQL insert + on_conflict path without a real DB."""

    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)

    session = DummySession()
    c = _make_client(session)

    rec = ProjectorMonitoring(
        equipment_no="EQ1",
        ip="1.2.3.4",
        firmware="FW",
        power_status="ON",
        updated_at=datetime.utcnow(),
    )

    n = c.upsert_projector_monitoring([rec])
    assert n == 1
    assert session.executed, "expected insert statement execution"
    assert session.committed == 1
    assert "postgres_monitoring_projector_upserted" in log.info_calls


def test_upsert_projector_monitoring_fallback_merge_path(monkeypatch: Any) -> None:
    """Force the fallback merge path by making dialect import fail."""

    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)

    session = DummySession()
    c = _make_client(session)

    # Force 'insert' import to fail.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("sqlalchemy.dialects.postgresql"):
            raise ImportError("no pg")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    rec = ProjectorMonitoring(
        equipment_no="EQ1",
        ip="1.2.3.4",
        firmware=None,
        power_status="OFF",
        updated_at=datetime.utcnow(),
    )

    assert c.upsert_projector_monitoring([rec]) == 1
    assert session.merged, "expected merge fallback"


def test_upsert_projector_monitoring_raises_postgres_error(monkeypatch: Any) -> None:
    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)

    session = DummySession(fail_execute=True)
    c = _make_client(session)

    rec = ProjectorMonitoring(
        equipment_no="EQ1",
        ip="1.2.3.4",
        firmware="FW",
        power_status="ON",
        updated_at=datetime.utcnow(),
    )

    with pytest.raises(PostgresMonitoringClientError):
        c.upsert_projector_monitoring([rec])

    assert session.rolled_back == 1
    assert "postgres_monitoring_upsert_failed" in log.exception_calls


def test_upsert_sysdescr_monitoring_merge_and_error(monkeypatch: Any) -> None:
    log = DummyLogger()
    monkeypatch.setattr(mod, "logger", log, raising=True)

    # Force merge path and failure.
    session = DummySession(fail_merge=True)
    c = _make_client(session)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("sqlalchemy.dialects.postgresql"):
            raise ImportError("no pg")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    rec = DeviceSysDescrMonitoring(
        equipment_no="EQ1",
        ip="1.2.3.4",
        eqclass="AVD",
        category="AV-PRO",
        sysdescr="descr",
        updated_at=datetime.utcnow(),
    )

    with pytest.raises(PostgresMonitoringClientError):
        c.upsert_device_sysdescr_monitoring([rec])

    assert session.rolled_back == 1
    assert "postgres_monitoring_sysdescr_upsert_failed" in log.exception_calls
