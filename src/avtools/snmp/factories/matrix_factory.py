"""Factory for AV matrix-switcher SNMP handlers (eq_class ``AVA`` / ``AVVS``).

Dispatch by manufacturer brand:

- ``EXT`` -> :class:`ExtronMatrix`
- anything else / blank -> ``None`` (device falls back to the generic handler)

Targets are :class:`~avtools.postgres.inventory.orm.landb_ipaddress.CachedIPAddress`.
"""

from __future__ import annotations

import structlog

from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.handlers.matrix import AbstractMatrix, ExtronMatrix

logger = structlog.get_logger(__name__).bind(
    component="snmp",
    factory="MatrixHandlerFactory",
)

_EXTRON_BRANDS = {"EXT", "EXTRON"}


class MatrixHandlerFactory:
    """Factory for AV matrix-switcher SNMP handlers."""

    def __init__(self, target: CachedIPAddress) -> None:
        """Initialize the factory.

        Args:
            target: CachedIPAddress target.

        Returns:
            None.
        """
        self.target = target

    def create(self) -> AbstractMatrix | None:
        """Create the appropriate matrix handler for the target.

        Returns:
            A concrete matrix handler, or ``None`` if the IP is missing or the
            manufacturer is unsupported.
        """
        ip = (self.target.ip or "").strip()
        if not ip:
            logger.debug("matrix_missing_ip")
            return None

        community = "public"
        port = 161

        brand = (self.target.manufacturer or "").strip().upper()

        if brand in _EXTRON_BRANDS:
            return ExtronMatrix(ip, community=community, port=port)

        logger.debug(
            "unsupported_matrix_manufacturer",
            manufacturer=brand or None,
            ip=ip,
        )
        return None
