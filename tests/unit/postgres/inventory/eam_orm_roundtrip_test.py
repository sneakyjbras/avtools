from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from avtools.exception.errors import EAMDeviceORMError, EAMPositionORMError
from avtools.postgres.inventory.orm.eam_device import EAMDeviceORM
from avtools.postgres.inventory.orm.eam_position import EAMPositionORM


@dataclass
class DummyEquipment:
    """Minimal object mimicking eam_rest_client.Equipment for our converters."""

    code: str | None = None
    serial_number: str | None = None
    class_code: str | None = None
    category_code: str | None = None
    description: str | None = None
    model: str | None = None
    manufacturer_code: str | None = None
    hierarchy_position_code: str | None = None
    hierarchy_asset_code: str | None = None
    status_desc: str | None = None
    hierarchy_location_code: str | None = None
    department_code: str | None = None
    assigned_to: str | None = None
    # NOTE: upstream typo
    comission_date: object | None = None


def test_eam_device_from_equipment_requires_code() -> None:
    with pytest.raises(EAMDeviceORMError):
        EAMDeviceORM.from_equipment(DummyEquipment(code=None))  # type: ignore[arg-type]


def test_eam_device_date_parsing_accepts_multiple_formats() -> None:
    # EAM format
    orm = EAMDeviceORM.from_equipment(
        DummyEquipment(code="EQ1", comission_date="07-Jan-2024")  # type: ignore[arg-type]
    )
    assert orm.commission_date == date(2024, 1, 7)

    # ISO date
    orm = EAMDeviceORM.from_equipment(
        DummyEquipment(code="EQ2", comission_date="2024-02-03")  # type: ignore[arg-type]
    )
    assert orm.commission_date == date(2024, 2, 3)

    # ISO datetime with Z
    orm = EAMDeviceORM.from_equipment(
        DummyEquipment(code="EQ3", comission_date="2024-02-03T10:11:12Z")  # type: ignore[arg-type]
    )
    assert orm.commission_date == date(2024, 2, 3)

    # datetime object
    orm = EAMDeviceORM.from_equipment(
        DummyEquipment(code="EQ4", comission_date=datetime(2024, 3, 4, 5, 6, 7))  # type: ignore[arg-type]
    )
    assert orm.commission_date == date(2024, 3, 4)


def test_eam_device_to_equipment_emits_misspelled_date_key() -> None:
    row = EAMDeviceORM(
        equipment_no="EQ1",
        serial_number=None,
        eq_class=None,
        category=None,
        equipment_desc=None,
        model=None,
        manufacturer=None,
        position=None,
        parent_asset=None,
        commission_date=date(2024, 1, 7),
        asset_status_display=None,
        location=None,
        department_code=None,
    )
    eq = row.to_equipment()
    # Boundary key is misspelled in the returned domain object.
    assert getattr(eq, "comission_date", None) == datetime(2024, 1, 7)
    # Ensure our compare-field whitelist includes the boundary key.
    assert "comission_date" in eq.avtools_compare_fields()


def test_eam_position_from_equipment_supports_dict_payload_and_requires_code() -> None:
    with pytest.raises(EAMPositionORMError):
        EAMPositionORM.from_equipment({"code": ""})  # type: ignore[arg-type]

    row = EAMPositionORM.from_equipment(
        {
            "code": "P1",
            "class_code": "AVD",
            "category_code": "AV-PRO",
            "assigned_to": "SP",
            "hierarchy_asset_code": "PARENT",
            "hierarchy_location_code": "BLD",
            "status_desc": "OK",
            "comission_date": "2024-02-03T10:11:12Z",
        }
    )

    assert row.equipment_no == "P1"
    assert row.commission_date == date(2024, 2, 3)


def test_eam_position_to_equipment_roundtrip_and_repr() -> None:
    row = EAMPositionORM(
        equipment_no="P1",
        eq_class="AVD",
        category="AV-PRO",
        equipment_desc="desc",
        sponsor="SP",
        parent_asset="PA",
        commission_date=None,
        asset_status_display="OK",
        location="LOC",
    )
    eq = row.to_equipment()
    assert getattr(eq, "code") == "P1"
    assert getattr(eq, "assigned_to") == "SP"
    assert "comission_date" not in eq.__dict__.get("__fields_set__", set())
    # repr should include key identifiers.
    r = repr(row)
    assert "equipment_no" in r and "P1" in r
