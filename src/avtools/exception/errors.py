"""AV Tools exception hierarchy.

The original AV Tools code only defined a small set of exceptions (EAM/LanDB).
For operational visibility we want typed exceptions for the major subsystems.

Design goals:
- Keep the taxonomy explicit (no metaprogramming).
- Preserve backwards compatibility: existing exceptions remain unchanged.
- Add module-level base exceptions and per-file leaf exceptions so that
  core/av_tools.py can catch and log failures at sensible boundaries.
"""

from __future__ import annotations  # Enable postponed evaluation of annotations


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

    def __init__(self, message: str = "Multiple devices found, expected unique result.") -> None:
        """
        Initialize MoreThanOneDeviceFound with an optional error message.

        Args:
            message (str): Human-readable error message (default provided).
        """
        super().__init__(message)


# ---------------------------------------------------------------------------
# AV Tools typed exceptions (new)
# ---------------------------------------------------------------------------


class AVToolsError(Exception):
    """Base class for AV Tools internal errors."""


class InfluxError(AVToolsError):
    """Base class for Influx-related failures."""


class PostgresError(AVToolsError):
    """Base class for Postgres/SQLAlchemy-related failures."""


class SNMPError(AVToolsError):
    """Base class for SNMP collection/handler failures."""


class UtilsError(AVToolsError):
    """Base class for utility/helper failures."""


class TimeseriesError(AVToolsError):
    """Base class for time-series publishing/encoding failures."""


class PipelineError(AVToolsError):
    """Base class for pipeline/router failures."""


# --- Influx tree -----------------------------------------------------------


class InfluxPackageError(InfluxError):
    """Errors originating from avtools.influx package initialization."""


class InfluxClientError(InfluxError):
    """Errors originating from avtools.influx.client."""


class InfluxParsableError(InfluxError):
    """Errors originating from avtools.influx.parsable."""


class InfluxSerializableError(InfluxError):
    """Errors originating from avtools.influx.serializable."""


class TimeSeriesHelperError(InfluxError):
    """Errors originating from avtools.influx.ts_helper."""


class InfluxDataPackageError(InfluxError):
    """Errors originating from avtools.influx.data package initialization."""


class StatsBaseError(InfluxError):
    """Errors originating from avtools.influx.data.stats_base."""


class ProjectorStatsError(InfluxError):
    """Errors originating from avtools.influx.data.projector_stats."""


# --- Postgres tree ---------------------------------------------------------


class PostgresPackageError(PostgresError):
    """Errors originating from avtools.postgres package initialization."""


class PostgresClientError(PostgresError):
    """Errors originating from avtools.postgres.client."""


class PostgresORMPackageError(PostgresError):
    """Errors originating from avtools.postgres.orm package initialization."""


class EAMDeviceORMError(PostgresError):
    """Errors originating from avtools.postgres.orm.eam_device."""


class EAMPositionORMError(PostgresError):
    """Errors originating from avtools.postgres.orm.eam_position."""


class LanDBIPAddressORMError(PostgresError):
    """Errors originating from avtools.postgres.orm.landb_ipaddress."""


# New Postgres subpackages (inventory/monitoring)


class PostgresInventoryPackageError(PostgresError):
    """Errors originating from avtools.postgres.inventory package initialization."""


class PostgresInventoryClientError(PostgresError):
    """Errors originating from avtools.postgres.inventory.client."""


class PostgresInventoryORMPackageError(PostgresError):
    """Errors originating from avtools.postgres.inventory.orm package initialization."""


class PostgresMonitoringPackageError(PostgresError):
    """Errors originating from avtools.postgres.monitoring package initialization."""


class PostgresMonitoringClientError(PostgresError):
    """Errors originating from avtools.postgres.monitoring.client."""


class PostgresMonitoringCodecError(PostgresError):
    """Errors originating from avtools.postgres.monitoring.codec."""


class PostgresMonitoringModelsPackageError(PostgresError):
    """Errors originating from avtools.postgres.monitoring.models package initialization."""


class PostgresMonitoringORMPackageError(PostgresError):
    """Errors originating from avtools.postgres.monitoring.orm package initialization."""


# --- SNMP tree -------------------------------------------------------------


class SNMPPackageError(SNMPError):
    """Errors originating from avtools.snmp package initialization."""


class SNMPClientError(SNMPError):
    """Errors originating from avtools.snmp.client."""


class SNMPFactoriesPackageError(SNMPError):
    """Errors originating from avtools.snmp.factories package initialization."""


class DeviceHandlerFactoryError(SNMPError):
    """Errors originating from avtools.snmp.factories.device_factory."""


class ProjectorHandlerFactoryError(SNMPError):
    """Errors originating from avtools.snmp.factories.projector_factory."""


class SNMPHandlersPackageError(SNMPError):
    """Errors originating from avtools.snmp.handlers package initialization."""


class AbstractDeviceHandlerError(SNMPError):
    """Errors originating from avtools.snmp.handlers.abstract_device_handler."""


class ProjectorHandlerError(SNMPError):
    """Errors originating from avtools.snmp.handlers.projector."""


# New SNMP subpackages (queries)


class SNMPQueriesPackageError(SNMPError):
    """Errors originating from avtools.snmp.queries package initialization."""


class SNMPQuerySpecError(SNMPError):
    """Errors originating from avtools.snmp.queries (spec definition/validation)."""


class SNMPQueryRoutingError(SNMPError):
    """Errors originating from routing/selection of SNMP queries."""


class SNMPQueryExecutionError(SNMPError):
    """Errors originating from executing a routed SNMP query spec."""


# New pipeline subpackage


class PipelinePackageError(PipelineError):
    """Errors originating from avtools.pipeline package initialization."""


class SNMPObserverRouterError(PipelineError):
    """Errors originating from avtools.pipeline.snmp_router."""


# New timeseries subpackage


class TimeseriesPackageError(TimeseriesError):
    """Errors originating from avtools.timeseries package initialization."""


class TimeseriesEncoderError(TimeseriesError):
    """Errors originating from avtools.timeseries.encoder."""


class TimeseriesMetricsError(TimeseriesError):
    """Errors originating from avtools.timeseries.metrics."""


class TimeseriesPublisherError(TimeseriesError):
    """Errors originating from avtools.timeseries.otlp_publisher."""


class OTLPPublishError(TimeseriesPublisherError):
    """Raised when OTLP publish fails (Prometheus via MONIT)."""


# --- Utils tree ------------------------------------------------------------


class UtilsPackageError(UtilsError):
    """Errors originating from avtools.utils package initialization."""


class EAMTextSanitizerError(UtilsError):
    """Errors originating from avtools.utils.eam_sanitizer."""


class SyncReportLoggerError(UtilsError):
    """Errors originating from avtools.utils.sync_reporting."""
