from __future__ import annotations

from typing import Any

import pytest

from avtools.exception.errors import InfluxError


class DummyLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.exceptions: list[str] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append(event)

    def warning(self, event: str, **kwargs: Any) -> None:
        pass

    def error(self, event: str, **kwargs: Any) -> None:
        pass

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exceptions.append(event)


def test_publish_snmp_no_points_short_circuits() -> None:
    from avtools.core.av_tools import AVTools

    av = object.__new__(AVTools)
    av.logger = DummyLogger()

    av._publish_snmp([], "h", 443, "u", "p", "db")
    assert "No metrics collected; skipping write" in av.logger.infos


def test_publish_snmp_constructs_influx_client_with_ssl(monkeypatch: Any) -> None:
    import avtools.core.av_tools as core
    from avtools.core.av_tools import AVTools

    created: dict[str, Any] = {}

    class FakeInflux:
        def __init__(self, **kwargs: Any) -> None:
            created.update(kwargs)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            created["points"] = points

    monkeypatch.setattr(core, "InfluxClient", FakeInflux, raising=True)

    av = object.__new__(AVTools)
    av.logger = DummyLogger()

    pts = [{"measurement": "m", "tags": {}, "fields": {"x": 1}}]
    av._publish_snmp(pts, "host", 8086, "user", "pass", "db")

    assert created["host"] == "host"
    assert created["port"] == 8086
    assert created["username"] == "user"
    assert created["password"] == "pass"
    assert created["database"] == "db"
    assert created["ssl"] is True
    assert created["verify_ssl"] is True
    assert created["points"] == pts
    assert "influx_write_ok" in av.logger.infos


def test_publish_snmp_logs_and_raises_on_influx_error(monkeypatch: Any) -> None:
    import avtools.core.av_tools as core
    from avtools.core.av_tools import AVTools

    class FakeInflux:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def write_points(self, points: list[dict[str, Any]]) -> None:
            raise InfluxError("nope")

    monkeypatch.setattr(core, "InfluxClient", FakeInflux, raising=True)

    av = object.__new__(AVTools)
    av.logger = DummyLogger()

    with pytest.raises(InfluxError):
        av._publish_snmp(
            [{"measurement": "m", "tags": {}, "fields": {"x": 1}}],
            "h",
            1,
            "u",
            "p",
            "db",
        )

    assert "influx_write_failed" in av.logger.exceptions
