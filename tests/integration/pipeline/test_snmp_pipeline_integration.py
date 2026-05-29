from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable

import pytest

import avtools.core.av_tools as av_mod
from avtools.core.av_tools import AVTools
from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.client import PingResult, ProbeResult, QueryResult
from avtools.timeseries.models import MetricSample
from avtools.timeseries import metrics as m


def _cached(
    *,
    equipment_no: str,
    ip: str,
    eq_class: str | None = "AVD",
    category: str | None = "AV-PRO",
) -> CachedIPAddress:
    """Create a CachedIPAddress with all required fields populated."""
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


@dataclass(frozen=True, slots=True)
class SNMPScenario:
    """Deterministic SNMP pipeline scenario used by the fake SNMP client."""

    ping_up: set[str]
    snmp_up: set[str]
    query_ok: set[str]
    query_empty: set[str] = frozenset()


class CapturingPublisher:
    """In-memory sink for MetricSample batches."""

    instances: list["CapturingPublisher"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)
        self.published: list[list[MetricSample]] = []
        type(self).instances.append(self)

    def publish(self, samples: list[MetricSample]) -> None:
        # Router publishes one batch per pipeline run.
        self.published.append(list(samples))


def make_fake_snmp_client(scenario: SNMPScenario):
    """Factory returning a Fake SNMPClient class bound to the provided scenario.

    This exercises AVTools' 3-phase scheduling logic (split-even + gather)
    while keeping results deterministic and CI-friendly.
    """

    class FakeSNMPClient:
        ping_called_with: list[str] = []
        probe_called_with: list[str] = []
        query_called_with: list[str] = []

        def __init__(self, targets: list[CachedIPAddress]) -> None:
            self.targets = list(targets)

        async def collect_ping(self):
            # Record which equipment numbers were in this chunk.
            type(self).ping_called_with.extend(
                [str(d.equipment_no) for d in self.targets if d.equipment_no]
            )

            results: list[PingResult] = []
            alive: list[CachedIPAddress] = []
            for d in self.targets:
                eq = d.equipment_no
                ip = d.ip
                up = 1 if (eq in scenario.ping_up) else 0
                if up == 1:
                    alive.append(d)
                results.append(
                    PingResult(
                        device=d,
                        ip=ip,
                        equipmentno=eq,
                        up=up,
                        rtt_ms=1.0 if up == 1 else None,
                        reason=None if up == 1 else "down",
                        attempts=1,
                    )
                )
            return results, alive

        async def collect_snmp_probe(self, devices: list[CachedIPAddress]):
            type(self).probe_called_with.extend(
                [str(d.equipment_no) for d in devices if d.equipment_no]
            )

            results: list[ProbeResult] = []
            alive: list[CachedIPAddress] = []
            for d in devices:
                eq = d.equipment_no
                ip = d.ip
                up = 1 if (eq in scenario.snmp_up) else 0
                if up == 1:
                    alive.append(d)
                results.append(
                    ProbeResult(
                        device=d,
                        ip=ip,
                        equipmentno=eq,
                        up=up,
                        eqclass=d.eq_class,
                        category=d.category,
                        sysdescr="sysDescr.0" if up == 1 else None,
                    )
                )
            return results, alive

        @staticmethod
        def _projector_match(eqclass: str | None, category: str | None) -> bool:
            cls = (eqclass or "").upper()
            cat = (category or "").upper()
            return cls.startswith("AVD") and ("AV-PRO" in cat)

        async def collect_snmp_queries(self, devices: list[CachedIPAddress]):
            type(self).query_called_with.extend(
                [str(d.equipment_no) for d in devices if d.equipment_no]
            )

            out: list[QueryResult] = []
            for d in devices:
                eq = d.equipment_no
                ip = d.ip

                # Simulate "empty" handler output: device is queried but yields no stats.
                if eq in scenario.query_empty:
                    continue

                # Only emit query results for explicit query_ok devices.
                if eq not in scenario.query_ok:
                    continue

                # Apply the same routing logic as projector_query_spec.
                if not self._projector_match(d.eq_class, d.category):
                    continue

                out.append(
                    QueryResult(
                        device=d,
                        ip=ip,
                        equipmentno=eq,
                        query="projector",
                        eqclass=d.eq_class,
                        category=d.category,
                        stats={
                            "lamp_hours": 100,
                            "uptime_seconds": 200,
                            "firmware": "1.2.3",
                            "power_status": "ON",
                        },
                    )
                )
            return out

    return FakeSNMPClient


