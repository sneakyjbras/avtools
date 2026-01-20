from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog
from pydantic.v1 import BaseModel

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class DummyEAM(BaseModel):
    # Fields used by _get_landb_ipaddresses()
    code: str
    serial_number: str | None = None
    description: str | None = None

    # Optional extra fields (harmless, sometimes useful for enrichment stubs)
    class_code: str | None = None
    manufacturer_code: str | None = None


class DummyDevice(BaseModel):
    serial_number: str | None = None
    name: str | None = None


class DummyIPAddress(BaseModel):
    # NOTE: In AVTools._get_landb_ipaddresses() correlation is:
    #   Device.name == IPAddress.device   (string)
    device: str | None = None
    ip: str | None = None

    # Only used by our dummy query engine for nested filter simulation
    device_serial_number: str | None = None
    device_name: str | None = None


class DummyCachedIPAddress(BaseModel):
    equipmentno: str
    serialnumber: str | None = None
    ip: str | None = None
    name: str | None = None


class DummyLogger:
    """
    Minimal structlog-like logger capturing events + kwargs.
    AVTools._get_landb_ipaddresses uses .info() and .warning().
    """

    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.warnings: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[tuple[str, dict[str, Any]]] = []
        self.exceptions: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.infos.append((str(event), dict(kwargs)))

    def warning(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((str(event), dict(kwargs)))

    def error(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.errors.append((str(event), dict(kwargs)))

    def exception(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.exceptions.append((str(event), dict(kwargs)))

    def events(self, event: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ev, kw in self.infos + self.warnings + self.errors + self.exceptions:
            if ev == event:
                out.append(kw)
        return out


class DummyQuery:
    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def all(self) -> list[Any]:
        return list(self._items)


class DummyObjects:
    """
    A tiny .objects implementation supporting:
      - Device.objects.filter(serial_number__in=[...]).all()
      - Device.objects.filter(name__in=[...]).all()
      - IPAddress.objects.filter(device__serial_number__in=[...]).all()
      - IPAddress.objects.filter(device__name__in=[...]).all()

    Optional raise_when(kwargs)->Exception lets tests simulate REST-client failures.
    """

    def __init__(
        self,
        items: list[Any],
        *,
        raise_when: Callable[[dict[str, Any]], Exception | None] | None = None,
    ) -> None:
        self._items = list(items)
        self.raise_when = raise_when
        self.filter_calls: list[dict[str, Any]] = []

    def filter(self, **kwargs: Any) -> DummyQuery:
        self.filter_calls.append(dict(kwargs))

        if self.raise_when is not None:
            exc = self.raise_when(kwargs)
            if exc is not None:
                raise exc

        out = list(self._items)

        # Device queries
        if "serial_number__in" in kwargs:
            wanted = set(kwargs["serial_number__in"] or [])
            out = [d for d in out if getattr(d, "serial_number", None) in wanted]

        if "name__in" in kwargs:
            wanted = set(kwargs["name__in"] or [])
            out = [d for d in out if getattr(d, "name", None) in wanted]

        # IPAddress queries (nested selectors in LanDB REST client)
        if "device__serial_number__in" in kwargs:
            wanted = set(kwargs["device__serial_number__in"] or [])
            out = [
                ip for ip in out if getattr(ip, "device_serial_number", None) in wanted
            ]

        if "device__name__in" in kwargs:
            wanted = set(kwargs["device__name__in"] or [])
            out = [ip for ip in out if getattr(ip, "device_name", None) in wanted]

        return DummyQuery(out)


def make_avtools_for_tests() -> AVTools:
    # Bypass __init__ so we don't touch PostgresClient.
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    return av


def _dump_model(m: Any) -> dict[str, Any]:
    if hasattr(m, "dict"):
        return m.dict()
    return dict(vars(m))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_landb_ipaddresses_returns_empty_when_no_eam_records():
    av = make_avtools_for_tests()
    av.logger = DummyLogger()  # type: ignore[assignment]

    assert av._get_landb_ipaddresses([]) == []


def test_get_landb_ipaddresses_matches_by_serial_and_name_and_enriches(monkeypatch):
    """
    _get_landb_ipaddresses should:
      - match Devices by EAM serial_number
      - fallback match Devices by EAM description (Device.name)
      - then fetch IPAddresses via device serials, and fallback by device names
      - correlate using Device.name == IPAddress.device
      - enrich each IPAddress with EAM keys via _enrich_ipaddress_with_eam_keys
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    # Patch the module-level Device / IPAddress used by AVTools._get_landb_ipaddresses
    monkeypatch.setattr(core, "Device", DummyDevice)
    monkeypatch.setattr(core, "IPAddress", DummyIPAddress)

    eam_records = [
        DummyEAM(code="DEV-34", serial_number="SN-34", description="DESC-34"),
        DummyEAM(
            code="DEV-38", serial_number="SN-38", description="DESC-38"
        ),  # no device in LanDB
        DummyEAM(
            code="DEV-39", serial_number=None, description="NAME-39"
        ),  # match by name
        DummyEAM(
            code="DEV-404", serial_number="SN-404", description="DESC-404"
        ),  # device exists but no IP
        DummyEAM(code="DEV-2137", serial_number="SN-2137", description="DESC-2137"),
    ]

    devices = [
        DummyDevice(serial_number="SN-34", name="LAN-34"),
        DummyDevice(serial_number="SN-39", name="NAME-39"),
        DummyDevice(serial_number="SN-404", name="LAN-404"),
        DummyDevice(serial_number="SN-2137", name="LAN-2137"),
    ]
    DummyDevice.objects = DummyObjects(devices)  # type: ignore[attr-defined]

    ips = [
        DummyIPAddress(
            device="LAN-34",
            ip="10.0.0.34",
            device_serial_number="SN-34",
            device_name="LAN-34",
        ),
        DummyIPAddress(
            device="NAME-39",
            ip="10.0.0.39",
            device_serial_number="SN-39",
            device_name="NAME-39",
        ),
        # NOTE: no entry for LAN-404 -> that EAM record should end up missing_ip
        DummyIPAddress(
            device="LAN-2137",
            ip="10.0.0.2137",
            device_serial_number="SN-2137",
            device_name="LAN-2137",
        ),
    ]
    DummyIPAddress.objects = DummyObjects(ips)  # type: ignore[attr-defined]

    enrich_calls: list[tuple[DummyIPAddress, DummyEAM, DummyDevice | None]] = []

    def fake_enrich(
        self: AVTools,
        ip_rec: DummyIPAddress,
        eam_rec: DummyEAM,
        *,
        landb_device: DummyDevice | None = None,
    ) -> DummyCachedIPAddress:
        enrich_calls.append((ip_rec, eam_rec, landb_device))
        return DummyCachedIPAddress(
            equipmentno=str(eam_rec.code),
            serialnumber=eam_rec.serial_number,
            ip=ip_rec.ip,
            name=ip_rec.device,
        )

    monkeypatch.setattr(AVTools, "_enrich_ipaddress_with_eam_keys", fake_enrich)

    out = av._get_landb_ipaddresses(eam_records)

    # Only DEV-34 (serial match), DEV-39 (name match), DEV-2137 survive.
    assert [o.equipmentno for o in out] == ["DEV-34", "DEV-39", "DEV-2137"]
    assert [o.ip for o in out] == ["10.0.0.34", "10.0.0.39", "10.0.0.2137"]

    # Ensure correlation is based on Device.name == IPAddress.device
    assert all(ip.device == (dev.name if dev else None) for ip, _, dev in enrich_calls)

    # Summary logs should have been emitted
    assert logger.events("landb_device_match_summary")
    assert logger.events("landb_ipaddress_match_summary")


def test_get_landb_ipaddresses_prefers_serial_match_over_name(monkeypatch):
    """
    If an EAM record could match both by serial and by name, serial match should win.
    """
    av = make_avtools_for_tests()
    av.logger = DummyLogger()  # type: ignore[assignment]

    monkeypatch.setattr(core, "Device", DummyDevice)
    monkeypatch.setattr(core, "IPAddress", DummyIPAddress)

    eam = DummyEAM(code="DEV-X", serial_number="SN-X", description="DESC-SAME")

    # Two devices:
    # - serial match device (should be chosen)
    # - name match device (should be ignored for this EAM record)
    dev_serial = DummyDevice(serial_number="SN-X", name="LAN-SERIAL")
    dev_name = DummyDevice(serial_number="SN-Y", name="DESC-SAME")

    DummyDevice.objects = DummyObjects([dev_serial, dev_name])  # type: ignore[attr-defined]

    DummyIPAddress.objects = DummyObjects(
        [
            DummyIPAddress(
                device="LAN-SERIAL",
                ip="10.0.0.1",
                device_serial_number="SN-X",
                device_name="LAN-SERIAL",
            ),
            DummyIPAddress(
                device="DESC-SAME",
                ip="10.0.0.2",
                device_serial_number="SN-Y",
                device_name="DESC-SAME",
            ),
        ]
    )  # type: ignore[attr-defined]

    chosen: list[DummyDevice | None] = []

    def fake_enrich(
        self: AVTools,
        ip_rec: DummyIPAddress,
        eam_rec: DummyEAM,
        *,
        landb_device: DummyDevice | None = None,
    ) -> DummyCachedIPAddress:
        chosen.append(landb_device)
        return DummyCachedIPAddress(
            equipmentno=str(eam_rec.code),
            serialnumber=eam_rec.serial_number,
            ip=ip_rec.ip,
            name=ip_rec.device,
        )

    monkeypatch.setattr(AVTools, "_enrich_ipaddress_with_eam_keys", fake_enrich)

    out = av._get_landb_ipaddresses([eam])

    assert [o.ip for o in out] == ["10.0.0.1"]
    assert chosen and chosen[0] is not None
    assert chosen[0].serial_number == "SN-X"
    assert chosen[0].name == "LAN-SERIAL"


def test_get_landb_ipaddresses_device_fetch_by_serial_exception_falls_back_to_name(
    monkeypatch,
):
    """
    If Device fetch by serial raises, function should log a warning and still try name-based device lookup.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    monkeypatch.setattr(core, "Device", DummyDevice)
    monkeypatch.setattr(core, "IPAddress", DummyIPAddress)

    eam = DummyEAM(code="DEV-1", serial_number="SN-1", description="NAME-1")

    def raise_on_serial(kwargs: dict[str, Any]) -> Exception | None:
        if "serial_number__in" in kwargs:
            return RuntimeError("boom devices-by-serial")
        return None

    DummyDevice.objects = DummyObjects(
        [DummyDevice(serial_number="SN-1", name="NAME-1")],
        raise_when=raise_on_serial,
    )  # type: ignore[attr-defined]

    DummyIPAddress.objects = DummyObjects(
        [
            DummyIPAddress(
                device="NAME-1",
                ip="10.0.0.1",
                device_serial_number="SN-1",
                device_name="NAME-1",
            )
        ]
    )  # type: ignore[attr-defined]

    monkeypatch.setattr(
        AVTools,
        "_enrich_ipaddress_with_eam_keys",
        lambda self, ip, eam_rec, *, landb_device=None: DummyCachedIPAddress(
            equipmentno=str(eam_rec.code),
            serialnumber=eam_rec.serial_number,
            ip=ip.ip,
            name=ip.device,
        ),
    )

    out = av._get_landb_ipaddresses([eam])

    assert [o.equipmentno for o in out] == ["DEV-1"]
    assert logger.events("landb_device_fetch_by_serial_failed")


def test_get_landb_ipaddresses_ip_fetch_by_serial_exception_falls_back_to_name(
    monkeypatch,
):
    """
    If IPAddress fetch by serial raises, function should log a warning and still try name-based IP lookup.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    monkeypatch.setattr(core, "Device", DummyDevice)
    monkeypatch.setattr(core, "IPAddress", DummyIPAddress)

    eam = DummyEAM(code="DEV-1", serial_number="SN-1", description="DESC-1")
    dev = DummyDevice(serial_number="SN-1", name="LAN-1")

    DummyDevice.objects = DummyObjects([dev])  # type: ignore[attr-defined]

    def raise_on_ip_serial(kwargs: dict[str, Any]) -> Exception | None:
        if "device__serial_number__in" in kwargs:
            return RuntimeError("boom ips-by-serial")
        return None

    # Only returned via name-based fallback
    DummyIPAddress.objects = DummyObjects(
        [
            DummyIPAddress(
                device="LAN-1",
                ip="10.0.0.1",
                device_serial_number="SN-1",
                device_name="LAN-1",
            )
        ],
        raise_when=raise_on_ip_serial,
    )  # type: ignore[attr-defined]

    monkeypatch.setattr(
        AVTools,
        "_enrich_ipaddress_with_eam_keys",
        lambda self, ip, eam_rec, *, landb_device=None: DummyCachedIPAddress(
            equipmentno=str(eam_rec.code),
            serialnumber=eam_rec.serial_number,
            ip=ip.ip,
            name=ip.device,
        ),
    )

    out = av._get_landb_ipaddresses([eam])

    assert [o.ip for o in out] == ["10.0.0.1"]
    assert logger.events("landb_ipaddress_fetch_by_serial_failed")


def test_get_landb_ipaddresses_does_not_mutate_eam_records(monkeypatch):
    av = make_avtools_for_tests()
    av.logger = DummyLogger()  # type: ignore[assignment]

    monkeypatch.setattr(core, "Device", DummyDevice)
    monkeypatch.setattr(core, "IPAddress", DummyIPAddress)

    eam_records = [
        DummyEAM(code="DEV-1", serial_number="SN-1", description="LAN-1"),
        DummyEAM(code="DEV-2", serial_number="SN-2", description="LAN-2"),
    ]
    before = [_dump_model(r) for r in eam_records]

    DummyDevice.objects = DummyObjects(
        [
            DummyDevice(serial_number="SN-1", name="LAN-1"),
            DummyDevice(serial_number="SN-2", name="LAN-2"),
        ]
    )  # type: ignore[attr-defined]

    DummyIPAddress.objects = DummyObjects(
        [
            DummyIPAddress(
                device="LAN-1",
                ip="10.0.0.1",
                device_serial_number="SN-1",
                device_name="LAN-1",
            ),
            DummyIPAddress(
                device="LAN-2",
                ip="10.0.0.2",
                device_serial_number="SN-2",
                device_name="LAN-2",
            ),
        ]
    )  # type: ignore[attr-defined]

    monkeypatch.setattr(
        AVTools,
        "_enrich_ipaddress_with_eam_keys",
        lambda self, ip, eam_rec, *, landb_device=None: DummyCachedIPAddress(
            equipmentno=str(eam_rec.code),
            serialnumber=eam_rec.serial_number,
            ip=ip.ip,
            name=ip.device,
        ),
    )

    _ = av._get_landb_ipaddresses(eam_records)
    after = [_dump_model(r) for r in eam_records]

    assert after == before
