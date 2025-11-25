from __future__ import annotations

from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base

from avtools.landb.device import LanDBDevice

Base = declarative_base()


class LanDBDeviceORM(Base):
    """ORM mapping for LanDB device rows."""

    __tablename__ = "landb_devices"

    equipment_no: str = Column("equipmentno", String, primary_key=True, nullable=False)
    serial_number: str = Column("serialnumber", String, nullable=False)
    manufacturer: str | None = Column("manufacturer", String, nullable=True)
    eq_class: str | None = Column("eqclass", String, nullable=True)
    ip: str | None = Column("ip", String, nullable=True)

    @classmethod
    def from_device(cls, device: LanDBDevice) -> LanDBDeviceORM:
        """Construct an ORM row from a LanDBDevice."""
        data = device.model_dump(by_alias=False)
        orm_data = {k: v for k, v in data.items() if hasattr(cls, k)}
        return cls(**orm_data)
