from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

import avtools.main as main
from avtools.exception.errors import NoRecordsFound


class DummyAVTools:
    """
    Test double for AVTools used by the CLI.

    Records constructor args and method calls so we can assert the Click wiring
    matches main.py.
    """

    instances: list[DummyAVTools] = []
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    # Toggle raises for specific methods
    raise_no_records_run_eam: bool = False
    raise_no_records_run_landb: bool = False

    global_state: dict[str, Any] = {"sentinel": "unchanged"}

    def __init__(self, dbod_url: str, logs: bool = False) -> None:
        self.dbod_url = dbod_url
        self.logs = logs
        type(self).instances.append(self)

    def run_eam(self, username: str, password: str) -> None:
        type(self).calls.append(("run_eam", (username, password), {}))
        if type(self).raise_no_records_run_eam:
            raise NoRecordsFound("no EAM records")

    def run_landb(
        self,
        *,
        client_id: str,
        client_secret: str,
        audience: str,
        max_workers: int,
    ) -> None:
        type(self).calls.append(
            (
                "run_landb",
                (),
                {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "audience": audience,
                    "max_workers": max_workers,
                },
            )
        )
        if type(self).raise_no_records_run_landb:
            raise NoRecordsFound("no LanDB records")

    def run_influx_snmp(
        self,
        *,
        influx_host: str,
        influx_port: int,
        influx_user: str,
        influx_password: str,
        influx_db: str,
        max_workers: int,
    ) -> None:
        type(self).calls.append(
            (
                "run_influx_snmp",
                (),
                {
                    "influx_host": influx_host,
                    "influx_port": influx_port,
                    "influx_user": influx_user,
                    "influx_password": influx_password,
                    "influx_db": influx_db,
                    "max_workers": max_workers,
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
    DummyAVTools.global_state = {"sentinel": "unchanged"}

    # Patch the symbol used by main.py
    monkeypatch.setattr(main, "AVTools", DummyAVTools)
    return DummyAVTools


def test_root_help_and_subcommand_help(
    runner: CliRunner, patched_avtools: type[DummyAVTools]
) -> None:
    """
    Root and subcommand help should expose current command names:
    - run-eam
    - run-landb
    - snmp-influx
    """
    cli = main.cli

    res = runner.invoke(cli, ["--help"])
    assert res.exit_code == 0
    assert "Usage:" in res.output

    assert "run-eam" in res.output
    assert "run-landb" in res.output
    assert "snmp-influx" in res.output

    # run-eam: only group-level --dbod-url exists/required
    res = runner.invoke(cli, ["--dbod-url", "postgres://dummy", "run-eam", "--help"])
    assert res.exit_code == 0
    assert "Usage:" in res.output

    # run-landb: group-level + command-level --dbod-url
    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-group",
            "run-landb",
            "--dbod-url",
            "postgres://dummy-cmd",
            "--help",
        ],
    )
    assert res.exit_code == 0
    assert "Usage:" in res.output

    # snmp-influx: group-level + command-level --dbod-url
    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy-group",
            "snmp-influx",
            "--dbod-url",
            "postgres://dummy-cmd",
            "--help",
        ],
    )
    assert res.exit_code == 0
    assert "Usage:" in res.output


def test_run_landb_happy_path_uses_default_workers_and_invokes_avtools(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    run-landb:
    - requires group --dbod-url AND command --dbod-url (as in main.py)
    - default --threads is 8
    - passes threads to AVTools.run_landb(max_workers=threads)
    - prints success message
    """
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://group-db",
            "run-landb",
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
            "--audience",
            "aud",
            "--dbod-url",
            "postgres://dummy-landb",
            # no --threads => default 8
        ],
    )

    assert res.exit_code == 0
    assert res.exception is None
    assert "LanDB CRUD operation completed successfully." in res.output

    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://dummy-landb"

    assert len(patched_avtools.calls) == 1
    name, args, kwargs = patched_avtools.calls[0]
    assert name == "run_landb"
    assert args == ()
    assert kwargs == {
        "client_id": "cid",
        "client_secret": "secret",
        "audience": "aud",
        "max_workers": 8,
    }


def test_run_influx_snmp_happy_path_uses_default_workers_and_invokes_avtools(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    snmp-influx:
    - requires group --dbod-url AND command --dbod-url
    - default --threads is 8
    - calls AVTools.run_influx_snmp(max_workers=threads)
    """
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://group-db",
            "snmp-influx",
            "--influx-host",
            "influx.local",
            "--influx-user",
            "user",
            "--influx-password",
            "pass",
            "--influx-db",
            "av_metrics",
            "--dbod-url",
            "postgres://dummy-snmp",
            # no --threads => default 8
            # no --influx-port => default 8086
        ],
    )

    assert res.exit_code == 0
    assert res.exception is None

    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://dummy-snmp"

    assert len(patched_avtools.calls) == 1
    name, args, kwargs = patched_avtools.calls[0]
    assert name == "run_influx_snmp"
    assert args == ()
    assert kwargs == {
        "influx_host": "influx.local",
        "influx_port": 8086,
        "influx_user": "user",
        "influx_password": "pass",
        "influx_db": "av_metrics",
        "max_workers": 8,
    }


