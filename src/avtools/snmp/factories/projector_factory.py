from __future__ import annotations

from typing import NoReturn

import structlog
from pysnmp.hlapi import SnmpEngine

from avtools.landb.client import LanDBDevice
from avtools.snmp.handlers.projector import AbstractProjector, EpsonProjector

# from avtools.snmp.handlers.sony_projector import SonyProjector
# from avtools.snmp.handlers.panasonic_projector import PanasonicProjector


logger = structlog.get_logger(__name__)


class ProjectorHandlerFactory:
    """
    Factory for creating an AbstractProjector handler based on the device's manufacturer.
    """

    def __init__(self, target: LanDBDevice) -> None:
        """
        Initialize the factory with the given LANDBDevice.

        Args:
            target (LanDBDevice): The device to create a handler for. Must have
                                 `.manufacturer`, `.ip`, and SNMP credentials.
        """
        self.target = target

    def create(self) -> AbstractProjector:
        """
        Instantiate and return a concrete AbstractProjector subclass for this device.

        Returns:
            AbstractProjector: An instance of the appropriate handler (e.g. EpsonProjector).

        Raises:
            ValueError: If no handler exists for the device's manufacturer.
        """
        brand = self.target.manufacturer.strip().upper()

        if brand == "EPSON":
            return EpsonProjector(self.target)

        logger.warning(
            "No SNMP handler for projector manufacturer '%s'",
            self.target.manufacturer,
        )
        return None
