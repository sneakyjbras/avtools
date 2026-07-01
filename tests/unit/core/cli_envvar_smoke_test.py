from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

import avtools.main as main


class DummyAVTools:
    """Test double for Click envvar wiring.

    This keeps the tests fast and makes sure Click options/envvars are
    mapped to the correct AVTools method calls.
    """

    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, dbod_url: str, logs: bool = False) -> None:
        self.dbod_url = dbod_url
        self.logs = logs

    def run_eam(self, *, username: str, password: str) -> None:
        type(self).calls.append(
            (
                "run_eam",
                {"dbod_url": self.dbod_url, "username": username, "password": password},
            )
        )

    def run_landb(self, *, client_id: str, client_secret: str, audience: str) -> None:
        type(self).calls.append(
            (
                "run_landb",
                {
                    "dbod_url": self.dbod_url,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "audience": audience,
                },
            )
        )

    def run_snmp_timeseries(
        self,
        *,
        otlp_endpoint: str,
        monit_tenant: str,
        monit_password: str,
        max_workers: int = 8,
        service_name: str = "avtools",
        otlp_ca_file: str | None = None,
        otlp_insecure: bool = False,
        submitter_environment: str = "prod",
        submitter_hostgroup: str = "itdcim/av",
        availability_zone: str = "cern-geneva-b",
        shard_index: int = 0,
        shard_total: int = 1,
    ) -> None:
        type(self).calls.append(
            (
                "run_snmp_timeseries",
                {
                    "dbod_url": self.dbod_url,
                    "otlp_endpoint": otlp_endpoint,
                    "monit_tenant": monit_tenant,
                    "monit_password": monit_password,
                    "max_workers": max_workers,
                    "service_name": service_name,
                    "otlp_ca_file": otlp_ca_file,
                    "otlp_insecure": otlp_insecure,
                    "submitter_environment": submitter_environment,
                    "submitter_hostgroup": submitter_hostgroup,
                    "availability_zone": availability_zone,
                    "shard_index": shard_index,
                    "shard_total": shard_total,
                },
            )
        )


@pytest.fixture(autouse=True)
def _patch_avtools(monkeypatch: Any) -> None:
    DummyAVTools.calls.clear()
    monkeypatch.setattr(main, "AVTools", DummyAVTools, raising=True)


def test_run_eam_uses_envvars_for_db_and_credentials() -> None:
    runner = CliRunner()
    env = {
        "DATABASE_URL": "postgres://env-db",
        "MY_USERNAME": "env_user",
        "MY_PASSWORD": "env_pass",
    }

    res = runner.invoke(main.cli, ["run-eam"], env=env)
    assert res.exit_code == 0
    assert DummyAVTools.calls == [
        (
            "run_eam",
            {
                "dbod_url": "postgres://env-db",
                "username": "env_user",
                "password": "env_pass",
            },
        )
    ]


def test_run_landb_uses_envvars_for_db_and_oauth_creds() -> None:
    runner = CliRunner()
    env = {
        "DATABASE_URL": "postgres://env-db",
        "LANDB_CLIENT_ID": "cid",
        "LANDB_CLIENT_SECRET": "sec",
        "LANDB_AUDIENCE": "aud",
    }

    res = runner.invoke(main.cli, ["run-landb"], env=env)
    assert res.exit_code == 0
    assert DummyAVTools.calls == [
        (
            "run_landb",
            {
                "dbod_url": "postgres://env-db",
                "client_id": "cid",
                "client_secret": "sec",
                "audience": "aud",
            },
        )
    ]


def test_snmp_timeseries_uses_envvars_for_required_and_optional_flags() -> None:
    runner = CliRunner()
    env = {
        "DATABASE_URL": "postgres://env-db",
        "MONIT_TENANT": "tenant",
        "MONIT_PASSWORD": "pass",
        "THREADS": "5",
        "MONIT_OTLP_ENDPOINT": "example:4317",
        "OTEL_SERVICE_NAME": "svc",
        "OTLP_CA_FILE": "/etc/ssl/certs/ca.pem",
        # click accepts true/false/1/0/etc for BOOL envvars
        "MONIT_OTLP_INSECURE": "true",
    }

    res = runner.invoke(main.cli, ["snmp-timeseries"], env=env)
    assert res.exit_code == 0

    assert DummyAVTools.calls == [
        (
            "run_snmp_timeseries",
            {
                "dbod_url": "postgres://env-db",
                "otlp_endpoint": "example:4317",
                "monit_tenant": "tenant",
                "monit_password": "pass",
                "max_workers": 5,
                "service_name": "svc",
                "otlp_ca_file": "/etc/ssl/certs/ca.pem",
                "otlp_insecure": True,
                "submitter_environment": "prod",
                "submitter_hostgroup": "itdcim/av",
                "availability_zone": "cern-geneva-b",
                "shard_index": 0,
                "shard_total": 1,
            },
        )
    ]


class DummyResp:
    def __init__(self, *, status_code: int = 200, text: str = "", payload: Any = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self) -> Any:
        return self._payload


def test_get_token_uses_envvars_and_includes_optional_fields(monkeypatch: Any) -> None:
    runner = CliRunner()

    def fake_post(url: str, data: dict[str, str], timeout: int):
        assert url == "https://token"
        # Optional fields should be present when set via envvar.
        assert data["audience"] == "aud"
        assert data["scope"] == "s"
        return DummyResp(payload={"access_token": "TOK"})

    monkeypatch.setattr(main.requests, "post", fake_post)

    env = {
        "OAUTH_TOKEN_URL": "https://token",
        "LANDB_CLIENT_ID": "cid",
        "LANDB_CLIENT_SECRET": "sec",
        "LANDB_AUDIENCE": "aud",
        "OAUTH_SCOPE": "s",
    }

    res = runner.invoke(main.get_token, [], env=env)
    assert res.exit_code == 0
    assert res.output.strip() == "TOK"


def test_get_token_omits_optional_fields_when_not_set(monkeypatch: Any) -> None:
    runner = CliRunner()

    def fake_post(url: str, data: dict[str, str], timeout: int):
        assert url == "https://token"
        assert "audience" not in data
        assert "scope" not in data
        return DummyResp(payload={"access_token": "TOK"})

    monkeypatch.setattr(main.requests, "post", fake_post)

    env = {
        "OAUTH_TOKEN_URL": "https://token",
        "LANDB_CLIENT_ID": "cid",
        "LANDB_CLIENT_SECRET": "sec",
    }

    res = runner.invoke(main.get_token, [], env=env)
    assert res.exit_code == 0
    assert res.output.strip() == "TOK"
