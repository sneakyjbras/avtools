from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import avtools.snmp.factories.codec_factory as cfac
import avtools.snmp.factories.matrix_factory as mfac
import avtools.snmp.handlers.codec as codec
import avtools.snmp.handlers.matrix as matrix


def _ok(var_binds):
    return (None, None, 0, var_binds)


@dataclass
class DummyDevice:
    ip: str | None
    manufacturer: str | None = None
    model: str | None = None
    eq_class: str | None = None
    equipment_no: str | None = "EQ"


# --------------------------------------------------------------------------- #
# Handlers: MIB-II uptime
# --------------------------------------------------------------------------- #


def test_codec_fetch_stats_uptime(monkeypatch: Any) -> None:
    h = codec.CiscoCodec("192.0.2.10")
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok([("uptime", 59010160)]))
    stats = h.fetch_stats()
    assert stats == {"uptime_seconds": 590101.6}


def test_codec_fetch_stats_error_empty(monkeypatch: Any) -> None:
    h = codec.PolycomCodec("192.0.2.11")
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: ("timeout", None, 0, []))
    assert h.fetch_stats() == {}


def test_matrix_fetch_stats_uptime(monkeypatch: Any) -> None:
    h = matrix.ExtronMatrix("192.0.2.12")
    monkeypatch.setattr(h, "_snmp_get", lambda *a, **k: _ok([("uptime", 100)]))
    assert h.fetch_stats() == {"uptime_seconds": 1.0}


# --------------------------------------------------------------------------- #
# Codec factory: brand dispatch
# --------------------------------------------------------------------------- #


def test_codec_factory_cisco():
    for brand in ("CISCO", "CISC01"):
        h = cfac.CodecHandlerFactory(DummyDevice(ip="192.0.2.10", manufacturer=brand)).create()
        assert isinstance(h, codec.CiscoCodec)


def test_codec_factory_polycom_and_radvision():
    h = cfac.CodecHandlerFactory(DummyDevice(ip="192.0.2.10", manufacturer="POLC")).create()
    assert isinstance(h, codec.PolycomCodec)
    h = cfac.CodecHandlerFactory(DummyDevice(ip="192.0.2.10", manufacturer="RADV")).create()
    assert isinstance(h, codec.RadvisionCodec)


def test_codec_factory_unknown_brand_returns_none():
    assert cfac.CodecHandlerFactory(DummyDevice(ip="192.0.2.10", manufacturer="")).create() is None
    assert (
        cfac.CodecHandlerFactory(DummyDevice(ip="192.0.2.10", manufacturer="EXT")).create() is None
    )


def test_codec_factory_missing_ip_returns_none():
    assert cfac.CodecHandlerFactory(DummyDevice(ip="", manufacturer="CISCO")).create() is None


# --------------------------------------------------------------------------- #
# Matrix factory: brand dispatch
# --------------------------------------------------------------------------- #


def test_matrix_factory_extron():
    h = mfac.MatrixHandlerFactory(DummyDevice(ip="192.0.2.12", manufacturer="EXT")).create()
    assert isinstance(h, matrix.ExtronMatrix)


def test_matrix_factory_unknown_brand_returns_none():
    assert (
        mfac.MatrixHandlerFactory(DummyDevice(ip="192.0.2.12", manufacturer="CISCO")).create()
        is None
    )
    assert mfac.MatrixHandlerFactory(DummyDevice(ip="192.0.2.12", manufacturer="")).create() is None
