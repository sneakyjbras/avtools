from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from typing import ClassVar, List, Tuple

from pysnmp.hlapi import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
)

from avtools.influx.data.projector_stats import EpsonProjectorStats, ProjectorStats
from avtools.io.logger import system_logger
from avtools.landb.client import LanDBDevice
from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler


class AbstractProjector(AbstractDeviceHandler):
    """
    Abstract base class for network-connected projectors queried via SNMP.

    Subclasses must:
      1. Define a class-level OIDS mapping of stat names to SNMP OID strings.
      2. Implement `fetch_stats()` to perform an SNMP GET/GETNEXT on those OIDs
         and convert the results into a ProjectorStats instance.
    """

    # Mapping of stat key → SNMP OID. Example in subclasses:
    # OIDS: ClassVar[Mapping[str, str]] = {
    #     "uptime": "...",
    #     "lamp_hours": "...",
    #     ...
    # }
    OIDS: ClassVar[Mapping[str, str]]

    def __init__(self, target: LanDBDevice) -> None:
        """
        Initialize with connection parameters sourced from a LanDBDevice.

        Args:
            target (LanDBDevice): Must have `.ip` (str), `.community` (str), and
                                  `.port` (int, defaults to 161).
        """
        super().__init__(ip=target.ip)

    @abstractmethod
    def fetch_stats(self) -> ProjectorStats:
        """
        Poll the SNMP agent for all OIDs defined in `self.OIDS` and return
        parsed metrics.

        Returns:
            ProjectorStats: A dataclass containing projector metrics.

        Raises:
            RuntimeError: On SNMP engine errors or individual OID failures.
        """
        ...


class EpsonProjector(AbstractProjector):
    """
    SNMP client for Epson network projectors.

    Provides a single‐shot GET of key projector status values.
    """

    OIDS: ClassVar[Mapping[str, str]] = {
        "uptime": "1.3.6.1.2.1.1.3.0",
        "firmware": "1.3.6.1.4.1.1248.4.1.1.1.8.0",
        "lamp_hours": "1.3.6.1.4.1.1248.4.1.1.1.1.0",
        "power_status": "1.3.6.1.4.1.1248.4.1.1.1.9.0",
    }

    def __init__(self, target: LanDBDevice) -> None:
        """
        Initialize with the LANDBDevice containing SNMP connection info.

        Args:
            target (LanDBDevice): Must have `.ip`, `.community`, and `.port`.
        """
        super().__init__(target)

    def fetch_stats(self) -> EpsonProjectorStats:
        """
        Perform an SNMP GET for all configured OIDs and return parsed stats.

        Returns:
            EpsonProjectorStats
        Raises:
            RuntimeError: on SNMP engine error or individual OID lookup failure.
        """
        # 1. Prepare the list of ObjectType binders
        object_types: list[ObjectType] = [
            ObjectType(ObjectIdentity(oid)) for oid in self.OIDS.values()
        ]

        # 2. Kick off the SNMP GET command — it returns an iterator yielding
        #    (errorIndication, errorStatus, errorIndex, varBinds)
        snmp_result: Iterator[
            tuple[
                Optional[Any],  # errorIndication
                Any,  # errorStatus
                int,  # errorIndex
                Sequence[tuple[ObjectName, ObjectSyntax]],  # varBinds
            ]
        ] = getCmd(
            self.engine,
            CommunityData(self.community, mpModel=1),
            UdpTransportTarget((self.ip, self.port)),
            ContextData(),
            *object_types,
        )

        # 3. Pull the first (and only) response
        error_indication, error_status, error_index, var_binds = next(snmp_result)

        # 4. Error handling
        if error_indication:
            raise RuntimeError(f"SNMP engine error: {error_indication}")
        if error_status:
            failed_key = list(self.OIDS.keys())[error_index - 1]
            raise RuntimeError(
                f"{failed_key} lookup failed: {error_status.prettyPrint()}"
            )

        # 5. Extract the raw values from the var_binds
        values: Sequence[ObjectSyntax] = [vb[1] for vb in var_binds]

        # 6. Convert each to the appropriate Python type
        uptime: str = values[0].prettyPrint()
        firmware: str = values[1].prettyPrint().strip('"')
        lamp_hours: int = int(values[2])

        # power_status comes back as an OCTET STRING like b'01 0000 0000 T1'
        raw_pw: ObjectSyntax = values[3]
        try:
            power_status: str = raw_pw.prettyPrint()
        except AttributeError:
            power_status = str(raw_pw)

        # 7. Return the dataclass
        return EpsonProjectorStats(
            uptime=uptime,
            firmware=firmware,
            lamp_hours=lamp_hours,
            power_status=power_status,
        )
