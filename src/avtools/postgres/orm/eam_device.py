from __future__ import annotations

from typing import Any

from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base

from avtools.eam.client import EAMDevice

Base = declarative_base()


class EAMDeviceORM(Base):
    """
    ORM model for EAM devices.

    Maps EAMDevice domain objects to the "eam_devices" table,
    with columns for equipment number, serial number,
    position, equipment description, class, and manufacturer.
    """

    __tablename__ = "eam_devices"

    equipmentno: str = Column(String, primary_key=True, nullable=False)
    serialnumber: str = Column(String, nullable=False)
    position: str | None = Column(String, nullable=True)
    equipmentdesc: str | None = Column(String, nullable=True)
    eqclass: str | None = Column(String, nullable=True)
    category: str | None = Column(String, nullable=True)
    manufacturer: str | None = Column(String, nullable=True)
    commissiondate: str | None = Column(String, nullable=True)

    @classmethod
    def from_device(cls, device: EAMDevice) -> EAMDeviceORM:
        """
        Create an ORM instance from an EAMDevice domain object.
        """
        return cls(
            equipmentno=device.equipmentno,
            serialnumber=device.serialnumber,
            position=device.position,
            equipmentdesc=device.equipmentdesc,
            eqclass=device.eqclass,
            category=device.category,
            manufacturer=device.manufacturer,
            commissiondate=device.commissiondate,
        )
