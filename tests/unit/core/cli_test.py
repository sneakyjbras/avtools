from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

import avtools.main as main


class DummyAVTools:
    """
    Test double for AVTools. Tracks instantiations and method calls and can be
    configured to raise from specific methods to exercise CLI error handling.
    """

    instances: list[DummyAVTools] = []
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    # Flags to simulate failures in underlying AVTools calls
    raise_run_eam: bool = False
    raise_run_landb: bool = False
    raise_run_influx_snmp: bool = False

    # "Global" state guard (for the 'no unintended side effects' check)
    global_state: dict[str, Any] = {"sentinel": "unchanged"}

    def __init__(self, dbod_url: str, logs: bool = False) -> None:
        self.dbod_url = dbod_url
        self.logs = logs
        type(self).instances.append(self)

    # ---- AVTools interface methods ----

    def run_eam(self, username: str, password: str) -> None:
        type(self).calls.append(("run_eam", (username, password), {}))
        if type(self).raise_run_eam:
            raise RuntimeError("run_eam boom")

    def run_landb(
        self,
        client_id: str,
        client_secret: str,
        audience: str,
        max_workers: int = 8,
    ) -> None:
        type(self).calls.append(
            ("run_landb", (client_id, client_secret, audience, max_workers), {})
        )
        if type(self).raise_run_landb:
            raise RuntimeError("run_landb boom")

    def run_influx_snmp(
        self,
        influx_host: str,
        influx_port: int,
        influx_user: str,
        influx_password: str,
        influx_db: str,
        max_workers: int = 8,
    ) -> None:
        type(self).calls.append(
            (
                "run_influx_snmp",
                (
                    influx_host,
                    influx_port,
                    influx_user,
                    influx_password,
                    influx_db,
                    max_workers,
                ),
                {},
            )
        )
        if type(self).raise_run_influx_snmp:
            raise RuntimeError("run_influx_snmp boom")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_avtools(monkeypatch) -> type[DummyAVTools]:
    """
    Patch avtools.main.AVTools with our DummyAVTools for all CLI tests and
    reset its static state between tests.
    """
    DummyAVTools.instances.clear()
    DummyAVTools.calls.clear()
    DummyAVTools.raise_run_eam = False
    DummyAVTools.raise_run_landb = False
    DummyAVTools.raise_run_influx_snmp = False
    DummyAVTools.global_state = {"sentinel": "unchanged"}

    monkeypatch.setattr(main, "AVTools", DummyAVTools)
    return DummyAVTools


# ---------------------------------------------------------------------------
# -- Help coverage / smoke tests -------------------------------------------
# ---------------------------------------------------------------------------


