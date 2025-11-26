from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from avtools.landb.device import LanDBDevice

# If you're on Postgres and want a proper network type, switch ip to INET:
# from sqlalchemy.dialects.postgresql import INET


# --- Base --------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# --- ORM ---------------------------------------------------------------------


class LanDBDeviceORM(Base):
    """LanDB device snapshot for local storage."""

    __tablename__ = "landb_devices"
    __table_args__ = (Index("ix_landb_devices_serialnumber", "serialnumber"),)

    # Sizes are conservative; adjust to upstream constraints if you know them.
    equipment_no: Mapped[str] = mapped_column(
        "equipmentno", String(64), primary_key=True
    )
    serial_number: Mapped[str] = mapped_column(
        "serialnumber", String(128), nullable=False
    )
    eq_class: Mapped[str | None] = mapped_column("eqclass", String(64), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(
        "manufacturer", String(32), nullable=True
    )

    # If Postgres: use INET for stronger semantics (and indexing)
    # ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    # Portable fallback: fits IPv6 text.
    ip: Mapped[str | None] = mapped_column("ip", String(45), nullable=True)

    # --- Converters ----------------------------------------------------------

    @classmethod
    def from_device(cls, device: LanDBDevice) -> LanDBDeviceORM:
        """Create an ORM row from a domain model."""
        data = device.model_dump(by_alias=False)
        orm_data = {k: v for k, v in data.items() if hasattr(cls, k)}
        return cls(**orm_data)

    def to_device(self) -> LanDBDevice:
        """Convert this row back to the Pydantic domain model."""
        # Pydantic v2: model_config(from_attributes=True) lets this work.
        return LanDBDevice.model_validate(self, from_attributes=True)

    # --- Debug ---------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            "LanDBDeviceORM("
            f"equipment_no={self.equipment_no!r}, "
            f"serial_number={self.serial_number!r}, "
            f"eq_class={self.eq_class!r}, "
            f"manufacturer={self.manufacturer!r}, "
            f"ip={self.ip!r}"
            ")"
        )
