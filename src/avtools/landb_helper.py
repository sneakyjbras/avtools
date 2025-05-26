from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, ConfigDict

from avtools.logger import system_logger


class LanDBDevice(BaseModel):
    """
    Represents a network device in the LanDB system.

    Attributes:
        equipmentno (str): Unique equipment identifier.
        serial_number (str): Unique serial number for the device.
        name (Optional[str]): Device name.
        manufacturer (Optional[str]): Device manufacturer.
        building (Optional[str]): Building location of the device.
        floor (Optional[str]): Floor location of the device.
        room (Optional[str]): Room location of the device.
        ip (Optional[str]): IPv4 address of the device.
    """

    equipmentno: str
    serial_number: str
    name: str | None = None
    manufacturer: str | None = None
    building: str | None = None
    floor: str | None = None
    room: str | None = None
    ip: str | None = None

    # enable attribute-based parsing for Pydantic v2
    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def create_device(cls, equipmentno: str, serial_number: str) -> LanDBDevice:
        """
        Initialize a minimal LanDBDevice with key identifiers.

        Args:
            equipmentno (str): The equipment number.
            serial_number (str): The device serial number.

        Returns:
            LanDBDevice: New instance with identifiers set.
        """
        return cls(equipmentno=equipmentno, serial_number=serial_number)

    def log_device(self) -> None:
        """
        Log the current state of the device.
        """
        system_logger.info(
            f"Device {self.equipmentno} | Serial: {self.serial_number} | "
            f"Name: {self.name} | Manufacturer: {self.manufacturer} | "
            f"Location: {self.building}/{self.floor}/{self.room} | IP: {self.ip}"
        )

    def from_ip(self, ip_data: dict[str, Any]) -> None:
        """
        Update the device's IP address using API response data.

        Args:
            ip_data (Dict[str, Any]): JSON data containing 'ipv4' key.
        """
        ipv4: str | None = ip_data.get("ipv4")
        if ipv4:
            self.ip = ipv4

    def from_device(self, device_data: dict[str, Any]) -> None:
        """
        Update device metadata from API response data, preserving existing values when absent.

        Args:
            device_data (Dict[str, Any]): JSON data with keys 'name', 'manufacturer', and 'location'.
        """
        new_name: str | None = device_data.get("name")
        new_manufacturer: str | None = device_data.get("manufacturer")
        location: dict[str, Any] = device_data.get("location", {}) or {}
        new_building: str | None = location.get("building")
        new_floor: str | None = location.get("floor")
        new_room: str | None = location.get("room")

        if new_name is not None:
            self.name = new_name
        if new_manufacturer is not None:
            self.manufacturer = new_manufacturer
        if new_building is not None:
            self.building = new_building
        if new_floor is not None:
            self.floor = new_floor
        if new_room is not None:
            self.room = new_room


class LanDBHelper:
    """
    Helper class for interacting with the LanDB REST API to fetch device details.

    Methods:
        get_data: Orchestrates fetching both metadata and IP for a device.
        get_device: Retrieves and applies device metadata.
        get_ip_address: Retrieves and applies device IP.
    """

    BASE_URL: str = "https://landb.cern.ch/api"
    DEVICE_ENDPOINT: str = "beta/devices"
    IP_ENDPOINT: str = "beta/ip-addresses"

    DEVICE_QUERY: dict[str, Any] = {
        "_offset": 0,
        "_limit": 100,
        "serialNumber.startsWith": "",
    }
    IP_ADDRESS_QUERY: dict[str, Any] = {
        "_offset": 0,
        "_limit": 100,
        "device.serialNumber.startsWith": "",
    }

    def __init__(self, token: str) -> None:
        """
        Initialize the LanDBHelper with an API bearer token.

        Args:
            token (str): Bearer token for authentication.
        """
        self.token = token

    @property
    def token(self) -> str:
        """
        Return the current API token.

        Returns:
            str: Bearer token string.
        """
        return self._token

    @token.setter
    def token(self, token: str) -> None:
        """
        Set or update the API token, with optional validation.

        Args:
            token (str): New bearer token.
        """
        # potential place for token format validation
        self._token = token

    def _landb_headers(self) -> dict[str, str]:
        """
        Construct default HTTP headers for LanDB API calls.

        Returns:
            Dict[str, str]: Headers including authorization.
        """
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def _fetch_data(
        self, endpoint: str, query: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """
        Execute a GET request against the specified endpoint and query parameters.

        Args:
            endpoint (str): Relative API endpoint.
            query (Dict[str, Any]): Query parameters for the request.

        Returns:
            Optional[List[Dict[str, Any]]]: Parsed JSON list, or None on failure.
        """
        url: str = f"{self.BASE_URL}/{endpoint}"
        response = requests.get(url, headers=self._landb_headers(), params=query)
        if response.status_code != 200:
            system_logger.error(f"LanDB request failed [{response.status_code}]: {url}")
            return None
        payload = response.json()
        if not payload:
            system_logger.info(f"No results for {endpoint} with query {query}")
            return None
        return payload

    def _update_device(
        self,
        landb_device: LanDBDevice,
        endpoint: str,
        query: dict[str, Any],
        key: str,
        update_fn: Callable[[dict[str, Any]], None],
    ) -> None:
        """
        Fetch data from an endpoint and invoke a callback to apply updates.

        Args:
            landb_device (LanDBDevice): Device to update.
            endpoint (str): API endpoint path.
            query (Dict[str, Any]): Query params template.
            key (str): Query key for serial number.
            update_fn (Callable): Function to apply each result dict.
        """
        query[key] = landb_device.serial_number
        results = self._fetch_data(endpoint, query)
        if results:
            for item in results:
                update_fn(item)

    def get_data(self, equipmentno: str, serial_number: str) -> LanDBDevice:
        """
        Retrieve a populated LanDBDevice by fetching both metadata and IP.

        Args:
            equipmentno (str): Equipment identifier.
            serial_number (str): Device serial number.

        Returns:
            LanDBDevice: Device with applied updates.
        """
        device = LanDBDevice.create_device(equipmentno, serial_number)
        system_logger.info(f"Fetching LanDB data for {equipmentno}/{serial_number}")
        self.get_device(device)
        self.get_ip_address(device)
        device.log_device()
        return device

    def get_device(self, landb_device: LanDBDevice) -> None:
        """
        Fetch and apply device metadata from the devices endpoint.

        Args:
            landb_device (LanDBDevice): Device to update.
        """
        params = self.DEVICE_QUERY.copy()
        self._update_device(
            landb_device,
            self.DEVICE_ENDPOINT,
            params,
            "serialNumber.startsWith",
            lambda data: landb_device.from_device(data),
        )

    def get_ip_address(self, landb_device: LanDBDevice) -> None:
        """
        Fetch and apply the device's IP address from the IP endpoint.

        Args:
            landb_device (LanDBDevice): Device to update.
        """
        params = self.IP_ADDRESS_QUERY.copy()
        self._update_device(
            landb_device,
            self.IP_ENDPOINT,
            params,
            "device.serialNumber.startsWith",
            lambda data: landb_device.from_ip(data),
        )
