from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from avtools.postgres.monitoring.models.sysdescr import DeviceSysDescrMonitoring
from avtools.postgres.monitoring.orm.projector import Base


class DeviceSysDescrMonitoringORM(Base):
    """Latest-known SNMP sysDescr.0 snapshot keyed by equipment_no."""

    __tablename__ = "avtools_device_sysdescr_monitoring"

    equipment_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    eqclass: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sysdescr: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ix_avtools_device_sysdescr_monitoring_equipment_no", "equipment_no"),
    )

    @classmethod
    def from_model(cls, m: DeviceSysDescrMonitoring) -> "DeviceSysDescrMonitoringORM":
        return cls(
            equipment_no=m.equipment_no,
            ip=m.ip,
            eqclass=m.eqclass,
            category=m.category,
            sysdescr=m.sysdescr,
            updated_at=m.updated_at,
        )
