from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LanDBConfig:
    """
    Configuration for LanDB API endpoints and default query parameters.
    """

    # Base HTTP settings
    base_url: str = "https://landb.cern.ch/api/"
    verify: bool | str = True  # bool or CA bundle path
    request_timeout: float = 10.0  # seconds

    # Pagination (offset-based)
    offset_param: str = "_offset"
    limit_param: str = "_limit"
    default_limit: int = 100

    # Endpoint: IP addresses
    ip_endpoint: str = "beta/ip-addresses"
    ip_serial_filter_key: str = "device.serialNumber.startsWith"

    # Defaults applied to IP queries (exclude the serial filter value here)
    ip_query_defaults: dict[str, Any] = field(
        default_factory=lambda: {
            "_offset": 0,
            "_limit": 100,
        }
    )
