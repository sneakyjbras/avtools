from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class InfluxSerializable(ABC):
    """
    Protocol for objects that can serialize themselves to InfluxDB format.
    """

    @abstractmethod
    def to_influx(self) -> dict[str, Any]:
        """
        Return a dict with keys:
          - measurement: str
          - tags: Dict[str, str]
          - fields: Dict[str, Any]
          - time (optional): str
        """
        pass
