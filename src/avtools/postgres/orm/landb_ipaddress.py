"""LanDB IPAddress ORM + AVTools-enriched cached domain model.

Why this exists
- We persist a *small subset* of LanDB IP data in Postgres to avoid hammering LanDB.
- AVTools needs to join LanDB IPs back to EAM Equipment (by equipment code).

Key change (per request)
- `CachedIPAddress` is **NOT** a subclass of `landb_rest_client.models.IPAddress`.
  It is its own Pydantic v1 model, typically constructed from an EAM `Equipment`
  plus a LanDB `IPAddress` (and optionally the matching LanDB `Device`).

Stored subset (only what we need)
  - equipmentno    (primary key, EAM equipment code)
  - serialnumber   (EAM serial)
  - ip             (SNMP target IP, normalised from LanDB ipv4/ipv6)
  - name           (LanDB identity, typically the device name / hostname)
  - eqclass        (EAM equipment class)
  - manufacturer
  - model
"""

from __future__ import annotations

from typing import Any

from eam_rest_client import Equipment
from landb_rest_client.models import Device, IPAddress
from pydantic import BaseModel, PrivateAttr
from sqlalchemy import Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _none_if_blank(value: Any) -> str | None:
    """Normalise empty strings and None to None; stringify everything else."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return str(value)


class CachedIPAddress(BaseModel):
    """AVTools cached LanDB IP record.

    This is the **domain** object used throughout the pipeline and stored in Postgres.
    It is built from EAM + LanDB data but only exposes the subset AVTools needs.
    """

    # --- DB-backed fields (subset) -----------------------------------------
    equipment_no: str | None
    serial_number: str | None
    ip: str | None
    name: str | None
    eq_class: str | None
    manufacturer: str | None
    model: str | None

    # --- Private extras (NOT stored / NOT diffed) ---------------------------
    _avtools_compare_fields: set[str] = PrivateAttr(default_factory=set)
    _source_equipment: Equipment | None = PrivateAttr(default=None)
    _source_ipaddress: IPAddress | None = PrivateAttr(default=None)
    _source_device: Device | None = PrivateAttr(default=None)

    class Config:
        # We are using Pydantic v1.
        allow_mutation = True

    def avtools_compare_fields(self) -> set[str]:
        # Return a copy to avoid accidental external mutation.
        return set(self._avtools_compare_fields)

    @classmethod
    def from_equipment_and_ipaddress(
        cls,
        equipment: Equipment,
        ipaddr: IPAddress,
        *,
        landb_device: Device | None = None,
    ) -> CachedIPAddress:
        """Create the cached record from EAM Equipment + LanDB IPAddress.

        Notes
        - We *always* pass all DB-backed fields explicitly (even if None) so that
          `.dict(exclude_unset=True)` still includes them for diffing.
        """

        equipment_no = _none_if_blank(getattr(equipment, "code", None))

        # Prefer EAM serial; fallback to LanDB Device serial when present.
        serial_number = _none_if_blank(getattr(equipment, "serial_number", None))
        if not serial_number and landb_device is not None:
            serial_number = _none_if_blank(getattr(landb_device, "serial_number", None))

        # Prefer LanDB Device name; fallback to EAM description; last resort: LanDB IP name.
        name = None
        if landb_device is not None:
            name = _none_if_blank(getattr(landb_device, "name", None))
        if not name:
            name = _none_if_blank(getattr(equipment, "description", None))
        if not name:
            name = _none_if_blank(getattr(ipaddr, "name", None))

        # Normalise SNMP target IP.
        ipv4 = _none_if_blank(getattr(ipaddr, "ipv4", None))
        ipv6 = _none_if_blank(getattr(ipaddr, "ipv6", None))
        ip = ipv4 or ipv6

        # EAM metadata.
        eq_class = _none_if_blank(
            getattr(equipment, "class_code", None)
            or getattr(equipment, "class_desc", None)
        )
        manufacturer = _none_if_blank(
            getattr(equipment, "manufacturer_code", None)
            or getattr(equipment, "manufacturer_desc", None)
        )
        model = _none_if_blank(getattr(equipment, "model", None))

        out = cls(
            equipment_no=equipment_no,
            serial_number=serial_number,
            ip=ip,
            name=name,
            eq_class=eq_class,
            manufacturer=manufacturer,
            model=model,
        )

        out._source_equipment = equipment
        out._source_ipaddress = ipaddr
        out._source_device = landb_device
        return out


class LanDBIPAddressORM(Base):
    """PostgreSQL snapshot of LanDB IPAddress data (EAM-linkable)."""

    __tablename__ = "landb_ipaddresses"
    __table_args__ = (
        Index("ix_landb_ipaddresses_serialnumber", "serialnumber"),
        Index("ix_landb_ipaddresses_name", "name"),
    )

    # Primary key is the EAM equipment number so we can join back to EAM.
    equipment_no: Mapped[str] = mapped_column(
        "equipmentno", String(64), primary_key=True
    )

    serial_number: Mapped[str | None] = mapped_column(
        "serialnumber", String(128), nullable=True
    )

    # Store as string for portability (IPv4 or IPv6). If Postgres-only, INET is nicer.
    ip: Mapped[str | None] = mapped_column("ip", String(45), nullable=True)

    name: Mapped[str | None] = mapped_column("name", String(255), nullable=True)

    # Cached metadata (DB column names are fixed: eqclass/manufacturer/model)
    eq_class: Mapped[str | None] = mapped_column("eqclass", String(128), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(
        "manufacturer", String(255), nullable=True
    )
    model: Mapped[str | None] = mapped_column("model", String(255), nullable=True)

    # Fields we consider when diffing against the DB row.
    _DB_COMPARE_FIELDS: set[str] = {
        "equipment_no",
        "serial_number",
        "ip",
        "name",
        "eq_class",
        "manufacturer",
        "model",
    }

    # --- Converters --------------------------------------------------------

    @classmethod
    def from_ipaddress(cls, ipaddr: CachedIPAddress) -> LanDBIPAddressORM:
        equipment_no = _none_if_blank(getattr(ipaddr, "equipment_no", None))
        if not equipment_no:
            raise ValueError(
                "LanDB cached IPAddress missing required join key 'equipment_no' (EAM code)."
            )

        return cls(
            equipment_no=equipment_no,
            serial_number=_none_if_blank(getattr(ipaddr, "serial_number", None)),
            ip=_none_if_blank(getattr(ipaddr, "ip", None)),
            name=_none_if_blank(getattr(ipaddr, "name", None)),
            eq_class=_none_if_blank(getattr(ipaddr, "eq_class", None)),
            manufacturer=_none_if_blank(getattr(ipaddr, "manufacturer", None)),
            model=_none_if_blank(getattr(ipaddr, "model", None)),
        )

    def to_ipaddress(self) -> CachedIPAddress:
        """Convert this row back into an AVTools cached IPAddress domain object."""
        payload: dict[str, Any] = {
            "equipment_no": self.equipment_no,
            "serial_number": self.serial_number,
            "ip": self.ip,
            "name": self.name,
            "eq_class": self.eq_class,
            "manufacturer": self.manufacturer,
            "model": self.model,
        }

        ipaddr = CachedIPAddress(**payload)
        ipaddr._avtools_compare_fields = set(self._DB_COMPARE_FIELDS)
        return ipaddr
