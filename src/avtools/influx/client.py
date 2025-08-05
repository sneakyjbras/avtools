from __future__ import annotations

from asyncio import get_running_loop
from typing import Any, Dict, List

from influxdb import InfluxDBClient


class InfluxClient:
    """
    Helper class for writing time-series data points to InfluxDB.
    Provides both synchronous and asynchronous methods, so you can
    integrate cleanly with asyncio-based workflows.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
        ssl: bool = False,
        verify_ssl: bool = False,
    ) -> None:
        """
        Initialize the InfluxDB client.

        Args:
            host:        InfluxDB server hostname or IP.
            port:        InfluxDB server port.
            username:    Username for authentication.
            password:    Password for authentication.
            database:    Default database to write to.
            ssl:         Whether to use HTTPS.
            verify_ssl:  Whether to verify the server's TLS certificate.
        """
        self.client: InfluxDBClient = InfluxDBClient(
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            ssl=ssl,
            verify_ssl=verify_ssl,
        )

    def write_points(self, points: list[dict[str, Any]]) -> None:
        """
        Write a batch of points to InfluxDB synchronously.

        Args:
            points: A list of InfluxDB‐style point dicts. Each dict
                    should include keys like 'measurement', 'tags',
                    'time', and 'fields'.
        """
        if not points:
            # Nothing to write
            return

        try:
            self.client.write_points(points)
        except Exception as exc:
            # Replace with your logger if desired
            print(f"[InfluxClient] Error writing points: {exc}")

    async def write_points_async(self, points: list[dict[str, Any]]) -> None:
        """
        Asynchronously write points to InfluxDB by delegating
        the blocking call to a thread pool.

        Args:
            points: A list of InfluxDB‐style point dicts.
        """
        loop = get_running_loop()
        # Run the synchronous write_points method in a background thread
        await loop.run_in_executor(None, self.write_points, points)
