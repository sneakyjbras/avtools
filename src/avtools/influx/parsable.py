from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class InfluxParsable(ABC):
    """
    Protocol for objects that can render human-readable representations.
    """

    @abstractmethod
    def to_human(self) -> dict[str, str]:
        """
        Return a mapping of label -> formatted value as strings.
        """
        pass
