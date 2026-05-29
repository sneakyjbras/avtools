from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from avtools.postgres.monitoring.models.projector import ProjectorMonitoring


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for monitoring tables."""

    pass


class ProjectorMonitoringORM(Base):
    """Latest-known projector text monitoring snapshot (firmware, etc.)."""

    __tablename__ = "avtools_projector_monitoring"

    equipment_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    firmware: Mapped[str | None] = mapped_column(Text, nullable=True)
    power_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_avtools_projector_monitoring_equipment_no", "equipment_no"),)

    @classmethod
    def from_model(cls, m: ProjectorMonitoring) -> "ProjectorMonitoringORM":
        return cls(
            equipment_no=m.equipment_no,
            ip=m.ip,
            firmware=m.firmware,
            power_status=m.power_status,
            updated_at=m.updated_at,
        )
