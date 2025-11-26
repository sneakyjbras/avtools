from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EAMConfig:
    """
    Configuration for the EAM grids/data API.
    Only provides a read-only base payload via make_query().
    """

    # --- Endpoints / grids --------------------------------------------------
    base_url: str = "https://cmmsx.cern.ch/WSHub/REST/apis/"
    endpoint: str = "grids/data"
    asset_grid_id: int = 84
    asset_grid_name: str = "OSOBJA"
    position_grid_id: int = 113
    position_grid_name: str = "OSOBJP"

    # --- HTTP settings ------------------------------------------------------
    verify: bool | str = True
    timeout: float = 15.0
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "INFOR_ORGANIZATION": "*",
            "INFOR_LOCALIZE_RESULTS": "true",
            "Accept": "application/json",
        }
    )

    # --- Query defaults -----------------------------------------------------
    av_class_prefix: str = "AV"  # used to build the 'class' filter
    grid_type: str = "LIST"  # ignored if strict_read_only=True
    use_native: bool = True
    default_row_count: int = 0  # 0 => count-only
    max_row_count: int = 1000  # (enforced in client helper)
    strict_read_only: bool = True  # force LIST gridType in make_query()

    # Template for fresh payloads; keep minimal and read-only
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
                    "fieldValue": "AV%",  # will be synced to av_class_prefix
                    "operator": "BEGINS",
                    "joiner": "AND",
                    "leftParenthesis": True,
                    "rightParenthesis": True,
                },
            ],
        }
    )

    # --- Builder ------------------------------------------------------------

    def make_query(self) -> dict[str, Any]:
        """
        Return a fresh, deep-copied base payload with a read-only LIST grid
        and the 'class' filter set from av_class_prefix.
        """
        q = copy.deepcopy(self.default_query)

        # Always read-only if strict_read_only, otherwise honor config
        q["gridType"] = "LIST" if self.strict_read_only else self.grid_type
        q["useNative"] = self.use_native

        # Sync the class filter to av_class_prefix if present
        gf = q.get("gridFilter", [])
        for f in gf:
            if f.get("fieldName") == "class":
                f["fieldValue"] = f"{self.av_class_prefix}%"

        # Ensure rowCount default is applied
        q["rowCount"] = self.default_row_count
        return q
