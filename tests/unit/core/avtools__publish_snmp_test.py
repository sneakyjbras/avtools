from __future__ import annotations

from typing import Any

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools


class DummyLogger:
    """
    Capture structlog-style event + kwargs.

    av_tools._publish_snmp() currently logs:
      - info("No metrics collected; skipping write")
      - info("influx_write_ok", points=<n>)
      - exception("influx_write_failed")
    """

    def __init__(self) -> None:
        self.info_events: list[tuple[str, dict[str, Any]]] = []
        self.exception_events: list[tuple[str, dict[str, Any]]] = []
        self.error_events: list[tuple[str, dict[str, Any]]] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.info_events.append((str(msg), dict(kwargs)))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exception_events.append((str(msg), dict(kwargs)))

    # Keep a generic error method in case it’s ever called
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.error_events.append((str(msg), dict(kwargs)))


def make_avtools_for_tests() -> tuple[AVTools, DummyLogger]:
    """Bypass __init__ and attach a dummy logger."""
    av = object.__new__(AVTools)
    logger = DummyLogger()
    av.logger = logger
    return av, logger


# ---------------------------------------------------------------------------
# Empty list behaviour
# ---------------------------------------------------------------------------


def test_publish_snmp_empty_points_skips_influx_write(monkeypatch):
    """If points is empty, InfluxClient must never be instantiated or called."""

    class DummyInfluxClient:
        instances: list[DummyInfluxClient] = []

        def __init__(
            self,
            host: str,
            port: int,
            username: str,
            password: str,
            database: str,
            ssl: bool,
            verify_ssl: bool,
        ) -> None:
            DummyInfluxClient.instances.append(self)
            self.writes: list[list[dict[str, Any]]] = []

        def write_points(self, points: list[dict[str, Any]]) -> None:
            self.writes.append(points)

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, logger = make_avtools_for_tests()

    av._publish_snmp(
        points=[],
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    # No InfluxClient created
    assert DummyInfluxClient.instances == []

    # Log should indicate skipping write (new message)
    assert any("skipping write" in event.lower() for event, _kw in logger.info_events)
    assert logger.exception_events == []


# ---------------------------------------------------------------------------
# Normal write behaviour
# ---------------------------------------------------------------------------


def test_publish_snmp_non_empty_points_calls_write_once(monkeypatch):
    """Non-empty list must produce exactly one write_points call and log influx_write_ok."""

    class DummyInfluxClient:
        instances: list[DummyInfluxClient] = []

        def __init__(
            self,
            host: str,
            port: int,
            username: str,
            password: str,
            database: str,
            ssl: bool,
            verify_ssl: bool,
        ) -> None:
            self.host = host
            self.port = port
            self.username = username
            self.password = password
            self.database = database
            self.ssl = ssl
            self.verify_ssl = verify_ssl
            self.writes: list[list[dict[str, Any]]] = []
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            self.writes.append(points)

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, logger = make_avtools_for_tests()

    points = [
        {"measurement": "ping", "tags": {"ip": "10.0.0.34"}, "fields": {"status": 1}},
        {"measurement": "probe", "tags": {"ip": "10.0.0.34"}, "fields": {"uptime": 42}},
        {
            "measurement": "query",
            "tags": {"ip": "10.0.0.34"},
            "fields": {"value": 2025},
        },
    ]

    av._publish_snmp(
        points=points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    # One client, one write call
    assert len(DummyInfluxClient.instances) == 1
    client = DummyInfluxClient.instances[0]
    assert len(client.writes) == 1

    # Same list object forwarded
    assert client.writes[0] is points

    # Logging: new event name + points count in kwargs
    assert any(event == "influx_write_ok" for event, _kw in logger.info_events)
    assert any(
        event == "influx_write_ok" and kw.get("points") == 3
        for event, kw in logger.info_events
    )

    # No exceptions logged
    assert logger.exception_events == []


def test_publish_snmp_passes_influx_connection_params(monkeypatch):
    """Ensure InfluxClient is constructed with the exact parameters."""

    class DummyInfluxClient:
        instances: list[DummyInfluxClient] = []

        def __init__(
            self,
            host: str,
            port: int,
            username: str,
            password: str,
            database: str,
            ssl: bool,
            verify_ssl: bool,
        ) -> None:
            self.host = host
            self.port = port
            self.username = username
            self.password = password
            self.database = database
            self.ssl = ssl
            self.verify_ssl = verify_ssl
            self.writes: list[list[dict[str, Any]]] = []
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            self.writes.append(points)

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, _ = make_avtools_for_tests()

    points = [
        {"measurement": "ping", "tags": {"ip": "10.0.0.38"}, "fields": {"status": 1}},
    ]

    av._publish_snmp(
        points=points,
        influx_host="influx-prod.cern.ch",
        influx_port=9999,
        influx_user="snmp_user",
        influx_password="secret",
        influx_db="avtools_snmp",
    )

    assert len(DummyInfluxClient.instances) == 1
    client = DummyInfluxClient.instances[0]
    assert client.host == "influx-prod.cern.ch"
    assert client.port == 9999
    assert client.username == "snmp_user"
    assert client.password == "secret"
    assert client.database == "avtools_snmp"
    assert client.ssl is True
    assert client.verify_ssl is True


# ---------------------------------------------------------------------------
# Error handling (updated to match new event names)
# ---------------------------------------------------------------------------


def test_publish_snmp_influx_write_error_is_caught(monkeypatch):
    """
    If write_points raises, the exception must be caught and logged via
    exception("influx_write_failed"), not propagated.
    """

    class DummyInfluxClient:
        instances: list[DummyInfluxClient] = []

        def __init__(
            self,
            host: str,
            port: int,
            username: str,
            password: str,
            database: str,
            ssl: bool,
            verify_ssl: bool,
        ) -> None:
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            raise RuntimeError("dummy influx failure")

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, logger = make_avtools_for_tests()

    points = [
        {"measurement": "ping", "tags": {"ip": "10.0.0.39"}, "fields": {"status": 1}},
    ]

    # Should not raise
    av._publish_snmp(
        points=points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    assert len(DummyInfluxClient.instances) == 1
    assert any(event == "influx_write_failed" for event, _kw in logger.exception_events)


def test_publish_snmp_malformed_points_still_handled_via_error(monkeypatch):
    """
    Malformed points => write error from Influx point of view.
    _publish_snmp must log influx_write_failed and not crash.
    """

    class DummyInfluxClient:
        instances: list[DummyInfluxClient] = []

        def __init__(
            self,
            host: str,
            port: int,
            username: str,
            password: str,
            database: str,
            ssl: bool,
            verify_ssl: bool,
        ) -> None:
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            for p in points:
                if "measurement" not in p:
                    raise ValueError("missing measurement")

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, logger = make_avtools_for_tests()

    malformed_points = [
        {"measurement": "ping", "tags": {"ip": "10.0.0.34"}, "fields": {"status": 1}},
        {"tags": {"ip": "10.0.0.38"}, "fields": {"status": 1}},  # missing measurement
    ]

    av._publish_snmp(
        points=malformed_points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    assert any(event == "influx_write_failed" for event, _kw in logger.exception_events)


def test_publish_snmp_invalid_field_type_logs_error_not_raise(monkeypatch):
    """
    Invalid field type should cause write_points to fail.
    _publish_snmp must only log influx_write_failed and return (no raise).
    """

    class DummyInfluxClient:
        instances: list[DummyInfluxClient] = []

        def __init__(
            self,
            host: str,
            port: int,
            username: str,
            password: str,
            database: str,
            ssl: bool,
            verify_ssl: bool,
        ) -> None:
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            for p in points:
                fields = p.get("fields", {})
                if any(isinstance(v, str) for v in fields.values()):
                    raise ValueError("invalid field type for Influx")

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, logger = make_avtools_for_tests()

    points = [
        {"measurement": "query", "tags": {"ip": "10.0.0.1"}, "fields": {"value": 1}},
        {
            "measurement": "query",
            "tags": {"ip": "10.0.0.2"},
            "fields": {"value": "not-a-number"},
        },
    ]

    av._publish_snmp(
        points=points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    assert len(DummyInfluxClient.instances) == 1
    assert any(event == "influx_write_failed" for event, _kw in logger.exception_events)


def test_publish_snmp_partial_write_error_does_not_retry(monkeypatch):
    """
    Partial write error must:
      - result in a single write_points attempt
      - be logged as influx_write_failed
      - not be retried or propagated
    """

    class DummyInfluxClient:
        instances: list[DummyInfluxClient] = []

        def __init__(
            self,
            host: str,
            port: int,
            username: str,
            password: str,
            database: str,
            ssl: bool,
            verify_ssl: bool,
        ) -> None:
            self.write_calls = 0
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            self.write_calls += 1
            raise RuntimeError("partial write: field type conflict")

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, logger = make_avtools_for_tests()

    points = [
        {
            "measurement": "query",
            "tags": {"ip": "10.0.0.34"},
            "fields": {"value": 2025},
        },
    ]

    av._publish_snmp(
        points=points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    assert len(DummyInfluxClient.instances) == 1
    client = DummyInfluxClient.instances[0]
    assert client.write_calls == 1  # no retry

    assert any(event == "influx_write_failed" for event, _kw in logger.exception_events)
