"""Structured-log sink — the *producer* end of the OpenSearch logging contract.

structlog events render to two destinations at once:

* a JSON-lines file (tailed by Fluent Bit -> OTLP -> OpenSearch ``otel-logs_<tenant>``)
* the console (-> journald, so ``journalctl -u avtools`` stays human-readable)

High-volume per-device events (``snmp_probe_failure``) are routed to a dedicated
``avtools.osevents`` logger that writes to the JSON file ONLY. During mass-down
periods that keeps the journal readable while OpenSearch still receives every
per-device record the alarms aggregate.

Envelope fields (``service``, ``host``, ``submitter_environment``, ``hostgroup``,
``cycle_id``) are bound as contextvars so every event carries them; ``host`` and
``cycle_id`` are universal, the rest are bound per service via :func:`bind_envelope`.
"""

from __future__ import annotations

import logging
import socket
import uuid
from pathlib import Path

import structlog

DEFAULT_LOG_FILE = "/var/log/avtools/avtools.jsonl"
OSEVENTS_LOGGER = "avtools.osevents"

# Shared processors run on every event before the per-handler renderer.
_TIMESTAMPER = structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp")
_SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    _TIMESTAMPER,
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def configure_logging(logs: bool, log_file: str = DEFAULT_LOG_FILE) -> None:
    """Configure dual-output structlog (JSON file + console) and bind run context.

    Args:
        logs: verbose (DEBUG) when True, else INFO.
        log_file: path of the JSON-lines file Fluent Bit tails.
    """
    level = logging.DEBUG if logs else logging.INFO

    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )

    json_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(json_formatter)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(level)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root.setLevel(level)

    # High-volume per-device events -> JSON file ONLY (do not flood the journal).
    osevents = logging.getLogger(OSEVENTS_LOGGER)
    for h in list(osevents.handlers):
        osevents.removeHandler(h)
    osevents.addHandler(file_handler)  # shared handler -> same file
    osevents.setLevel(level)
    osevents.propagate = False  # stop it reaching root's console handler

    # Universal envelope: host + a per-run correlation id.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        host=socket.gethostname(),
        cycle_id=uuid.uuid4().hex,
    )


def bind_envelope(service: str, submitter_environment: str, hostgroup: str) -> None:
    """Bind the per-service envelope fields onto every subsequent log event."""
    structlog.contextvars.bind_contextvars(
        service=service,
        submitter_environment=submitter_environment,
        hostgroup=hostgroup,
    )


def event_logger():
    """Logger for high-volume per-device events (writes to the JSON file only)."""
    return structlog.get_logger(OSEVENTS_LOGGER)
