from __future__ import annotations

import math
from asyncio import TaskGroup
from asyncio import run as asyncio_run
from asyncio import to_thread
from collections.abc import Callable, Sequence
from time import time
from typing import Any, TypeVar

import structlog
from cern_oauthlib.cern_session import ServiceAuthSession
from eam_rest_client import Equipment
from eam_rest_client.credentials import register_credentials
from eam_rest_client.grid_query import GridQuery
from pydantic.v1 import BaseModel

from avtools.exception.errors import (  # noqa: F401 (may be used elsewhere)
    NoRecordsFound,
)
from avtools.influx.client import InfluxClient
from avtools.landb.client import LanDBClient
from avtools.landb.config import LanDBConfig
from avtools.landb.device import LanDBDevice
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
        """Initialize AVTools with DBOD endpoint and optional logging."""
        self.dbod_helper: PostgresClient = PostgresClient(dbod_url)
        self.logs: bool = logs
        self.logger = structlog.get_logger(self.__class__.__name__)

    def run_eam(
        self,
        username: str,
        password: str,
        base_url: str = "https://cmmsx.cern.ch/",
        *,
        asset_grid: str = "OSOBJA",
        position_grid: str = "OSOBJP",
        department_code: str = "AV",
        limit: int | None = None,
    ) -> None:
        """
        Register EAM credentials (HTTP Basic Auth behind the scenes) and trigger synchronization
        of devices and positions using `eam-rest-client`.

        Notes:
            - Service-account access requires the *service account* username/password.
            - Authentication is performed via HTTP Basic Auth by the EAM API.
        """
        register_credentials(
            base_url=base_url,
            user=username,
            password=password,
        )

        self.sync_eam_devices(
            asset_grid=asset_grid,
            department_code=department_code,
            limit=limit,
        )
        self.sync_eam_positions(
            position_grid=position_grid,
            limit=limit,
        )

    def sync_eam_devices(
        self,
        *,
        asset_grid: str = "OSOBJA",
        department_code: str = "AV",
        limit: int | None = None,
    ) -> None:
        """
        Fetch EAM devices (assets) from the remote API and reconcile them with the local cache.
        """

        query = Equipment.objects.use_grid(name=asset_grid)

        if department_code:
            try:
                query = query.filter(department_code__startswith=department_code)
            except Exception:
                self.logger.warning(
                    "eam_asset_deparment_code_filter_failed",
                    prefix=department_code,
                    exc_info=True,
                )

        if limit is not None:
            try:
                query = query.limit(limit)
            except Exception:
                self.logger.warning("eam_limit_failed", limit=limit, exc_info=True)

        eam_list: list[Equipment] = query.all()
        cache_list: list[Equipment] = self.dbod_helper.get_all_eam_devices()

        self._sync_entities(
            api_items=eam_list,
            cached_items=cache_list,
            get_id=lambda d: d.code,
            sync_func=self.dbod_helper.sync_eam_devices,
            name="EAM Devices",
        )

    def sync_eam_positions(
        self,
        *,
        position_grid: str = "OSOBJP",
        department_code: str = "AV",
        limit: int | None = None,
    ) -> None:
        """
        Fetch EAM positions from the remote API and reconcile them with the local cache.
        """

        # TODO: Waiting for https://gitlab.cern.ch/itdcim/av-tools/-/merge_requests/11#note_10728458 to be merged
        query = GridQuery(
            name=position_grid,
            field_map={
                "assigned_to": "assignedto",
                "alias": "alias",
                "category_code": "category",
                "class_code": "class",
                "code": "equipmentno",
                "comission_date": "commissiondate",
                "department_code": "department",
                "description": "equipmentdesc",
                "hierarchy_asset_code": "parentasset",
                "hierarchy_location_code": "location",
                "out_of_service": "outofservice",
                "primary_system": "primarysystem",
                "production": "production",
                "status_desc": "assetstatus_display",
                "variable2": "variable2",
            },
        )
        # TODO: uncomment once it's merged
        # query = Equipment.objects.use_grid(name=position_grid)

        if department_code:
            try:
                query = query.filter(department_code__startswith=department_code)
            except Exception:
                self.logger.warning(
                    "eam_position_department_code_filter_failed",
                    prefix=department_code,
                    exc_info=True,
                )

        if limit is not None:
            try:
                query = query.limit(limit)
            except Exception:
                self.logger.warning(
                    "eam_position_limit_failed",
                    limit=limit,
                    exc_info=True,
                )

        eam_list: list[Position] = query.all()
        cache_list = self.dbod_helper.get_all_eam_positions()

        self._sync_entities(
            api_items=eam_list,
            cached_items=cache_list,
            get_id=lambda p: p.code,
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
        """
        Synchronize LanDB devices using Auth0 client credentials.
        """
        eam_list: list[Equipment] = self.dbod_helper.get_all_eam_devices()
        if not eam_list:
            self.logger.info("No EAM devices—skipping LanDB sync.")
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
            get_id=lambda d: d.equipment_no,
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
        """
        Collect SNMP metrics for all LanDB devices and write them to InfluxDB.
        """
        devices: list[LanDBDevice] = self.dbod_helper.get_all_landb_devices()
        total = len(devices)
        if not devices:
            self.logger.info("No LanDB devices to monitor.")
            return

        self.logger.info(f"Fetching {total} LanDB devices with {max_workers} tasks.")

        all_points = asyncio_run(self._get_snmp_points(devices, max_workers))

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
        eam_records: list[Equipment],
        session: ServiceAuthSession,
        max_workers: int = 8,
    ) -> list[LanDBDevice]:
        """
        Fetch LanDB devices concurrently using asyncio.TaskGroup and chunking.
        """
        total = len(eam_records)
        if total == 0:
            self.logger.info("No EAM records provided; nothing to fetch.")
            return []

        num_tasks = min(max_workers, total)
        self.logger.info(f"Fetching {total} LanDB devices with {num_tasks} tasks.")

        # Preallocate results
        result: list[LanDBDevice | None] = [None] * total
        chunk = (total + num_tasks - 1) // num_tasks

        async def process_slice(start: int, end: int) -> None:
            helper = LanDBClient(session=session)
            for idx in range(start, end):
                rec = eam_records[idx]
                try:
                    dev = await to_thread(
                        helper.build_device_with_ip,
                        rec.code,
                        rec.serial_number,
                        rec.class_code,
                        rec.manufacturer_code,
                    )
                except Exception as e:
                    self.logger.error(f"Error fetching {rec.code}: {e}")
                    continue
                if dev and dev.serial_number and dev.ip is not None:
                    result[idx] = dev

        # Use TaskGroup for clean task management
        async with TaskGroup() as tg:
            for i in range(num_tasks):
                start = i * chunk
                end = min(start + chunk, total)
                tg.create_task(process_slice(start, end))

        # Filter out failed lookups
        devices: list[LanDBDevice] = [d for d in result if d is not None]  # type: ignore[arg-type]
        self.logger.info(f"Fetched {len(devices)} of {total} LanDB devices.")
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
        """
        start_time = time()

        if not cached_items:
            sync_func(to_insert=api_items, to_update=[], to_delete=[])
            self.logger.info(
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
        self.logger.info(
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
        if total == 0:
            return []

        max_workers = max(1, max_workers)
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

                self.logger.info(
                    f"Task on {len(chunk)} devices: "
                    f"{len(ping_pts)} ping, {len(probe_pts)} probe, {len(query_pts)} query points"
                )
            except Exception as e:
                self.logger.exception(f"Worker task error: {e}")
            return pts

        all_points: list[Point] = []
        tasks = []
        async with TaskGroup() as tg:
            for chunk in device_chunks:
                tasks.append(tg.create_task(worker_fn(chunk)))

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
            self.logger.info("No metrics collected; skipping write.")
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
            self.logger.info(f"Wrote {len(points)} total points")
        except Exception as e:
            self.logger.exception(f"Failed writing points: {e}")

    def _diff_models(self, old: Any, new: Any) -> dict[str, Any]:
        """
        Compute field-by-field differences between two models/objects.

        Key behavior:
        - If `old` carries `_avtools_compare_fields`, ONLY compare those fields.
        This prevents comparing API-only fields (alias, assigned_to, bin, store_code, etc.)
        that are not stored in the ORM.
        - Avoids `vars(old)` for subset-mode; instead reads exactly those fields via getattr.
        - For `new` (API), uses exclude_unset=True when supported.
        - Canonicalizes "" -> None to match DB semantics (your ORM `_get()` already does this).
        """

        def canon(v: Any) -> Any:
            return None if v == "" else v

        def to_new_dict(obj: Any) -> dict[str, Any]:
            # Pydantic v2
            if hasattr(obj, "model_dump"):
                return obj.model_dump(exclude_unset=True)
            # Pydantic v1
            if hasattr(obj, "dict"):
                return obj.dict(exclude_unset=True)
            # Plain object fallback
            return vars(obj)

        compare_fields = getattr(old, "_avtools_compare_fields", None)
        new_data = to_new_dict(new)

        # NOTE (cache subset vs API full model):
        # The cached object is a lightweight DB projection (EAMDeviceORM) materialized back into an
        # `Equipment` instance for convenience. That `Equipment` class exposes many more fields than
        # we actually persist, so "missing" ORM columns end up as class-defaults (often None/""/False)
        # on the derived object. If we diff the full Equipment schema, we get permanent churn:
        #   DB-derived: alias=None, assigned_to=None, ...   vs   API: alias="", assigned_to="", ...
        # even though those fields are not stored and can never converge.
        #
        # To prevent this, DB-derived Equipment instances carry an explicit whitelist of DB-backed
        # fields (e.g. `_avtools_compare_fields`). When present, diffs are restricted to that subset:
        # only fields persisted in the ORM are compared and allowed to drive updates.
        if compare_fields is not None:
            diffs: dict[str, Any] = {}
            for k in compare_fields:
                if k not in new_data:
                    continue  # API didn't provide it -> don't diff it
                old_v = canon(getattr(old, k, None))
                new_v = canon(new_data.get(k))
                if old_v != new_v:
                    diffs[k] = new_data.get(k)  # keep raw API value
            return diffs

        # Fallback generic mode (if you ever compare other models)
        old_data: dict[str, Any]
        if hasattr(old, "model_dump"):
            old_data = old.model_dump(exclude_unset=False)
        elif hasattr(old, "dict"):
            old_data = old.dict(exclude_unset=False)
        else:
            old_data = vars(old)

        return {
            k: v
            for k, v in new_data.items()
            if k in old_data and canon(old_data.get(k)) != canon(v)
        }

    def _ensure_token(
        self,
        session: ServiceAuthSession,
        config: LanDBConfig = LanDBConfig(),
    ) -> None:
        """
        Ensure that session.auth.token is populated by the CERN OAuth wrapper.

        If no token exists, fire a dummy GET against the LanDB devices endpoint
        (which under the hood will trigger the token fetch).
        """
        token: Any = session.auth.token

        if token:
            expires_at = getattr(token, "expires_at", None)
            if expires_at:
                self.logger.info(f"Using cached access token (expires at {expires_at})")
            else:
                self.logger.info("Using cached access token")
            return

        # No token → hit the devices endpoint to force a token fetch
        probe_url = f"{config.base_url}/{config.device_endpoint}"
        self.logger.info(f"No access token found; probing {probe_url} to acquire one")
        try:
            resp = session.get(probe_url, params={"_limit": 1}, verify=True)
            if resp.ok:
                self.logger.info(
                    "Successfully obtained new access token via probe GET."
                )
            else:
                self.logger.error(
                    f"Probe GET failed [{resp.status_code}] when obtaining token."
                )
        except Exception as e:
            self.logger.error(f"Error during token-fetch probe: {e}")
