"""OTLP/HTTP transport: success, HTTP error statuses, timeouts, retries and CA.

The single most important property here: an export is confirmed by MONIT's HTTP
**response status** and by nothing else. ``force_flush()`` has been observed
returning ``True`` while the exporter logged
``Failed to export ... UNAVAILABLE``, which is exactly the silent-drop failure
mode this transport exists to make impossible.
"""

from __future__ import annotations

import base64
import io
import ssl
from pathlib import Path
from typing import Any
from urllib import error as urllib_error

import pytest

from avtools.otlp import http_transport as mod

_TENANT = "avtools"
# Not a credential: a fixed string so the Basic-auth assertion below is exact.
_CREDENTIAL = "unit-test-value"
_ENDPOINT = "https://monit-otlp.cern.ch:4319/v1/metrics"


class _FakeResponse:
    """Minimal stand-in for the object urlopen returns."""

    def __init__(self, status: int, body: bytes = b'{"partialSuccess":{}}') -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, limit: int | None = None) -> bytes:
        return self._body.read(limit) if limit else self._body.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False


def _transport(monkeypatch: Any, urlopen: Any, **kwargs: Any) -> mod.OTLPHttpTransport:
    """Build a transport with the network seam replaced and no real backoff."""
    monkeypatch.setattr(mod, "_urlopen", urlopen)
    kwargs.setdefault("endpoint", _ENDPOINT)
    kwargs.setdefault("tenant", _TENANT)
    kwargs.setdefault("password", _CREDENTIAL)
    kwargs.setdefault("sleep", lambda _s: None)
    return mod.OTLPHttpTransport(**kwargs)


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


def test_post_returns_ok_on_200(monkeypatch: Any) -> None:
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    response = transport.post(b"payload")
    assert response.status == 200
    assert response.ok is True


@pytest.mark.parametrize("status", [200, 202, 204])
def test_any_2xx_counts_as_confirmed(monkeypatch: Any, status: int) -> None:
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(status))
    assert transport.post(b"payload").ok is True


def test_request_carries_basic_auth_and_content_type(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_urlopen(request: Any, **_kwargs: Any) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["data"] = request.data
        seen["headers"] = dict(request.header_items())
        return _FakeResponse(200)

    transport = _transport(monkeypatch, fake_urlopen)
    transport.post(b"body-bytes", content_type=mod.CONTENT_TYPE_PROTOBUF)

    assert seen["url"] == _ENDPOINT
    assert seen["method"] == "POST"
    assert seen["data"] == b"body-bytes"
    headers = {key.lower(): value for key, value in seen["headers"].items()}
    expected = base64.b64encode(f"{_TENANT}:{_CREDENTIAL}".encode()).decode()
    assert headers["authorization"] == f"Basic {expected}"
    assert headers["content-type"] == mod.CONTENT_TYPE_PROTOBUF


def test_json_encoding_sets_the_json_content_type(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_urlopen(request: Any, **_kwargs: Any) -> _FakeResponse:
        seen.update({key.lower(): value for key, value in request.header_items()})
        return _FakeResponse(200)

    transport = _transport(monkeypatch, fake_urlopen)
    transport.post(b"{}", content_type=mod.CONTENT_TYPE_JSON)
    assert seen["content-type"] == mod.CONTENT_TYPE_JSON


def test_tenant_id_header_is_optional_and_applied_when_given(monkeypatch: Any) -> None:
    """MONIT accepts Basic auth alone, so the header is off unless asked for."""
    plain = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    assert "x-scope-orgid" not in plain.headers(mod.CONTENT_TYPE_PROTOBUF)

    scoped = _transport(
        monkeypatch, lambda *_a, **_k: _FakeResponse(200), tenant_id_header="itdcim"
    )
    assert scoped.headers(mod.CONTENT_TYPE_PROTOBUF)["x-scope-orgid"] == "itdcim"


def test_timeout_is_passed_to_urlopen(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_urlopen(_request: Any, *, timeout: float, context: Any) -> _FakeResponse:
        seen["timeout"] = timeout
        seen["context"] = context
        return _FakeResponse(200)

    transport = _transport(monkeypatch, fake_urlopen, timeout_s=4.5)
    transport.post(b"payload")
    assert seen["timeout"] == 4.5
    assert isinstance(seen["context"], ssl.SSLContext)


# ---------------------------------------------------------------------------
# HTTP error statuses
# ---------------------------------------------------------------------------


def test_http_error_status_raises_with_the_status_and_body(monkeypatch: Any) -> None:
    def fake_urlopen(*_a: Any, **_k: Any):
        raise urllib_error.HTTPError(
            _ENDPOINT, 500, "Server Error", {}, io.BytesIO(b"mimir exploded")
        )

    transport = _transport(monkeypatch, fake_urlopen)
    with pytest.raises(mod.OTLPTransportError) as excinfo:
        transport.post(b"payload")
    assert excinfo.value.status == 500
    assert "500" in str(excinfo.value)
    assert "mimir exploded" in str(excinfo.value)


def test_non_2xx_response_without_an_httperror_still_fails(monkeypatch: Any) -> None:
    """A 3xx/4xx surfaced as a normal response must not be read as success."""
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(302, b"redirected"))
    with pytest.raises(mod.OTLPTransportError) as excinfo:
        transport.post(b"payload")
    assert excinfo.value.status == 302


def test_empty_error_body_is_rendered_readably(monkeypatch: Any) -> None:
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(503, b""))
    with pytest.raises(mod.OTLPTransportError, match="<empty response body>"):
        transport.post(b"payload")


@pytest.mark.parametrize("status", [401, 400, 404, 413])
def test_client_errors_are_not_retryable(monkeypatch: Any, status: int) -> None:
    """Retrying bad credentials or a bad payload can only delay the job."""
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(status))
    with pytest.raises(mod.OTLPTransportError) as excinfo:
        transport.post(b"payload")
    assert excinfo.value.retryable is False


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_server_and_throttling_errors_are_retryable(monkeypatch: Any, status: int) -> None:
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(status))
    with pytest.raises(mod.OTLPTransportError) as excinfo:
        transport.post(b"payload")
    assert excinfo.value.retryable is True


