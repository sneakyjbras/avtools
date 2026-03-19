from __future__ import annotations

import json

import click
import pytest

import avtools.main as main


class _Resp:
    def __init__(self, status: int, text: str, payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_get_token_happy_path_prints_token(monkeypatch):
    def fake_post(url, data, timeout):
        assert data["grant_type"] == "client_credentials"
        return _Resp(200, "ok", {"access_token": "TKN"})

    monkeypatch.setattr(main.requests, "post", fake_post)

    from click.testing import CliRunner

    runner = CliRunner()
    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            "https://example/token",
            "--client-id",
            "id",
            "--client-secret",
            "sec",
        ],
    )
    assert res.exit_code == 0
    assert "TKN" in res.output


def test_get_token_http_error_becomes_click_exception(monkeypatch):
    def fake_post(url, data, timeout):
        return _Resp(400, "bad", {"error": "no"})

    monkeypatch.setattr(main.requests, "post", fake_post)

    from click.testing import CliRunner

    runner = CliRunner()
    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            "https://example/token",
            "--client-id",
            "id",
            "--client-secret",
            "sec",
        ],
    )
    assert res.exit_code != 0
    assert "Token request failed" in res.output
