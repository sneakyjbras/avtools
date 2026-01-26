from __future__ import annotations

from dataclasses import dataclass

from avtools.snmp.client import SNMPClient


@dataclass
class Obj:
    a: str | None = None
    b: str | None = None


def test__get_prefers_first_non_empty_attr_and_skips_blank_strings() -> None:
    o = Obj(a="", b="X")
    assert SNMPClient._get(o, "a", "b") == "X"


def test__get_works_for_dicts_too() -> None:
    d = {"a": None, "b": "Y"}
    assert SNMPClient._get(d, "a", "b") == "Y"