def test_response_without_a_status_is_an_error(monkeypatch: Any) -> None:
    class _NoStatus:
        def read(self, *_a: Any) -> bytes:
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_exc: Any) -> bool:
            return False

    transport = _transport(monkeypatch, lambda *_a, **_k: _NoStatus())
    with pytest.raises(mod.OTLPTransportError, match="did not expose a status code"):
        transport.post(b"payload")


# ---------------------------------------------------------------------------
# Timeouts and connection failures
# ---------------------------------------------------------------------------


def test_timeout_raises_a_named_transport_error(monkeypatch: Any) -> None:
    def fake_urlopen(*_a: Any, **_k: Any):
        raise TimeoutError("timed out")

    transport = _transport(monkeypatch, fake_urlopen, timeout_s=2.0)
    with pytest.raises(mod.OTLPTransportError, match="timed out after 2.0s"):
        transport.post(b"payload")


def test_timeout_is_retryable(monkeypatch: Any) -> None:
    def fake_urlopen(*_a: Any, **_k: Any):
        raise TimeoutError("timed out")

    transport = _transport(monkeypatch, fake_urlopen)
    confirmed, error = transport.post_with_retry(b"payload")
    assert confirmed is False
    assert isinstance(error, mod.OTLPTransportError)


def test_connection_failure_reports_the_reason(monkeypatch: Any) -> None:
    def fake_urlopen(*_a: Any, **_k: Any):
        raise urllib_error.URLError("Name or service not known")

    transport = _transport(monkeypatch, fake_urlopen)
    with pytest.raises(mod.OTLPTransportError, match="Name or service not known"):
        transport.post(b"payload")


# ---------------------------------------------------------------------------
# TLS failures point at the real cause
# ---------------------------------------------------------------------------


def test_tls_failure_explains_the_missing_intermediate(monkeypatch: Any) -> None:
    """MONIT does not send its intermediate; the message must say so."""

    def fake_urlopen(*_a: Any, **_k: Any):
        raise ssl.SSLCertVerificationError("unable to get local issuer certificate")

    transport = _transport(monkeypatch, fake_urlopen)
    with pytest.raises(mod.OTLPTransportError) as excinfo:
        transport.post(b"payload")
    message = str(excinfo.value)
    assert "CERN Grid Certification Authority" in message
    assert "CERN Root Certification Authority 2" in message
    assert "--otlp-ca-file" in message
    assert excinfo.value.retryable is False


