from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

import avtools.core.av_tools as core
from avtools.core.av_tools import AVTools


class DummyLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []
        self.exception_messages: list[str] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.info_messages.append(str(msg))

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.error_messages.append(str(msg))

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.exception_messages.append(str(msg))


@dataclass
class DummyEAMDevice:
    equipment_no: str
    serial_number: str | None = None
    eq_class: str | None = None
    manufacturer: str | None = None


@dataclass
class DummyLanDBDevice:
    equipment_no: str
    ip: str | None = None
    serial_number: str | None = None


class DummyDBODHelper:
    def __init__(
        self,
        eam_devices: list[DummyEAMDevice],
        landb_devices: list[DummyLanDBDevice],
    ) -> None:
        # Stored copies; run_landb should not mutate these.
        self._eam_devices = list(eam_devices)
        self._landb_devices = list(landb_devices)
        self.calls: list[str] = []
        self.synced_payload: dict[str, Any] | None = None

    def get_all_eam_devices(self) -> list[DummyEAMDevice]:
        self.calls.append("get_all_eam_devices")
        # Return a fresh list each time so tests can detect mutation.
        return list(self._eam_devices)

    def get_all_landb_devices(self) -> list[DummyLanDBDevice]:
        self.calls.append("get_all_landb_devices")
        return list(self._landb_devices)

    def sync_landb_devices(
        self,
        *,
        to_insert: list[DummyLanDBDevice],
        to_update: list[tuple[DummyLanDBDevice, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        self.calls.append("sync_landb_devices")
        self.synced_payload = {
            "to_insert": list(to_insert),
            "to_update": list(to_update),
            "to_delete": list(to_delete),
        }


class DummyAuth:
    def __init__(self) -> None:
        self.token: Any = None


class DummyServiceAuthSession:
    def __init__(self, client_id: str, client_secret: str, audience: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.audience = audience
        self.auth = DummyAuth()
        self.get_calls: list[tuple[str, dict[str, Any] | None, bool]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, verify: bool = True):
        self.get_calls.append((url, params, verify))

        class DummyResp:
            ok = True
            status_code = 200

        return DummyResp()


def make_avtools_for_tests(
    eam_devices: list[DummyEAMDevice] | None = None,
    landb_devices: list[DummyLanDBDevice] | None = None,
) -> tuple[AVTools, DummyLogger, DummyDBODHelper]:
    av = object.__new__(AVTools)
    logger = DummyLogger()
    helper = DummyDBODHelper(
        eam_devices=eam_devices or [],
        landb_devices=landb_devices or [],
    )
    av.logger = logger
    av.dbod_helper = helper
    return av, logger, helper


# ---------------------------------------------------------------------------
# Existing tests
# ---------------------------------------------------------------------------


def test_run_landb_no_eam_devices(monkeypatch):
    av, logger, dbod = make_avtools_for_tests()

    ensure_called = False
    get_landb_called = False
    sync_called = False

    def fake_ensure_token(self, session):
        nonlocal ensure_called
        ensure_called = True

    async def fake_get_landb_devices(self, eam_records, session, max_workers: int):
        nonlocal get_landb_called
        get_landb_called = True
        return []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        nonlocal sync_called
        sync_called = True

    monkeypatch.setattr(core, "ServiceAuthSession", DummyServiceAuthSession)
    monkeypatch.setattr(core, "asyncio_run", lambda coro: asyncio.run(coro))

    av._ensure_token = fake_ensure_token.__get__(av, AVTools)
    av._get_landb_devices = fake_get_landb_devices.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(
        client_id="cid",
        client_secret="secret",
        audience="aud",
        max_workers=4,
    )

    # Only EAM lookup should be called, then early return.
    assert dbod.calls == ["get_all_eam_devices"]
    assert ensure_called is False
    assert get_landb_called is False
    assert sync_called is False
    assert any("no eam devices" in msg.lower() for msg in logger.info_messages)


def test_run_landb_normal_flow_calls_all_steps(monkeypatch):
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34"),
        DummyEAMDevice(equipment_no="EQ-38"),
    ]
    landb_cache = [
        DummyLanDBDevice(equipment_no="EQ-34", ip="10.0.0.34"),
        DummyLanDBDevice(equipment_no="EQ-99", ip="10.0.0.99"),
    ]

    av, logger, dbod = make_avtools_for_tests(
        eam_devices=eam_devices,
        landb_devices=landb_cache,
    )

    created_sessions: list[DummyServiceAuthSession] = []

    def make_session(client_id: str, client_secret: str, audience: str):
        sess = DummyServiceAuthSession(client_id, client_secret, audience)
        created_sessions.append(sess)
        return sess

    monkeypatch.setattr(core, "ServiceAuthSession", make_session)
    monkeypatch.setattr(core, "asyncio_run", lambda coro: asyncio.run(coro))

    ensure_calls: list[DummyServiceAuthSession] = []
    landb_args: list[tuple[list[DummyEAMDevice], DummyServiceAuthSession, int]] = []
    sync_calls: list[tuple] = []

    def fake_ensure_token(self, session):
        ensure_calls.append(session)

    async def fake_get_landb_devices(self, eam_records, session, max_workers: int):
        landb_args.append((list(eam_records), session, max_workers))
        return [
            DummyLanDBDevice(equipment_no="EQ-34", ip="10.0.0.34"),
            DummyLanDBDevice(equipment_no="EQ-38", ip="10.0.0.38"),
        ]

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        sync_calls.append(
            (list(api_items), list(cached_items), get_id, sync_func, name)
        )

    av._ensure_token = fake_ensure_token.__get__(av, AVTools)
    av._get_landb_devices = fake_get_landb_devices.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(
        client_id="CID",
        client_secret="CSECRET",
        audience="AUDIENCE",
        max_workers=5,
    )

    # DB helper call order
    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]

    # Session creation
    assert len(created_sessions) == 1
    sess = created_sessions[0]
    assert sess.client_id == "CID"
    assert sess.client_secret == "CSECRET"
    assert sess.audience == "AUDIENCE"

    # _ensure_token called once with that session
    assert ensure_calls == [sess]

    # _get_landb_devices called once with full EAM list and max_workers
    assert len(landb_args) == 1
    eam_arg, sess_arg, workers_arg = landb_args[0]
    assert eam_arg == eam_devices
    assert sess_arg is sess
    assert workers_arg == 5

    # _sync_entities called once with landb list + cache list and correct sync func
    assert len(sync_calls) == 1
    api_items_arg, cached_items_arg, get_id_arg, sync_func_arg, name_arg = sync_calls[0]

    assert [d.equipment_no for d in api_items_arg] == ["EQ-34", "EQ-38"]
    assert [d.equipment_no for d in cached_items_arg] == ["EQ-34", "EQ-99"]
    assert name_arg == "LanDB"
    assert sync_func_arg is dbod.sync_landb_devices
    # get_id lambda should use equipment_no
    assert get_id_arg(api_items_arg[0]) == api_items_arg[0].equipment_no

    # We don't expect specific info logs here apart from those in sub-calls
    assert logger.exception_messages == []


def test_run_landb_landb_list_empty_still_calls_sync(monkeypatch):
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34"),
        DummyEAMDevice(equipment_no="EQ-38"),
    ]
    landb_cache = [
        DummyLanDBDevice(equipment_no="EQ-34", ip="10.0.0.34"),
        DummyLanDBDevice(equipment_no="EQ-39", ip="10.0.0.39"),
    ]

    av, logger, dbod = make_avtools_for_tests(
        eam_devices=eam_devices,
        landb_devices=landb_cache,
    )

    monkeypatch.setattr(core, "ServiceAuthSession", DummyServiceAuthSession)
    monkeypatch.setattr(core, "asyncio_run", lambda coro: asyncio.run(coro))

    async def fake_get_landb_devices(self, eam_records, session, max_workers: int):
        return []

    sync_calls: list[tuple] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        sync_calls.append(
            (list(api_items), list(cached_items), get_id, sync_func, name)
        )

    av._ensure_token = (lambda self, session: None).__get__(av, AVTools)
    av._get_landb_devices = fake_get_landb_devices.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(
        client_id="CID",
        client_secret="CSECRET",
        audience="AUDIENCE",
        max_workers=2,
    )

    # EAM + LanDB cache lookups
    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]

    # sync_entities should still be called with empty API list and full cache list
    assert len(sync_calls) == 1
    api_items_arg, cached_items_arg, get_id_arg, sync_func_arg, name_arg = sync_calls[0]
    assert api_items_arg == []
    assert [d.equipment_no for d in cached_items_arg] == [
        d.equipment_no for d in landb_cache
    ]
    assert name_arg == "LanDB"


