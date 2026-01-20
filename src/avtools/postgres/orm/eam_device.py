from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog
from eam_rest_client import Equipment
from pydantic import PrivateAttr
from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    pass


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return str(value)


def _parse_any_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return datetime.strptime(s, "%d-%b-%Y").date()
        except ValueError:
            pass
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s).date()
        except ValueError:
            return None
    return None


def _format_eam_date(d: date | None) -> str | None:
    return d.strftime("%d-%b-%Y") if d else None


class CachedEquipment(Equipment):
    _avtools_compare_fields: set[str] = PrivateAttr(default_factory=set)

    def avtools_compare_fields(self) -> set[str]:
        return set(self._avtools_compare_fields)


class EAMDeviceORM(Base):
    __tablename__ = "eam_devices"
    __table_args__ = (
        Index("ix_eam_devices_serialnumber", "serialnumber"),
        Index("ix_eam_devices_eqclass_category", "eqclass", "category"),
        Index("ix_eam_devices_status", "assetstatus_display"),
        Index("ix_eam_devices_position", "position"),
    )

    equipment_no: Mapped[str] = mapped_column(
        "equipmentno", String(64), primary_key=True
    )

    serial_number: Mapped[str | None] = mapped_column(
        "serialnumber", String(128), nullable=True
    )
    eq_class: Mapped[str | None] = mapped_column("eqclass", String(64), nullable=True)
    category: Mapped[str | None] = mapped_column("category", String(64), nullable=True)
    equipment_desc: Mapped[str | None] = mapped_column(
        "equipmentdesc", Text, nullable=True
    )

    model: Mapped[str | None] = mapped_column("model", String(128), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(
        "manufacturer", String(128), nullable=True
    )

    position: Mapped[str | None] = mapped_column("position", String(64), nullable=True)
    parent_asset: Mapped[str | None] = mapped_column(
        "parentasset", String(64), nullable=True
    )

    # INTERNAL: spelled correctly
    commission_date: Mapped[date | None] = mapped_column(
        "commissiondate", Date, nullable=True
    )

    asset_status_display: Mapped[str | None] = mapped_column(
        "assetstatus_display", String(64), nullable=True
    )
    hierarchy_location_code: Mapped[str | None] = mapped_column(
        "hierarchy_location_code", String(64), nullable=True
    )
    department_code: Mapped[str | None] = mapped_column(
        "department_code", String(64), nullable=True
    )

    @classmethod
    def from_equipment(cls, device: Equipment) -> EAMDeviceORM:
        equipment_no = _none_if_blank(getattr(device, "code", None))
        if not equipment_no:
            raise ValueError("EAM Equipment missing required field: code")

        # UPSTREAM key is misspelled, keep it misspelled here (input side).
        raw_commission = getattr(device, "comission_date", None)

        return cls(
            equipment_no=equipment_no,
            serial_number=_none_if_blank(getattr(device, "serial_number", None)),
            eq_class=_none_if_blank(getattr(device, "class_code", None)),
            category=_none_if_blank(getattr(device, "category_code", None)),
            equipment_desc=_none_if_blank(getattr(device, "description", None)),
            model=_none_if_blank(getattr(device, "model", None)),
            manufacturer=_none_if_blank(getattr(device, "manufacturer_code", None)),
            position=_none_if_blank(getattr(device, "hierarchy_position_code", None)),
            parent_asset=_none_if_blank(getattr(device, "hierarchy_asset_code", None)),
            commission_date=_parse_any_date(raw_commission),
            asset_status_display=_none_if_blank(getattr(device, "status_desc", None)),
            hierarchy_location_code=_none_if_blank(
                getattr(device, "hierarchy_location_code", None)
            ),
            department_code=_none_if_blank(getattr(device, "department_code", None)),
        )

    def to_equipment(self) -> Equipment:
        payload: dict[str, Any] = {
            "code": self.equipment_no,
            "serial_number": self.serial_number,
            "class_code": self.eq_class,
            "category_code": self.category,
            "description": self.equipment_desc,
            "model": self.model,
            "manufacturer_code": self.manufacturer,
            "hierarchy_position_code": self.position,
            "hierarchy_asset_code": self.parent_asset,
            "status_desc": self.asset_status_display,
            "hierarchy_location_code": self.hierarchy_location_code,
            "department_code": self.department_code,
        }

        # OUTPUT side: ONLY misspelled key, never "commission_date".
        eam_commission = _format_eam_date(self.commission_date)
        if eam_commission:
            payload["comission_date"] = eam_commission

        payload = {k: v for k, v in payload.items() if v is not None}

        eq = CachedEquipment(**payload)

        # Compare only persisted fields (plus upstream misspelled date key).
        eq._avtools_compare_fields = {
            "code",
            "serial_number",
            "class_code",
            "category_code",
            "description",
            "model",
            "manufacturer_code",
            "hierarchy_position_code",
            "hierarchy_asset_code",
            "status_desc",
            "hierarchy_location_code",
            "department_code",
            "comission_date",
        } & set(eq.__fields__.keys())

        return eq
