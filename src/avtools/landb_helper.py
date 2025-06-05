from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, ConfigDict

from avtools.logger import system_logger


@dataclass
class LanDBConfig:
    """
    Configuration for LanDB API endpoints and default query parameters.

    Attributes:
        base_url: Base URL for all API requests.
        device_endpoint: Endpoint path for retrieving device metadata.
        ip_endpoint: Endpoint path for retrieving IP address data.
        device_query: Default pagination and filter settings for metadata calls.
        ip_address_query: Default pagination and filter settings for IP calls.
    """

    base_url: str = "https://landb.cern.ch/api"
    device_endpoint: str = "beta/devices"
    ip_endpoint: str = "beta/ip-addresses"

    device_query: dict[str, Any] = field(
        default_factory=lambda: {
            "_offset": 0,
            "_limit": 100,
            "serialNumber.startsWith": "",
        }
    )

    ip_address_query: dict[str, Any] = field(
        default_factory=lambda: {
            "_offset": 0,
            "_limit": 100,
            "device.serialNumber.startsWith": "",
        }
    )


class LanDBDevice(BaseModel):
    """
    Represents a network device record from LanDB.

    Attributes:
        equipmentno: Unique equipment identifier.
        serial_number: Serial number used for query filtering.
        name: Friendly device name, if available.
        manufacturer: Vendor name, if available.
        building: Location building, if available.
        floor: Location floor, if available.
        room: Location room, if available.
        ip: Assigned IPv4 address, if present.
    """

    equipmentno: str
    serial_number: str
    name: str | None = None
    manufacturer: str | None = None
    eqclass: str | None = None
    building: str | None = None
    floor: str | None = None
    room: str | None = None
    ip: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def create_device(
        cls, equipmentno: str, serial_number: str, eqclass: str
    ) -> LanDBDevice:
        """
        Factory method to initialize a device with only identifiers.

        Args:
            equipmentno: Unique equipment tag.
            serial_number: Serial number for API filtering.
        Returns:
            A LanDBDevice with identifiers set.
        """
        return cls(
            equipmentno=equipmentno, serial_number=serial_number, eqclass=eqclass
        )

    def log_device(self) -> None:
        """
        Output the device's current state to the system logger.
        """
        system_logger.info(
            f"Device {self.equipmentno} | Serial: {self.serial_number} | "
            f"Name: {self.name} | Manufacturer: {self.manufacturer} | "
            f"Location: {self.building}/{self.floor}/{self.room} | IP: {self.ip}"
        )

    def from_ip(self, ip_data: dict[str, Any]) -> None:
        """
        Update the device's IP attribute from a JSON IP record.

        Args:
            ip_data: JSON containing key 'ipv4'.
        """
        if ipv4 := ip_data.get("ipv4"):
            self.ip = ipv4

    def from_device(self, device_data: dict[str, Any]) -> None:
        """
        Merge metadata fields (name, manufacturer, location) into this device.
        Existing attributes remain unchanged if new values are missing.

        Args:
            device_data: JSON with 'name', 'manufacturer', and nested 'location'.
        """
        if name := device_data.get("name"):
            self.name = name
        if manufacturer := device_data.get("manufacturer"):
            self.manufacturer = manufacturer
        loc = device_data.get("location") or {}
        if building := loc.get("building"):
            self.building = building
        if floor := loc.get("floor"):
            self.floor = floor
        if room := loc.get("room"):
            self.room = room


class LanDBHelper:
    """
    Helper for fetching and enriching LanDBDevice objects via REST API calls.

    Methods:
        _fetch_data: Send a GET to a given endpoint and parse JSON results.
        _update_device: Apply a provided update function to each fetched record.
        get_data: Instantiate and populate a device with metadata and IP.
        get_device: Retrieve and apply device metadata.
        get_ip_address: Retrieve and apply device IP address.
    """

    def __init__(
        self, session: requests.Session, config: LanDBConfig = LanDBConfig()
    ) -> None:
        """
        Initialize with an HTTP session and configuration.

        Args:
            session: Authenticated requests.Session for API interactions.
            config: LanDBConfig containing endpoints and default queries.
        """
        self.session = session
        self.config = config

    def _fetch_data(
        self, endpoint: str, query: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """
        Execute a GET request against a relative endpoint and return list of records.

        Args:
            endpoint: API path segment relative to base_url.
            query: Query parameters including pagination and filters.

        Returns:
            List of JSON records if non-empty, or None if failure or no data.
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

        Args:
            landb_device: Device to be enriched with fetched data.
            endpoint: Endpoint path segment for the call.
            query: Template parameters; cloned before modification.
            key: Query parameter name to which serial_number is assigned.
            update_fn: Function invoked per record to update the device.
        """
        params = dict(query)
        params[key] = landb_device.serial_number
        if records := self._fetch_data(endpoint, params):
            for rec in records:
                update_fn(rec)

    def get_data(
        self, equipmentno: str, serial_number: str, eqclass: str
    ) -> LanDBDevice:
        """
        Instantiate a device and populate it with metadata then IP.

        Args:
            equipmentno: Identifier tag for this device.
            serial_number: Serial filter for API queries.

        Returns:
            A LanDBDevice with both metadata and IP applied.
        """
        device = LanDBDevice.create_device(equipmentno, serial_number, eqclass)
        system_logger.info(f"Fetching LanDB data for {equipmentno}/{serial_number}")
        self.get_device(device)
        self.get_ip_address(device)
        device.log_device()
        return device

    def get_device(self, landb_device: LanDBDevice) -> None:
        """
        Retrieve and merge metadata into the given device instance.

        Args:
            landb_device: Device instance to update.
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

        Args:
            landb_device: Device instance to update.
        """
        self._update_device(
            landb_device,
            self.config.ip_endpoint,
            self.config.ip_address_query,
            "device.serialNumber.startsWith",
            landb_device.from_ip,
        )