@pytest.fixture()
def avtools(postgres_url: str) -> AVTools:
    # Each test starts from a clean DB (see conftest.py).
    return AVTools(postgres_url)


def _seed_landb_targets(av: AVTools, targets: Iterable[CachedIPAddress]) -> None:
    av.dbod_helper.sync_landb_devices(list(targets), to_update=[], to_delete=[])


def test_c1_pipeline_happy_path_real_postgres(monkeypatch, avtools: AVTools, row_count):
    """C1: End-to-end pipeline with real Postgres and deterministic SNMP.

    Validates:
      - AVTools loads targets from Postgres
      - 3-phase pipeline produces ping/probe/query results
      - Router publishes numeric samples
      - Router persists text monitoring rows into Postgres
    """

    targets = [
        _cached(equipment_no="EQ1", ip="10.0.0.1", eq_class="AVD", category="AV-PRO"),
        _cached(equipment_no="EQ2", ip="10.0.0.2", eq_class="AVD", category="AV-PRO"),
        _cached(equipment_no="EQ3", ip="10.0.0.3/32", eq_class="AVD", category="AV-PRO"),
        _cached(equipment_no="EQ4", ip="10.0.0.4", eq_class="AVD", category="AV-PRO"),
        _cached(equipment_no="EQ5", ip="10.0.0.5", eq_class="AVD", category="AV-PRO"),
    ]
    _seed_landb_targets(avtools, targets)

    scenario = SNMPScenario(
        ping_up={"EQ1", "EQ2", "EQ3"},
        snmp_up={"EQ1", "EQ2"},
        query_ok={"EQ1", "EQ2"},
    )

    FakeSNMPClient = make_fake_snmp_client(scenario)
    monkeypatch.setattr(av_mod, "SNMPClient", FakeSNMPClient, raising=True)

    # Capture OTLP publishes (no real gRPC).
    CapturingPublisher.instances.clear()
    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", CapturingPublisher, raising=True)

    avtools.run_snmp_timeseries(
        otlp_endpoint="dummy:4316",
        monit_tenant="tenant",
        monit_password="pass",
        max_workers=4,
        otlp_insecure=True,
    )

    # Router must have published exactly once.
    assert len(CapturingPublisher.instances) == 1
    pub = CapturingPublisher.instances[0]
    assert len(pub.published) == 1

    samples = pub.published[0]
    # Expected sample count:
    # - ping: 5 status + 3 rtt = 8
    # - probe: 5 status = 5
    # - queries: 2 devices * (lamp + uptime) = 4
    assert len(samples) == 17

    # Monitoring rows persisted to Postgres.
    assert row_count("avtools_projector_monitoring") == 2
    assert row_count("avtools_device_sysdescr_monitoring") == 2


def test_c2_partial_device_results_do_not_break_pipeline(monkeypatch, avtools: AVTools, row_count):
    """C2: Some devices yield empty query stats; others still publish/persist."""

    targets = [
        _cached(equipment_no="EQ1", ip="10.1.0.1"),
        _cached(equipment_no="EQ2", ip="10.1.0.2"),
        _cached(equipment_no="EQ3", ip="10.1.0.3"),
    ]
    _seed_landb_targets(avtools, targets)

    scenario = SNMPScenario(
        ping_up={"EQ1", "EQ2", "EQ3"},
        snmp_up={"EQ1", "EQ2", "EQ3"},
        query_ok={"EQ1", "EQ2", "EQ3"},
        query_empty={"EQ3"},  # queried but produces no stats -> omitted from QueryResult
    )

    FakeSNMPClient = make_fake_snmp_client(scenario)
    monkeypatch.setattr(av_mod, "SNMPClient", FakeSNMPClient, raising=True)

    CapturingPublisher.instances.clear()
    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", CapturingPublisher, raising=True)

    avtools.run_snmp_timeseries(
        otlp_endpoint="dummy:4316",
        monit_tenant="tenant",
        monit_password="pass",
        max_workers=3,
        otlp_insecure=True,
    )

    pub = CapturingPublisher.instances[0]
    samples = pub.published[0]

    # Expected samples:
    # - ping: 3 status + 3 rtt = 6
    # - probe: 3 status = 3
    # - queries: 2 devices * 2 = 4
    assert len(samples) == 13

    # Sysdescr for all 3 (probe up=1 for all) -> 3 rows
    assert row_count("avtools_device_sysdescr_monitoring") == 3

    # Projector monitoring only for EQ1/EQ2 (EQ3 had empty stats)
    assert row_count("avtools_projector_monitoring") == 2


