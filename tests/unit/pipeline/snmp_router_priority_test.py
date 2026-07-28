"""Tests for the publish-time --priority filter + guardrail `tier` label.

Verifies (router = single publish-side enforcement point):
- `priority="all"` (default) is byte-for-byte back-compat: no filtering, and the
  ALWAYS guardrails carry NO `tier` label.
- A tier publishes only that tier's device metrics plus the ALWAYS guardrails; a
  device metric of another tier is dropped and never leaks.
- The guardrails (targeted/polled/coverage/cycle_duration) are ALWAYS metrics and
  emit under every tier, carrying `tier=<value>` when tiered.
- `tier` and `shard` labels coexist when the run is both sharded and tiered.
"""

from __future__ import annotations

import pytest
import structlog

from avtools.pipeline.snmp_router import SNMPObserverRouter
from avtools.snmp.client import PingResult
from avtools.timeseries import metrics as m

GUARDRAILS = {
    m.SNMP_DEVICES_TARGETED,
    m.SNMP_DEVICES_POLLED,
    m.SNMP_COVERAGE_RATIO,
    m.SNMP_CYCLE_DURATION_SECONDS,
}


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


def _router(ts: _CapTS) -> SNMPObserverRouter:
    r = SNMPObserverRouter.__new__(SNMPObserverRouter)
    r._ts = ts
    r._pg = _NoPG()
    r.log = structlog.get_logger()
    return r


def _ping(eq: str = "E1") -> PingResult:
    # Produces PING_CHECK_STATUS (CRITICAL) + PING_CHECK_RTT_MS (HIGH).
    return PingResult(
        device=None,
        ip="10.0.0.1",
        equipmentno=eq,
        up=True,
        rtt_ms=1.0,
        reason=None,
        attempts=1,
    )


def _run(priority: str, *, shard_index: int = 0, shard_total: int = 1):
    ts = _CapTS()
    r = _router(ts)
    r.process(
        ping=[_ping()],
        probe=[],
        queries=[],
        targeted=1,
        cycle_duration_s=2.0,
        shard_index=shard_index,
        shard_total=shard_total,
        priority=priority,
    )
    return ts.samples


# ---------------------------------------------------------------------------
# Back-compat: priority="all"
# ---------------------------------------------------------------------------


def test_all_publishes_both_device_tiers_and_untagged_guardrails() -> None:
    samples = _run("all")
    names = {s.name for s in samples}
    # Both a CRITICAL and a HIGH device metric survive (no filtering).
    assert m.PING_CHECK_STATUS in names
    assert m.PING_CHECK_RTT_MS in names
    # Guardrails present and carry NO tier label (unchanged series identity).
    for g in GUARDRAILS:
        assert g in names
    for s in samples:
        if s.name in GUARDRAILS:
            assert "tier" not in s.labels


# ---------------------------------------------------------------------------
# Tiered runs: exact device filtering + tier-labelled guardrails
# ---------------------------------------------------------------------------


def test_critical_keeps_critical_device_drops_high() -> None:
    samples = _run("critical")
    names = {s.name for s in samples}
    assert m.PING_CHECK_STATUS in names  # CRITICAL device metric kept
    assert m.PING_CHECK_RTT_MS not in names  # HIGH device metric dropped
    assert GUARDRAILS <= names  # ALWAYS guardrails still emitted


def test_high_keeps_high_device_drops_critical() -> None:
    samples = _run("high")
    names = {s.name for s in samples}
    assert m.PING_CHECK_RTT_MS in names  # HIGH device metric kept
    assert m.PING_CHECK_STATUS not in names  # CRITICAL device metric dropped
    assert GUARDRAILS <= names


@pytest.mark.parametrize("tier", ["critical", "high", "medium", "low"])
def test_guardrails_emit_under_every_tier_with_tier_label(tier: str) -> None:
    samples = _run(tier)
    guardrail_samples = [s for s in samples if s.name in GUARDRAILS]
    # All four guardrails emit under every tier...
    assert {s.name for s in guardrail_samples} == GUARDRAILS
    # ...and each carries tier=<value> (no shard label on a single-emitter run).
    for s in guardrail_samples:
        assert s.labels.get("tier") == tier
        assert "shard" not in s.labels


def test_medium_and_low_drop_the_ping_device_metrics() -> None:
    # PING_CHECK_STATUS (CRITICAL) and PING_CHECK_RTT_MS (HIGH) are in neither
    # MEDIUM nor LOW, so under those tiers only guardrails remain.
    for tier in ("medium", "low"):
        names = {s.name for s in _run(tier)}
        assert m.PING_CHECK_STATUS not in names
        assert m.PING_CHECK_RTT_MS not in names
        assert GUARDRAILS <= names


# ---------------------------------------------------------------------------
# shard + tier labels coexist
# ---------------------------------------------------------------------------


def test_tier_and_shard_labels_coexist_when_sharded_and_tiered() -> None:
    samples = _run("critical", shard_index=1, shard_total=2)
    guardrail_samples = [s for s in samples if s.name in GUARDRAILS]
    assert guardrail_samples
    for s in guardrail_samples:
        assert s.labels.get("tier") == "critical"
        assert s.labels.get("shard") == "1"
