"""Exception re-exports for AV Tools.

This module provides a single import location for AVTools exception types.
"""

from __future__ import annotations

from avtools.exception.errors import (
    AVToolsError,
    AbstractDeviceHandlerError,
    DeviceHandlerFactoryError,
    EAMError,
    EAMDeviceORMError,
    EAMPositionORMError,
    EAMTextSanitizerError,
    InfluxClientError,
    InfluxDataPackageError,
    InfluxError,
    InfluxPackageError,
    InfluxParsableError,
    InfluxSerializableError,
    LanDBError,
    LanDBIPAddressORMError,
    MoreThanOneDeviceFound,
    NoRecordsFound,
    PostgresClientError,
    PostgresError,
    PostgresORMPackageError,
    PostgresPackageError,
    ProjectorHandlerError,
    ProjectorHandlerFactoryError,
    ProjectorStatsError,
    SNMPClientError,
    SNMPError,
    SNMPFactoriesPackageError,
    SNMPHandlersPackageError,
    SNMPPackageError,
    StatsBaseError,
    SyncReportLoggerError,
    TimeSeriesHelperError,
    UtilsError,
    UtilsPackageError,
)

__all__ = [
    # Legacy
    "EAMError",
    "LanDBError",
    "NoRecordsFound",
    "MoreThanOneDeviceFound",
    # New base types
    "AVToolsError",
    "InfluxError",
    "PostgresError",
    "SNMPError",
    "UtilsError",
    # Influx
    "InfluxPackageError",
    "InfluxClientError",
    "InfluxParsableError",
    "InfluxSerializableError",
    "TimeSeriesHelperError",
    "InfluxDataPackageError",
    "StatsBaseError",
    "ProjectorStatsError",
    # Postgres
    "PostgresPackageError",
    "PostgresClientError",
    "PostgresORMPackageError",
    "EAMDeviceORMError",
    "EAMPositionORMError",
    "LanDBIPAddressORMError",
    # SNMP
    "SNMPPackageError",
    "SNMPClientError",
    "SNMPFactoriesPackageError",
    "DeviceHandlerFactoryError",
    "ProjectorHandlerFactoryError",
    "SNMPHandlersPackageError",
    "AbstractDeviceHandlerError",
    "ProjectorHandlerError",
    # Utils
    "UtilsPackageError",
    "EAMTextSanitizerError",
    "SyncReportLoggerError",
]
