"""Fixtures for Postgres DB integration tests (destructive)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


# ---------------------------------------------------------------------------
# Postgres integration test fixtures
#
# These tests are DESTRUCTIVE: they drop and recreate AVTools cache/monitoring
# tables inside the target database.
#
# Provide a dedicated DB URL via:
#   - AVTOOLS_TEST_POSTGRES_URL (preferred)
#   - POSTGRES_URL (fallback)
# ---------------------------------------------------------------------------


_DROPPABLE_TABLES: tuple[str, ...] = (
    # Inventory cache tables
    "landb_location",  # legacy
    "landb_ipaddresses",
    "eam_devices",
    "eam_positions",
    # Monitoring tables
    "avtools_projector_monitoring",
    "avtools_device_sysdescr_monitoring",
)


def _drop_all_avtools_tables(engine: Engine) -> None:
    """Drop AVTools-owned tables (cache + monitoring).

    Args:
        engine: SQLAlchemy engine connected to the integration-test database.

    Returns:
        None.

    Notes:
        This is intentionally explicit (no schema reflection) to keep the tests
        deterministic and safe against accidentally dropping unrelated tables.
    """
    with engine.begin() as conn:
        for t in _DROPPABLE_TABLES:
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{t}" CASCADE')


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Return the Postgres URL for integration tests.

    Set either AVTOOLS_TEST_POSTGRES_URL or POSTGRES_URL.
    """
    url = os.environ.get("AVTOOLS_TEST_POSTGRES_URL") or os.environ.get("POSTGRES_URL")
    if not url:
        pytest.skip("Postgres integration tests require AVTOOLS_TEST_POSTGRES_URL or POSTGRES_URL")
    return url


@pytest.fixture(scope="session")
def pg_engine(postgres_url: str) -> Iterator[Engine]:
    """Create a session-scoped SQLAlchemy Engine."""
    engine = create_engine(postgres_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_db(pg_engine: Engine) -> None:
    """Ensure each test starts from a clean DB state."""
    _drop_all_avtools_tables(pg_engine)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres: marks tests that require a real Postgres database",
    )
