from __future__ import annotations

import asyncio
import zlib
from asyncio import run as asyncio_run
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Sequence
from time import time
from typing import Any, TypeVar

import structlog
from eam_rest_client import Equipment
from eam_rest_client.credentials import register_credentials
from eam_rest_client.grid_query import GridQuery
from landb_rest_client import register_credentials as landb_register_credentials
from landb_rest_client.models import Device, IPAddress

from avtools.exception.errors import (
    PipelineError,
    PostgresError,
    PostgresInventoryClientError,
    PostgresMonitoringClientError,
    SNMPError,
    SNMPQueryExecutionError,
    SNMPObserverRouterError,
    TimeseriesError,
    UtilsError,
)

# ---------------------------------------------------------------------------
# Optional rest-client exception imports
#
# The upstream REST clients may define additional typed exceptions. AVTools uses
# them when available, but falls back to placeholder types if the dependency
# version does not expose them.
# ---------------------------------------------------------------------------

try:  # pragma: no cover
    from eam_rest_client.exceptions import (  # type: ignore
        EamRestClientError,
        EamClientHTTPError,
        EamClientRetryableHTTPError,
        EamClientTimeoutError,
        EamClientTransportError,
        EamQueryError,
    )
except Exception:  # pragma: no cover

    class EamRestClientError(Exception):
        """Fallback EAM REST client base error.

        This placeholder is used when the installed ``eam_rest_client`` version does
        not expose typed exceptions.
        """

        pass

    class EamClientHTTPError(Exception):
        """Fallback error for non-retryable HTTP failures from EAM REST client."""

        pass

    class EamClientRetryableHTTPError(Exception):
        """Fallback error for retryable HTTP failures from EAM REST client."""

        pass

    class EamClientTimeoutError(Exception):
        """Fallback error raised on EAM request timeouts."""

        pass

    class EamClientTransportError(Exception):
        """Fallback error for low-level transport failures talking to EAM."""

        pass

    class EamQueryError(Exception):
        """Fallback error for EAM query construction/execution issues."""

        pass


try:  # pragma: no cover
    from landb_rest_client.exceptions import (  # type: ignore
        DataAwareValidationError,
        LanDBRestError,
        QuerySetError,
        TokenExpired,
    )
except Exception:  # pragma: no cover

    class TokenExpired(Exception):
        """Fallback error for expired OAuth tokens (LanDB REST client)."""

        pass

    class QuerySetError(Exception):
        """Fallback error for LanDB query/filter issues."""

        pass

    class LanDBRestError(Exception):
        """Fallback base error for LanDB REST client failures."""

        pass

    class DataAwareValidationError(Exception):
        """Fallback error for validation problems in LanDB REST client models."""

        pass


from avtools.postgres.client import PostgresClient, PostgresMonitoringClient
from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.client import SNMPClient, InterfaceResult, PingResult, ProbeResult, QueryResult
from avtools.pipeline import SNMPObserverRouter
from avtools.timeseries.models import MetricSample
from avtools.timeseries.otlp_publisher import OTLPMetricsPublisher, OTLPPublishError
from avtools.utils.eam_sanitizer import EAMTextSanitizer
from avtools.utils.sync_reporting import SyncReportLogger

Model = TypeVar("Model")


def device_in_shard(equipment_no: str, shard_index: int, shard_total: int) -> bool:
    """Return True if this device belongs to the caller's shard.

    Pure function of the device key: every pod runs the SAME hash over the SAME
    ``equipment_no`` and keeps only the rows where ``crc32 % N == my_index``. The
    union of all shards is the full fleet and their intersection is empty (each
    device maps to exactly one shard), so N pods partition the work with zero
    inter-pod coordination — the property that lets this stay masterless/AP.

    ``crc32`` is used deliberately instead of the builtin ``hash()``: ``hash()``
    is salted per process (``PYTHONHASHSEED``), so two pods would compute
    different partitions and silently create gaps + overlaps. ``crc32`` is
    stable across processes and hosts.
    """
    if shard_total <= 1:
        return True
    return zlib.crc32(equipment_no.encode("utf-8")) % shard_total == shard_index


