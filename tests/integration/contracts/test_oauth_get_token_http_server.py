from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from click.testing import CliRunner

import avtools.main as main


class _TokenHandler(BaseHTTPRequestHandler):
    # These are set by the fixture.
    response_status: int = 200
    response_payload: Any = {"access_token": "TOK"}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else ""

        # Basic contract: must send form-encoded client_credentials.
        # We keep it loose here (not a full parser), but enforce key presence.
        assert "grant_type=client_credentials" in body
        assert "client_id=" in body
        assert "client_secret=" in body

        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        if isinstance(self.response_payload, str):
            self.wfile.write(self.response_payload.encode("utf-8"))
        else:
            self.wfile.write(json.dumps(self.response_payload).encode("utf-8"))

    def log_message(self, fmt: str, *args: Any) -> None:
        # Silence server logs in pytest output.
        return


@pytest.fixture()
def token_server() -> Any:
    server = HTTPServer(("127.0.0.1", 0), _TokenHandler)
    host, port = server.server_address
    url = f"http://{host}:{port}/token"

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        yield server, url
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def test_get_token_happy_path_real_http(token_server: Any) -> None:
    server, url = token_server
    server.RequestHandlerClass.response_status = 200
    server.RequestHandlerClass.response_payload = {"access_token": "TOK_REAL"}

    runner = CliRunner()
    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            url,
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
        ],
    )

    assert res.exit_code == 0
    assert res.output.strip() == "TOK_REAL"


def test_get_token_http_error_real_http(token_server: Any) -> None:
    server, url = token_server
    server.RequestHandlerClass.response_status = 401
    server.RequestHandlerClass.response_payload = {"error": "unauthorized"}

    runner = CliRunner()
    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            url,
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
        ],
    )

    assert res.exit_code != 0
    assert "Token request failed" in res.output
    assert "401" in res.output


def test_get_token_missing_access_token_real_http(token_server: Any) -> None:
    server, url = token_server
    server.RequestHandlerClass.response_status = 200
    server.RequestHandlerClass.response_payload = {"foo": "bar"}

    runner = CliRunner()
    res = runner.invoke(
        main.get_token,
        [
            "--token-url",
            url,
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
        ],
    )

    assert res.exit_code != 0
    assert "Missing access_token" in res.output
