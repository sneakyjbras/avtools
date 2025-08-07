from __future__ import annotations

from typing import Any, List

from requests import Response, Session
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
        self,
        authorization: HTTPBasicAuth,
        config: EAMConfig = EAMConfig(),
    ) -> None:
        self.config = config
        self._session = Session()
        self._session.auth = authorization
        self._session.headers.update(self._default_headers())

    def _request(self, payload: dict[str, Any]) -> Response:
        """
        Perform a POST request against the EAM grid data endpoint.
        """
        url = f"{self.config.base_url}/{self.config.endpoint}"
        response = self._session.post(url, json=payload)
        try:
            response.raise_for_status()
        except Exception as e:
            system_logger.error(f"EAM API request failed: {e}")
            raise
        return response

    def _extract(self, response: Response, *keys: str, default=None):
        """
        Safely extract nested JSON keys from the response.
        """
        data = response.json()
        for key in keys:
            data = data.get(key, {})
        return data or default

    def number_av_records(self, payload: dict[str, Any]) -> int:
        """
        Return the total number of AV records in EAM for the given payload.
        """
        records = self._extract(self._request(payload), "data", "records", default=0)
        return int(records)

    def get_number_av_assets(self) -> int:
        """
        Get the total number of AV assets in EAM.
        """
        payload = dict(self.config.default_query)
        payload.update(
            {
                "gridID": self.config.asset_grid_id,
                "userFunctionName": self.config.asset_grid_name,
                "gridName": self.config.asset_grid_name,
            }
        )
        return self.number_av_records(payload)

    def get_number_av_positions(self) -> int:
        """
        Get the total number of AV positions in EAM.
        """
        payload = dict(self.config.default_query)
        payload.update(
            {
                "gridID": self.config.position_grid_id,
                "userFunctionName": self.config.position_grid_name,
                "gridName": self.config.position_grid_name,
            }
        )
        return self.number_av_records(payload)

    def _fetch_list(
        self,
        grid_id: str,
        grid_name: str,
        missing_msg: str,
        row_count: int,
    ) -> list[EAMDevice]:
        """
        Fetch rows from the EAM API and return them as EAMDevice instances.
        """
        payload = dict(self.config.default_query)
        payload.update(
            {
                "rowCount": row_count,
                "gridID": grid_id,
                "userFunctionName": grid_name,
                "gridName": grid_name,
            }
        )
        response = self._request(payload)
        rows = self._extract(response, "data", "row", default=[])
        if not rows:
            raise NoRecordsFound(missing_msg)
        devices: list[EAMDevice] = []
        for row in rows:
            device = EAMDevice.parse_obj(row)
            device.log_device()
            devices.append(device)
        system_logger.info(f"Finished processing retrieved data for {grid_name}")
        return devices

    def get_device_list(self, records: int) -> list[EAMDevice]:
        """
        Fetch AV device data and return as a list of EAMDevice instances.
        """
        return self._fetch_list(
            self.config.asset_grid_id,
            self.config.asset_grid_name,
            "No records found for any AV class type in EAM.",
            records,
        )

    def get_positions_list(self, records: int) -> list[EAMDevice]:
        """
        Fetch AV position data and return as a list of EAMDevice instances.
        """
        return self._fetch_list(
            self.config.position_grid_id,
            self.config.position_grid_name,
            "No records found for any AV position in EAM.",
            records,
        )

    @staticmethod
    def _default_headers() -> dict[str, str]:
        return {
            "INFOR_ORGANIZATION": "*",
            "INFOR_LOCALIZE_RESULTS": "true",
            "Accept": "application/json",
        }
