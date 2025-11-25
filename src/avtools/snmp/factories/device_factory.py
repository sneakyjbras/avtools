from __future__ import annotations

from typing import Optional, Type

from avtools.io.logger import system_logger
from avtools.landb.client import LanDBDevice
from avtools.snmp.factories.projector_factory import ProjectorHandlerFactory
from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler

# from avtools.snmp.factories.sensor_factory import SensorHandlerFactory

# Map eqclass → factory class
_FACTORY_REGISTRY: dict[str, type] = {
    "AVD": ProjectorHandlerFactory,
    # "SENSOR": SensorHandlerFactory,
}


class DeviceHandlerFactory:
    def __init__(self, target: LanDBDevice) -> None:
        self.target = target

    def create(self) -> AbstractDeviceHandler | None:
        dtype = self.target.eq_class.upper()
        FactoryCls = _FACTORY_REGISTRY.get(dtype)
        if not FactoryCls:
            system_logger.warning(
                f"Unsupported device class '{self.target.eq_class}' "
                f"for {self.target.serial_number} (IP: {self.target.ip})"
            )
            return None

        return FactoryCls(self.target).create()
