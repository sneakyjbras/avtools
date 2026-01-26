"""Parsing helpers for Influx-related models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class InfluxParsable(ABC):
    """
    Protocol for objects that can render human-readable representations.
    """

    @abstractmethod
    def to_human(self) -> dict[str, str]:
        """Render the object into a human-friendly mapping.

        Returns:
            Mapping of label to formatted string value.
        """
        pass
