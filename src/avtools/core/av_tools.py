from __future__ import annotations

import asyncio
import zlib
from asyncio import run as asyncio_run
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import time
from typing import Any, TypeVar

import structlog
from eam_rest_client import Equipment
from eam_rest_client.credentials import register_credentials
from eam_rest_client.grid_query import GridQuery
from landb_rest_client import register_credentials as landb_register_credentials
from landb_rest_client.models import Device, IPAddress

from avtools.exception.errors import (
    MassDeleteRefused,
    PipelineError,
    PostgresError,
    PostgresInventoryClientError,
    PostgresMonitoringClientError,
    SNMPError,
    SNMPObserverRouterError,
    SNMPQueryExecutionError,
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
        EamClientHTTPError,
        EamClientRetryableHTTPError,
        EamClientTimeoutError,
        EamClientTransportError,
        EamQueryError,
        EamRestClientError,
    )
except Exception:  # pragma: no cover

    class EamRestClientError(Exception):
        """Fallback EAM REST client base error.

        This placeholder is used when the installed ``eam_rest_client`` version does
        not expose typed exceptions.
        """

    class EamClientHTTPError(Exception):
        """Fallback error for non-retryable HTTP failures from EAM REST client."""

    class EamClientRetryableHTTPError(Exception):
        """Fallback error for retryable HTTP failures from EAM REST client."""

    class EamClientTimeoutError(Exception):
        """Fallback error raised on EAM request timeouts."""

    class EamClientTransportError(Exception):
        """Fallback error for low-level transport failures talking to EAM."""

    class EamQueryError(Exception):
        """Fallback error for EAM query construction/execution issues."""


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

    class QuerySetError(Exception):
        """Fallback error for LanDB query/filter issues."""

    class LanDBRestError(Exception):
        """Fallback base error for LanDB REST client failures."""

    class DataAwareValidationError(Exception):
        """Fallback error for validation problems in LanDB REST client models."""


from avtools.algorithms.room_resolver import RoomResolver
from avtools.pipeline import SNMPObserverRouter, cycle_guardrail_samples
from avtools.postgres.client import PostgresClient, PostgresMonitoringClient
from avtools.postgres.inventory.orm.eam_room import EAMRoom
from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.snmp.client import InterfaceResult, PingResult, ProbeResult, QueryResult, SNMPClient
from avtools.timeseries.models import MetricSample
from avtools.timeseries.otlp_publisher import (
    DEFAULT_ENCODING,
    DEFAULT_PROTOCOL,
    OTLPMetricsPublisher,
    OTLPPublishError,
)
from avtools.utils.eam_sanitizer import EAMTextSanitizer
from avtools.utils.sync_reporting import SyncReportLogger

Model = TypeVar("Model")

# ---------------------------------------------------------------------------
# LanDB request-parameter budget  (why we chunk every `__in` lookup)
# ---------------------------------------------------------------------------
# LanDB is served by Tomcat, which HARD-REJECTS any request carrying more than
# 1000 HTTP parameters (GET plus POST) with:
#
#   requests.exceptions.HTTPError: 422 Client Error: More than the maximum
#   number of request parameters (GET plus POST) for a single request ([1,000])
#   were detected.
#
# `landb_rest_client` encodes an `__in` filter as ONE query parameter PER VALUE
# (`serialNumber.in=AAA&serialNumber.in=BBB&...`) and always adds `_limit` and
# `_offset`, so a single `filter(field__in=values)` call costs
# ``len(values) + 2`` request parameters — it does NOT matter how long the
# individual values are.
#
# With ~4.5k EAM devices (3749 serials / 3231 names in production on
# 2026-07-30) one un-chunked call is ~3.7k parameters: every LanDB fetch 422s,
# the failure is swallowed as a warning, the reconciler sees an EMPTY API result
# and deletes the entire fleet cache. Hence every `__in` lookup is split into
# chunks of LANDB_IN_CHUNK_SIZE and the results merged.
#
# 800 — and NOT 998, and definitely not 5000 — deliberately keeps ~200
# parameters of headroom under the ceiling for `_limit`/`_offset` and for any
# extra filter a future caller may add to the same query. Do not raise it
# without re-verifying the server-side limit below.
LANDB_MAX_REQUEST_PARAMS = 1000
LANDB_IN_CHUNK_SIZE = 800

