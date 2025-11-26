from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

device_logger = structlog.get_logger(__name__).bind(
    component="landb",
    model="LanDBDevice",
)


class LanDBDevice(BaseModel):
    """LanDB network device."""

    # Snake_case fields; aliases match LanDB/legacy names.
    equipment_no: str = Field(alias="equipmentno")
    serial_number: str = Field(alias="serialnumber")
    eq_class: str | None = Field(default=None, alias="eqclass")
    manufacturer: str | None = None
    ip: str | None = None

    # Allow reading attributes from ORM objects and using field names or aliases.
    # Ignore legacy/extra fields gracefully.
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="ignore",
    )

    # --- Validators ---------------------------------------------------------

    @field_validator("equipment_no", "serial_number", mode="before")
    @classmethod
    def _strip_required(cls, v: Any) -> str:
        """Normalize required identifiers: coerce to non-empty, stripped string."""
        if v is None:
            raise ValueError("value is required")
        if not isinstance(v, str):
            v = str(v)
        v = v.strip()
        if not v:
            raise ValueError("value cannot be empty")
        return v

    @field_validator("eq_class", "manufacturer", mode="before")
    @classmethod
    def _strip_optional(cls, v: Any) -> str | None:
        """Normalize optional strings: strip and convert empty to None."""
        if v is None:
            return None
        if not isinstance(v, str):
            v = str(v)
        v = v.strip()
        return v or None

    # --- API ----------------------------------------------------------------

    @classmethod
    def create_device(
        cls,
        equipment_no: str,
        serial_number: str,
        eq_class: str,
        manufacturer: str,
    ) -> LanDBDevice:
        """Factory for callers that prefer an explicit constructor."""
        return cls(
            equipment_no=equipment_no,
            serial_number=serial_number,
            eq_class=eq_class,
            manufacturer=manufacturer,
        )

    def log_device(self) -> None:
        """Emit a structured snapshot of the device."""
        device_logger.info(
            "landb_device_snapshot",
            equipment_no=self.equipment_no,
            serial_number=self.serial_number,
            eq_class=self.eq_class,
            manufacturer=self.manufacturer,
            ip=self.ip,
        )

    def from_ip(self, ip_data: Mapping[str, Any]) -> bool:
        """Merge IP record into the model; return True if IP changed."""
        raw = ip_data.get("ipv4") or ip_data.get("ip") or ip_data.get("address")
        if not raw:
            return False
        ip_str = str(raw).strip()
        if not ip_str or ip_str == self.ip:
            return False
        self.ip = ip_str
        return True

    def from_device(self, device_data: Mapping[str, Any]) -> None:
        """Merge metadata fields from a LanDB device record."""
        if manufacturer := device_data.get("manufacturer"):
            # Rely on validator to normalize on next model update, but keep simple here.
            self.manufacturer = str(manufacturer).strip() or self.manufacturer
        # Add more fields here later if you decide to keep them in the model.
