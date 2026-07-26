"""Unit tests for the pure, DB-free RoomResolver (CTE-equivalent room climb).

These mirror the semantics of the recursive CTE in the Grafana Rooms dashboard:
climb the EAM position tree from a device's anchor position and pick the topmost
AV room, else emit one of the two sentinels. All data is synthetic and in-memory.
"""

from __future__ import annotations

import pytest

from avtools.algorithms.room_resolver import (
    AVR_EQCLASS,
    MAX_CLIMB_DEPTH,
    NO_ROOM_NO_PARENT,
    NO_ROOM_NO_POSITION,
    ROOM_CATEGORIES,
    PositionNode,
    RoomResolver,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def room(equipment_no: str, parent: str | None, category: str = "AV-VCR") -> PositionNode:
    """Build an AV-room position node (eqclass AVR + allowed category)."""
    return PositionNode(equipment_no, parent, AVR_EQCLASS, category)


def plain(equipment_no: str, parent: str | None, eqclass: str = "AVX") -> PositionNode:
    """Build a non-room position node."""
    return PositionNode(equipment_no, parent, eqclass, "AV-OTHER")


# ---------------------------------------------------------------------------
# Constants match the canonical CTE
# ---------------------------------------------------------------------------


def test_constants_match_cte() -> None:
    """The resolver constants must match the dashboard CTE byte-for-byte."""
    assert AVR_EQCLASS == "AVR"
    assert ROOM_CATEGORIES == frozenset({"AV-VCR", "AV-VIS", "AV-MRO"})
    assert NO_ROOM_NO_POSITION == "__NO_ROOM_NO_POSITION__"
    assert NO_ROOM_NO_PARENT == "__NO_ROOM_NO_PARENT__"
    assert MAX_CLIMB_DEPTH == 100


@pytest.mark.parametrize("category", ["AV-VCR", "AV-VIS", "AV-MRO"])
def test_is_room_true_for_each_allowed_category(category: str) -> None:
    """A node is a room only for AVR + one of the three allowed categories."""
    assert RoomResolver.is_room(PositionNode("R", None, "AVR", category)) is True


@pytest.mark.parametrize(
    "node",
    [
        PositionNode("N", None, "AVX", "AV-VCR"),  # wrong eqclass
        PositionNode("N", None, "AVR", "AV-OTHER"),  # wrong category
        PositionNode("N", None, None, None),  # missing both
    ],
)
def test_is_room_false_for_non_rooms(node: PositionNode) -> None:
    """Non-AVR eqclass or a category outside the whitelist is not a room."""
    assert RoomResolver.is_room(node) is False


# ---------------------------------------------------------------------------
# Normal climb
# ---------------------------------------------------------------------------


def test_normal_climb_reaches_avr_room() -> None:
    """A device climbs through plain positions up to the AVR room ancestor."""
    positions = [
        plain("P-LEAF", "P-MID"),
        plain("P-MID", "ROOM-1"),
        room("ROOM-1", None),
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("P-LEAF") == "ROOM-1"


def test_anchor_itself_is_the_room() -> None:
    """When the anchor position is itself an AVR room, it resolves to itself."""
    resolver = RoomResolver([room("ROOM-1", None)])
    assert resolver.resolve_one("ROOM-1") == "ROOM-1"


# ---------------------------------------------------------------------------
# Topmost tie-break: two AVR nodes in the chain -> pick the deepest (topmost)
# ---------------------------------------------------------------------------


def test_topmost_room_wins_when_two_avr_nodes_exist() -> None:
    """With two AVR rooms in the chain, the topmost (greatest climb depth) wins."""
    positions = [
        plain("P-LEAF", "ROOM-LOW"),
        room("ROOM-LOW", "ROOM-HIGH"),  # depth 1
        room("ROOM-HIGH", None),  # depth 2 (topmost)
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("P-LEAF") == "ROOM-HIGH"


def test_topmost_room_wins_even_when_anchor_is_a_room() -> None:
    """A room anchor is superseded by a higher room ancestor."""
    positions = [
        room("ROOM-LOW", "ROOM-HIGH"),  # anchor + room, depth 0
        room("ROOM-HIGH", None),  # depth 1 (topmost)
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("ROOM-LOW") == "ROOM-HIGH"


# ---------------------------------------------------------------------------
# NULL / missing position -> NO_POSITION sentinel
# ---------------------------------------------------------------------------


def test_null_position_returns_no_position_sentinel() -> None:
    """A device with a NULL position resolves to the NO_POSITION sentinel."""
    resolver = RoomResolver([room("ROOM-1", None)])
    assert resolver.resolve_one(None) == NO_ROOM_NO_POSITION


def test_position_absent_from_positions_returns_no_position_sentinel() -> None:
    """A position that does not exist in eam_positions -> NO_POSITION sentinel."""
    resolver = RoomResolver([room("ROOM-1", None)])
    assert resolver.resolve_one("P-UNKNOWN") == NO_ROOM_NO_POSITION


# ---------------------------------------------------------------------------
# Chain ends without a room -> NO_PARENT sentinel
# ---------------------------------------------------------------------------


def test_chain_ends_without_room_returns_no_parent_sentinel() -> None:
    """A fully-resolvable chain that never hits an AVR room -> NO_PARENT sentinel."""
    positions = [
        plain("P-LEAF", "P-MID"),
        plain("P-MID", "P-ROOT"),
        plain("P-ROOT", None),
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("P-LEAF") == NO_ROOM_NO_PARENT


def test_avr_eqclass_but_wrong_category_is_not_a_room() -> None:
    """An AVR node whose category is outside the whitelist is not a room -> NO_PARENT."""
    positions = [
        plain("P-LEAF", "NOT-A-ROOM"),
        PositionNode("NOT-A-ROOM", None, "AVR", "AV-OTHER"),
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("P-LEAF") == NO_ROOM_NO_PARENT


# ---------------------------------------------------------------------------
# Broken parentasset -> NO_PARENT sentinel
# ---------------------------------------------------------------------------


def test_broken_parentasset_returns_no_parent_sentinel() -> None:
    """A parentasset pointing to a non-existent position stops the climb -> NO_PARENT."""
    positions = [
        plain("P-LEAF", "P-GONE"),  # P-GONE does not exist in the index
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("P-LEAF") == NO_ROOM_NO_PARENT


def test_broken_parentasset_after_a_room_still_returns_that_room() -> None:
    """A room found before the chain breaks is still returned (room takes priority)."""
    positions = [
        plain("P-LEAF", "ROOM-1"),
        room("ROOM-1", "P-GONE"),  # broken above the room
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("P-LEAF") == "ROOM-1"


# ---------------------------------------------------------------------------
# Cycle handling (no infinite loop)
# ---------------------------------------------------------------------------


def test_two_node_cycle_is_handled_without_infinite_loop() -> None:
    """A 2-node parent cycle terminates and yields NO_PARENT (no room, no hang)."""
    positions = [
        plain("A", "B"),
        plain("B", "A"),
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("A") == NO_ROOM_NO_PARENT


def test_self_cycle_is_handled() -> None:
    """A self-referential parent link terminates immediately."""
    positions = [plain("A", "A")]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("A") == NO_ROOM_NO_PARENT


def test_cycle_with_room_returns_the_room() -> None:
    """A cycle that passes through a room still returns that room (guard, not crash)."""
    positions = [
        plain("A", "ROOM-1"),
        room("ROOM-1", "A"),  # cycle back to A
    ]
    resolver = RoomResolver(positions)
    assert resolver.resolve_one("A") == "ROOM-1"


def test_deep_chain_beyond_max_depth_is_bounded() -> None:
    """A chain longer than MAX_CLIMB_DEPTH stops climbing and does not reach the top room."""
    # Build a linear chain P0 -> P1 -> ... -> P(MAX+5), with the only room at the very top.
    depth = MAX_CLIMB_DEPTH + 5
    positions = [plain(f"P{i}", f"P{i + 1}") for i in range(depth)]
    positions.append(room(f"P{depth}", None))
    resolver = RoomResolver(positions)
    # The top room sits beyond the depth guard, so the climb never reaches it.
    assert resolver.resolve_one("P0") == NO_ROOM_NO_PARENT
    # A device anchored close enough to the top *does* reach the room.
    assert resolver.resolve_one(f"P{depth - 1}") == f"P{depth}"


# ---------------------------------------------------------------------------
# Construction helpers + memoization
# ---------------------------------------------------------------------------


def test_from_rows_builds_equivalent_resolver() -> None:
    """from_rows() accepts raw (equipmentno, parentasset, eqclass, category) tuples."""
    rows = [
        ("P-LEAF", "ROOM-1", "AVX", "AV-OTHER"),
        ("ROOM-1", None, "AVR", "AV-MRO"),
    ]
    resolver = RoomResolver.from_rows(rows)
    assert resolver.resolve_one("P-LEAF") == "ROOM-1"


def test_resolve_one_is_memoized_per_position() -> None:
    """Repeated resolution of the same anchor returns the cached result."""
    resolver = RoomResolver([room("ROOM-1", None)])
    first = resolver.resolve_one("ROOM-1")
    assert resolver.resolve_one("ROOM-1") == first
    assert "ROOM-1" in resolver._cache


def test_build_index_last_write_wins_on_duplicate_ids() -> None:
    """Duplicate equipmentno keeps the last node, mirroring PK semantics."""
    index = RoomResolver.build_index(
        [
            plain("P", "OLD"),
            plain("P", "NEW"),
        ]
    )
    assert index["P"].parent_asset == "NEW"
