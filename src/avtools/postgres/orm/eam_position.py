from __future__ import annotations

from datetime import date, datetime
from typing import Any

from eam_rest_client import Equipment, Position
from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --- Base --------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# --- ORM ---------------------------------------------------------------------


class EAMPositionORM(Base):
    """ORM mapping for EAM position rows.

    Stores a small subset of EAM fields into the legacy `eam_positions` schema.

    Important: the DB schema is legacy and keeps equipment-ish column names, but
    this mapper supports both:
      - Position (OSOBJP / REST positions)
      - Equipment (OSOBJA / legacy behaviour)
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
        """Return the first non-empty attribute/key found among `names`.

        Also checks `_original_response` (EamModel stores raw payload there sometimes).
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
    def _as_dict(obj: Any) -> dict[str, Any]:
        """Best-effort conversion to a plain dict (merges alias + non-alias when possible)."""
        if isinstance(obj, dict):
            return obj

        # Pydantic v2
        if hasattr(obj, "model_dump"):
            try:
                d1 = obj.model_dump(by_alias=False, exclude_none=True)  # type: ignore[attr-defined]
                d2 = obj.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
                return {**d1, **d2}
            except TypeError:
                return obj.model_dump(by_alias=False)  # type: ignore[attr-defined]

        # Pydantic v1
        if hasattr(obj, "dict"):
            try:
                d1 = obj.dict(by_alias=False, exclude_none=True)  # type: ignore[attr-defined]
                d2 = obj.dict(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
                return {**d1, **d2}
            except TypeError:
                return obj.dict(by_alias=False)  # type: ignore[attr-defined]

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
    def from_position(
        cls, position: Equipment | Position | dict[str, Any]
    ) -> EAMPositionORM:
        """Create an ORM row from a `Position` (preferred) or `Equipment` (legacy) domain model.

        Mapping into legacy DB columns:
          equipment_no          <- code / positioncode / equipmentno
          eq_class              <- class_code (fallback class_desc / class / eqclass)
          category              <- department_code (Position) OR category_code (Equipment) (fallbacks apply)
          equipment_desc        <- description / positiondesc / equipmentdesc
          sponsor               <- organization / sponsor / assigned_to(_desc) (fallback department)
          parent_asset          <- hierarchy_position_code / parentposition / hierarchy_asset_code / parentasset
          commission_date       <- comission_date / commission_date / commissiondate / original_install_date
          asset_status_display  <- status_desc / positionstatus_display / assetstatus_display / state_desc
        """
        equipment_no = cls._get(
            position,
            # normalized
            "code",
            # position-ish
            "position_code",
            "positioncode",
            "positionno",
            "position_no",
            # legacy equipment-ish
            "equipment_no",
            "equipmentno",
            "id",
        )

        if not equipment_no:
            data = cls._as_dict(position)
            equipment_no = (
                data.get("code")
                or data.get("position_code")
                or data.get("positioncode")
                or data.get("equipment_no")
                or data.get("equipmentno")
                or data.get("id")
            )

        if not equipment_no:
            # Fix the misleading old message
            raise ValueError(
                f"Position missing code (keys={sorted(cls._as_dict(position).keys())})"
            )

        # Dates (often missing for positions; safe to store None)
        cd_raw = cls._get(
            position,
            "comission_date",  # legacy typo
            "commission_date",
            "commissiondate",
            "original_install_date",
        )
        cd_parsed = cls._parse_date(cd_raw)

        # Class
        eq_class = cls._get(position, "class_code", "class_desc", "class", "eqclass")

        # Category column is legacy; for positions we typically store department
        category = cls._get(
            position,
            # Equipment
            "category_code",
            "category_desc",
            # Position
            "department_code",
            "department",
        )

        # Description
        equipment_desc = cls._get(
            position, "description", "positiondesc", "equipmentdesc", "desc"
        )

        # Sponsor / owner-ish
        sponsor = cls._get(
            position,
            "organization",
            "sponsor",
            "assigned_to_desc",
            "assigned_to",
            # if nothing else, at least keep dept visible somewhere
            "department_code",
            "department",
        )

        # Parent relationship
        parent = cls._get(
            position,
            # Position
            "hierarchy_position_code",
            "parent_position_code",
            "parentposition",
            # Equipment/legacy
            "hierarchy_asset_code",
            "parentasset",
        )

        # Status display
        status = cls._get(
            position,
            "status_desc",
            "positionstatus_display",
            "assetstatus_display",
            "state_desc",
            "status",
        )

        return cls(
            equipment_no=str(equipment_no),
            eq_class=eq_class,
            category=category,
            equipment_desc=equipment_desc,
            sponsor=sponsor,
            parent_asset=parent,
            commission_date=cd_parsed,
            asset_status_display=status,
        )

    # --- Back conversions ----------------------------------------------------

    def to_position(self) -> Position:
        """Convert this row back to a `Position` domain model (subset only)."""
        payload: dict[str, Any] = {
            "code": self.equipment_no,
            "class_code": self.eq_class,
            "department_code": self.category,  # legacy column reused for department
            "description": self.equipment_desc,
            "hierarchy_position_code": self.parent_asset,
            "status_desc": self.asset_status_display,
        }

        if hasattr(Position, "model_validate"):
            return Position.model_validate(payload)  # type: ignore[attr-defined]
        return Position(**payload)  # type: ignore[call-arg]

    def to_equipment(self) -> Equipment:
        """Convert this row back to an `Equipment` domain model (subset only; legacy)."""
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
            f"parent – {self.parent_asset!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"status={self.asset_status_display!r}"
            ")"
        )
