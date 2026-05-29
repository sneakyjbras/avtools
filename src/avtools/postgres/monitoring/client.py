"""Postgres monitoring client.

Stores *non time-series* monitoring data (strings / identity-like fields)
derived from SNMP.

Today:
- projector firmware + power status (latest-known) keyed by equipment_no
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from avtools.exception.errors import PostgresMonitoringClientError
from avtools.postgres.monitoring.models.projector import ProjectorMonitoring
from avtools.postgres.monitoring.models.sysdescr import DeviceSysDescrMonitoring
from avtools.postgres.monitoring.orm.projector import Base as MonitoringBase
from avtools.postgres.monitoring.orm.projector import ProjectorMonitoringORM
from avtools.postgres.monitoring.orm.sysdescr import DeviceSysDescrMonitoringORM

logger = structlog.get_logger(__name__)


class PostgresMonitoringClient:
    """Client for Postgres monitoring tables (non time-series)."""

    def __init__(self, postgres_url: str) -> None:
        self.postgres_url = postgres_url
        try:
            self._engine = create_engine(postgres_url, pool_pre_ping=True)
        except Exception as exc:  # pragma: no cover
            raise PostgresMonitoringClientError("Failed to create SQLAlchemy engine") from exc

        self._session_maker: sessionmaker[Session] = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

        # Create tables if missing (no migrations here).
        try:
            MonitoringBase.metadata.create_all(self._engine)
        except SQLAlchemyError as exc:  # pragma: no cover
            raise PostgresMonitoringClientError("Failed to create monitoring tables") from exc

    def upsert_projector_monitoring(self, records: Iterable[ProjectorMonitoring]) -> int:
        """Upsert projector monitoring rows.

        Args:
            records: Iterable of ProjectorMonitoring models.

        Returns:
            Number of rows attempted (len(records) if it is a sized container, else count).
        """
        rows = [ProjectorMonitoringORM.from_model(r) for r in records]
        if not rows:
            return 0

        try:
            from sqlalchemy.dialects.postgresql import insert  # type: ignore
        except Exception:
            insert = None  # type: ignore[assignment]

        with self._session_maker() as session:
            try:
                if insert is not None:
                    stmt = insert(ProjectorMonitoringORM).values(
                        [
                            {
                                "equipment_no": r.equipment_no,
                                "ip": r.ip,
                                "firmware": r.firmware,
                                "power_status": r.power_status,
                                "updated_at": r.updated_at,
                            }
                            for r in rows
                        ]
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[ProjectorMonitoringORM.equipment_no],
                        set_={
                            "ip": stmt.excluded.ip,
                            "firmware": stmt.excluded.firmware,
                            "power_status": stmt.excluded.power_status,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                    session.execute(stmt)
                else:
                    # Fallback: ORM merge (less efficient, but safe).
                    for r in rows:
                        session.merge(r)
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.exception("postgres_monitoring_upsert_failed", rows=len(rows))
                raise PostgresMonitoringClientError("Failed to upsert monitoring rows") from exc

        logger.info("postgres_monitoring_projector_upserted", rows=len(rows))
        return len(rows)

    def upsert_device_sysdescr_monitoring(self, records: Iterable[DeviceSysDescrMonitoring]) -> int:
        """Upsert sysDescr monitoring rows (latest-known) keyed by equipment_no."""
        rows = [DeviceSysDescrMonitoringORM.from_model(r) for r in records]
        if not rows:
            return 0

        try:
            from sqlalchemy.dialects.postgresql import insert  # type: ignore
        except Exception:
            insert = None  # type: ignore[assignment]

        with self._session_maker() as session:
            try:
                if insert is not None:
                    stmt = insert(DeviceSysDescrMonitoringORM).values(
                        [
                            {
                                "equipment_no": r.equipment_no,
                                "ip": r.ip,
                                "eqclass": r.eqclass,
                                "category": r.category,
                                "sysdescr": r.sysdescr,
                                "updated_at": r.updated_at,
                            }
                            for r in rows
                        ]
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[DeviceSysDescrMonitoringORM.equipment_no],
                        set_={
                            "ip": stmt.excluded.ip,
                            "eqclass": stmt.excluded.eqclass,
                            "category": stmt.excluded.category,
                            "sysdescr": stmt.excluded.sysdescr,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                    session.execute(stmt)
                else:
                    for r in rows:
                        session.merge(r)
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.exception("postgres_monitoring_sysdescr_upsert_failed", rows=len(rows))
                raise PostgresMonitoringClientError(
                    "Failed to upsert sysDescr monitoring rows"
                ) from exc

        logger.info("postgres_monitoring_sysdescr_upserted", rows=len(rows))
        return len(rows)
