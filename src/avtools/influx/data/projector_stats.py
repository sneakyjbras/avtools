"""Projector-specific stats model used for Influx points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Dict

from avtools.influx.data.stats_base import StatsBase


@dataclass
class ProjectorStats(StatsBase):
    """
    Base container for projector statistics fetched via SNMP.
    All numeric values (except firmware) are ints for Grafana.
    """

    measurement: ClassVar[str] = "projector_stats"
    tags: ClassVar[tuple[str, ...]] = ("firmware",)

    uptime: int  # raw tick counter (1/60-sec ticks)
    firmware: str  # version string (tag)
    lamp_hours: int  # total lamp hours
    power_status: int  # raw numeric status code

    def to_fields(self) -> dict[str, int]:
        """
        What actually gets sent to InfluxDB as numeric fields:
          - uptime_seconds: total uptime in seconds (ticks ÷ 60)
          - lamp_hours:     total lamp hours
          - power_status:   raw status code
        """
        return {
            "uptime_seconds": self.uptime // 60,
            "lamp_hours": self.lamp_hours,
            "power_status": self.power_status,
        }


@dataclass
class EpsonProjectorStats(ProjectorStats):
    """
    Epson-specific projector stats, with SNMP OIDs predefined.
    """

    measurement: ClassVar[str] = "epson_projector_stats"
    tags: ClassVar[tuple[str, ...]] = ProjectorStats.tags
    oids: ClassVar[dict[str, str]] = {
        "uptime": "1.3.6.1.4.1.xxx.xxx.1.0",
        "firmware": "1.3.6.1.4.1.xxx.xxx.2.0",
        "lamp_hours": "1.3.6.1.4.1.xxx.xxx.3.0",
        "power_status": "1.3.6.1.4.1.xxx.xxx.4.0",
    }
