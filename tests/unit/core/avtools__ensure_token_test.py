from __future__ import annotations

from typing import Any

import structlog

from avtools.core.av_tools import AVTools


class DummyAuth:
    def __init__(self, token: Any) -> None:
        self.token = token


class DummySession:
    """
    Minimal stand-in for ServiceAuthSession.

    - .auth.token controls whether _ensure_token thinks we already have a token.
    - .get(...) records calls and returns a dummy response or raises.
    - On a successful (ok=True) GET when token is None, it simulates the
      OAuth client populating auth.token.
    """

    def __init__(
        self,
        token: Any,
        *,
        ok: bool = True,
        status_code: int = 200,
        raise_exc: bool = False,
    ) -> None:
        self.auth = DummyAuth(token)
        self.headers: dict[str, str] = {"X-Existing": "keep-me"}
        self._ok = ok
        self._status_code = status_code
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, dict[str, Any] | None, bool | None]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        verify: bool | None = None,
    ):
        self.calls.append((url, params, verify))
        if self._raise_exc:
            raise RuntimeError("dummy error from GET")

        class Resp:
            def __init__(self, ok: bool, status_code: int) -> None:
                self.ok = ok
                self.status_code = status_code

        # Simulate ServiceAuthSession side-effect: a successful GET ensures
        # auth.token is now populated.
        if self._ok and self.auth.token is None:
            self.auth.token = "dummy-token"

        return Resp(self._ok, self._status_code)


class DummyConfig:
    def __init__(self, base_url: str = "https://landb.example.cern") -> None:
        self.base_url = base_url
        self.device_endpoint = "api/devices"


def make_avtools_for_tests() -> AVTools:
    # Bypass __init__ so we don't touch PostgresClient at all
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    return av


# ---------------------------------------------------------------------------
# Basic behaviour: cached token vs no token
# ---------------------------------------------------------------------------


def test_ensure_token_uses_existing_token_without_expires_at():
    """
    If session.auth.token is truthy (no expires_at), _ensure_token should NOT
    trigger a probe GET and must leave auth/headers untouched.
    """
    av = make_avtools_for_tests()

    token = object()  # any truthy object without expires_at
    session = DummySession(token=token)
    config = DummyConfig()

    auth_before = session.auth
    headers_before = session.headers.copy()

    av._ensure_token(session, config)

    assert session.calls == []
    assert session.auth is auth_before
    assert session.headers == headers_before


def test_ensure_token_uses_existing_token_with_expires_at():
    """
    If token has expires_at attribute, it is treated as a cached token and no
    probe GET occurs.
    """

    class TokenWithExpiry:
        def __init__(self) -> None:
            self.expires_at = 2025  # just a value, not interpreted by AVTools

    av = make_avtools_for_tests()
    session = DummySession(token=TokenWithExpiry())
    config = DummyConfig()

    av._ensure_token(session, config)

    # Still no GET when a token is present (regardless of expires_at value)
    assert session.calls == []


def test_ensure_token_fetches_token_when_missing_and_probe_ok():
    """
    If no token, _ensure_token should fire a probe GET once and, on a successful
    response, we expect the session.auth.token to be populated (simulated here
    by DummySession).
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, ok=True, status_code=200)
    config = DummyConfig()

    assert session.auth.token is None

    av._ensure_token(session, config)

    # Exactly one probe call
    assert len(session.calls) == 1
    url, params, verify = session.calls[0]
    assert url == "https://landb.example.cern/api/devices"
    assert params == {"_limit": 1}
    assert verify is True

    # DummySession simulates OAuth client having populated the token
    assert session.auth.token == "dummy-token"


def test_ensure_token_fetches_token_when_missing_and_probe_not_ok():
    """
    If no token and probe GET returns non-ok status (e.g. 404),
    _ensure_token should still not raise, just log, and token remains None.
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, ok=False, status_code=404)
    config = DummyConfig()

    assert session.auth.token is None

    av._ensure_token(session, config)

    assert len(session.calls) == 1
    url, params, verify = session.calls[0]
    assert url == "https://landb.example.cern/api/devices"
    assert params == {"_limit": 1}
    assert verify is True

    # Since the probe failed, DummySession does not populate auth.token
    assert session.auth.token is None


def test_ensure_token_does_not_propagate_exceptions_from_get():
    """
    If GET raises, _ensure_token should catch and log, not crash.
    Token remains unchanged (None in this setup).
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, raise_exc=True)
    config = DummyConfig()

    # Should not raise, even though session.get() throws
    av._ensure_token(session, config)

    # We attempted exactly one probe before the exception
    assert len(session.calls) == 1
    assert session.auth.token is None


def test_ensure_token_uses_config_for_probe_url():
    """
    Ensure that the probe URL is built from the supplied LanDBConfig,
    not hard-coded in the method.
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, ok=True, status_code=200)
    # Use a non-default base_url to ensure it is respected
    config = DummyConfig(base_url="https://custom.landb.cern")

    av._ensure_token(session, config)

    assert len(session.calls) == 1
    url, params, verify = session.calls[0]
    assert url == "https://custom.landb.cern/api/devices"
    assert params == {"_limit": 1}
    assert verify is True


def test_ensure_token_with_cached_token_does_not_mutate_auth_or_headers():
    """
    When a cached token already exists, _ensure_token must not touch
    session.auth or session.headers and must not perform any probe GET.
    """
    av = make_avtools_for_tests()

    token = object()
    session = DummySession(token=token)
    config = DummyConfig()

    auth_before = session.auth
    headers_before = session.headers.copy()

    av._ensure_token(session, config)

    assert session.calls == []
    assert session.auth is auth_before
    assert session.headers == headers_before


