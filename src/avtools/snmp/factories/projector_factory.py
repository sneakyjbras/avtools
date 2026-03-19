"""Factory for projector SNMP handlers.

Targets are expected to be :class:`~avtools.postgres.orm.landb_ipaddress.CachedIPAddress`
domain objects.
"""

from __future__ import annotations

import structlog

from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.handlers.projector import AbstractProjector, EpsonProjector

logger = structlog.get_logger(__name__).bind(
    component="snmp",
    factory="ProjectorHandlerFactory",
)


class ProjectorHandlerFactory:
    """Factory for projector SNMP handlers."""

    def __init__(self, target: CachedIPAddress) -> None:
        """Initialize the factory.

        Args:
            target: CachedIPAddress target.

        Returns:
            None.
        """
        self.target = target

    def create(self) -> AbstractProjector | None:
        """Create the appropriate projector handler for the given target.

        Policy (kept the same as before):
        - Epson is supported.
        - If manufacturer is missing (targets coming from landb_ipaddresses),
          default to Epson so we can still query known projectors.

        Returns:
            A concrete projector handler, or None if IP is missing or manufacturer is unsupported.
        """
        ip = (self.target.ip or "").strip()
        if not ip:
            logger.debug("projector_missing_ip")
            return None

        # CachedIPAddress does not currently carry SNMP params; keep defaults.
        community = "public"
        port = 161

        brand = (self.target.manufacturer or "").strip().upper()

        if not brand or brand in {"EPS", "EPSON"}:
            return EpsonProjector(ip, community=community, port=port)

        logger.debug(
            "unsupported_projector_manufacturer",
            manufacturer=brand or None,
            ip=ip,
        )
        return None
