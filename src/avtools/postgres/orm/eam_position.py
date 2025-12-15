from __future__ import annotations

from datetime import date, datetime
from typing import Any

from eam_rest_client import Equipment
from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --- Base --------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# --- ORM ---------------------------------------------------------------------


class EAMPositionORM(Base):
    """ORM mapping for EAM position rows.

    Stores a small subset of EAM REST `Equipment` fields into the legacy
    `eam_positions` schema.
    """

    __tablename__ = "eam_positions"
    __table_args__ = (
        Index("ix_eam_positions_parentasset", "parentasset"),
        Index("ix_eam_positions_status", "assetstatus_display"),
        Index("ix_eam_positions_eqclass_category", "eqclass", "category"),
    )

    # DB columns kept as-is (legacy schema)
    equipment_no: Mapped[str] = mapped_column(
        "equipmentno", String(64), primary_key=True
    )
    eq_class: Mapped[str | None] = mapped_column("eqclass", String(64), nullable=True)
    category: Mapped[str | None] = mapped_column("category", String(64), nullable=True)
    equipment_desc: Mapped[str | None] = mapped_column(
        "equipmentdesc", Text, nullable=True
    )
    sponsor: Mapped[str | None] = mapped_column("sponsor", String(64), nullable=True)
    parent_asset: Mapped[str | None] = mapped_column(
        "parentasset", String(64), nullable=True
    )
    commission_date: Mapped[date | None] = mapped_column(
        "commissiondate", Date, nullable=True
    )
    asset_status_display: Mapped[str | None] = mapped_column(
        "assetstatus_display", String(64), nullable=True
    )

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _get(obj: Any, *names: str) -> Any:
        """Return the first non-empty attribute/key found among `names`."""
        for n in names:
            if isinstance(obj, dict) and n in obj and obj[n] not in ("", None):
                return obj[n]
            if hasattr(obj, n):
                v = getattr(obj, n)
                if v not in ("", None):
                    return v
        return None

    @staticmethod
    def _as_dict(obj: Any) -> dict[str, Any]:
        """Best-effort conversion to a plain dict."""
        if hasattr(obj, "model_dump"):
            return obj.model_dump(by_alias=False)  # type: ignore[attr-defined]
        if hasattr(obj, "dict"):
            return obj.dict(by_alias=False)  # type: ignore[attr-defined]
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
        return {}

    @staticmethod
    def _parse_date(v: Any) -> date | None:
        if v is None or v == "":
            return None
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, str):
            try:
                return date.fromisoformat(v[:10])
            except ValueError:
                return None
        return None

    # --- Converters ----------------------------------------------------------

    @classmethod
    def from_position(cls, position: Equipment) -> EAMPositionORM:
        """Create an ORM row from an `Equipment` domain model.

        Mapping (Equipment -> legacy DB columns):
          equipment_no          <- code
          eq_class              <- class_code (fallback class_desc)
          category              <- category_code (fallback category_desc)
          equipment_desc        <- description
          sponsor               <- organization (fallback assigned_to / assigned_to_desc)
          parent_asset          <- hierarchy_position_code (fallback hierarchy_asset_code)
          commission_date       <- comission_date (note spelling)
          asset_status_display  <- status_desc (fallback state_desc)
        """
        equipment_no = cls._get(position, "code")
        if not equipment_no:
            data = cls._as_dict(position)
            equipment_no = data.get("code")
        if not equipment_no:
            raise ValueError("Equipment missing code")

        cd_raw = cls._get(
            position, "comission_date", "commission_date", "original_install_date"
        )
        cd_parsed = cls._parse_date(cd_raw)

        eq_class = cls._get(position, "class_code", "class_desc")
        category = cls._get(position, "category_code", "category_desc")
        sponsor = cls._get(position, "organization", "assigned_to_desc", "assigned_to")

        parent = cls._get(position, "hierarchy_position_code", "hierarchy_asset_code")

        return cls(
            equipment_no=str(equipment_no),
            eq_class=eq_class,
            category=category,
            equipment_desc=cls._get(position, "description"),
            sponsor=sponsor,
            parent_asset=parent,
            commission_date=cd_parsed,
            asset_status_display=cls._get(position, "status_desc", "state_desc"),
        )

    # Back-compat name: callers may still call `to_position()`.
    def to_position(self) -> Equipment:
        return self.to_equipment()

    def to_equipment(self) -> Equipment:
        """Convert this row back to an `Equipment` domain model (subset only)."""
        payload: dict[str, Any] = {
            "code": self.equipment_no,
            "class_code": self.eq_class,
            "category_code": self.category,
            "description": self.equipment_desc,
            "organization": self.sponsor,
            "hierarchy_position_code": self.parent_asset,
            "status_desc": self.asset_status_display,
        }
        if self.commission_date is not None:
            payload["comission_date"] = datetime(
                self.commission_date.year,
                self.commission_date.month,
                self.commission_date.day,
            )

        if hasattr(Equipment, "model_validate"):
            return Equipment.model_validate(payload)  # type: ignore[attr-defined]
        return Equipment(**payload)  # type: ignore[call-arg]

    def __repr__(self) -> str:
        return (
            "EAMPositionORM("
            f"equipment_no={self.equipment_no!r}, "
            f"parent_asset={self.parent_asset!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"status={self.asset_status_display!r}"
            ")"
        )
