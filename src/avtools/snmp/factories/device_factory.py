"""Factory dispatch for SNMP handlers by device class (batch-first).

This factory builds an ``ip -> handler`` mapping for a batch of targets. Targets are
expected to be :class:`~avtools.postgres.orm.landb_ipaddress.CachedIPAddress` domain
objects produced by the AVTools router (Postgres cache loader).

Policy (kept the same as before):
- Missing ``eq_class`` defaults to ``AVD`` (projectors) for backwards compatibility.
- Unknown/unsupported ``eq_class`` falls back to :class:`~avtools.snmp.handlers.generic_snmp_handler.GenericSnmpHandler`
  so we can still run a generic SNMP probe.
- Specialized factories may decline (e.g. unsupported manufacturer); in that case we
  also fall back to the generic handler.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog

from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.factories.projector_factory import ProjectorHandlerFactory
from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler
from avtools.snmp.handlers.generic_snmp_handler import GenericSnmpHandler

logger = structlog.get_logger(__name__).bind(
    component="snmp",
    factory="DeviceHandlerFactory",
)

_FACTORY_REGISTRY: dict[str, type] = {
    "AVD": ProjectorHandlerFactory,
    # add more: "SENSOR": SensorHandlerFactory, ...
}


class DeviceHandlerFactory:
    """Build SNMP handlers from a list of targets (single is just list-of-1)."""

    def __init__(self, devices: Iterable[CachedIPAddress]) -> None:
        """Create a factory over a batch of SNMP targets.

        Args:
            devices: Iterable of CachedIPAddress targets.

        Returns:
            None.
        """
        self.devices = list(devices)

    @classmethod
    def from_target(cls, target: CachedIPAddress) -> "DeviceHandlerFactory":
        """Convenience wrapper: single device == batch of 1.

        Args:
            target: CachedIPAddress target.

        Returns:
            DeviceHandlerFactory instance wrapping a single target.
        """
        return cls([target])

    def _create_for_one(self, dev: CachedIPAddress) -> AbstractDeviceHandler | None:
        """Create the most specific handler possible for one target.

        Args:
            dev: CachedIPAddress target.

        Returns:
            A concrete handler instance, or None if the target is missing an IP.
        """
        ip = (dev.ip or "").strip()
        if not ip:
            return None

        # Keep historical default: missing class == "AVD".
        eq_class = (dev.eq_class or "AVD").strip().upper()

        factory_cls = _FACTORY_REGISTRY.get(eq_class)
        if not factory_cls:
            logger.debug(
                "unsupported_device_class",
                eq_class=eq_class,
                equipment_no=dev.equipment_no,
                ip=ip,
            )
            return GenericSnmpHandler(ip, community="public", port=161)

        try:
            h = factory_cls(dev).create()
            if h is not None:
                return h

            # Specialized factory declined to create a handler (e.g. unsupported manufacturer).
            logger.debug(
                "handler_fallback_generic",
                eq_class=eq_class,
                equipment_no=dev.equipment_no,
                ip=ip,
            )
            return GenericSnmpHandler(ip, community="public", port=161)
        except Exception:
            logger.exception(
                "handler_factory_failed",
                eq_class=eq_class,
                equipment_no=dev.equipment_no,
                ip=ip,
            )
            # If specialized creation fails unexpectedly, still allow probing.
            return GenericSnmpHandler(ip, community="public", port=161)

    def get_handlers(self) -> dict[str, AbstractDeviceHandler]:
        """Return ``ip -> handler`` map for all provided targets.

        Returns:
            Mapping from IP string to handler instance.
        """
        handlers: dict[str, AbstractDeviceHandler] = {}

        for dev in self.devices:
            ip = (dev.ip or "").strip()
            if not ip:
                logger.debug("device_missing_ip", equipment_no=dev.equipment_no)
                continue

            if ip in handlers:
                logger.warning("duplicate_ip_in_targets", ip=ip)
                continue

            h = self._create_for_one(dev)
            if h is not None:
                handlers[ip] = h

        logger.info(
            "snmp_handlers_built",
            targets=len(self.devices),
            handlers=len(handlers),
        )
        return handlers

    def get_handler(self) -> AbstractDeviceHandler | None:
        """Convenience: return the first handler (single-device flows).

        Returns:
            First handler if any were created, else None.
        """
        handlers = self.get_handlers()
        return next(iter(handlers.values()), None)
