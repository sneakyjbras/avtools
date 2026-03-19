from __future__ import annotations

import asyncio

import avtools.snmp.client as snmp_mod
from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress


def _dev(
    *,
    equipment_no: str | None,
    ip: str | None,
    eq_class: str | None = None,
    category: str | None = None,
) -> CachedIPAddress:
    # CachedIPAddress is a Pydantic model with many optional fields.
    return CachedIPAddress(
        equipment_no=equipment_no,
        serial_number=None,
        ip=ip,
        name=None,
        hostname=None,
        landb_serial=None,
        landb_description=None,
        building=None,
        floor=None,
        room=None,
        eq_class=eq_class,
        category=category,
        manufacturer=None,
        model=None,
    )


class _Handler:
    def __init__(
        self,
        probe_ok: bool = True,
        sysdescr: str | None = None,
        stats: dict | None = None,
    ):
        self._probe_ok = probe_ok
        self._sysdescr = sysdescr
        self._stats = stats or {}

    def probe(self):
        return self._probe_ok

    def fetch_sysdescr(self):
        return self._sysdescr

    def fetch_stats(self):
        return self._stats


def test_collect_ping_handles_missing_fields_and_success(monkeypatch):
    # Avoid building real handlers.
    monkeypatch.setattr(snmp_mod.DeviceHandlerFactory, "get_handlers", lambda self: {})

    async def fake_ping(self, host: str):
        # Always up for any host.
        return (True, 1.5, None, None, 1)

    monkeypatch.setattr(snmp_mod.SNMPClient, "_ping_posix", fake_ping)

    targets = [
        _dev(equipment_no=None, ip="10.0.0.1"),
        _dev(equipment_no="EQ2", ip=None),
        _dev(equipment_no="EQ3", ip="10.0.0.3"),
    ]

    c = snmp_mod.SNMPClient(targets=targets, ping_retries=0)
    results, alive = asyncio.run(c.collect_ping())

    assert len(results) == 3
    # Only the valid eq + ip host can be alive.
    assert [d.equipment_no for d in alive] == ["EQ3"]

    # Missing equipmentno should be classified.
    r0 = results[0]
    assert r0.up == 0
    assert r0.reason == "missing_equipmentno"

    # Missing ip classified.
    r1 = results[1]
    assert r1.up == 0
    assert r1.reason == "missing_ip"

    # Success produces rtt_ms.
    r2 = results[2]
    assert r2.up == 1
    assert r2.rtt_ms == 1.5


def test_collect_snmp_probe_fetches_sysdescr_when_probe_ok(monkeypatch):
    # Force deterministic to_thread (no threads in unit test).
    async def sync_to_thread(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", sync_to_thread)

    # Build handlers map for one IP.
    handler = _Handler(probe_ok=True, sysdescr="Dummy OS")

    def fake_get_handlers(self):
        return {"10.0.0.10": handler}

    monkeypatch.setattr(
        snmp_mod.DeviceHandlerFactory, "get_handlers", fake_get_handlers
    )

    targets = [
        _dev(equipment_no="EQ10", ip="10.0.0.10", eq_class="AVD", category="AV-PRO"),
        _dev(equipment_no="EQ11", ip="10.0.0.11", eq_class="AVD", category="AV-PRO"),
    ]

    c = snmp_mod.SNMPClient(targets=targets)
    results, alive = asyncio.run(c.collect_snmp_probe())

    assert len(results) == 2
    assert [d.equipment_no for d in alive] == ["EQ10"]

    ok = next(r for r in results if r.equipmentno == "EQ10")
    assert ok.up == 1
    assert ok.sysdescr == "Dummy OS"
    assert ok.eqclass == "AVD"
    assert ok.category == "AV-PRO"

    missing = next(r for r in results if r.equipmentno == "EQ11")
    assert missing.up == 0


def test_collect_snmp_queries_routes_projector_and_skips_unmatched(monkeypatch):
    async def sync_to_thread(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", sync_to_thread)

    handler = _Handler(stats={"uptime_seconds": 10, "lamp_hours": 5, "firmware": "FW"})

    def fake_get_handlers(self):
        return {"10.0.0.20": handler}

    monkeypatch.setattr(
        snmp_mod.DeviceHandlerFactory, "get_handlers", fake_get_handlers
    )

    # One matches projector spec; one doesn't.
    d_ok = _dev(equipment_no="EQ20", ip="10.0.0.20", eq_class="AVD", category="AV-PRO")
    d_skip = _dev(equipment_no="EQ21", ip="10.0.0.21", eq_class="OTHER", category="X")

    c = snmp_mod.SNMPClient(targets=[d_ok, d_skip])
    out = asyncio.run(c.collect_snmp_queries([d_ok, d_skip]))

    assert len(out) == 1
    q = out[0]
    assert q.equipmentno == "EQ20"
    assert q.query == "projector"
    assert q.stats["uptime_seconds"] == 10
