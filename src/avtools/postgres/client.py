"""Postgres cache client.

AVTools persists external system snapshots (EAM, LanDB) into Postgres cache
tables. This client is a thin SQLAlchemy ORM wrapper used by the core
orchestrator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog
from eam_rest_client import Equipment
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session, sessionmaker

from avtools.exception.errors import NoRecordsFound, PostgresClientError
from avtools.postgres.orm.eam_device import EAMDeviceORM
from avtools.postgres.orm.eam_position import EAMPositionORM
from avtools.postgres.orm.landb_ipaddress import CachedIPAddress, LanDBIPAddressORM

logger = structlog.get_logger(__name__)


class PostgresClient:
    """Postgres cache client used by AVTools.

    AVTools stores *snapshots* of upstream systems (EAM, LanDB) in Postgres cache
    tables. This client is a thin SQLAlchemy wrapper that:

    - reads full snapshot tables into domain objects, and
    - applies inserts/updates/deletes computed by the core sync logic.

    Notes:
        These are cache tables. When a schema mismatch is detected, the client may
        drop and recreate specific tables on startup.
    """

    def __init__(self, connection_string: str) -> None:
        """Create a PostgresClient and ensure cache tables exist.

        Args:
            connection_string: SQLAlchemy connection string (e.g. DBoD URL).

        Returns:
            None.

        Notes:
            The cache tables are created via SQLAlchemy metadata. This is not a
            migration system: on detected legacy schemas, the relevant cache tables
            are dropped and recreated.
        """
        from sqlalchemy.engine import Engine

        try:
            self.engine: Engine = create_engine(connection_string, echo=False)
        except Exception as exc:
            raise PostgresClientError("Failed to create SQLAlchemy engine") from exc

        # NOTE:
        # SQLAlchemy's `create_all()` does not migrate existing tables.
        # The LanDB tables are caches: if we detect a legacy schema,
        # we drop & recreate them.
        try:
            inspector = inspect(self.engine)
            if "landb_ipaddresses" in inspector.get_table_names():
                cols = {c["name"] for c in inspector.get_columns("landb_ipaddresses")}
                expected = {
                    "equipmentno",
                    "serialnumber",
                    "ip",
                    "name",
                    "hostname",
                    "landb_serial",
                    "landb_description",
                    "eqclass",
                    "manufacturer",
                    "model",
                    "building",
                    "floor",
                    "room",
                }
                if not expected.issubset(cols):
                    logger.warning(
                        "landb_ipaddresses_legacy_schema_detected_dropping_cache_table",
                        existing_columns=sorted(cols),
                    )
                    with self.engine.begin() as conn:
                        conn.exec_driver_sql("DROP TABLE landb_ipaddresses")
        except Exception:
            logger.warning("landb_ipaddresses_schema_probe_failed", exc_info=True)

        # Legacy table cleanup: `landb_location` was merged into `landb_ipaddresses`.
        try:
            inspector = inspect(self.engine)
            if "landb_location" in inspector.get_table_names():
                logger.warning("landb_location_table_found_dropping_legacy_cache_table")
                with self.engine.begin() as conn:
                    conn.exec_driver_sql("DROP TABLE landb_location")
        except Exception:
            logger.warning("landb_location_drop_failed", exc_info=True)

        # Ensure EAM tables include the new `location` column (cache tables; drop & recreate on schema change).
        try:
            inspector = inspect(self.engine)
            if "eam_devices" in inspector.get_table_names():
                cols = {c["name"] for c in inspector.get_columns("eam_devices")}
                if "location" not in cols:
                    logger.warning(
                        "eam_devices_legacy_schema_detected_dropping_cache_table",
                        existing_columns=sorted(cols),
                    )
                    with self.engine.begin() as conn:
                        conn.exec_driver_sql("DROP TABLE eam_devices")
        except Exception:
            logger.warning("eam_devices_schema_probe_failed", exc_info=True)

        try:
            inspector = inspect(self.engine)
            if "eam_positions" in inspector.get_table_names():
                cols = {c["name"] for c in inspector.get_columns("eam_positions")}
                if "location" not in cols:
                    logger.warning(
                        "eam_positions_legacy_schema_detected_dropping_cache_table",
                        existing_columns=sorted(cols),
                    )
                    with self.engine.begin() as conn:
                        conn.exec_driver_sql("DROP TABLE eam_positions")
        except Exception:
            logger.warning("eam_positions_schema_probe_failed", exc_info=True)

        # Create all tables declared on Base metadata
        try:
            LanDBIPAddressORM.metadata.create_all(self.engine)
            EAMDeviceORM.metadata.create_all(self.engine)
            EAMPositionORM.metadata.create_all(self.engine)
        except Exception as exc:
            raise PostgresClientError("Failed to create cache tables") from exc

        self.Session: sessionmaker[Session] = sessionmaker(bind=self.engine)

    # --- Generic helpers -----------------------------------------------------

    def _get_all(
        self, orm_cls: type, converter: Callable[[Any], Any], error_msg: str
    ) -> list[Any]:
        """Fetch all rows from a cache table and convert them to domain objects.

        Args:
            orm_cls: SQLAlchemy ORM mapped class for the cache table.
            converter: Function converting an ORM instance to a domain object.
            error_msg: Log/exception message prefix.

        Returns:
            List of converted domain objects.

        Notes:
            This performs a full-table SELECT. That is intentional because these
            tables are snapshots and are expected to remain reasonably small.
        """
        with self.Session() as session:
            try:
                stmt = select(orm_cls)
                orm_instances = session.execute(stmt).scalars().all()
                return [converter(inst) for inst in orm_instances]
            except SQLAlchemyError as e:
                logger.error(error_msg, error=str(e), exc_info=True)
                raise PostgresClientError(error_msg) from e

    @staticmethod
    def _get_pk(obj: Any) -> str:
        """Best-effort primary key getter for domain objects.

        Args:
            obj: Domain object with a stable identifier attribute.

        Returns:
            A stringified identifier.

        Notes:
            AVTools primarily uses:
            - EAM Equipment: ``code``
            - LanDB cached records: ``equipment_no``/``equipmentno``
        """
        for attr in (
            "code",
            "name",
            "device",
            "equipment_no",
            "equipmentno",
            "device_name",
            "hostname",
            "host_name",
        ):
            v = getattr(obj, attr, None)
            if v not in (None, ""):
                return str(v)
        raise ValueError(f"Unable to determine primary key for {type(obj)!r}")

    def _sync_devices(
        self,
        orm_cls: type,
        from_domain: Callable[[Any], Any],
        id_getter: Callable[[Any], str],
        to_insert: list[Any],
        to_update: list[tuple[Any, dict[str, Any]]],
        to_delete: list[str],
        *,
        pk_attr: str = "equipment_no",
    ) -> None:
        """Apply inserts/updates/deletes to a cache table.

        Args:
            orm_cls: Target SQLAlchemy ORM mapped class.
            from_domain: Converter from domain object to ORM instance.
            id_getter: Function extracting the primary key from a domain object.
            to_insert: Items that do not exist in the cache table yet.
            to_update: Items that exist in the cache table and have field diffs.
            to_delete: Primary keys that exist in the cache table but not upstream.
            pk_attr: ORM attribute name representing the primary key column.

        Returns:
            None.

        Notes:
            For updates, AVTools re-builds an ORM instance from the domain object and
            copies column attributes onto the persisted row. This avoids having to
            maintain a separate field name mapping for each update.
        """
        mapper_cols = [c.key for c in inspect(orm_cls).column_attrs]

        try:
            with self.Session() as session:
                with session.begin():
                    if to_delete:
                        pk_col = getattr(orm_cls, pk_attr)
                        stmt = delete(orm_cls).where(pk_col.in_(to_delete))
                        session.execute(stmt)

                    for domain_obj, _changes in to_update:
                        pk = id_getter(domain_obj)
                        orm_obj = session.get(orm_cls, pk)
                        if not orm_obj:
                            continue

                        mapped = from_domain(domain_obj)

                        # Copy all mapped column attributes except the PK.
                        for attr in mapper_cols:
                            if attr == pk_attr:
                                continue
                            if hasattr(mapped, attr):
                                setattr(orm_obj, attr, getattr(mapped, attr))

                    if to_insert:
                        orm_objs = [from_domain(d) for d in to_insert]
                        session.add_all(orm_objs)
        except SQLAlchemyError as e:
            logger.error(
                "postgres_sync_failed",
                table=getattr(orm_cls, "__tablename__", str(orm_cls)),
                error=str(e),
                exc_info=True,
            )
            raise PostgresClientError("Failed to sync cache table") from e

    # --- Getters -------------------------------------------------------------

    def get_all_landb_devices(self) -> list[CachedIPAddress]:
        """Return all cached LanDB IP targets.

        Returns:
            List of ``CachedIPAddress`` rows converted from the cache table.

        Notes:
            Method name is kept for backwards compatibility.
        """
        return self._get_all(
            LanDBIPAddressORM,
            lambda row: row.to_ipaddress(),
            "Error querying LanDB IP addresses",
        )

    def get_all_eam_devices(self) -> list[Equipment]:
        """Return all cached EAM devices.

        Returns:
            List of ``Equipment`` domain objects reconstructed from the cache table.
        """
        return self._get_all(
            EAMDeviceORM,
            lambda row: (row.to_equipment()),
            "Error querying EAM devices",
        )

    def get_all_eam_positions(self) -> list[Equipment]:
        """Return all cached EAM positions.

        Returns:
            List of ``Equipment`` domain objects reconstructed from the cache table.
        """
        return self._get_all(
            EAMPositionORM,
            lambda row: (row.to_equipment()),
            "Error querying EAM positions",
        )

    # --- Sync entrypoints ----------------------------------------------------

    def sync_eam_devices(
        self,
        to_insert: list[Equipment],
        to_update: list[tuple[Equipment, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        """Persist the EAM device diff into the cache table.

        Args:
            to_insert: Devices that should be inserted.
            to_update: Devices that should be updated, along with a diff dict.
            to_delete: Primary keys that should be deleted.

        Returns:
            None.
        """
        self._sync_devices(
            EAMDeviceORM,
            EAMDeviceORM.from_equipment,
            lambda e: str(e.code),
            to_insert,
            to_update,
            to_delete,
            pk_attr="equipment_no",
        )

    def sync_eam_positions(
        self,
        to_insert: list[Equipment],
        to_update: list[tuple[Equipment, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        """Persist the EAM position diff into the cache table.

        Args:
            to_insert: Positions that should be inserted.
            to_update: Positions that should be updated, along with a diff dict.
            to_delete: Primary keys that should be deleted.

        Returns:
            None.
        """
        self._sync_devices(
            EAMPositionORM,
            EAMPositionORM.from_equipment,
            lambda e: str(e.code),
            to_insert,
            to_update,
            to_delete,
            pk_attr="equipment_no",
        )

    def sync_landb_devices(
        self,
        to_insert: list[CachedIPAddress],
        to_update: list[tuple[CachedIPAddress, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        """Persist the LanDB IP cache diff into the cache table.

        Args:
            to_insert: New cached IP rows to insert.
            to_update: Cached IP rows to update, along with a diff dict.
            to_delete: Primary keys (equipment numbers) to delete.

        Returns:
            None.

        Notes:
            The primary key is the EAM equipment number (``equipment_no``) to keep
            joins back to EAM fast and stable.
        """
        self._sync_devices(
            LanDBIPAddressORM,
            LanDBIPAddressORM.from_ipaddress,
            lambda ip: str(getattr(ip, "equipment_no")),
            to_insert,
            to_update,
            to_delete,
            pk_attr="equipment_no",
        )
