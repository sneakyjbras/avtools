from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, List, Tuple

from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from avtools.eam.client import EAMDevice
from avtools.exception.errors import NoRecordsFound
from avtools.io.logger import system_logger
from avtools.landb.client import LanDBDevice
from avtools.postgres.orm.eam_device import EAMDeviceORM
from avtools.postgres.orm.landb_device import LanDBDeviceORM

# postgres_client.py


class PostgresClient:
    """
    Helper class for managing database operations using SQLAlchemy ORM.

    Converts domain device objects to ORM models and handles CRUD sync operations.
    """

    def __init__(self, connection_string: str) -> None:
        """
        Initialize PostgresClient with a database connection string.

        Args:
            connection_string (str): PostgreSQL connection URL.
        """
        from sqlalchemy.engine import Engine

        self.engine: Engine = create_engine(connection_string, echo=False)
        # Create all tables declared on Base metadata
        LanDBDeviceORM.metadata.create_all(self.engine)
        EAMDeviceORM.metadata.create_all(self.engine)

        self.Session: sessionmaker[Session] = sessionmaker(bind=self.engine)

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
                system_logger.error(f"{error_msg}: {e}")
                raise

    def get_all_landb_devices(self) -> list[LanDBDevice]:
        """
        Retrieve all LanDB devices from the database.
        """
        return self._get_all(
            LanDBDeviceORM, LanDBDevice.from_orm, "Error querying LanDB devices"
        )

    def get_all_eam_devices(self) -> list[EAMDevice]:
        """
        Retrieve all EAM devices from the database.
        """
        return self._get_all(
            EAMDeviceORM, EAMDevice.from_orm, "Error querying EAM devices"
        )

    def _sync_devices(
        self,
        orm_cls: type,
        from_device: Callable[[Any], Any],
        to_insert: list[Any],
        to_update: list[tuple[Any, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        """
        Generic sync implementation for device tables.
        """
        with self.Session() as session:
            with session.begin():
                if to_delete:
                    stmt = delete(orm_cls).where(orm_cls.equipmentno.in_(to_delete))
                    session.execute(stmt)

                for device, changes in to_update:
                    orm_obj = session.get(orm_cls, device.equipmentno)
                    if orm_obj:
                        for attr, val in changes.items():
                            setattr(orm_obj, attr, val)

                if to_insert:
                    orm_objs = [from_device(d) for d in to_insert]
                    session.add_all(orm_objs)

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
            LanDBDeviceORM.from_device.__self__,  # or directly LanDBDeviceORM.from_device
            LanDBDeviceORM.from_device,
            to_insert,
            to_update,
            to_delete,
        )

    def sync_eam_devices(
        self,
        to_insert: list[EAMDevice],
        to_update: list[tuple[EAMDevice, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        """
        Sync EAM devices by delegating to the generic sync implementation.
        """
        self._sync_devices(
            EAMDeviceORM,
            EAMDeviceORM.from_device,
            to_insert,
            to_update,
            to_delete,
        )
