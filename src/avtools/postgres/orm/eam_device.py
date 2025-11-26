from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from avtools.eam.device import EAMDevice

# --- Base --------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# --- ORM ---------------------------------------------------------------------


class EAMDeviceORM(Base):
    """ORM mapping for EAM device rows."""

    __tablename__ = "eam_devices"
    __table_args__ = (
        Index("ix_eam_devices_serialnumber", "serialnumber"),
        Index("ix_eam_devices_eqclass_category", "eqclass", "category"),
        Index("ix_eam_devices_status", "assetstatus_display"),
        Index("ix_eam_devices_position", "position"),
    )

    # Sizes are conservative; adjust to upstream constraints if you know them.
    equipment_no: Mapped[str] = mapped_column(
        "equipmentno", String(64), primary_key=True
    )
    serial_number: Mapped[str | None] = mapped_column(
        "serialnumber", String(128), nullable=True
    )
    eq_class: Mapped[str | None] = mapped_column("eqclass", String(64), nullable=True)
    category: Mapped[str | None] = mapped_column("category", String(64), nullable=True)
    equipment_desc: Mapped[str | None] = mapped_column(
        "equipmentdesc", Text, nullable=True
    )
    model: Mapped[str | None] = mapped_column("model", String(128), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(
        "manufacturer", String(128), nullable=True
    )
    position: Mapped[str | None] = mapped_column("position", String(64), nullable=True)
    parent_asset: Mapped[str | None] = mapped_column(
        "parentasset", String(64), nullable=True
    )
    commission_date: Mapped[date | None] = mapped_column(
        "commissiondate", Date, nullable=True
    )
    asset_status_display: Mapped[str | None] = mapped_column(
        "assetstatus_display", String(64), nullable=True
    )

    # --- Converters ----------------------------------------------------------

    @classmethod
    def from_device(cls, device: EAMDevice) -> EAMDeviceORM:
        """Create an ORM row from a domain model."""
        data = device.model_dump(by_alias=False)
        orm_data = {k: v for k, v in data.items() if hasattr(cls, k)}
        return cls(**orm_data)

    def to_device(self) -> EAMDevice:
        """Convert this row back to the domain model."""
        # Preferred: Pydantic v2 with from_attributes=True.
        try:
            return EAMDevice.model_validate(self, from_attributes=True)  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback: construct manually.
            return EAMDevice(
                equipment_no=self.equipment_no,
                serial_number=self.serial_number,
                eq_class=self.eq_class,
                category=self.category,
                equipment_desc=self.equipment_desc,
                model=self.model,
                manufacturer=self.manufacturer,
                position=self.position,
                parent_asset=self.parent_asset,
                commission_date=self.commission_date,
                asset_status_display=self.asset_status_display,
            )

    # --- Debug ---------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            "EAMDeviceORM("
            f"equipment_no={self.equipment_no!r}, "
            f"serial_number={self.serial_number!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"status={self.asset_status_display!r}"
            ")"
        )
