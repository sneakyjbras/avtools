from __future__ import annotations

from datetime import date
from typing import Any

from eam_rest_client import Equipment
from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --- Base --------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base for EAM ORM models."""

    pass


# --- ORM ---------------------------------------------------------------------


class EAMPositionORM(Base):
    """EAM position node (positions form a parent/child tree in EAM).

    Note: this now maps the EAM REST client's `Equipment` model.
    """

    __tablename__ = "eam_positions"
    __table_args__ = (
        Index("ix_eam_positions_parentasset", "parentasset"),
        Index("ix_eam_positions_status", "assetstatus_display"),
        Index("ix_eam_positions_eqclass_category", "eqclass", "category"),
    )

    # Sizes are conservative; tune to upstream constraints if you know them.
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

    # --- Converters ----------------------------------------------------------

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
            return obj.dict()  # type: ignore[attr-defined]
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
        return {}

    @staticmethod
    def _parse_date(v: Any) -> date | None:
        if isinstance(v, date):
            return v
        if isinstance(v, str) and v:
            try:
                return date.fromisoformat(v)
            except ValueError:
                return None
        return None

    @classmethod
    def from_position(cls, position: Equipment) -> EAMPositionORM:
        """Create an ORM row from an `Equipment` domain model."""
        equipment_no = cls._get(position, "equipment_no", "equipmentno")
        if not equipment_no:
            data = cls._as_dict(position)
            equipment_no = data.get("equipment_no") or data.get("equipmentno")
        if not equipment_no:
            raise ValueError("Equipment missing equipment_no/equipmentno")

        cd_raw = cls._get(position, "commission_date", "commissiondate")
        cd_parsed = cls._parse_date(cd_raw)

        return cls(
            equipment_no=str(equipment_no),
            eq_class=cls._get(position, "eq_class", "eqclass", "class"),
            category=cls._get(position, "category"),
            equipment_desc=cls._get(
                position, "equipment_desc", "equipmentdesc", "equipment_description"
            ),
            sponsor=cls._get(position, "sponsor"),
            parent_asset=cls._get(position, "parent_asset", "parentasset"),
            commission_date=cd_parsed,
            asset_status_display=cls._get(
                position,
                "asset_status_display",
                "assetstatus_display",
                "status_desc",
                "status_description",
            ),
        )

    # Back-compat name (callers may still use `to_position()`).
    def to_position(self) -> Equipment:
        return self.to_equipment()

    def to_equipment(self) -> Equipment:
        """Convert this row back to an `Equipment` domain model."""
        payload = {
            "equipment_no": self.equipment_no,
            "eq_class": self.eq_class,
            "category": self.category,
            "equipment_desc": self.equipment_desc,
            "sponsor": self.sponsor,
            "parent_asset": self.parent_asset,
            "commission_date": self.commission_date,
            "asset_status_display": self.asset_status_display,
        }

        if hasattr(Equipment, "model_validate"):
            return Equipment.model_validate(payload)  # type: ignore[attr-defined]
        return Equipment(**payload)  # type: ignore[call-arg]

    # --- Debug ---------------------------------------------------------------

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
