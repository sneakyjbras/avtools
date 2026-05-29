from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ObjA:
    equipmentno: str


@dataclass
class ObjB:
    equipment_no: str


def test_landb_get_id_prefers_equipmentno_then_equipment_no() -> None:
    from avtools.core.av_tools import AVTools

    av = object.__new__(AVTools)

    assert av._landb_get_id(ObjA(equipmentno="EQ-1")) == "EQ-1"
    assert av._landb_get_id(ObjB(equipment_no="EQ-2")) == "EQ-2"


def test_enrich_ipaddress_with_eam_keys_forwards_to_cachedipaddress(
    monkeypatch: Any,
) -> None:
    import avtools.core.av_tools as av_mod
    from avtools.core.av_tools import AVTools

    calls: list[tuple[Any, Any, Any]] = []

    class DummyCached:
        @staticmethod
        def from_equipment_and_ipaddress(
            eam_rec: Any, ip_rec: Any, *, landb_device: Any = None
        ) -> str:
            calls.append((eam_rec, ip_rec, landb_device))
            return "ok"

    monkeypatch.setattr(av_mod, "CachedIPAddress", DummyCached)

    av = object.__new__(AVTools)
    eam = object()
    ip = object()
    dev = object()

    out = av._enrich_ipaddress_with_eam_keys(ip, eam, landb_device=dev)
    assert out == "ok"
    assert calls == [(eam, ip, dev)]
