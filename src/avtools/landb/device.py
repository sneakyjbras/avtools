from __future__ import annotations

from typing import Any
from collections.abc import Mapping

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger("LanDBDevice")


class LanDBDevice(BaseModel):
    """LanDB network device."""

    # Snake_case field names, aliases match DB / legacy names.
    equipment_no: str = Field(alias="equipmentno")
    serial_number: str = Field(alias="serialnumber")
    manufacturer: str | None = None
    eq_class: str | None = Field(default=None, alias="eqclass")
    ip: str | None = None

    # Allow reading attributes from ORM objects and using field names or aliases.
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    @classmethod
    def create_device(
        cls,
        equipment_no: str,
        serial_number: str,
        eq_class: str,
    ) -> LanDBDevice:
        """Factory to initialize a device with identifiers only."""
        return cls(
            equipment_no=equipment_no,
            serial_number=serial_number,
            eq_class=eq_class,
        )

    def log_device(self) -> None:
        """Log the current device state."""
        logger.info(
            "LanDB device",
            equipment_no=self.equipment_no,
            serial_number=self.serial_number,
            manufacturer=self.manufacturer,
            eq_class=self.eq_class,
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
            self.manufacturer = manufacturer
        # Add more fields here later if you decide to keep them in the model.
