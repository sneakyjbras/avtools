from __future__ import annotations

import datetime

import pytest

from avtools.postgres.orm.eam_device import (
    EAMDeviceORM,
    EAMDeviceORMError,
    _parse_any_date,
)


class DummyEq:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_parse_any_date_accepts_datetime_date_and_strings() -> None:
    dt = datetime.datetime(2025, 1, 2, 3, 4, 5)
    d = datetime.date(2025, 1, 2)

    assert _parse_any_date(dt) == datetime.date(2025, 1, 2)
    assert _parse_any_date(d) == datetime.date(2025, 1, 2)

    assert _parse_any_date("02-Jan-2025") == datetime.date(2025, 1, 2)
    assert _parse_any_date("2025-01-02") == datetime.date(2025, 1, 2)


def test_parse_any_date_invalid_returns_none() -> None:
    assert _parse_any_date("not a date") is None


def test_from_equipment_raises_when_code_missing() -> None:
    eq = DummyEq(code=None)

    with pytest.raises(EAMDeviceORMError):
        EAMDeviceORM.from_equipment(eq)  # type: ignore[arg-type]
