from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from avtools.postgres.client import PostgresClient


@pytest.mark.postgres
def test_inventory_drops_legacy_landb_ipaddresses_table(
    pg_engine: Engine, postgres_url: str
) -> None:
    # Create a legacy-shaped table (missing many expected columns)
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE landb_ipaddresses (
                equipmentno VARCHAR(64) PRIMARY KEY,
                serialnumber VARCHAR(128),
                ip VARCHAR(45)
            )
            """
        )

    # Init should detect legacy schema and drop/recreate
    _ = PostgresClient(postgres_url)

    inspector = inspect(pg_engine)
    cols = {c["name"] for c in inspector.get_columns("landb_ipaddresses")}

    expected = {
        "equipmentno",
        "serialnumber",
        "ip",
        "name",
        "hostname",
        "landb_serial",
        "landb_description",
        "eqclass",
        "manufacturer",
        "model",
        "building",
        "floor",
        "room",
    }
    assert expected.issubset(cols)


@pytest.mark.postgres
def test_inventory_drops_legacy_landb_location_table(pg_engine: Engine, postgres_url: str) -> None:
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE landb_location (
                equipmentno VARCHAR(64) PRIMARY KEY,
                room VARCHAR(64)
            )
            """
        )

    _ = PostgresClient(postgres_url)

    inspector = inspect(pg_engine)
    assert "landb_location" not in set(inspector.get_table_names())


@pytest.mark.postgres
def test_inventory_drops_legacy_eam_devices_missing_location(
    pg_engine: Engine, postgres_url: str
) -> None:
    # Create a legacy-shaped eam_devices table missing 'location'
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE eam_devices (
                equipmentno VARCHAR(64) PRIMARY KEY,
                serialnumber VARCHAR(128)
            )
            """
        )

    _ = PostgresClient(postgres_url)

    inspector = inspect(pg_engine)
    cols = {c["name"] for c in inspector.get_columns("eam_devices")}
    assert "location" in cols


@pytest.mark.postgres
def test_inventory_drops_legacy_eam_positions_missing_location(
    pg_engine: Engine, postgres_url: str
) -> None:
    with pg_engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE eam_positions (
                equipmentno VARCHAR(64) PRIMARY KEY,
                eqclass VARCHAR(64)
            )
            """
        )

    _ = PostgresClient(postgres_url)

    inspector = inspect(pg_engine)
    cols = {c["name"] for c in inspector.get_columns("eam_positions")}
    assert "location" in cols
