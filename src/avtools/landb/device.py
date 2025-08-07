from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict

from avtools.io.logger import system_logger


class LanDBDevice(BaseModel):
    """
    Represents a network device record from LanDB.

    Attributes:
        equipmentno: Unique equipment identifier.
        serialnumber: Serial number used for query filtering.
        equipmentdesc: Friendly device name, if available.
        manufacturer: Vendor name, if available.
        eqclass: Equipment classification, if available.
        building: Location building, if available.
        floor: Location floor, if available.
        room: Location room, if available.
        ip: Assigned IPv4 address, if present.
    """

    equipmentno: str
    serialnumber: str
    equipmentdesc: str | None = None
    manufacturer: str | None = None
    eqclass: str | None = None
    building: str | None = None
    floor: str | None = None
    room: str | None = None
    ip: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def create_device(
        cls, equipmentno: str, serialnumber: str, eqclass: str
    ) -> LanDBDevice:
        """
        Factory method to initialize a device with only identifiers.
        """
        return cls(equipmentno=equipmentno, serialnumber=serialnumber, eqclass=eqclass)

    def log_device(self) -> None:
        """
        Output the device's current state to the system logger.
        """
        system_logger.info(
            f"Device {self.equipmentno} | Serial: {self.serialnumber} | "
            f"Description: {self.equipmentdesc} | Manufacturer: {self.manufacturer} | "
            f"Eqclass: {self.eqclass} | "
            f"Location: {self.building}/{self.floor}/{self.room} | IP: {self.ip}"
        )

    def from_ip(self, ip_data: dict[str, Any]) -> None:
        """
        Update the device's IP attribute from a JSON IP record.
        """
        if ipv4 := ip_data.get("ipv4"):
            self.ip = ipv4

    def from_device(self, device_data: dict[str, Any]) -> None:
        """
        Merge metadata fields (equipmentdesc, manufacturer, location) into this device.
        """
        if equipmentdesc := device_data.get("name"):
            self.equipmentdesc = equipmentdesc
        if manufacturer := device_data.get("manufacturer"):
            self.manufacturer = manufacturer
        loc = device_data.get("location") or {}
        if building := loc.get("building"):
            self.building = building
        if floor := loc.get("floor"):
            self.floor = floor
        if room := loc.get("room"):
            self.room = room
