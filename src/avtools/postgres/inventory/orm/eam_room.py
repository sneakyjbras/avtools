"""EAM Room cache ORM + domain model.

Defines the ``eam_rooms`` cache table and its :class:`EAMRoom` Pydantic domain
model. ``eam_rooms`` is a precomputed device -> room mapping: it lets Grafana
replace an expensive recursive-CTE room climb with a simple join. The mapping is
produced by :class:`avtools.algorithms.room_resolver.RoomResolver` and refreshed
periodically by ``avtools sync-rooms``.

The read/write split mirrors the other inventory caches (e.g.
``landb_ipaddress``): the resolver/orchestrator produce :class:`EAMRoom`
(Pydantic) objects on the **write** path (``EAMRoomORM.from_room``), and the
client returns :class:`EAMRoom` objects on the **read** path
(``EAMRoomORM.to_room``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, PrivateAttr
from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from avtools.exception.errors import EAMRoomORMError


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for this module."""


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


class EAMRoom(BaseModel):
    """Resolved device -> room mapping (AVTools domain model).

    This is the domain object produced by the room resolver and persisted into
    the ``eam_rooms`` cache table.

    Attributes:
        equipment_no: The device's ``equipmentno`` (primary key / join key).
        parent_position: The device's ``position`` at the last resolution. Acts
            as the change-detection anchor: if a device's position moves, the row
            is re-resolved.
        room_no: The resolved room ``equipmentno``, or one of the resolver
            sentinels (``__NO_ROOM_NO_POSITION__`` / ``__NO_ROOM_NO_PARENT__``).
        resolved_at: When this row was last written.
    """

    equipment_no: str | None
    parent_position: str | None
    room_no: str | None
    resolved_at: datetime | None

    # Private extras (NOT stored / NOT diffed against directly).
    _avtools_compare_fields: set[str] = PrivateAttr(default_factory=set)

    class Config:
        # Pydantic v1 (see pyproject: pydantic >=1.10,<2).
        allow_mutation = True

    def avtools_compare_fields(self) -> set[str]:
        """Return the set of field names to compare when diffing.

        Returns:
            A copy of the compare-field set. ``resolved_at`` is intentionally
            excluded so a re-resolution that yields the same room/position is not
            treated as a change (it would otherwise churn on every run).
        """
        return set(self._avtools_compare_fields)


class EAMRoomORM(Base):
    """PostgreSQL cache of the resolved device -> room mapping."""

    __tablename__ = "eam_rooms"
    __table_args__ = (Index("ix_eam_rooms_room_no", "room_no"),)

    equipment_no: Mapped[str] = mapped_column("equipmentno", String(64), primary_key=True)
    parent_position: Mapped[str | None] = mapped_column(
        "parent_position", String(64), nullable=True
    )
    room_no: Mapped[str | None] = mapped_column("room_no", String(64), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column("resolved_at", DateTime, nullable=False)

    # Fields we consider when diffing a fresh resolution against the DB row.
    _DB_COMPARE_FIELDS: set[str] = {
        "parent_position",
        "room_no",
    }

    # --- Converters --------------------------------------------------------

    @classmethod
    def from_room(cls, room: EAMRoom) -> EAMRoomORM:
        """Convert an :class:`EAMRoom` domain object into an ORM row (write path).

        Args:
            room: Resolved room-mapping domain object.

        Returns:
            ORM instance ready to be inserted/updated in Postgres.

        Raises:
            EAMRoomORMError: If the required primary key (``equipment_no``) is missing.
        """
        equipment_no = _none_if_blank(getattr(room, "equipment_no", None))
        if not equipment_no:
            raise EAMRoomORMError("EAMRoom missing required primary key 'equipment_no'.")

        # `resolved_at` is set on every write; default to now if the caller left it unset.
        resolved_at = getattr(room, "resolved_at", None) or datetime.utcnow()

        return cls(
            equipment_no=equipment_no,
            parent_position=_none_if_blank(getattr(room, "parent_position", None)),
            room_no=_none_if_blank(getattr(room, "room_no", None)),
            resolved_at=resolved_at,
        )

    def to_room(self) -> EAMRoom:
        """Convert this ORM row back into an :class:`EAMRoom` domain object (read path).

        Returns:
            An :class:`EAMRoom` populated from this row, carrying the compare-field
            whitelist used by the diff logic.
        """
        room = EAMRoom(
            equipment_no=self.equipment_no,
            parent_position=self.parent_position,
            room_no=self.room_no,
            resolved_at=self.resolved_at,
        )
        room._avtools_compare_fields = set(self._DB_COMPARE_FIELDS)
        return room

    def __repr__(self) -> str:
        """Return a compact, debug-friendly representation."""
        return (
            "EAMRoomORM("
            f"equipment_no={self.equipment_no!r}, "
            f"parent_position={self.parent_position!r}, "
            f"room_no={self.room_no!r}"
            ")"
        )
