"""LanDB IP cache ORM.

Defines the LanDB IP cache table and the CachedIPAddress domain helper used by
AVTools when joining EAM metadata with LanDB network targets.
"""

from __future__ import annotations

from typing import Any

from eam_rest_client import Equipment
from landb_rest_client.models import Device, IPAddress
from pydantic import BaseModel, PrivateAttr
from sqlalchemy import Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from avtools.exception.errors import LanDBIPAddressORMError


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for this module."""

    pass


def _none_if_blank(value: Any) -> str | None:
    """Normalize a potentially empty value to a trimmed string.

    Args:
        value: Input value (string/number/None).

    Returns:
        Trimmed string, or ``None`` if the value is ``None`` or blank after trimming.
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return str(value)


def _parse_location(loc: Any) -> tuple[str | None, str | None, str | None]:
    """Parse LanDB ``Device.location`` into a (building, floor, room) tuple.

    Args:
        loc: The LanDB location value (may be a dict, object, tuple, or ``None``).

    Returns:
        ``(building, floor, room)`` where each element may be ``None``.

    Notes:
        LanDB's location shape can vary; this function is intentionally tolerant.
    """
    building = floor = room = None

    if loc is None:
        return building, floor, room

    if isinstance(loc, dict):
        building = _none_if_blank(loc.get("building"))
        floor = _none_if_blank(loc.get("floor"))
        room = _none_if_blank(loc.get("room"))
        return building, floor, room

    if hasattr(loc, "building") or hasattr(loc, "floor") or hasattr(loc, "room"):
        building = _none_if_blank(getattr(loc, "building", None))
        floor = _none_if_blank(getattr(loc, "floor", None))
        room = _none_if_blank(getattr(loc, "room", None))
        return building, floor, room

    if isinstance(loc, (tuple, list)):
        building = _none_if_blank(loc[0]) if len(loc) > 0 else None
        floor = _none_if_blank(loc[1]) if len(loc) > 1 else None
        room = _none_if_blank(loc[2]) if len(loc) > 2 else None
        return building, floor, room

    # Unknown shape: keep best-effort in room.
    return None, None, _none_if_blank(loc)


