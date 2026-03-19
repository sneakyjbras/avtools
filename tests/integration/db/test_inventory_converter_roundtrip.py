from __future__ import annotations

from datetime import date, datetime

import pytest

from avtools.postgres.client import PostgresClient
from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress


class DummyEq:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.postgres
def test_eam_device_commission_date_roundtrip(postgres_url: str) -> None:
    client = PostgresClient(postgres_url)

    client.sync_eam_devices(
        to_insert=[
            DummyEq(
                code="EQ1",
                serial_number="SN1",
                class_code="CLASS1",
                category_code="CAT1",
                description="desc",
                hierarchy_location_code="B001",
                # upstream misspelling is the boundary here
                comission_date="07-Jan-2024",
            )
        ],
        to_update=[],
        to_delete=[],
    )

    devices = client.get_all_eam_devices()
    assert len(devices) == 1
    d = devices[0]

    assert getattr(d, "code") == "EQ1"
    # Upstream `eam_rest_client.Equipment` may expose this as a string OR datetime.
    # AVTools guarantees the *date* value; accept either representation.
    v = getattr(d, "comission_date")
    if isinstance(v, str):
        assert v == "07-Jan-2024"
    elif isinstance(v, datetime):
        assert v.date() == date(2024, 1, 7)
    elif isinstance(v, date):
        assert v == date(2024, 1, 7)
    else:
        raise AssertionError(f"Unexpected comission_date type: {type(v)!r}")


@pytest.mark.postgres
def test_eam_position_commission_date_roundtrip(postgres_url: str) -> None:
    client = PostgresClient(postgres_url)

    client.sync_eam_positions(
        to_insert=[
            DummyEq(
                code="POS1",
                class_code="POSITION",
                category_code="ROOM",
                description="pos",
                hierarchy_location_code="L001",
                comission_date="2024-01-07",  # ISO accepted
            )
        ],
        to_update=[],
        to_delete=[],
    )

    positions = client.get_all_eam_positions()
    assert len(positions) == 1
    p = positions[0]

    assert getattr(p, "code") == "POS1"
    v = getattr(p, "comission_date")
    if isinstance(v, str):
        assert v == "07-Jan-2024"
    elif isinstance(v, datetime):
        assert v.date() == date(2024, 1, 7)
    elif isinstance(v, date):
        assert v == date(2024, 1, 7)
    else:
        raise AssertionError(f"Unexpected comission_date type: {type(v)!r}")


@pytest.mark.postgres
def test_landb_ipaddress_roundtrip_fields_and_compare_whitelist(
    postgres_url: str,
) -> None:
    client = PostgresClient(postgres_url)

    client.sync_landb_devices(
        to_insert=[
            CachedIPAddress(
                equipment_no="EQ1",
                serial_number="SN1",
                ip="192.0.2.1",
                name="n",
                hostname="h",
                landb_serial="L-SN",
                landb_description="LanDB",
                building="B",
                floor="1",
                room="R",
                eq_class="Projector",
                category=None,
                manufacturer="Epson",
                model="X1",
            )
        ],
        to_update=[],
        to_delete=[],
    )

    rows = client.get_all_landb_devices()
    assert len(rows) == 1
    r = rows[0]

    assert r.equipment_no == "EQ1"
    assert r.ip == "192.0.2.1"
    assert r.building == "B"

    compare = r.avtools_compare_fields()
    # Spot-check the important compare keys
    assert "equipment_no" in compare
    assert "ip" in compare
    assert "eq_class" in compare
    assert "manufacturer" in compare
