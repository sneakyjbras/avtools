from __future__ import annotations

from functools import cached_property
from typing import Any, List

from requests import Response, post
from requests.auth import HTTPBasicAuth

from avtools.eam.config import EAMConfig
from avtools.eam.device import EAMDevice
from avtools.exception.errors import NoRecordsFound
from avtools.io.logger import system_logger


class EAMClient:
    """
    Facilitates fetching record counts and device lists from the EAM API.
    """

    def __init__(
        self, authorization: HTTPBasicAuth, config: EAMConfig = EAMConfig()
    ) -> None:
        self.authorization = authorization
        self.config = config

    def _request(self, query: dict[str, Any]) -> Response:
        """
        Perform a POST request against the EAM grid data endpoint.
        """
        url = f"{self.config.base_url}/{self.config.endpoint}"
        return post(
            url, auth=self.authorization, headers=self._default_headers(), json=query
        )

    @cached_property
    def number_av_records(self) -> int:
        """
        Cached property for the total number of AV records in EAM.
        """
        payload = dict(self.config.default_query)
        response = self._request(payload)
        if response.ok:
            return int(response.json().get("data", {}).get("records", 0))
        return 0

    def get_number_av_records(self) -> int:
        """
        Backwards-compatible method invoking the cached property.
        """
        return self.number_av_records

    def get_device_list(self, records: int) -> list[EAMDevice]:
        """
        Fetch AV device data and return as a list of EAMDevice instances.
        """
        payload = dict(self.config.default_query)
        payload["rowCount"] = records
        response = self._request(payload)

        if not response.ok:
            raise NoRecordsFound("No records found for any AV class type in EAM.")

        rows = response.json().get("data", {}).get("row", [])
        devices: list[EAMDevice] = []
        for row in rows:
            device = EAMDevice.parse_obj(row)
            device.log_device()
            devices.append(device)

        system_logger.info("Finished processing retrieved AV equipment data!")
        return devices

    @staticmethod
    def _default_headers() -> dict[str, str]:
        return {
            "INFOR_ORGANIZATION": "*",
            "INFOR_LOCALIZE_RESULTS": "true",
            "accept": "application/json",
        }