def test_tls_failure_wrapped_in_a_urlerror_is_also_recognised(monkeypatch: Any) -> None:
    def fake_urlopen(*_a: Any, **_k: Any):
        raise urllib_error.URLError(ssl.SSLCertVerificationError("unable to get local issuer"))

    transport = _transport(monkeypatch, fake_urlopen)
    with pytest.raises(mod.OTLPTransportError) as excinfo:
        transport.post(b"payload")
    assert "CERN Grid Certification Authority" in str(excinfo.value)
    assert excinfo.value.retryable is False


# ---------------------------------------------------------------------------
# Retry budget
# ---------------------------------------------------------------------------


def test_retry_then_succeed(monkeypatch: Any) -> None:
    attempts = {"n": 0}

    def flaky(*_a: Any, **_k: Any) -> _FakeResponse:
        attempts["n"] += 1
        return _FakeResponse(200 if attempts["n"] == 2 else 503)

    slept: list[float] = []
    transport = _transport(monkeypatch, flaky, sleep=slept.append)

    confirmed, error = transport.post_with_retry(b"payload")
    assert confirmed is True
    assert error is None
    assert attempts["n"] == 2
    assert slept == [mod.DEFAULT_BACKOFF_S * 1]  # one backoff, growing with the attempt


def test_retry_exhausted_reports_unconfirmed_not_success(monkeypatch: Any) -> None:
    attempts = {"n": 0}

    def always_503(*_a: Any, **_k: Any) -> _FakeResponse:
        attempts["n"] += 1
        return _FakeResponse(503)

    slept: list[float] = []
    transport = _transport(monkeypatch, always_503, sleep=slept.append, max_attempts=3)

    confirmed, error = transport.post_with_retry(b"payload")
    assert confirmed is False
    assert isinstance(error, mod.OTLPTransportError)
    assert error.status == 503
    assert attempts["n"] == 3
    # Backoff grows with the attempt number and is not slept after the last try.
    assert slept == [mod.DEFAULT_BACKOFF_S * 1, mod.DEFAULT_BACKOFF_S * 2]


def test_non_retryable_failure_stops_after_one_attempt(monkeypatch: Any) -> None:
    attempts = {"n": 0}

    def unauthorized(*_a: Any, **_k: Any) -> _FakeResponse:
        attempts["n"] += 1
        return _FakeResponse(401)

    slept: list[float] = []
    transport = _transport(monkeypatch, unauthorized, sleep=slept.append)

    confirmed, error = transport.post_with_retry(b"payload")
    assert confirmed is False
    assert attempts["n"] == 1
    assert slept == []
    assert isinstance(error, mod.OTLPTransportError)
    assert error.status == 401


def test_unexpected_exception_does_not_escape_the_retry_loop(monkeypatch: Any) -> None:
    """A transport bug must not crash a collection cycle that already succeeded."""

    def boom(*_a: Any, **_k: Any):
        raise RuntimeError("unexpected")

    transport = _transport(monkeypatch, boom, max_attempts=2)
    confirmed, error = transport.post_with_retry(b"payload")
    assert confirmed is False
    assert isinstance(error, RuntimeError)


def test_single_attempt_transport_does_not_sleep(monkeypatch: Any) -> None:
    slept: list[float] = []
    transport = _transport(
        monkeypatch, lambda *_a, **_k: _FakeResponse(503), sleep=slept.append, max_attempts=1
    )
    confirmed, _error = transport.post_with_retry(b"payload")
    assert confirmed is False
    assert slept == []


# ---------------------------------------------------------------------------
# CA resolution
# ---------------------------------------------------------------------------


def test_bundled_ca_chain_ships_with_the_package() -> None:
    """The wheel must carry the chain; the system trust store does NOT work."""
    path = mod.default_ca_file()
    assert path is not None
    text = Path(path).read_text()
    assert text.count("-----BEGIN CERTIFICATE-----") == 2


def test_bundled_ca_chain_loads_into_a_verifying_ssl_context() -> None:
    """Two certificates: the CERN Grid intermediate plus CERN Root CA 2."""
    context = ssl.create_default_context(cafile=mod.default_ca_file())
    assert context.verify_mode is ssl.CERT_REQUIRED
    subjects = {
        dict(item for entry in cert["subject"] for item in entry).get("commonName")
        for cert in context.get_ca_certs()
    }
    assert "CERN Grid Certification Authority" in subjects
    assert "CERN Root Certification Authority 2" in subjects


