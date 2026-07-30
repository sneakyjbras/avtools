"""Tests for the metric priority taxonomy (single source of truth).

Guards:
- Contract: every metric in METRIC_META is classified (no unclassified metric).
- The exact CRITICAL/HIGH/MEDIUM/LOW/ALWAYS mapping is pinned (accidental
  reclassification fails loudly — the rest of the tiering initiative keys off it).
- metrics_for_priority is EXACT (single tier, non-cumulative).
- publishable_metrics(tier) == that tier ∪ ALWAYS and never leaks another tier.
- should_publish honours ALWAYS and exact-tier semantics.
- The union across all tiers equals every metric name.
"""

from __future__ import annotations

import pytest

from avtools.timeseries import metrics as m
from avtools.timeseries.metrics import Priority

# The authoritative mapping from the initiative brief, transcribed verbatim.
EXPECTED: dict[Priority, set[str]] = {
    Priority.CRITICAL: {
        m.PING_CHECK_STATUS,
        m.SNMP_PROBE_STATUS,
        m.DEVICE_IF_OPER_STATUS,
        m.PDU_QUERY_HEALTHY,
        m.PDU_QUERY_PQ_SEVERITY,
        m.PDU_QUERY_INPUT_STATUS,
        m.PDU_QUERY_ACTIVE_POWER_STATUS,
        m.PDU_QUERY_POWER_FACTOR_STATUS,
        m.PDU_QUERY_BALANCE_STATUS,
        m.PDU_QUERY_LOAD_STATUS,
        m.PDU_QUERY_TEMPERATURE_C,
        m.PDU_QUERY_HUMIDITY_PCT,
    },
    Priority.HIGH: {
        m.PING_CHECK_RTT_MS,
        m.PDU_QUERY_ACTIVE_POWER_WATTS,
        m.PDU_QUERY_LINE_CURRENT_AMPS,
        m.PDU_QUERY_CURRENT_UTILIZED_PCT,
        m.PDU_QUERY_VOLTAGE_VOLTS,
        m.PDU_QUERY_OUTLET_STATE,
        m.PROJECTOR_QUERY_POWER_STATUS,
    },
    Priority.MEDIUM: {
        m.PDU_QUERY_ENERGY_KWH,
        m.PDU_QUERY_FREQUENCY_HZ,
        m.PDU_QUERY_POWER_FACTOR,
        m.PDU_QUERY_APPARENT_POWER_VA,
        m.PDU_QUERY_OUT_OF_BALANCE_PCT,
        m.PDU_QUERY_OUTLET_CURRENT_AMPS,
        m.PDU_QUERY_OUTLET_POWER_WATTS,
        m.PDU_QUERY_OUTLET_ENERGY_WH,
        m.DEVICE_IF_IN_OCTETS,
        m.DEVICE_IF_OUT_OCTETS,
        m.DEVICE_IF_IN_ERRORS,
        m.DEVICE_IF_OUT_ERRORS,
        m.PROJECTOR_QUERY_LAMP_HOURS,
        m.PROJECTOR_QUERY_UPTIME_SECONDS,
        m.PDU_QUERY_UPTIME_SECONDS,
        m.CODEC_QUERY_UPTIME_SECONDS,
        m.MATRIX_QUERY_UPTIME_SECONDS,
    },
    Priority.LOW: {
        m.PROJECTOR_QUERY_FIRMWARE_INFO,
        m.PDU_QUERY_FIRMWARE_INFO,
        m.PDU_QUERY_STATUS_INFO,
        m.PDU_QUERY_ACTIVE_POWER_STATUS_INFO,
        m.PDU_QUERY_POWER_FACTOR_STATUS_INFO,
        m.PDU_QUERY_BALANCE_STATUS_INFO,
        m.PDU_QUERY_LOAD_STATUS_INFO,
        m.PDU_QUERY_SENSOR_INFO,
        m.PDU_QUERY_OUTLET_INFO,
        m.DEVICE_IF_INFO,
    },
    Priority.ALWAYS: {
        m.SNMP_DEVICES_TARGETED,
        m.SNMP_DEVICES_POLLED,
        m.SNMP_COVERAGE_RATIO,
        m.SNMP_CYCLE_DURATION_SECONDS,
        m.EAM_LAST_RUN_TIMESTAMP,
        m.LANDB_LAST_RUN_TIMESTAMP,
        m.ROOMS_LAST_RUN_TIMESTAMP,
    },
}

