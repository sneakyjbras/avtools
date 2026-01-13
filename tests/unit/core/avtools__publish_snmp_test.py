from __future__ import annotations

from typing import Any

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools


class DummyLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.exception_messages: list[str] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.info_messages.append(str(msg))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exception_messages.append(str(msg))

    # Keep a generic error method in case it’s ever called
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exception_messages.append(str(msg))


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
    # Log should indicate skipping
    assert any("skipping" in msg.lower() for msg in logger.info_messages)


# ---------------------------------------------------------------------------
# Normal write behaviour
# ---------------------------------------------------------------------------


def test_publish_snmp_non_empty_points_calls_write_once(monkeypatch):
    """Non-empty list must produce exactly one write_points call."""

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

    # Logging: should mention how many points were written
    assert any("wrote" in msg.lower() for msg in logger.info_messages)
    assert any("3" in msg for msg in logger.info_messages)
    # No exceptions logged
    assert logger.exception_messages == []


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
# Error handling
# ---------------------------------------------------------------------------


def test_publish_snmp_influx_write_error_is_caught(monkeypatch):
    """
    If write_points raises, the exception must be caught and logged,
    not propagated.
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

    # Should not raise, even though write_points fails
    av._publish_snmp(
        points=points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    assert len(DummyInfluxClient.instances) == 1
    # We expect at least one exception log
    assert any(
        "failed writing points" in msg.lower() for msg in logger.exception_messages
    )


def test_publish_snmp_malformed_points_still_handled_via_error(monkeypatch):
    """
    Malformed points are effectively just another write error from the
    perspective of _publish_snmp: it should log and not crash.
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
            # Simulate Influx rejecting malformed points
            for p in points:
                if "measurement" not in p:
                    raise ValueError("missing measurement")
            # If everything was fine, we'd just return

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

    # Error should be logged, not raised
    assert any(
        "failed writing points" in msg.lower() for msg in logger.exception_messages
    )


# ---------------------------------------------------------------------------
# Mixed point types and large batches
# ---------------------------------------------------------------------------


def test_publish_snmp_mixed_point_types_forwarded_unchanged(monkeypatch):
    """Ping, probe, and query points should be forwarded as-is in a single batch."""

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
            self.writes: list[list[dict[str, Any]]] = []
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            self.writes.append(points)

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, _ = make_avtools_for_tests()

    points = [
        {"measurement": "ping", "tags": {"ip": "10.0.0.34"}, "fields": {"status": 1}},
        {
            "measurement": "probe",
            "tags": {"ip": "10.0.0.34"},
            "fields": {"uptime": 1234},
        },
        {
            "measurement": "query",
            "tags": {"ip": "10.0.0.34"},
            "fields": {"value": 2137},
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
    assert len(client.writes) == 1
    assert client.writes[0] is points


def test_publish_snmp_large_batch_still_single_write_call(monkeypatch):
    """A large list of points should still produce a single write_points batch."""

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
            self.writes: list[list[dict[str, Any]]] = []
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            self.writes.append(points)

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, _ = make_avtools_for_tests()

    large_points = [
        {
            "measurement": "query",
            "tags": {"ip": f"10.0.0.{i}"},
            "fields": {"value": i},
        }
        for i in range(1, 1001)
    ]

    av._publish_snmp(
        points=large_points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    assert len(DummyInfluxClient.instances) == 1
    client = DummyInfluxClient.instances[0]
    assert len(client.writes) == 1
    assert len(client.writes[0]) == len(large_points)


# ---------------------------------------------------------------------------
# Re-entrancy / multiple calls
# ---------------------------------------------------------------------------


def test_publish_snmp_multiple_calls_are_independent(monkeypatch):
    """
    Calling _publish_snmp multiple times should create separate InfluxClient
    instances, each with its own writes.
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
            self.writes: list[list[dict[str, Any]]] = []
            DummyInfluxClient.instances.append(self)

        def write_points(self, points: list[dict[str, Any]]) -> None:
            self.writes.append(points)

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, _ = make_avtools_for_tests()

    points_first = [
        {"measurement": "ping", "tags": {"ip": "10.0.0.34"}, "fields": {"status": 1}},
    ]
    points_second = [
        {"measurement": "ping", "tags": {"ip": "10.0.0.38"}, "fields": {"status": 1}},
        {"measurement": "ping", "tags": {"ip": "10.0.0.39"}, "fields": {"status": 1}},
    ]

    av._publish_snmp(
        points=points_first,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )
    av._publish_snmp(
        points=points_second,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    assert len(DummyInfluxClient.instances) == 2
    first_client, second_client = DummyInfluxClient.instances

    assert first_client.writes == [points_first]
    assert second_client.writes == [points_second]


def test_publish_snmp_does_not_mutate_points_list(monkeypatch):
    """_publish_snmp should not modify the points list in-place."""

    class DummyInfluxClient:
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
            self.writes: list[list[dict[str, Any]]] = []

        def write_points(self, points: list[dict[str, Any]]) -> None:
            self.writes.append(points)

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, _ = make_avtools_for_tests()

    points = [
        {"measurement": "ping", "tags": {"ip": "10.0.0.34"}, "fields": {"status": 1}},
        {"measurement": "probe", "tags": {"ip": "10.0.0.34"}, "fields": {"uptime": 99}},
    ]
    before = [p.copy() for p in points]

    av._publish_snmp(
        points=points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    # Points content remains unchanged
    assert points == before


# ---------------------------------------------------------------------------
# Additional error-path tests for malformed / partial writes
# ---------------------------------------------------------------------------


def test_publish_snmp_invalid_field_type_logs_error_not_raise(monkeypatch):
    """
    Points with incorrect field types should cause Influx write to fail,
    but _publish_snmp must only log and return, without raising.
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
            # Simulate a type error from Influx due to bad field type
            for p in points:
                fields = p.get("fields", {})
                if any(isinstance(v, str) for v in fields.values()):
                    raise ValueError("invalid field type for Influx")
            # otherwise succeed silently

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, logger = make_avtools_for_tests()

    points = [
        {
            "measurement": "query",
            "tags": {"ip": "10.0.0.1"},
            "fields": {"value": 1},
        },
        {
            "measurement": "query",
            "tags": {"ip": "10.0.0.2"},
            "fields": {"value": "not-a-number"},  # invalid type
        },
    ]

    # Should not raise, even though one field type is invalid
    av._publish_snmp(
        points=points,
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
    )

    # Error should be logged
    assert any(
        "failed writing points" in msg.lower() for msg in logger.exception_messages
    )
    # Influx client instantiated exactly once
    assert len(DummyInfluxClient.instances) == 1


def test_publish_snmp_partial_write_error_does_not_retry(monkeypatch):
    """
    A 'partial write' style error from Influx must:
    - result in a single write_points attempt;
    - be logged;
    - not be retried or propagated.
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
            # Simulate a typical Influx "partial write" error
            raise RuntimeError("partial write: field type conflict")

    monkeypatch.setattr(core, "InfluxClient", DummyInfluxClient)

    av, logger = make_avtools_for_tests()

    points = [
        {
            "measurement": "query",
            "tags": {"ip": "10.0.0.34"},
            "fields": {"value": 2025},
        }
    ]

    # Must not raise, even though Influx reports a partial write failure
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
    # Only a single write attempt → no retry
    assert client.write_calls == 1

    # Error message logged and includes 'partial write'
    assert any("partial write" in msg.lower() for msg in logger.exception_messages)