def test_c3_concurrency_determinism_get_snmp_raw(monkeypatch, postgres_url: str):
    """C3: _get_snmp_raw yields the same result set regardless of max_workers."""

    av = AVTools(postgres_url)

    devices = [_cached(equipment_no=f"EQ{i}", ip=f"10.2.0.{i}") for i in range(1, 8)]

    scenario = SNMPScenario(
        ping_up={"EQ1", "EQ3", "EQ5", "EQ7"},
        snmp_up={"EQ2", "EQ3", "EQ4", "EQ7"},
        query_ok={"EQ2", "EQ3", "EQ4", "EQ7"},
    )

    FakeSNMPClient = make_fake_snmp_client(scenario)
    monkeypatch.setattr(av_mod, "SNMPClient", FakeSNMPClient, raising=True)

    def key_ping(r: PingResult) -> tuple[str, int, float | None]:
        return (
            str(r.equipmentno),
            int(r.up),
            float(r.rtt_ms) if r.rtt_ms is not None else None,
        )

    def key_probe(r: ProbeResult) -> tuple[str, int, str | None, str | None]:
        return (str(r.equipmentno), int(r.up), r.eqclass, r.category)

    def key_query(r: QueryResult) -> tuple[str, str, int, int]:
        lamp = int(r.stats.get("lamp_hours") or 0)
        up = int(r.stats.get("uptime_seconds") or 0)
        return (str(r.equipmentno), str(r.query), lamp, up)

    ping1, probe1, query1 = asyncio.run(av._get_snmp_raw(devices, max_workers=1))
    ping8, probe8, query8 = asyncio.run(av._get_snmp_raw(devices, max_workers=8))

    assert sorted(map(key_ping, ping1)) == sorted(map(key_ping, ping8))
    assert sorted(map(key_probe, probe1)) == sorted(map(key_probe, probe8))
    assert sorted(map(key_query, query1)) == sorted(map(key_query, query8))


def test_c4_data_quality_category_optional_eqclass_required_for_queries(
    monkeypatch, avtools: AVTools, row_count
):
    """C4: Category can be missing; eq_class missing prevents projector query routing.

    Policy tested (current behavior):
      - Devices with missing category/eq_class are still pinged and probed.
      - Projector query results are emitted only when BOTH:
          * eq_class starts with "AVD"
          * category contains "AV-PRO"
    """

    targets = [
        # Category missing (allowed) -> should still be probed, but no projector query match.
        _cached(equipment_no="EQ_CAT_NONE", ip="10.3.0.1", eq_class="AVD", category=None),
        # eq_class missing (not OK for query routing) -> no projector query match.
        _cached(
            equipment_no="EQ_CLASS_NONE",
            ip="10.3.0.2",
            eq_class=None,
            category="AV-PRO",
        ),
        # Fully routable projector
        _cached(equipment_no="EQ_OK", ip="10.3.0.3", eq_class="AVD", category="AV-PRO"),
    ]
    _seed_landb_targets(avtools, targets)

    scenario = SNMPScenario(
        ping_up={"EQ_CAT_NONE", "EQ_CLASS_NONE", "EQ_OK"},
        snmp_up={"EQ_CAT_NONE", "EQ_CLASS_NONE", "EQ_OK"},
        query_ok={"EQ_CAT_NONE", "EQ_CLASS_NONE", "EQ_OK"},
    )

    FakeSNMPClient = make_fake_snmp_client(scenario)
    monkeypatch.setattr(av_mod, "SNMPClient", FakeSNMPClient, raising=True)

    CapturingPublisher.instances.clear()
    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", CapturingPublisher, raising=True)

    avtools.run_snmp_timeseries(
        otlp_endpoint="dummy:4316",
        monit_tenant="tenant",
        monit_password="pass",
        max_workers=2,
        otlp_insecure=True,
    )

    pub = CapturingPublisher.instances[0]
    samples = pub.published[0]

    # Sysdescr should be persisted for all three (probe up=1 + sysdescr set).
    assert row_count("avtools_device_sysdescr_monitoring") == 3

    # Only EQ_OK should yield projector monitoring rows (because only it matches routing).
    assert row_count("avtools_projector_monitoring") == 1

    # Only EQ_OK should yield projector numeric metrics.
    projector_metric_names = {
        m.PROJECTOR_QUERY_LAMP_HOURS,
        m.PROJECTOR_QUERY_UPTIME_SECONDS,
    }
    projector_samples = [s for s in samples if s.name in projector_metric_names]

    assert projector_samples  # at least one
    assert {s.labels.get("equipmentno") for s in projector_samples} == {"EQ_OK"}
