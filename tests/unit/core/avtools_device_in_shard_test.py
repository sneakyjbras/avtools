"""Unit tests for the shard partition function `device_in_shard`.

The distributed SNMP collector replaces one 64-thread shared-memory process with
N pods that each keep only their slice of the fleet. Correctness rests on one
property: for a fixed N, every device belongs to *exactly one* shard, so the N
pods partition the work with zero inter-pod coordination (complete + disjoint).
These tests pin that property, plus the two ways it can silently break.
"""

from __future__ import annotations

from collections import Counter

import pytest

from avtools.core.av_tools import device_in_shard


SAMPLE_IDS = [f"EQ{i:05d}" for i in range(5000)]


@pytest.mark.parametrize("shard_total", [2, 3, 8, 16, 64])
def test_partition_is_complete_and_disjoint(shard_total: int) -> None:
    """Every device maps to exactly one shard for a given N."""
    for equipment_no in SAMPLE_IDS:
        owning = [k for k in range(shard_total) if device_in_shard(equipment_no, k, shard_total)]
        assert owning == [owning[0]], (equipment_no, owning)  # exactly one shard


@pytest.mark.parametrize("shard_total", [2, 8, 64])
def test_every_shard_is_used_and_roughly_balanced(shard_total: int) -> None:
    """crc32-mod-N spreads device counts evenly across all shards."""
    counts = Counter(
        next(k for k in range(shard_total) if device_in_shard(e, k, shard_total))
        for e in SAMPLE_IDS
    )
    assert set(counts) == set(range(shard_total))  # no empty shard
    # Count balance (not time balance) is what the hash guarantees.
    assert max(counts.values()) / min(counts.values()) < 1.25


def test_shard_total_one_is_a_noop() -> None:
    """The unsharded (Puppet) deployment must be unaffected: everything matches."""
    assert all(device_in_shard(e, 0, 1) for e in SAMPLE_IDS)


def test_assignment_is_stable_across_calls() -> None:
    """crc32 is not salted per process (unlike builtin hash()), so the split is
    identical on every pod — the property that keeps the partition gap-free."""
    for e in SAMPLE_IDS[:100]:
        first = [k for k in range(8) if device_in_shard(e, k, 8)]
        second = [k for k in range(8) if device_in_shard(e, k, 8)]
        assert first == second
