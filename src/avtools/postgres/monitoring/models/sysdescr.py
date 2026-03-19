from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class DeviceSysDescrMonitoring(BaseModel):
    """Latest-known SNMP sysDescr.0 snapshot for a device.

    This is identity-like text (not time-series). It is keyed by `equipment_no`.
    """

    equipment_no: str
    ip: str | None = None
    eqclass: str | None = None
    category: str | None = None
    sysdescr: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
