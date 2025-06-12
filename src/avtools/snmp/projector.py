"""
Module for querying Epson network projectors via SNMP.

Provides:
- ProjectorStats: dataclass container for projector status values.
- EpsonProjectorSNMP: SNMP client for fetching stats from Epson projectors.
"""

from __future__ import annotations

from dataclasses import dataclass

from pysnmp.hlapi import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
)


@dataclass
class ProjectorStats:
    """
    Data container for Epson projector statistics fetched via SNMP.

    Attributes:
        uptime (str): Time since device last reboot, as returned by sysUpTimeInstance.
        firmware (str): Current firmware version string.
        lamp_hours (int): Total lamp-on time in hours.
        power_status (int): Current power state (1=On, 2=Off, 3=Standby).
    """

    uptime: str
    firmware: str
    lamp_hours: int
    power_status: int


class EpsonProjectorSNMP:
    """
    SNMP client for Epson network projectors.

    Provides a method to fetch key status values in a single SNMP GET.

    Attributes:
        host (str): Hostname or IP address of the projector.
        community (str): SNMP community string.
        port (int): SNMP port number (default 161).
    """

    OIDS: dict[str, str] = {
        "uptime": "1.3.6.1.2.1.1.3.0",
        "firmware": "1.3.6.1.4.1.1248.4.1.1.1.8.0",
        "lamp_hours": "1.3.6.1.4.1.1248.4.1.1.1.1.0",
        "power_status": "1.3.6.1.4.1.1248.4.1.1.1.7.0",
    }

    def __init__(
        self,
        host: str,
        community: str = "public",
        port: int = 161,
    ) -> None:
        """
        Initialize the SNMP client for an Epson projector.

        Args:
            host (str): Hostname or IP address of the projector.
            community (str, optional): SNMP community string. Defaults to 'public'.
            port (int, optional): SNMP port number. Defaults to 161.
        """
        self.host: str = host
        self.community: str = community
        self.port: int = port

    def fetch_stats(self) -> ProjectorStats:
        """
        Poll the SNMP agent on the projector and return key status metrics.

        Performs a single SNMP GET request for:
          - sysUpTimeInstance
          - epIMFirmwareVersion
          - epIMLampTimer
          - epIMPowerStatus

        Returns:
            ProjectorStats: Container with fetched values.

        Raises:
            RuntimeError: If the SNMP engine or protocol returns an error.
        """
        # Build SNMP GET request for all configured OIDs
        object_types = [ObjectType(ObjectIdentity(oid)) for oid in self.OIDS.values()]
        error_ind, error_stat, error_idx, var_binds = next(
            getCmd(
                SnmpEngine(),
                CommunityData(self.community, mpModel=1),
                UdpTransportTarget((self.host, self.port)),
                ContextData(),
                *object_types,
            )
        )

        # Handle SNMP engine errors
        if error_ind:
            raise RuntimeError(f"SNMP engine error: {error_ind}")
        # Handle SNMP protocol errors at specific OID
        if error_stat:
            idx = int(error_idx) - 1
            key = list(self.OIDS.keys())[idx]
            raise RuntimeError(f"{key} lookup failed: {error_stat.prettyPrint()}")

        # Extract values and map into dataclass
        vals = [vb[1] for vb in var_binds]
        uptime_str = vals[0].prettyPrint()
        firmware_str = vals[1].prettyPrint().strip('"')
        lamp_hours_val = int(vals[2])
        power_status_val = int(vals[3])

        return ProjectorStats(
            uptime=uptime_str,
            firmware=firmware_str,
            lamp_hours=lamp_hours_val,
            power_status=power_status_val,
        )
