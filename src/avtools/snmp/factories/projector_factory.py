"""Factory for projector SNMP handlers."""

from __future__ import annotations

import structlog

from avtools.snmp.handlers.projector import AbstractProjector, EpsonProjector

# from avtools.snmp.handlers.sony_projector import SonyProjector
# from avtools.snmp.handlers.panasonic_projector import PanasonicProjector


logger = structlog.get_logger(__name__)


class ProjectorHandlerFactory:
    """Factory for projector SNMP handlers.

    The selection is currently based on the device manufacturer code.
    """

    def __init__(self, target: LanDBDevice) -> None:
        """Create a projector handler factory.

        Args:
            target: LanDB device-like object. Must expose at least ``manufacturer`` and
                ``ip`` (and optionally SNMP credentials).

        Returns:
            None.
        """
        self.target = target

    def create(self) -> AbstractProjector | None:
        """Instantiate the appropriate projector handler.

        Returns:
            A concrete ``AbstractProjector`` instance (e.g. ``EpsonProjector``), or
            ``None`` if the manufacturer is not supported.

        Notes:
            The manufacturer value is normalized to uppercase and matched against
            known vendor codes.
        """
        brand = self.target.manufacturer.strip().upper()

        if brand == "EPS":
            return EpsonProjector(self.target)

        logger.warning(
            "No SNMP handler for projector manufacturer '%s'",
            self.target.manufacturer,
        )
        return None
