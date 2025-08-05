from __future__ import annotations

import re
from re import Pattern
from typing import ClassVar


class EAMRegex:
    """
    Utility class for formatting room identifiers according to EAM standards.
    Examples:
        EAMRegex.format_room("CCAP-874.1.011") -> "874/1-011"
        EAMRegex.format_room("CWD-561.R.001")    -> "561/R-001"
    """

    # Matches prefixes of 3–4 uppercase letters + dash, then three groups separated by dots
    _ROOM_PATTERN: ClassVar[Pattern[str]] = re.compile(
        r"^[A-Z]{3,4}-(\d+)\.(\w+)\.(\d+)$"
    )

    @classmethod
    def format_room(cls, room: str) -> str:
        """
        Remove the prefix (e.g. 'CCAP-' or 'CWD-') and reformat the remaining segments.

        Args:
            room: A room identifier, e.g. 'CCAP-874.1.011' or 'CWD-561.R.001'.

        Returns:
            A reformatted string, e.g. '874/1-011' or '561/R-001'.

        Raises:
            ValueError: If the room string does not match the expected EAM pattern.
        """
        match = cls._ROOM_PATTERN.match(room)
        if not match:
            raise ValueError(f"Invalid room format: {room}")

        major, middle, minor = match.groups()
        return f"{major}/{middle}-{minor}"
