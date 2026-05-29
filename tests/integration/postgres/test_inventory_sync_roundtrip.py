from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Engine

import pytest

from avtools.postgres.client import PostgresClient
from avtools.postgres.inventory.orm.eam_device import EAMDeviceORM
from avtools.postgres.inventory.orm.eam_position import EAMPositionORM
from avtools.postgres.inventory.orm.landb_ipaddress import (
    CachedIPAddress,
    LanDBIPAddressORM,
)


class DummyEq:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _mk_cached_ip(
    *,
    equipment_no: str,
    serial_number: str | None = None,
    ip: str | None = None,
    name: str | None = None,
    hostname: str | None = None,
    landb_serial: str | None = None,
    landb_description: str | None = None,
    building: str | None = None,
    floor: str | None = None,
    room: str | None = None,
    eq_class: str | None = None,
    category: str | None = None,
    manufacturer: str | None = None,
    model: str | None = None,
) -> CachedIPAddress:
    return CachedIPAddress(
        equipment_no=equipment_no,
        serial_number=serial_number,
        ip=ip,
        name=name,
        hostname=hostname,
        landb_serial=landb_serial,
        landb_description=landb_description,
        building=building,
        floor=floor,
        room=room,
        eq_class=eq_class,
        category=category,
        manufacturer=manufacturer,
        model=model,
    )


@pytest.mark.postgres
def test_sync_eam_devices_insert_update_delete_roundtrip(
    pg_engine: Engine, postgres_url: str
) -> None:
    client = PostgresClient(postgres_url)

    # Insert
    client.sync_eam_devices(
        to_insert=[
            DummyEq(
                code="EQ1",
                serial_number="SN1",
                class_code="CLASS1",
                category_code="CAT1",
                description="desc1",
                hierarchy_location_code="B001",
            )
        ],
        to_update=[],
        to_delete=[],
    )

    with client.Session() as session:
        rows = session.execute(select(EAMDeviceORM)).scalars().all()
        assert len(rows) == 1
        assert rows[0].equipment_no == "EQ1"
        assert rows[0].location == "B001"

    # Update
    client.sync_eam_devices(
        to_insert=[],
        to_update=[
            (
                DummyEq(
                    code="EQ1",
                    serial_number="SN1",
                    class_code="CLASS1",
                    category_code="CAT1",
                    description="desc1-updated",
                    hierarchy_location_code="B002",
                ),
                {"description": "desc1-updated"},
            )
        ],
        to_delete=[],
    )

    with client.Session() as session:
        row = session.get(EAMDeviceORM, "EQ1")
        assert row is not None
        assert row.equipment_desc == "desc1-updated"
        assert row.location == "B002"

    # Delete
    client.sync_eam_devices(to_insert=[], to_update=[], to_delete=["EQ1"])

    with client.Session() as session:
        rows = session.execute(select(EAMDeviceORM)).scalars().all()
        assert rows == []


@pytest.mark.postgres
def test_sync_eam_positions_insert_update_delete_roundtrip(
    pg_engine: Engine, postgres_url: str
) -> None:
    client = PostgresClient(postgres_url)

    client.sync_eam_positions(
        to_insert=[
            DummyEq(
                code="POS1",
                class_code="POSITION",
                category_code="ROOM",
                description="position desc",
                assigned_to="SPONSOR1",
                hierarchy_asset_code="PARENT1",
                status_desc="ACTIVE",
                hierarchy_location_code="BLDG-ROOM",
            )
        ],
        to_update=[],
        to_delete=[],
    )

    with client.Session() as session:
        row = session.get(EAMPositionORM, "POS1")
        assert row is not None
        assert row.equipment_desc == "position desc"
        assert row.location == "BLDG-ROOM"

    client.sync_eam_positions(
        to_insert=[],
        to_update=[
            (
                DummyEq(
                    code="POS1",
                    class_code="POSITION",
                    category_code="ROOM",
                    description="position desc v2",
                    assigned_to="SPONSOR1",
                    hierarchy_asset_code="PARENT1",
                    status_desc="ACTIVE",
                    hierarchy_location_code="BLDG-ROOM-2",
                ),
                {"description": "position desc v2"},
            )
        ],
        to_delete=[],
    )

    with client.Session() as session:
        row = session.get(EAMPositionORM, "POS1")
        assert row is not None
        assert row.equipment_desc == "position desc v2"
        assert row.location == "BLDG-ROOM-2"

    client.sync_eam_positions(to_insert=[], to_update=[], to_delete=["POS1"])

    with client.Session() as session:
        assert session.get(EAMPositionORM, "POS1") is None


@pytest.mark.postgres
def test_sync_landb_ipaddresses_insert_update_delete_roundtrip(
    pg_engine: Engine, postgres_url: str
) -> None:
    client = PostgresClient(postgres_url)

    # Seed with an existing row (via sync)
    client.sync_landb_devices(
        to_insert=[
            _mk_cached_ip(
                equipment_no="EQ1",
                serial_number="SN1",
                ip="192.0.2.1",
                name="old",
                hostname="host1",
                landb_serial="L-SN1",
                landb_description="LanDB name",
                building="B1",
                floor="1",
                room="R1",
                eq_class="Projector",
                category=None,
                manufacturer="Epson",
                model="X1",
            )
        ],
        to_update=[],
        to_delete=[],
    )

    # Update one field
    client.sync_landb_devices(
        to_insert=[],
        to_update=[
            (
                _mk_cached_ip(
                    equipment_no="EQ1",
                    serial_number="SN1",
                    ip="192.0.2.1",
                    name="updated",
                    hostname="host1",
                    landb_serial="L-SN1",
                    landb_description="LanDB name",
                    building="B1",
                    floor="1",
                    room="R1",
                    eq_class="Projector",
                    category=None,
                    manufacturer="Epson",
                    model="X1",
                ),
                {"name": "updated"},
            )
        ],
        to_delete=[],
    )

    with client.Session() as session:
        row = session.get(LanDBIPAddressORM, "EQ1")
        assert row is not None
        assert row.name == "updated"

    # Delete
    client.sync_landb_devices(to_insert=[], to_update=[], to_delete=["EQ1"])

    with client.Session() as session:
        assert session.get(LanDBIPAddressORM, "EQ1") is None


@pytest.mark.postgres
def test_sync_update_nonexistent_pk_is_noop(pg_engine: Engine, postgres_url: str) -> None:
    client = PostgresClient(postgres_url)

    client.sync_landb_devices(
        to_insert=[_mk_cached_ip(equipment_no="EQ1")],
        to_update=[],
        to_delete=[],
    )

    client.sync_landb_devices(
        to_insert=[],
        to_update=[(_mk_cached_ip(equipment_no="DOES_NOT_EXIST", name="x"), {"name": "x"})],
        to_delete=[],
    )

    with client.Session() as session:
        rows = session.execute(select(LanDBIPAddressORM)).scalars().all()
        assert len(rows) == 1
        assert rows[0].equipment_no == "EQ1"
