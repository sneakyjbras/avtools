from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

import avtools.postgres.inventory.client as mod
from avtools.exception.errors import PostgresInventoryClientError
from avtools.postgres.inventory.orm.eam_position import EAMPositionORM


class _Begin:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummySession:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.executed: list[Any] = []
        self.added: list[Any] = []
        self.rows: dict[str, Any] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        if self.fail:
            raise SQLAlchemyError("boom")
        return _Begin()

    def execute(self, stmt):
        if self.fail:
            raise SQLAlchemyError("boom")
        self.executed.append(stmt)

    def add_all(self, objs):
        if self.fail:
            raise SQLAlchemyError("boom")
        self.added.extend(list(objs))

    def get(self, orm_cls, pk):
        return self.rows.get(str(pk))


@dataclass
class DummyDomain:
    code: str
    class_code: str | None = None
    category_code: str | None = None
    description: str | None = None
    assigned_to: str | None = None
    hierarchy_asset_code: str | None = None
    hierarchy_location_code: str | None = None
    status_desc: str | None = None
    comission_date: str | None = None


def _make_client(session: DummySession) -> mod.PostgresClient:
    c = object.__new__(mod.PostgresClient)
    c.Session = lambda: session  # type: ignore[assignment]
    return c


def test_get_pk_prefers_known_attributes_and_raises_on_unknown() -> None:
    assert mod.PostgresClient._get_pk(DummyDomain(code="X")) == "X"

    class NoKeys:
        pass

    with pytest.raises(ValueError):
        mod.PostgresClient._get_pk(NoKeys())


def test_get_all_wraps_sqlalchemy_errors(monkeypatch: Any) -> None:
    session = DummySession(fail=True)
    c = _make_client(session)

    # Provide a fake orm class; execution fails before conversion.
    with pytest.raises(PostgresInventoryClientError):
        c._get_all(EAMPositionORM, lambda r: r, "Error querying EAM positions")


def test_sync_devices_applies_delete_update_insert_paths() -> None:
    session = DummySession()
    c = _make_client(session)

    # Existing row in DB
    existing = EAMPositionORM(
        equipment_no="P1",
        eq_class="OLD",
        category="OLD",
        equipment_desc=None,
        sponsor=None,
        parent_asset=None,
        commission_date=None,
        asset_status_display=None,
        location=None,
    )
    session.rows["P1"] = existing

    to_update = [(DummyDomain(code="P1", class_code="NEW", category_code="CAT"), {"x": 1})]
    to_insert = [DummyDomain(code="P2", class_code="AVD", category_code="AV-PRO")]
    to_delete = ["P9"]

    c._sync_devices(
        orm_cls=EAMPositionORM,
        from_domain=EAMPositionORM.from_equipment,
        id_getter=lambda d: d.code,
        to_insert=to_insert,
        to_update=to_update,
        to_delete=to_delete,
        pk_attr="equipment_no",
    )

    # Delete should have executed one statement
    assert session.executed, "expected delete statement"
    # Existing row should have been updated in-place
    assert existing.eq_class == "NEW"
    assert existing.category == "CAT"
    # Insert should have added one ORM instance
    assert len(session.added) == 1
    assert session.added[0].equipment_no == "P2"


def test_sync_devices_wraps_sqlalchemy_errors() -> None:
    session = DummySession(fail=True)
    c = _make_client(session)

    with pytest.raises(PostgresInventoryClientError):
        c._sync_devices(
            orm_cls=EAMPositionORM,
            from_domain=EAMPositionORM.from_equipment,
            id_getter=lambda d: d.code,
            to_insert=[],
            to_update=[],
            to_delete=["X"],
            pk_attr="equipment_no",
        )
