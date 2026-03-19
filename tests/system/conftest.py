"""Pytest fixtures for system-level pipeline tests."""

from __future__ import annotations

import os
from typing import Iterable

import pytest
from sqlalchemy import create_engine, text


def _postgres_url() -> str | None:
    """Return Postgres URL from environment.

    Returns:
        Postgres SQLAlchemy URL if configured, else None.
    """
    return (
        os.environ.get("AVTOOLS_TEST_POSTGRES_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("DATABASE_URL")
    )


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Return the SQLAlchemy Postgres URL for integration tests.

    The tests require a *dedicated* Postgres database because they drop AVTools
    tables between tests.

    Env vars (first one wins):
      - AVTOOLS_TEST_POSTGRES_URL
      - POSTGRES_URL
      - DATABASE_URL
    """
    url = _postgres_url()
    if not url:
        pytest.skip(
            "No Postgres URL configured. Set AVTOOLS_TEST_POSTGRES_URL to run pipeline integration tests."
        )
    return url


@pytest.fixture(scope="session")
def engine(postgres_url: str):
    """Shared SQLAlchemy engine for test helpers."""
    return create_engine(postgres_url, pool_pre_ping=True)


def _drop_tables(engine, tables: Iterable[str]) -> None:
    with engine.begin() as conn:
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))


@pytest.fixture(autouse=True)
def clean_avtools_tables(engine):
    """Drop AVTools tables before each test.

    This keeps tests isolated without requiring a full DB reset.

    IMPORTANT: Use a dedicated test database.
    """
    _drop_tables(
        engine,
        [
            # Inventory cache tables
            "landb_ipaddresses",
            "eam_devices",
            "eam_positions",
            # Monitoring tables
            "avtools_projector_monitoring",
            "avtools_device_sysdescr_monitoring",
        ],
    )
    yield


@pytest.fixture()
def row_count(engine):
    """Helper: return row count for a given table name."""

    def _count(table: str) -> int:
        with engine.begin() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())

    return _count
