"""Pure, DB-free algorithms that mirror the SQL CTEs behind the dashboards.

This package is the foundation layer for CTE-equivalent computations: each
algorithm is a self-contained, unit-testable component with no database, ORM, or
network dependencies. The first of these is :class:`RoomResolver`, the in-memory
reimplementation of the Grafana "Rooms" recursive-CTE room climb.
"""

from avtools.algorithms.room_resolver import (
    AVR_EQCLASS,
    MAX_CLIMB_DEPTH,
    NO_ROOM_NO_PARENT,
    NO_ROOM_NO_POSITION,
    ROOM_CATEGORIES,
    PositionNode,
    RoomResolver,
)

__all__ = [
    "AVR_EQCLASS",
    "MAX_CLIMB_DEPTH",
    "NO_ROOM_NO_PARENT",
    "NO_ROOM_NO_POSITION",
    "ROOM_CATEGORIES",
    "PositionNode",
    "RoomResolver",
]
