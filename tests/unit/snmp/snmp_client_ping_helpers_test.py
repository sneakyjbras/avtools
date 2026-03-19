from __future__ import annotations

import math

import avtools.snmp.client as snmp_mod


def test_ping_hint_prefers_useful_lines_and_bounds_length():
    text = """PING 10.0.0.1 (10.0.0.1): 56 data bytes
From 10.0.0.254 icmp_seq=1 Destination Host Unreachable
--- 10.0.0.1 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss
"""

    hint = snmp_mod.SNMPClient._ping_hint(text)
    assert hint is not None
    # Should pick the unreachable line.
    assert "Unreachable" in hint
    assert len(hint) <= snmp_mod.SNMPClient._PING_FAIL_SNIPPET_MAX


def test_ping_failure_reason_classifies_common_cases():
    reason, _ = snmp_mod.SNMPClient._ping_failure_reason(
        returncode=1, timed_out=True, text=""
    )
    assert reason == "process_timeout"

    reason, _ = snmp_mod.SNMPClient._ping_failure_reason(
        returncode=2, timed_out=False, text="temporary failure in name resolution"
    )
    assert reason == "dns_resolution_failed"

    reason, _ = snmp_mod.SNMPClient._ping_failure_reason(
        returncode=1, timed_out=False, text="100% packet loss"
    )
    assert reason == "packet_loss"

    reason, _ = snmp_mod.SNMPClient._ping_failure_reason(
        returncode=None, timed_out=False, text="some unknown error"
    )
    assert reason == "unknown"


def test_ping_max_budget_s_matches_documented_upper_bound(monkeypatch):
    # Avoid building real handlers.
    monkeypatch.setattr(snmp_mod.DeviceHandlerFactory, "get_handlers", lambda self: {})

    c = snmp_mod.SNMPClient(
        targets=[],
        ping_timeout_s=1,
        ping_retries=2,  # attempts = 3
        ping_backoff_s=0.2,
        ping_backoff_max_s=0.8,
        ping_jitter_s=0.2,
    )

    # Worst-case budget:
    # - attempts * timeout = 3 * 1 = 3
    # - backoffs (attempts-1):
    #   i=0: min(0.8, 0.2*1)=0.2 + jitter(0.2) => 0.4
    #   i=1: min(0.8, 0.2*2)=0.4 + jitter(0.2) => 0.6
    # total = 3 + 0.4 + 0.6 = 4.0
    assert math.isclose(c.ping_max_budget_s(), 4.0, rel_tol=0, abs_tol=1e-9)


def test_next_backoff_s_is_deterministic_under_mocked_jitter(monkeypatch):
    monkeypatch.setattr(snmp_mod.DeviceHandlerFactory, "get_handlers", lambda self: {})
    monkeypatch.setattr(snmp_mod.random, "uniform", lambda a, b: 0.1)

    c = snmp_mod.SNMPClient(
        targets=[],
        ping_backoff_s=0.2,
        ping_backoff_max_s=0.8,
        ping_jitter_s=0.2,
    )

    # attempt_index=0 -> base 0.2 + 0.1
    assert math.isclose(c._next_backoff_s(0), 0.3, rel_tol=0, abs_tol=1e-9)
    # attempt_index=2 -> base min(0.8, 0.2*4)=0.8 + 0.1
    assert math.isclose(c._next_backoff_s(2), 0.9, rel_tol=0, abs_tol=1e-9)
