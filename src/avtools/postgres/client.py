"""Postgres facade for AV Tools.

- Inventory cache client lives in :mod:`avtools.postgres.inventory.client`.
- Monitoring persistence client lives in :mod:`avtools.postgres.monitoring.client`.
"""

from avtools.postgres.inventory.client import PostgresClient  # inventory cache
from avtools.postgres.monitoring.client import PostgresMonitoringClient

__all__ = ["PostgresClient", "PostgresMonitoringClient"]
