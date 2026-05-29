from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append(event)

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append(event)

    def error(self, event: str, **kwargs: Any) -> None:
        pass

    def exception(self, event: str, **kwargs: Any) -> None:
        pass


@dataclass
class EAMRec:
    serial_number: str | None
    description: str | None
    code: str = "EQ"


@dataclass
class DummyDevice:
    serial_number: str | None
    name: str | None


@dataclass
class DummyIPAddress:
    device: str | None
    ipv4: str | None = None
    ipv6: str | None = None


class _QS:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)


class _Manager:
    def __init__(self, *, on_filter: dict[str, Any]) -> None:
        # on_filter: mapping key -> list or Exception
        self._map = on_filter

    def filter(self, **kwargs: Any) -> _QS:
        key = str(sorted(kwargs.items()))
        resp = self._map.get(key, [])
        if isinstance(resp, Exception):
            raise resp
        return _QS(list(resp))


def _make_avtools() -> Any:
    from avtools.core.av_tools import AVTools

    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av._landb_initialized = True
    return av


def test_get_landb_ipaddresses_warns_when_device_fetch_by_name_raises(
    monkeypatch: Any,
) -> None:
    import avtools.core.av_tools as core

    # serial lookup returns a device for S1
    # name lookup raises
    serial_key = str(sorted({"serial_number__in": ["S1"]}.items()))
    name_key = str(sorted({"name__in": ["NAME2"]}.items()))
    Device = type(
        "Device",
        (),
        {
            "objects": _Manager(
                on_filter={
                    serial_key: [DummyDevice(serial_number="S1", name="DEV1")],
                    name_key: RuntimeError("boom"),
                }
            )
        },
    )
    core_ip_key = str(sorted({"device__serial_number__in": ["S1"]}.items()))
    IPAddress = type(
        "IPAddress",
        (),
        {
            "objects": _Manager(
                on_filter={core_ip_key: [DummyIPAddress(device="DEV1", ipv4="192.0.2.1")]}
            )
        },
    )

    monkeypatch.setattr(core, "Device", Device)
    monkeypatch.setattr(core, "IPAddress", IPAddress)

    av = _make_avtools()

    class CachedOut:
        def __init__(self, ip: str | None) -> None:
            self.ip = ip

    def enrich(ip_rec: Any, eam_rec: Any, *, landb_device: Any = None) -> CachedOut:
        return CachedOut(ip=getattr(ip_rec, "ipv4", None) or getattr(ip_rec, "ipv6", None))

    av._enrich_ipaddress_with_eam_keys = enrich  # type: ignore[assignment]

    eam = [
        EAMRec(serial_number="S1", description="DEV1"),
        EAMRec(serial_number=None, description="NAME2"),
    ]
    out = av._get_landb_ipaddresses(eam)

    assert out and out[0].ip == "192.0.2.1"
    assert "landb_device_fetch_by_name_failed" in av.logger.warnings
    assert "landb_eam_equipment_not_found" in av.logger.warnings


def test_get_landb_ipaddresses_warns_when_ip_fetch_by_name_raises(
    monkeypatch: Any,
) -> None:
    import avtools.core.av_tools as core

    serial_key = str(sorted({"serial_number__in": ["S1"]}.items()))
    Device = type(
        "Device",
        (),
        {
            "objects": _Manager(
                on_filter={serial_key: [DummyDevice(serial_number="S1", name="DEV1")]}
            )
        },
    )

    ip_by_serial_key = str(sorted({"device__serial_number__in": ["S1"]}.items()))
    ip_by_name_key = str(sorted({"device__name__in": ["DEV1"]}.items()))
    IPAddress = type(
        "IPAddress",
        (),
        {
            "objects": _Manager(
                on_filter={ip_by_serial_key: [], ip_by_name_key: RuntimeError("boom")}
            )
        },
    )

    monkeypatch.setattr(core, "Device", Device)
    monkeypatch.setattr(core, "IPAddress", IPAddress)

    av = _make_avtools()

    # even if enrich returns something, we won't reach it (no ip record)
    av._enrich_ipaddress_with_eam_keys = lambda *a, **k: object()  # type: ignore[assignment]

    out = av._get_landb_ipaddresses([EAMRec(serial_number="S1", description="DEV1")])

    assert out == []
    assert "landb_ipaddress_fetch_by_name_failed" in av.logger.warnings
    assert "landb_ipaddress_not_found_for_equipment" in av.logger.warnings


def test_get_landb_ipaddresses_skips_devices_without_target_ip(
    monkeypatch: Any,
) -> None:
    import avtools.core.av_tools as core

    serial_key = str(sorted({"serial_number__in": ["S1"]}.items()))
    Device = type(
        "Device",
        (),
        {
            "objects": _Manager(
                on_filter={serial_key: [DummyDevice(serial_number="S1", name="DEV1")]}
            )
        },
    )
    ip_by_serial_key = str(sorted({"device__serial_number__in": ["S1"]}.items()))
    IPAddress = type(
        "IPAddress",
        (),
        {
            "objects": _Manager(
                on_filter={ip_by_serial_key: [DummyIPAddress(device="DEV1", ipv4=None, ipv6=None)]}
            )
        },
    )

    monkeypatch.setattr(core, "Device", Device)
    monkeypatch.setattr(core, "IPAddress", IPAddress)

    av = _make_avtools()

    class CachedOut:
        def __init__(self) -> None:
            self.ip = None

    av._enrich_ipaddress_with_eam_keys = lambda *a, **k: CachedOut()  # type: ignore[assignment]

    out = av._get_landb_ipaddresses([EAMRec(serial_number="S1", description="DEV1")])

    assert out == []
    assert "landb_ipaddress_not_found_for_equipment" in av.logger.warnings


def test_get_landb_ipaddresses_warns_when_device_fetch_by_serial_raises(
    monkeypatch: Any,
) -> None:
    import avtools.core.av_tools as core

    serial_key = str(sorted({"serial_number__in": ["S1"]}.items()))
    name_key = str(sorted({"name__in": ["DEV1"]}.items()))

    Device = type(
        "Device",
        (),
        {
            "objects": _Manager(
                on_filter={
                    serial_key: RuntimeError("boom"),
                    name_key: [DummyDevice(serial_number="S1", name="DEV1")],
                }
            )
        },
    )

    ip_by_serial_key = str(sorted({"device__serial_number__in": ["S1"]}.items()))
    IPAddress = type(
        "IPAddress",
        (),
        {
            "objects": _Manager(
                on_filter={ip_by_serial_key: [DummyIPAddress(device="DEV1", ipv4="192.0.2.9")]}
            )
        },
    )

    monkeypatch.setattr(core, "Device", Device)
    monkeypatch.setattr(core, "IPAddress", IPAddress)

    av = _make_avtools()

    class CachedOut:
        def __init__(self, ip: str) -> None:
            self.ip = ip

    av._enrich_ipaddress_with_eam_keys = lambda ip_rec, eam_rec, *, landb_device=None: CachedOut(ip=getattr(ip_rec, "ipv4"))  # type: ignore[assignment]

    out = av._get_landb_ipaddresses([EAMRec(serial_number="S1", description="DEV1")])

    assert out and out[0].ip == "192.0.2.9"
    assert "landb_device_fetch_by_serial_failed" in av.logger.warnings
