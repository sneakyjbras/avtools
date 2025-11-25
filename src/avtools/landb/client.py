from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, List

import requests
import structlog

from avtools.landb.config import LanDBConfig
from avtools.landb.device import LanDBDevice


class LanDBClient:
    """REST client for fetching and enriching LanDBDevice objects."""

    def __init__(
        self, session: requests.Session, config: LanDBConfig = LanDBConfig()
    ) -> None:
        """Initialize with an HTTP session and configuration."""
        self.session = session
        self.config = config
        self.logger = structlog.get_logger(self.__class__.__name__)

    def _fetch_data(
        self, endpoint: str, query: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Execute a GET request and return records if any."""
        url = f"{self.config.base_url}/{endpoint}"
        response = self.session.get(url=url, params=query, verify=True)

        if response.status_code != 200:
            self.logger.error(
                "LanDB request failed",
                status_code=response.status_code,
                url=url,
            )
            return None

        data = response.json()
        if not data:
            self.logger.info(
                "No LanDB results",
                endpoint=endpoint,
                query=query,
            )
            return None

        return data

    def _update_device(
        self,
        landb_device: LanDBDevice,
        endpoint: str,
        query: dict[str, Any],
        key: str,
        update_fn: Callable[[dict[str, Any]], None],
    ) -> None:
        """Fetch records for a device and apply an update callback."""
        params = dict(query)
        params[key] = landb_device.serial_number
        if records := self._fetch_data(endpoint, params):
            for rec in records:
                update_fn(rec)

    def get_data(
        self,
        equipment_no: str,
        serial_number: str,
        eq_class: str,
        commission_date: str,
    ) -> LanDBDevice:
        """Instantiate a device and populate it with metadata and IP."""
        device = LanDBDevice.create_device(equipment_no, serial_number, eq_class)
        self.logger.info(
            "Fetching LanDB data",
            equipment_no=equipment_no,
            serial_number=serial_number,
        )
        self.get_device(device)
        self.get_ip_address(device)
        device.log_device()
        return device

    def get_device(self, landb_device: LanDBDevice) -> None:
        """Retrieve and merge metadata into the given device."""
        self._update_device(
            landb_device,
            self.config.device_endpoint,
            self.config.device_query,
            "serialNumber.startsWith",
            landb_device.from_device,
        )

    def get_ip_address(self, landb_device: LanDBDevice) -> None:
        """Retrieve and merge IP address data into the given device."""
        self._update_device(
            landb_device,
            self.config.ip_endpoint,
            self.config.ip_address_query,
            "device.serialNumber.startsWith",
            landb_device.from_ip,
        )
