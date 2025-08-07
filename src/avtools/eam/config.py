from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    asset_grid_id: int = 84
    asset_grid_name: str = "OSOBJA"
    position_grid_id: int = 113
    position_grid_name: str = "OSOBJP"
    default_query: dict[str, Any] = field(
        default_factory=lambda: {
            "rowCount": 0,
            "cursorPosition": 1,
            "gridID": 0,
            "userFunctionName": "",
            "gridName": "",
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
