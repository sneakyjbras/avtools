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


class EAMDeviceORM(Base):
    """ORM mapping for EAM device rows.

    This stores a small AV-Tools-relevant subset of fields from the EAM REST client's
    `Equipment` model. `Equipment` field names differ from the legacy EAMDevice model,
    so conversions are explicitly mapped.
    """

    __tablename__ = "eam_devices"
    __table_args__ = (
        Index("ix_eam_devices_serialnumber", "serialnumber"),
        Index("ix_eam_devices_eqclass_category", "eqclass", "category"),
        Index("ix_eam_devices_status", "assetstatus_display"),
        Index("ix_eam_devices_position", "position"),
    )

    # DB columns kept as-is (legacy schema)
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
        # pydantic v2
        if hasattr(obj, "model_dump"):
            return obj.model_dump(by_alias=False)  # type: ignore[attr-defined]
        # pydantic v1
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
    def from_device(cls, device: Equipment) -> EAMDeviceORM:
        """Create an ORM row from an `Equipment` domain model.

        Mapping (Equipment -> legacy DB columns):
          equipment_no          <- code
          serial_number         <- serial_number
          eq_class              <- class_code (fallback class_desc)
          category              <- category_code (fallback category_desc)
          equipment_desc        <- description
          manufacturer          <- manufacturer_desc (fallback manufacturer_code)
          position              <- hierarchy_position_code (fallback cern_pos)
          parent_asset          <- hierarchy_asset_code (fallback hierarchy_position_code)
          commission_date       <- comission_date (note spelling)
          asset_status_display  <- status_desc (fallback state_desc)
        """
        equipment_no = cls._get(device, "code")
        if not equipment_no:
            data = cls._as_dict(device)
            equipment_no = data.get("code")
        if not equipment_no:
            raise ValueError("Equipment missing code")

        cd_raw = cls._get(
            device, "comission_date", "commission_date", "original_install_date"
        )
        cd_parsed = cls._parse_date(cd_raw)

        eq_class = cls._get(device, "class_code", "class_desc")
        category = cls._get(device, "category_code", "category_desc")
        manufacturer = cls._get(device, "manufacturer_desc", "manufacturer_code")

        return cls(
            equipment_no=str(equipment_no),
            serial_number=cls._get(device, "serial_number"),
            eq_class=eq_class,
            category=category,
            equipment_desc=cls._get(device, "description"),
            model=cls._get(device, "model"),
            manufacturer=manufacturer,
            position=cls._get(device, "hierarchy_position_code", "cern_pos"),
            parent_asset=cls._get(
                device, "hierarchy_asset_code", "hierarchy_position_code"
            ),
            commission_date=cd_parsed,
            asset_status_display=cls._get(device, "status_desc", "state_desc"),
        )

    # Back-compat name: callers may still call `to_device()`.
    def to_device(self) -> Equipment:
        return self.to_equipment()

    def to_equipment(self) -> Equipment:
        """Convert this row back to an `Equipment` domain model (subset only)."""
        payload: dict[str, Any] = {
            "code": self.equipment_no,
            "serial_number": self.serial_number,
            "class_code": self.eq_class,
            "category_code": self.category,
            "description": self.equipment_desc,
            "model": self.model,
            "manufacturer_desc": self.manufacturer,
            "hierarchy_position_code": self.position,
            "hierarchy_asset_code": self.parent_asset,
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
            "EAMDeviceORM("
            f"equipment_no={self.equipment_no!r}, "
            f"serial_number={self.serial_number!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"status={self.asset_status_display!r}"
            ")"
        )
