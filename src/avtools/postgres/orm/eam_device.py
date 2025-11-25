from __future__ import annotations

from datetime import date

from sqlalchemy import Column, Date, String
from sqlalchemy.orm import declarative_base

from avtools.eam.client import EAMDevice

Base = declarative_base()


class EAMDeviceORM(Base):
    """ORM mapping for EAM device rows."""

    __tablename__ = "eam_devices"

    equipment_no: str = Column("equipmentno", String, primary_key=True, nullable=False)
    serial_number: str = Column("serialnumber", String, nullable=False)
    position: str | None = Column("position", String, nullable=True)
    equipment_desc: str | None = Column("equipmentdesc", String, nullable=True)
    eq_class: str | None = Column("eqclass", String, nullable=True)
    category: str | None = Column("category", String, nullable=True)
    manufacturer: str | None = Column("manufacturer", String, nullable=True)
    commission_date: date | None = Column("commissiondate", Date, nullable=True)
    parent_asset: str | None = Column("parentasset", String, nullable=True)

    @classmethod
    def from_device(cls, device: EAMDevice) -> EAMDeviceORM:
        """Construct an ORM row from an `EAMDevice`."""
        data = device.model_dump(by_alias=False)
        orm_data = {k: v for k, v in data.items() if hasattr(cls, k)}
        return cls(**orm_data)
