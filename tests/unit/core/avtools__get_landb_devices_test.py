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
            out = [ip for ip in out if getattr(ip, "device_serial_number", None) in wanted]

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
        DummyEAM(code="DEV-38", serial_number="SN-38", description="DESC-38"),  # no device in LanDB
        DummyEAM(code="DEV-39", serial_number=None, description="NAME-39"),  # match by name
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


# ---------------------------------------------------------------------------
# W1 — `__in` chunking (LanDB/Tomcat 1000-request-parameter ceiling)
#
# The REST client spends ONE query parameter per `__in` value plus `_limit` and
# `_offset`, so a single un-chunked call with the production fleet (3749 serials)
# is rejected with HTTP 422 and the whole fetch comes back empty.
# ---------------------------------------------------------------------------


class ChunkRecordingManager:
    """`.objects` double that records each chunk and replays canned responses.

    Unlike DummyObjects this does not filter: each call returns the pre-canned
    response for that call index, which lets tests place specific records in
    specific chunks (merge / first-wins / partial-failure coverage).
    """

    def __init__(
        self,
        responses: list[list[Any]] | None = None,
        *,
        fail_at: set[int] | None = None,
    ) -> None:
        self.responses = responses or []
        self.fail_at = fail_at or set()
        self.chunks: list[list[Any]] = []

    def filter(self, **kwargs: Any) -> DummyQuery:
        assert len(kwargs) == 1, "chunked lookups must send exactly one filter kwarg"
        (values,) = kwargs.values()
        idx = len(self.chunks)
        self.chunks.append(list(values))

        if idx in self.fail_at:
            raise RuntimeError(f"boom chunk {idx}")

        if idx < len(self.responses):
            return DummyQuery(self.responses[idx])
        return DummyQuery([])


def _model_with(manager: Any) -> Any:
    """Build a throwaway LanDB-ish model class exposing ``.objects``."""
    return type("DummyModel", (), {"objects": manager})


def _fetch_serials(
    av: AVTools,
    manager: Any,
    values: list[str],
    **kwargs: Any,
) -> tuple[dict[str, Any], bool]:
    return av._landb_fetch_in_chunks(
        model=_model_with(manager),
        filter_key="serial_number__in",
        values=values,
        key_of=lambda d: getattr(d, "serial_number", None),
        failure_event="landb_device_fetch_by_serial_failed",
        count_field="serial_count",
        **kwargs,
    )


def _av_with_dummy_logger() -> tuple[AVTools, DummyLogger]:
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]
    return av, logger


def test_chunk_size_default_leaves_headroom_under_the_tomcat_limit():
    """800 + _limit + _offset must stay well under the 1000-parameter ceiling."""
    assert core.LANDB_MAX_REQUEST_PARAMS == 1000
    assert core.LANDB_IN_CHUNK_SIZE == 800
    # +2 for _limit/_offset, plus room for a future extra filter.
    assert core.LANDB_IN_CHUNK_SIZE + 2 < core.LANDB_MAX_REQUEST_PARAMS


def test_fetch_in_chunks_zero_values_makes_no_request():
    av, _logger = _av_with_dummy_logger()
    mgr = ChunkRecordingManager()

    results, degraded = _fetch_serials(av, mgr, [])

    assert results == {}
    assert degraded is False
    assert mgr.chunks == []


def test_fetch_in_chunks_single_value_is_one_request():
    av, _logger = _av_with_dummy_logger()
    mgr = ChunkRecordingManager([[DummyDevice(serial_number="SN-1", name="LAN-1")]])

    results, degraded = _fetch_serials(av, mgr, ["SN-1"])

    assert mgr.chunks == [["SN-1"]]
    assert degraded is False
    assert list(results) == ["SN-1"]


def test_fetch_in_chunks_exactly_chunk_size_is_one_request():
    av, _logger = _av_with_dummy_logger()
    mgr = ChunkRecordingManager()
    values = [f"SN-{i}" for i in range(core.LANDB_IN_CHUNK_SIZE)]

    _results, degraded = _fetch_serials(av, mgr, values)

    assert [len(c) for c in mgr.chunks] == [800]
    assert degraded is False


def test_fetch_in_chunks_chunk_size_plus_one_splits_into_two_requests():
    av, _logger = _av_with_dummy_logger()
    mgr = ChunkRecordingManager()
    values = [f"SN-{i}" for i in range(core.LANDB_IN_CHUNK_SIZE + 1)]

    _results, degraded = _fetch_serials(av, mgr, values)

    assert [len(c) for c in mgr.chunks] == [800, 1]
    assert degraded is False
    # No value is lost or duplicated by the split.
    assert [v for chunk in mgr.chunks for v in chunk] == values


