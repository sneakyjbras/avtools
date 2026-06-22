from __future__ import annotations

from dataclasses import dataclass

import avtools.snmp.factories.pdu_factory as mod
from avtools.snmp.handlers.pdu import Sentry3Pdu, Sentry4Pdu


@dataclass
class DummyDevice:
    """Minimal CachedIPAddress stand-in for the factory."""

    ip: str | None
    manufacturer: str | None = None
    model: str | None = None
    eq_class: str | None = "AVS"
    equipment_no: str | None = "EQ"


def test_cw_model_dispatches_to_sentry4():
    dev = DummyDevice(ip="192.0.2.1", manufacturer="STECH", model="CW-16HEK452")
    h = mod.PduHandlerFactory(dev).create()
    assert isinstance(h, Sentry4Pdu)


def test_aa_model_with_blank_brand_dispatches_to_sentry3():
    # Manufacturer blank, model carries the SERVERTECH keyword + AA prefix.
    dev = DummyDevice(ip="192.0.2.2", manufacturer=None, model="SERVERTECH AA12J")
    h = mod.PduHandlerFactory(dev).create()
    assert isinstance(h, Sentry3Pdu)


def test_globalservertech_cw_dispatches_to_sentry4():
    dev = DummyDevice(ip="192.0.2.3", manufacturer=None, model="GLOBALSERVERTECH CW-16HEU452CR")
    h = mod.PduHandlerFactory(dev).create()
    assert isinstance(h, Sentry4Pdu)


def test_mfg_prefixed_aa_dispatches_to_sentry3():
    dev = DummyDevice(ip="192.0.2.4", manufacturer=None, model="SERVERTECH MFG-AA11K")
    h = mod.PduHandlerFactory(dev).create()
    assert isinstance(h, Sentry3Pdu)


def test_unknown_servertech_model_returns_none():
    dev = DummyDevice(ip="192.0.2.5", manufacturer="STECH", model="ZZ-999")
    assert mod.PduHandlerFactory(dev).create() is None


def test_non_servertech_returns_none():
    dev = DummyDevice(ip="192.0.2.6", manufacturer="CISCO", model="SX20")
    assert mod.PduHandlerFactory(dev).create() is None


def test_missing_ip_returns_none():
    dev = DummyDevice(ip="", manufacturer="STECH", model="CW-16HEK452")
    assert mod.PduHandlerFactory(dev).create() is None
