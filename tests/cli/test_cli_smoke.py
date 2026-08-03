"""CLI smoke tests for avtools.main Click commands."""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

import avtools.main as main
from avtools.exception.errors import NoRecordsFound


class DummyAVTools:
    """Test double for AVTools used by the CLI.

    Records constructor args and method calls so we can assert Click wiring
    matches avtools/main.py.
    """

    instances: list[DummyAVTools] = []
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    # Toggle raises for specific methods
    raise_no_records_run_eam: bool = False
    raise_no_records_run_landb: bool = False

    def __init__(self, dbod_url: str, logs: bool = False) -> None:
        self.dbod_url = dbod_url
        self.logs = logs
        type(self).instances.append(self)

    def run_eam(self, *, username: str, password: str) -> None:
        type(self).calls.append(("run_eam", (), {"username": username, "password": password}))
        if type(self).raise_no_records_run_eam:
            raise NoRecordsFound("no EAM records")

    def run_landb(
        self,
        *,
        client_id: str,
        client_secret: str,
        audience: str,
    ) -> None:
        type(self).calls.append(
            (
                "run_landb",
                (),
                {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "audience": audience,
                },
            )
        )
        if type(self).raise_no_records_run_landb:
            raise NoRecordsFound("no LanDB records")

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
        otlp_protocol: str = "http",
        otlp_encoding: str = "protobuf",
        submitter_environment: str = "prod",
        submitter_hostgroup: str = "itdcim/av",
        availability_zone: str = "cern-geneva-b",
        shard_index: int = 0,
        shard_total: int = 1,
        priority: str = "all",
    ) -> None:
        type(self).calls.append(
            (
                "run_snmp_timeseries",
                (),
                {
                    "otlp_endpoint": otlp_endpoint,
                    "monit_tenant": monit_tenant,
                    "monit_password": monit_password,
                    "max_workers": max_workers,
                    "service_name": service_name,
                    "otlp_ca_file": otlp_ca_file,
                    "otlp_insecure": otlp_insecure,
                    "otlp_protocol": otlp_protocol,
                    "otlp_encoding": otlp_encoding,
                    "submitter_environment": submitter_environment,
                    "submitter_hostgroup": submitter_hostgroup,
                    "availability_zone": availability_zone,
                    "shard_index": shard_index,
                    "shard_total": shard_total,
                    "priority": priority,
                },
            )
        )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_avtools(monkeypatch) -> type[DummyAVTools]:
    DummyAVTools.instances.clear()
    DummyAVTools.calls.clear()
    DummyAVTools.raise_no_records_run_eam = False
    DummyAVTools.raise_no_records_run_landb = False

    # Patch the symbol used by main.py
    monkeypatch.setattr(main, "AVTools", DummyAVTools)
    return DummyAVTools


def test_root_help_and_subcommand_help(
    runner: CliRunner, patched_avtools: type[DummyAVTools]
) -> None:
    """Root and subcommand help should expose current command names."""
    cli = main.cli

    res = runner.invoke(cli, ["--help"])
    assert res.exit_code == 0
    assert "Usage:" in res.output

    assert "run-eam" in res.output
    assert "run-landb" in res.output
    assert "snmp-timeseries" in res.output

    # run-eam
    res = runner.invoke(cli, ["--dbod-url", "postgres://dummy", "run-eam", "--help"])
    assert res.exit_code == 0

    # run-landb
    res = runner.invoke(cli, ["--dbod-url", "postgres://dummy", "run-landb", "--help"])
    assert res.exit_code == 0

    # snmp-timeseries
    res = runner.invoke(cli, ["--dbod-url", "postgres://dummy", "snmp-timeseries", "--help"])
    assert res.exit_code == 0


def test_run_landb_happy_path_uses_default_workers_and_invokes_avtools(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """run-landb should pass credentials and default threads (8)."""
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-landb",
            "run-landb",
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
            "--audience",
            "aud",
        ],
    )

    assert res.exit_code == 0
    assert res.exception is None

    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://dummy-landb"

    assert len(patched_avtools.calls) == 1
    name, _args, kwargs = patched_avtools.calls[0]
    assert name == "run_landb"
    assert kwargs == {
        "client_id": "cid",
        "client_secret": "secret",
        "audience": "aud",
    }


