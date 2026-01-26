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


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for this module."""

    pass


def _none_if_blank(value: Any) -> str | None:
    """Normalize a potentially empty value to a trimmed string.

    Args:
        value: Input value (string/number/None).

    Returns:
        Trimmed string, or ``None`` if the value is ``None`` or blank after trimming.
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return str(value)


def _parse_any_date(v: Any) -> date | None:
    """Parse a date from multiple common EAM representations.

    Args:
        v: A value that may represent a date (``date``, ``datetime``, or string).

    Returns:
        A ``date`` if parsing succeeds, otherwise ``None``.

    Notes:
        Supports EAM's common ``"%d-%b-%Y"`` format as well as ISO-8601 date/datetime
        strings (including a trailing ``Z``).
    """
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
    """Format a date in EAM's typical display format.

    Args:
        d: Date value.

    Returns:
        A string formatted as ``DD-Mon-YYYY`` or ``None``.
    """
    return d.strftime("%d-%b-%Y") if d else None


class CachedEquipment(Equipment):
    """Equipment with AVTools-only private state for diffing."""

    _avtools_compare_fields: set[str] = PrivateAttr(default_factory=set)

    def avtools_compare_fields(self) -> set[str]:
        """Return the set of field names to compare when diffing.

        Returns:
            A copy of the compare-field set.
        """
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
        """Fetch an attribute or dict key, treating empty strings as missing.

        Args:
            obj: Domain object or dict payload.
            name: Attribute/key name to look up.

        Returns:
            The value if present and non-empty, otherwise ``None``.
        """
        if isinstance(obj, dict):
            v = obj.get(name)
            return None if v in ("", None) else v
        v = getattr(obj, name, None)
        return None if v in ("", None) else v

    # --- Converters ----------------------------------------------------------

    @classmethod
    def from_equipment(cls, equipment: Equipment) -> EAMPositionORM:
        """Convert an EAM ``Equipment`` domain object into an ORM row.

        Args:
            equipment: EAM equipment/position record.

        Returns:
            ORM instance ready to be inserted/updated in Postgres.

        Raises:
            EAMPositionORMError: If the required join key (``code``) is missing.

        Notes:
            The upstream EAM payload uses the misspelled key ``comission_date``.
            Internally we store the corrected spelling as ``commission_date``.
        """
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
        """Convert this ORM row back into an EAM ``Equipment``-shaped object.

        Returns:
            A ``CachedEquipment`` instance (subclass of ``Equipment``) containing
            only the fields AVTools persists/diffs against.

        Notes:
            - The outbound payload uses the upstream misspelling ``comission_date``.
            - The returned object carries an AVTools compare-field whitelist.
        """
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
        """Return a compact, debug-friendly representation."""
        return (
            "EAMPositionORM("
            f"equipment_no={self.equipment_no!r}, "
            f"parent={self.parent_asset!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"status={self.asset_status_display!r}"
            ")"
        )
