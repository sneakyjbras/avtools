from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, List, Tuple, Type

from sqlalchemy import Column, String, create_engine, delete, select
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from avtools.eam_helper import EAMDevice
from avtools.errors import NoRecordsFound
from avtools.landb_helper import LanDBDevice
from avtools.logger import system_logger

Base = declarative_base()


class LanDBDeviceORM(Base):
    """
    ORM model for LanDB devices.

    Maps LanDBDevice domain objects to the "landb_devices" table,
    with columns for equipment number, serial number, name,
    manufacturer, building, floor, room, and IP.
    """

    __tablename__ = "landb_devices"

    equipmentno: str = Column(String, primary_key=True, nullable=False)
    serial_number: str = Column(String, nullable=False)
    name: str | None = Column(String, nullable=True)
    manufacturer: str | None = Column(String, nullable=True)
    building: str | None = Column(String, nullable=True)
    floor: str | None = Column(String, nullable=True)
    room: str | None = Column(String, nullable=True)
    ip: str | None = Column(String, nullable=True)

    @classmethod
    def from_device(cls, device: LanDBDevice) -> LanDBDeviceORM:
        """
        Create an ORM instance from a LanDBDevice domain object.
        """
        return cls(
            equipmentno=device.equipmentno,
            serial_number=device.serial_number,
            name=device.name,
            manufacturer=device.manufacturer,
            building=device.building,
            floor=device.floor,
            room=device.room,
            ip=device.ip,
        )


class EAMDeviceORM(Base):
    """
    ORM model for EAM devices.

    Maps EAMDevice domain objects to the "eam_devices" table,
    with columns for equipment number, serial number,
    position, and equipment description.
    """

    __tablename__ = "eam_devices"

    equipmentno: str = Column(String, primary_key=True, nullable=False)
    serialnumber: str = Column(String, nullable=False)
    position: str | None = Column(String, nullable=True)
    equipmentdesc: str | None = Column(String, nullable=True)
    eqclass: str | None = Column(String, nullable=True)
    manufacturer: str | None = Column(String, nullable=True)

    @classmethod
    def from_device(cls, device: EAMDevice) -> EAMDeviceORM:
        """
        Create an ORM instance from an EAMDevice domain object.
        """
        return cls(
            equipmentno=device.equipmentno,
            serialnumber=device.serialnumber,
            position=device.position,
            equipmentdesc=device.equipmentdesc,
            eqclass=device.eqclass,
            manufacturer=device.manufacturer,
        )


class DBHelper:
    """
    Helper class for managing database operations using SQLAlchemy ORM.

    Converts domain device objects to ORM models and handles CRUD sync operations.
    """

    def __init__(self, connection_string: str) -> None:
        """
        Initialize DBHelper with a database connection string.

        Args:
            connection_string (str): PostgreSQL connection URL.
        """
        from sqlalchemy.engine import Engine

        self.engine: Engine = create_engine(connection_string, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session: sessionmaker[Session] = sessionmaker(bind=self.engine)

    def _get_all(
        self, orm_cls: type[Base], converter: Callable[[Any], Any], error_msg: str
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

        Returns:
            List[LanDBDevice]: List of domain LanDB devices.

        Raises:
            Exception: On query failure.
        """
        return self._get_all(
            LanDBDeviceORM, LanDBDevice.from_orm, "Error querying LanDB devices"
        )

    def get_all_eam_devices(self) -> list[EAMDevice]:
        """
        Retrieve all EAM devices from the database.

        Returns:
            List[EAMDevice]: List of domain EAM devices.

        Raises:
            Exception: On query failure.
        """
        return self._get_all(
            EAMDeviceORM, EAMDevice.from_orm, "Error querying EAM devices"
        )

    def _sync_devices(
        self,
        orm_cls: type[Base],
        from_device: Callable[[Any], Base],
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
                    orm_objs: list[Base] = [from_device(d) for d in to_insert]
                    session.add_all(orm_objs)

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
            to_insert,
            to_update,
            to_delete,
        )