# ---------------------------------------------------------------------------
# Mass-delete circuit breaker
# ---------------------------------------------------------------------------
# `_sync_entities` derives deletes from `cached_ids - api_ids`, so ANY upstream
# fetch that comes back empty or near-empty is indistinguishable from "the whole
# fleet was decommissioned" — and on 2026-07-30 that wiped the entire
# landb_ipaddresses table while the job still logged status=ok.
#
# Refuse to delete more than this fraction of the existing cache in a single run.
# A genuine mass decommission is rare, and re-running it deliberately with
# ``allow_mass_delete=True`` costs minutes; a silent wipe costs the whole
# monitoring signal until someone notices the blank dashboards.
SYNC_MAX_DELETE_FRACTION = 0.5


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
        # True when the last _get_landb_ipaddresses() call returned a PARTIAL view
        # of LanDB (at least one chunked lookup failed). run_landb() reads it and
        # refuses to delete cached rows in that case.
        self._landb_fetch_degraded = False
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
    # Rooms (precomputed device -> room mapping)
    # ---------------------------------------------------------------------

    def sync_rooms(self) -> None:
        """Recompute the device -> room mapping and persist the diff into eam_rooms.

        Reads only the Postgres inventory cache (no EAM/LanDB credentials needed):
        it loads device anchors + the position tree, runs the pure RoomResolver
        over ALL devices in memory, then writes only the rows that changed. This
        full-recompute + diff-write satisfies the "new device / moved position"
        triggers AND catches upstream position-tree changes that alter a resolved
        room, while writing only diffs. Devices no longer present in ``eam_devices``
        are deleted from ``eam_rooms``.

        Returns:
            None.

        Notes:
            Logs a start/end envelope with a concrete ``status`` string, mirroring
            run_eam / run_landb.
        """
        run_started = time()
        status: str = "started"
        had_errors = False

        devices_total = 0
        positions_total = 0
        resolved_total = 0

        self.logger.info("avtools_sync_rooms_start")

        try:
            try:
                device_rows, position_rows = self.dbod_helper.get_eam_room_inputs()
                devices_total = len(device_rows)
                positions_total = len(position_rows)
            except PostgresError as e:
                status = "failed_load_inputs_postgres_error"
                self.logger.exception("rooms_load_inputs_postgres_error", error=str(e))
                return
            except Exception:
                status = "failed_load_inputs"
                self.logger.exception("rooms_load_inputs_failed")
                return

            if not device_rows:
                status = "skipped_no_devices"
                self.logger.info("No EAM devices—skipping rooms sync")
                return

            resolver = RoomResolver.from_rows(position_rows)
            resolved_rooms = self._resolve_rooms(resolver, device_rows)
            resolved_total = len(resolved_rooms)

            try:
                cached_rooms = self.dbod_helper.get_all_eam_rooms()
            except PostgresError as e:
                status = "failed_load_cached_rooms_postgres_error"
                self.logger.exception("rooms_load_cached_rooms_postgres_error", error=str(e))
                return
            except Exception:
                status = "failed_load_cached_rooms"
                self.logger.exception("rooms_load_cached_rooms_failed")
                return

            try:
                self._sync_entities(
                    api_items=resolved_rooms,
                    cached_items=cached_rooms,
                    get_id=lambda r: str(r.equipment_no),
                    sync_func=self.dbod_helper.sync_eam_rooms,
                    name="EAM Rooms",
                )
            except PostgresError as e:
                had_errors = True
                status = "failed_sync_postgres_error"
                self.logger.exception("rooms_sync_postgres_error", error=str(e))
                return
            except UtilsError as e:
                had_errors = True
                status = "failed_sync_utils_error"
                self.logger.exception("rooms_sync_utils_error", error=str(e))
                return
            except Exception:
                had_errors = True
                status = "failed_sync"
                self.logger.exception("rooms_sync_failed")
                return

            status = "ok" if not had_errors else "completed_with_errors"

        except KeyboardInterrupt:
            status = "interrupted"
            raise
        finally:
            duration_s = time() - run_started
            self.logger.info(
                "avtools_sync_rooms_end",
                status=status,
                duration_s=round(duration_s, 3),
                duration_ms=int(duration_s * 1000),
                devices=devices_total,
                positions=positions_total,
                resolved=resolved_total,
            )

    def _resolve_rooms(
        self, resolver: RoomResolver, device_rows: list[tuple[str, str | None]]
    ) -> list[EAMRoom]:
        """Build an :class:`EAMRoom` for every device using the resolver.

        Args:
            resolver: In-memory room resolver built from the position tree.
            device_rows: ``(equipmentno, position)`` pairs for every device.

        Returns:
            One :class:`EAMRoom` per device, all stamped with the same
            resolution time.
        """
        resolved_at = datetime.utcnow()
        return [
            EAMRoom(
                equipment_no=str(equipment_no),
                parent_position=position,
                room_no=resolver.resolve_one(position),
                resolved_at=resolved_at,
            )
            for equipment_no, position in device_rows
        ]

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
    ) -> str:
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
            The terminal status string, identical to the ``status`` field of the
            ``avtools_run_landb_end`` event: ``"ok"``, ``"skipped_no_eam_devices"``
            or one of the ``failed_*`` values. The CLI uses it to decide the process
            exit code and whether to publish the LanDB heartbeat — errors are handled
            here, so a plain ``return`` would tell the caller nothing.

        Notes:
            Only devices with a usable SNMP target IP (IPv4 or IPv6) are written.

            A DEGRADED fetch (``failed_landb_fetch_degraded``) means at least one
            chunked LanDB lookup failed, so the API view is partial. Such a run
            still applies inserts/updates but deletes NOTHING, because
            ``cached_ids - api_ids`` on a partial view deletes live devices.
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
                return status
            except Exception:
                status = "failed_load_eam_devices"
                self.logger.exception("landb_load_eam_devices_failed")
                return status

            if not eam_list:
                status = "skipped_no_eam_devices"
                self.logger.info("No EAM devices—skipping LanDB sync")
                return status

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
                return status
            except QuerySetError as e:
                status = "failed_client_init_queryset_error"
                self.logger.error("landb_client_init_queryset_error", error=str(e))
                return status
            except LanDBRestError as e:
                status = "failed_client_init"
                self.logger.error("landb_client_init_failed", error=str(e))
                return status
            except Exception:
                status = "failed_client_init_unexpected"
                self.logger.exception("landb_client_init_failed_unexpected")
                return status

            # Cleared before the fetch so a partially-failed lookup cannot be
            # inherited from a previous run on the same instance.
            self._landb_fetch_degraded = False
            try:
                landb_ips = self._get_landb_ipaddresses(eam_list)
                enriched_ip_count = len(landb_ips)
            except TokenExpired as e:
                status = "failed_fetch_token_expired"
                self.logger.error("landb_token_expired", error=str(e))
                return status
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
                return status
            except QuerySetError as e:
                status = "failed_fetch_queryset_error"
                self.logger.error("landb_queryset_error", error=str(e))
                return status
            except LanDBRestError as e:
                status = "failed_fetch"
                self.logger.error("landb_fetch_failed", error=str(e))
                return status
            except Exception:
                status = "failed_fetch_unexpected"
                self.logger.exception("landb_fetch_failed_unexpected")
                return status

            # A degraded fetch means the LanDB view we just built is PARTIAL: rows
            # are missing because requests failed, not because the devices are gone.
            # Reconciling that destructively is what emptied the fleet table.
            fetch_degraded = bool(getattr(self, "_landb_fetch_degraded", False))
            if fetch_degraded:
                self.logger.error(
                    "landb_fetch_degraded",
                    eam_devices=eam_count,
                    enriched_ipaddresses=enriched_ip_count,
                    reason="one_or_more_landb_lookups_failed",
                    action="deletes_suppressed_run_marked_failed",
                )

            try:
                cache_list = self.dbod_helper.get_all_landb_devices()
                cached_count = len(cache_list)
            except PostgresError as e:
                status = "failed_load_cached_devices_postgres_error"
                self.logger.exception("landb_load_cached_devices_postgres_error", error=str(e))
                return status
            except Exception:
                status = "failed_load_cached_devices"
                self.logger.exception("landb_load_cached_devices_failed")
                return status

            try:
                self._sync_entities(
                    api_items=landb_ips,
                    cached_items=cache_list,
                    get_id=self._landb_get_id,
                    sync_func=self.dbod_helper.sync_landb_devices,
                    name="LanDB IPAddress",
                    # Freshness is expendable, the inventory is not.
                    allow_deletes=not fetch_degraded,
                )
            except MassDeleteRefused as e:
                had_errors = True
                status = "failed_sync_mass_delete_refused"
                self.logger.exception("landb_sync_mass_delete_refused", error=str(e))
                return status
            except PostgresError as e:
                had_errors = True
                status = "failed_sync_postgres_error"
                self.logger.exception("landb_sync_postgres_error", error=str(e))
                return status
            except UtilsError as e:
                had_errors = True
                status = "failed_sync_utils_error"
                self.logger.exception("landb_sync_utils_error", error=str(e))
                return status
            except Exception:
                had_errors = True
                status = "failed_sync"
                self.logger.exception("landb_sync_failed")
                return status

            if fetch_degraded:
                # Inserts/updates were applied, deletes were not: this run did NOT
                # produce a trustworthy snapshot, so it must not read as `ok` and
                # must not refresh the LanDB heartbeat (the CLI keys off this).
                status = "failed_landb_fetch_degraded"
                return status

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

        return status

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

    def _landb_fetch_in_chunks(
        self,
        *,
        model: Any,
        filter_key: str,
        values: Sequence[str],
        key_of: Callable[[Any], str | None],
        failure_event: str,
        count_field: str,
        chunk_size: int = LANDB_IN_CHUNK_SIZE,
    ) -> tuple[dict[str, Any], bool]:
        """Run one ``field__in`` LanDB lookup in chunks and merge the results.

        Splitting is mandatory, not an optimisation: see LANDB_IN_CHUNK_SIZE for
        the 1000-request-parameter Tomcat ceiling that a single large ``__in``
        list blows through.

        Args:
            model:         LanDB REST model class exposing ``.objects.filter``
                           (``Device`` / ``IPAddress``). Accessed lazily so an
                           empty ``values`` list performs no API work at all.
            filter_key:    Filter kwarg to use, e.g. ``"serial_number__in"``.
            values:        Full list of values to look up (already de-duplicated
                           and normalized by the caller).
            key_of:        Extracts the merge key from a returned record. Records
                           with a falsy key are dropped, exactly as before.
            failure_event: Log event name emitted (warning) for a failed chunk.
                           Kept identical to the pre-chunking event names.
            count_field:   Field name carrying the TOTAL value count on that
                           warning event (``serial_count`` / ``name_count``),
                           again identical to the pre-chunking payload.
            chunk_size:    Values per request (default LANDB_IN_CHUNK_SIZE).

        Returns:
            ``(results, degraded)``:

            * ``results`` maps ``key_of(record)`` -> record. Chunks are processed
              in order and a key already present is never overwritten, so the
              "first wins" de-duplication of the un-chunked code is preserved
              across chunk boundaries as well.
            * ``degraded`` is True when AT LEAST ONE chunk failed. A degraded
              lookup is NOT the same as "no rows for those values": the merged
              result is a partial view and callers must not treat it as
              authoritative (see :meth:`run_landb`, which suppresses deletes).
        """
        results: dict[str, Any] = {}
        if not values:
            return results, False

        chunks = [list(values[i : i + chunk_size]) for i in range(0, len(values), chunk_size)]
        chunk_count = len(chunks)
        degraded = False

        for chunk_index, chunk in enumerate(chunks):
            try:
                for rec in model.objects.filter(**{filter_key: chunk}).all():
                    k = key_of(rec)
                    if k and k not in results:
                        results[k] = rec
            except Exception:
                degraded = True
                self.logger.warning(
                    failure_event,
                    exc_info=True,
                    **{
                        count_field: len(values),
                        "chunk_index": chunk_index,
                        "chunk_count": chunk_count,
                        "chunk_values": len(chunk),
                        "chunk_size": chunk_size,
                    },
                )

        self.logger.info(
            "landb_chunked_fetch_done",
            filter_key=filter_key,
            values=len(values),
            chunk_count=chunk_count,
            chunk_size=chunk_size,
            matched=len(results),
            degraded=degraded,
        )

        return results, degraded

    def _get_landb_ipaddresses(self, eam_records: list[Equipment]) -> list[CachedIPAddress]:
        """Bulk LanDB lookup in 4 logical queries (each chunked into N requests).

        Strategy:
        1) Fetch LanDB Devices by EAM serial_number (Device.serial_number__in).
        2) For EAM records not found via serial (and those lacking a serial), fetch Devices by
           EAM description (Device.name__in).
        3) Using the matched LanDB Devices, fetch IPAddresses by device serial_number, then
           fallback by device name.

        Every one of those four ``__in`` lookups is executed in chunks of
        LANDB_IN_CHUNK_SIZE values via :meth:`_landb_fetch_in_chunks` because
        LanDB/Tomcat rejects requests with more than 1000 parameters and the REST
        client spends one parameter per ``__in`` value (see LANDB_IN_CHUNK_SIZE).

        Output:
        - List[CachedIPAddress] enriched with EAM keys (equipment_no, class_code, etc.).
        - Only includes devices that have a usable SNMP target IP (LanDB ipv4 or ipv6).

        Side effect:
        - Sets ``self._landb_fetch_degraded`` to True when any chunk of any of the
          four lookups failed, i.e. the returned list is a PARTIAL view of LanDB
          rather than an authoritative one. :meth:`run_landb` reads that flag and
          refuses to delete cached rows on a degraded fetch.

        IMPORTANT CORRELATION:
        - (EAM) Equipment.serial_number == (LanDB) Device.serial_number
        - (LanDB) Device.name == (LanDB) IPAddress.device   (NOT IPAddress.name)
        """
        # Reset per call; every chunked lookup below ORs its own failure into it.
        self._landb_fetch_degraded = False

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
        devices_by_serial, degraded = self._landb_fetch_in_chunks(
            model=Device,
            filter_key="serial_number__in",
            values=eam_serials,
            key_of=lambda d: norm(getattr(d, "serial_number", None)),
            failure_event="landb_device_fetch_by_serial_failed",
            count_field="serial_count",
        )
        self._landb_fetch_degraded = self._landb_fetch_degraded or degraded

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

        devices_by_name, degraded = self._landb_fetch_in_chunks(
            model=Device,
            filter_key="name__in",
            values=names_to_query,
            key_of=lambda d: norm(getattr(d, "name", None)),
            failure_event="landb_device_fetch_by_name_failed",
            count_field="name_count",
        )
        self._landb_fetch_degraded = self._landb_fetch_degraded or degraded

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
        ips_by_device, degraded = self._landb_fetch_in_chunks(
            model=IPAddress,
            filter_key="device__serial_number__in",
            values=device_serials,
            key_of=lambda ip: norm(getattr(ip, "device", None)),
            failure_event="landb_ipaddress_fetch_by_serial_failed",
            count_field="serial_count",
        )
        self._landb_fetch_degraded = self._landb_fetch_degraded or degraded

        # --- (4) Fallback IPAddresses by device name ----------------------
        missing_ip_names = [n for n in device_names if n not in ips_by_device]
        ips_by_name, degraded = self._landb_fetch_in_chunks(
            model=IPAddress,
            filter_key="device__name__in",
            values=missing_ip_names,
            key_of=lambda ip: norm(getattr(ip, "device", None)),
            failure_event="landb_ipaddress_fetch_by_name_failed",
            count_field="name_count",
        )
        self._landb_fetch_degraded = self._landb_fetch_degraded or degraded

        # Merge the fallback in: keys already resolved by the serial lookup win,
        # matching the previous single-dict "first wins" behaviour.
        for k, ip_rec in ips_by_name.items():
            if k not in ips_by_device:
                ips_by_device[k] = ip_rec

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
        otlp_protocol: str = DEFAULT_PROTOCOL,
        otlp_encoding: str = DEFAULT_ENCODING,
        submitter_environment: str = "prod",
        submitter_hostgroup: str = "itdcim/av",
        availability_zone: str = "cern-geneva-b",
        shard_index: int = 0,
        shard_total: int = 1,
        priority: str = "all",
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
            otlp_endpoint:          MONIT OTLP endpoint: a full OTLP/HTTP URL
                                    ('https://host:4319/v1/metrics') or the legacy
                                    OTLP/gRPC 'host:port' form, which is migrated
                                    automatically.
            monit_tenant:           MONIT tenant name (Basic auth username).
            monit_password:         MONIT tenant password (Basic auth password).
            max_workers:            Number of concurrent worker tasks.
            service_name:           OTel resource service.name (default: avtools).
            otlp_ca_file:           Optional PEM CA bundle. Defaults to the CERN
                                    chain bundled in the wheel on the HTTP path.
            otlp_insecure:          Plaintext OTLP/gRPC. Ignored on the HTTP path,
                                    which always verifies TLS.
            otlp_protocol:          'http' (default, TLS) or 'grpc' (deprecated
                                    plaintext rollback path).
            otlp_encoding:          OTLP/HTTP payload encoding ('protobuf'/'json').
            submitter_environment:  Deployment environment ("prod" or "qa").
                                    Exposed as the ``submitter_environment`` label.
            submitter_hostgroup:    Full Puppet hostgroup path (e.g. "itdcim/av").
                                    Exposed as the ``submitter_hostgroup`` label.
            availability_zone:      CERN compute zone (e.g. "cern-geneva-b").
                                    Exposed as the ``availability_zone`` label.
            priority:               Publish-time priority tier filter. ``"all"``
                                    (default) publishes every metric — today's
                                    behaviour. A tier ("critical"/"high"/"medium"/
                                    "low") publishes only that tier's device
                                    metrics plus the ALWAYS guardrails; collection
                                    is unchanged. Passed to
                                    :meth:`SNMPObserverRouter.process`.

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
            priority=priority,
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
                # This return is BEFORE snmp_router.process, so the cycle
                # guardrails would never be published and the series would VANISH
                # from MonIT instead of going to zero. Four Grafana SLO alerts read
                # absence as health, which is why an empty fleet table looked
                # healthy on every dashboard. Publish the same three ALWAYS series
                # (same builder, same conditional shard/tier labels) with zeros so
                # the series stays continuous and the alerts can fire.
                self._publish_zero_cycle_guardrails(
                    otlp_endpoint=otlp_endpoint,
                    monit_tenant=monit_tenant,
                    monit_password=monit_password,
                    service_name=service_name,
                    otlp_ca_file=otlp_ca_file,
                    otlp_insecure=otlp_insecure,
                    otlp_protocol=otlp_protocol,
                    otlp_encoding=otlp_encoding,
                    metric_labels=metric_labels,
                    shard_index=shard_index,
                    shard_total=shard_total,
                    priority=priority,
                )
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
                    protocol=otlp_protocol,
                    encoding=otlp_encoding,
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
                    priority=priority,
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

    def _publish_zero_cycle_guardrails(
        self,
        *,
        otlp_endpoint: str,
        monit_tenant: str,
        monit_password: str,
        service_name: str,
        otlp_ca_file: str | None,
        otlp_insecure: bool,
        metric_labels: dict[str, str],
        otlp_protocol: str = DEFAULT_PROTOCOL,
        otlp_encoding: str = DEFAULT_ENCODING,
        shard_index: int,
        shard_total: int,
        priority: str,
    ) -> None:
        """Publish the cycle coverage guardrails with value 0 for a skipped cycle.

        Used on the ``skipped_no_targets`` path, which returns before the router
        runs. Without this the ``avtools_snmp_devices_targeted`` /
        ``_devices_polled`` / ``_coverage_ratio`` series simply stop being written:
        MonIT/Mimir then has no data points at all rather than zeros, and Grafana
        SLO alerts built on those series read the absence as health.

        Samples come from the SAME builder the normal path uses
        (:func:`~avtools.pipeline.snmp_router.cycle_guardrail_samples`), so the
        metric set and the conditional ``shard`` / ``tier`` labels cannot drift
        apart. Only the OTLP publisher is constructed — no Postgres monitoring
        client, since a skipped cycle has no text rows to write.

        Failures are logged and swallowed: a missing guardrail must not turn a
        "nothing to do" cycle into a crash.

        Args:
            otlp_endpoint:  MONIT OTLP endpoint (URL or legacy 'host:port').
            monit_tenant:   MONIT tenant (Basic auth user).
            monit_password: MONIT tenant password.
            service_name:   OTel resource service.name.
            otlp_ca_file:   Optional PEM CA bundle for the OTLP transport.
            otlp_insecure:  Use plaintext OTLP/gRPC (gRPC path only).
            metric_labels:  Layer-2 global labels (same dict as the normal path).
            otlp_protocol:  'http' (default) or 'grpc' (deprecated).
            otlp_encoding:  OTLP/HTTP payload encoding ('protobuf'/'json').
            shard_index:    This pod's shard index.
            shard_total:    Total shards (1 = unsharded).
            priority:       ``--priority`` token in force this cycle.

        Returns:
            None.
        """
        try:
            publisher = OTLPMetricsPublisher(
                endpoint=otlp_endpoint,
                tenant=monit_tenant,
                password=monit_password,
                service_name=service_name,
                ca_file=otlp_ca_file,
                insecure=otlp_insecure,
                protocol=otlp_protocol,
                encoding=otlp_encoding,
                metric_labels=metric_labels,
            )
            # targeted=0/polled=0: the guardrails are Priority.ALWAYS, so they are
            # published under every --priority tier by definition.
            samples = cycle_guardrail_samples(
                targeted=0,
                polled=0,
                shard_index=shard_index,
                shard_total=shard_total,
                priority=priority,
            )
            publisher.publish(samples)
            self.logger.info(
                "snmp_zero_guardrails_published",
                samples=len(samples),
                shard_index=shard_index,
                shard_total=shard_total,
                priority=priority,
            )
        except Exception:
            self.logger.exception("snmp_zero_guardrail_publish_failed")

    async def _get_snmp_raw(
        self, devices: list[CachedIPAddress], max_workers: int
    ) -> tuple[list[PingResult], list[ProbeResult], list[QueryResult], list[InterfaceResult]]:
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
        otlp_protocol: str = DEFAULT_PROTOCOL,
        otlp_encoding: str = DEFAULT_ENCODING,
    ) -> None:
        """Publish samples to Prometheus via MONIT OTLP (HTTP by default)."""
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
                protocol=otlp_protocol,
                encoding=otlp_encoding,
            )
            confirmed = publisher.publish(samples)
            if confirmed:
                self.logger.info("otlp_publish_ok", samples=len(samples))
            else:
                # Export not confirmed by MONIT (the k8s-only gap). Collection +
                # DB write succeeded, so do NOT fail the cycle — log it so the
                # gap is visible/alertable instead of silent.
                self.logger.error(
                    "otlp_publish_unconfirmed",
                    samples=len(samples),
                    endpoint=otlp_endpoint,
                )
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
        *,
        allow_deletes: bool = True,
        allow_mass_delete: bool = False,
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
            allow_deletes: When False, inserts/updates are applied but NOTHING is
                deleted. Callers set this when they know the API view they just
                fetched is incomplete (e.g. a degraded LanDB fetch): a partial
                snapshot must never be reconciled destructively.
            allow_mass_delete: Explicit opt-out of the mass-delete circuit breaker
                below. Only pass True for a deliberate, verified mass
                decommission.

        Returns:
            None.

        Raises:
            MassDeleteRefused: If the computed delete set exceeds
                SYNC_MAX_DELETE_FRACTION of the cache and ``allow_mass_delete`` is
                False. Inserts/updates are still applied first; the raise exists so
                the caller records the run as failed instead of ``ok``.
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

        # --- Delete guards -------------------------------------------------
        # (1) The caller knows the API view is partial: apply freshness, never
        #     destruction. Losing an update is recoverable next cycle; losing the
        #     inventory table takes the whole SNMP collection down with it.
        if to_delete and not allow_deletes:
            self.logger.warning(
                "sync_deletes_suppressed",
                entity=name,
                would_delete=len(to_delete),
                cached=len(cache_ids),
                api_items=len(api_ids),
                reason="upstream_fetch_degraded",
            )
            to_delete = []

        # (2) Circuit breaker, independent of (1) and of any caller flag: a delete
        #     set covering most of the cache is far more likely to be an upstream
        #     fetch failure than a real mass decommission. Refuse it, apply only
        #     inserts/updates, and fail the run (raise below, after the normal
        #     reporting so the diff is still observable).
        refused_mass_delete = 0
        if to_delete and not allow_mass_delete:
            delete_fraction = len(to_delete) / len(cache_ids)
            if delete_fraction > SYNC_MAX_DELETE_FRACTION:
                refused_mass_delete = len(to_delete)
                self.logger.error(
                    "sync_mass_delete_refused",
                    entity=name,
                    would_delete=refused_mass_delete,
                    cached=len(cache_ids),
                    api_items=len(api_ids),
                    delete_fraction=round(delete_fraction, 4),
                    max_delete_fraction=SYNC_MAX_DELETE_FRACTION,
                )
                to_delete = []

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

        if refused_mass_delete:
            raise MassDeleteRefused(
                f"{name}: refused to delete {refused_mass_delete} of {len(cache_ids)} cached "
                f"rows (> {SYNC_MAX_DELETE_FRACTION:.0%} of the cache). Inserts/updates were "
                "applied, deletes were not. Re-run with allow_mass_delete=True if this "
                "decommission is intentional."
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
