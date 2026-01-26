from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from avtools.exception.errors import PostgresError


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.exceptions: list[str] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        pass

    def error(self, event: str, **kwargs: Any) -> None:
        pass

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exceptions.append(event)


@dataclass
class Dev:
    ip: str | None


class DummyDB:
    def __init__(self, *, devices: list[Dev] | Exception) -> None:
        self._devices = devices

    def get_all_landb_devices(self) -> list[Dev]:
        if isinstance(self._devices, Exception):
            raise self._devices
        return list(self._devices)


def _make_avtools(db: DummyDB) -> Any:
    from avtools.core.av_tools import AVTools

    av = object.__new__(AVTools)
    av.logger = DummyLogger()
    av.dbod_helper = db
    av.logs = False
    av._landb_initialized = True
    return av


def _end_status(av: Any) -> str:
    ends = [kw for (ev, kw) in av.logger.infos if ev == "avtools_run_influx_snmp_end"]
    assert ends
    return str(ends[-1].get("status"))


def test_run_influx_snmp_load_devices_postgres_error() -> None:
    db = DummyDB(devices=PostgresError("db down"))
    av = _make_avtools(db)

    av.run_influx_snmp(
        influx_host="h",
        influx_port=8086,
        influx_user="u",
        influx_password="p",
        influx_db="db",
        max_workers=1,
    )

    assert "snmp_load_landb_devices_postgres_error" in av.logger.exceptions
    assert _end_status(av) == "failed_load_landb_devices_postgres_error"


def test_run_influx_snmp_filters_devices_without_ip(monkeypatch: Any) -> None:
    import avtools.core.av_tools as core
    from avtools.core.av_tools import AVTools

    db = DummyDB(devices=[Dev(ip=None), Dev(ip="192.0.2.5")])
    av = _make_avtools(db)

    seen: dict[str, Any] = {}

    async def fake_get_snmp_points(
        self: AVTools, devices: list[Any], max_workers: int
    ) -> list[dict[str, Any]]:
        seen["devices"] = list(devices)
        return []  # triggers skipped_no_snmp_points

    av._get_snmp_points = fake_get_snmp_points.__get__(av, AVTools)

    # avoid writing in this test
    av._publish_snmp = lambda *a, **k: None  # type: ignore[assignment]

    av.run_influx_snmp("h", 1, "u", "p", "db", max_workers=1)

    assert seen["devices"] == [Dev(ip="192.0.2.5")]
