from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

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
        self.ip: str = ip
        self.community: str = community
        self.port: int = port

    def set_engine(self, engine: SnmpEngine) -> None:
        """
        TODO: comment
        """
        self.engine = engine

    def probe(
        self,
        oid: str = _SYS_UPTIME_OID,
        *,
        timeout: int = 1,
        retries: int = 2,
    ) -> bool:
        """
        Check whether the device responds to a simple SNMP GET request.

        A successful response to the query (default: ``sysUpTime.0``) confirms
        that the network path, agent process and credentials are all valid.

        Args:
            oid (str): Object Identifier to query.  Defaults to
                ``1.3.6.1.2.1.1.3.0``.
            timeout (int): Seconds to wait for each reply.
            retries (int): How many attempts to make after the first timeout.

        Returns:
            bool: ``True`` if any valid SNMP response is received,
            ``False`` otherwise.
        """
        error_indication, error_status, *_ = next(
            getCmd(
                SnmpEngine(),
                CommunityData(self.community, mpModel=1),  # SNMP v2c
                UdpTransportTarget(
                    (self.ip, self.port),
                    timeout=timeout,
                    retries=retries,
                ),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )
        )
        return not (error_indication or error_status)

    @abstractmethod
    def fetch_stats(self) -> Mapping[str, Any]:
        """
        Retrieve device-specific metrics.

        Implementations should raise ``RuntimeError`` on unrecoverable SNMP
        errors.

        Returns:
            Mapping[str, Any]: A dictionary mapping metric names to values.
        """
        ...