TIERS = (Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM, Priority.LOW)


# ---------------------------------------------------------------------------
# Contract: every metric is classified
# ---------------------------------------------------------------------------


def test_every_metric_has_a_priority() -> None:
    """No metric in METRIC_META may be left unclassified (guards future additions)."""
    for name, meta in m.METRIC_META.items():
        assert isinstance(meta.priority, Priority), f"{name} has no valid priority"


def test_taxonomy_matches_the_pinned_mapping_exactly() -> None:
    """Each tier's membership matches the initiative brief verbatim."""
    for tier, expected in EXPECTED.items():
        assert m.metrics_for_priority(tier) == expected, f"tier {tier} drifted"


def test_expected_mapping_covers_the_whole_registry() -> None:
    """The pinned mapping must partition METRIC_META with no orphans/extras."""
    union = set().union(*EXPECTED.values())
    assert union == set(m.METRIC_META)


# ---------------------------------------------------------------------------
# metrics_for_priority: exact, non-cumulative, disjoint tiers
# ---------------------------------------------------------------------------


def test_tiers_are_disjoint() -> None:
    seen: set[str] = set()
    for tier in Priority:
        s = m.metrics_for_priority(tier)
        assert not (s & seen), f"tier {tier} overlaps a previous tier"
        seen |= s


def test_metrics_for_priority_is_not_cumulative() -> None:
    """CRITICAL must not implicitly contain HIGH/MEDIUM/LOW (tiers are exact)."""
    critical = m.metrics_for_priority(Priority.CRITICAL)
    for other in (Priority.HIGH, Priority.MEDIUM, Priority.LOW):
        assert not (critical & m.metrics_for_priority(other))


# ---------------------------------------------------------------------------
# publishable_metrics: tier ∪ ALWAYS, never another tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", TIERS)
def test_publishable_is_tier_union_always(tier: Priority) -> None:
    always = m.metrics_for_priority(Priority.ALWAYS)
    expected = EXPECTED[tier] | always
    assert m.publishable_metrics(tier) == expected


@pytest.mark.parametrize("tier", TIERS)
def test_publishable_never_leaks_other_tiers(tier: Priority) -> None:
    published = m.publishable_metrics(tier)
    for other in TIERS:
        if other is tier:
            continue
        # Other tiers' metrics never appear (ALWAYS is not a device tier).
        assert not (published & EXPECTED[other]), f"{other} leaked into {tier}"


def test_all_tiers_together_equal_full_registry() -> None:
    union: set[str] = set()
    for tier in Priority:
        union |= m.metrics_for_priority(tier)
    assert union == set(m.METRIC_META)


# ---------------------------------------------------------------------------
# should_publish: ALWAYS passes everywhere; tiers are exact; unknown fails closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", TIERS)
def test_always_metrics_pass_under_every_tier(tier: Priority) -> None:
    for name in m.metrics_for_priority(Priority.ALWAYS):
        assert m.should_publish(name, tier) is True


def test_should_publish_is_exact_per_tier() -> None:
    # A CRITICAL metric passes only under CRITICAL.
    assert m.should_publish(m.PING_CHECK_STATUS, Priority.CRITICAL) is True
    assert m.should_publish(m.PING_CHECK_STATUS, Priority.HIGH) is False
    # A HIGH metric passes only under HIGH.
    assert m.should_publish(m.PING_CHECK_RTT_MS, Priority.HIGH) is True
    assert m.should_publish(m.PING_CHECK_RTT_MS, Priority.CRITICAL) is False


def test_unknown_metric_never_publishes_under_a_tier() -> None:
    assert m.should_publish("avtools_not_a_real_metric", Priority.CRITICAL) is False


def test_priority_all_sentinel_is_not_a_tier_member() -> None:
    assert m.PRIORITY_ALL == "all"
    assert m.PRIORITY_ALL not in {p.value for p in Priority}
