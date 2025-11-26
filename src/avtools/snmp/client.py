from __future__ import annotations

from asyncio import to_thread
from datetime import datetime
from platform import system as _system
from subprocess import STDOUT, CalledProcessError, check_output
from typing import Any

import structlog
from pysnmp.hlapi import SnmpEngine

from avtools.landb.device import LanDBDevice
from avtools.snmp.factories.device_factory import DeviceHandlerFactory

logger = structlog.get_logger(__name__)


class SNMPClient:
    """
    Async-friendly SNMP client that does no internal threading.

    Each async method loops its targets one by one,
    offloading blocking calls via asyncio.to_thread.
    All measurement methods use _build_point for consistency.
    """

    PING_TIMEOUT: int = 3  # seconds per ping

    # Mapping: InfluxDB tag key (lowercase, no underscores) -> LanDBDevice attribute (snake_case)
    TAG_KEYS: dict[str, str] = {
        "equipmentno": "equipment_no",
        "serialnumber": "serial_number",
        "eqclass": "eq_class",
        "manufacturer": "manufacturer",
        "ip": "ip",
    }

    def __init__(
        self,
        targets: list[LanDBDevice],
        snmp_engine: SnmpEngine | None = None,
    ) -> None:
        self.targets = targets
        self.engine = snmp_engine or SnmpEngine()

        # Pre-create SNMP handlers
        self.handlers: dict[str, Any] = {}
        self.device_map: dict[str, LanDBDevice] = {}
        for device in targets:
            self.device_map[device.ip] = device
            handler = DeviceHandlerFactory(device).create()
            if handler:
                handler.set_engine(self.engine)
                self.handlers[device.ip] = handler
            else:
                logger.warning(f"Failed to create handler for {device.ip}")

    def _build_point(
        self, measurement: str, device: LanDBDevice, fields: dict[str, Any]
    ) -> dict[str, Any]:
        tags = {
            influx_key: getattr(device, attr_name)
            for influx_key, attr_name in self.TAG_KEYS.items()
        }

        return {
            "measurement": measurement,
            "time": datetime.utcnow().isoformat(),
            "tags": tags,
            "fields": fields,
        }

    def _ping_host(self, ip: str, timeout: int) -> float | None:
        """Blocking helper—wrapped in asyncio.to_thread."""
        if _system().lower() == "windows":
            cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), ip]
        else:
            cmd = ["ping", "-c", "1", "-W", str(timeout), ip]
        try:
            out = check_output(cmd, stderr=STDOUT, text=True)
        except CalledProcessError:
            return None

        for tok in out.split():
            if tok.startswith(("time=", "time<")):
                num = tok.replace("time=", "").replace("time<", "").replace("ms", "")
                try:
                    return float(num)
                except ValueError:
                    continue
        return None

    async def collect_ping(self) -> list[dict[str, Any]]:
        """Sequentially ping each target, offloading to threads one at a time."""
        logger.info(f"Starting ICMP pinging for {len(self.targets)} targets")
        points: list[dict[str, Any]] = []

        for dev in self.targets:
            rtt = await to_thread(self._ping_host, dev.ip, self.PING_TIMEOUT)
            status = 1 if rtt is not None else 0
            fields: dict[str, Any] = {"status": status}
            if rtt is not None:
                fields["rtt_ms"] = round(rtt, 2)
            points.append(self._build_point("ping_check", dev, fields))

        logger.info(f"Ping completed: {len(points)}/{len(self.targets)}")
        return points

    async def collect_snmp_probe(
        self,
    ) -> tuple[list[dict[str, Any]], list[LanDBDevice]]:
        """
        Sequentially probe sysUpTime on each handler, offloading each to a thread.

        Returns (points, alive_devices).
        """
        logger.info(f"Starting SNMP probing for {len(self.handlers)} targets")
        points: list[dict[str, Any]] = []
        alive: list[LanDBDevice] = []

        for ip, handler in self.handlers.items():
            try:
                ok = await to_thread(handler.probe)
            except Exception as e:
                logger.error(f"Probe error {ip}: {e}", exc_info=True)
                ok = False

            device = self.device_map[ip]
            status = 1 if ok else 0
            points.append(self._build_point("snmp_probe", device, {"status": status}))
            if ok:
                alive.append(device)

        logger.info(f"SNMP probing completed: {len(points)} points, {len(alive)} alive")
        return points, alive

    async def collect_snmp_query(
        self, alive: list[LanDBDevice] | None = None
    ) -> list[dict[str, Any]]:
        """
        Sequentially fetch full SNMP stats for each alive device,
        offloading each fetch_stats call to a thread.
        """
        if alive is None:
            _, alive = await self.collect_snmp_probe()

        logger.info(f"Starting SNMP queries for {len(alive)} targets")
        points: list[dict[str, Any]] = []

        for dev in alive:
            handler = self.handlers.get(dev.ip)
            if not handler:
                continue
            try:
                stats = await to_thread(handler.fetch_stats)
            except Exception as e:
                logger.error(f"Stats error {dev.ip}: {e}", exc_info=True)
                continue

            if not stats:
                continue

            fields = stats.to_human()
            points.append(self._build_point("snmp_query", dev, fields))

        logger.info(f"SNMP querying completed: {len(points)}/{len(alive)}")
        return points
