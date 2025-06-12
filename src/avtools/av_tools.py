from __future__ import annotations

from asyncio import TaskGroup
from asyncio import run as asyncio_run
from asyncio import to_thread
from collections.abc import Callable, Sequence
from time import time
from typing import Any, Dict, List, Optional, Tuple, TypeVar

from cern_oauthlib.cern_session import ServiceAuthSession
from pydantic import BaseModel
from requests.auth import HTTPBasicAuth

from avtools.db_helper import DBHelper
from avtools.eam_helper import EAMDevice, EAMHelper
from avtools.errors import NoRecordsFound
from avtools.influx_helper import InfluxHelper
from avtools.landb_helper import LanDBConfig, LanDBDevice, LanDBHelper
from avtools.logger import system_logger
from avtools.snmp_helper import SNMPHelper

Model = TypeVar("Model", bound=BaseModel)


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
        """
        Initialize AVTools with DBOD endpoint and optional logging.

        Args:
            dbod_url (str): URL for the DBOD service.
            logs (bool): Enable detailed logging if True.
        """
        self.dbod_helper: DBHelper = DBHelper(dbod_url)
        self.logs: bool = logs

    def run_eam(self, username: str, password: str) -> None:
        """
        Synchronize EAM devices with the local cache.

        Authenticates to the EAM API, retrieves all devices, and delegates
        to the generic sync routine.

        Args:
            username (str): EAM API username.
            password (str): EAM API password.
        """
        auth = HTTPBasicAuth(username, password)
        eam_helper = EAMHelper(auth)
        total: int = eam_helper.get_number_av_records()
        eam_list: list[EAMDevice] = eam_helper.get_device_list(total)
        cache_list: list[EAMDevice] = self.dbod_helper.get_all_eam_devices()

        self._sync_entities(
            api_items=eam_list,
            cached_items=cache_list,
            get_id=lambda d: d.equipmentno,
            sync_func=self.dbod_helper.sync_eam_devices,
            name="EAM",
        )

    def run_landb(
        self,
        client_id: str,
        client_secret: str,
        audience: str,
        concurrency: int = 8,
    ) -> None:
        """
        Synchronize LanDB devices using Auth0 client credentials.

        Uses your internal Auth0 service to exchange the provided client_id,
        client_secret, and audience for a LanDB API token, then:

        1. Fetches all EAM devices from the database cache.
        2. If none are found, logs and exits.
        3. Retrieves LanDB devices asynchronously against the EAM list.
        4. Loads current LanDB devices from the database cache.
        5. Calls the generic sync routine to update the database.

        Args:
            client_id (str): Auth0 Client ID for obtaining the LanDB API token.
            client_secret (str): Auth0 Client Secret for obtaining the LanDB API token.
            audience (str): Auth0 audience (API identifier) for the token request.
            concurrency (int): Max number of parallel ping tasks.
        """
        eam_list: list[EAMDevice] = self.dbod_helper.get_all_eam_devices()
        if not eam_list:
            system_logger.info("No EAM devices—skipping LanDB sync.")
            return

        session = ServiceAuthSession(
            client_id=client_id, client_secret=client_secret, audience=audience
        )
        self._ensure_token(session)

        landb_list: list[LanDBDevice] = asyncio_run(
            self._get_landb_devices(eam_list, session, concurrency)
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
        concurrency: int = 8,
    ) -> None:
        """
        Synchronize SNMP and ping metrics with InfluxDB.

        Fetches cached LanDBDevice entries, runs up to `concurrency` parallel
        ICMP ping checks and synchronous SNMP queries, then writes all collected
        points to InfluxDB.

        Args:
            influx_host (str):     InfluxDB host.
            influx_port (int):     InfluxDB port.
            influx_user (str):     InfluxDB username.
            influx_password (str): InfluxDB password.
            influx_db (str):       InfluxDB database name.
            concurrency (int):     Max number of parallel ping tasks.
        """
        # 1. Retrieve cached devices
        devices = self.dbod_helper.get_all_landb_devices()
        if not devices:
            return

        # 2. Prepare polling and publishing helpers
        monitor = SNMPHelper(targets=devices, concurrency=concurrency)
        publisher = InfluxHelper(
            host=influx_host,
            port=influx_port,
            username=influx_user,
            password=influx_password,
            database=influx_db,
            ssl=True,
            verify_ssl=True,
        )

        # 3. Define and run async workflow
        async def _run_once() -> None:
            ping_points = await monitor.ping_all()
            # snmp_points = monitor.snmp_all()
            # all_points = ping_points + snmp_points
            await publisher.write_points_async(ping_points)

        import asyncio

        asyncio.run(_run_once())

    async def _get_landb_devices(
        self,
        eam_records: list[EAMDevice],
        session: ServiceAuthSession,
        concurrency: int = 8,
    ) -> list[LanDBDevice]:
        """
        Fetch LanDB devices concurrently using asyncio.TaskGroup and chunking.

        Uses the provided authenticated session to query the LanDB API in parallel,
        breaking the EAM records into manageable chunks for efficient retrieval.

        Args:
            eam_records (List[EAMDevice]): Cached EAM devices to use as lookup keys.
            session (ServiceAuthSession): Authenticated service session for LanDB API requests.
            concurrency (int): Max number of parallel ping tasks.

        Returns:
            List[LanDBDevice]: All LanDB devices retrieved for the given EAM records.
        """
        total = len(eam_records)
        if total == 0:
            system_logger.info("No EAM records provided; nothing to fetch.")
            return []

        num_tasks = min(concurrency, total)
        system_logger.info(f"Fetching {total} LanDB devices with {num_tasks} tasks.")

        # preallocate results
        result: list[LanDBDevice | None] = [None] * total
        chunk = (total + num_tasks - 1) // num_tasks

        async def process_slice(start: int, end: int) -> None:
            helper = LanDBHelper(session=session)
            for idx in range(start, end):
                rec = eam_records[idx]
                try:
                    dev = await to_thread(
                        helper.get_data,
                        rec.equipmentno,
                        rec.serialnumber,
                        rec.eqclass,
                    )
                except Exception as e:
                    system_logger.error(f"Error fetching {rec.equipmentno}: {e}")
                    continue
                if dev and dev.serial_number and dev.ip is not None:
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
        """
        Generic synchronization routine for any entity type.

        Compares `api_items` against `cached_items`, computes inserts,
        updates, and deletes, and invokes `sync_func` to persist changes.

        Args:
            api_items (Sequence[Model]): Items fetched from external API.
            cached_items (Sequence[Model]): Items currently in local cache.
            get_id (Callable[[Model], str]): Function to extract unique ID.
            sync_func (Callable[..., None]): DB helper method to apply changes.
            name (str): Human-readable label for logging (e.g., "EAM").
        """
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

    def _diff_models(self, old: Model, new: Model) -> dict[str, Any]:
        """
        Compute field-by-field differences between two Pydantic-like models.

        Args:
            old (Model): The original model instance.
            new (Model): The updated (possibly partial) model instance.

        Returns:
            Dict[str, Any]: A mapping of fields present in `new` whose values differ from `old`.
        """
        old_data: dict[str, Any] = old.dict()
        new_data: dict[str, Any] = new.dict(exclude_unset=True)
        return {k: v for k, v in new_data.items() if old_data.get(k) != v}

    def _ensure_token(
        self,
        session: ServiceAuthSession,
        config: LanDBConfig = LanDBConfig(),
    ) -> None:
        """
        Ensure that session.auth.token is populated by the CERN OAuth wrapper.
        If no token exists, fire a dummy GET against the LanDB devices endpoint
        (which under the hood will trigger the token fetch).

        Args:
            session: Authenticated ServiceAuthSession to verify.
            config:  LanDBConfig holding base_url & endpoints.
        """
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
