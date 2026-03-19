from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ProjectorMonitoring(BaseModel):
    """Text/identity-like projector monitoring snapshot.

    This is *not* time-series data. It represents the latest known values for
    strings/state we care about (e.g. firmware, power status), keyed by equipment number.
    """

    equipment_no: str
    ip: str | None = None
    firmware: str | None = None
    power_status: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @classmethod
    def from_query_stats(
        cls,
        *,
        equipment_no: str,
        ip: str | None,
        stats: dict[str, Any],
    ) -> "ProjectorMonitoring | None":
        """Build a monitoring snapshot from raw SNMP projector stats.

        We persist *latest-known* text/state fields in Postgres (not Prometheus).
        A record is only produced if at least one relevant field is present.

        Args:
            equipment_no: Stable device identifier.
            ip: Current device IP (best-effort).
            stats: Raw handler stats (e.g. firmware, power_status).

        Returns:
            A ProjectorMonitoring snapshot, or None if no relevant fields exist.
        """
        fw = stats.get("firmware")
        power = stats.get("power_status")

        fw_s = str(fw).strip() if fw not in (None, "") else None
        power_s = str(power).strip() if power not in (None, "") else None

        if fw_s is None and power_s is None:
            return None

        return cls(
            equipment_no=equipment_no,
            ip=ip,
            firmware=fw_s,
            power_status=power_s,
        )
