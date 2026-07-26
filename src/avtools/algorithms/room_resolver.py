"""Room-climb resolver: pure, DB-free reimplementation of the Rooms CTE.

This module reproduces, in memory, the recursive-CTE logic that the Grafana
"Rooms" dashboard uses to map each device to the AV room it lives in. The SQL
climbs the EAM *position* tree from a device's anchor position up through
``parentasset`` links and picks the topmost node that qualifies as an AV room.

It is the first algorithm in :mod:`avtools.algorithms`: the home for pure,
unit-testable reimplementations of the SQL CTEs backing the dashboards. It has
**no** database, ORM, or network dependencies so it can be exercised in
isolation and reused by any persistence/orchestration layer built on top.

Canonical source
----------------
``av-tools-grafana`` panel *Rooms Table* (recursive CTE ``climb`` /
``pos_to_room`` / ``no_parent_positions`` / ``no_position_devices``). The
constants below (:data:`AVR_EQCLASS`, :data:`ROOM_CATEGORIES`,
:data:`NO_ROOM_NO_POSITION`, :data:`NO_ROOM_NO_PARENT`, :data:`MAX_CLIMB_DEPTH`)
mirror that CTE and must stay in sync with it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# CTE-equivalent constants (keep in lockstep with the Rooms dashboard CTE)
# ---------------------------------------------------------------------------

#: A position node is an AV room iff ``eqclass == AVR_EQCLASS`` and its
#: ``category`` is one of :data:`ROOM_CATEGORIES` (CTE: ``rp.eqclass = 'AVR'``).
AVR_EQCLASS = "AVR"

#: Allowed room categories (CTE: ``rp.category IN ('AV-VCR','AV-VIS','AV-MRO')``).
ROOM_CATEGORIES: frozenset[str] = frozenset({"AV-VCR", "AV-VIS", "AV-MRO"})

#: Device position is NULL or absent from ``eam_positions`` (CTE:
#: ``no_position_devices`` / ``'__NO_ROOM_NO_POSITION__'``).
NO_ROOM_NO_POSITION = "__NO_ROOM_NO_POSITION__"

#: Position exists but its chain never reaches an AV room (CTE:
#: ``no_parent_positions`` / ``'__NO_ROOM_NO_PARENT__'``).
NO_ROOM_NO_PARENT = "__NO_ROOM_NO_PARENT__"

#: Maximum upward climb depth (CTE guard: ``c.depth < 100``).
MAX_CLIMB_DEPTH = 100


@dataclass(frozen=True, slots=True)
class PositionNode:
    """An EAM position row reduced to the fields the climb needs.

    Attributes:
        equipment_no: The position's ``equipmentno`` (its identity / join key).
        parent_asset: The ``parentasset`` link climbed upward (``None`` at a root).
        eq_class: The ``eqclass`` value (``'AVR'`` marks an AV room).
        category: The ``category`` value (see :data:`ROOM_CATEGORIES`).
    """

    equipment_no: str
    parent_asset: str | None = None
    eq_class: str | None = None
    category: str | None = None


class RoomResolver:
    """Resolve a device's anchor position to its topmost AV room id.

    The resolver indexes positions by ``equipmentno`` once, then answers
    :meth:`resolve_one` for every device. Results are memoized per anchor
    position so many devices sharing a position climb the tree only once.
    """

    def __init__(self, positions: Iterable[PositionNode]) -> None:
        """Build the resolver over a collection of position nodes.

        Args:
            positions: The full set of EAM position rows (order irrelevant).
        """
        self._index: dict[str, PositionNode] = self.build_index(positions)
        self._cache: dict[str | None, str] = {}

    # --- construction ----------------------------------------------------

    @staticmethod
    def build_index(positions: Iterable[PositionNode]) -> dict[str, PositionNode]:
        """Index position nodes by ``equipmentno``.

        Args:
            positions: Position nodes to index.

        Returns:
            Mapping of ``equipmentno`` -> :class:`PositionNode`. On duplicate ids
            the last one wins, mirroring the primary-key semantics of the table.
        """
        return {node.equipment_no: node for node in positions if node.equipment_no is not None}

    @classmethod
    def from_rows(
        cls, rows: Iterable[tuple[str, str | None, str | None, str | None]]
    ) -> RoomResolver:
        """Build a resolver from raw ``(equipmentno, parentasset, eqclass, category)`` rows.

        This is the lean entry point used by the persistence layer, which selects
        exactly those four columns rather than hydrating full ORM objects.

        Args:
            rows: Iterable of 4-tuples in column order.

        Returns:
            A ready-to-use :class:`RoomResolver`.
        """
        return cls(PositionNode(r[0], r[1], r[2], r[3]) for r in rows)

    # --- predicates ------------------------------------------------------

    @classmethod
    def is_room(cls, node: PositionNode) -> bool:
        """Return ``True`` iff the node qualifies as an AV room.

        Args:
            node: The position node to test.

        Returns:
            ``True`` when ``eq_class == AVR_EQCLASS`` and ``category`` is in
            :data:`ROOM_CATEGORIES` (byte-for-byte the CTE's room predicate).
        """
        return node.eq_class == AVR_EQCLASS and node.category in ROOM_CATEGORIES

    # --- resolution ------------------------------------------------------

    def resolve_one(self, position: str | None) -> str:
        """Resolve a single device anchor position to a room id or sentinel.

        Args:
            position: The device's ``position`` (its anchor in the position tree).

        Returns:
            The resolved room ``equipmentno``, or one of the sentinels
            :data:`NO_ROOM_NO_POSITION` / :data:`NO_ROOM_NO_PARENT`.
        """
        if position in self._cache:
            return self._cache[position]
        room_no = self._resolve_uncached(position)
        self._cache[position] = room_no
        return room_no

    def _resolve_uncached(self, position: str | None) -> str:
        """Compute the room id for an anchor position without consulting the cache.

        Args:
            position: The device's anchor position.

        Returns:
            The resolved room id or a sentinel (see :meth:`resolve_one`).
        """
        anchor = self._index.get(position) if position is not None else None
        if anchor is None:
            return NO_ROOM_NO_POSITION
        room_no = self._climb_to_topmost_room(anchor)
        return room_no if room_no is not None else NO_ROOM_NO_PARENT

    def _climb_to_topmost_room(self, anchor: PositionNode) -> str | None:
        """Climb ``parentasset`` links and return the topmost AV room id.

        The chain is linear (one parent per node), so the CTE's
        ``ORDER BY depth DESC, cur_pos`` tie-break degenerates to "the last room
        seen while climbing" — i.e. the greatest-depth (topmost) room.

        Args:
            anchor: The starting position node (already known to exist).

        Returns:
            The ``equipmentno`` of the topmost AV room in the chain, or ``None``
            if the chain terminates without reaching any room.
        """
        node = anchor
        visited: set[str] = {node.equipment_no}
        topmost_room: str | None = node.equipment_no if self.is_room(node) else None

        for _ in range(MAX_CLIMB_DEPTH):
            parent = self._next_parent(node, visited)
            if parent is None:
                break
            visited.add(parent.equipment_no)
            node = parent
            if self.is_room(node):
                topmost_room = node.equipment_no

        return topmost_room

    def _next_parent(self, node: PositionNode, visited: set[str]) -> PositionNode | None:
        """Return the next node to climb to, applying the CTE's join and guards.

        Args:
            node: The current position node.
            visited: Equipment numbers already visited on this chain (cycle guard).

        Returns:
            The parent :class:`PositionNode`, or ``None`` to stop the climb when
            there is no parent, the parent was already visited (cycle), or the
            parent reference does not resolve to a known position (broken link).
        """
        parent_ref = node.parent_asset
        if parent_ref is None or parent_ref in visited:
            return None
        return self._index.get(parent_ref)