def test_fetch_in_chunks_production_fleet_size_never_exceeds_the_budget():
    """3749 serials (the live count on the outage day) must be split, in order."""
    av, _logger = _av_with_dummy_logger()
    mgr = ChunkRecordingManager()
    values = [f"SN-{i}" for i in range(3749)]

    _results, degraded = _fetch_serials(av, mgr, values)

    assert [len(c) for c in mgr.chunks] == [800, 800, 800, 800, 549]
    assert degraded is False
    assert [v for chunk in mgr.chunks for v in chunk] == values
    # The property that actually matters: every request stays under Tomcat's cap.
    for chunk in mgr.chunks:
        assert len(chunk) + 2 < core.LANDB_MAX_REQUEST_PARAMS


def test_fetch_in_chunks_merges_results_across_chunks_first_wins():
    av, _logger = _av_with_dummy_logger()
    mgr = ChunkRecordingManager(
        [
            [DummyDevice(serial_number="SN-A", name="FIRST")],
            [
                DummyDevice(serial_number="SN-A", name="DUPLICATE"),
                DummyDevice(serial_number="SN-B", name="LAN-B"),
            ],
            # Falsy keys are dropped, as in the un-chunked code.
            [DummyDevice(serial_number=None, name="NO-KEY")],
        ],
    )

    results, degraded = _fetch_serials(av, mgr, ["SN-A", "SN-B", "SN-C"], chunk_size=1)

    assert degraded is False
    assert sorted(results) == ["SN-A", "SN-B"]
    # First occurrence wins across chunk boundaries (matches `if k not in dict`).
    assert results["SN-A"].name == "FIRST"


def test_fetch_in_chunks_one_chunk_fails_others_still_merge():
    av, logger = _av_with_dummy_logger()
    mgr = ChunkRecordingManager(
        [
            [DummyDevice(serial_number="SN-A", name="LAN-A")],
            [],  # never reached: this chunk raises
            [DummyDevice(serial_number="SN-C", name="LAN-C")],
        ],
        fail_at={1},
    )

    results, degraded = _fetch_serials(av, mgr, ["SN-A", "SN-B", "SN-C"], chunk_size=1)

    # The surviving chunks' rows are kept ...
    assert sorted(results) == ["SN-A", "SN-C"]
    # ... but the caller is told the view is partial, NOT "no rows for SN-B".
    assert degraded is True

    events = logger.events("landb_device_fetch_by_serial_failed")
    assert len(events) == 1
    assert events[0]["serial_count"] == 3
    assert events[0]["chunk_index"] == 1
    assert events[0]["chunk_count"] == 3


def test_fetch_in_chunks_all_chunks_fail_is_degraded_and_empty():
    av, logger = _av_with_dummy_logger()
    mgr = ChunkRecordingManager(fail_at={0, 1, 2})

    results, degraded = _fetch_serials(av, mgr, ["SN-A", "SN-B", "SN-C"], chunk_size=1)

    assert results == {}
    assert degraded is True
    assert len(logger.events("landb_device_fetch_by_serial_failed")) == 3


def test_get_landb_ipaddresses_chunks_all_four_lookups(monkeypatch):
    """End-to-end: 3749 EAM records must not produce any oversized request."""
    av, logger = _av_with_dummy_logger()

    monkeypatch.setattr(core, "Device", DummyDevice)
    monkeypatch.setattr(core, "IPAddress", DummyIPAddress)

    total = 3749
    eam_records = [
        DummyEAM(code=f"DEV-{i}", serial_number=f"SN-{i}", description=f"DESC-{i}")
        for i in range(total)
    ]
    devices = [DummyDevice(serial_number=f"SN-{i}", name=f"LAN-{i}") for i in range(total)]
    ips = [
        DummyIPAddress(
            device=f"LAN-{i}",
            ip=f"10.0.0.{i}",
            device_serial_number=f"SN-{i}",
            device_name=f"LAN-{i}",
        )
        for i in range(total)
    ]

    dev_objects = DummyObjects(devices)
    ip_objects = DummyObjects(ips)
    DummyDevice.objects = dev_objects  # type: ignore[attr-defined]
    DummyIPAddress.objects = ip_objects  # type: ignore[attr-defined]

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

    out = av._get_landb_ipaddresses(eam_records)

    # Every device is resolved even though the lookup spanned several requests.
    assert len(out) == total
    assert av._landb_fetch_degraded is False

    # Devices by serial: 3749 -> 5 requests, none over the parameter budget.
    serial_chunks = [
        len(c["serial_number__in"]) for c in dev_objects.filter_calls if "serial_number__in" in c
    ]
    assert serial_chunks == [800, 800, 800, 800, 549]

    # IPAddresses by device serial: same split (all devices matched by serial, so
    # the two name-based fallbacks are not needed at all).
    ip_chunks = [
        len(c["device__serial_number__in"])
        for c in ip_objects.filter_calls
        if "device__serial_number__in" in c
    ]
    assert ip_chunks == [800, 800, 800, 800, 549]

    for call in dev_objects.filter_calls + ip_objects.filter_calls:
        for value_list in call.values():
            assert len(value_list) + 2 < core.LANDB_MAX_REQUEST_PARAMS

    assert logger.events("landb_chunked_fetch_done")
