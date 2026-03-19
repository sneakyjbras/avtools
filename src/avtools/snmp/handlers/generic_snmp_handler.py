"""Generic SNMP handler.

This module provides a minimal SNMP handler implementation that supports
generic SNMP probing (reachability) for devices whose concrete `eq_class`
handler is not implemented yet.

The handler intentionally returns no device-specific stats; it exists so that
`SNMPClient.collect_snmp_probe()` can still probe any SNMP-capable device
without requiring a specialized handler.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler


class GenericSnmpHandler(AbstractDeviceHandler):
    """Generic SNMP handler used as a safe fallback.

    This handler is used when AV Tools cannot build a specialized handler for a
    given device type (e.g., matrices, DSPs) but still wants to run a generic
    SNMP probe.

    Args:
        ip: IP address or DNS name of the target device.
        community: SNMP v2c community string. Defaults to ``"public"``.
        port: UDP port on which the SNMP agent listens. Defaults to ``161``.

    Returns:
        A handler instance that supports :meth:`probe` and returns an empty
        mapping for :meth:`fetch_stats`.
    """

    def fetch_stats(self) -> Mapping[str, Any]:
        """Return no device-specific metrics.

        This method intentionally returns an empty mapping because AV Tools does
        not (yet) know which device-specific OIDs to query for this device.

        Returns:
            An empty mapping.
        """
        return {}
