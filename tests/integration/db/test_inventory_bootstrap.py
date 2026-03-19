from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from avtools.postgres.client import PostgresClient


@pytest.mark.postgres
def test_inventory_boot_creates_tables(pg_engine: Engine, postgres_url: str) -> None:
    # When
    _ = PostgresClient(postgres_url)

    # Then
    inspector = inspect(pg_engine)
    tables = set(inspector.get_table_names())
    assert "eam_devices" in tables
    assert "eam_positions" in tables
    assert "landb_ipaddresses" in tables


@pytest.mark.postgres
def test_inventory_schema_has_expected_columns(
    pg_engine: Engine, postgres_url: str
) -> None:
    _ = PostgresClient(postgres_url)

    inspector = inspect(pg_engine)

    eam_device_cols = {c["name"] for c in inspector.get_columns("eam_devices")}
    assert "equipmentno" in eam_device_cols
    assert "location" in eam_device_cols  # legacy-probe column

    eam_pos_cols = {c["name"] for c in inspector.get_columns("eam_positions")}
    assert "equipmentno" in eam_pos_cols
    assert "location" in eam_pos_cols  # legacy-probe column

    landb_cols = {c["name"] for c in inspector.get_columns("landb_ipaddresses")}
    # subset of the expected schema (full set asserted in legacy cleanup test)
    for col in (
        "equipmentno",
        "serialnumber",
        "ip",
        "hostname",
        "landb_serial",
        "landb_description",
        "building",
        "floor",
        "room",
        "eqclass",
        "manufacturer",
        "model",
    ):
        assert col in landb_cols
