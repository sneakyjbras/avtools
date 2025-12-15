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
from avtools.landb.client import LanDBDevice
from avtools.postgres.orm.eam_device import EAMDeviceORM
from avtools.postgres.orm.eam_position import EAMPositionORM
from avtools.postgres.orm.landb_device import LanDBDeviceORM

logger = structlog.get_logger(__name__)


class PostgresClient:
    """
    Helper class for managing database operations using SQLAlchemy ORM.

    - Persists EAM records as a small subset of fields in `EAMDeviceORM`/`EAMPositionORM`.
    - Domain model for EAM is `eam_rest_client.Equipment` (NOT legacy EAMDevice/EAMPosition).
    """

    def __init__(self, connection_string: str) -> None:
        from sqlalchemy.engine import Engine

        self.engine: Engine = create_engine(connection_string, echo=False)

        # Create all tables declared on Base metadata
        LanDBDeviceORM.metadata.create_all(self.engine)
        EAMDeviceORM.metadata.create_all(self.engine)
        EAMPositionORM.metadata.create_all(self.engine)

        self.Session: sessionmaker[Session] = sessionmaker(bind=self.engine)

    # --- Generic helpers -----------------------------------------------------

    def _get_all(
        self, orm_cls: type, converter: Callable[[Any], Any], error_msg: str
    ) -> list[Any]:
        """
        Generic "get all" helper for fetching and converting ORM records.
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
        For older/other models, we try a few common fallbacks.

        """
        for attr in (
            "code",
            "equipment_no",
            "equipmentno",
            "device_name",
            "name",
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
    ) -> None:
        """
        Generic sync implementation for device-like tables.

        Note:
          With `Equipment`, the diff keys (domain field names) do not match ORM attribute
          names. For updates, we therefore re-map using `from_domain(domain_obj)` and
          copy mapped ORM attributes onto the persisted row.
        """
        mapper_cols = [c.key for c in inspect(orm_cls).column_attrs]

        with self.Session() as session:
            with session.begin():
                if to_delete:
                    stmt = delete(orm_cls).where(orm_cls.equipment_no.in_(to_delete))
                    session.execute(stmt)

                for domain_obj, _changes in to_update:
                    pk = id_getter(domain_obj)
                    orm_obj = session.get(orm_cls, pk)
                    if not orm_obj:
                        continue

                    mapped = from_domain(domain_obj)

                    # Copy all mapped column attributes except the PK.
                    for attr in mapper_cols:
                        if attr == "equipment_no":
                            continue
                        if hasattr(mapped, attr):
                            setattr(orm_obj, attr, getattr(mapped, attr))

                if to_insert:
                    orm_objs = [from_domain(d) for d in to_insert]
                    session.add_all(orm_objs)

    # --- Getters -------------------------------------------------------------

    def get_all_landb_devices(self) -> list[LanDBDevice]:
        """
        Retrieve all LanDB devices from the database.
        """
        return self._get_all(
            LanDBDeviceORM, LanDBDevice.from_orm, "Error querying LanDB devices"
        )

    def get_all_eam_devices(self) -> list[Equipment]:
        """
        Retrieve all EAM devices (Equipment) from the database.
        """
        return self._get_all(
            EAMDeviceORM,
            lambda row: (
                row.to_equipment() if hasattr(row, "to_equipment") else row.to_device()
            ),  # back-compat
            "Error querying EAM devices",
        )

    def get_all_eam_positions(self) -> list[Equipment]:
        """
        Retrieve all EAM positions (Equipment) from the database.
        """
        return self._get_all(
            EAMPositionORM,
            lambda row: (
                row.to_equipment()
                if hasattr(row, "to_equipment")
                else row.to_position()
            ),  # back-compat
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
            EAMDeviceORM.from_device,
            lambda e: str(e.code),
            to_insert,
            to_update,
            to_delete,
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
            EAMPositionORM.from_position,
            lambda e: str(e.code),
            to_insert,
            to_update,
            to_delete,
        )

    def sync_landb_devices(
        self,
        to_insert: list[LanDBDevice],
        to_update: list[tuple[LanDBDevice, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        """
        Sync LanDB devices by delegating to the generic sync implementation.
        """
        self._sync_devices(
            LanDBDeviceORM,
            LanDBDeviceORM.from_device,
            self._get_pk,
            to_insert,
            to_update,
            to_delete,
        )
