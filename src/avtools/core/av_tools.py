from __future__ import annotations

import math
import os
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
from avtools.utils.eam_sanitizer import EAMTextSanitizer
from avtools.utils.sync_reporting import SyncReportLogger

Model = TypeVar("Model")
Point = dict[str, Any]


class AVTools:
    """AV Tools orchestrator: EAM sync, LanDB sync, SNMP->Influx collection."""

    def __init__(self, dbod_url: str, logs: bool = False) -> None:
        """Initialize the AV Tools orchestrator.

        Args:
            dbod_url: Postgres/DBOD connection URL used by the cache client.
            logs: If True, emit per-entity sync reports (field-level diffs) to the logger.

        Returns:
            None.
        """
        self.dbod_helper = PostgresClient(dbod_url)
        self.logs = logs
        self.logger = structlog.get_logger(self.__class__.__name__)
        self._landb_initialized = False
        self._eam_sanitizer = EAMTextSanitizer()

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
        """Run the EAM sync and persist assets + positions into Postgres.

        Registers EAM credentials, then fetches assets (OSOBJA) and positions (OSOBJP)
        for the given department prefix. Each step is guarded so a failure in one flow
        does not crash the whole process.
        """
        run_started = time()
        status: str = "started"
        had_errors = False

        self.logger.info(
            "avtools_run_eam_start",
            base_url=base_url,
            asset_grid=asset_grid,
            position_grid=position_grid,
            department_code=department_code,
            limit=limit,
        )

        try:
            # --- Auth / client configuration ---------------------------------
            try:
                register_credentials(
                    base_url=base_url, user=username, password=password
                )
            except EamClientRetryableHTTPError as e:
                status = "failed_register_credentials_retryable_http_error"
                self.logger.error(
                    "eam_register_credentials_retryable_http_error",
                    status=e.status_code,
                    url=e.url,
                    request_id=getattr(e, "request_id", None),
                    retry_after=getattr(e, "retry_after", None),
                    error=str(e),
                )
                return
            except EamClientHTTPError as e:
                status = "failed_register_credentials_http_error"
                self.logger.error(
                    "eam_register_credentials_http_error",
                    status=e.status_code,
                    url=e.url,
                    request_id=getattr(e, "request_id", None),
                    error=str(e),
                )
                return
            except EamClientTimeoutError as e:
                status = "failed_register_credentials_timeout"
                self.logger.error(
                    "eam_register_credentials_timeout",
                    retry_after=e.retry_after(),
                    error=str(e),
                )
                return
            except EamClientTransportError as e:
                status = "failed_register_credentials_transport_error"
                self.logger.error(
                    "eam_register_credentials_transport_error",
                    error=str(e),
                    original=repr(getattr(e, "original", None)),
                )
                return
            except EamRestClientError as e:
                status = "failed_register_credentials"
                self.logger.error("eam_register_credentials_failed", error=str(e))
                return
            except Exception:
                status = "failed_register_credentials_unexpected"
                self.logger.exception("eam_register_credentials_failed_unexpected")
                return

            # --- Assets -------------------------------------------------------
            try:
                self.sync_eam_devices(
                    asset_grid=asset_grid,
                    department_code=department_code,
                    limit=limit,
                )
            except EamClientRetryableHTTPError as e:
                had_errors = True
                self.logger.error(
                    "eam_devices_retryable_http_error",
                    status=e.status_code,
                    url=e.url,
                    request_id=getattr(e, "request_id", None),
                    retry_after=getattr(e, "retry_after", None),
                    error=str(e),
                )
            except EamClientHTTPError as e:
                had_errors = True
                self.logger.error(
                    "eam_devices_http_error",
                    status=e.status_code,
                    url=e.url,
                    request_id=getattr(e, "request_id", None),
                    error=str(e),
                )
            except EamClientTimeoutError as e:
                had_errors = True
                self.logger.error(
                    "eam_devices_timeout",
                    retry_after=e.retry_after(),
                    error=str(e),
                )
            except EamClientTransportError as e:
                had_errors = True
                self.logger.error(
                    "eam_devices_transport_error",
                    error=str(e),
                    original=repr(getattr(e, "original", None)),
                )
            except EamQueryError as e:
                had_errors = True
                self.logger.error("eam_devices_query_error", error=str(e))
            except EamRestClientError as e:
                had_errors = True
                self.logger.error("eam_devices_failed", error=str(e))
            except Exception:
                had_errors = True
                self.logger.exception("eam_devices_failed_unexpected")

            # --- Positions ----------------------------------------------------
            try:
                self.sync_eam_positions(
                    position_grid=position_grid,
                    department_code=department_code,
                    limit=limit,
                )
            except EamClientRetryableHTTPError as e:
                had_errors = True
                self.logger.error(
                    "eam_positions_retryable_http_error",
                    status=e.status_code,
                    url=e.url,
                    request_id=getattr(e, "request_id", None),
                    retry_after=getattr(e, "retry_after", None),
                    error=str(e),
                )
            except EamClientHTTPError as e:
                had_errors = True
                self.logger.error(
                    "eam_positions_http_error",
                    status=e.status_code,
                    url=e.url,
                    request_id=getattr(e, "request_id", None),
                    error=str(e),
                )
            except EamClientTimeoutError as e:
                had_errors = True
                self.logger.error(
                    "eam_positions_timeout",
                    retry_after=e.retry_after(),
                    error=str(e),
                )
            except EamClientTransportError as e:
                had_errors = True
                self.logger.error(
                    "eam_positions_transport_error",
                    error=str(e),
                    original=repr(getattr(e, "original", None)),
                )
            except EamQueryError as e:
                had_errors = True
                self.logger.error("eam_positions_query_error", error=str(e))
            except EamRestClientError as e:
                had_errors = True
                self.logger.error("eam_positions_failed", error=str(e))
            except Exception:
                had_errors = True
                self.logger.exception("eam_positions_failed_unexpected")

            status = "ok" if not had_errors else "completed_with_errors"

        except KeyboardInterrupt:
            status = "interrupted"
            raise
        finally:
            duration_s = time() - run_started
            self.logger.info(
                "avtools_run_eam_end",
                status=status,
                duration_s=round(duration_s, 3),
                duration_ms=int(duration_s * 1000),
                asset_grid=asset_grid,
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
        """Sync EAM assets (devices) into the Postgres cache.

        Fetches Equipment rows from the EAM asset grid, applies text sanitization, loads
        cached rows from Postgres, and delegates the reconciliation to _sync_entities.

        Args:
            asset_grid: EAM grid name for assets (default OSOBJA).
            department_code: Department code prefix filter (e.g. 'AV').
            limit: Optional row limit (debug/testing).

        Returns:
            None.
        """
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
        eam_list = self._eam_sanitizer.clean_items(eam_list)
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
        """Sync EAM positions into the Postgres cache.

        Runs a GridQuery against the positions grid (OSOBJP) into Equipment models,
        sanitizes text, loads cached rows from Postgres, and delegates the reconciliation
        to _sync_entities.

        Args:
            position_grid: EAM grid name for positions (default OSOBJP).
            department_code: Department code prefix filter (e.g. 'AV').
            limit: Optional row limit (debug/testing).

        Returns:
            None.
        """
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
        eam_list = self._eam_sanitizer.clean_items(eam_list)
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
        """Run the LanDB sync (EAM devices -> LanDB devices/IPs -> Postgres)."""
        run_started = time()
        status: str = "started"
        had_errors = False

        eam_count = 0
        enriched_ip_count = 0
        cached_count = 0

        self.logger.info(
            "avtools_run_landb_start",
            base_url=base_url,
            audience=audience,
            max_workers=max_workers,
        )

        try:
            try:
                # Load the current EAM snapshot from Postgres (source of truth for join keys).
                eam_list: list[Equipment] = self.dbod_helper.get_all_eam_devices()
                eam_count = len(eam_list)
            except Exception:
                status = "failed_load_eam_devices"
                self.logger.exception("landb_load_eam_devices_failed")
                return

            if not eam_list:
                status = "skipped_no_eam_devices"
                self.logger.info("No EAM devices—skipping LanDB sync")
                return

            try:
                self._init_landb_rest_client(
                    client_id=client_id,
                    client_secret=client_secret,
                    audience=audience,
                    url=base_url,
                )
            except TokenExpired as e:
                status = "failed_client_init_token_expired"
                self.logger.error("landb_token_expired", error=str(e))
                return
            except QuerySetError as e:
                status = "failed_client_init_queryset_error"
                self.logger.error("landb_client_init_queryset_error", error=str(e))
                return
            except LanDBRestError as e:
                status = "failed_client_init"
                self.logger.error("landb_client_init_failed", error=str(e))
                return
            except Exception:
                status = "failed_client_init_unexpected"
                self.logger.exception("landb_client_init_failed_unexpected")
                return

            try:
                landb_ips = self._get_landb_ipaddresses(eam_list)
                enriched_ip_count = len(landb_ips)
            except TokenExpired as e:
                status = "failed_fetch_token_expired"
                self.logger.error("landb_token_expired", error=str(e))
                return
            except DataAwareValidationError as e:
                status = "failed_fetch_validation_error"
                err_count = None
                try:
                    err_count = len(e.errors())
                except Exception:
                    err_count = None
                self.logger.error(
                    "landb_validation_error",
                    error=str(e),
                    error_count=err_count,
                )
                return
            except QuerySetError as e:
                status = "failed_fetch_queryset_error"
                self.logger.error("landb_queryset_error", error=str(e))
                return
            except LanDBRestError as e:
                status = "failed_fetch"
                self.logger.error("landb_fetch_failed", error=str(e))
                return
            except Exception:
                status = "failed_fetch_unexpected"
                self.logger.exception("landb_fetch_failed_unexpected")
                return

            try:
                cache_list = self.dbod_helper.get_all_landb_devices()
                cached_count = len(cache_list)
            except Exception:
                status = "failed_load_cached_devices"
                self.logger.exception("landb_load_cached_devices_failed")
                return

            try:
                self._sync_entities(
                    api_items=landb_ips,
                    cached_items=cache_list,
                    get_id=self._landb_get_id,
                    sync_func=self.dbod_helper.sync_landb_devices,
                    name="LanDB IPAddress",
                )
            except Exception:
                had_errors = True
                status = "failed_sync"
                self.logger.exception("landb_sync_failed")
                return

            status = "ok" if not had_errors else "completed_with_errors"

        except KeyboardInterrupt:
            status = "interrupted"
            raise

        finally:
            duration_s = time() - run_started
            self.logger.info(
                "avtools_run_landb_end",
                status=status,
                duration_s=round(duration_s, 3),
                duration_ms=int(duration_s * 1000),
                eam_devices=eam_count,
                enriched_ipaddresses=enriched_ip_count,
                cached_rows=cached_count,
                base_url=base_url,
                audience=audience,
            )

    def _landb_get_id(self, obj: Any) -> str:
        """Get the stable identifier used for LanDB cached rows.

        Args:
            obj: CachedIPAddress (or compatible object) carrying equipment identifiers.

        Returns:
            Equipment number as a string.
        """
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
        """Configure the LanDB REST client credentials once per process.

        Registers OAuth credentials for the generated LanDB client and marks the
        instance as initialized so subsequent calls are no-ops.

        Args:
            client_id: OAuth client id.
            client_secret: OAuth client secret.
            audience: Target audience (e.g. production-microservice-landb-rest).
            url: Base URL for the LanDB API.

        Returns:
            None.
        """
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
        - Only includes devices that have a usable SNMP target IP (LanDB ipv4 or ipv6).

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
        missing_target_ip_eam: list[Equipment] = []

        for eam_rec, dev, _match_kind in matched:
            dev_name = norm(getattr(dev, "name", None))
            if not dev_name:
                missing_ip_eam.append(eam_rec)
                continue

            ip_rec = ips_by_device.get(dev_name)
            if ip_rec is None:
                missing_ip_eam.append(eam_rec)
                continue

            cached = self._enrich_ipaddress_with_eam_keys(
                ip_rec,
                eam_rec,
                landb_device=dev,
            )

            # Only commit devices that have a real SNMP target IP (ipv4 or ipv6).
            if not getattr(cached, "ip", None):
                missing_target_ip_eam.append(eam_rec)
                continue

            out.append(cached)

        self.logger.info(
            "landb_ipaddress_match_summary",
            eam_total=total,
            matched_devices=len(matched),
            unique_devices=len(device_name_set),
            ip_records=len(ips_by_device),
            enriched=len(out),
            missing_ip=len(missing_ip_eam),
            missing_target_ip=len(missing_target_ip_eam),
        )
        if missing_ip_eam or missing_target_ip_eam:
            self.logger.warning(
                "landb_ipaddress_not_found_for_equipment",
                missing_count=len(missing_ip_eam) + len(missing_target_ip_eam),
                missing_ip=len(missing_ip_eam),
                missing_target_ip=len(missing_target_ip_eam),
            )

        return out

    def _enrich_ipaddress_with_eam_keys(
        self,
        ip_rec: IPAddress | None,
        eam_rec: Equipment,
        *,
        landb_device: Device | None = None,
    ) -> CachedIPAddress:
        """Build an AV Tools CachedIPAddress by combining EAM + LanDB records.

        Args:
            ip_rec: LanDB IPAddress model returned by the REST client.
            eam_rec: EAM Equipment model used as the source of equipment keys/metadata.
            landb_device: Optional LanDB Device used for extra enrichment.

        Returns:
            CachedIPAddress ready to be synced into Postgres.
        """
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
        """Collect SNMP metrics for cached LanDB devices and publish them to InfluxDB."""
        run_started = time()
        status: str = "started"
        had_errors = False

        devices_total = 0
        points_total = 0

        self.logger.info(
            "avtools_run_influx_snmp_start",
            influx_host=influx_host,
            influx_port=influx_port,
            influx_db=influx_db,
            tasks=max_workers,
        )

        try:
            try:
                devices = self.dbod_helper.get_all_landb_devices()
                devices_total = len(devices)

                # The LanDB cache table now also stores rows that may not have an IP.
                # SNMP collection requires a target IP, so filter here.
                devices = [d for d in devices if getattr(d, "ip", None)]
                devices_total = len(devices)
            except Exception:
                status = "failed_load_landb_devices"
                self.logger.exception("snmp_load_landb_devices_failed")
                return

            if not devices:
                status = "skipped_no_landb_devices"
                self.logger.info("No LanDB devices to monitor")
                return

            self.logger.info(
                "Fetching LanDB devices", total=devices_total, tasks=max_workers
            )

            try:
                all_points = asyncio_run(self._get_snmp_points(devices, max_workers))
                points_total = len(all_points)
            except KeyboardInterrupt:
                status = "interrupted"
                raise
            except Exception:
                status = "failed_snmp_collection"
                self.logger.exception("snmp_collection_failed")
                return

            try:
                self._publish_snmp(
                    all_points,
                    influx_host,
                    influx_port,
                    influx_user,
                    influx_password,
                    influx_db,
                )
            except KeyboardInterrupt:
                status = "interrupted"
                raise
            except Exception:
                had_errors = True
                # _publish_snmp already logs, but keep a guardrail.
                self.logger.exception("influx_publish_failed_unexpected")

            status = "ok" if not had_errors else "completed_with_errors"

        finally:
            duration_s = time() - run_started
            self.logger.info(
                "avtools_run_influx_snmp_end",
                status=status,
                duration_s=round(duration_s, 3),
                duration_ms=int(duration_s * 1000),
                devices=devices_total,
                points=points_total,
                influx_host=influx_host,
                influx_port=influx_port,
                influx_db=influx_db,
                tasks=max_workers,
            )

    async def _get_snmp_points(
        self, devices: list[Any], max_workers: int
    ) -> list[Point]:
        """Collect SNMP/ping points for a set of cached devices.

        Splits the device list into chunks, runs workers concurrently (TaskGroup), and
        aggregates the produced points. Each worker pings first, then probes/queries
        only devices that replied.

        Args:
            devices: Cached LanDB device rows (must expose .ip).
            max_workers: Number of concurrent workers/chunks.

        Returns:
            List of InfluxDB points (dicts) to be written.
        """
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
        """Write SNMP points to InfluxDB.

        Creates an InfluxClient and writes the provided points in one batch.

        Args:
            points: Influx line protocol dicts (measurement/tags/fields).
            influx_host: InfluxDB host.
            influx_port: InfluxDB port.
            influx_user: InfluxDB username.
            influx_password: InfluxDB password.
            influx_db: InfluxDB database name.

        Returns:
            None.
        """
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
        """Reconcile API items vs cached items and persist the changes.

        Builds id maps, computes inserts/updates/deletes, optionally logs a per-entity
        diff report, and calls the provided sync_func to apply DB mutations.

        Args:
            api_items: Fresh items fetched from the upstream API.
            cached_items: Items currently stored in Postgres.
            get_id: Function that returns a stable id for an item.
            sync_func: DB-layer function that applies inserts/updates/deletes.
            name: Human label used in logs/reporting.

        Returns:
            None.
        """
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

        reporter = (
            SyncReportLogger(
                self.logger,
                entity=name,
                sanitize_text=self._eam_sanitizer.sanitize_text,
            )
            if self.logs
            else None
        )

        for eid in api_ids & cache_ids:
            old_item = cache_map[eid]
            new_item = api_map[eid]
            changes = self._diff_models(old_item, new_item)
            if changes:
                to_update.append((new_item, changes))

                if reporter is not None:
                    reporter.record_updated(
                        id=eid,
                        old_item=old_item,
                        new_item=new_item,
                        changes=changes,
                    )

        sync_func(to_insert=to_insert, to_update=to_update, to_delete=to_delete)

        if reporter is not None:
            for did in to_delete:
                old_item = cache_map.get(did)
                if old_item is not None:
                    reporter.record_deleted(id=did, item=old_item)

            for it in to_insert:
                reporter.record_added(id=get_id(it), item=it)

            reporter.emit()

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
        """Compute field-level differences between a cached model and a fresh model.

        Uses Pydantic v1 .dict() and an optional compare-field whitelist
        (avtools_compare_fields / _compare_fields). For EAM Equipment, it sanitizes
        dirty text values before comparing persisted fields.

        Args:
            old: Cached model instance from Postgres.
            new: Fresh model instance from the API.

        Returns:
            Mapping of changed field name -> new value (empty if no changes).
        """

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

        # Normalize dirty EAM strings on the fields we persist/diff against.
        if isinstance(new, Equipment):
            self._eam_sanitizer.sanitize_dict_in_place(
                new_data,
                compare_fields=compare_fields if compare_fields is not None else None,
            )

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
