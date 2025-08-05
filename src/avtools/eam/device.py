from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, root_validator

from avtools.io.logger import system_logger


class EAMDevice(BaseModel):
    """
    Represents a device from the EAM system.

    Attributes:
        serialnumber: The device's serial number.
        position: The device's position.
        equipmentno: Equipment number.
        equipmentdesc: Equipment description.
        eqclass: Classification (alias for "class").
        category: Sub classification (might be null for some devices).
        manufacturer: Manufacturer name.
        commissiondate: Commission date (ISO string).
    """

    serialnumber: str | None = None
    position: str | None = None
    equipmentno: str | None = None
    equipmentdesc: str | None = None
    eqclass: str | None = Field(None, alias="class")
    category: str | None = None
    manufacturer: str | None = None
    commissiondate: str | None = None

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,  # enable .from_orm()
        extra="ignore",  # drop any keys not defined above
    )

    @root_validator(pre=True)
    def _flatten_cells(cls, values: Any) -> dict[str, Any] | Any:
        """
        If given a raw JSON row, pull each { "t": key, "value": val }
        entry in values["cell"] up into values[key]. Otherwise (ORM),
        leave values untouched.
        """
        if not isinstance(values, dict):
            # we're in from_orm(); let Pydantic read attributes normally
            return values

        for cell in values.get("cell", []):
            key = cell.get("t")
            if key is not None:
                values[key] = cell.get("value")
        values.pop("cell", None)
        return values

    def log_device(self) -> None:
        """
        Log the details of this device to the system logger.
        """
        system_logger.info(
            f"EquipmentNo: {self.equipmentno}, "
            f"Position: {self.position}, "
            f"EquipmentDesc: {self.equipmentdesc}, "
            f"SerialNumber: {self.serialnumber}, "
            f"Class: {self.eqclass}, "
            f"Category: {self.category}, "
            f"Manufacturer: {self.manufacturer}, "
            f"CommissionDate: {self.commissiondate}"
        )
