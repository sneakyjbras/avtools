from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from avtools.core.av_tools import AVTools


class DummyLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.exception_messages: list[str] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.info_messages.append(str(msg))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exception_messages.append(str(msg))


@dataclass
class DummyDevice:
    ip: str


class DummyDBODHelper:
    def __init__(self, devices: list[DummyDevice]) -> None:
        self.devices = devices
        self.calls = 0

    def get_all_landb_devices(self) -> list[DummyDevice]:
        self.calls += 1
        # return a copy so the original list in the test is never mutated
        return list(self.devices)


def make_avtools_for_tests(
    devices: list[DummyDevice],
) -> tuple[AVTools, DummyLogger, DummyDBODHelper]:
    """Create an AVTools instance without running __init__."""
    av = object.__new__(AVTools)
    logger = DummyLogger()
    dbod_helper = DummyDBODHelper(devices=devices)
    av.logger = logger
    av.dbod_helper = dbod_helper
    return av, logger, dbod_helper


def test_run_influx_snmp_no_devices():
    """
    No LanDB devices:
    - get_all_landb_devices called once
    - _get_snmp_points and _publish_snmp are not called
    - log mentions skipping
    """
    av, logger, dbod = make_avtools_for_tests(devices=[])

    called_get_snmp_points = False
    called_publish_snmp = False

    async def fake_get_snmp_points(self, devices, max_workers: int):
        nonlocal called_get_snmp_points
        called_get_snmp_points = True
        return []

    def fake_publish_snmp(
        self,
        points,
        influx_host,
        influx_port,
        influx_user,
        influx_password,
        influx_db,
    ):
        nonlocal called_publish_snmp
        called_publish_snmp = True

    av._get_snmp_points = fake_get_snmp_points.__get__(av, AVTools)
    av._publish_snmp = fake_publish_snmp.__get__(av, AVTools)

    av.run_influx_snmp(
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
        max_workers=4,
    )

    assert dbod.calls == 1
    assert called_get_snmp_points is False
    assert called_publish_snmp is False
    assert any("no landb devices" in msg.lower() for msg in logger.info_messages)


def test_run_influx_snmp_devices_but_no_points():
    """
    Devices exist, but _get_snmp_points returns []:
    - _get_snmp_points called once with devices + max_workers
    - _publish_snmp NOT called
    - log mentions no points
    """
    devices = [DummyDevice(ip="10.0.0.34"), DummyDevice(ip="10.0.0.38")]
    av, logger, dbod = make_avtools_for_tests(devices=devices)

    captured_args: list[tuple[list[DummyDevice], int]] = []
    called_publish_snmp = False

    async def fake_get_snmp_points(self, devices_arg, max_workers: int):
        captured_args.append((devices_arg, max_workers))
        return []

    def fake_publish_snmp(
        self,
        points,
        influx_host,
        influx_port,
        influx_user,
        influx_password,
        influx_db,
    ):
        nonlocal called_publish_snmp
        called_publish_snmp = True

    av._get_snmp_points = fake_get_snmp_points.__get__(av, AVTools)
    av._publish_snmp = fake_publish_snmp.__get__(av, AVTools)

    av.run_influx_snmp(
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
        max_workers=3,
    )

    assert dbod.calls == 1
    # _get_snmp_points called once with all devices
    assert len(captured_args) == 1
    devices_arg, workers_arg = captured_args[0]
    assert devices_arg == devices
    assert workers_arg == 3

    # no publish
    assert called_publish_snmp is False
    assert any(
        "no snmp points" in msg.lower() or "no points" in msg.lower()
        for msg in logger.info_messages
    )


