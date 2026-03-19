from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EAMRec:
    code: str
    serial_number: str | None
    description: str | None
    class_code: str | None = "PJ"
    category_code: str | None = "PROJECTOR"


@dataclass
class LanDBDevice:
    serial_number: str | None
    name: str | None
    location: Any = None


@dataclass
class LanDBIPAddress:
    device: str | None
    name: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None


class _Query:
    def __init__(self, results: list[Any]) -> None:
        self._results = results

    def all(self) -> list[Any]:
        return list(self._results)


class _RecordingManager:
    def __init__(self, results_by_call: list[list[Any]]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results_by_call = list(results_by_call)
        self._i = 0

    def filter(self, **kwargs: Any) -> _Query:
        self.calls.append(dict(kwargs))
        if self._i >= len(self._results_by_call):
            raise AssertionError(f"Unexpected extra filter call: {kwargs}")
        results = self._results_by_call[self._i]
        self._i += 1
        return _Query(results)


def test_get_landb_ipaddresses_uses_expected_filters_and_correlates(
    monkeypatch: Any, avtools_no_db: Any
) -> None:
    """Contract test for LanDB adapter usage.

    Verifies AVTools uses these filter keys:
    - Device.serial_number__in
    - Device.name__in
    - IPAddress.device__serial_number__in
    - IPAddress.device__name__in

    And that correlation logic Device.name == IPAddress.device produces CachedIPAddress
    enriched with EAM keys.
    """

    import avtools.core.av_tools as av_mod

    # EAM snapshot (inputs)
    eam = [
        EAMRec(code="EQ1", serial_number="S1", description="DESC1"),
        # IMPORTANT: AVTools name-fallback matches EAM `description` against LanDB `Device.name`.
        EAMRec(code="EQ2", serial_number="S2", description="DEV2"),
    ]

    # Device lookup:
    # 1) by serial -> only S1 matches
    # 2) by name -> matches DEV2
    device_manager = _RecordingManager(
        results_by_call=[
            [LanDBDevice(serial_number="S1", name="DEV1")],
            [LanDBDevice(serial_number="LS2", name="DEV2")],
        ]
    )

    # IP lookup:
    # 3) by device serial -> only DEV1 returns IP
    # 4) fallback by device name -> DEV2 returns IP (ipv6 only)
    ip_manager = _RecordingManager(
        results_by_call=[
            [LanDBIPAddress(device="DEV1", name="dev1.example", ipv4="192.0.2.1")],
            [LanDBIPAddress(device="DEV2", name="dev2.example", ipv6="2001:db8::2")],
        ]
    )

    class DummyDevice:
        objects = device_manager

    class DummyIPAddress:
        objects = ip_manager

    monkeypatch.setattr(av_mod, "Device", DummyDevice)
    monkeypatch.setattr(av_mod, "IPAddress", DummyIPAddress)

    out = avtools_no_db._get_landb_ipaddresses(eam)  # type: ignore[attr-defined]

    # --- Verify filter kwargs contract ---------------------------------
    assert device_manager.calls[0].get("serial_number__in") == ["S1", "S2"]
    assert device_manager.calls[1].get("name__in") == ["DEV2"]

    # device serials used for IP query are the LanDB device serials, not EAM serials
    assert set(ip_manager.calls[0].get("device__serial_number__in")) == {"S1", "LS2"}
    assert ip_manager.calls[1].get("device__name__in") == ["DEV2"]

    # --- Verify correlation/enrichment output ---------------------------
    assert len(out) == 2

    by_eq = {c.equipment_no: c for c in out}

    assert by_eq["EQ1"].ip == "192.0.2.1"
    assert by_eq["EQ1"].hostname == "dev1.example"
    assert by_eq["EQ1"].landb_description == "DEV1"

    assert by_eq["EQ2"].ip == "2001:db8::2"  # ipv6 selected when ipv4 absent
    assert by_eq["EQ2"].hostname == "dev2.example"
    assert by_eq["EQ2"].landb_description == "DEV2"
