from __future__ import annotations

from types import MethodType
from typing import Any

import structlog

from avtools.core.av_tools import AVTools


class DummyLogger:
    """
    Minimal logger to capture info/error/exception messages for assertions.

    NOTE: av_tools.py uses structlog-style calls:
        logger.error("event_name", key=value, ...)
    We only store the first positional "event_name" string here.
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


def patch_register_credentials(
    monkeypatch: Any, calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """
    Patch the register_credentials symbol *as used* by avtools.core.av_tools.run_eam.
    """
    import avtools.core.av_tools as av_mod

    def fake_register_credentials(**kwargs: Any) -> None:
        calls.append(("register_credentials", dict(kwargs)))

    monkeypatch.setattr(
        av_mod, "register_credentials", fake_register_credentials, raising=True
    )


def patch_eam_exceptions(monkeypatch: Any) -> None:
    """
    av_tools.py now references EamClientRetryableHTTPError, etc. in except clauses.
    If those names aren't present in avtools.core.av_tools, Python will raise NameError
    when an exception occurs.

    These dummies are enough for matching + logging attributes used by run_eam().
    """
    import avtools.core.av_tools as av_mod

    class EamRestClientError(Exception):
        pass

    class EamClientHTTPError(EamRestClientError):
        def __init__(
            self,
            message: str = "http error",
            *,
            status_code: int = 500,
            url: str = "https://example/eam",
            request_id: str | None = None,
        ) -> None:
            super().__init__(message)
            self.status_code = status_code
            self.url = url
            self.request_id = request_id

    class EamClientRetryableHTTPError(EamClientHTTPError):
        def __init__(
            self,
            message: str = "retryable http error",
            *,
            status_code: int = 503,
            url: str = "https://example/eam",
            request_id: str | None = None,
            retry_after: int | None = None,
        ) -> None:
            super().__init__(
                message, status_code=status_code, url=url, request_id=request_id
            )
            self.retry_after = retry_after

    class EamClientTimeoutError(EamRestClientError):
        def __init__(
            self, message: str = "timeout", *, retry_after_s: int | None = None
        ) -> None:
            super().__init__(message)
            self._retry_after_s = retry_after_s

        def retry_after(self) -> int | None:
            return self._retry_after_s

    class EamClientTransportError(EamRestClientError):
        def __init__(
            self, message: str = "transport", *, original: Exception | None = None
        ) -> None:
            super().__init__(message)
            self.original = original

    class EamQueryError(EamRestClientError):
        pass

    monkeypatch.setattr(av_mod, "EamRestClientError", EamRestClientError, raising=False)
    monkeypatch.setattr(av_mod, "EamClientHTTPError", EamClientHTTPError, raising=False)
    monkeypatch.setattr(
        av_mod,
        "EamClientRetryableHTTPError",
        EamClientRetryableHTTPError,
        raising=False,
    )
    monkeypatch.setattr(
        av_mod, "EamClientTimeoutError", EamClientTimeoutError, raising=False
    )
    monkeypatch.setattr(
        av_mod, "EamClientTransportError", EamClientTransportError, raising=False
    )
    monkeypatch.setattr(av_mod, "EamQueryError", EamQueryError, raising=False)


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


def test_run_eam_happy_path_calls_both_in_order_with_shared_auth_and_logs(
    monkeypatch: Any,
) -> None:
    """
    Happy path (matches current run_eam implementation):
    - run_eam must call register_credentials once with correct credentials.
    - sync_eam_devices is called first, then sync_eam_positions.
    - Defaults:
        devices: asset_grid OSOBJA, department_code AV, limit None
        positions: position_grid OSOBJP, department_code AV, limit None
    - Returns None.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[tuple[str, dict[str, Any]]] = []
    patch_register_credentials(monkeypatch, calls)

    def fake_sync_devices(self: AVTools, **kwargs: Any) -> None:
        calls.append(("devices", dict(kwargs)))

    def fake_sync_positions(self: AVTools, **kwargs: Any) -> None:
        calls.append(("positions", dict(kwargs)))

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    result = av.run_eam("eam-user", "eam-pass")
    assert result is None

    # Order: register → devices → positions
    assert [name for name, _ in calls] == [
        "register_credentials",
        "devices",
        "positions",
    ]

    reg = calls[0][1]
    assert reg["base_url"] == "https://cmmsx.cern.ch/"
    assert reg["user"] == "eam-user"
    assert reg["password"] == "eam-pass"

    dev = calls[1][1]
    assert dev["asset_grid"] == "OSOBJA"
    assert dev["department_code"] == "AV"
    assert dev["limit"] is None

    pos = calls[2][1]
    assert pos["position_grid"] == "OSOBJP"
    assert pos["department_code"] == "AV"
    assert pos["limit"] is None

    # No unexpected errors logged in happy path
    assert logger.errors == []
    assert logger.exceptions == []