def test_run_influx_snmp_normal_flow_calls_publish_snmp_once():
    """
    Normal flow:
    - Devices exist
    - _get_snmp_points returns points
    - _publish_snmp called exactly once with those points + config
    """
    devices = [
        DummyDevice(ip="10.0.0.34"),
        DummyDevice(ip="10.0.0.38"),
        DummyDevice(ip="10.0.0.39"),
    ]
    av, logger, dbod = make_avtools_for_tests(devices=devices)

    captured_get_args: list[tuple[list[DummyDevice], int]] = []
    captured_publish_args: list[
        tuple[list[dict[str, Any]], str, int, str, str, str]
    ] = []

    async def fake_get_snmp_points(self, devices_arg, max_workers: int):
        captured_get_args.append((devices_arg, max_workers))
        # one point per device
        return [
            {
                "measurement": "ping",
                "tags": {"ip": d.ip},
                "fields": {"status": 1},
            }
            for d in devices_arg
        ]

    def fake_publish_snmp(
        self,
        points,
        influx_host,
        influx_port,
        influx_user,
        influx_password,
        influx_db,
    ):
        captured_publish_args.append(
            (points, influx_host, influx_port, influx_user, influx_password, influx_db)
        )

    av._get_snmp_points = fake_get_snmp_points.__get__(av, AVTools)
    av._publish_snmp = fake_publish_snmp.__get__(av, AVTools)

    av.run_influx_snmp(
        influx_host="influx-prod.cern.ch",
        influx_port=9092,
        influx_user="snmp_user",
        influx_password="secret",
        influx_db="avtools_snmp",
        max_workers=5,
    )

    assert dbod.calls == 1

    # _get_snmp_points called once with all devices and correct max_workers
    assert len(captured_get_args) == 1
    devices_arg, workers_arg = captured_get_args[0]
    assert devices_arg == devices
    assert workers_arg == 5

    # _publish_snmp called once with returned points and correct config
    assert len(captured_publish_args) == 1
    points_arg, host_arg, port_arg, user_arg, password_arg, db_arg = (
        captured_publish_args[0]
    )
    assert len(points_arg) == len(devices)
    assert host_arg == "influx-prod.cern.ch"
    assert port_arg == 9092
    assert user_arg == "snmp_user"
    assert password_arg == "secret"
    assert db_arg == "avtools_snmp"


def test_run_influx_snmp_snmp_collection_exception_is_caught():
    """
    _get_snmp_points raises:
    - run_influx_snmp should catch and log
    - _publish_snmp should not be called
    """
    devices = [DummyDevice(ip="10.0.0.34")]
    av, logger, dbod = make_avtools_for_tests(devices=devices)

    called_publish_snmp = False

    async def fake_get_snmp_points(self, devices_arg, max_workers: int):
        raise RuntimeError("SNMP failure")

    def fake_publish_snmp(
        self,
        points,
        influx_host,
        influx_port,
        influx_user,
        influx_password,
        influx_db,
    ):
        nonlocal called_publish_snmp
        called_publish_snmp = True

    av._get_snmp_points = fake_get_snmp_points.__get__(av, AVTools)
    av._publish_snmp = fake_publish_snmp.__get__(av, AVTools)

    av.run_influx_snmp(
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
        max_workers=2,
    )

    assert dbod.calls == 1
    assert called_publish_snmp is False
    assert any(
        "snmp" in msg.lower() or "failed" in msg.lower()
        for msg in logger.exception_messages
    )


def test_run_influx_snmp_publish_exception_is_caught():
    """
    _publish_snmp raises:
    - run_influx_snmp should catch/log and not propagate
    """
    devices = [DummyDevice(ip="10.0.0.34")]
    av, logger, dbod = make_avtools_for_tests(devices=devices)

    async def fake_get_snmp_points(self, devices_arg, max_workers: int):
        return [
            {
                "measurement": "ping",
                "tags": {"ip": d.ip},
                "fields": {"status": 1},
            }
            for d in devices_arg
        ]

    def fake_publish_snmp(
        self,
        points,
        influx_host,
        influx_port,
        influx_user,
        influx_password,
        influx_db,
    ):
        raise RuntimeError("Influx write failure")

    av._get_snmp_points = fake_get_snmp_points.__get__(av, AVTools)
    av._publish_snmp = fake_publish_snmp.__get__(av, AVTools)

    av.run_influx_snmp(
        influx_host="influx.local",
        influx_port=8086,
        influx_user="user",
        influx_password="pass",
        influx_db="avtools",
        max_workers=2,
    )

    assert dbod.calls == 1
    assert any(
        "influx" in msg.lower() or "failed" in msg.lower()
        for msg in logger.exception_messages
    )
