from __future__ import annotations

import datetime
from typing import Dict

import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = structlog.get_logger(__name__)


class TimeSeriesHelper:
    """
    Helper for writing SNMP query results to InfluxDB.

    Attributes:
        url (str): InfluxDB HTTP endpoint.
        token (str): Authentication token for InfluxDB.
        org (str): Organization name in InfluxDB.
        bucket (str): Target bucket for writes.
        client (InfluxDBClient): Low-level client instance.
        write_api: Synchronous write API for record ingestion.
    """

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
    ) -> None:
        """
        Initialize TimeSeriesHelper with InfluxDB connection parameters.

        Args:
            url (str): URL of the InfluxDB server.
            token (str): Authentication token for the InfluxDB API.
            org (str): Organization name in InfluxDB.
            bucket (str): Bucket to write time series data into.
        """
        self.url: str = url
        self.token: str = token
        self.org: str = org
        self.bucket: str = bucket
        # Create client and write API
        self.client: InfluxDBClient = InfluxDBClient(
            url=self.url,
            token=self.token,
            org=self.org,
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        logger.info(f"TimeSeriesHelper connected to InfluxDB at {self.url}")

    def write_snmp_data(self, snmp_results: dict[str, str]) -> None:
        """
        Write SNMP response data to InfluxDB as time series points.

        Args:
            snmp_results (Dict[str, str]): Mapping of IP address to SNMP response string.

        For each entry:
        - measurement: 'snmp'
        - tag: 'ip'
        - field: 'response'
        - timestamp: current UTC time with nanosecond precision
        """
        points: list[Point] = []
        now: datetime.datetime = datetime.datetime.utcnow()
        for ip, response in snmp_results.items():
            point: Point = (
                Point("snmp")
                .tag("ip", ip)
                .field("response", response)
                .time(now, WritePrecision.NS)
            )
            points.append(point)
            logger.info(f"Prepared SNMP point for IP {ip}")

        try:
            self.write_api.write(bucket=self.bucket, org=self.org, record=points)
            logger.info("SNMP data written successfully to InfluxDB.")
        except Exception as err:
            logger.error(f"Error writing SNMP data to InfluxDB: {err}")
