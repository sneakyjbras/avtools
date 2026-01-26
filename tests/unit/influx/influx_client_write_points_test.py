from __future__ import annotations

from typing import Any

import pytest

from avtools.exception.errors import InfluxClientError
from avtools.influx.client import InfluxClient


class DummyInfluxDBClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.written: list[list[dict[str, Any]]] = []
        self.raise_on_write: Exception | None = None

    def write_points(self, points: list[dict[str, Any]]) -> None:
        if self.raise_on_write:
            raise self.raise_on_write
        self.written.append(points)


def test_influx_client_init_wraps_constructor_errors(monkeypatch: Any) -> None:
    import avtools.influx.client as mod

    class Boom(Exception):
        pass

    def raising_ctor(**kwargs: Any):
        raise Boom("nope")

    monkeypatch.setattr(mod, "InfluxDBClient", raising_ctor, raising=True)

    with pytest.raises(InfluxClientError):
        InfluxClient(host="h", port=1, username="u", password="p", database="d")


def test_write_points_no_points_is_noop(monkeypatch: Any) -> None:
    import avtools.influx.client as mod

    dummy = DummyInfluxDBClient()
    monkeypatch.setattr(mod, "InfluxDBClient", lambda **kwargs: dummy, raising=True)

    client = InfluxClient(host="h", port=1, username="u", password="p", database="d")
    client.write_points([])

    assert dummy.written == []


def test_write_points_calls_underlying_client(monkeypatch: Any) -> None:
    import avtools.influx.client as mod

    dummy = DummyInfluxDBClient()
    monkeypatch.setattr(mod, "InfluxDBClient", lambda **kwargs: dummy, raising=True)

    client = InfluxClient(host="h", port=1, username="u", password="p", database="d")

    pts = [{"measurement": "m", "fields": {"x": 1}}]
    client.write_points(pts)

    assert dummy.written == [pts]


def test_write_points_wraps_write_errors(monkeypatch: Any) -> None:
    import avtools.influx.client as mod

    dummy = DummyInfluxDBClient()
    dummy.raise_on_write = RuntimeError("write failed")
    monkeypatch.setattr(mod, "InfluxDBClient", lambda **kwargs: dummy, raising=True)

    client = InfluxClient(host="h", port=1, username="u", password="p", database="d")

    with pytest.raises(InfluxClientError):
        client.write_points([{"measurement": "m", "fields": {"x": 1}}])
