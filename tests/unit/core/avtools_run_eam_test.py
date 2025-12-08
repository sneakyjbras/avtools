from __future__ import annotations

from types import MethodType
from typing import Any, List, Tuple

import structlog
from requests.auth import HTTPBasicAuth

from avtools.core.av_tools import AVTools


class DummyLogger:
    """
    Minimal logger to capture info/error/exception messages for assertions.
    """

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.errors: list[str] = []
        self.exceptions: list[str] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.infos.append(str(msg))

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.errors.append(str(msg))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exceptions.append(str(msg))


def make_avtools_for_tests() -> AVTools:
    """
    Bypass __init__ so we don't touch PostgresClient.
    Only attach a logger; everything else is injected/mocked.
    """
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    return av


def attach_sync_stubs(
    av: AVTools,
    devices_fn: Any,
    positions_fn: Any,
) -> None:
    """
    Attach bound methods to a specific AVTools instance.
    """
    av.sync_eam_devices = MethodType(devices_fn, av)  # type: ignore[attr-defined]
    av.sync_eam_positions = MethodType(positions_fn, av)  # type: ignore[attr-defined]


# 1, 5, 6, 8, 9, 11: happy path, auth forwarding, logging, return value, multi-call sanity


def test_run_eam_happy_path_calls_both_in_order_with_shared_auth_and_logs():
    """
    Happy path:
    - run_eam must construct a single HTTPBasicAuth instance.
    - sync_eam_devices is called first, then sync_eam_positions.
    - Both receive the *same* auth object with correct credentials.
    - Logs overall EAM sync and per-phase messages.
    - Returns None.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[tuple[str, HTTPBasicAuth]] = []

    def fake_sync_devices(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append(("devices", auth))

    def fake_sync_positions(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append(("positions", auth))

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    result = av.run_eam("eam-user", "eam-pass")
    assert result is None

    # Order: devices → positions, once each
    assert [name for name, _ in calls] == ["devices", "positions"]

    # Same auth instance shared between both calls
    first_auth = calls[0][1]
    second_auth = calls[1][1]
    assert first_auth is second_auth

    # Credentials correct and not mutated
    assert isinstance(first_auth, HTTPBasicAuth)
    assert first_auth.username == "eam-user"
    assert first_auth.password == "eam-pass"

    # Logging: overall + phase logs containing some EAM/devices/positions hints
    info_text = " ".join(logger.infos)
    assert "EAM" in info_text  # e.g. "Running EAM sync"
    assert "devices" in info_text  # e.g. "EAM devices sync"
    assert "positions" in info_text  # e.g. "EAM positions sync"


def test_run_eam_can_be_called_multiple_times_without_caching():
    """
    Performance / multi-call sanity:
    - Calling run_eam() twice calls both sync functions twice.
    - No caching or reuse of HTTPBasicAuth between calls.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[tuple[str, HTTPBasicAuth]] = []

    def fake_sync_devices(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append(("devices", auth))

    def fake_sync_positions(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append(("positions", auth))

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    av.run_eam("user-1", "pass-1")
    av.run_eam("user-2", "pass-2")

    # devices,pos for first; devices,pos for second
    assert [name for name, _ in calls] == [
        "devices",
        "positions",
        "devices",
        "positions",
    ]

    auth_1_devices = calls[0][1]
    auth_1_positions = calls[1][1]
    auth_2_devices = calls[2][1]
    auth_2_positions = calls[3][1]

    # Within each run: same auth object
    assert auth_1_devices is auth_1_positions
    assert auth_2_devices is auth_2_positions

    # Across runs: different auth objects
    assert auth_1_devices is not auth_2_devices

    # Credentials match each call
    assert auth_1_devices.username == "user-1"
    assert auth_1_devices.password == "pass-1"
    assert auth_2_devices.username == "user-2"
    assert auth_2_devices.password == "pass-2"


# 2, 6, 8, 13: devices raises → positions still runs; error logged; exception swallowed


def test_run_eam_devices_failure_still_runs_positions_and_logs_error():
    """
    If sync_eam_devices raises:
    - run_eam must catch/log the error.
    - sync_eam_positions must still be invoked.
    - No exception should propagate.
    - Error log mentions devices (and ideally method name).
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[str] = []

    def fake_sync_devices(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("devices")
        assert isinstance(auth, HTTPBasicAuth)
        assert auth.username == "user"
        assert auth.password == "pass"
        raise RuntimeError("devices boom")

    def fake_sync_positions(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("positions")
        assert isinstance(auth, HTTPBasicAuth)
        # Auth credentials should be unchanged
        assert auth.username == "user"
        assert auth.password == "pass"

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    # Must NOT raise
    av.run_eam("user", "pass")

    # Both phases invoked, even though first one failed
    assert calls == ["devices", "positions"]

    error_text = " ".join(logger.errors + logger.exceptions)
    # Error log must mention devices / method name & message
    assert "devices" in error_text or "sync_eam_devices" in error_text
    assert "boom" in error_text


# 3, 6, 8, 13: positions raises → error logged; exception swallowed


def test_run_eam_positions_failure_is_logged_and_swallowed():
    """
    If sync_eam_positions raises:
    - sync_eam_devices must be called once.
    - run_eam must catch/log the error.
    - No exception should propagate.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[str] = []

    def fake_sync_devices(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("devices")
        assert isinstance(auth, HTTPBasicAuth)
        assert auth.username == "user"
        assert auth.password == "pass"

    def fake_sync_positions(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("positions")
        raise ValueError("positions boom")

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    # Must NOT raise
    av.run_eam("user", "pass")

    assert calls == ["devices", "positions"]

    error_text = " ".join(logger.errors + logger.exceptions)
    assert "positions" in error_text or "sync_eam_positions" in error_text
    assert "boom" in error_text


# 4, 6, 13: both raise → both errors logged; run_eam does not crash


def test_run_eam_both_phases_fail_and_both_errors_are_logged():
    """
    If both sync_eam_devices and sync_eam_positions raise:
    - run_eam must log both errors.
    - Still return cleanly without propagating exceptions.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[str] = []

    def fake_sync_devices(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("devices")
        raise RuntimeError("devices kaboom")

    def fake_sync_positions(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("positions")
        raise RuntimeError("positions kaboom")

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    # Must NOT raise even though both fail
    av.run_eam("user", "pass")

    assert calls == ["devices", "positions"]

    error_text = " ".join(logger.errors + logger.exceptions)
    # We expect mention of both phases in error logs
    assert "devices" in error_text or "sync_eam_devices" in error_text
    assert "positions" in error_text or "sync_eam_positions" in error_text
    assert "kaboom" in error_text


# 7: no unexpected calls to other pipelines


def test_run_eam_does_not_call_other_pipelines_or_snmp_or_token():
    """
    run_eam must not call:
      - run_landb
      - run_influx_snmp
      - any SNMP functions
      - any token functions
    Only sync_eam_devices and sync_eam_positions.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    # Normal sync stubs
    calls: list[str] = []

    def fake_sync_devices(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("devices")

    def fake_sync_positions(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("positions")

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    # Guards: if any of these are called, we hard-fail the test.
    def forbidden(
        *args: Any, **kwargs: Any
    ) -> None:  # pragma: no cover - only if broken
        raise AssertionError("Unexpected pipeline method was called from run_eam")

    av.run_landb = MethodType(forbidden, av)  # type: ignore[attr-defined]
    av.run_influx_snmp = MethodType(forbidden, av)  # type: ignore[attr-defined]
    av._get_snmp_points = MethodType(forbidden, av)  # type: ignore[attr-defined]
    av._ensure_token = MethodType(forbidden, av)  # type: ignore[attr-defined]

    av.run_eam("user", "pass")

    # Only EAM sync methods should have been called
    assert calls == ["devices", "positions"]


# 10: global state safety (no new attributes set by run_eam)


def test_run_eam_does_not_mutate_global_state_on_avtools_instance():
    """
    run_eam should not set new attributes on AVTools instance
    (beyond what tests attach themselves).
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    # Attach some arbitrary state + sync stubs
    av.logs = True
    av.dbod_helper = object()
    av.extra_state = {"key": "value"}

    def fake_sync_devices(self: AVTools, auth: HTTPBasicAuth) -> None:
        assert isinstance(auth, HTTPBasicAuth)

    def fake_sync_positions(self: AVTools, auth: HTTPBasicAuth) -> None:
        assert isinstance(auth, HTTPBasicAuth)

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    before_keys = set(av.__dict__.keys())
    av.run_eam("user", "pass")
    after_keys = set(av.__dict__.keys())

    # No new attributes added by run_eam
    assert before_keys == after_keys
    # Existing attributes untouched
    assert av.logs is True
    assert isinstance(av.dbod_helper, object)
    assert av.extra_state == {"key": "value"}


# 12: large dataset mock (simulated heavy work inside devices sync)


def test_run_eam_large_dataset_mock_does_not_hang():
    """
    Large dataset mock:
    - sync_eam_devices simulates processing many records (cheap loop).
    - run_eam must still return quickly and call positions afterwards.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[str] = []

    def fake_sync_devices(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("devices")
        # Simulate some "heavy" work without actually being slow
        for _ in range(10_000):
            pass

    def fake_sync_positions(self: AVTools, auth: HTTPBasicAuth) -> None:
        calls.append("positions")

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    av.run_eam("user", "pass")

    assert calls == ["devices", "positions"]