def test_run_snmp_timeseries_happy_path_uses_default_workers_and_invokes_avtools(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """snmp-timeseries should call AVTools.run_snmp_timeseries with defaults."""
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-snmp",
            "snmp-timeseries",
            "--tenant",
            "monit_tenant",
            "--password",
            "monit_pass",
        ],
    )

    assert res.exit_code == 0
    assert res.exception is None

    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://dummy-snmp"

    assert len(patched_avtools.calls) == 1
    name, _args, kwargs = patched_avtools.calls[0]
    assert name == "run_snmp_timeseries"

    assert kwargs["max_workers"] == 8
    assert kwargs["monit_tenant"] == "monit_tenant"
    assert kwargs["monit_password"] == "monit_pass"
    # Default endpoint moved to the TLS OTLP/HTTP URL (the gRPC ports have no
    # TLS listener at all — see avtools.otlp.endpoints).
    assert kwargs["otlp_endpoint"] == "https://monit-otlp.cern.ch:4319/v1/metrics"
    assert kwargs["service_name"] == "avtools"
    assert kwargs["otlp_ca_file"] is None
    assert kwargs["otlp_insecure"] is False
    assert kwargs["otlp_protocol"] == "http"
    assert kwargs["otlp_encoding"] == "protobuf"


def test_run_snmp_timeseries_invalid_threads_type_causes_click_error(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """Invalid --threads should be a Click parse error."""
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-snmp",
            "snmp-timeseries",
            "--threads",
            "NaN",
            "--tenant",
            "monit_tenant",
            "--password",
            "monit_pass",
        ],
    )

    assert res.exit_code != 0
    assert patched_avtools.instances == []


def test_run_eam_happy_path_invokes_avtools(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """run-eam should pass credentials and invoke AVTools.run_eam."""
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-eam",
            "run-eam",
            "--username",
            "u",
            "--password",
            "p",
        ],
    )

    assert res.exit_code == 0
    assert res.exception is None

    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://dummy-eam"

    assert len(patched_avtools.calls) == 1
    name, _args, kwargs = patched_avtools.calls[0]
    assert name == "run_eam"
    assert kwargs == {"username": "u", "password": "p"}


def test_run_eam_no_records_is_reported_as_click_error(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """NoRecordsFound raised by AVTools should become a ClickException."""
    patched_avtools.raise_no_records_run_eam = True
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-eam",
            "run-eam",
            "--username",
            "u",
            "--password",
            "p",
        ],
    )

    assert res.exit_code != 0
    assert "no EAM records" in res.output


def test_run_landb_no_records_is_reported_as_click_error(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """NoRecordsFound raised by AVTools should become a ClickException."""
    patched_avtools.raise_no_records_run_landb = True
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-landb",
            "run-landb",
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
            "--audience",
            "aud",
        ],
    )

    assert res.exit_code != 0
    assert "no LanDB records" in res.output


def test_run_snmp_timeseries_passes_all_cli_flags_through(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """All CLI flags should map 1:1 to AVTools.run_snmp_timeseries kwargs."""
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-snmp",
            "snmp-timeseries",
            "--threads",
            "12",
            "--otlp-endpoint",
            "example:4317",
            "--tenant",
            "t",
            "--password",
            "p",
            "--service-name",
            "svc",
            "--otlp-ca-file",
            "/tmp/ca.pem",
            "--otlp-insecure",
            "--shard-index",
            "2",
            "--shard-total",
            "8",
        ],
    )

    assert res.exit_code == 0
    assert res.exception is None

    assert len(patched_avtools.calls) == 1
    name, _args, kwargs = patched_avtools.calls[0]
    assert name == "run_snmp_timeseries"
    assert kwargs == {
        "otlp_endpoint": "example:4317",
        "monit_tenant": "t",
        "monit_password": "p",
        "max_workers": 12,
        "service_name": "svc",
        "otlp_ca_file": "/tmp/ca.pem",
        "otlp_insecure": True,
        "otlp_protocol": "http",
        "otlp_encoding": "protobuf",
        "submitter_environment": "prod",
        "submitter_hostgroup": "itdcim/av",
        "availability_zone": "cern-geneva-b",
        "shard_index": 2,
        "shard_total": 8,
        "priority": "all",
    }
