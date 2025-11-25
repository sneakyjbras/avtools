from __future__ import annotations

from typing import Any, List, Type, TypeVar

import structlog
from requests import Response, Session
from requests.auth import HTTPBasicAuth

from avtools.eam.config import EAMConfig
from avtools.eam.device import EAMDevice
from avtools.eam.position import EAMPosition
from avtools.exception.errors import NoRecordsFound

# Generic parser type for devices and positions
T = TypeVar("T", bound=EAMDevice)


class EAMClient:
    """Client for fetching record counts and device/position lists from EAM."""

    def __init__(
        self,
        authorization: HTTPBasicAuth,
        config: EAMConfig = EAMConfig(),
    ) -> None:
        """Initialize EAM client with auth and default headers."""
        self.config = config
        self._session = Session()
        self._session.auth = authorization
        self._session.headers.update(self._default_headers())
        self.logger = structlog.get_logger(self.__class__.__name__)

    def _request(self, payload: dict[str, Any]) -> Response:
        """Perform a POST request against the EAM grid data endpoint."""
        url = f"{self.config.base_url}/{self.config.endpoint}"
        response = self._session.post(url, json=payload)
        try:
            response.raise_for_status()
        except Exception as e:
            self.logger.error("EAM API request failed", error=str(e), url=url)
            raise
        return response

    def _extract(self, response: Response, *keys: str, default=None):
        """Safely extract nested JSON keys from a response."""
        data = response.json()
        for key in keys:
            data = data.get(key, {})
        return data or default

    def _get_total_records(self, grid_id: str, grid_name: str) -> int:
        """Return total AV records for a given grid."""
        payload = dict(self.config.default_query)
        payload.update(
            {
                "gridID": grid_id,
                "userFunctionName": grid_name,
                "gridName": grid_name,
            }
        )
        records = self._extract(self._request(payload), "data", "records", default=0)
        return int(records)

    def get_number_av_assets(self) -> int:
        """Get total number of AV assets in EAM."""
        return self._get_total_records(
            self.config.asset_grid_id,
            self.config.asset_grid_name,
        )

    def get_number_av_positions(self) -> int:
        """Get total number of AV positions in EAM."""
        return self._get_total_records(
            self.config.position_grid_id,
            self.config.position_grid_name,
        )

    def _fetch_list(
        self,
        grid_id: str,
        grid_name: str,
        missing_msg: str,
        row_count: int,
        parser: type[T] = EAMDevice,
    ) -> list[T]:
        """Fetch rows from EAM and return them as parser model instances."""
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

        items: list[T] = []
        for row in rows:
            obj = parser.parse_obj(row)
            obj.log_device()
            items.append(obj)

        self.logger.info(
            "Finished processing retrieved EAM data",
            grid_name=grid_name,
            items=len(items),
        )
        return items

    def get_device_list(self, records: int) -> list[EAMDevice]:
        """Fetch AV device data as a list of EAMDevice instances."""
        return self._fetch_list(
            self.config.asset_grid_id,
            self.config.asset_grid_name,
            "No records found for any AV class type in EAM.",
            records,
            parser=EAMDevice,
        )

    def get_positions_list(self, records: int) -> list[EAMPosition]:
        """Fetch AV position data as a list of EAMPosition instances."""
        return self._fetch_list(
            self.config.position_grid_id,
            self.config.position_grid_name,
            "No records found for any AV position in EAM.",
            records,
            parser=EAMPosition,  # type: ignore[arg-type]
        )

    @staticmethod
    def _default_headers() -> dict[str, str]:
        """Default headers for EAM requests."""
        return {
            "INFOR_ORGANIZATION": "*",
            "INFOR_LOCALIZE_RESULTS": "true",
            "Accept": "application/json",
        }
