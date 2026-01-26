from __future__ import annotations

from sqlalchemy import select

from avtools.postgres.client import PostgresClient
from avtools.postgres.orm.landb_ipaddress import CachedIPAddress, LanDBIPAddressORM


def test_sync_landb_devices_inserts_updates_and_deletes(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    client = PostgresClient(f"sqlite:///{db_path}")

    # Seed existing row
    with client.Session() as session:
        session.add(
            LanDBIPAddressORM(
                equipment_no="EQ1",
                serial_number="SN1",
                ip="192.0.2.1",
                name="old",
                hostname="h1",
                landb_serial=None,
                landb_description=None,
                building=None,
                floor=None,
                room=None,
                eq_class=None,
                manufacturer=None,
                model=None,
            )
        )
        session.add(
            LanDBIPAddressORM(
                equipment_no="EQ2",
                serial_number="SN2",
                ip="192.0.2.2",
                name="to_delete",
                hostname="h2",
                landb_serial=None,
                landb_description=None,
                building=None,
                floor=None,
                room=None,
                eq_class=None,
                manufacturer=None,
                model=None,
            )
        )
        session.commit()

    to_insert = [
        CachedIPAddress(
            equipment_no="EQ3",
            serial_number="SN3",
            ip="192.0.2.3",
            name="new",
            hostname="h3",
            landb_serial=None,
            landb_description=None,
            building=None,
            floor=None,
            room=None,
            eq_class=None,
            manufacturer=None,
            model=None,
        )
    ]

    to_update = [
        (
            CachedIPAddress(
                equipment_no="EQ1",
                serial_number="SN1",
                ip="192.0.2.1",
                name="updated",
                hostname="h1",
                landb_serial=None,
                landb_description=None,
                building=None,
                floor=None,
                room=None,
                eq_class=None,
                manufacturer=None,
                model=None,
            ),
            {"name": "updated"},
        )
    ]

    client.sync_landb_devices(
        to_insert=to_insert, to_update=to_update, to_delete=["EQ2"]
    )

    with client.Session() as session:
        stmt = select(LanDBIPAddressORM)
        rows = {r.equipment_no: r for r in session.execute(stmt).scalars().all()}

    assert set(rows) == {"EQ1", "EQ3"}
    assert rows["EQ1"].name == "updated"
    assert rows["EQ3"].name == "new"
