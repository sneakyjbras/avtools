"""EAM Position ORM.

Defines the SQLAlchemy ORM table for cached EAM position snapshot rows, along
with conversion helpers to/from `eam_rest_client.Equipment`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from eam_rest_client import Equipment
from pydantic import PrivateAttr
from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from avtools.exception.errors import EAMPositionORMError

from avtools.exception.errors import EAMPositionORMError


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

        # EAM format: '07-Jan-2024'
        try:
            return datetime.strptime(s, "%d-%b-%Y").date()
        except ValueError:
            pass

        # ISO date / ISO datetime (handle trailing Z)
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
    """Equipment with AVTools-only private state for diffing."""

    _avtools_compare_fields: set[str] = PrivateAttr(default_factory=set)

    def avtools_compare_fields(self) -> set[str]:
        return set(self._avtools_compare_fields)


class EAMPositionORM(Base):
    """PostgreSQL snapshot of EAM position data (subset)."""

    __tablename__ = "eam_positions"
    __table_args__ = (
        Index("ix_eam_positions_parentasset", "parentasset"),
        Index("ix_eam_positions_status", "assetstatus_display"),
        Index("ix_eam_positions_eqclass_category", "eqclass", "category"),
        Index("ix_eam_positions_location", "location"),
    )

    equipment_no: Mapped[str] = mapped_column(
        "equipmentno", String(64), primary_key=True
    )
    eq_class: Mapped[str | None] = mapped_column("eqclass", String(64), nullable=True)

    # Legacy schema note: we often store department_code here (historical column name).
    category: Mapped[str | None] = mapped_column("category", String(64), nullable=True)

    equipment_desc: Mapped[str | None] = mapped_column(
        "equipmentdesc", Text, nullable=True
    )
    sponsor: Mapped[str | None] = mapped_column("sponsor", String(64), nullable=True)
    parent_asset: Mapped[str | None] = mapped_column(
        "parentasset", String(64), nullable=True
    )

    # Internal spelling is correct.
    commission_date: Mapped[date | None] = mapped_column(
        "commissiondate", Date, nullable=True
    )

    asset_status_display: Mapped[str | None] = mapped_column(
        "assetstatus_display", String(64), nullable=True
    )

    location: Mapped[str | None] = mapped_column("location", String(64), nullable=True)

    # Domain fields we consider when diffing against the DB row.
    _DB_COMPARE_FIELDS: set[str] = {
        "code",
        "class_code",
        "department_code",  # backed by legacy `category` column
        "description",
        "assigned_to",  # backed by `sponsor`
        "hierarchy_asset_code",
        "status_desc",
        "hierarchy_location_code",
        "comission_date",  # upstream typo (boundary)
    }

    @staticmethod
    def _get(obj: Any, name: str) -> Any:
        """Get attribute or dict key; treat '' as missing."""
        if isinstance(obj, dict):
            v = obj.get(name)
            return None if v in ("", None) else v
        v = getattr(obj, name, None)
        return None if v in ("", None) else v

    # --- Converters ----------------------------------------------------------

    @classmethod
    def from_equipment(cls, equipment: Equipment) -> EAMPositionORM:
        equipment_no = _none_if_blank(cls._get(equipment, "code"))
        if not equipment_no:
            raise EAMPositionORMError("Equipment missing required field: code")

        # Upstream typo only at the boundary:
        raw_commission = cls._get(equipment, "comission_date")

        # Legacy schema note: store department_code into `category`.
        dept = _none_if_blank(cls._get(equipment, "department_code"))

        return cls(
            equipment_no=equipment_no,
            eq_class=_none_if_blank(cls._get(equipment, "class_code")),
            category=dept,
            equipment_desc=_none_if_blank(cls._get(equipment, "description")),
            sponsor=_none_if_blank(cls._get(equipment, "assigned_to")),
            parent_asset=_none_if_blank(cls._get(equipment, "hierarchy_asset_code")),
            location=_none_if_blank(cls._get(equipment, "hierarchy_location_code")),
            commission_date=_parse_any_date(raw_commission),
            asset_status_display=_none_if_blank(cls._get(equipment, "status_desc")),
        )

    def to_equipment(self) -> Equipment:
        payload: dict[str, Any] = {
            "code": self.equipment_no,
            "class_code": self.eq_class,
            "department_code": self.category,  # legacy mapping
            "description": self.equipment_desc,
            "assigned_to": self.sponsor,  # legacy mapping
            "hierarchy_asset_code": self.parent_asset,
            "hierarchy_location_code": self.location,
            "status_desc": self.asset_status_display,
        }

        # Boundary spelling: only misspelled key goes into the domain model.
        eam_commission = _format_eam_date(self.commission_date)
        if eam_commission:
            payload["comission_date"] = eam_commission

        # Drop Nones so we don't spam defaults
        payload = {k: v for k, v in payload.items() if v is not None}

        eq = CachedEquipment(**payload)
        eq._avtools_compare_fields = set(self._DB_COMPARE_FIELDS)
        return eq

    def __repr__(self) -> str:
        return (
            "EAMPositionORM("
            f"equipment_no={self.equipment_no!r}, "
            f"parent={self.parent_asset!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"status={self.asset_status_display!r}"
            ")"
        )
