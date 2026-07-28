"""CLI wiring tests for the snmp-timeseries --priority option.

Verifies:
- Default is "all".
- Each valid choice (all/critical/high/medium/low) is accepted and threaded to
  AVTools.run_snmp_timeseries(priority=...).
- The AVTOOLS_PRIORITY envvar feeds the option.
- An invalid choice is rejected by Click (non-zero exit, no AVTools call).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import avtools.main as main


class DummyAVTools:
    """Records the priority passed through to run_snmp_timeseries."""

    calls: list[dict[str, Any]] = []

    def __init__(self, dbod_url: str, logs: bool = False) -> None:
        self.dbod_url = dbod_url

    def run_snmp_timeseries(self, *, priority: str = "all", **kwargs: Any) -> None:
        type(self).calls.append({"priority": priority, **kwargs})


@pytest.fixture(autouse=True)
def _patch_avtools(monkeypatch: Any) -> None:
    DummyAVTools.calls.clear()
    monkeypatch.setattr(main, "AVTools", DummyAVTools, raising=True)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    # Point logging at a writable path so the group callback's configure_logging
    # never touches the default /var/log path (keeps the test hermetic).
    base = {
        "DATABASE_URL": "postgres://env-db",
        "MONIT_TENANT": "tenant",
        "MONIT_PASSWORD": "pass",
        "AVTOOLS_LOG_FILE": str(tmp_path / "avtools.jsonl"),
    }
    base.update(extra)
    return base


def test_priority_defaults_to_all(tmp_path: Path) -> None:
    res = CliRunner().invoke(main.cli, ["snmp-timeseries"], env=_env(tmp_path))
    assert res.exit_code == 0, res.output
    assert len(DummyAVTools.calls) == 1
    assert DummyAVTools.calls[0]["priority"] == "all"


@pytest.mark.parametrize("choice", ["all", "critical", "high", "medium", "low"])
def test_priority_accepts_each_valid_choice(tmp_path: Path, choice: str) -> None:
    res = CliRunner().invoke(
        main.cli, ["snmp-timeseries", "--priority", choice], env=_env(tmp_path)
    )
    assert res.exit_code == 0, res.output
    assert len(DummyAVTools.calls) == 1
    assert DummyAVTools.calls[0]["priority"] == choice


def test_priority_reads_the_envvar(tmp_path: Path) -> None:
    res = CliRunner().invoke(
        main.cli, ["snmp-timeseries"], env=_env(tmp_path, AVTOOLS_PRIORITY="medium")
    )
    assert res.exit_code == 0, res.output
    assert DummyAVTools.calls[0]["priority"] == "medium"


def test_priority_rejects_invalid_choice(tmp_path: Path) -> None:
    res = CliRunner().invoke(
        main.cli, ["snmp-timeseries", "--priority", "urgent"], env=_env(tmp_path)
    )
    assert res.exit_code != 0
    assert "urgent" in res.output
    assert DummyAVTools.calls == []  # command body never ran
