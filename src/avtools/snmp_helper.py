from __future__ import annotations

from asyncio import Semaphore, TaskGroup, get_running_loop
from datetime import datetime
from platform import system as _system
from subprocess import STDOUT, CalledProcessError, check_output
from typing import Any, Dict, List, Optional

from pysnmp.hlapi import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
)

from avtools.landb_helper import LanDBDevice
from avtools.logger import system_logger


class SNMPHelper:
    """
    SNMPHelper class for polling network devices asynchronously (via system ping + SNMP).
    Uses asyncio.Semaphore and TaskGroup to limit and manage concurrency.
    Provides methods to generate InfluxDB‐style point dicts for both ping and SNMP checks.
    """

    PING_TIMEOUT: int = 3  # seconds per ping attempt

    def __init__(
        self,
        targets: list[LanDBDevice],
        concurrency: int = 8,
    ) -> None:
        """
        Initialize SNMPHelper.

        Args:
            targets: List of LanDBDevice instances to monitor.
            concurrency: Maximum number of concurrent ping tasks.
        """
        self.targets: list[LanDBDevice] = targets
        self.concurrency: int = concurrency

    async def ping_all(self) -> list[dict[str, Any]]:
        """
        Asynchronously ping all targets with limited concurrency.

        Returns:
            A list of InfluxDB‐style point dicts for measurement "ping_check".
        """
        total = len(self.targets)
        system_logger.info(
            f"Starting ping_all for {total} targets (concurrency={self.concurrency})"
        )

        sem: Semaphore = Semaphore(self.concurrency)
        points: list[dict[str, Any]] = []

        async with TaskGroup() as tg:
            for target in self.targets:
                tg.create_task(self._ping_target(target, sem, points))

        system_logger.info(
            f"Finished ping_all: collected {len(points)}/{total} pong responses"
        )
        return points

    async def _ping_target(
        self,
        target: LanDBDevice,
        sem: Semaphore,
        points: list[dict[str, Any]],
    ) -> None:
        """
        Ping a single device under semaphore control, then build
        an InfluxDB point and append to the shared list.

        Args:
            target: LanDBDevice with attributes like .ip, .equipmentno, etc.
            sem: Asyncio.Semaphore limiting parallel pings.
            points: Shared output list for resulting point dicts.
        """
        async with sem:
            loop = get_running_loop()
            # delegate to subprocess-based ping, returns ms if OK or None
            rtt_ms: float | None = await loop.run_in_executor(
                None,
                self._ping_host,
                target.ip,
                self.PING_TIMEOUT,
            )
            status: int = 1 if rtt_ms is not None else 0
            fields: dict[str, Any] = {"status": status}
            if rtt_ms is not None:
                fields["rtt_ms"] = round(rtt_ms, 2)

            point: dict[str, Any] = {
                "measurement": "ping_check",
                "time": datetime.utcnow().isoformat(),
                "tags": {
                    "equipmentno": target.equipmentno,
                    "serial_number": target.serial_number,
                    "name": target.name,
                    "manufacturer": target.manufacturer,
                    "building": target.building,
                    "floor": target.floor,
                    "room": target.room,
                    "ip": target.ip,
                },
                "fields": fields,
            }
            points.append(point)

    def _ping_host(self, ip: str, timeout: int) -> float | None:
        """
        Perform a system ping to the given IP/hostname.

        Returns the round-trip time in milliseconds if the ping succeeds,
        or None on failure.

        Args:
            ip: IP address or hostname to ping.
            timeout: Timeout per ping in seconds.
        """
        cmd: list[str]
        if _system().lower() == "windows":
            # on Windows: -n <count>, -w <timeout in ms>
            cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), ip]
        else:
            # on *nix: -c <count>, -W <timeout in seconds>
            cmd = ["ping", "-c", "1", "-W", str(timeout), ip]

        try:
            output = check_output(cmd, stderr=STDOUT, text=True)
        except CalledProcessError:
            return None

        # Parse for something like "time=12.3 ms" or "time<1 ms"
        for token in output.split():
            if token.startswith("time=") or token.startswith("time<"):
                # extract the numeric part and convert to float
                num = token.split("time=")[-1].split("time<")[-1].replace("ms", "")
                try:
                    return float(num)
                except ValueError:
                    continue
        return None

    def snmp_get(self, target: LanDBDevice) -> dict[str, str]:
        """
        Perform a synchronous SNMP GET for each OID in target.snmp_oids.

        Args:
            target: LanDBDevice with .community, .ip, and .snmp_oids attributes.

        Returns:
            A dict mapping each OID (dots replaced with "_") to its retrieved value.
        """
        results: dict[str, str] = {}
        for oid in target.snmp_oids:
            errInd, errStat, _, varBinds = next(
                getCmd(
                    SnmpEngine(),
                    CommunityData(target.community, mpModel=0),
                    UdpTransportTarget(
                        (target.ip, 161), timeout=self.PING_TIMEOUT, retries=1
                    ),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
            )
            if errInd or errStat:
                continue
            for oid_str, value in varBinds:
                key: str = str(oid_str).replace(".", "_")
                results[key] = str(value)
        return results

    def snmp_all(self) -> list[dict[str, Any]]:
        """
        Perform SNMP GET on all targets and return InfluxDB‐style points.

        Returns:
            A list of point dicts for measurement "snmp_check".
        """
        points: list[dict[str, Any]] = []
        timestamp: str = datetime.utcnow().isoformat()
        for target in self.targets:
            data: dict[str, str] = self.snmp_get(target)
            if not data:
                continue
            point: dict[str, Any] = {
                "measurement": "snmp_check",
                "time": timestamp,
                "tags": {"host": target.ip},
                "fields": data,
            }
            points.append(point)
        return points
