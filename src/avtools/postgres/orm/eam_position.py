from __future__ import annotations

from datetime import date

from sqlalchemy import Column, Date, String
from sqlalchemy.orm import declarative_base

from avtools.eam.client import EAMPosition

Base = declarative_base()


class EAMPositionORM(Base):
    """ORM mapping for EAM position rows."""

    __tablename__ = "eam_positions"

    equipment_no: str = Column("equipmentno", String, primary_key=True, nullable=False)
    equipment_desc: str | None = Column("equipmentdesc", String, nullable=True)
    eq_class: str | None = Column("eqclass", String, nullable=True)
    commission_date: date | None = Column("commissiondate", Date, nullable=True)
    parent_asset: str | None = Column("parentasset", String, nullable=True)
    # if you later add sponsor to the DB:
    # sponsor: str | None = Column("sponsor", String, nullable=True)

    @classmethod
    def from_position(cls, position: EAMPosition) -> EAMPositionORM:
        """Construct an ORM row from an `EAMPosition`."""
        data = position.model_dump(by_alias=False)
        orm_data = {k: v for k, v in data.items() if hasattr(cls, k)}
        return cls(**orm_data)
