from __future__ import annotations

import datetime
from typing import Any

import pytest

from avtools.exception.errors import TimeSeriesHelperError
from avtools.influx.ts_helper import TimeSeriesHelper


class DummyWriteAPI:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []
        self.raise_on_write: Exception | None = None

    def write(self, *, bucket: str, org: str, record: list[Any]) -> None:
        if self.raise_on_write:
            raise self.raise_on_write
        self.writes.append({"bucket": bucket, "org": org, "record": record})


class DummyInfluxDBClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self._write_api = DummyWriteAPI()

    def write_api(self, write_options: Any = None) -> DummyWriteAPI:
        return self._write_api


class DummyPoint:
    def __init__(self, measurement: str) -> None:
        self.measurement = measurement
        self.tags: dict[str, str] = {}
        self.fields: dict[str, Any] = {}
        self.t: datetime.datetime | None = None
        self.precision: Any = None

    def tag(self, k: str, v: str) -> "DummyPoint":
        self.tags[k] = v
        return self

    def field(self, k: str, v: Any) -> "DummyPoint":
        self.fields[k] = v
        return self

    def time(self, t: datetime.datetime, precision: Any) -> "DummyPoint":
        self.t = t
        self.precision = precision
        return self


class DummyWritePrecision:
    NS = object()


def test_ts_helper_init_wraps_client_ctor_errors(monkeypatch: Any) -> None:
    import avtools.influx.ts_helper as mod

    class Boom(Exception):
        pass

    def raising_client(**kwargs: Any):
        raise Boom("nope")

    monkeypatch.setattr(mod, "InfluxDBClient", raising_client, raising=True)

    with pytest.raises(TimeSeriesHelperError):
        TimeSeriesHelper(url="http://x", token="t", org="o", bucket="b")


def test_write_snmp_data_builds_points_and_calls_write_api(monkeypatch: Any) -> None:
    import avtools.influx.ts_helper as mod

    dummy_client = DummyInfluxDBClient()
    monkeypatch.setattr(
        mod, "InfluxDBClient", lambda **kwargs: dummy_client, raising=True
    )
    monkeypatch.setattr(mod, "Point", DummyPoint, raising=True)
    monkeypatch.setattr(mod, "WritePrecision", DummyWritePrecision, raising=True)

    helper = TimeSeriesHelper(url="http://x", token="t", org="o", bucket="b")

    helper.write_snmp_data({"192.0.2.1": "ok", "198.51.100.2": "no"})

    assert len(dummy_client._write_api.writes) == 1
    payload = dummy_client._write_api.writes[0]
    assert payload["bucket"] == "b"
    assert payload["org"] == "o"

    records = payload["record"]
    assert len(records) == 2
    assert all(isinstance(r, DummyPoint) for r in records)
    assert {r.tags["ip"] for r in records} == {"192.0.2.1", "198.51.100.2"}
    assert {r.fields["response"] for r in records} == {"ok", "no"}


def test_write_snmp_data_wraps_write_errors(monkeypatch: Any) -> None:
    import avtools.influx.ts_helper as mod

    dummy_client = DummyInfluxDBClient()
    dummy_client._write_api.raise_on_write = RuntimeError("boom")

    monkeypatch.setattr(
        mod, "InfluxDBClient", lambda **kwargs: dummy_client, raising=True
    )
    monkeypatch.setattr(mod, "Point", DummyPoint, raising=True)
    monkeypatch.setattr(mod, "WritePrecision", DummyWritePrecision, raising=True)

    helper = TimeSeriesHelper(url="http://x", token="t", org="o", bucket="b")

    with pytest.raises(TimeSeriesHelperError):
        helper.write_snmp_data({"192.0.2.1": "ok"})
