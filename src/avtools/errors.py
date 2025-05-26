from __future__ import annotations  # Enable postponed evaluation of annotations

from collections.abc import Callable
from re import Pattern
from typing import Any


class EAMError(ValueError):  # type: ignore[misc]
    """
    Base exception for errors related to the EAM system.
    Inherits from ValueError to indicate invalid values or operations.

    Attributes:
        message (str): Description of the error.
    """

    def __init__(self, message: str) -> None:
        """
        Initialize EAMError with an error message.

        Args:
            message (str): Human-readable error message.
        """
        super().__init__(message)


class LanDBError(ValueError):  # type: ignore[misc]
    """
    Base exception for errors related to the LanDB system.
    Inherits from ValueError to signify issues with LanDB operations.

    Attributes:
        message (str): Description of the error.
    """

    def __init__(self, message: str) -> None:
        """
        Initialize LanDBError with an error message.

        Args:
            message (str): Human-readable error message.
        """
        super().__init__(message)


class NoRecordsFound(EAMError, LanDBError):  # type: ignore[misc]
    """
    Exception raised when no records are found in the EAM or LanDB system.
    Inherits from both EAMError and LanDBError.

    Attributes:
        message (str): Description of the error.
    """

    def __init__(self, message: str = "No records found in the system.") -> None:
        """
        Initialize NoRecordsFound with an optional error message.

        Args:
            message (str): Human-readable error message (default provided).
        """
        super().__init__(message)


class MoreThanOneDeviceFound(EAMError):  # type: ignore[misc]
    """
    Exception raised when more than one device is found where a unique device was expected.
    Inherits from EAMError.

    Attributes:
        message (str): Description of the error.
    """

    def __init__(
        self, message: str = "Multiple devices found, expected unique result."
    ) -> None:
        """
        Initialize MoreThanOneDeviceFound with an optional error message.

        Args:
            message (str): Human-readable error message (default provided).
        """
        super().__init__(message)