def test_run_eam_can_be_called_multiple_times_without_caching(monkeypatch: Any) -> None:
    """
    Multi-call sanity:
    - Calling run_eam() twice calls register_credentials twice (no caching).
    - Calls devices+positions twice in correct order.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[tuple[str, dict[str, Any]]] = []
    patch_register_credentials(monkeypatch, calls)

    def fake_sync_devices(self: AVTools, **kwargs: Any) -> None:
        calls.append(("devices", dict(kwargs)))

    def fake_sync_positions(self: AVTools, **kwargs: Any) -> None:
        calls.append(("positions", dict(kwargs)))

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    av.run_eam("user-1", "pass-1")
    av.run_eam("user-2", "pass-2")

    assert [name for name, _ in calls] == [
        "register_credentials",
        "devices",
        "positions",
        "register_credentials",
        "devices",
        "positions",
    ]

    reg1 = calls[0][1]
    reg2 = calls[3][1]
    assert reg1["user"] == "user-1"
    assert reg1["password"] == "pass-1"
    assert reg2["user"] == "user-2"
    assert reg2["password"] == "pass-2"


# -----------------------------------------------------------------------------
# Updated failure behavior: run_eam swallows phase failures and continues
# -----------------------------------------------------------------------------


def test_run_eam_devices_failure_still_runs_positions_and_logs_error(
    monkeypatch: Any,
) -> None:
    """
    Current run_eam behavior (see av_tools.py):
    If sync_eam_devices raises a handled EAM exception, run_eam:
      - logs an error event
      - continues and still runs sync_eam_positions
      - does NOT raise
    """
    patch_eam_exceptions(monkeypatch)

    # Import the patched exception symbol from the module where run_eam resolves it.
    import avtools.core.av_tools as av_mod

    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[str] = []
    reg_calls: list[tuple[str, dict[str, Any]]] = []
    patch_register_credentials(monkeypatch, reg_calls)

    def fake_sync_devices(self: AVTools, **kwargs: Any) -> None:
        calls.append("devices")
        raise av_mod.EamClientRetryableHTTPError(
            "devices retryable",
            status_code=503,
            url="https://cmmsx.cern.ch/dev",
            retry_after=5,
        )

    def fake_sync_positions(self: AVTools, **kwargs: Any) -> None:
        calls.append("positions")

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    result = av.run_eam("user", "pass")
    assert result is None

    # register happened, devices attempted, positions still called
    assert len(reg_calls) == 1
    assert calls == ["devices", "positions"]

    # Error event logged
    assert "eam_devices_retryable_http_error" in logger.errors


def test_run_eam_positions_failure_is_logged_and_swallowed(monkeypatch: Any) -> None:
    """
    Current run_eam behavior:
    If sync_eam_positions raises a handled EAM exception, run_eam:
      - logs an error event
      - does NOT raise
    """
    patch_eam_exceptions(monkeypatch)
    import avtools.core.av_tools as av_mod

    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[str] = []
    reg_calls: list[tuple[str, dict[str, Any]]] = []
    patch_register_credentials(monkeypatch, reg_calls)

    def fake_sync_devices(self: AVTools, **kwargs: Any) -> None:
        calls.append("devices")

    def fake_sync_positions(self: AVTools, **kwargs: Any) -> None:
        calls.append("positions")
        raise av_mod.EamClientHTTPError(
            "positions http", status_code=500, url="https://cmmsx.cern.ch/pos"
        )

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    result = av.run_eam("user", "pass")
    assert result is None

    assert len(reg_calls) == 1
    assert calls == ["devices", "positions"]
    assert "eam_positions_http_error" in logger.errors


def test_run_eam_both_phases_fail_and_both_errors_are_logged(monkeypatch: Any) -> None:
    """
    Current run_eam behavior:
    Devices failure is swallowed and positions is still attempted.
    If both fail with handled EAM exceptions, both errors are logged.
    """
    patch_eam_exceptions(monkeypatch)
    import avtools.core.av_tools as av_mod

    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[str] = []
    reg_calls: list[tuple[str, dict[str, Any]]] = []
    patch_register_credentials(monkeypatch, reg_calls)

    def fake_sync_devices(self: AVTools, **kwargs: Any) -> None:
        calls.append("devices")
        raise av_mod.EamClientTimeoutError("devices timeout", retry_after_s=10)

    def fake_sync_positions(self: AVTools, **kwargs: Any) -> None:
        calls.append("positions")
        raise av_mod.EamQueryError("positions query error")

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    result = av.run_eam("user", "pass")
    assert result is None

    assert len(reg_calls) == 1
    assert calls == ["devices", "positions"]

    # Two error events: one for devices timeout, one for positions query error
    assert "eam_devices_timeout" in logger.errors
    assert "eam_positions_query_error" in logger.errors


# -----------------------------------------------------------------------------
# Guardrails
# -----------------------------------------------------------------------------


def test_run_eam_does_not_call_other_pipelines_or_snmp_or_token(
    monkeypatch: Any,
) -> None:
    """
    run_eam must not call:
      - run_landb
      - run_influx_snmp
      - any SNMP functions
      - any token functions
    Only register_credentials + sync_eam_devices + sync_eam_positions.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    calls: list[str] = []
    reg_calls: list[tuple[str, dict[str, Any]]] = []
    patch_register_credentials(monkeypatch, reg_calls)

    def fake_sync_devices(self: AVTools, **kwargs: Any) -> None:
        calls.append("devices")

    def fake_sync_positions(self: AVTools, **kwargs: Any) -> None:
        calls.append("positions")

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    def forbidden(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
        raise AssertionError("Unexpected pipeline method was called from run_eam")

    av.run_landb = MethodType(forbidden, av)  # type: ignore[attr-defined]
    av.run_influx_snmp = MethodType(forbidden, av)  # type: ignore[attr-defined]
    av._get_snmp_points = MethodType(forbidden, av)  # type: ignore[attr-defined]
    av._ensure_token = MethodType(forbidden, av)  # type: ignore[attr-defined]

    av.run_eam("user", "pass")

    assert len(reg_calls) == 1
    assert calls == ["devices", "positions"]


def test_run_eam_does_not_mutate_global_state_on_avtools_instance(
    monkeypatch: Any,
) -> None:
    """
    run_eam should not set new attributes on AVTools instance
    (beyond what tests attach themselves).
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    reg_calls: list[tuple[str, dict[str, Any]]] = []
    patch_register_credentials(monkeypatch, reg_calls)

    # Attach some arbitrary state + sync stubs
    av.logs = True
    av.dbod_helper = object()
    av.extra_state = {"key": "value"}

    def fake_sync_devices(self: AVTools, **kwargs: Any) -> None:
        pass

    def fake_sync_positions(self: AVTools, **kwargs: Any) -> None:
        pass

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


def test_run_eam_large_dataset_mock_does_not_hang(monkeypatch: Any) -> None:
    """
    Large dataset mock:
    - sync_eam_devices simulates processing many records (cheap loop).
    - run_eam must still return and call positions afterwards.
    """
    av = make_avtools_for_tests()
    logger = DummyLogger()
    av.logger = logger  # type: ignore[assignment]

    reg_calls: list[tuple[str, dict[str, Any]]] = []
    patch_register_credentials(monkeypatch, reg_calls)

    calls: list[str] = []

    def fake_sync_devices(self: AVTools, **kwargs: Any) -> None:
        calls.append("devices")
        for _ in range(10_000):
            pass

    def fake_sync_positions(self: AVTools, **kwargs: Any) -> None:
        calls.append("positions")

    attach_sync_stubs(av, fake_sync_devices, fake_sync_positions)

    av.run_eam("user", "pass")

    assert len(reg_calls) == 1
    assert calls == ["devices", "positions"]
