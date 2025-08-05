from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, List

import requests

from avtools.io.logger import system_logger
from avtools.landb.config import LanDBConfig
from avtools.landb.device import LanDBDevice


class LanDBClient:
    """
    Helper for fetching and enriching LanDBDevice objects via REST API calls.
    """

    def __init__(
        self, session: requests.Session, config: LanDBConfig = LanDBConfig()
    ) -> None:
        """
        Initialize with an HTTP session and configuration.
        """
        self.session = session
        self.config = config

    def _fetch_data(
        self, endpoint: str, query: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """
        Execute a GET request against a relative endpoint and return list of records.
        """
        url = f"{self.config.base_url}/{endpoint}"
        response = self.session.get(url=url, params=query, verify=True)
        if response.status_code != 200:
            system_logger.error(f"LanDB request failed [{response.status_code}]: {url}")
            return None
        data = response.json()
        if not data:
            system_logger.info(f"No results for {endpoint} with query {query}")
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
        """
        Fetch records for a device filter and apply an update callback to each.
        """
        params = dict(query)
        params[key] = landb_device.serialnumber
        if records := self._fetch_data(endpoint, params):
            for rec in records:
                update_fn(rec)

    def get_data(
        self, equipmentno: str, serialnumber: str, eqclass: str, commissiondate: str
    ) -> LanDBDevice:
        """
        Instantiate a device and populate it with metadata then IP.
        """
        device = LanDBDevice.create_device(equipmentno, serialnumber, eqclass)
        system_logger.info(f"Fetching LanDB data for {equipmentno}/{serialnumber}")
        self.get_device(device)
        self.get_ip_address(device)
        device.log_device()
        return device

    def get_device(self, landb_device: LanDBDevice) -> None:
        """
        Retrieve and merge metadata into the given device instance.
        """
        self._update_device(
            landb_device,
            self.config.device_endpoint,
            self.config.device_query,
            "serialNumber.startsWith",
            landb_device.from_device,
        )

    def get_ip_address(self, landb_device: LanDBDevice) -> None:
        """
        Retrieve and merge IP address data into the given device instance.
        """
        self._update_device(
            landb_device,
            self.config.ip_endpoint,
            self.config.ip_address_query,
            "device.serialNumber.startsWith",
            landb_device.from_ip,
        )
