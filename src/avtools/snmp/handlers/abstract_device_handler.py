"""Abstract base class for SNMP handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, Union

from pysnmp.hlapi import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
)

_SYS_UPTIME_OID = "1.3.6.1.2.1.1.3.0"  # sysUpTime.0 (mandatory on all agents)
_SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"  # sysDescr.0 (mandatory on all agents)


class AbstractDeviceHandler(ABC):
    """
    Base class for all SNMP device handlers.

    This class stores the connection parameters shared by every handler and
    provides a lightweight `probe` method that verifies whether the SNMP agent
    is reachable.  Subclasses must supply their own implementation of
    :meth:`fetch_stats`.

    Args:
        ip (str): IP address or DNS name of the target device.
        community (str): SNMP v2c community string. Defaults to ``"public"``.
        port (int, optional): UDP port on which the agent listens. Defaults
            to ``161``.
    """

    def __init__(self, ip: str, community: str = "public", port: int = 161) -> None:
        """Initialize a base SNMP handler with connection parameters."""
        self.ip: str = ip
        self.community: str = community
        self.port: int = port
        self.engine: SnmpEngine | None = None

    def set_engine(self, engine: SnmpEngine) -> None:
        """Attach a shared PySNMP engine to this handler."""
        self.engine = engine

    def _snmp_get(
        self,
        oids: Sequence[Union[str, ObjectIdentity]],
        *,
        timeout: float = 1.0,
        retries: int = 2,
    ):
        """Perform a single SNMP GET for multiple OIDs.

        Convenience wrapper so subclasses don't duplicate PySNMP boilerplate,
        and so we can reuse a shared engine when provided via `set_engine()`.
        """
        obj_types = [
            ObjectType(ObjectIdentity(oid) if isinstance(oid, str) else oid) for oid in oids
        ]

        iterator = getCmd(
            self.engine or SnmpEngine(),
            CommunityData(self.community, mpModel=1),  # SNMP v2c
            UdpTransportTarget((self.ip, self.port), timeout=timeout, retries=retries),
            ContextData(),
            *obj_types,
        )

        return next(iterator)

    def probe(
        self,
        oid: str = _SYS_UPTIME_OID,
        *,
        timeout: int = 1,
        retries: int = 2,
    ) -> bool:
        """
        Check whether the device responds to a simple SNMP GET request.
        """
        error_indication, error_status, *_ = self._snmp_get(
            [oid],
            timeout=timeout,
            retries=retries,
        )
        return not (error_indication or error_status)

    def fetch_sysdescr(
        self,
        oid: str = _SYS_DESCR_OID,
        *,
        timeout: float = 1.0,
        retries: int = 2,
    ) -> str | None:
        """Fetch sysDescr.0 (device description string).

        This is a generic identity-like field present on virtually all SNMP agents.
        It is intentionally **not** exported to time-series sinks; it is meant for
        Postgres monitoring tables (latest-known snapshot).

        Args:
            oid: OID to fetch (defaults to sysDescr.0).
            timeout: SNMP timeout in seconds.
            retries: SNMP retries.

        Returns:
            sysDescr string, or None if unavailable.
        """
        try:
            error_indication, error_status, *_rest = self._snmp_get(
                [oid],
                timeout=timeout,
                retries=retries,
            )
            if error_indication or error_status:
                return None

            # _snmp_get returns (errorIndication, errorStatus, errorIndex, varBinds)
            # where varBinds is typically a list of (ObjectName, ObjectValue).
            var_binds = _rest[-1] if _rest else None
            if not var_binds:
                return None

            vb0 = var_binds[0]
            val = None
            try:
                # Common case: tuple pair
                if isinstance(vb0, tuple) and len(vb0) == 2:
                    val = vb0[1]
                else:
                    # pysnmp ObjectType supports indexing
                    val = vb0[1]  # type: ignore[index]
            except Exception:
                # Best-effort fallback
                try:
                    val = getattr(vb0, "getComponentByPosition")(1)
                except Exception:
                    val = None

            if val is None:
                return None

            s = val.prettyPrint() if hasattr(val, "prettyPrint") else str(val)
            s = str(s).strip()
            return s or None
        except Exception:
            return None

    @abstractmethod
    def fetch_stats(self) -> Mapping[str, Any]:
        """
        Retrieve device-specific metrics.
        """
        ...
