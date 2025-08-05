from __future__ import annotations

import sys
from typing import Any, List, Optional

import structlog


class NullLogger:
    """
    A logger implementation that discards all log messages.

    Methods mirror common logging interface but perform no operations.
    """

    def info(self, message: Any = None, *args: Any, **kwargs: Any) -> None:
        """No-op for info level logs."""
        return None

    def debug(self, message: Any = None, *args: Any, **kwargs: Any) -> None:
        """No-op for debug level logs."""
        return None

    def warning(self, message: Any = None, *args: Any, **kwargs: Any) -> None:
        """No-op for warning level logs."""
        return None

    def error(self, message: Any = None, *args: Any, **kwargs: Any) -> None:
        """No-op for error level logs."""
        return None

    def exception(self, message: Any = None, *args: Any, **kwargs: Any) -> None:
        """No-op for exception logs."""
        return None


class Logger:
    """
    Singleton Logger that wraps structlog for structured logging.

    Provides methods for different log levels, muting, and logging list items.
    """

    _instance: Logger | None = None

    def __new__(cls) -> Logger:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._setup()
        return cls._instance

    def _setup(self) -> None:
        """
        Configure the structlog processor pipeline and default loggers.
        """
        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            logger_factory=structlog.PrintLoggerFactory(sys.stdout),
            cache_logger_on_first_use=True,
        )
        self._real_logger = structlog.get_logger()
        self._null_logger = NullLogger()
        self.logger = self._real_logger

    def set_muted(self, muted: bool) -> None:
        """
        Disable or enable logging output.

        Args:
            muted (bool): If True, switches to a null logger; otherwise, uses real logger.
        """
        self.logger = self._null_logger if muted else self._real_logger

    def configure(self, log_level: str) -> None:
        """
        Mute or unmute based on log level string.

        Args:
            log_level (str): "DEBUG" un-mutes; any other level mutes.
        """
        level = log_level.strip().upper()
        self.set_muted(level != "DEBUG")

    def info(self, message: Any, **kwargs: Any) -> None:
        """Log an info-level message."""
        self.logger.info(message, **kwargs)

    def debug(self, message: Any, **kwargs: Any) -> None:
        """Log a debug-level message."""
        self.logger.debug(message, **kwargs)

    def warning(self, message: Any, **kwargs: Any) -> None:
        """Log a warning-level message."""
        self.logger.warning(message, **kwargs)

    def error(self, message: Any, **kwargs: Any) -> None:
        """Log an error-level message."""
        self.logger.error(message, **kwargs)

    def exception(self, message: Any, **kwargs: Any) -> None:
        """Log an exception with traceback info."""
        self.logger.error(message, exc_info=True, **kwargs)

    def log_items(
        self, level: str, message: str, items: list[Any] | None = None
    ) -> None:
        """
        Log a message followed by each item in a list.

        Args:
            level (str): Name of the log method to call (e.g., "info").
            message (str): Initial message to log.
            items (List[Any], optional): List of items to log individually.
        """
        if items is None:
            items = []
        log_func = getattr(self.logger, level, self.logger.info)
        log_func(message)
        for idx, entry in enumerate(items):
            entry_str = str(entry).strip("'")
            if entry_str:
                log_func(f"{idx}: {entry_str}")
        log_func("")


# Global singleton instance
system_logger: Logger = Logger()
