from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict
from requests import Response, post
from requests.auth import HTTPBasicAuth

from avtools.errors import NoRecordsFound
from avtools.logger import system_logger


class EAMDevice(BaseModel):
    """
    Represents a device from the EAM system.

    Attributes:
        serialnumber (Optional[str]): The device's serial number.
        position (Optional[str]): The device's position.
        equipmentno (Optional[str]): Equipment number.
        equipmentdesc (Optional[str]): Equipment description.
    """

    serialnumber: str | None
    position: str | None
    equipmentno: str | None
    equipmentdesc: str | None

    # allow .from_orm() in Pydantic v2
    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_eam(cls, row: dict[str, Any]) -> EAMDevice:
        """
        Create an EAMDevice instance from a row in the EAM JSON.

        Args:
            row (Dict[str, Any]): A row of JSON data from the EAM API.

        Returns:
            EAMDevice: The constructed device instance.
        """
        data: dict[str, str | None] = {}
        for cell in row.get("cell", []):
            field_type: str | None = cell.get("t")
            value: str | None = cell.get("value")
            if field_type == "serialnumber":
                data["serialnumber"] = value
            elif field_type == "position":
                data["position"] = value
            elif field_type == "equipmentno":
                data["equipmentno"] = value
            elif field_type == "equipmentdesc":
                data["equipmentdesc"] = value
        return cls(**data)  # type: ignore[arg-type]

    def log_device(self) -> None:
        """
        Log the details of the device.
        """
        system_logger.info(
            f"EquipmentNo: {self.equipmentno}, Position: {self.position}, "
            f"EquipmentDesc: {self.equipmentdesc}, SerialNumber: {self.serialnumber}"
        )


class EAMHelper:
    """
    A helper class for interacting with the EAM system API.
    """

    BASE_URL: str = "https://cmmsx.cern.ch/WSHub/REST/apis"
    ENDPOINT: str = "grids/data"
    DEFAULT_EAM_QUERY: dict[str, Any] = {
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

    def __init__(self, authorization: HTTPBasicAuth) -> None:
        """
        Initialize the EAMHelper with HTTP Basic Auth credentials.

        Args:
            authorization (HTTPBasicAuth): Authentication for API requests.
        """
        self.authorization = authorization

    def get_number_av_records(self) -> int:
        """
        Retrieve the total number of AV records from EAM.

        Returns:
            int: The number of available records.
        """
        response: Response = post(
            f"{self.BASE_URL}/{self.ENDPOINT}",
            auth=self.authorization,
            headers=self._default_headers(),
            json=self.DEFAULT_EAM_QUERY,
        )
        if response.ok:
            return int(response.json().get("data", {}).get("records", 0))
        return 0

    def get_device_list(self, records: int) -> list[EAMDevice]:
        """
        Fetch device data from the EAM system and return a list of EAMDevice objects.

        Args:
            records (int): The number of records to fetch.

        Returns:
            List[EAMDevice]: A list of device objects.

        Raises:
            NoRecordsFound: If the request fails or no data is returned.
        """
        # update desired row count
        self.DEFAULT_EAM_QUERY["rowCount"] = records
        response: Response = post(
            f"{self.BASE_URL}/{self.ENDPOINT}",
            auth=self.authorization,
            headers=self._default_headers(),
            json=self.DEFAULT_EAM_QUERY,
        )
        if not response.ok:
            raise NoRecordsFound("No records found for any AV class type in EAM.")

        eam_json = response.json()
        device_list: list[EAMDevice] = []
        for row in eam_json.get("data", {}).get("row", []):
            device = EAMDevice.from_eam(row)
            device.log_device()
            device_list.append(device)

        system_logger.info("Finished processing retrieved AV equipment data!")
        return device_list

    @staticmethod
    def _default_headers() -> dict[str, str]:
        """
        Return the default headers for EAM API requests.

        Returns:
            Dict[str, str]: The headers dictionary.
        """
        return {
            "INFOR_ORGANIZATION": "*",
            "INFOR_LOCALIZE_RESULTS": "true",
            "accept": "application/json",
        }
