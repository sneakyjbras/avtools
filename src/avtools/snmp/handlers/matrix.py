"""SNMP handlers for AV matrix switchers (eq_class ``AVA`` / ``AVVS``).

The CERN fleet uses Extron DXP / DTP CrossPoint HDMI matrices. These are
primarily controlled over Extron SIS (TCP/23); their SNMP exposure beyond the
MIB-II system group is firmware-dependent and not openly documented. Extron's
enterprise tree is ``1.3.6.1.4.1.3606``.

For now the handler collects the reliably-available **uptime** (``sysUpTime``);
identity (``sysDescr``) is already captured by the probe stage. Extron-enterprise
signal/tie/temperature OIDs are intentionally **not** hardcoded — they will be
added in a later iteration once a live walk against a unit confirms what is
exposed (see ``OID_EXTRON_*`` placeholder below).

Normalised output key:

    uptime_seconds   MIB-II sysUpTime (s)
"""

from __future__ import annotations

from abc import ABC

from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler
from avtools.snmp.handlers.parsing import as_int, ticks_to_seconds, values_from_varbinds

# MIB-II sysUpTime.0 (TimeTicks).
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

# Extron enterprise root — reserved for future signal/temperature OIDs once a
# live snmpwalk confirms what a given DXP model/firmware actually exposes.
OID_EXTRON_ENTERPRISE = "1.3.6.1.4.1.3606"


class AbstractMatrix(AbstractDeviceHandler, ABC):
    """Base class for AV matrix-switcher SNMP handlers.

    Provides the MIB-II uptime read. Vendor subclasses add enterprise OIDs as
    they are confirmed.
    """

    DEFAULT_TIMEOUT: float = 1.5
    DEFAULT_RETRIES: int = 1

    def fetch_stats(self) -> dict[str, object]:
        """Fetch MIB-II uptime from the matrix switcher.

        Returns:
            ``{"uptime_seconds": float}`` when available, else an empty dict.
        """
        error_indication, error_status, _error_index, var_binds = self._snmp_get(
            [OID_SYS_UPTIME],
            timeout=self.DEFAULT_TIMEOUT,
            retries=self.DEFAULT_RETRIES,
        )
        if error_indication or error_status:
            return {}

        v = values_from_varbinds(var_binds)
        uptime_seconds = ticks_to_seconds(as_int(v[0])) if v else None

        out: dict[str, object] = {}
        if uptime_seconds is not None:
            out["uptime_seconds"] = uptime_seconds
        return out


class ExtronMatrix(AbstractMatrix):
    """Extron DXP / DTP CrossPoint HDMI matrix switcher.

    MIB-II only for now; Extron-enterprise OIDs (under
    :data:`OID_EXTRON_ENTERPRISE`) are deferred pending a live capability walk.
    """
