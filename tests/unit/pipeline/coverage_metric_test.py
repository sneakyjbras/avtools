from __future__ import annotations

from avtools.pipeline.snmp_router import (
    SNMPObserverRouter,
    cycle_guardrail_samples,
    guardrail_labels,
)
from avtools.timeseries import metrics as m


class _CapTS:
    def __init__(self) -> None:
        self.samples: list = []

    def publish(self, samples) -> None:
        self.samples.extend(samples)


class _NoPG:
    def upsert_projector_monitoring(self, *_a, **_k):
        pass

    def upsert_device_sysdescr_monitoring(self, *_a, **_k):
        pass


def _router(ts):
    r = SNMPObserverRouter.__new__(SNMPObserverRouter)
    r._ts = ts
    r._pg = _NoPG()
    r.log = __import__("structlog").get_logger()
    return r


def test_coverage_emitted_when_targeted_given():
    ts = _CapTS()
    r = _router(ts)
    # 3 ping results, 4 targeted -> coverage 0.75
    from avtools.snmp.client import PingResult

    pings = [
        PingResult(
            device=None,
            ip=f"10.0.0.{i}",
            equipmentno=f"E{i}",
            up=True,
            rtt_ms=1.0,
            reason=None,
            attempts=1,
        )
        for i in range(3)
    ]
    r.process(ping=pings, probe=[], queries=[], targeted=4)
    by = {s.name: s.value for s in ts.samples if not s.labels}
    assert by[m.SNMP_DEVICES_TARGETED] == 4
    assert by[m.SNMP_DEVICES_POLLED] == 3
    assert by[m.SNMP_COVERAGE_RATIO] == 0.75


def test_no_coverage_when_targeted_zero():
    ts = _CapTS()
    r = _router(ts)
    r.process(ping=[], probe=[], queries=[], targeted=0)
    names = {s.name for s in ts.samples}
    assert m.SNMP_COVERAGE_RATIO not in names


# ---------------------------------------------------------------------------
# W4a — reusable guardrail builder (used by the router AND by the orchestrator's
# skipped-cycle path, so the metric set and labels cannot drift apart).
# ---------------------------------------------------------------------------


def test_cycle_guardrail_samples_zero_values_for_a_skipped_cycle():
    samples = cycle_guardrail_samples(targeted=0, polled=0)

    by = {s.name: s.value for s in samples}
    assert by == {
        m.SNMP_DEVICES_TARGETED: 0,
        m.SNMP_DEVICES_POLLED: 0,
        # polled/targeted is undefined with no targets, and "no targets" is not coverage.
        m.SNMP_COVERAGE_RATIO: 0.0,
    }
    for s in samples:
        assert s.labels == {}


def test_cycle_guardrail_samples_match_the_router_values():
    samples = cycle_guardrail_samples(targeted=4, polled=3)
    by = {s.name: s.value for s in samples}
    assert by[m.SNMP_DEVICES_TARGETED] == 4
    assert by[m.SNMP_DEVICES_POLLED] == 3
    assert by[m.SNMP_COVERAGE_RATIO] == 0.75


def test_cycle_guardrail_samples_are_all_always_tier():
    """They must survive every --priority filter, including on the zero path."""
    for s in cycle_guardrail_samples(targeted=0, polled=0):
        assert m.METRIC_META[s.name].priority is m.Priority.ALWAYS
        for tier in (m.Priority.CRITICAL, m.Priority.HIGH, m.Priority.MEDIUM, m.Priority.LOW):
            assert m.should_publish(s.name, tier)


def test_guardrail_labels_omit_defaults_and_add_shard_and_tier():
    assert guardrail_labels() == {}
    assert guardrail_labels(shard_index=0, shard_total=1, priority=m.PRIORITY_ALL) == {}
    assert guardrail_labels(shard_index=3, shard_total=8) == {"shard": "3"}
    assert guardrail_labels(priority="critical") == {"tier": "critical"}
    assert guardrail_labels(shard_index=1, shard_total=2, priority="low") == {
        "shard": "1",
        "tier": "low",
    }
