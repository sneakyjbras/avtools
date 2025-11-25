from __future__ import annotations

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

    equipment_no: str | None = Field(
        None,
        alias="equipmentno",
        description="The equipment number of the position.",
    )
    equipment_desc: str | None = Field(
        None,
        alias="equipmentdesc",
        description="The human-readable description of the position.",
    )
    eq_class: str | None = Field(
        None,
        alias="class",
        description='The classification of the position (EAM "class" field).',
    )
    commission_date: date | None = Field(
        None,
        alias="commissiondate",
        description="Commissioning date of the position.",
    )
    parent_asset: str | None = Field(
        None,
        alias="parentasset",
        description="Parent asset or higher-level position for this position.",
    )
    sponsor: str | None = Field(
        None,
        description="Sponsor or responsible entity for this position.",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _flatten_cells(cls, values: Any) -> Any:
        """Flatten EAM grid `cell` entries when parsing raw JSON rows."""
        if not isinstance(values, dict):
            return values

        flattened: dict[str, Any] = {k: v for k, v in values.items() if k != "cell"}
        for cell in values.get("cell", []):
            key = cell.get("t")
            if key is not None:
                flattened[key] = cell.get("value")

        return flattened

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

    def __str__(self) -> str:
        return (
            "EAMPosition("
            f"equipment_no={self.equipment_no!r}, "
            f"equipment_desc={self.equipment_desc!r}, "
            f"eq_class={self.eq_class!r}, "
            f"commission_date={self.commission_date!r}, "
            f"parent_asset={self.parent_asset!r}, "
            f"sponsor={self.sponsor!r}"
            ")"
        )

    def log_device(self) -> None:
        """Emit this position as a structured log event."""
        payload = self.model_dump(mode="json", by_alias=False, exclude_none=True)
        position_logger.info("eam_position_synced", **payload)