def test_run_landb_does_not_mutate_eam_device_list(monkeypatch):
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34"),
        DummyEAMDevice(equipment_no="EQ-38"),
        DummyEAMDevice(equipment_no="EQ-39"),
    ]
    landb_cache: list[DummyLanDBDevice] = []

    av, logger, dbod = make_avtools_for_tests(
        eam_devices=eam_devices,
        landb_devices=landb_cache,
    )

    monkeypatch.setattr(core, "ServiceAuthSession", DummyServiceAuthSession)
    monkeypatch.setattr(core, "asyncio_run", lambda coro: asyncio.run(coro))

    async def fake_get_landb_devices(self, eam_records, session, max_workers: int):
        # Simulate that we only return two of three devices from LanDB
        return [
            DummyLanDBDevice(equipment_no=e.equipment_no, ip=f"10.0.0.{i}")
            for i, e in enumerate(eam_records[:2], start=1)
        ]

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        # No-op
        return None

    av._ensure_token = (lambda self, session: None).__get__(av, AVTools)
    av._get_landb_devices = fake_get_landb_devices.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    # Snapshot original EAM list held by dbod helper
    original = list(dbod._eam_devices)

    av.run_landb(
        client_id="CID",
        client_secret="CSECRET",
        audience="AUDIENCE",
        max_workers=2,
    )

    # EAM list stored in dbod_helper must remain unchanged
    assert dbod._eam_devices == original
    # get_all_eam_devices + get_all_landb_devices called
    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert logger.exception_messages == []


