from __future__ import annotations

from dataclasses import dataclass

import pytest

from avtools.core.av_tools import AVTools
from avtools.exception.errors import PostgresError


@dataclass
class DummyTarget:
    equipment_no: str | None
    ip: str | None


class DummyDB:
    def __init__(self, targets):
        self._targets = targets

    def get_all_landb_devices(self):
        if isinstance(self._targets, Exception):
            raise self._targets
        return list(self._targets)


def _make_av(db: DummyDB) -> AVTools:
    av = object.__new__(AVTools)
    av.dbod_helper = db
    return av


def test_load_timeseries_targets_filters_and_normalizes_ip() -> None:
    db = DummyDB(
        [
            DummyTarget(equipment_no=" EQ1 ", ip="10.0.0.1/24"),
            DummyTarget(equipment_no=None, ip="10.0.0.2"),
            DummyTarget(equipment_no="EQ3", ip=None),
            DummyTarget(equipment_no="", ip="10.0.0.4"),
            DummyTarget(equipment_no="EQ5", ip="  "),
        ]
    )
    av = _make_av(db)

    out = av._load_timeseries_targets_from_landb_ipaddresses()
    assert len(out) == 1
    assert out[0].equipment_no == "EQ1"
    assert out[0].ip == "10.0.0.1"


def test_load_timeseries_targets_wraps_unexpected_errors() -> None:
    av = _make_av(DummyDB(RuntimeError("boom")))
    with pytest.raises(PostgresError):
        av._load_timeseries_targets_from_landb_ipaddresses()