class CachedIPAddress(BaseModel):
    """AVTools cached LanDB IP record.

    This is the **domain** object used throughout the pipeline and stored in Postgres.
    It is built from EAM + LanDB data but only exposes the subset AVTools needs.

    Semantics (important for alerting/comparisons)
    - serial_number: EAM Equipment.serial_number (what EAM thinks)
    - landb_serial:  LanDB Device.serial_number (what LanDB thinks)
    - name:          best-effort display name (kept for backwards compatibility)
    - landb_description: LanDB Device.name (what LanDB calls the device)
    - hostname:      LanDB IPAddress.name (often DNS hostname for that IP)
    """

    # --- DB-backed fields (subset) -----------------------------------------
    equipment_no: str | None
    serial_number: str | None  # EAM serial_number
    ip: str | None
    name: str | None  # display name (legacy)
    hostname: str | None  # IPAddress.name
    landb_serial: str | None  # Device.serial_number
    landb_description: str | None  # Device.name

    # LanDB Device.location (merged into this cache table)
    building: str | None
    floor: str | None
    room: str | None

    eq_class: str | None
    category: str | None
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
        """Return the set of field names to compare when diffing.

        Returns:
            A copy of the compare-field set.
        """
        return set(self._avtools_compare_fields)

    @classmethod
    def from_equipment_and_ipaddress(
        cls,
        equipment: Equipment,
        ipaddr: IPAddress | None,
        *,
        landb_device: Device | None = None,
    ) -> CachedIPAddress:
        """Build a cached LanDB IP record from EAM + LanDB models.

        Args:
            equipment: EAM ``Equipment`` instance.
            ipaddr: LanDB ``IPAddress`` instance (may be ``None``).
            landb_device: LanDB ``Device`` instance, if already resolved.

        Returns:
            A ``CachedIPAddress`` populated with the subset of fields AVTools stores.

        Notes:
            AVTools passes all DB-backed fields explicitly (even if ``None``) so that
            diffing is stable and does not depend on ``exclude_unset`` behavior.
        """

        equipment_no = _none_if_blank(getattr(equipment, "code", None))

        # EAM serial (what we will compare *against* LanDB's serial in alerting).
        serial_number = _none_if_blank(getattr(equipment, "serial_number", None))

        # LanDB fields needed for comparison/alerts.
        landb_serial = None
        landb_description = None
        if landb_device is not None:
            landb_serial = _none_if_blank(getattr(landb_device, "serial_number", None))
            landb_description = _none_if_blank(getattr(landb_device, "name", None))

        # LanDB hostname (from IPAddress.name).
        hostname = _none_if_blank(getattr(ipaddr, "name", None)) if ipaddr is not None else None

        # Best-effort display name (legacy).
        # Prefer LanDB device name; fallback to EAM description; then LanDB hostname.
        name = (
            landb_description or _none_if_blank(getattr(equipment, "description", None)) or hostname
        )

        # Normalise SNMP target IP.
        ipv4 = _none_if_blank(getattr(ipaddr, "ipv4", None)) if ipaddr is not None else None
        ipv6 = _none_if_blank(getattr(ipaddr, "ipv6", None)) if ipaddr is not None else None
        ip = ipv4 or ipv6

        # EAM metadata.
        eq_class = _none_if_blank(
            getattr(equipment, "class_code", None) or getattr(equipment, "class_desc", None)
        )
        category = _none_if_blank(
            getattr(equipment, "category_code", None)
            or getattr(equipment, "category_desc", None)
            or getattr(equipment, "category", None)
        )

        manufacturer = _none_if_blank(
            getattr(equipment, "manufacturer_code", None)
            or getattr(equipment, "manufacturer_desc", None)
        )
        model = _none_if_blank(getattr(equipment, "model", None))

        building = floor = room = None
        if landb_device is not None:
            building, floor, room = _parse_location(getattr(landb_device, "location", None))

        out = cls(
            equipment_no=equipment_no,
            serial_number=serial_number,
            ip=ip,
            name=name,
            hostname=hostname,
            landb_serial=landb_serial,
            landb_description=landb_description,
            building=building,
            floor=floor,
            room=room,
            eq_class=eq_class,
            category=category,
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
        Index("ix_landb_ipaddresses_hostname", "hostname"),
        Index("ix_landb_ipaddresses_landb_serial", "landb_serial"),
        Index("ix_landb_ipaddresses_building", "building"),
        Index("ix_landb_ipaddresses_room", "room"),
        Index("ix_landb_ipaddresses_category", "category"),
    )

    # Primary key is the EAM equipment number so we can join back to EAM.
    equipment_no: Mapped[str] = mapped_column("equipmentno", String(64), primary_key=True)

    serial_number: Mapped[str | None] = mapped_column("serialnumber", String(128), nullable=True)

    # Store as string for portability (IPv4 or IPv6). If Postgres-only, INET is nicer.
    ip: Mapped[str | None] = mapped_column("ip", String(45), nullable=True)

    # Best-effort display name (legacy).
    name: Mapped[str | None] = mapped_column("name", String(255), nullable=True)

    # New: dedicated LanDB fields used for comparisons/alerts.
    hostname: Mapped[str | None] = mapped_column("hostname", String(255), nullable=True)

    landb_serial: Mapped[str | None] = mapped_column("landb_serial", String(128), nullable=True)

    landb_description: Mapped[str | None] = mapped_column(
        "landb_description", String(255), nullable=True
    )

    building: Mapped[str | None] = mapped_column("building", String(64), nullable=True)
    floor: Mapped[str | None] = mapped_column("floor", String(32), nullable=True)
    room: Mapped[str | None] = mapped_column("room", String(128), nullable=True)

    # Cached metadata (DB column names are fixed: eqclass/manufacturer/model)
    eq_class: Mapped[str | None] = mapped_column("eqclass", String(128), nullable=True)
    category: Mapped[str | None] = mapped_column("category", String(128), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column("manufacturer", String(255), nullable=True)
    model: Mapped[str | None] = mapped_column("model", String(255), nullable=True)

    # Fields we consider when diffing against the DB row.
    _DB_COMPARE_FIELDS: set[str] = {
        "equipment_no",
        "serial_number",
        "ip",
        "name",
        "hostname",
        "landb_serial",
        "landb_description",
        "building",
        "floor",
        "room",
        "eq_class",
        "category",
        "manufacturer",
        "model",
    }

    # --- Converters --------------------------------------------------------

    @classmethod
    def from_ipaddress(cls, ipaddr: CachedIPAddress) -> LanDBIPAddressORM:
        """Convert a cached domain object into an ORM row.

        Args:
            ipaddr: Cached LanDB IP domain object.

        Returns:
            ORM instance ready to be inserted/updated in Postgres.

        Raises:
            LanDBIPAddressORMError: If the required join key (``equipment_no``) is missing.
        """
        equipment_no = _none_if_blank(getattr(ipaddr, "equipment_no", None))
        if not equipment_no:
            raise LanDBIPAddressORMError(
                "LanDB cached IPAddress missing required join key 'equipment_no' (EAM code)."
            )

        return cls(
            equipment_no=equipment_no,
            serial_number=_none_if_blank(getattr(ipaddr, "serial_number", None)),
            ip=_none_if_blank(getattr(ipaddr, "ip", None)),
            name=_none_if_blank(getattr(ipaddr, "name", None)),
            hostname=_none_if_blank(getattr(ipaddr, "hostname", None)),
            landb_serial=_none_if_blank(getattr(ipaddr, "landb_serial", None)),
            landb_description=_none_if_blank(getattr(ipaddr, "landb_description", None)),
            building=_none_if_blank(getattr(ipaddr, "building", None)),
            floor=_none_if_blank(getattr(ipaddr, "floor", None)),
            room=_none_if_blank(getattr(ipaddr, "room", None)),
            eq_class=_none_if_blank(getattr(ipaddr, "eq_class", None)),
            category=_none_if_blank(getattr(ipaddr, "category", None)),
            manufacturer=_none_if_blank(getattr(ipaddr, "manufacturer", None)),
            model=_none_if_blank(getattr(ipaddr, "model", None)),
        )

    def to_ipaddress(self) -> CachedIPAddress:
        """Convert this ORM row back into a ``CachedIPAddress`` domain object.

        Returns:
            A ``CachedIPAddress`` populated from this row.
        """
        payload: dict[str, Any] = {
            "equipment_no": self.equipment_no,
            "serial_number": self.serial_number,
            "ip": self.ip,
            "name": self.name,
            "hostname": self.hostname,
            "landb_serial": self.landb_serial,
            "landb_description": self.landb_description,
            "building": self.building,
            "floor": self.floor,
            "room": self.room,
            "eq_class": self.eq_class,
            "category": self.category,
            "manufacturer": self.manufacturer,
            "model": self.model,
        }

        ipaddr = CachedIPAddress(**payload)
        ipaddr._avtools_compare_fields = set(self._DB_COMPARE_FIELDS)
        return ipaddr
