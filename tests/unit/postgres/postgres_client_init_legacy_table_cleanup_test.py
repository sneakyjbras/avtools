from __future__ import annotations

import sqlite3

from avtools.postgres.client import PostgresClient


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_postgres_client_drops_legacy_tables_and_recreates_with_expected_columns(
    tmp_path,
) -> None:
    db_path = tmp_path / "test.db"

    conn = sqlite3.connect(str(db_path))
    try:
        # Legacy landb_ipaddresses table missing many expected columns
        conn.execute(
            "CREATE TABLE landb_ipaddresses (equipmentno TEXT PRIMARY KEY, serialnumber TEXT)"
        )
        # Legacy merged table should be dropped unconditionally
        conn.execute("CREATE TABLE landb_location (dummy TEXT)")

        # Legacy EAM tables missing 'location'
        conn.execute("CREATE TABLE eam_devices (equipmentno TEXT PRIMARY KEY, serialnumber TEXT)")
        conn.execute("CREATE TABLE eam_positions (equipmentno TEXT PRIMARY KEY, serialnumber TEXT)")
        conn.commit()
    finally:
        conn.close()

    # Run the schema management in __init__ (sqlite is enough for unit tests)
    client = PostgresClient(f"sqlite:///{db_path}")

    # Check resulting schema
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        assert "landb_location" not in tables
        assert "landb_ipaddresses" in tables
        assert "eam_devices" in tables
        assert "eam_positions" in tables

        # Ensure tables were recreated with expected columns
        assert "location" in _columns(conn, "eam_devices")
        assert "location" in _columns(conn, "eam_positions")

        # LanDB IP table should have at least these
        cols = _columns(conn, "landb_ipaddresses")
        assert "ip" in cols
        assert "building" in cols
        assert "hostname" in cols
    finally:
        conn.close()

    # silence unused
    assert client
