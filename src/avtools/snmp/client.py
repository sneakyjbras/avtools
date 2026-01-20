from __future__ import annotations

from asyncio import to_thread
from datetime import datetime, timezone
from platform import system as _system
from subprocess import STDOUT, CalledProcessError, check_output
from typing import Any

import structlog
from landb_rest_client.models import Device
from pysnmp.hlapi import SnmpEngine

from avtools.snmp.factories.device_factory import DeviceHandlerFactory

logger = structlog.get_logger(__name__)


class SNMPClient:
    """
    Async-friendly SNMP client that does no internal threading.

    This client operates on the *upstream* LanDB REST client's `Device` model
    (or any subclass of it, e.g. `CachedDevice`). Network operations (ping/SNMP)
    only require the parent class fields (most importantly `ip`).

    Each async method loops its targets one by one, offloading blocking calls via
    asyncio.to_thread. All measurement methods use _build_point for consistency.
    """

    PING_TIMEOUT: int = 3  # seconds per ping

    # Mapping: InfluxDB tag key (lowercase, no underscores) -> possible Device attribute names.
    #
    # LanDB's upstream model has evolved (e.g. primary identifier may be `name` vs `equipment_no`).
    # We keep tags stable for InfluxDB while resolving values from whichever attribute exists.
    TAG_KEYS: dict[str, tuple[str, ...]] = {
        "equipmentno": ("equipment_no", "equipmentno", "code", "name"),
        "serialnumber": ("serial_number", "serialnumber"),
        "eqclass": ("eq_class", "eqclass", "class_code", "class"),
        "manufacturer": ("manufacturer", "manufacturer_code", "vendor", "brand"),
        "ip": ("ip", "ip_address", "ipaddr"),
    }

    def __init__(
        self,
        targets: list[Device],
        snmp_engine: SnmpEngine | None = None,
    ) -> None:
        self.targets = targets
        self.engine = snmp_engine or SnmpEngine()

        # Pre-create SNMP handlers
        self.handlers: dict[str, Any] = {}
        self.device_map: dict[str, Device] = {}

        for device in targets:
            ip = self._get(device, *self.TAG_KEYS["ip"])
            if not ip:
                logger.warning("Skipping device with no IP", device=device)
                continue

            ip = str(ip)
            self.device_map[ip] = device

            handler = DeviceHandlerFactory(device).create()
            if handler:
                handler.set_engine(self.engine)
                self.handlers[ip] = handler
            else:
                logger.warning("Failed to create handler", ip=ip)

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _get(obj: Any, *names: str) -> Any:
        """Return the first non-empty attribute/key found among `names`."""
        for n in names:
            if isinstance(obj, dict) and n in obj and obj[n] not in ("", None):
                return obj[n]
            if hasattr(obj, n):
                v = getattr(obj, n)
                if v not in ("", None):
                    return v
        return None

    def _build_point(
        self, measurement: str, device: Device, fields: dict[str, Any]
    ) -> dict[str, Any]:
        tags: dict[str, str] = {}

        for influx_key, candidates in self.TAG_KEYS.items():
            value = self._get(device, *candidates)
            if value not in ("", None):
                tags[influx_key] = str(value)

        return {
            "measurement": measurement,
            "time": datetime.now(timezone.utc).isoformat(),
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

    # --- Public API ----------------------------------------------------------

    async def collect_ping(self) -> list[dict[str, Any]]:
        """Sequentially ping each target, offloading to threads one at a time."""
        logger.info("Starting ICMP pinging", targets=len(self.targets))
        points: list[dict[str, Any]] = []

        for dev in self.targets:
            ip = self._get(dev, *self.TAG_KEYS["ip"])
            if not ip:
                points.append(self._build_point("ping_check", dev, {"status": 0}))
                continue

            rtt = await to_thread(self._ping_host, str(ip), self.PING_TIMEOUT)
            status = 1 if rtt is not None else 0
            fields: dict[str, Any] = {"status": status}
            if rtt is not None:
                fields["rtt_ms"] = round(rtt, 2)
            points.append(self._build_point("ping_check", dev, fields))

        logger.info("Ping completed", points=len(points), targets=len(self.targets))
        return points

    async def collect_snmp_probe(self) -> tuple[list[dict[str, Any]], list[Device]]:
        """
        Sequentially probe sysUpTime on each handler, offloading each to a thread.

        Returns (points, alive_devices).
        """
        logger.info("Starting SNMP probing", targets=len(self.handlers))
        points: list[dict[str, Any]] = []
        alive: list[Device] = []

        for ip, handler in self.handlers.items():
            try:
                ok = await to_thread(handler.probe)
            except Exception as e:
                logger.error("Probe error", ip=ip, error=str(e), exc_info=True)
                ok = False

            device = self.device_map[ip]
            status = 1 if ok else 0
            points.append(self._build_point("snmp_probe", device, {"status": status}))
            if ok:
                alive.append(device)

        logger.info("SNMP probing completed", points=len(points), alive=len(alive))
        return points, alive

    async def collect_snmp_query(
        self, alive: list[Device] | None = None
    ) -> list[dict[str, Any]]:
        """
        Sequentially fetch full SNMP stats for each alive device,
        offloading each fetch_stats call to a thread.
        """
        if alive is None:
            _, alive = await self.collect_snmp_probe()

        logger.info("Starting SNMP queries", targets=len(alive))
        points: list[dict[str, Any]] = []

        for dev in alive:
            ip = self._get(dev, *self.TAG_KEYS["ip"])
            if not ip:
                continue

            handler = self.handlers.get(str(ip))
            if not handler:
                continue

            try:
                stats = await to_thread(handler.fetch_stats)
            except Exception as e:
                logger.error("Stats error", ip=str(ip), error=str(e), exc_info=True)
                continue

            if not stats:
                continue

            fields = stats.to_human()
            points.append(self._build_point("snmp_query", dev, fields))

        logger.info("SNMP querying completed", points=len(points), targets=len(alive))
        return points
