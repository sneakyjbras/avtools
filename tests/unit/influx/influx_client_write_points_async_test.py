from __future__ import annotations

import asyncio
from typing import Any

from avtools.influx.client import InfluxClient


class DummyInfluxDBClient:
    def __init__(self, **kwargs: Any) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    def write_points(self, points: list[dict[str, Any]]) -> None:
        self.calls.append(points)


def test_write_points_async_runs_write_points_in_executor(monkeypatch: Any) -> None:
    import avtools.influx.client as mod

    dummy = DummyInfluxDBClient()
    monkeypatch.setattr(mod, "InfluxDBClient", lambda **kwargs: dummy, raising=True)

    client = InfluxClient(host="h", port=1, username="u", password="p", database="d")

    pts = [{"measurement": "m", "fields": {"x": 1}}]
    asyncio.run(client.write_points_async(pts))

    assert dummy.calls == [pts]
