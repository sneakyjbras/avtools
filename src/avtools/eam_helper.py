from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from requests import Response, post
from requests.auth import HTTPBasicAuth

from avtools.errors import NoRecordsFound
from avtools.logger import system_logger


@dataclass
class EAMConfig:
    """
    Configuration container for EAM API.

    Attributes:
        base_url: Base URL for all API requests.
        endpoint: Path segment for the EAM grid data endpoint.
        default_query: Default JSON body for grid data requests.
    """

    base_url: str = "https://cmmsx.cern.ch/WSHub/REST/apis"
    endpoint: str = "grids/data"
    default_query: dict[str, Any] = field(
        default_factory=lambda: {
            "rowCount": 0,
            "cursorPosition": 1,
            "gridID": 84,
            "userFunctionName": "OSOBJA",
            "gridName": "OSOBJA",
            "gridType": "LIST",
            "useNative": True,
            "gridFilter": [
                {
                    "fieldName": "class",
                    "fieldValue": "AV%",
                    "operator": "BEGINS",
                    "joiner": "AND",
                    "leftParenthesis": True,
                    "rightParenthesis": False,
                },
                {
                    "fieldName": "assetstatus_display",
                    "fieldValue": "Installed",
                    "operator": "EQUALS",
                    "joiner": "AND",
                    "leftParenthesis": False,
                    "rightParenthesis": True,
                },
            ],
        }
    )


class EAMDevice(BaseModel):
    """
    Represents a device from the EAM system.

    Attributes:
        serialnumber: The device's serial number.
        position: The device's position.
        equipmentno: Equipment number.
        equipmentdesc: Equipment description.
    """

    serialnumber: str | None
    position: str | None
    equipmentno: str | None
    equipmentdesc: str | None
    eqclass: str | None = Field(None, alias="class")
    manufacturer: str | None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_eam(cls, row: dict[str, Any]) -> EAMDevice:
        """
        Construct an EAMDevice from a JSON row.
        """
        data: dict[str, str | None] = {}
        for cell in row.get("cell", []):
            t = cell.get("t")
            v = cell.get("value")
            if t in {"serialnumber", "position", "equipmentno", "equipmentdesc", "class", "manufacturer"}:
                data[t] = v
        return cls(**data)  # type: ignore[arg-type]

    def log_device(self) -> None:
        """
        Log the details of this device to the system logger.
        """
        system_logger.info(
            f"EquipmentNo: {self.equipmentno}, Position: {self.position}, "
            f"EquipmentDesc: {self.equipmentdesc}, SerialNumber: {self.serialnumber}"
            f"Class: {self.eqclass}, Manufacturer: {self.manufacturer}"
        )


class EAMHelper:
    """
    Facilitates fetching record counts and device lists from the EAM API.

    Usage:
        config = EAMConfig()
        helper = EAMHelper(auth, config)
        total = helper.number_av_records
        devices = helper.get_device_list(total)
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
            device = EAMDevice.from_eam(row)
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

