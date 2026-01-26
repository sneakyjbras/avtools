"""Factory dispatch for SNMP handlers by device class."""

from __future__ import annotations

import structlog

from avtools.snmp.factories.projector_factory import ProjectorHandlerFactory
from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler

# from avtools.snmp.factories.sensor_factory import SensorHandlerFactory

logger = structlog.get_logger(__name__).bind(
    component="snmp",
    factory="DeviceHandlerFactory",
)

# Map eq_class → factory class
_FACTORY_REGISTRY: dict[str, type] = {
    "AVD": ProjectorHandlerFactory,
    # "SENSOR": SensorHandlerFactory,
}


class DeviceHandlerFactory:
    """Factory for SNMP device handlers based on LanDB eq_class."""

    def __init__(self, target: LanDBDevice) -> None:
        self.target = target

    def create(self) -> AbstractDeviceHandler | None:
        """Return an appropriate handler for the target device, or None."""
        eq_class = (self.target.eq_class or "").upper()
        factory_cls = _FACTORY_REGISTRY.get(eq_class)
        if not factory_cls:
            logger.warning(
                "unsupported_device_class",
                eq_class=self.target.eq_class,
                equipment_no=getattr(self.target, "equipment_no", None),
                serial_number=getattr(self.target, "serial_number", None),
                ip=self.target.ip,
            )
            return None

        return factory_cls(self.target).create()