class AVTools:
    """AV Tools orchestrator: EAM sync, LanDB sync, SNMP->Prometheus collection."""

    def __init__(self, dbod_url: str, logs: bool = False) -> None:
        """Initialize the AV Tools orchestrator.

        Args:
            dbod_url: Postgres/DBOD connection URL used by the cache client.
            logs: If True, emit per-entity sync reports (field-level diffs) to the logger.

        Returns:
            None.
        """
        self.dbod_url = dbod_url
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
        """Sync EAM assets and positions into the Postgres cache.

        This registers EAM credentials, then runs two independent sync flows:
        - **Assets/devices** from ``asset_grid`` (default: ``OSOBJA``)
        - **Positions** from ``position_grid`` (default: ``OSOBJP``)

        Each flow is guarded so failures are logged and the overall run can still
        complete (status becomes ``completed_with_errors``).

        Args:
            username: EAM username.
            password: EAM password.
            base_url: EAM base URL.
            asset_grid: EAM grid name for assets/devices.

            department_code: Department code prefix filter (e.g. ``"AV"``).
            limit: Optional row limit (useful for debugging).

        Returns:
            None.

        Notes:
            This method logs a start/end envelope with a concrete ``status`` string
            that can be used by systemd timers or log aggregation.
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
                register_credentials(base_url=base_url, user=username, password=password)
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

            except PostgresError as e:
                had_errors = True
                self.logger.exception("eam_devices_postgres_error", error=str(e))

            except UtilsError as e:
                had_errors = True
                self.logger.exception("eam_devices_utils_error", error=str(e))
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

            except PostgresError as e:
                had_errors = True
                self.logger.exception("eam_positions_postgres_error", error=str(e))

            except UtilsError as e:
                had_errors = True
                self.logger.exception("eam_positions_utils_error", error=str(e))
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
        *,
        base_url: str = "https://landb.cern.ch/api/",
    ) -> None:
        """Sync LanDB IP targets into Postgres by enriching the EAM snapshot.

        The LanDB sync uses the **current EAM devices snapshot** in Postgres as the
        source of truth for join keys. It then performs a small number of LanDB API
        calls to:
        1) Resolve LanDB devices by EAM serial numbers and/or names.
        2) Fetch IP addresses for those devices.
        3) Build ``CachedIPAddress`` domain objects and persist them.

        Args:
            client_id: OAuth client id for the LanDB REST client.
            client_secret: OAuth client secret.
            audience: OAuth audience (API identifier).
            max_workers: Kept for backwards compatibility (LanDB sync is single-shot).
            base_url: LanDB API base URL.

        Returns:
            None.

        Notes:
            Only devices with a usable SNMP target IP (IPv4 or IPv6) are written.
        """
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
        )

        try:
            try:
                # Load the current EAM snapshot from Postgres (source of truth for join keys).
                eam_list: list[Equipment] = self.dbod_helper.get_all_eam_devices()
                eam_count = len(eam_list)
            except PostgresError as e:
                status = "failed_load_eam_devices_postgres_error"
                self.logger.exception("landb_load_eam_devices_postgres_error", error=str(e))
                return
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
            except PostgresError as e:
                status = "failed_load_cached_devices_postgres_error"
                self.logger.exception("landb_load_cached_devices_postgres_error", error=str(e))
                return
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
            except PostgresError as e:
                had_errors = True
                status = "failed_sync_postgres_error"
                self.logger.exception("landb_sync_postgres_error", error=str(e))
                return
            except UtilsError as e:
                had_errors = True
                status = "failed_sync_utils_error"
                self.logger.exception("landb_sync_utils_error", error=str(e))
                return
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

    def _get_landb_ipaddresses(self, eam_records: list[Equipment]) -> list[CachedIPAddress]:
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
            """Normalize a value into a trimmed string.

            Args:
                v: Value to normalize.

            Returns:
                Trimmed string, or ``None`` if missing/blank.
            """
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
                ips = IPAddress.objects.filter(device__serial_number__in=device_serials).all()
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
    # Time-series + SNMP (Prometheus via MONIT OTLP)
    # ---------------------------------------------------------------------

    def _load_timeseries_targets_from_landb_ipaddresses(self) -> list[CachedIPAddress]:
        """Load SNMP/ping targets from the Postgres cache.

        Targets are loaded from the ``landb_ipaddresses`` cache table as
        :class:`~avtools.postgres.orm.landb_ipaddress.CachedIPAddress` domain
        objects. This removes the need for downstream duck-typing / key guessing:
        the SNMP pipeline consumes a single canonical model.

        Returns:
            List of CachedIPAddress targets with a non-empty ``equipment_no`` and ``ip``.

        Raises:
            PostgresError: If Postgres query fails.
        """
        try:
            targets = self.dbod_helper.get_all_landb_devices()
        except PostgresError:
            raise
        except Exception as e:
            raise PostgresError(f"Failed to load landb_ipaddresses targets: {e}") from e

        out: list[CachedIPAddress] = []
        for t in targets:
            equipment_no = (t.equipment_no or "").strip()
            ip = (t.ip or "").strip()

            if not equipment_no or not ip:
                continue

            # Some sources may store a CIDR. SNMP/ping expect a bare IP.
            if "/" in ip:
                ip = ip.split("/", 1)[0].strip()
            if not ip:
                continue

            # Mutate in-place (Pydantic v1 allow_mutation=True) so downstream uses the normalized IP.
            t.equipment_no = equipment_no
            t.ip = ip

            out.append(t)

        return out

    def run_snmp_timeseries(
        self,
        otlp_endpoint: str,
        monit_tenant: str,
        monit_password: str,
        max_workers: int = 8,
        service_name: str = "avtools",
        otlp_ca_file: str | None = None,
        otlp_insecure: bool = False,
        submitter_environment: str = "prod",
        submitter_hostgroup: str = "itdcim/av",
        availability_zone: str = "cern-geneva-b",
        shard_index: int = 0,
        shard_total: int = 1,
    ) -> None:
        """Collect ping/SNMP metrics and publish to Prometheus via MONIT OTLP.

        This replaces the old InfluxDB sink. Metrics are exported as Prometheus
        gauges through MONIT's OTLP endpoint (stored in Mimir).

        Metadata strategy (mirrors timeseries-dip):
          Layer 1 — OTel resource attributes on every ResourceMetrics envelope:
            service.name, service.instance.id, service.version, service.namespace.
          Layer 2 — global metric labels merged into every sample before export:
            job, submitter_environment, toplevel_hostgroup, submitter_hostgroup,
            region, availability_zone.
          Per-device labels on each MetricSample:
            equipmentno (mandatory join key), building, room, eq_class, model,
            category, hostname (all sourced from CachedIPAddress / EAM / LanDB).

        Args:
            otlp_endpoint:          OTLP gRPC endpoint in 'host:port' form.
            monit_tenant:           MONIT tenant name (Basic auth username).
            monit_password:         MONIT tenant password (Basic auth password).
            max_workers:            Number of concurrent worker tasks.
            service_name:           OTel resource service.name (default: avtools).
            otlp_ca_file:           Optional CA bundle path for gRPC TLS.
            otlp_insecure:          If True, use plaintext OTLP/gRPC (no TLS).
            submitter_environment:  Deployment environment ("prod" or "qa").
                                    Exposed as the ``submitter_environment`` label.
            submitter_hostgroup:    Full Puppet hostgroup path (e.g. "itdcim/av").
                                    Exposed as the ``submitter_hostgroup`` label.
            availability_zone:      CERN compute zone (e.g. "cern-geneva-b").
                                    Exposed as the ``availability_zone`` label.

        Returns:
            None.
        """
        run_started = time()
        status: str = "started"
        had_errors = False

        devices_total = 0
        samples_total = 0
        polled_total: int | None = None
        failed_total: int | None = None
        failed_equipment: int | None = None
        unreachable_targets: int | None = None
        collect_duration_s: float | None = None

        self.logger.info(
            "avtools_run_snmp_timeseries_start",
            otlp_endpoint=otlp_endpoint,
            tasks=max_workers,
        )

        try:
            # Layer 2: global deployment labels merged into every MetricSample.
            metric_labels: dict[str, str] = {
                "job": service_name,
                "submitter_environment": submitter_environment,
                "toplevel_hostgroup": "itdcim",
                "submitter_hostgroup": submitter_hostgroup,
                "region": "cern",
                "availability_zone": availability_zone,
            }

            try:
                devices = self._load_timeseries_targets_from_landb_ipaddresses()

                # Shard partition: keep only the devices that hash to THIS pod's
                # shard. Applied before device_lookup/collection so both the work
                # and the coverage denominator (`targeted`) are shard-scoped. With
                # shard_total == 1 this is a no-op and the unsharded deployment is
                # unaffected. `targeted` per shard is what the fleet-wide coverage
                # SLO sums back up across shards.
                if shard_total > 1:
                    devices_before_shard = len(devices)
                    devices = [
                        d
                        for d in devices
                        if d.equipment_no
                        and device_in_shard(d.equipment_no, shard_index, shard_total)
                    ]
                    self.logger.info(
                        "snmp_shard_applied",
                        shard_index=shard_index,
                        shard_total=shard_total,
                        devices_in_shard=len(devices),
                        devices_fleet=devices_before_shard,
                    )

                devices_total = len(devices)

                # Pre-compute per-device label dicts from EAM/LanDB metadata.
                # Keys are equipment_no strings; values are the enrichment labels
                # added on top of the mandatory 'equipmentno' label in encoder.py.
                device_lookup: dict[str, dict[str, str]] = {}
                for _d in devices:
                    if not _d.equipment_no:
                        continue
                    _extra: dict[str, str] = {}
                    for _k, _v in (
                        ("building", getattr(_d, "building", None)),
                        ("room", getattr(_d, "room", None)),
                        ("eq_class", getattr(_d, "eq_class", None)),
                        ("model", getattr(_d, "model", None)),
                        ("category", getattr(_d, "category", None)),
                        ("hostname", getattr(_d, "hostname", None)),
                    ):
                        if _v:
                            _extra[_k] = _v
                    device_lookup[_d.equipment_no] = _extra
            except PostgresInventoryClientError as e:
                status = "failed_load_landb_ipaddresses_postgres_error"
                self.logger.exception("snmp_load_landb_ipaddresses_postgres_error", error=str(e))
                return
            except PostgresError as e:
                status = "failed_load_landb_ipaddresses_postgres_error"
                self.logger.exception("snmp_load_landb_ipaddresses_postgres_error", error=str(e))
                return
            except Exception:
                status = "failed_load_landb_ipaddresses"
                self.logger.exception("snmp_load_landb_ipaddresses_failed")
                return

            if not devices:
                status = "skipped_no_targets"
                self.logger.info("No LanDB IP targets to monitor")
                return

            self.logger.info("Fetching LanDB IP targets", total=devices_total, tasks=max_workers)

            try:
                collect_started = time()
                ping_results, probe_results, query_results, interface_results = asyncio_run(
                    self._get_snmp_raw(devices, max_workers)
                )
                collect_duration_s = time() - collect_started
                polled_total = sum(1 for r in probe_results if r.up == 1)
                failed_total = devices_total - polled_total
                # Split the failures: equipment-mapped devices that are down (these emit
                # snmp_probe_failure and are the actionable signal) vs. targets with no
                # equipmentno (raw LanDB IPs never SNMP-managed — structural noise). The
                # two always reconcile: failed_equipment + unreachable_targets == failed.
                failed_equipment = sum(1 for r in probe_results if r.up == 0 and r.equipmentno)
                unreachable_targets = failed_total - failed_equipment
            except KeyboardInterrupt:
                status = "interrupted"
                raise
            except SNMPQueryExecutionError as e:
                status = "failed_snmp_collection"
                self.logger.exception("snmp_query_failed", error=str(e))
                return
            except SNMPError as e:
                status = "failed_snmp_collection"
                self.logger.exception("snmp_collection_failed", error=str(e))
                return
            except Exception:
                status = "failed_snmp_collection"
                self.logger.exception("snmp_collection_failed")
                return

            try:

                publisher = OTLPMetricsPublisher(
                    endpoint=otlp_endpoint,
                    tenant=monit_tenant,
                    password=monit_password,
                    service_name=service_name,
                    ca_file=otlp_ca_file,
                    insecure=otlp_insecure,
                    metric_labels=metric_labels,
                )

                pg_monitoring = PostgresMonitoringClient(self.dbod_url)

                router = SNMPObserverRouter(
                    timeseries_publisher=publisher,
                    postgres_monitoring=pg_monitoring,
                )

                routing_stats = router.process(
                    ping=ping_results,
                    probe=probe_results,
                    queries=query_results,
                    interfaces=interface_results,
                    device_lookup=device_lookup,
                    targeted=devices_total,
                    shard_index=shard_index,
                    shard_total=shard_total,
                    cycle_duration_s=collect_duration_s,
                )

                samples_total = routing_stats.ts_samples

            except KeyboardInterrupt:
                status = "interrupted"
                raise
            except OTLPPublishError as e:
                had_errors = True
                self.logger.exception("otlp_publish_failed", error=str(e))
            except PostgresMonitoringClientError as e:
                had_errors = True
                self.logger.exception("postgres_monitoring_failed", error=str(e))
            except PostgresError as e:
                had_errors = True
                self.logger.exception("postgres_failed", error=str(e))
            except SNMPObserverRouterError as e:
                had_errors = True
                self.logger.exception("snmp_routing_failed", error=str(e))
            except TimeseriesError as e:
                had_errors = True
                self.logger.exception("timeseries_failed", error=str(e))
            except PipelineError as e:
                had_errors = True
                self.logger.exception("pipeline_failed", error=str(e))
            except Exception:
                had_errors = True
                self.logger.exception("timeseries_publish_failed_unexpected")

            status = "ok" if not had_errors else "completed_with_errors"

        finally:
            duration_s = time() - run_started
            self.logger.info(
                "avtools_run_snmp_timeseries_end",
                status=status,
                duration_s=round(duration_s, 3),
                duration_ms=int(duration_s * 1000),
                devices=devices_total,
                samples=samples_total,
                otlp_endpoint=otlp_endpoint,
                tasks=max_workers,
            )

            # Contract event (OpenSearch): one per cycle. polled/failed and the two
            # breakdown counts are None if the cycle failed before the SNMP probe phase
            # (status conveys that). `failed` = failed_equipment + unreachable_targets;
            # alert on failed_equipment, not the raw failed total (see AVTOOLS-OPENSEARCH).
            self.logger.info(
                "cycle_summary",
                status=status,
                targeted=devices_total,
                polled=polled_total,
                failed=failed_total,
                failed_equipment=failed_equipment,
                unreachable_targets=unreachable_targets,
                duration_s=round(duration_s, 3),
                shard_index=shard_index,
                shard_total=shard_total,
            )

    async def _get_snmp_raw(
        self, devices: list[CachedIPAddress], max_workers: int
    ) -> tuple[
        list["PingResult"], list["ProbeResult"], list["QueryResult"], list["InterfaceResult"]
    ]:
        """Collect raw ping/probe/query results with global barriers between phases.

        This keeps the existing, efficient 3-phase pipeline:
          1) Ping -> subset of ping-online devices
          2) Barrier + redistribute -> SNMP probe -> subset of SNMP-available devices
          3) Barrier + redistribute -> Routed SNMP queries

        Returns:
            (ping_results, probe_results, query_results)
        """
        total = len(devices)
        if total == 0:
            return ([], [], [], [])

        max_workers = max(1, int(max_workers))

        # Concurrency ceiling: all blocking SNMP (probe/query/walks) is offloaded via
        # asyncio.to_thread, which uses the running loop's DEFAULT executor. Python's
        # default is only ~min(32, cpu+4) threads (~8 on a 4-core host), which — not the
        # semaphores — was the real cap. Since this work is I/O-wait (SNMP timeouts), not
        # CPU, we install an explicit pool sized by `threads` so the semaphores actually
        # bind. (Ping is native-async and unaffected.) Fresh loop per run (asyncio.run),
        # so this is scoped to this cycle; shut down before returning.
        loop = asyncio.get_running_loop()
        snmp_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="snmp-io")
        loop.set_default_executor(snmp_pool)
        self.logger.info("snmp_executor_sized", thread_pool_size=max_workers)

        def split_even(items: list[CachedIPAddress]) -> list[list[CachedIPAddress]]:
            """Split items across up to max_workers chunks (round-robin) for balance."""
            if not items:
                return []
            n = min(max_workers, len(items))
            chunks: list[list[CachedIPAddress]] = [[] for _ in range(n)]
            for i, item in enumerate(items):
                chunks[i % n].append(item)
            return [c for c in chunks if c]

        # Phase 1: Ping
        ping_chunks = split_even(devices)

        async def ping_worker(chunk: list[CachedIPAddress]):
            monitor = SNMPClient(targets=chunk)
            results, alive = await monitor.collect_ping()
            self.logger.info("snmp_ping_task_done", devices=len(chunk), alive=len(alive))
            return results, alive

        ping_done = await asyncio.gather(*(ping_worker(c) for c in ping_chunks))
        ping_results: list[PingResult] = []
        ping_alive: list[CachedIPAddress] = []
        for res, alive in ping_done:
            ping_results.extend(res)
            ping_alive.extend(alive)

        self.logger.info(
            "snmp_ping_phase_done",
            devices=total,
            alive=len(ping_alive),
            tasks=len(ping_chunks),
        )

        # Phase 2: SNMP probe (probe ALL targets; ICMP may be blocked even when SNMP works)
        probe_targets = devices
        probe_chunks = split_even(probe_targets)

        async def probe_worker(chunk: list[CachedIPAddress]):
            monitor = SNMPClient(targets=chunk)
            results, alive = await monitor.collect_snmp_probe(chunk)
            self.logger.info("snmp_probe_task_done", devices=len(chunk), alive=len(alive))
            return results, alive

        probe_done = await asyncio.gather(*(probe_worker(c) for c in probe_chunks))
        probe_results: list[ProbeResult] = []
        snmp_alive: list[CachedIPAddress] = []
        for res, alive in probe_done:
            probe_results.extend(res)
            snmp_alive.extend(alive)

        self.logger.info(
            "snmp_probe_phase_done",
            devices=len(ping_alive),
            alive=len(snmp_alive),
            tasks=len(probe_chunks),
        )

        # Phase 3: Queries (redistribute SNMP-alive devices)
        query_chunks = split_even(snmp_alive)

        async def query_worker(chunk: list[CachedIPAddress]):
            monitor = SNMPClient(targets=chunk)
            results = await monitor.collect_snmp_queries(chunk)
            self.logger.info("snmp_query_task_done", devices=len(chunk), results=len(results))
            return results

        query_done = await asyncio.gather(*(query_worker(c) for c in query_chunks))
        query_results: list[QueryResult] = []
        for res in query_done:
            query_results.extend(res)

        self.logger.info(
            "snmp_query_phase_done",
            devices=len(snmp_alive),
            results=len(query_results),
            tasks=len(query_chunks),
        )

        # Phase 4: MIB-II interfaces (universal; runs on all SNMP-alive devices)
        async def interface_worker(chunk: list[CachedIPAddress]):
            monitor = SNMPClient(targets=chunk)
            results = await monitor.collect_snmp_interfaces(chunk)
            self.logger.info("snmp_interface_task_done", devices=len(chunk), results=len(results))
            return results

        interface_done = await asyncio.gather(*(interface_worker(c) for c in query_chunks))
        interface_results: list[InterfaceResult] = []
        for res in interface_done:
            interface_results.extend(res)

        self.logger.info(
            "snmp_interface_phase_done",
            devices=len(snmp_alive),
            results=len(interface_results),
            interfaces=sum(len(r.interfaces) for r in interface_results),
            tasks=len(query_chunks),
        )

        # Observability: engine builds should be ~= pool threads (not ~= SNMP calls).
        from avtools.snmp.handlers.abstract_device_handler import engine_build_count

        self.logger.info(
            "snmp_collection_done",
            thread_pool_size=max_workers,
            engine_builds=engine_build_count(),
        )
        snmp_pool.shutdown(wait=False)
        return (ping_results, probe_results, query_results, interface_results)

    async def _get_snmp_samples(
        self, devices: list[CachedIPAddress], max_workers: int
    ) -> list[MetricSample]:
        """Backwards-compatible wrapper: encode numeric results to MetricSample.

        Text/identity-like fields (e.g. firmware) are intentionally excluded.
        """
        from avtools.timeseries.encoder import encode_all

        ping_results, probe_results, query_results, interface_results = await self._get_snmp_raw(
            devices, max_workers
        )
        return encode_all(
            ping=ping_results,
            probe=probe_results,
            queries=query_results,
            interfaces=interface_results,
        )

    def _publish_timeseries(
        self,
        samples: list[MetricSample],
        *,
        otlp_endpoint: str,
        monit_tenant: str,
        monit_password: str,
        service_name: str,
        otlp_ca_file: str | None = None,
        otlp_insecure: bool = False,
    ) -> None:
        """Publish samples to Prometheus via MONIT OTLP (gRPC)."""
        if not samples:
            self.logger.info("No metrics collected; skipping publish")
            return

        try:
            publisher = OTLPMetricsPublisher(
                endpoint=otlp_endpoint,
                tenant=monit_tenant,
                password=monit_password,
                service_name=service_name,
                ca_file=otlp_ca_file,
                insecure=otlp_insecure,
            )
            publisher.publish(samples)
            self.logger.info("otlp_publish_ok", samples=len(samples))
        except OTLPPublishError as e:
            self.logger.exception("otlp_publish_failed", error=str(e))
            raise
        except Exception as e:
            self.logger.exception("otlp_publish_failed_unexpected", error=str(e))
            raise

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
            """Canonicalize values for stable comparisons.

            Args:
                v: Value to normalize.

            Returns:
                ``None`` for empty strings, otherwise the input value.
            """
            return None if v == "" else v

        def to_dict(obj: Any, *, exclude_unset: bool) -> dict[str, Any]:
            """Convert a Pydantic v1 model into a plain dict.

            Args:
                obj: Pydantic model instance.
                exclude_unset: Whether to exclude unset values.

            Returns:
                Dict representation of the model.
            """
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
