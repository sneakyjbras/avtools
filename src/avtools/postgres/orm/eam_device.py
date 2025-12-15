from __future__ import annotations

from datetime import date
from typing import Any

from eam_rest_client import Equipment
from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --- Base --------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# --- ORM ---------------------------------------------------------------------


class EAMDeviceORM(Base):
    """ORM mapping for EAM device rows.

    Note: despite the name, this now maps the EAM REST client's `Equipment` model.
    """

    __tablename__ = "eam_devices"
    __table_args__ = (
        Index("ix_eam_devices_serialnumber", "serialnumber"),
        Index("ix_eam_devices_eqclass_category", "eqclass", "category"),
        Index("ix_eam_devices_status", "assetstatus_display"),
        Index("ix_eam_devices_position", "position"),
    )

    # Sizes are conservative; adjust to upstream constraints if you know them.
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
            # dict-like
            if isinstance(obj, dict) and n in obj and obj[n] not in ("", None):
                return obj[n]
            # attribute-like
            if hasattr(obj, n):
                v = getattr(obj, n)
                if v not in ("", None):
                    return v
        return None

    @staticmethod
    def _as_dict(obj: Any) -> dict[str, Any]:
        """Best-effort conversion to a plain dict."""
        # pydantic v2
        if hasattr(obj, "model_dump"):
            return obj.model_dump(by_alias=False)  # type: ignore[attr-defined]
        # pydantic v1
        if hasattr(obj, "dict"):
            return obj.dict()  # type: ignore[attr-defined]
        # already a mapping
        if isinstance(obj, dict):
            return obj
        # normal object
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
        return {}

    @classmethod
    def from_device(cls, device: Equipment) -> EAMDeviceORM:
        """Create an ORM row from an `Equipment` domain model."""
        # Prefer direct attribute access (more stable than dump key names)
        equipment_no = cls._get(device, "equipment_no", "equipmentno")
        if not equipment_no:
            # last resort: try dict dump
            data = cls._as_dict(device)
            equipment_no = data.get("equipment_no") or data.get("equipmentno")
        if not equipment_no:
            raise ValueError("Equipment missing equipment_no/equipmentno")

        return cls(
            equipment_no=str(equipment_no),
            serial_number=cls._get(device, "serial_number", "serialnumber"),
            eq_class=cls._get(device, "eq_class", "eqclass", "class"),
            category=cls._get(device, "category"),
            equipment_desc=cls._get(
                device, "equipment_desc", "equipmentdesc", "equipment_description"
            ),
            model=cls._get(device, "model"),
            manufacturer=cls._get(device, "manufacturer"),
            position=cls._get(device, "position"),
            parent_asset=cls._get(device, "parent_asset", "parentasset"),
            commission_date=cls._get(device, "commission_date", "commissiondate"),
            asset_status_display=cls._get(
                device,
                "asset_status_display",
                "assetstatus_display",
                "status_desc",
                "status_description",
            ),
        )

    # Back-compat name: some callers may still call `to_device()`.
    def to_device(self) -> Equipment:
        return self.to_equipment()

    def to_equipment(self) -> Equipment:
        """Convert this row back to an `Equipment` domain model."""
        payload = {
            "equipment_no": self.equipment_no,
            "serial_number": self.serial_number,
            "eq_class": self.eq_class,
            "category": self.category,
            "equipment_desc": self.equipment_desc,
            "model": self.model,
            "manufacturer": self.manufacturer,
            "position": self.position,
            "parent_asset": self.parent_asset,
            "commission_date": self.commission_date,
            "asset_status_display": self.asset_status_display,
        }

        # If Equipment is pydantic v2, this will exist:
        if hasattr(Equipment, "model_validate"):
            return Equipment.model_validate(payload)  # type: ignore[attr-defined]

        # Otherwise, try normal construction (works for dataclass/attrs/plain classes).
        return Equipment(**payload)  # type: ignore[call-arg]

    # --- Debug ---------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            "EAMDeviceORM("
            f"equipment_no={self.equipment_no!r}, "
            f"serial_number={self.serial_number!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"status={self.asset_status_display!r}"
            ")"
        )
