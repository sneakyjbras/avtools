from __future__ import annotations

import math
import os
import re
from asyncio import TaskGroup
from asyncio import run as asyncio_run
from collections.abc import Callable, Sequence
from time import time
from typing import Any, TypeVar

import structlog
from eam_rest_client import Equipment
from eam_rest_client.credentials import register_credentials
from eam_rest_client.grid_query import GridQuery
from landb_rest_client import register_credentials as landb_register_credentials
from landb_rest_client.models import Device, IPAddress

from avtools.influx.client import InfluxClient
from avtools.postgres.client import PostgresClient
from avtools.postgres.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.client import SNMPClient

Model = TypeVar("Model")
Point = dict[str, Any]


class AVTools:
    """AV Tools orchestrator: EAM sync, LanDB sync, SNMP->Influx collection."""

    def __init__(self, dbod_url: str, logs: bool = False) -> None:
        self.dbod_helper = PostgresClient(dbod_url)
        self.logs = logs
        self.logger = structlog.get_logger(self.__class__.__name__)
        self._landb_initialized = False

    # ---------------------------------------------------------------------
    # EAM
    # ---------------------------------------------------------------------

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
        register_credentials(base_url=base_url, user=username, password=password)

        self.sync_eam_devices(
            asset_grid=asset_grid,
            department_code=department_code,
            limit=limit,
        )
        self.sync_eam_positions(
            position_grid=position_grid,
            department_code=department_code,
            limit=limit,
        )

    def sync_eam_devices(
        self,
        *,
        asset_grid: str = "OSOBJA",
        department_code: str = "AV",
        limit: int | None = None,
    ) -> None:
        query = Equipment.objects.use_grid(name=asset_grid)

        if department_code:
            try:
                query = query.filter(department_code__startswith=department_code)
            except Exception:
                self.logger.warning(
                    "eam_asset_department_code_filter_failed",
                    prefix=department_code,
                    exc_info=True,
                )

        if limit is not None:
            try:
                query = query.limit(limit)
            except Exception:
                self.logger.warning("eam_limit_failed", limit=limit, exc_info=True)

        eam_list: list[Equipment] = query.all()
        eam_list = self._clean_eam_text_fields(eam_list)
        cache_list: list[Equipment] = self.dbod_helper.get_all_eam_devices()

        self._sync_entities(
            api_items=eam_list,
            cached_items=cache_list,
            get_id=lambda d: str(d.code),
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
        # TODO: switch to Position once the client exposes the model/grid officially.
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
            model=Equipment,
            grid_type="LIST",
        )

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

        eam_list: list[Equipment] = query.all()
        eam_list = self._clean_eam_text_fields(eam_list)
        cache_list: list[Equipment] = self.dbod_helper.get_all_eam_positions()

        self._sync_entities(
            api_items=eam_list,
            cached_items=cache_list,
            get_id=lambda p: str(p.code),
            sync_func=self.dbod_helper.sync_eam_positions,
            name="EAM Positions",
        )

    # ---------------------------------------------------------------------
    # LanDB
    # ---------------------------------------------------------------------

    def run_landb(
        self,
        client_id: str,
        client_secret: str,
        audience: str,
        max_workers: int = 8,  # kept for backwards compat; LanDB sync is intentionally single-shot
        *,
        base_url: str = "https://landb.cern.ch/api/",
    ) -> None:
        eam_list: list[Equipment] = self.dbod_helper.get_all_eam_devices()
        if not eam_list:
            self.logger.info("No EAM devices—skipping LanDB sync")
            return

        self._init_landb_rest_client(
            client_id=client_id,
            client_secret=client_secret,
            audience=audience,
            url=base_url,
        )

        landb_ips: list[CachedIPAddress] = self._get_landb_ipaddresses(eam_list)

        cache_list = self.dbod_helper.get_all_landb_devices()

        self._sync_entities(
            api_items=landb_ips,
            cached_items=cache_list,
            get_id=self._landb_get_id,
            sync_func=self.dbod_helper.sync_landb_devices,
            name="LanDB IPAddress",
        )

    def _landb_get_id(self, obj: Any) -> str:
        v = getattr(obj, "equipmentno", None) or getattr(obj, "equipment_no", None)
        return str(v)

    def _init_landb_rest_client(
        self,
        *,
        client_id: str,
        client_secret: str,
        audience: str,
        url: str,
    ) -> None:
        if self._landb_initialized:
            return

        self.logger.info("Initializing LanDB REST client", url=url, audience=audience)

        landb_register_credentials(
            client_id=client_id,
            client_secret=client_secret,
            url=url,
            audience=audience,
            user=None,
            user_token=None,
        )

        self._landb_initialized = True

    def _get_landb_ipaddresses(
        self, eam_records: list[Equipment]
    ) -> list[CachedIPAddress]:
        """Bulk LanDB lookup in <=4 API calls.

        Strategy:
        1) Fetch LanDB Devices by EAM serial_number (Device.serial_number__in).
        2) For EAM records not found via serial (and those lacking a serial), fetch Devices by
           EAM description (Device.name__in).
        3) Using the matched LanDB Devices, fetch IPAddresses by device serial_number, then
           fallback by device name.

        Output:
        - List[CachedIPAddress] enriched with EAM keys (equipment_no, class_code, etc.).

        IMPORTANT CORRELATION:
        - (EAM) Equipment.serial_number == (LanDB) Device.serial_number
        - (LanDB) Device.name == (LanDB) IPAddress.device   (NOT IPAddress.name)
        """

        def norm(v: Any) -> str | None:
            if v is None:
                return None
            s = str(v).strip()
            return s or None

        # NOTE: per the user's clarification, both `Device.name` and `IPAddress.device`
        # are plain strings in this environment.

        total = len(eam_records)
        if total == 0:
            return []

        # --- Build EAM key sets (unique, trimmed) -------------------------
        eam_serials: list[str] = []
        eam_serial_set: set[str] = set()
        for rec in eam_records:
            s = norm(getattr(rec, "serial_number", None))
            if s and s not in eam_serial_set:
                eam_serial_set.add(s)
                eam_serials.append(s)

        # --- (1) Devices by serial_number --------------------------------
        devices_by_serial: dict[str, Device] = {}
        try:
            if eam_serials:
                devs = Device.objects.filter(serial_number__in=eam_serials).all()
                for d in devs:
                    s = norm(getattr(d, "serial_number", None))
                    if s and s not in devices_by_serial:
                        devices_by_serial[s] = d
        except Exception:
            self.logger.warning(
                "landb_device_fetch_by_serial_failed",
                serial_count=len(eam_serials),
                exc_info=True,
            )

        # Determine which EAM records still need a name-based lookup.
        # (includes missing serials AND serials not present in LanDB)
        need_name_lookup: list[Equipment] = []
        for rec in eam_records:
            s = norm(getattr(rec, "serial_number", None))
            if not s or s not in devices_by_serial:
                need_name_lookup.append(rec)

        # --- (2) Devices by name (EAM description) ------------------------
        names_to_query: list[str] = []
        names_set: set[str] = set()
        for rec in need_name_lookup:
            n = norm(getattr(rec, "description", None))
            if n and n not in names_set:
                names_set.add(n)
                names_to_query.append(n)

        devices_by_name: dict[str, Device] = {}
        try:
            if names_to_query:
                devs = Device.objects.filter(name__in=names_to_query).all()
                for d in devs:
                    n = norm(getattr(d, "name", None))
                    if n and n not in devices_by_name:
                        devices_by_name[n] = d
        except Exception:
            self.logger.warning(
                "landb_device_fetch_by_name_failed",
                name_count=len(names_to_query),
                exc_info=True,
            )

        # --- Match EAM -> LanDB Device (prefer serial match) --------------
        matched: list[tuple[Equipment, Device, str]] = []
        missing_eam: list[Equipment] = []
        matched_by_serial = 0
        matched_by_name = 0

        for rec in eam_records:
            serial = norm(getattr(rec, "serial_number", None))
            desc = norm(getattr(rec, "description", None))

            if serial and serial in devices_by_serial:
                matched.append((rec, devices_by_serial[serial], "serial"))
                matched_by_serial += 1
                continue

            if desc and desc in devices_by_name:
                matched.append((rec, devices_by_name[desc], "name"))
                matched_by_name += 1
                continue

            missing_eam.append(rec)

        self.logger.info(
            "landb_device_match_summary",
            eam_total=total,
            matched=len(matched),
            missing=len(missing_eam),
            matched_by_serial=matched_by_serial,
            matched_by_name=matched_by_name,
        )
        if missing_eam:
            self.logger.warning(
                "landb_eam_equipment_not_found",
                missing_count=len(missing_eam),
            )

        if not matched:
            self.logger.warning("landb_no_devices_matched")
            return []

        # --- (3) IPAddresses by device serial_number ----------------------
        device_serials: list[str] = []
        device_serial_set: set[str] = set()
        device_names: list[str] = []
        device_name_set: set[str] = set()

        for _, dev, _ in matched:
            s = norm(getattr(dev, "serial_number", None))
            if s and s not in device_serial_set:
                device_serial_set.add(s)
                device_serials.append(s)

            n = norm(getattr(dev, "name", None))
            if n and n not in device_name_set:
                device_name_set.add(n)
                device_names.append(n)

        # Key by IPAddress.device so we can correlate Device.name == IPAddress.device.
        ips_by_device: dict[str, IPAddress] = {}

        try:
            if device_serials:
                ips = IPAddress.objects.filter(
                    device__serial_number__in=device_serials
                ).all()
                for ip in ips:
                    k = norm(getattr(ip, "device", None))
                    if k and k not in ips_by_device:
                        ips_by_device[k] = ip
        except Exception:
            self.logger.warning(
                "landb_ipaddress_fetch_by_serial_failed",
                serial_count=len(device_serials),
                exc_info=True,
            )

        # --- (4) Fallback IPAddresses by device name ----------------------
        missing_ip_names = [n for n in device_names if n not in ips_by_device]
        try:
            if missing_ip_names:
                ips = IPAddress.objects.filter(device__name__in=missing_ip_names).all()
                for ip in ips:
                    k = norm(getattr(ip, "device", None))
                    if k and k not in ips_by_device:
                        ips_by_device[k] = ip
        except Exception:
            self.logger.warning(
                "landb_ipaddress_fetch_by_name_failed",
                name_count=len(missing_ip_names),
                exc_info=True,
            )

        # --- Correlate EAM -> Device -> IPAddress, enrich, return ---------
        out: list[CachedIPAddress] = []
        missing_ip_eam: list[Equipment] = []

        for eam_rec, dev, _match_kind in matched:
            dev_name = norm(getattr(dev, "name", None))
            if not dev_name:
                missing_ip_eam.append(eam_rec)
                continue

            ip_rec = ips_by_device.get(dev_name)
            if ip_rec is None:
                missing_ip_eam.append(eam_rec)
                continue

            out.append(
                self._enrich_ipaddress_with_eam_keys(
                    ip_rec,
                    eam_rec,
                    landb_device=dev,
                )
            )

        self.logger.info(
            "landb_ipaddress_match_summary",
            eam_total=total,
            matched_devices=len(matched),
            unique_devices=len(device_name_set),
            ip_records=len(ips_by_device),
            enriched=len(out),
            missing_ip=len(missing_ip_eam),
        )
        if missing_ip_eam:
            self.logger.warning(
                "landb_ipaddress_not_found_for_equipment",
                missing_count=len(missing_ip_eam),
            )

        return out

    def _enrich_ipaddress_with_eam_keys(
        self,
        ip_rec: IPAddress,
        eam_rec: Equipment,
        *,
        landb_device: Device | None = None,
    ) -> CachedIPAddress:
        # CachedIPAddress is now a standalone (non-inherited) model.
        return CachedIPAddress.from_equipment_and_ipaddress(
            eam_rec,
            ip_rec,
            landb_device=landb_device,
        )

    # ---------------------------------------------------------------------
    # Influx + SNMP
    # ---------------------------------------------------------------------

    def run_influx_snmp(
        self,
        influx_host: str,
        influx_port: int,
        influx_user: str,
        influx_password: str,
        influx_db: str,
        max_workers: int = 8,
    ) -> None:
        devices = self.dbod_helper.get_all_landb_devices()
        total = len(devices)
        if not devices:
            self.logger.info("No LanDB devices to monitor")
            return

        self.logger.info("Fetching LanDB devices", total=total, tasks=max_workers)

        all_points = asyncio_run(self._get_snmp_points(devices, max_workers))
        self._publish_snmp(
            all_points,
            influx_host,
            influx_port,
            influx_user,
            influx_password,
            influx_db,
        )

    async def _get_snmp_points(
        self, devices: list[Any], max_workers: int
    ) -> list[Point]:
        total = len(devices)
        if total == 0:
            return []

        max_workers = max(1, max_workers)
        chunk_size = math.ceil(total / max_workers)
        device_chunks = [
            devices[i : i + chunk_size] for i in range(0, total, chunk_size)
        ]

        async def worker_fn(chunk: list[Any]) -> list[Point]:
            monitor = SNMPClient(targets=chunk)
            pts: list[Point] = []
            try:
                ping_pts = await monitor.collect_ping()
                pts.extend(ping_pts)

                ok_ips = {
                    p["tags"]["ip"] for p in ping_pts if p["fields"].get("status") == 1
                }
                monitor.targets = [d for d in chunk if d.ip in ok_ips]

                probe_pts, alive_devices = await monitor.collect_snmp_probe()
                pts.extend(probe_pts)
                monitor.targets = alive_devices

                query_pts = await monitor.collect_snmp_query(alive_devices)
                pts.extend(query_pts)

                self.logger.info(
                    "snmp_task_done",
                    devices=len(chunk),
                    ping_points=len(ping_pts),
                    probe_points=len(probe_pts),
                    query_points=len(query_pts),
                )
            except Exception:
                self.logger.exception("snmp_worker_failed")
            return pts

        tasks = []
        async with TaskGroup() as tg:
            for chunk in device_chunks:
                tasks.append(tg.create_task(worker_fn(chunk)))

        all_points: list[Point] = []
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
        if not points:
            self.logger.info("No metrics collected; skipping write")
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
            self.logger.info("influx_write_ok", points=len(points))
        except Exception:
            self.logger.exception("influx_write_failed")

    # ---------------------------------------------------------------------
    # Generic sync
    # ---------------------------------------------------------------------

    def _sync_entities(
        self,
        api_items: Sequence[Model],
        cached_items: Sequence[Model],
        get_id: Callable[[Model], str],
        sync_func: Callable[..., None],
        name: str,
    ) -> None:
        start_time = time()

        if not cached_items:
            sync_func(to_insert=api_items, to_update=[], to_delete=[])
            self.logger.info(
                "sync_first_run",
                entity=name,
                inserted=len(api_items),
                updated=0,
                deleted=0,
            )
            return

        cache_map: dict[str, Model] = {get_id(item): item for item in cached_items}
        api_map: dict[str, Model] = {get_id(item): item for item in api_items}

        cache_ids = set(cache_map)
        api_ids = set(api_map)

        to_delete: list[str] = list(cache_ids - api_ids)
        to_insert: list[Model] = [api_map[i] for i in api_ids - cache_ids]
        to_update: list[tuple[Model, dict[str, Any]]] = []

        updated_rows: list[dict[str, Any]] = [] if self.logs else []

        for eid in api_ids & cache_ids:
            old_item = cache_map[eid]
            new_item = api_map[eid]
            changes = self._diff_models(old_item, new_item)
            if changes:
                to_update.append((new_item, changes))

                if self.logs:
                    # Build a row-level verbose diff for the end-of-sync report.
                    verbose: dict[str, dict[str, Any]] = {}
                    for k, new_v in changes.items():
                        old_v = getattr(old_item, k, None)
                        # Normalize empty strings to None for readability.
                        if old_v == "":
                            old_v = None
                        if new_v == "":
                            new_v = None

                        if old_v != new_v:
                            verbose[k] = {"old": old_v, "new": new_v}

                    updated_rows.append(
                        {
                            "id": eid,
                            "serial_number": self._sanitize_text(
                                getattr(new_item, "serial_number", None)
                            ),
                            "changed_fields": sorted(verbose.keys()),
                            "diff": verbose,
                        }
                    )

        sync_func(to_insert=to_insert, to_update=to_update, to_delete=to_delete)

        if self.logs:

            def sn(obj: Any) -> str | None:
                return self._sanitize_text(getattr(obj, "serial_number", None))

            deleted: list[dict[str, Any]] = []
            for did in to_delete:
                old_item = cache_map.get(did)
                if old_item is None:
                    continue
                deleted.append(
                    {
                        "id": did,
                        "serial_number": sn(old_item),
                    }
                )

            added: list[dict[str, Any]] = []
            for it in to_insert:
                added.append(
                    {
                        "id": get_id(it),
                        "serial_number": sn(it),
                    }
                )

            # Convenience lists (serials only)
            deleted_serials = [
                d["serial_number"] for d in deleted if d.get("serial_number")
            ]
            added_serials = [
                a["serial_number"] for a in added if a.get("serial_number")
            ]
            updated_serials = [
                u.get("serial_number") for u in updated_rows if u.get("serial_number")
            ]

            # Shell-friendly detailed report (line-by-line)
            # NOTE: We keep the existing per-row 'sync_row_update' logs for full diffs;
            # here we only summarize which rows changed and which fields triggered it.

            # Stable order for readability
            deleted = sorted(deleted, key=lambda r: r.get("id", ""))
            added = sorted(added, key=lambda r: r.get("id", ""))
            updated_rows_sorted = sorted(updated_rows, key=lambda r: r.get("id", ""))

            for row in deleted:
                self.logger.info(
                    "sync_report_deleted_row",
                    entity=name,
                    id=row.get("id"),
                    serial_number=row.get("serial_number"),
                )

            for row in added:
                self.logger.info(
                    "sync_report_added_row",
                    entity=name,
                    id=row.get("id"),
                    serial_number=row.get("serial_number"),
                )

            for row in updated_rows_sorted:
                self.logger.info(
                    "sync_report_updated_row",
                    entity=name,
                    id=row.get("id"),
                    serial_number=row.get("serial_number"),
                    changed_fields=row.get("changed_fields", []),
                    # Full per-field diffs are already emitted in 'sync_row_update'.
                )

            # Compact summary (plus serial-only lists for quick copy/paste)
            self.logger.info(
                "sync_report_summary",
                entity=name,
                deleted=len(deleted),
                added=len(added),
                updated=len(updated_rows_sorted),
                deleted_serials=deleted_serials,
                added_serials=added_serials,
                updated_serials=updated_serials,
            )

        duration = time() - start_time
        self.logger.info(
            "sync_done",
            entity=name,
            duration_s=round(duration, 3),
            inserted=len(to_insert),
            updated=len(to_update),
            deleted=len(to_delete),
        )

    def _diff_models(self, old: Any, new: Any) -> dict[str, Any]:
        def canon(v: Any) -> Any:
            return None if v == "" else v

        def to_dict(obj: Any, *, exclude_unset: bool) -> dict[str, Any]:
            # Pydantic v1: we always use .dict()
            return obj.dict(exclude_unset=exclude_unset)  # type: ignore[attr-defined]

        compare_fields = getattr(old, "avtools_compare_fields", None) or getattr(
            old, "_compare_fields", None
        )

        if callable(compare_fields):
            compare_fields = compare_fields()

        new_data = to_dict(new, exclude_unset=True)

        # Normalize whitespace on string fields we persist/diff against (EAM is often dirty).
        if compare_fields is not None:
            for k in compare_fields:
                if k == "code" or k not in new_data:
                    continue
                v = new_data.get(k)
                if k == "serial_number" or isinstance(v, str):
                    new_data[k] = self._sanitize_text(v)
        else:
            # Best-effort fallback for models that don't expose compare fields.
            for k in ("serial_number", "model", "description"):
                if k not in new_data:
                    continue
                v = new_data.get(k)
                if k == "serial_number" or isinstance(v, str):
                    new_data[k] = self._sanitize_text(v)

        if compare_fields is not None:
            diffs: dict[str, Any] = {}
            for k in compare_fields:
                if k not in new_data:
                    continue
                old_v = canon(getattr(old, k, None))
                new_v = canon(new_data.get(k))
                if old_v != new_v:
                    diffs[k] = new_data.get(k)
            return diffs

        old_data = to_dict(old, exclude_unset=False)

        return {
            k: v
            for k, v in new_data.items()
            if k in old_data and canon(old_data.get(k)) != canon(v)
        }

    # ---------------------------------------------------------------------
    # Sanitization helpers (private)
    # ---------------------------------------------------------------------

    # TODO: Add sanitization module in the future
    _TEXT_TRIM_RE = re.compile(r"^[\s   ​‎‏﻿]+|[\s   ​‎‏﻿]+$")

    # Only sanitize fields that AVTools persists and diffs against (prevents churn).
    _EAM_TEXT_FIELDS: tuple[str, ...] = (
        # Keys used in downstream joins/filters
        "serial_number",
        "description",
        # Common persisted/diffed device fields
        "model",
        "manufacturer_code",
        "class_code",
        "category_code",
        "department_code",
        "status_code",
        "status_desc",
        # Optional but commonly dirty fields (esp. positions grid)
        "alias",
        "assigned_to",
        "primary_system",
        "hierarchy_position_code",
        "hierarchy_asset_code",
        "hierarchy_location_code",
        "variable2",
    )

    def _sanitize_text(self, value: Any) -> str | None:
        """Normalize upstream text.

        - Convert to string (if needed)
        - Strip leading/trailing whitespace (incl. tabs, NBSP, BOM, zero-width)
        - Preserve internal spacing
        """
        if value is None:
            return None

        s = value if isinstance(value, str) else str(value)
        if not s:
            return None

        s2 = self._TEXT_TRIM_RE.sub("", s)
        return s2 or None

    def _clean_eam_text_fields(self, items: list[Equipment]) -> list[Equipment]:
        """Return a list where selected EAM text fields are sanitized.

        We only touch a small whitelist (persisted + diffed fields) to keep behavior safe
        and predictable.
        """
        out: list[Equipment] = []
        for eq in items:
            updates: dict[str, Any] = {}

            for field in self._EAM_TEXT_FIELDS:
                raw = getattr(eq, field, None)
                if raw is None:
                    continue

                if field != "serial_number" and not isinstance(raw, str):
                    # Avoid accidentally stringifying dates/objects.
                    continue

                cleaned = self._sanitize_text(raw)
                if raw != cleaned:
                    updates[field] = cleaned

            if not updates:
                out.append(eq)
                continue

            # Prefer non-mutating copies (works for frozen models).
            try:
                if hasattr(eq, "model_copy"):
                    out.append(eq.model_copy(update=updates))  # type: ignore[attr-defined]
                    continue
                if hasattr(eq, "copy"):
                    out.append(eq.copy(update=updates))  # type: ignore[attr-defined]
                    continue
            except Exception:
                pass

            # Last resort: try in-place set, then fall back to a re-construct.
            try:
                for k, v in updates.items():
                    setattr(eq, k, v)
                out.append(eq)
                continue
            except Exception:
                pass

            try:
                payload = eq.dict(exclude_unset=False)  # type: ignore[attr-defined]
                payload.update(updates)
                out.append(eq.__class__(**payload))
            except Exception:
                # If we cannot safely mutate/copy, keep original (better than crashing).
                out.append(eq)

        return out
