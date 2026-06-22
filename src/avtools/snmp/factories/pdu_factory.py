"""Factory for Server Technology PDU SNMP handlers.

Dispatch follows ``brand -> model``:

- The device must be a Server Technology unit (manufacturer ``STECH`` or, where
  the manufacturer field is blank, a model string carrying ``SERVERTECH`` /
  ``GLOBALSERVERTECH``).
- Generation is then chosen by model prefix:
    - ``CW-*``           -> PRO2  -> :class:`Sentry4Pdu`
    - ``AA-*`` / ``MFG-AA-*`` -> CDU   -> :class:`Sentry3Pdu`
- Anything else (non-Server-Technology, or an unrecognised Server Technology
  model) returns ``None`` so the device falls back to the generic SNMP handler.

Targets are :class:`~avtools.postgres.inventory.orm.landb_ipaddress.CachedIPAddress`.
"""

from __future__ import annotations

import structlog

from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.handlers.pdu import AbstractPdu, Sentry3Pdu, Sentry4Pdu

logger = structlog.get_logger(__name__).bind(
    component="snmp",
    factory="PduHandlerFactory",
)

# Manufacturer codes / model keywords that identify Server Technology hardware.
_SERVERTECH_BRANDS = {"STECH", "SERVERTECH", "GLOBALSERVERTECH"}
_SERVERTECH_MODEL_KEYWORDS = ("SERVERTECH", "GLOBALSERVERTECH")
_SERVERTECH_MODEL_PREFIXES = ("SERVERTECH ", "GLOBALSERVERTECH ", "MFG-")


class PduHandlerFactory:
    """Factory for Server Technology PDU SNMP handlers."""

    def __init__(self, target: CachedIPAddress) -> None:
        """Initialize the factory.

        Args:
            target: CachedIPAddress target.

        Returns:
            None.
        """
        self.target = target

    @staticmethod
    def _normalise_model(model: str | None) -> str:
        """Uppercase a model string and strip vendor-word/MFG prefixes.

        Args:
            model: Raw model string (may be ``None``).

        Returns:
            Normalised model string (possibly empty).
        """
        m = (model or "").strip().upper()
        for prefix in _SERVERTECH_MODEL_PREFIXES:
            if m.startswith(prefix):
                m = m[len(prefix) :].strip()
        return m

    def _is_servertech(self, brand: str, model: str | None) -> bool:
        """Return True if the target is a Server Technology unit.

        Args:
            brand: Upper-cased manufacturer code.
            model: Raw model string.

        Returns:
            True if brand or model identifies Server Technology.
        """
        if brand in _SERVERTECH_BRANDS:
            return True
        m = (model or "").upper()
        return any(kw in m for kw in _SERVERTECH_MODEL_KEYWORDS)

    def create(self) -> AbstractPdu | None:
        """Create the appropriate PDU handler for the target.

        Returns:
            A concrete PDU handler, or ``None`` if the IP is missing, the device
            is not Server Technology, or the model is unrecognised.
        """
        ip = (self.target.ip or "").strip()
        if not ip:
            logger.debug("pdu_missing_ip")
            return None

        # CachedIPAddress does not carry SNMP params; keep defaults.
        community = "public"
        port = 161

        brand = (self.target.manufacturer or "").strip().upper()
        if not self._is_servertech(brand, self.target.model):
            logger.debug(
                "unsupported_pdu_manufacturer",
                manufacturer=brand or None,
                model=self.target.model,
                ip=ip,
            )
            return None

        model = self._normalise_model(self.target.model)

        if model.startswith("CW"):
            return Sentry4Pdu(ip, community=community, port=port)
        if model.startswith("AA"):
            return Sentry3Pdu(ip, community=community, port=port)

        # Server Technology, but an unrecognised model: fail to generic.
        logger.debug(
            "unsupported_pdu_model",
            manufacturer=brand or None,
            model=self.target.model,
            ip=ip,
        )
        return None