def test_run_influx_snmp_invalid_port_type_causes_click_error(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    snmp-influx invalid --influx-port should be a Click parse error (exit_code != 0),
    and should not instantiate AVTools.
    """
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://group-db",
            "snmp-influx",
            "--influx-host",
            "influx.local",
            "--influx-port",
            "not-an-int",
            "--influx-user",
            "user",
            "--influx-password",
            "pass",
            "--influx-db",
            "av_metrics",
            "--dbod-url",
            "postgres://dummy-snmp",
        ],
    )

    assert res.exit_code != 0
    assert "Invalid value" in res.output or "invalid" in res.output.lower()

    assert patched_avtools.instances == []
    assert patched_avtools.calls == []


def test_run_eam_exception_is_handled_by_cli(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    main.py catches NoRecordsFound for run-eam and prints it (does not crash).
    """
    cli = main.cli
    patched_avtools.raise_no_records_run_eam = True

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://dummy",
            "run-eam",
            "--username",
            "u",
            "--password",
            "p",
        ],
    )

    assert res.exit_code == 0
    assert res.exception is None
    assert "Error: " in res.output
    assert "no EAM records" in res.output


def test_run_landb_exception_is_handled_by_cli(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    main.py catches NoRecordsFound for run-landb and prints it (does not crash).
    """
    cli = main.cli
    patched_avtools.raise_no_records_run_landb = True

    res = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://group-db",
            "run-landb",
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
            "--audience",
            "aud",
            "--dbod-url",
            "postgres://dummy-landb",
            # threads default
        ],
    )

    assert res.exit_code == 0
    assert res.exception is None
    assert "Error: " in res.output
    assert "no LanDB records" in res.output


def test_dbod_url_can_be_supplied_via_envvar(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    Root group --dbod-url uses envvar DATABASE_URL.
    For run-eam (no command-level dbod-url), envvar alone should satisfy the requirement.
    """
    cli = main.cli

    res = runner.invoke(
        cli,
        [
            "run-eam",
            "--username",
            "env-user",
            "--password",
            "env-pass",
        ],
        env={"DATABASE_URL": "postgres://from-env"},
    )

    assert res.exit_code == 0
    assert res.exception is None
    assert "EAM CRUD operation completed successfully." in res.output

    assert len(patched_avtools.instances) == 1
    assert patched_avtools.instances[0].dbod_url == "postgres://from-env"


def test_multiple_cli_calls_do_not_share_instances_or_mutate_globals(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    Separate invocations should create separate AVTools instances and not mutate global state.
    """
    cli = main.cli

    res1 = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://first",
            "run-eam",
            "--username",
            "user1",
            "--password",
            "pass1",
        ],
    )
    res2 = runner.invoke(
        cli,
        [
            "--dbod-url",
            "postgres://group-db",
            "run-landb",
            "--client-id",
            "cid2",
            "--client-secret",
            "secret2",
            "--audience",
            "aud2",
            "--dbod-url",
            "postgres://second",
        ],
    )

    assert res1.exit_code == 0
    assert res2.exit_code == 0
    assert res1.exception is None
    assert res2.exception is None

    assert len(patched_avtools.instances) == 2
    urls = {inst.dbod_url for inst in patched_avtools.instances}
    assert urls == {"postgres://first", "postgres://second"}

    assert patched_avtools.global_state == {"sentinel": "unchanged"}
