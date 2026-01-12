from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urljoin

import requests
import structlog
from requests.exceptions import RequestException

from avtools.landb.config import LanDBConfig
from avtools.landb.device import LanDBDevice

landb_logger = structlog.get_logger(__name__).bind(
    component="landb",
    model="LanDBClient",
)


class LanDBClient:
    """REST client for fetching and enriching LanDBDevice objects."""

    # --- Lifecycle ---------------------------------------------------------

    def __init__(
        self,
        session: requests.Session | None = None,
        config: LanDBConfig | None = None,
    ) -> None:
        self._owns_session = session is None
        self.session = session or requests.Session()
        self.config = config or LanDBConfig()

    def __enter__(self) -> LanDBClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owns_session:
            self.session.close()

    # --- URL helpers -------------------------------------------------------

    def _resolve_url(self, endpoint: str) -> str:
        """Return an absolute URL for the given endpoint."""
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        return urljoin(self.config.base_url, endpoint.lstrip("/"))

    # --- HTTP helpers ------------------------------------------------------

    def _fetch_data(
        self,
        endpoint: str,
        query: Mapping[str, Any],
        *,
        log_if_empty: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Execute a GET request expecting JSON list or {'items': [...]}.

        Returns an empty list on error or if no records are found.
        """
        url = self._resolve_url(endpoint)

        try:
            resp = self.session.get(
                url=url,
                params=query,
                verify=self.config.verify,
                timeout=self.config.request_timeout,
            )
        except RequestException:
            landb_logger.error(
                "landb_request_exception",
                endpoint=endpoint,
                query=dict(query),
                exc_info=True,
            )
            return []

        if resp.status_code != 200:
            landb_logger.error(
                "landb_request_failed",
                status_code=resp.status_code,
                endpoint=endpoint,
                query=dict(query),
                body_preview=(resp.text or "")[:500],
            )
            return []

        try:
            data = resp.json()
        except ValueError:
            landb_logger.error(
                "landb_json_decode_failed",
                endpoint=endpoint,
                query=dict(query),
                text_preview=resp.text[:500],
                exc_info=True,
            )
            return []

        if not data:
            if log_if_empty:
                landb_logger.info(
                    "landb_no_results",
                    endpoint=endpoint,
                    query=dict(query),
                )
            return []

        # Accept a list or {"items": [...]}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return items
            landb_logger.warning(
                "landb_unexpected_payload_dict",
                endpoint=endpoint,
                query=dict(query),
                keys=list(data.keys())[:10],
            )
            return []

        landb_logger.warning(
            "landb_unexpected_payload_type",
            endpoint=endpoint,
            query=dict(query),
            type=type(data).__name__,
        )
        return []

    # --- Pagination --------------------------------------------------------

    def _fetch_all_pages(
        self, endpoint: str, base_params: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Offset/limit pagination using config parameter names."""
        results: list[dict[str, Any]] = []

        offset_key = self.config.offset_param
        limit_key = self.config.limit_param
        limit = int(base_params.get(limit_key, self.config.default_limit))
        offset = int(base_params.get(offset_key, 0))

        if limit <= 0:
            limit = max(1, self.config.default_limit)

        while True:
            params: dict[str, Any] = dict(base_params)
            params[offset_key] = offset
            params[limit_key] = limit

            chunk = self._fetch_data(endpoint, params, log_if_empty=False)
            if not chunk:
                break

            results.extend(chunk)
            if len(chunk) < limit:
                break

            offset += limit

        return results

    # --- Device enrichment -------------------------------------------------

    def _update_device(
        self,
        landb_device: LanDBDevice,
        endpoint: str,
        query: Mapping[str, Any],
        serial_filter_key: str,
        update_fn: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Fetch matching records and apply the provided updater to each."""
        serial = landb_device.serial_number
        if not serial:
            landb_logger.warning(
                "landb_missing_serialnumber",
                equipment_no=landb_device.equipment_no,
            )
            return

        base_params: dict[str, Any] = dict(query)
        base_params[serial_filter_key] = serial

        for rec in self._fetch_all_pages(endpoint, base_params):
            if not isinstance(rec, dict):
                landb_logger.warning(
                    "landb_skipping_non_dict_record",
                    endpoint=endpoint,
                    record_preview=str(rec)[:200],
                )
                continue
            try:
                update_fn(rec)
            except Exception:
                landb_logger.error(
                    "landb_update_fn_raised",
                    endpoint=endpoint,
                    record_preview=str(rec)[:300],
                    exc_info=True,
                )

    def populate_ip_address(self, landb_device: LanDBDevice) -> None:
        """Merge IP address data into the given device instance."""
        self._update_device(
            landb_device=landb_device,
            endpoint=self.config.ip_endpoint,
            query=self.config.ip_query_defaults,
            serial_filter_key=self.config.ip_serial_filter_key,
            update_fn=landb_device.from_ip,
        )

    # --- Public API --------------------------------------------------------

    def build_device_with_ip(
        self,
        equipment_no: str,
        serial_number: str,
        eq_class: str,
        manufacturer: str,
    ) -> LanDBDevice:
        """Create a device and enrich it with IP metadata."""
        print(equipment_no, serial_number, eq_class, manufacturer)
        device = LanDBDevice.create_device(
            equipment_no=equipment_no,
            serial_number=serial_number,
            eq_class=eq_class,
            manufacturer=manufacturer,
        )
        landb_logger.info(
            "landb_fetching_ip_data",
            equipment_no=equipment_no,
            serial_number=serial_number,
            eq_class=eq_class,
            manufacturer=manufacturer,
        )
        self.populate_ip_address(device)
        device.log_device()
        return device
