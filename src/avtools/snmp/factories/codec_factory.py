"""Factory for video-codec SNMP handlers (eq_class ``AVV``).

Dispatch by manufacturer brand:

- ``CISCO`` / ``CISC01`` -> :class:`CiscoCodec`
- ``POLC``              -> :class:`PolycomCodec`
- ``RADV``              -> :class:`RadvisionCodec`
- anything else / blank -> ``None`` (device falls back to the generic handler)

Unlike projectors, blank brand does **not** default to a dominant vendor: codec
brands are reliably populated in the inventory, so an unknown brand is treated
conservatively. (All codec handlers currently share a MIB-II-only
implementation, so the brand split is about future vendor OIDs and clear
routing, not present behaviour.)

Targets are :class:`~avtools.postgres.inventory.orm.landb_ipaddress.CachedIPAddress`.
"""

from __future__ import annotations

import structlog

from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.handlers.codec import (
    AbstractCodec,
    CiscoCodec,
    PolycomCodec,
    RadvisionCodec,
)

logger = structlog.get_logger(__name__).bind(
    component="snmp",
    factory="CodecHandlerFactory",
)

_CISCO_BRANDS = {"CISCO", "CISC01"}
_POLYCOM_BRANDS = {"POLC", "POLY", "POLYCOM"}
_RADVISION_BRANDS = {"RADV", "RADVISION"}


class CodecHandlerFactory:
    """Factory for video-codec SNMP handlers."""

    def __init__(self, target: CachedIPAddress) -> None:
        """Initialize the factory.

        Args:
            target: CachedIPAddress target.

        Returns:
            None.
        """
        self.target = target

    def create(self) -> AbstractCodec | None:
        """Create the appropriate codec handler for the target.

        Returns:
            A concrete codec handler, or ``None`` if the IP is missing or the
            manufacturer is unsupported.
        """
        ip = (self.target.ip or "").strip()
        if not ip:
            logger.debug("codec_missing_ip")
            return None

        community = "public"
        port = 161

        brand = (self.target.manufacturer or "").strip().upper()

        if brand in _CISCO_BRANDS:
            return CiscoCodec(ip, community=community, port=port)
        if brand in _POLYCOM_BRANDS:
            return PolycomCodec(ip, community=community, port=port)
        if brand in _RADVISION_BRANDS:
            return RadvisionCodec(ip, community=community, port=port)

        logger.debug(
            "unsupported_codec_manufacturer",
            manufacturer=brand or None,
            ip=ip,
        )
        return None