def test_run_landb_ensure_token_called_before_landb_fetch(monkeypatch):
    eam_devices = [DummyEAMDevice(equipment_no="EQ-34")]
    landb_cache: list[DummyLanDBDevice] = []

    av, logger, dbod = make_avtools_for_tests(
        eam_devices=eam_devices,
        landb_devices=landb_cache,
    )

    created_sessions: list[DummyServiceAuthSession] = []

    def make_session(client_id: str, client_secret: str, audience: str):
        sess = DummyServiceAuthSession(client_id, client_secret, audience)
        created_sessions.append(sess)
        return sess

    monkeypatch.setattr(core, "ServiceAuthSession", make_session)
    monkeypatch.setattr(core, "asyncio_run", lambda coro: asyncio.run(coro))

    call_order: list[str] = []

    def fake_ensure_token(self, session):
        call_order.append("ensure_token")

    async def fake_get_landb_devices(self, eam_records, session, max_workers: int):
        call_order.append("get_landb_devices")
        return []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        call_order.append("sync_entities")

    av._ensure_token = fake_ensure_token.__get__(av, AVTools)
    av._get_landb_devices = fake_get_landb_devices.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(
        client_id="CID",
        client_secret="CSECRET",
        audience="AUDIENCE",
        max_workers=4,
    )

    # Ensure we called token-handling before the LanDB fetch and sync
    assert call_order[0] == "ensure_token"
    assert "get_landb_devices" in call_order
    assert "sync_entities" in call_order
    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]


# ---------------------------------------------------------------------------
# New tests: duplicates, unordered results, malformed data
# ---------------------------------------------------------------------------


def test_run_landb_duplicate_landb_device_ids_passed_to_sync(monkeypatch):
    """
    Duplicate device IDs in the LandB API list:
    - run_landb should pass them through to _sync_entities unchanged.
    - get_id should still resolve both to the same equipment_no.
    """
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34"),
        DummyEAMDevice(equipment_no="EQ-38"),
    ]
    landb_cache = [
        DummyLanDBDevice(equipment_no="EQ-34", ip="10.0.0.34"),
        DummyLanDBDevice(equipment_no="EQ-99", ip="10.0.0.99"),
    ]

    av, logger, dbod = make_avtools_for_tests(
        eam_devices=eam_devices,
        landb_devices=landb_cache,
    )

    monkeypatch.setattr(core, "ServiceAuthSession", DummyServiceAuthSession)
    monkeypatch.setattr(core, "asyncio_run", lambda coro: asyncio.run(coro))

    async def fake_get_landb_devices(self, eam_records, session, max_workers: int):
        # Two entries share the same equipment_no
        return [
            DummyLanDBDevice(equipment_no="EQ-34", ip="10.0.0.1"),
            DummyLanDBDevice(equipment_no="EQ-34", ip="10.0.0.2"),
            DummyLanDBDevice(equipment_no="EQ-38", ip="10.0.0.3"),
        ]

    sync_calls: list[tuple] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        sync_calls.append(
            (list(api_items), list(cached_items), get_id, sync_func, name)
        )

    av._ensure_token = (lambda self, session: None).__get__(av, AVTools)
    av._get_landb_devices = fake_get_landb_devices.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(
        client_id="CID",
        client_secret="CSECRET",
        audience="AUDIENCE",
        max_workers=3,
    )

    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert len(sync_calls) == 1

    api_items_arg, cached_items_arg, get_id_arg, sync_func_arg, name_arg = sync_calls[0]

    # Duplicates preserved and passed through
    assert [d.equipment_no for d in api_items_arg] == [
        "EQ-34",
        "EQ-34",
        "EQ-38",
    ]
    # get_id must map both duplicate entries to the same ID
    assert get_id_arg(api_items_arg[0]) == "EQ-34"
    assert get_id_arg(api_items_arg[1]) == "EQ-34"
    assert name_arg == "LanDB"
    assert logger.exception_messages == []


