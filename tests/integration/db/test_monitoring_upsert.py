from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine

from avtools.postgres.monitoring.client import PostgresMonitoringClient
from avtools.postgres.monitoring.models.projector import ProjectorMonitoring
from avtools.postgres.monitoring.models.sysdescr import DeviceSysDescrMonitoring
from avtools.postgres.monitoring.orm.projector import ProjectorMonitoringORM
from avtools.postgres.monitoring.orm.sysdescr import DeviceSysDescrMonitoringORM


@pytest.mark.postgres
def test_monitoring_boot_creates_tables(pg_engine: Engine, postgres_url: str) -> None:
    _ = PostgresMonitoringClient(postgres_url)

    inspector = inspect(pg_engine)
    tables = set(inspector.get_table_names())
    assert "avtools_projector_monitoring" in tables
    assert "avtools_device_sysdescr_monitoring" in tables


@pytest.mark.postgres
def test_projector_upsert_insert_then_update_no_duplicates(
    pg_engine: Engine, postgres_url: str
) -> None:
    c = PostgresMonitoringClient(postgres_url)

    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

    n = c.upsert_projector_monitoring(
        [
            ProjectorMonitoring(
                equipment_no="EQ1",
                ip="192.0.2.1",
                firmware="fw1",
                power_status="on",
                updated_at=t1,
            )
        ]
    )
    assert n == 1

    n = c.upsert_projector_monitoring(
        [
            ProjectorMonitoring(
                equipment_no="EQ1",
                ip="192.0.2.2",
                firmware="fw2",
                power_status="off",
                updated_at=t2,
            )
        ]
    )
    assert n == 1

    # Verify DB state
    with c._session_maker() as session:  # intentional: integration test
        rows = session.execute(select(ProjectorMonitoringORM)).scalars().all()
    assert len(rows) == 1
    assert rows[0].equipment_no == "EQ1"
    assert rows[0].ip == "192.0.2.2"
    assert rows[0].firmware == "fw2"
    assert rows[0].power_status == "off"
    assert rows[0].updated_at == t2


@pytest.mark.postgres
def test_sysdescr_upsert_insert_then_update_no_duplicates(
    pg_engine: Engine, postgres_url: str
) -> None:
    c = PostgresMonitoringClient(postgres_url)

    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

    n = c.upsert_device_sysdescr_monitoring(
        [
            DeviceSysDescrMonitoring(
                equipment_no="EQ1",
                ip="192.0.2.1",
                eqclass="Projector",
                category=None,
                sysdescr="descr1",
                updated_at=t1,
            )
        ]
    )
    assert n == 1

    n = c.upsert_device_sysdescr_monitoring(
        [
            DeviceSysDescrMonitoring(
                equipment_no="EQ1",
                ip="192.0.2.2",
                eqclass="Projector",
                category="CAT",
                sysdescr="descr2",
                updated_at=t2,
            )
        ]
    )
    assert n == 1

    with c._session_maker() as session:
        rows = session.execute(select(DeviceSysDescrMonitoringORM)).scalars().all()

    assert len(rows) == 1
    assert rows[0].equipment_no == "EQ1"
    assert rows[0].ip == "192.0.2.2"
    assert rows[0].category == "CAT"
    assert rows[0].sysdescr == "descr2"
    assert rows[0].updated_at == t2


@pytest.mark.postgres
def test_monitoring_upsert_multiple_rows_mixed_insert_update(postgres_url: str) -> None:
    c = PostgresMonitoringClient(postgres_url)

    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

    c.upsert_projector_monitoring(
        [
            ProjectorMonitoring(
                equipment_no="EQ1",
                ip="1",
                firmware="a",
                power_status="on",
                updated_at=t1,
            ),
            ProjectorMonitoring(
                equipment_no="EQ2",
                ip="2",
                firmware="b",
                power_status="on",
                updated_at=t1,
            ),
        ]
    )

    # Second pass: update EQ1 and insert EQ3
    c.upsert_projector_monitoring(
        [
            ProjectorMonitoring(
                equipment_no="EQ1",
                ip="1",
                firmware="a2",
                power_status="off",
                updated_at=t2,
            ),
            ProjectorMonitoring(
                equipment_no="EQ3",
                ip="3",
                firmware="c",
                power_status="on",
                updated_at=t2,
            ),
        ]
    )

    with c._session_maker() as session:
        rows = {
            r.equipment_no: r
            for r in session.execute(select(ProjectorMonitoringORM)).scalars().all()
        }

    assert set(rows) == {"EQ1", "EQ2", "EQ3"}
    assert rows["EQ1"].firmware == "a2"
    assert rows["EQ1"].power_status == "off"


@pytest.mark.postgres
def test_monitoring_upsert_empty_returns_zero(postgres_url: str) -> None:
    c = PostgresMonitoringClient(postgres_url)

    assert c.upsert_projector_monitoring([]) == 0
    assert c.upsert_device_sysdescr_monitoring([]) == 0
