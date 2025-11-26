from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

device_logger = structlog.get_logger(__name__).bind(component="eam", model="EAMDevice")


class EAMDevice(BaseModel):
    """Device record from the EAM grid."""

    # Identifiers
    equipment_no: str | None = Field(
        None,
        alias="equipmentno",
        description="Equipment number of the device in EAM.",
    )
    serial_number: str | None = Field(
        None,
        alias="serialnumber",
        description="Device serial number as stored in EAM.",
    )

    # Classification
    eq_class: str | None = Field(
        None,
        alias="class",
        description='Classification of the equipment (EAM "class" field).',
    )
    category: str | None = Field(
        None,
        description="Sub-classification or category of the device.",
    )

    # Description / model
    equipment_desc: str | None = Field(
        None,
        alias="equipmentdesc",
        description="Human-readable description of the equipment.",
    )
    model: str | None = Field(
        None,
        description="Model identifier of the device, if available.",
    )
    manufacturer: str | None = Field(
        None,
        description="Manufacturer of the device.",
    )

    # Location / hierarchy
    position: str | None = Field(
        None,
        description="Position identifier in EAM.",
    )
    parent_asset: str | None = Field(
        None,
        alias="parentasset",
        description="Parent asset or position of the current asset.",
    )

    # Lifecycle / status
    commission_date: date | None = Field(
        None,
        alias="commissiondate",
        description="Commissioning date of the device.",
    )
    asset_status_display: str | None = Field(
        None,
        alias="assetstatus_display",
        description="Human-readable asset status label.",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,  # allow model_validate(ORM_instance)
        extra="ignore",  # ignore unknown keys from the API
        str_strip_whitespace=True,
    )

    # --- Input normalization -------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _flatten_cells(cls, values: Any) -> Any:
        """Flatten EAM grid `cell` entries when parsing raw JSON rows."""
        if not isinstance(values, Mapping):
            return values

        flattened: dict[str, Any] = {k: v for k, v in values.items() if k != "cell"}
        for cell in values.get("cell", []):
            if isinstance(cell, Mapping):
                key = cell.get("t")
                if key:
                    flattened[key] = cell.get("value")

        return flattened

    @field_validator(
        "equipment_no",
        "serial_number",
        "eq_class",
        "category",
        "equipment_desc",
        "model",
        "manufacturer",
        "position",
        "parent_asset",
        "asset_status_display",
        mode="before",
    )
    @classmethod
    def _normalize_str(cls, v: Any) -> str | None:
        """Normalize incoming string-like values (strip, empty -> None)."""
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("commission_date", mode="before")
    @classmethod
    def _normalise_commission_date(cls, v: Any) -> Any:
        """Parse EAM commission_date strings into a `date`."""
        if v is None or v == "":
            return None

        if isinstance(v, date):
            return v

        raw = str(v).strip()

        # Try multiple common formats we see from EAM.
        for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.date()
            except ValueError:
                continue

        raise ValueError(f"Invalid commission date format: {raw!r}")

    @field_serializer("commission_date", when_used="json")
    def _serialize_commission_date(self, value: date | None) -> str | None:
        """Serialize commission_date as YYYY-MM-DD in JSON output."""
        if value is None:
            return None
        return value.isoformat()  # '2024-07-01'

    # --- Convenience ---------------------------------------------------------

    def __str__(self) -> str:
        return (
            "EAMDevice("
            f"equipment_no={self.equipment_no!r}, "
            f"position={self.position!r}, "
            f"equipment_desc={self.equipment_desc!r}, "
            f"serial_number={self.serial_number!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"manufacturer={self.manufacturer!r}, "
            f"model={self.model!r}, "
            f"commission_date={self.commission_date!r}, "
            f"parent_asset={self.parent_asset!r}, "
            f"asset_status_display={self.asset_status_display!r}"
            ")"
        )

    def log_device(self) -> None:
        """Emit this device as a structured log event."""
        payload = self.model_dump(mode="json", by_alias=False, exclude_none=True)
        device_logger.info("eam_device_synced", **payload)
