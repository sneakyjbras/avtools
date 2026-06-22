"""SNMP handlers for video-conferencing codecs (eq_class ``AVV``).

Cisco/Tandberg TelePresence endpoints (SX/C-series, Room Kit, MXP), Polycom and
Radvision/Scopia units expose **only the MIB-II system group** over SNMP — Cisco
explicitly does not implement corporate MIBs on these codecs, and SNMP is off by
default on RoomOS/CE (the enabled units were configured deliberately).

The only meaningful *numeric* signal available is therefore **uptime**
(``sysUpTime``); reachability and identity (``sysDescr``) are already captured by
the probe stage. Each vendor gets its own handler class so that, once vendor MIBs
are available, brand-specific OIDs can be added without disturbing the others —
following the Class -> Brand -> Model dispatch used across the factory.

Normalised output key:

    uptime_seconds   MIB-II sysUpTime (s)
"""

from __future__ import annotations

from abc import ABC

from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler
from avtools.snmp.handlers.parsing import as_int, ticks_to_seconds, values_from_varbinds

# MIB-II sysUpTime.0 (TimeTicks) — the one reliable signal on these codecs.
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"


class AbstractCodec(AbstractDeviceHandler, ABC):
    """Base class for video-codec SNMP handlers.

    Provides the MIB-II uptime read shared by every codec vendor. Concrete
    vendor subclasses exist for future divergence (vendor MIBs) and for clear
    routing/labelling; today they share this implementation.
    """

    DEFAULT_TIMEOUT: float = 1.5
    DEFAULT_RETRIES: int = 1

    def fetch_stats(self) -> dict[str, object]:
        """Fetch MIB-II uptime from the codec.

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


class CiscoCodec(AbstractCodec):
    """Cisco/Tandberg TelePresence codec (SX/C-series, Room Kit, MXP)."""


class PolycomCodec(AbstractCodec):
    """Polycom video endpoint."""


class RadvisionCodec(AbstractCodec):
    """Radvision / Avaya Scopia video endpoint."""