def test_run_landb_preserves_unordered_landb_results(monkeypatch):
    """
    Unordered LandB results:
    - run_landb must not reorder the list before passing it to _sync_entities.
    """
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34"),
        DummyEAMDevice(equipment_no="EQ-38"),
        DummyEAMDevice(equipment_no="EQ-39"),
    ]
    landb_cache = [
        DummyLanDBDevice(equipment_no="EQ-34", ip="10.0.0.34"),
    ]

    av, logger, dbod = make_avtools_for_tests(
        eam_devices=eam_devices,
        landb_devices=landb_cache,
    )

    monkeypatch.setattr(core, "ServiceAuthSession", DummyServiceAuthSession)
    monkeypatch.setattr(core, "asyncio_run", lambda coro: asyncio.run(coro))

    # Return devices in a deliberately odd order
    unordered_landb = [
        DummyLanDBDevice(equipment_no="EQ-39", ip="10.0.0.39"),
        DummyLanDBDevice(equipment_no="EQ-34", ip="10.0.0.34"),
        DummyLanDBDevice(equipment_no="EQ-38", ip="10.0.0.38"),
    ]

    async def fake_get_landb_devices(self, eam_records, session, max_workers: int):
        return list(unordered_landb)

    sync_calls: list[tuple] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        sync_calls.append(
            (list(api_items), list(cached_items), get_id, sync_func, name)
        )

    av._ensure_token = (lambda self, session: None).__get__(av, AVTools)
    av._get_landb_devices = fake_get_landb_devices.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(
        client_id="CID",
        client_secret="CSECRET",
        audience="AUDIENCE",
        max_workers=4,
    )

    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert len(sync_calls) == 1

    api_items_arg, cached_items_arg, get_id_arg, sync_func_arg, name_arg = sync_calls[0]

    # Order must be preserved exactly as returned by _get_landb_devices
    assert [d.equipment_no for d in api_items_arg] == [
        "EQ-39",
        "EQ-34",
        "EQ-38",
    ]
    assert name_arg == "LanDB"
    assert logger.exception_messages == []


def test_run_landb_handles_malformed_landb_cache_entries(monkeypatch):
    """
    Malformed data in the LandB cache (e.g. None IP, missing serial) must not
    cause run_landb to crash. Cached entries are forwarded as-is to _sync_entities.
    """
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34", serial_number=None),
        DummyEAMDevice(equipment_no="EQ-38", serial_number="SN-38"),
    ]
    # Cached devices with "malformed" fields: None IP, None serial
    landb_cache = [
        DummyLanDBDevice(equipment_no="EQ-34", ip=None, serial_number=None),
        DummyLanDBDevice(equipment_no="EQ-38", ip="10.0.0.38", serial_number=None),
    ]

    av, logger, dbod = make_avtools_for_tests(
        eam_devices=eam_devices,
        landb_devices=landb_cache,
    )

    monkeypatch.setattr(core, "ServiceAuthSession", DummyServiceAuthSession)
    monkeypatch.setattr(core, "asyncio_run", lambda coro: asyncio.run(coro))

    async def fake_get_landb_devices(self, eam_records, session, max_workers: int):
        # Valid devices coming from the API side
        return [
            DummyLanDBDevice(
                equipment_no="EQ-34", ip="10.0.0.34", serial_number="SN-34"
            ),
            DummyLanDBDevice(
                equipment_no="EQ-38", ip="10.0.0.38", serial_number="SN-38"
            ),
        ]

    sync_calls: list[tuple] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name):
        sync_calls.append(
            (list(api_items), list(cached_items), get_id, sync_func, name)
        )

    av._ensure_token = (lambda self, session: None).__get__(av, AVTools)
    av._get_landb_devices = fake_get_landb_devices.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(
        client_id="CID",
        client_secret="CSECRET",
        audience="AUDIENCE",
        max_workers=2,
    )

    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert len(sync_calls) == 1

    api_items_arg, cached_items_arg, get_id_arg, sync_func_arg, name_arg = sync_calls[0]

    # Malformed cache entries are forwarded as-is to _sync_entities
    assert [d.equipment_no for d in cached_items_arg] == ["EQ-34", "EQ-38"]
    # We explicitly keep the None IP on the first cached device
    assert cached_items_arg[0].ip is None
    assert cached_items_arg[1].ip == "10.0.0.38"
    assert name_arg == "LanDB"
    # No exceptions should have been logged
    assert logger.exception_messages == []