def test_root_help_and_subcommand_help(
    runner: CliRunner, patched_avtools: type[DummyAVTools]
) -> None:
    # Root --help
    result = runner.invoke(main.cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output
    # Expect subcommands listed in root help
    assert "run-eam" in result.output
    assert "run-landb" in result.output
    assert "run-influx-snmp" in result.output

    # Subcommand --help (requires dbod-url if the group option is required)
    for cmd in ("run-eam", "run-landb", "run-influx-snmp"):
        result = runner.invoke(
            main.cli,
            ["--dbod-url", "postgres://dummy", cmd, "--help"],
        )
        assert result.exit_code == 0
        assert "Usage" in result.output


# ---------------------------------------------------------------------------
# -- Happy path command execution ------------------------------------------
# ---------------------------------------------------------------------------


def test_run_eam_happy_path_executes_and_invokes_avtools(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    result = runner.invoke(
        main.cli,
        [
            "--dbod-url",
            "postgres://dummy-eam",
            "run-eam",
            "--username",
            "eam-user",
            "--password",
            "eam-pass",
        ],
    )

    # Command executes successfully (exit code, no crash)
    assert result.exit_code == 0
    assert result.exception is None

    # AVTools instantiated exactly once
    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://dummy-eam"

    # AVTools methods correctly invoked, arguments forwarded properly
    assert patched_avtools.calls == [("run_eam", ("eam-user", "eam-pass"), {})]

    # No other AVTools methods called
    names = {name for (name, _, _) in patched_avtools.calls}
    assert names == {"run_eam"}

    # Global state untouched
    assert patched_avtools.global_state == {"sentinel": "unchanged"}


def test_run_landb_happy_path_uses_default_workers_and_invokes_avtools(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    result = runner.invoke(
        main.cli,
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

    assert result.exit_code == 0
    assert result.exception is None

    # Exactly one AVTools instantiation for this invocation
    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://dummy-landb"

    # Args forwarded properly and default max_workers applied (8)
    assert len(patched_avtools.calls) == 1
    name, args, kwargs = patched_avtools.calls[0]
    assert name == "run_landb"
    assert args[:3] == ("cid", "secret", "aud")
    assert args[3] == 8  # default workers
    assert kwargs == {}

    # No unintended side effects on global state
    assert patched_avtools.global_state == {"sentinel": "unchanged"}


def test_run_influx_snmp_happy_path_uses_default_workers_and_invokes_avtools(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    result = runner.invoke(
        main.cli,
        [
            "--dbod-url",
            "postgres://dummy-snmp",
            "run-influx-snmp",
            "--influx-host",
            "influx.local",
            "--influx-port",
            "8086",
            "--influx-user",
            "user",
            "--influx-password",
            "pass",
            "--influx-db",
            "av_metrics",
        ],
    )

    assert result.exit_code == 0
    assert result.exception is None

    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://dummy-snmp"

    assert len(patched_avtools.calls) == 1
    name, args, kwargs = patched_avtools.calls[0]
    assert name == "run_influx_snmp"
    # Arguments forwarded correctly
    assert args[0:5] == (
        "influx.local",
        8086,
        "user",
        "pass",
        "av_metrics",
    )
    # Default workers applied
    assert args[5] == 8
    assert kwargs == {}

    # Again, no unintended mutation of "global" state
    assert patched_avtools.global_state == {"sentinel": "unchanged"}


# ---------------------------------------------------------------------------
# -- Missing arguments / Click validation ----------------------------------
# ---------------------------------------------------------------------------


def test_run_eam_missing_password_causes_click_error(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    # Missing --password should be caught by Click, not by us
    result = runner.invoke(
        main.cli,
        [
            "--dbod-url",
            "postgres://dummy",
            "run-eam",
            "--username",
            "user-only",
        ],
    )

    assert result.exit_code != 0
    assert "Missing option" in result.output
    # AVTools must not be called at all when args are invalid
    assert patched_avtools.calls == []


# ---------------------------------------------------------------------------
# -- Exception handling in CLI layer ---------------------------------------
# ---------------------------------------------------------------------------


def test_run_eam_exception_is_handled_by_cli(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    # Configure the dummy to raise when run_eam is called
    patched_avtools.raise_run_eam = True

    result = runner.invoke(
        main.cli,
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

    # Exceptions handled properly in CLI layer:
    # - non-zero exit
    # - no uncaught traceback
    # - runner did not capture an unhandled exception
    assert result.exit_code != 0
    assert result.exception is None
    assert "Traceback" not in result.output

    # We can be lenient on the exact message, just ensure something is printed
    assert result.output.strip() != ""


def test_run_landb_exception_is_handled_by_cli(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    patched_avtools.raise_run_landb = True

    result = runner.invoke(
        main.cli,
        [
            "--dbod-url",
            "postgres://dummy",
            "run-landb",
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
            "--audience",
            "aud",
        ],
    )

    assert result.exit_code != 0
    assert result.exception is None
    assert "Traceback" not in result.output
    assert result.output.strip() != ""


# ---------------------------------------------------------------------------
# -- Environment variable overrides ----------------------------------------
# ---------------------------------------------------------------------------


def test_dbod_url_can_be_supplied_via_envvar(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    Environment variable overrides (if supported):

    Assumes the cli group uses something like:
        @click.option("--dbod-url", envvar="AVTOOLS_DBOD_URL", required=True)
    Adjust env var name here if your implementation differs.
    """
    result = runner.invoke(
        main.cli,
        [
            "run-eam",
            "--username",
            "env-user",
            "--password",
            "env-pass",
        ],
        env={"AVTOOLS_DBOD_URL": "postgres://from-env"},
    )

    assert result.exit_code == 0
    assert result.exception is None

    # AVTools instantiated once with DBOD URL taken from env var
    assert len(patched_avtools.instances) == 1
    inst = patched_avtools.instances[0]
    assert inst.dbod_url == "postgres://from-env"

    # And run_eam was called with expected credentials
    assert patched_avtools.calls == [("run_eam", ("env-user", "env-pass"), {})]


# ---------------------------------------------------------------------------
# -- No unintended side effects / multiple calls ---------------------------
# ---------------------------------------------------------------------------


def test_multiple_cli_calls_do_not_share_instances_or_mutate_globals(
    runner: CliRunner,
    patched_avtools: type[DummyAVTools],
) -> None:
    """
    Performance / multiple calls:
    - Each CLI invocation creates a fresh AVTools instance.
    - Calls do not mutate DummyAVTools.global_state.
    """
    # First call: run-eam
    result1 = runner.invoke(
        main.cli,
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
    # Second independent call: run-landb
    patched_avtools.calls.clear()
    result2 = runner.invoke(
        main.cli,
        [
            "--dbod-url",
            "postgres://second",
            "run-landb",
            "--client-id",
            "cid2",
            "--client-secret",
            "secret2",
            "--audience",
            "aud2",
        ],
    )

    assert result1.exit_code == 0
    assert result2.exit_code == 0
    assert result1.exception is None
    assert result2.exception is None

    # We should have two distinct AVTools instances overall
    assert len(patched_avtools.instances) == 2
    urls = {inst.dbod_url for inst in patched_avtools.instances}
    assert urls == {"postgres://first", "postgres://second"}

    # Global state still has the sentinel value
    assert patched_avtools.global_state == {"sentinel": "unchanged"}
