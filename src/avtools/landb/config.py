from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


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
