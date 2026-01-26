from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[tuple[str, dict[str, Any]]] = []
        self.exceptions: list[str] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self.errors.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        pass

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exceptions.append(event)


def _make_avtools() -> Any:
    from avtools.core.av_tools import AVTools

    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av.logs = False
    av._landb_initialized = False
    av._eam_sanitizer = object()
    av.dbod_helper = object()
    # Ensure these are not called in this test group
    av.sync_eam_devices = lambda **kwargs: (_ for _ in ()).throw(AssertionError("sync_eam_devices should not run"))  # type: ignore[assignment]
    av.sync_eam_positions = lambda **kwargs: (_ for _ in ()).throw(AssertionError("sync_eam_positions should not run"))  # type: ignore[assignment]
    return av


def _end_status(logger: DummyLogger) -> str:
    ends = [kw for (ev, kw) in logger.infos if ev == "avtools_run_eam_end"]
    assert ends, "expected avtools_run_eam_end info log"
    return str(ends[-1].get("status"))


def test_run_eam_register_credentials_retryable_http_error(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    class DummyRetryableHTTPError(Exception):
        def __init__(self) -> None:
            self.status_code = 503
            self.url = "https://example/"
            self.request_id = "rid"
            self.retry_after = 7

    monkeypatch.setattr(av_mod, "EamClientRetryableHTTPError", DummyRetryableHTTPError)

    called: dict[str, Any] = {}

    def boom(**kwargs: Any) -> None:
        called["called"] = True
        raise DummyRetryableHTTPError()

    monkeypatch.setattr(av_mod, "register_credentials", boom)

    av = _make_avtools()
    assert av.run_eam("u", "p") is None
    assert called.get("called") is True
    assert _end_status(av.logger) == "failed_register_credentials_retryable_http_error"


def test_run_eam_register_credentials_http_error(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    class DummyHTTPError(Exception):
        def __init__(self) -> None:
            self.status_code = 401
            self.url = "https://example/"
            self.request_id = None

    monkeypatch.setattr(av_mod, "EamClientHTTPError", DummyHTTPError)
    monkeypatch.setattr(
        av_mod,
        "register_credentials",
        lambda **kwargs: (_ for _ in ()).throw(DummyHTTPError()),
    )

    av = _make_avtools()
    assert av.run_eam("u", "p") is None
    assert _end_status(av.logger) == "failed_register_credentials_http_error"


def test_run_eam_register_credentials_timeout(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    class DummyTimeout(Exception):
        def retry_after(self) -> int:
            return 3

    monkeypatch.setattr(av_mod, "EamClientTimeoutError", DummyTimeout)
    monkeypatch.setattr(
        av_mod,
        "register_credentials",
        lambda **kwargs: (_ for _ in ()).throw(DummyTimeout()),
    )

    av = _make_avtools()
    assert av.run_eam("u", "p") is None
    assert _end_status(av.logger) == "failed_register_credentials_timeout"


def test_run_eam_register_credentials_transport_error(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    class DummyTransport(Exception):
        def __init__(self) -> None:
            self.original = RuntimeError("socket")

    monkeypatch.setattr(av_mod, "EamClientTransportError", DummyTransport)
    monkeypatch.setattr(
        av_mod,
        "register_credentials",
        lambda **kwargs: (_ for _ in ()).throw(DummyTransport()),
    )

    av = _make_avtools()
    assert av.run_eam("u", "p") is None
    assert _end_status(av.logger) == "failed_register_credentials_transport_error"


def test_run_eam_register_credentials_rest_client_error(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    class DummyRestClientError(Exception):
        pass

    monkeypatch.setattr(av_mod, "EamRestClientError", DummyRestClientError)
    monkeypatch.setattr(
        av_mod,
        "register_credentials",
        lambda **kwargs: (_ for _ in ()).throw(DummyRestClientError("nope")),
    )

    av = _make_avtools()
    assert av.run_eam("u", "p") is None
    assert _end_status(av.logger) == "failed_register_credentials"


def test_run_eam_register_credentials_unexpected(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod

    monkeypatch.setattr(
        av_mod,
        "register_credentials",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    av = _make_avtools()
    assert av.run_eam("u", "p") is None
    assert _end_status(av.logger) == "failed_register_credentials_unexpected"
    assert "eam_register_credentials_failed_unexpected" in av.logger.exceptions
