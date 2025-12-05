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
        self._ok = ok
        self._status_code = status_code
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, dict[str, Any] | None, bool | None]] = []

    def get(
        self, url: str, params: dict[str, Any] | None = None, verify: bool | None = None
    ):
        self.calls.append((url, params, verify))
        if self._raise_exc:
            raise RuntimeError("dummy error from GET")

        class Resp:
            def __init__(self, ok: bool, status_code: int) -> None:
                self.ok = ok
                self.status_code = status_code

        return Resp(self._ok, self._status_code)


class DummyConfig:
    def __init__(self) -> None:
        self.base_url = "https://landb.example.cern"
        self.device_endpoint = "api/devices"


def make_avtools_for_tests() -> AVTools:
    # Bypass __init__ so we don't touch PostgresClient at all
    av = object.__new__(AVTools)
    av.logger = structlog.get_logger("AVToolsTest")
    return av


def test_ensure_token_uses_existing_token_without_expires_at():
    """If session.auth.token is truthy (no expires_at), do not call GET."""
    av = make_avtools_for_tests()

    token = object()  # any truthy object without expires_at
    session = DummySession(token=token)
    config = DummyConfig()

    av._ensure_token(session, config)

    assert session.calls == []


def test_ensure_token_uses_existing_token_with_expires_at():
    """If token has expires_at attribute, still do not call GET."""

    class TokenWithExpiry:
        def __init__(self) -> None:
            self.expires_at = 2025  # meme-safe year, just a value

    av = make_avtools_for_tests()
    session = DummySession(token=TokenWithExpiry())
    config = DummyConfig()

    av._ensure_token(session, config)

    assert session.calls == []


def test_ensure_token_fetches_token_when_missing_and_probe_ok():
    """If no token, _ensure_token should fire a probe GET once and not raise."""
    av = make_avtools_for_tests()
    session = DummySession(token=None, ok=True, status_code=200)
    config = DummyConfig()

    av._ensure_token(session, config)

    assert len(session.calls) == 1
    url, params, verify = session.calls[0]
    assert url == "https://landb.example.cern/api/devices"
    assert params == {"_limit": 1}
    assert verify is True


def test_ensure_token_fetches_token_when_missing_and_probe_not_ok():
    """
    If no token and probe GET returns non-ok status (e.g. 404),
    _ensure_token should still not raise, just log.
    """
    av = make_avtools_for_tests()
    session = DummySession(token=None, ok=False, status_code=404)
    config = DummyConfig()

    av._ensure_token(session, config)

    assert len(session.calls) == 1
    url, params, verify = session.calls[0]
    assert url == "https://landb.example.cern/api/devices"
    assert params == {"_limit": 1}
    assert verify is True


def test_ensure_token_does_not_propagate_exceptions_from_get():
    """If GET raises, _ensure_token should catch and log, not crash."""
    av = make_avtools_for_tests()
    session = DummySession(token=None, raise_exc=True)
    config = DummyConfig()

    # Should not raise, even though session.get() throws
    av._ensure_token(session, config)

    assert len(session.calls) == 1
