from __future__ import annotations

import datetime

import pytest

from avtools.postgres.inventory.orm.eam_position import (
    EAMPositionORM,
    EAMPositionORMError,
)


class DummyEq:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_from_equipment_maps_fields_and_parses_commission_date() -> None:
    eq = DummyEq(
        code="POS-1",
        class_code="AVS",
        category_code="AV-SCR",
        description="DESC",
        assigned_to="SPONSOR",
        hierarchy_asset_code="ASSET-1",
        hierarchy_location_code="BLDG/1/ROOM",
        status_desc="Installed",
        comission_date=datetime.datetime(2025, 1, 2, 0, 0),
    )

    orm = EAMPositionORM.from_equipment(eq)  # type: ignore[arg-type]

    assert orm.equipment_no == "POS-1"
    assert orm.eq_class == "AVS"
    assert orm.category == "AV-SCR"
    assert orm.equipment_desc == "DESC"
    assert orm.sponsor == "SPONSOR"
    assert orm.parent_asset == "ASSET-1"
    assert orm.location == "BLDG/1/ROOM"
    assert orm.commission_date == datetime.date(2025, 1, 2)
    assert orm.asset_status_display == "Installed"


def test_from_equipment_raises_when_code_missing() -> None:
    eq = DummyEq(code=None)
    with pytest.raises(EAMPositionORMError):
        EAMPositionORM.from_equipment(eq)  # type: ignore[arg-type]


def test_to_equipment_sets_expected_keys_and_compare_fields() -> None:
    orm = EAMPositionORM(
        equipment_no="POS-2",
        eq_class="AVS",
        category="AV-SCR",
        equipment_desc="X",
        sponsor="S",
        parent_asset="PA",
        commission_date=datetime.date(2025, 1, 2),
        asset_status_display="Installed",
        location="LOC",
    )

    eq = orm.to_equipment()

    assert getattr(eq, "code") == "POS-2"
    assert getattr(eq, "class_code") == "AVS"
    assert getattr(eq, "category_code") == "AV-SCR"
    assert getattr(eq, "description") == "X"
    assert getattr(eq, "assigned_to") == "S"
    assert getattr(eq, "hierarchy_asset_code") == "PA"
    assert getattr(eq, "hierarchy_location_code") == "LOC"
    assert getattr(eq, "status_desc") == "Installed"

    # Boundary spelling (upstream typo) must be present when we have a commission date.
    # Note: `eam_rest_client.Equipment` may parse the EAM-formatted date string into a
    # datetime/date object depending on its schema, so we assert on the semantic value.
    raw_commission = getattr(eq, "comission_date")
    if isinstance(raw_commission, str):
        assert raw_commission == "02-Jan-2025"
    elif isinstance(raw_commission, datetime.datetime):
        assert raw_commission.date() == datetime.date(2025, 1, 2)
    elif isinstance(raw_commission, datetime.date):
        assert raw_commission == datetime.date(2025, 1, 2)
    else:  # pragma: no cover
        raise AssertionError(f"Unexpected comission_date type: {type(raw_commission)!r}")

    compare = eq.avtools_compare_fields()  # type: ignore[attr-defined]
    assert "code" in compare
    assert "hierarchy_location_code" in compare
    assert "category_code" in compare
    assert "assigned_to" in compare
    assert "comission_date" in compare
