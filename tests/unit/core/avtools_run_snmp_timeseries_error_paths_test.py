from __future__ import annotations

from dataclasses import dataclass
from typing import Any


import avtools.core.av_tools as av_mod
from avtools.core.av_tools import AVTools
from avtools.exception.errors import PostgresError
from avtools.timeseries.otlp_publisher import OTLPPublishError


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.exceptions: list[str] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, dict(kwargs)))

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exceptions.append(event)


@dataclass
class DummyTarget:
    ip: str
    equipment_no: str


def _make_av() -> AVTools:
    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av.dbod_url = "postgres://dummy"
    return av


def _end_status(av: AVTools) -> str:
    ends = [kw for (ev, kw) in av.logger.infos if ev == "avtools_run_snmp_timeseries_end"]
    assert ends
    return str(ends[-1]["status"])


def test_run_snmp_timeseries_load_targets_postgres_error(monkeypatch: Any) -> None:
    av = _make_av()

    monkeypatch.setattr(
        AVTools,
        "_load_timeseries_targets_from_landb_ipaddresses",
        lambda self: (_ for _ in ()).throw(PostgresError("db")),
    )

    av.run_snmp_timeseries(
        otlp_endpoint="x:1",
        monit_tenant="t",
        monit_password="p",
    )

    assert _end_status(av).startswith("failed_load_landb_ipaddresses_postgres_error")


def test_run_snmp_timeseries_load_targets_unexpected(monkeypatch: Any) -> None:
    av = _make_av()
    monkeypatch.setattr(
        AVTools,
        "_load_timeseries_targets_from_landb_ipaddresses",
        lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    av.run_snmp_timeseries(
        otlp_endpoint="x:1",
        monit_tenant="t",
        monit_password="p",
    )
    assert _end_status(av) == "failed_load_landb_ipaddresses"


def test_run_snmp_timeseries_snmp_collection_failure(monkeypatch: Any) -> None:
    av = _make_av()
    monkeypatch.setattr(
        AVTools,
        "_load_timeseries_targets_from_landb_ipaddresses",
        lambda self: [DummyTarget(ip="1.2.3.4", equipment_no="EQ")],
    )

    async def boom(self, devices, max_workers):
        raise RuntimeError("snmp")

    monkeypatch.setattr(AVTools, "_get_snmp_raw", boom)

    av.run_snmp_timeseries(
        otlp_endpoint="x:1",
        monit_tenant="t",
        monit_password="p",
        max_workers=1,
    )

    assert _end_status(av) == "failed_snmp_collection"
    assert "snmp_collection_failed" in av.logger.exceptions


def test_run_snmp_timeseries_otlp_publish_error_marks_completed_with_errors(
    monkeypatch: Any,
) -> None:
    av = _make_av()
    monkeypatch.setattr(
        AVTools,
        "_load_timeseries_targets_from_landb_ipaddresses",
        lambda self: [DummyTarget(ip="1.2.3.4", equipment_no="EQ")],
    )

    async def ok(self, devices, max_workers):
        return ([], [], [], [])

    monkeypatch.setattr(AVTools, "_get_snmp_raw", ok)

    class BoomPublisher:
        def __init__(self, **kwargs: Any) -> None:
            raise OTLPPublishError("boom")

    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", BoomPublisher)
    monkeypatch.setattr(av_mod, "PostgresMonitoringClient", lambda url: object())
    monkeypatch.setattr(av_mod, "SNMPObserverRouter", lambda **kw: object())

    av.run_snmp_timeseries(
        otlp_endpoint="x:1",
        monit_tenant="t",
        monit_password="p",
    )

    assert _end_status(av) == "completed_with_errors"


def test_run_snmp_timeseries_unexpected_publish_error_is_logged(
    monkeypatch: Any,
) -> None:
    av = _make_av()
    monkeypatch.setattr(
        AVTools,
        "_load_timeseries_targets_from_landb_ipaddresses",
        lambda self: [DummyTarget(ip="1.2.3.4", equipment_no="EQ")],
    )

    async def ok(self, devices, max_workers):
        return ([], [], [], [])

    monkeypatch.setattr(AVTools, "_get_snmp_raw", ok)

    monkeypatch.setattr(av_mod, "OTLPMetricsPublisher", lambda **kw: object())
    monkeypatch.setattr(av_mod, "PostgresMonitoringClient", lambda url: object())

    class BoomRouter:
        def __init__(self, **kw: Any) -> None:
            pass

        def process(self, **kw: Any):
            raise RuntimeError("router")

    monkeypatch.setattr(av_mod, "SNMPObserverRouter", BoomRouter)

    av.run_snmp_timeseries(
        otlp_endpoint="x:1",
        monit_tenant="t",
        monit_password="p",
    )

    assert _end_status(av) == "completed_with_errors"
    assert "timeseries_publish_failed_unexpected" in av.logger.exceptions
