from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

position_logger = structlog.get_logger(__name__).bind(
    component="eam", model="EAMPosition"
)


class EAMPosition(BaseModel):
    """Position record from the EAM grid."""

    # Identifiers / classification
    equipment_no: str | None = Field(
        None,
        alias="equipmentno",
        description="Equipment number of the position.",
    )
    eq_class: str | None = Field(
        None,
        alias="class",
        description='Classification of the position (EAM "class" field).',
    )
    category: str | None = Field(
        None,
        description="Sub-classification or category of the position.",
    )

    # Description / ownership
    equipment_desc: str | None = Field(
        None,
        alias="equipmentdesc",
        description="Human-readable description of the position.",
    )
    sponsor: str | None = Field(
        None,
        validation_alias="cust_3_CHAR_OBJ_AV008",
        description="Sponsor or responsible entity for this position.",
    )

    # Hierarchy / lifecycle / status
    parent_asset: str | None = Field(
        None,
        alias="parentasset",
        description="Parent asset or higher-level position.",
    )
    commission_date: date | None = Field(
        None,
        alias="commissiondate",
        description="Commissioning date of the position.",
    )
    asset_status_display: str | None = Field(
        None,
        alias="assetstatus_display",
        description="Human-readable asset status label.",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,  # allow model_validate(ORM_instance)
        extra="ignore",
        str_strip_whitespace=True,
    )

    # --- Normalization -------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _flatten_cells(cls, values: Any) -> Any:
        """
        Flatten EAM grid rows of the form {"cell": [{"t": key, "value": val}, ...], ...}
        into a simple mapping {key: value, ...}. Non-mapping inputs pass through.
        """
        if not isinstance(values, Mapping):
            return values

        flattened: dict[str, Any] = {k: v for k, v in values.items() if k != "cell"}
        for cell in values.get("cell", []):
            if isinstance(cell, Mapping):
                key = cell.get("t")
                if key:
                    flattened[key] = cell.get("value")

        return flattened

    @field_validator(
        "equipment_no",
        "eq_class",
        "category",
        "equipment_desc",
        "sponsor",
        "parent_asset",
        "asset_status_display",
        mode="before",
    )
    @classmethod
    def _normalize_str(cls, v: Any) -> str | None:
        """Normalize incoming string-like values (strip, empty -> None)."""
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("commission_date", mode="before")
    @classmethod
    def _normalise_commission_date(cls, v: Any) -> Any:
        """Parse commission_date into a `date` instance."""
        if v is None or v == "":
            return None

        if isinstance(v, date):
            return v

        raw = str(v).strip()

        for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.date()
            except ValueError:
                continue

        raise ValueError(f"Invalid commission date format: {raw!r}")

    @field_serializer("commission_date", when_used="json")
    def _serialize_commission_date(self, value: date | None) -> str | None:
        """Serialize commission_date as YYYY-MM-DD in JSON output."""
        if value is None:
            return None
        return value.isoformat()

    # --- Convenience ---------------------------------------------------------

    def __str__(self) -> str:
        return (
            "EAMPosition("
            f"equipment_no={self.equipment_no!r}, "
            f"equipment_desc={self.equipment_desc!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"commission_date={self.commission_date!r}, "
            f"parent_asset={self.parent_asset!r}, "
            f"sponsor={self.sponsor!r}, "
            f"asset_status_display={self.asset_status_display!r}"
            ")"
        )

    def log_device(self) -> None:
        """Emit this position as a structured log event (name kept for compatibility)."""
        payload = self.model_dump(mode="json", by_alias=False, exclude_none=True)
        position_logger.info("eam_position_synced", **payload)