def test_ca_defaults_to_the_bundled_chain(monkeypatch: Any) -> None:
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    assert transport.ca_file == mod.default_ca_file()


def test_explicit_ca_file_wins(monkeypatch: Any, tmp_path: Path) -> None:
    custom = tmp_path / "custom-chain.pem"
    custom.write_text(Path(mod.default_ca_file()).read_text())
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), ca_file=str(custom))
    assert transport.ca_file == str(custom)


def test_blank_ca_file_falls_back_to_the_bundled_chain() -> None:
    assert mod.resolve_ca_file("   ") == mod.default_ca_file()


def test_missing_ca_file_fails_loudly(monkeypatch: Any, tmp_path: Path) -> None:
    """Silently falling back to the system store would look like working TLS."""
    missing = str(tmp_path / "nope.pem")
    with pytest.raises(mod.OTLPTransportError, match="does not exist"):
        _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), ca_file=missing)


def test_unparseable_ca_file_fails_loudly(monkeypatch: Any, tmp_path: Path) -> None:
    junk = tmp_path / "junk.pem"
    junk.write_text("not a certificate")
    with pytest.raises(mod.OTLPTransportError, match="could not be loaded"):
        _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), ca_file=str(junk))


def test_missing_bundled_chain_falls_back_to_the_system_store(monkeypatch: Any) -> None:
    """An install without data files still runs; it just has to pass verification."""
    monkeypatch.setattr(mod, "default_ca_file", lambda: None)
    transport = _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    assert transport.ca_file is None
    assert transport.post(b"payload").ok is True


def test_plaintext_endpoint_builds_no_ssl_context(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_urlopen(_request: Any, *, timeout: float, context: Any) -> _FakeResponse:
        seen["context"] = context
        return _FakeResponse(200)

    transport = _transport(monkeypatch, fake_urlopen, endpoint="http://localhost:4318/v1/metrics")
    assert transport.is_tls is False
    transport.post(b"payload")
    assert seen["context"] is None


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_bare_host_port_endpoint_is_rejected_with_a_pointer(monkeypatch: Any) -> None:
    with pytest.raises(mod.OTLPTransportError, match="normalize_http_endpoint"):
        _transport(
            monkeypatch, lambda *_a, **_k: _FakeResponse(200), endpoint="monit-otlp.cern.ch:4316"
        )


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_endpoint_is_rejected(monkeypatch: Any, bad: str) -> None:
    with pytest.raises(mod.OTLPTransportError, match="endpoint is required"):
        _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), endpoint=bad)


def test_empty_tenant_is_rejected(monkeypatch: Any) -> None:
    with pytest.raises(mod.OTLPTransportError, match="tenant is required"):
        _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), tenant="")


def test_empty_password_is_rejected(monkeypatch: Any) -> None:
    with pytest.raises(mod.OTLPTransportError, match="password is required"):
        _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), password="")


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan")])
def test_non_positive_timeout_is_rejected(monkeypatch: Any, bad: float) -> None:
    with pytest.raises(mod.OTLPTransportError, match="timeout_s"):
        _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), timeout_s=bad)


@pytest.mark.parametrize("bad", [0, -3, 1.5])
def test_invalid_max_attempts_is_rejected(monkeypatch: Any, bad: Any) -> None:
    with pytest.raises(mod.OTLPTransportError, match="max_attempts"):
        _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), max_attempts=bad)


@pytest.mark.parametrize("bad", [-1.0, float("nan")])
def test_invalid_backoff_is_rejected(monkeypatch: Any, bad: float) -> None:
    with pytest.raises(mod.OTLPTransportError, match="backoff_s"):
        _transport(monkeypatch, lambda *_a, **_k: _FakeResponse(200), backoff_s=bad)


def test_retry_defaults_match_the_publishers_flush_budget() -> None:
    """The migration must not change how long a failing cycle takes."""
    from avtools.timeseries import otlp_publisher

    assert mod.DEFAULT_MAX_ATTEMPTS == otlp_publisher._FLUSH_ATTEMPTS
    assert mod.DEFAULT_BACKOFF_S == otlp_publisher._FLUSH_BACKOFF_S
    assert mod.DEFAULT_TIMEOUT_S * 1000 == otlp_publisher._FLUSH_TIMEOUT_MS
