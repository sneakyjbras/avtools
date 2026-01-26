"""Serialization helpers for Influx-related models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class InfluxSerializable(ABC):
    """
    Protocol for objects that can serialize themselves to InfluxDB format.
    """

    @abstractmethod
    def to_influx(self) -> dict[str, Any]:
        """Serialize the object to an InfluxDB point dict.

        Returns:
            Dict with keys:
            - ``measurement`` (str)
            - ``tags`` (dict[str, str])
            - ``fields`` (dict[str, Any])
            - ``time`` (optional str)
        """
        pass
