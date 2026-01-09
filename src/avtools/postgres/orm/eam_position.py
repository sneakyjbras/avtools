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

    Stores a small subset of EAM fields into the legacy `eam_positions` schema.

    Note: the DB schema is legacy and keeps equipment-ish column names.
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

    # IMPORTANT: keep this as a real date in DB/ORM
    commission_date: Mapped[date | None] = mapped_column(
        "commissiondate", Date, nullable=True
    )

    asset_status_display: Mapped[str | None] = mapped_column(
        "assetstatus_display", String(64), nullable=True
    )

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _get(obj: Any, *names: str) -> Any:
        """Return the first non-empty attribute/key found among `names`.

        Also checks `_original_response` (EamModel sometimes stores raw payload there).
        """
        for n in names:
            # dict-like payload
            if isinstance(obj, dict) and n in obj and obj[n] not in ("", None):
                return obj[n]

            # attribute on model
            if hasattr(obj, n):
                v = getattr(obj, n)
                if v not in ("", None):
                    return v

            # raw/original payload
            original = getattr(obj, "_original_response", None)
            if (
                isinstance(original, dict)
                and n in original
                and original[n] not in ("", None)
            ):
                return original[n]

        return None

    @staticmethod
    def _parse_any_date(v: Any) -> date | None:
        """Parse date-ish values into a `date` (same rules as devices)."""
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

            # 1) EAM: '07-Jan-2024'
            try:
                return datetime.strptime(s, "%d-%b-%Y").date()
            except ValueError:
                pass

            # 2) ISO date
            try:
                return date.fromisoformat(s[:10])
            except ValueError:
                pass

            # 3) ISO datetime (handle 'Z')
            try:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                return datetime.fromisoformat(s).date()
            except ValueError:
                return None

        return None

    @staticmethod
    def _format_eam_date(d: date | None) -> str | None:
        """Format a `date` as EAM expects ('DD-Mon-YYYY')."""
        if not d:
            return None
        return d.strftime("%d-%b-%Y")

    # --- Converters ----------------------------------------------------------

    @classmethod
    def from_position(cls, position: Any) -> EAMPositionORM:
        """Create an ORM row from a Position-like model.

        `position` may be:
          - a Position model (OSOBJP)
          - an Equipment-ish object/dict (legacy behaviour)
        """
        equipment_no = cls._get(position, "equipmentno", "code")
        if not equipment_no:
            raise ValueError("Position missing code")

        raw_commission = cls._get(
            position,
            "commissiondate",  # OSOBJP-style
            "comission_date",  # equipment-style typo
            "commission_date",
            "commissionDate",
            "comissionDate",
        )
        commission_date = cls._parse_any_date(raw_commission)

        return cls(
            equipment_no=str(equipment_no),
            eq_class=cls._get(position, "class_code", "eqclass"),
            category=cls._get(position, "category", "category_code"),
            equipment_desc=cls._get(position, "equipmentdesc", "description"),
            sponsor=cls._get(position, "sponsor"),
            parent_asset=cls._get(position, "parentasset", "hierarchy_asset_code"),
            commission_date=commission_date,
            asset_status_display=cls._get(
                position, "assetstatus_display", "status_desc"
            ),
        )

    # --- Back conversions ----------------------------------------------------

    def to_equipment(self) -> Equipment:
        """Convert this row to an `Equipment` domain model (subset only).

        Positions and devices are treated uniformly as Equipment downstream.
        """
        payload: dict[str, Any] = {
            "code": self.equipment_no,
            "class_code": self.eq_class,
            "category_code": self.category,
            "description": self.equipment_desc,
            "hierarchy_asset_code": self.parent_asset,
            "status_desc": self.asset_status_display,
        }

        eam_commission = self._format_eam_date(self.commission_date)
        if eam_commission:
            payload["comission_date"] = eam_commission

        return Equipment(**payload)

    # Optional: keep this if you still use Position elsewhere.
    # If not needed anymore, delete it to avoid confusion.
    def to_position_payload(self) -> dict[str, Any]:
        """Convert this row back to a Position-shaped payload (subset only)."""
        payload: dict[str, Any] = {
            "equipmentno": self.equipment_no,
            "class_code": self.eq_class,
            "category": self.category,  # legacy column reused for department in some flows
            "equipmentdesc": self.equipment_desc,
            "sponsor": self.sponsor,
            "parentasset": self.parent_asset,
            "assetstatus_display": self.asset_status_display,
        }
        if self.commission_date is not None:
            payload["commissiondate"] = self.commission_date.isoformat()
        return payload

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
