from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

import avtools.main as main


class DummyResp:
    def __init__(self, *, status_code: int = 200, text: str = "", payload: Any = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_get_token_happy_path(monkeypatch: Any) -> None:
    runner = CliRunner()

    def fake_post(url: str, data: dict[str, str], timeout: int):
        assert data["grant_type"] == "client_credentials"
        assert data["client_id"] == "cid"
        assert data["client_secret"] == "sec"
        assert data["audience"] == "aud"
        assert data["scope"] == "s"
        return DummyResp(payload={"access_token": "TOK"})

    monkeypatch.setattr(main.requests, "post", fake_post)

    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            "https://token",
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
            "--audience",
            "aud",
            "--scope",
            "s",
        ],
    )

    assert res.exit_code == 0
    assert res.output.strip() == "TOK"


def test_get_token_request_exception(monkeypatch: Any) -> None:
    runner = CliRunner()

    class Boom(main.requests.RequestException):
        pass

    monkeypatch.setattr(
        main.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(Boom("net")),
    )

    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            "https://token",
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
        ],
    )
    assert res.exit_code != 0
    assert "Token request failed" in res.output


def test_get_token_http_error(monkeypatch: Any) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        main.requests,
        "post",
        lambda *a, **k: DummyResp(status_code=401, text=" nope "),
    )

    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            "https://token",
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
        ],
    )
    assert res.exit_code != 0
    assert "401" in res.output


def test_get_token_non_json(monkeypatch: Any) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        main.requests,
        "post",
        lambda *a, **k: DummyResp(payload=ValueError("bad"), text="<html>"),
    )

    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            "https://token",
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
        ],
    )
    assert res.exit_code != 0
    assert "did not return JSON" in res.output


def test_get_token_missing_access_token(monkeypatch: Any) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        main.requests,
        "post",
        lambda *a, **k: DummyResp(payload={"foo": "bar"}),
    )

    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            "https://token",
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
        ],
    )
    assert res.exit_code != 0
    assert "Missing access_token" in res.output
