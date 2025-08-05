from __future__ import annotations

from typing import Any

from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base

from avtools.landb.device import LanDBDevice

Base = declarative_base()


class LanDBDeviceORM(Base):
    """
    ORM model for LanDB devices.

    Maps LanDBDevice domain objects to the "landb_devices" table,
    with columns for equipment number, serial number,
    equipmentdesc, manufacturer, building, floor, room, and IP.
    """

    __tablename__ = "landb_devices"

    equipmentno: str = Column(String, primary_key=True, nullable=False)
    serialnumber: str = Column(String, nullable=False)
    equipmentdesc: str | None = Column(String, nullable=True)
    manufacturer: str | None = Column(String, nullable=True)
    eqclass: str | None = Column(String, nullable=True)
    building: str | None = Column(String, nullable=True)
    floor: str | None = Column(String, nullable=True)
    room: str | None = Column(String, nullable=True)
    ip: str | None = Column(String, nullable=True)

    @classmethod
    def from_device(cls, device: LanDBDevice) -> LanDBDeviceORM:
        """
        Create an ORM instance from a LanDBDevice domain object.
        """
        return cls(
            equipmentno=device.equipmentno,
            serialnumber=device.serialnumber,
            equipmentdesc=device.equipmentdesc,
            manufacturer=device.manufacturer,
            eqclass=device.eqclass,
            building=device.building,
            floor=device.floor,
            room=device.room,
            ip=device.ip,
        )
