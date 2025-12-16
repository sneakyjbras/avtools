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

    hierarchy_location_code: Mapped[str | None] = mapped_column(
        "hierarchy_location_code", String(64), nullable=True
    )

    department_code: Mapped[str | None] = mapped_column(
        "department_code", String(64), nullable=True
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
        """Create an ORM row from an `Equipment` domain model."""

        # from pprint import pprint
        #
        #        pprint(device)
        #        print("")

        equipment_no = cls._get(device, "code")
        if not equipment_no:
            raise ValueError("Equipment missing code")

        comission_date = (
            datetime.strptime(v, "%d-%b-%Y").date().isoformat()
            if (v := cls._get(device, "comission_date"))
            else None
        )

        return cls(
            equipment_no=str(equipment_no),
            serial_number=cls._get(device, "serial_number"),
            eq_class=cls._get(device, "class_code"),
            category=cls._get(device, "category_code"),
            equipment_desc=cls._get(device, "description"),
            model=cls._get(device, "model"),
            manufacturer=cls._get(device, "manufacturer_code"),
            position=cls._get(device, "hierarchy_position_code"),
            parent_asset=cls._get(device, "hierarchy_asset_code"),
            commission_date=comission_date,
            asset_status_display=cls._get(device, "status_desc"),
            hierarchy_location_code=cls._get(device, "hierarchy_location_code"),
            department_code=cls._get(device, "department_code"),
        )

    def to_equipment(self) -> Equipment:
        """Convert this row back to an `Equipment` domain model (subset only)."""
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

        if self.commission_date is not None:
            payload["comission_date"] = self.commission_date.isoformat()

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
