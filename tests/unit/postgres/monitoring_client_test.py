from __future__ import annotations

from datetime import datetime, timezone

import pytest

from avtools.postgres.monitoring.client import PostgresMonitoringClient
from avtools.postgres.monitoring.models.projector import ProjectorMonitoring
from avtools.postgres.monitoring.models.sysdescr import DeviceSysDescrMonitoring
from avtools.postgres.monitoring.orm.projector import ProjectorMonitoringORM
from avtools.postgres.monitoring.orm.sysdescr import DeviceSysDescrMonitoringORM


@pytest.fixture
def sqlite_monitoring_client(monkeypatch):
    # Force the client to take the safe ORM-merge fallback path (no Postgres insert).
    try:
        import sqlalchemy.dialects.postgresql as pgdial

        monkeypatch.setattr(pgdial, "insert", None)
    except Exception:
        pass

    return PostgresMonitoringClient("sqlite+pysqlite:///:memory:")


def test_upsert_projector_monitoring_empty_is_noop(sqlite_monitoring_client):
    assert sqlite_monitoring_client.upsert_projector_monitoring([]) == 0


def test_upsert_projector_monitoring_roundtrip(sqlite_monitoring_client):
    now = datetime(2026, 2, 23, tzinfo=timezone.utc)
    rec = ProjectorMonitoring(
        equipment_no="EQ1",
        ip="10.0.0.1",
        firmware="FW1",
        power_status="1",
        updated_at=now,
    )

    assert sqlite_monitoring_client.upsert_projector_monitoring([rec]) == 1

    # Verify row exists.
    with sqlite_monitoring_client._session_maker() as s:
        row = s.get(ProjectorMonitoringORM, "EQ1")
        assert row is not None
        assert row.firmware == "FW1"
        assert row.power_status == "1"
        assert row.ip == "10.0.0.1"


def test_upsert_sysdescr_monitoring_roundtrip(sqlite_monitoring_client):
    now = datetime(2026, 2, 23, tzinfo=timezone.utc)
    rec = DeviceSysDescrMonitoring(
        equipment_no="EQ2",
        ip="10.0.0.2",
        eqclass="AVD",
        category="AV-PRO",
        sysdescr="Linux",
        updated_at=now,
    )

    assert sqlite_monitoring_client.upsert_device_sysdescr_monitoring([rec]) == 1

    with sqlite_monitoring_client._session_maker() as s:
        row = s.get(DeviceSysDescrMonitoringORM, "EQ2")
        assert row is not None
        assert row.sysdescr == "Linux"
        assert row.eqclass == "AVD"
        assert row.category == "AV-PRO"
