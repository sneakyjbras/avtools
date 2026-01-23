from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog
from eam_rest_client import Equipment
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session, sessionmaker

from avtools.exception.errors import NoRecordsFound
from avtools.postgres.orm.eam_device import EAMDeviceORM
from avtools.postgres.orm.eam_position import EAMPositionORM
from avtools.postgres.orm.landb_ipaddress import CachedIPAddress, LanDBIPAddressORM

logger = structlog.get_logger(__name__)


class PostgresClient:
    """
    Helper class for managing database operations using SQLAlchemy ORM.

    - Persists EAM records as a small subset of fields in `EAMDeviceORM`/`EAMPositionORM`.
    - Persists LanDB records as a small subset of fields in `LanDBIPAddressORM`.
    - Domain model for EAM is `eam_rest_client.Equipment`.
    - Domain model for LanDB IP cache is `avtools.postgres.orm.landb_ipaddress.CachedIPAddress`.
    """

    def __init__(self, connection_string: str) -> None:
        from sqlalchemy.engine import Engine

        self.engine: Engine = create_engine(connection_string, echo=False)

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
        LanDBIPAddressORM.metadata.create_all(self.engine)
        EAMDeviceORM.metadata.create_all(self.engine)
        EAMPositionORM.metadata.create_all(self.engine)

        self.Session: sessionmaker[Session] = sessionmaker(bind=self.engine)

    # --- Generic helpers -----------------------------------------------------

    def _get_all(
        self, orm_cls: type, converter: Callable[[Any], Any], error_msg: str
    ) -> list[Any]:
        """
        Generic "get all" helper for fetching and converting ORM records.

        Efficiency:
        - One SELECT for the whole table (appropriate for these cache/snapshot tables).
        - Uses scalars().all() to avoid loading row tuples.
        """
        with self.Session() as session:
            try:
                stmt = select(orm_cls)
                orm_instances = session.execute(stmt).scalars().all()
                return [converter(inst) for inst in orm_instances]
            except SQLAlchemyError as e:
                logger.error(f"{error_msg}: {e}")
                raise

    @staticmethod
    def _get_pk(obj: Any) -> str:
        """Best-effort primary key getter for domain objects.

        For EAM `Equipment`, the identifier is `code`.
        For LanDB cached records, the identifier is usually `equipment_no`.
        For older/other models, we try a few common fallbacks.
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
        """
        Generic sync implementation for "device-like" tables.

        Note:
          With `Equipment`, the diff keys (domain field names) do not match ORM attribute
          names. For updates, we therefore re-map using `from_domain(domain_obj)` and
          copy mapped ORM attributes onto the persisted row.

        pk_attr:
          The ORM column name that holds the primary key for this table.
          - EAM ORM tables typically use "equipment_no"
          - LanDB cache tables use "equipment_no"
        """
        mapper_cols = [c.key for c in inspect(orm_cls).column_attrs]

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

    # --- Getters -------------------------------------------------------------

    def get_all_landb_devices(self) -> list[CachedIPAddress]:
        """
        Retrieve all LanDB cached IP addresses (CachedIPAddress) from the database.

        NOTE: method name kept for backwards compatibility with callers.
        """
        return self._get_all(
            LanDBIPAddressORM,
            lambda row: row.to_ipaddress(),
            "Error querying LanDB IP addresses",
        )

    def get_all_eam_devices(self) -> list[Equipment]:
        """
        Retrieve all EAM devices (Equipment) from the database.
        """
        return self._get_all(
            EAMDeviceORM,
            lambda row: (row.to_equipment()),
            "Error querying EAM devices",
        )

    def get_all_eam_positions(self) -> list[Equipment]:
        """
        Retrieve all EAM positions (Equipment) from the database.
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
        """
        Sync EAM devices (Equipment) by delegating to the generic sync implementation.
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
        """
        Sync EAM positions (Equipment) by delegating to the generic sync implementation.
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
        """
        Sync LanDB cached IP addresses (CachedIPAddress) using the LanDB IP cache ORM.
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
