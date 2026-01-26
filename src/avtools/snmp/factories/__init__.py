"""Factories for selecting SNMP handlers."""

from __future__ import annotations

from avtools.snmp.factories.device_factory import DeviceHandlerFactory
from avtools.snmp.factories.projector_factory import ProjectorHandlerFactory

__all__ = [
    "DeviceHandlerFactory",
    "ProjectorHandlerFactory",
    # "create_sensor_handler",
]
