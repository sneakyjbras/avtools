from __future__ import annotations

from typing import Any

import pytest


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append(event)

    def warning(self, event: str, **kwargs: Any) -> None:
        pass

    def error(self, event: str, **kwargs: Any) -> None:
        pass

    def exception(self, event: str, **kwargs: Any) -> None:
        pass


def test_init_landb_rest_client_registers_only_once(monkeypatch: Any) -> None:
    import avtools.core.av_tools as av_mod
    from avtools.core.av_tools import AVTools

    calls: list[dict[str, Any]] = []

    def fake_register_credentials(**kwargs: Any) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr(av_mod, "landb_register_credentials", fake_register_credentials)

    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av._landb_initialized = False

    av._init_landb_rest_client(
        client_id="cid",
        client_secret="secret",
        audience="aud",
        url="https://landb.example/api/",
    )

    assert av._landb_initialized is True
    assert len(calls) == 1
    assert calls[0]["client_id"] == "cid"
    assert calls[0]["client_secret"] == "secret"
    assert calls[0]["audience"] == "aud"
    assert calls[0]["url"] == "https://landb.example/api/"
    assert calls[0]["user"] is None
    assert calls[0]["user_token"] is None

    # second call is a no-op
    av._init_landb_rest_client(
        client_id="cid2",
        client_secret="secret2",
        audience="aud2",
        url="https://landb.example/api2/",
    )
    assert len(calls) == 1
