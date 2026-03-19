from __future__ import annotations

from dataclasses import dataclass

from avtools.postgres.inventory.orm.landb_ipaddress import _parse_location


@dataclass
class LocationObj:
    building: str | None = None
    floor: str | None = None
    room: str | None = None


def test_parse_location_handles_dict_tuple_and_object_shapes() -> None:
    assert _parse_location({"building": "B1", "floor": "2", "room": "12"}) == (
        "B1",
        "2",
        "12",
    )

    assert _parse_location(("B2", "3", "07")) == ("B2", "3", "07")

    obj = LocationObj(building="B3", floor="4", room="99")
    assert _parse_location(obj) == ("B3", "4", "99")


def test_parse_location_returns_best_effort_for_unknown_shapes() -> None:
    # Unknown string: treated as room best-effort.
    assert _parse_location("SOMEWHERE") == (None, None, "SOMEWHERE")

    # Missing/None -> all None
    assert _parse_location(None) == (None, None, None)
