from __future__ import annotations

import math
from asyncio import TaskGroup
from asyncio import run as asyncio_run
from asyncio import to_thread
from collections.abc import Callable, Sequence
from time import time
from typing import Any, Dict, List, Optional, Tuple, TypeVar

from cern_oauthlib.cern_session import ServiceAuthSession
from pydantic import BaseModel
from requests.auth import HTTPBasicAuth

from avtools.eam.client import EAMClient, EAMDevice, EAMPosition
from avtools.exception.errors import NoRecordsFound
from avtools.influx.client import InfluxClient
from avtools.io.logger import system_logger
from avtools.landb.client import LanDBClient, LanDBConfig, LanDBDevice
from avtools.postgres.client import PostgresClient
from avtools.snmp.client import SNMPClient

Model = TypeVar("Model", bound=BaseModel)

Point = dict[str, Any]


class AVTools:
    """
    Main class to interact with AV Tools systems, including EAM and LanDB.
    Provides methods to synchronize device records and collect SNMP metrics.
    """

    def __init__(
        self,
        dbod_url: str,
        logs: bool = False,
    ) -> None:
        """Create an AVTools instance backed by the given DBOD Postgres URL."""
        self.dbod_helper: PostgresClient = PostgresClient(dbod_url)
        self.logs: bool = logs

    def run_eam(self, username: str, password: str) -> None:
        """Run both EAM syncs (devices and positions) using HTTP basic auth."""
        auth = HTTPBasicAuth(username, password)
        self.sync_eam_devices(auth)
        self.sync_eam_positions(auth)

    def sync_eam_devices(self, auth: HTTPBasicAuth) -> None:
        """Sync EAM devices from the API into the local cache."""
        eam_helper = EAMClient(auth)
        total = eam_helper.get_number_av_assets()
        eam_list: list[EAMDevice] = eam_helper.get_device_list(total)
        cache_list: list[EAMDevice] = self.dbod_helper.get_all_eam_devices()

        self._sync_entities(
            api_items=eam_list,
            cached_items=cache_list,
            # NOTE: use the Python attribute name (snake_case), not the alias
            get_id=lambda d: d.equipment_no,
            sync_func=self.dbod_helper.sync_eam_devices,
            name="EAM Devices",
        )

    def sync_eam_positions(self, auth: HTTPBasicAuth) -> None:
        """Sync EAM positions from the API into the local cache."""
        eam_helper = EAMClient(auth)
        total: int = eam_helper.get_number_av_positions()
        eam_list: list[EAMPosition] = eam_helper.get_positions_list(total)
        cache_list: list[EAMPosition] = self.dbod_helper.get_all_eam_positions()

        self._sync_entities(
            api_items=eam_list,
            cached_items=cache_list,
            # Same here: snake_case attribute name
            get_id=lambda d: d.equipment_no,
            sync_func=self.dbod_helper.sync_eam_positions,
            name="EAM Positions",
        )

    def run_landb(
        self,
        client_id: str,
        client_secret: str,
        audience: str,
        max_workers: int = 8,
    ) -> None:
        """Sync LanDB devices for cached EAM devices using an Auth0-backed session."""
        eam_list: list[EAMDevice] = self.dbod_helper.get_all_eam_devices()
        if not eam_list:
            system_logger.info("No EAM devices—skipping LanDB sync.")
            return

        session = ServiceAuthSession(
            client_id=client_id, client_secret=client_secret, audience=audience
        )
        self._ensure_token(session)

        landb_list: list[LanDBDevice] = asyncio_run(
            self._get_landb_devices(eam_list, session, max_workers)
        )

        cache_list: list[LanDBDevice] = self.dbod_helper.get_all_landb_devices()

        self._sync_entities(
            api_items=landb_list,
            cached_items=cache_list,
            get_id=lambda d: d.equipmentno,
            sync_func=self.dbod_helper.sync_landb_devices,
            name="LanDB",
        )

    def run_influx_snmp(
        self,
        influx_host: str,
        influx_port: int,
        influx_user: str,
        influx_password: str,
        influx_db: str,
        max_workers: int = 8,
    ) -> None:
        # 1) Load devices
        devices: list[LanDBDevice] = self.dbod_helper.get_all_landb_devices()
        total = len(devices)
        if not devices:
            system_logger.info("No LanDB devices to monitor.")
            return

        system_logger.info(f"Fetching {total} LanDB devices with {max_workers} tasks.")

        # 2) Fetch all SNMP points asynchronously
        all_points = asyncio_run(self._get_snmp_points(devices, max_workers))

        # 3) Publish them
        self._publish_snmp(
            all_points,
            influx_host,
            influx_port,
            influx_user,
            influx_password,
            influx_db,
        )

    async def _get_landb_devices(
        self,
        eam_records: list[EAMDevice],
        session: ServiceAuthSession,
        max_workers: int = 8,
    ) -> list[LanDBDevice]:
        """Fetch LanDB devices concurrently for the given EAM records."""
        total = len(eam_records)
        if total == 0:
            system_logger.info("No EAM records provided; nothing to fetch.")
            return []

        num_tasks = min(max_workers, total)
        system_logger.info(f"Fetching {total} LanDB devices with {num_tasks} tasks.")

        # preallocate results
        result: list[LanDBDevice | None] = [None] * total
        chunk = (total + num_tasks - 1) // num_tasks

        async def process_slice(start: int, end: int) -> None:
            helper = LanDBClient(session=session)
            for idx in range(start, end):
                rec = eam_records[idx]
                try:
                    dev = await to_thread(
                        helper.get_data,
                        rec.equipment_no,
                        rec.serial_number,
                        rec.eq_class,
                        rec.commission_date,
                    )
                except Exception as e:
                    system_logger.error(f"Error fetching {rec.equipment_no}: {e}")
                    continue
                if dev and dev.serialnumber and dev.ip is not None:
                    result[idx] = dev

        # use TaskGroup for clean task management
        async with TaskGroup() as tg:
            for i in range(num_tasks):
                start = i * chunk
                end = min(start + chunk, total)
                tg.create_task(process_slice(start, end))

        # filter out failed lookups
        devices: list[LanDBDevice] = [d for d in result if d is not None]  # type: ignore
        system_logger.info(f"Fetched {len(devices)} of {total} LanDB devices.")
        return devices

    def _sync_entities(
        self,
        api_items: Sequence[Model],
        cached_items: Sequence[Model],
        get_id: Callable[[Model], str],
        sync_func: Callable[..., None],
        name: str,
    ) -> None:
        """Generic reconciliation helper used by the sync_* methods."""
        start_time = time()

        if not cached_items:
            sync_func(to_insert=api_items, to_update=[], to_delete=[])
            system_logger.info(
                f"{name} sync (first run): inserted={len(api_items)}, updated=0, deleted=0"
            )
            return

        cache_map: dict[str, Model] = {get_id(item): item for item in cached_items}
        api_map: dict[str, Model] = {get_id(item): item for item in api_items}
        cache_ids = set(cache_map.keys())
        api_ids = set(api_map.keys())

        to_delete: list[str] = list(cache_ids - api_ids)
        to_insert: list[Model] = [api_map[i] for i in api_ids - cache_ids]
        to_update: list[tuple[Model, dict[str, Any]]] = []

        for eid in api_ids & cache_ids:
            old_item = cache_map[eid]
            new_item = api_map[eid]
            changes = self._diff_models(old_item, new_item)
            if changes:
                to_update.append((new_item, changes))

        sync_func(to_insert=to_insert, to_update=to_update, to_delete=to_delete)
        duration = time() - start_time
        system_logger.info(
            f"{name} sync completed in {duration:.2f}s: "
            f"inserted={len(to_insert)}, updated={len(to_update)}, deleted={len(to_delete)}"
        )

    async def _get_snmp_points(
        self,
        devices: list[LanDBDevice],
        max_workers: int,
    ) -> list[Point]:
        """Slice devices, ping/probe/query each chunk in parallel, return all points."""
        total = len(devices)
        chunk_size = math.ceil(total / max_workers)
        device_chunks = [
            devices[i : i + chunk_size] for i in range(0, total, chunk_size)
        ]

        async def worker_fn(chunk: list[LanDBDevice]) -> list[Point]:
            monitor = SNMPClient(targets=chunk)
            pts: list[Point] = []
            try:
                # ICMP ping
                ping_pts = await monitor.collect_ping()
                pts.extend(ping_pts)
                ok_ips = {
                    p["tags"]["ip"] for p in ping_pts if p["fields"].get("status") == 1
                }
                monitor.targets = [d for d in chunk if d.ip in ok_ips]

                # SNMP sysUpTime probe
                probe_pts, alive_devices = await monitor.collect_snmp_probe()
                pts.extend(probe_pts)
                monitor.targets = alive_devices

                # Full SNMP query
                query_pts = await monitor.collect_snmp_query(alive_devices)
                pts.extend(query_pts)

                system_logger.info(
                    f"Task on {len(chunk)} devices: "
                    f"{len(ping_pts)} ping, {len(probe_pts)} probe, {len(query_pts)} query points"
                )
            except Exception as e:
                system_logger.exception(f"Worker task error: {e}")
            return pts

        all_points: list[Point] = []
        tasks = []
        async with TaskGroup() as tg:
            for chunk in device_chunks:
                tasks.append(tg.create_task(worker_fn(chunk)))

        # threads have implicit barriers at the end
        for task in tasks:
            all_points.extend(task.result())

        return all_points

    def _publish_snmp(
        self,
        points: list[Point],
        influx_host: str,
        influx_port: int,
        influx_user: str,
        influx_password: str,
        influx_db: str,
    ) -> None:
        """Publish a list of InfluxDB points in one batch."""
        if not points:
            system_logger.info("No metrics collected; skipping write.")
            return

        publisher = InfluxClient(
            host=influx_host,
            port=influx_port,
            username=influx_user,
            password=influx_password,
            database=influx_db,
            ssl=True,
            verify_ssl=True,
        )
        try:
            publisher.write_points(points)
            system_logger.info(f"Wrote {len(points)} total points")
        except NoRecordsFound:
            system_logger.info("No points to write to InfluxDB.")
        except Exception as e:
            system_logger.exception(f"Failed writing points: {e}")

    def _diff_models(self, old: Model, new: Model) -> dict[str, Any]:
        """Return a mapping of fields that differ between two Pydantic models."""
        old_data: dict[str, Any] = old.dict()
        new_data: dict[str, Any] = new.dict(exclude_unset=True)
        return {k: v for k, v in new_data.items() if old_data.get(k) != v}

    def _ensure_token(
        self,
        session: ServiceAuthSession,
        config: LanDBConfig = LanDBConfig(),
    ) -> None:
        """Ensure the session has an access token, probing LanDB if needed."""
        token: Any = session.auth.token

        if token:
            expires_at = getattr(token, "expires_at", None)
            if expires_at:
                system_logger.info(
                    f"Using cached access token (expires at {expires_at})"
                )
            else:
                system_logger.info("Using cached access token")
            return

        # No token → hit the devices endpoint to force a token fetch
        probe_url = f"{config.base_url}/{config.device_endpoint}"
        system_logger.info(f"No access token found; probing {probe_url} to acquire one")
        try:
            resp = session.get(probe_url, params={"_limit": 1}, verify=True)
            if resp.ok:
                system_logger.info(
                    "Successfully obtained new access token via probe GET."
                )
            else:
                system_logger.error(
                    f"Probe GET failed [{resp.status_code}] when obtaining token."
                )
        except Exception as e:
            system_logger.error(f"Error during token-fetch probe: {e}")