# ---------------------------------------------------------------------------
# Logging and idempotence
# ---------------------------------------------------------------------------


class LoggerRecorder:
    """
    Simple logger stub to capture info/error messages emitted by _ensure_token.
    """

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.errors: list[str] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        # Message is already formatted via f-string in AVTools._ensure_token
        self.infos.append(str(msg))

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.errors.append(str(msg))


def test_ensure_token_logs_cached_token_message_without_expires_at():
    """
    With an existing token (no expires_at), we should log that we are using
    a cached access token and not hit the network.
    """
    av = make_avtools_for_tests()
    logger = LoggerRecorder()
    av.logger = logger

    token = object()
    session = DummySession(token=token)
    config = DummyConfig()

    av._ensure_token(session, config)

    assert session.calls == []
    assert any("Using cached access token" in msg for msg in logger.infos)
    assert logger.errors == []


def test_ensure_token_logs_cached_token_message_with_expires_at():
    """
    With an existing token that has expires_at, the log should include that
    expiry value.
    """

    class TokenWithExpiry:
        def __init__(self) -> None:
            self.expires_at = 2025

    av = make_avtools_for_tests()
    logger = LoggerRecorder()
    av.logger = logger

    session = DummySession(token=TokenWithExpiry())
    config = DummyConfig()

    av._ensure_token(session, config)

    assert session.calls == []
    assert any(
        "Using cached access token (expires at 2025)" in msg for msg in logger.infos
    )
    assert logger.errors == []


def test_ensure_token_logs_probe_flow_and_success_when_fetching_token():
    """
    When no token exists and the probe succeeds:
    - we log that we are probing for a token;
    - we log that the token was obtained successfully.
    """
    av = make_avtools_for_tests()
    logger = LoggerRecorder()
    av.logger = logger

    session = DummySession(token=None, ok=True, status_code=200)
    config = DummyConfig()

    av._ensure_token(session, config)

    # One HTTP call
    assert len(session.calls) == 1
    # Logger should record both probe and success messages
    assert any("No access token found; probing" in msg for msg in logger.infos)
    assert any(
        "Successfully obtained new access token via probe GET." in msg
        for msg in logger.infos
    )
    assert logger.errors == []


def test_ensure_token_logs_error_when_probe_not_ok():
    """
    Non-OK response should result in an error log mentioning the status code.
    """
    av = make_avtools_for_tests()
    logger = LoggerRecorder()
    av.logger = logger

    session = DummySession(token=None, ok=False, status_code=404)
    config = DummyConfig()

    av._ensure_token(session, config)

    # One HTTP call
    assert len(session.calls) == 1

    # Info about probing + error about failure
    assert any("No access token found; probing" in msg for msg in logger.infos)
    assert any(
        "Probe GET failed [404] when obtaining token." in msg for msg in logger.errors
    )


def test_ensure_token_logs_error_when_get_raises():
    """
    Exceptions from the probe must be caught and logged as an error.
    """
    av = make_avtools_for_tests()
    logger = LoggerRecorder()
    av.logger = logger

    session = DummySession(token=None, raise_exc=True)
    config = DummyConfig()

    # Should not raise
    av._ensure_token(session, config)

    assert len(session.calls) == 1
    assert any(
        "Error during token-fetch probe: dummy error from GET" in msg
        for msg in logger.errors
    )


def test_ensure_token_second_call_does_not_probe_when_token_already_set():
    """
    After a successful probe that populates session.auth.token, a second call
    to _ensure_token must not issue another HTTP request.
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, ok=True, status_code=200)
    config = DummyConfig()

    # First call: should probe and populate token
    av._ensure_token(session, config)
    assert session.auth.token == "dummy-token"
    assert len(session.calls) == 1

    # Second call: token exists → no additional GET
    av._ensure_token(session, config)
    assert len(session.calls) == 1  # still exactly one call


def test_ensure_token_probe_does_not_mutate_auth_object_or_headers():
    """
    Even when probing (no token case), _ensure_token should not replace
    session.auth or modify headers; only token value may change.
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, ok=True, status_code=200)
    config = DummyConfig()

    auth_before = session.auth
    headers_before = session.headers.copy()

    av._ensure_token(session, config)

    # Same auth object, but token may be updated
    assert session.auth is auth_before
    assert session.auth.token == "dummy-token"

    # Headers unchanged
    assert session.headers == headers_before


# ---------------------------------------------------------------------------
# “Retry-ish” behaviour across multiple calls
# ---------------------------------------------------------------------------


def test_ensure_token_allows_second_probe_after_non_ok_first_probe():
    """
    If the first probe returns non-ok and token remains None, a second call
    should issue another GET and can succeed later.
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, ok=False, status_code=503)
    config = DummyConfig()

    # First attempt: fails, token still None
    av._ensure_token(session, config)
    assert len(session.calls) == 1
    assert session.auth.token is None

    # Flip the dummy session to simulate the backend coming back
    session._ok = True
    session._status_code = 200

    # Second attempt: should call GET again and now populate token
    av._ensure_token(session, config)
    assert len(session.calls) == 2
    assert session.auth.token == "dummy-token"


def test_ensure_token_allows_second_probe_after_exception_on_first_probe():
    """
    If the first probe raises an exception, a later call should be able
    to try again successfully.
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, raise_exc=True)
    config = DummyConfig()

    # First attempt: raises inside DummySession.get, but _ensure_token swallows
    av._ensure_token(session, config)
    assert len(session.calls) == 1
    assert session.auth.token is None

    # Clear the error and make the probe succeed
    session._raise_exc = False
    session._ok = True
    session._status_code = 200

    av._ensure_token(session, config)
    # Now we should have a second call and a populated token
    assert len(session.calls) == 2
    assert session.auth.token == "dummy-token"
