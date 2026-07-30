from __future__ import annotations

from dataclasses import dataclass
from typing import Any


from avtools.core.av_tools import AVTools

# -----------------------------------------------------------------------------
# Test doubles (logger / sanitizer / lightweight models)
# -----------------------------------------------------------------------------


class DummyLogger:
    """
    Minimal logger capturing strings.
    We don't assert structured kwargs here because run_landb mostly logs plain messages.
    """

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


class DummySanitizer:
    """
    Present so that if anything touches _sync_entities internals in future,
    the AVTools instance still looks like a real one.
    """

    def sanitize_text(self, v: Any) -> Any:
        return v

    def sanitize_dict_in_place(self, data: dict[str, Any], *, compare_fields=None) -> None:
        return


@dataclass
class DummyEAMDevice:
    """
    EAM device placeholder. run_landb just forwards the list to _get_landb_ipaddresses(),
    so we don't need to mirror the full Equipment model here.
    """

    equipment_no: str
    serial_number: str | None = None
    description: str | None = None


@dataclass
class DummyCachedIPAddress:
    """
    LanDB "API" return placeholder for _get_landb_ipaddresses().
    run_landb passes these into _sync_entities() and uses _landb_get_id:
      - prefers equipmentno
      - otherwise equipment_no
    """

    equipmentno: str
    ip: str | None = None
    serialnumber: str | None = None


@dataclass
class DummyCachedLanDBRow:
    """
    LanDB cached DB row placeholder returned by dbod_helper.get_all_landb_devices().
    """

    equipmentno: str
    ip: str | None = None
    serialnumber: str | None = None


# -----------------------------------------------------------------------------
# Dummy DB helper used by run_landb()
# -----------------------------------------------------------------------------


class DummyDBODHelper:
    """
    Captures calls to:
      - get_all_eam_devices
      - get_all_landb_devices
      - sync_landb_devices (NOT called directly by run_landb; run_landb uses _sync_entities)
    """

    def __init__(
        self,
        eam_devices: list[DummyEAMDevice],
        landb_cache: list[DummyCachedLanDBRow],
    ) -> None:
        # Store copies: run_landb should not mutate these containers.
        self._eam_devices = list(eam_devices)
        self._landb_cache = list(landb_cache)
        self.calls: list[str] = []
        self.synced_payload: dict[str, Any] | None = None

    def get_all_eam_devices(self) -> list[DummyEAMDevice]:
        self.calls.append("get_all_eam_devices")
        return list(self._eam_devices)

    def get_all_landb_devices(self) -> list[DummyCachedLanDBRow]:
        self.calls.append("get_all_landb_devices")
        return list(self._landb_cache)

    def sync_landb_devices(
        self,
        *,
        to_insert: list[Any],
        to_update: list[tuple[Any, dict[str, Any]]],
        to_delete: list[str],
    ) -> None:
        self.calls.append("sync_landb_devices")
        self.synced_payload = {
            "to_insert": list(to_insert),
            "to_update": list(to_update),
            "to_delete": list(to_delete),
        }


# -----------------------------------------------------------------------------
# Factory: create an AVTools instance without running __init__
# -----------------------------------------------------------------------------


def make_avtools_for_tests(
    eam_devices: list[DummyEAMDevice] | None = None,
    landb_cache: list[DummyCachedLanDBRow] | None = None,
) -> tuple[AVTools, DummyLogger, DummyDBODHelper]:
    """
    We bypass AVTools.__init__ to avoid touching PostgresClient / network credentials.
    Therefore we must manually provide attributes used by run_landb().
    """
    av = object.__new__(AVTools)
    logger = DummyLogger()
    helper = DummyDBODHelper(eam_devices=eam_devices or [], landb_cache=landb_cache or [])

    av.logger = logger
    av.dbod_helper = helper

    # New AVTools expects these to exist in various code paths.
    av.logs = False
    av._eam_sanitizer = DummySanitizer()
    av._landb_initialized = False

    return av, logger, helper


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


def test_run_landb_no_eam_devices(monkeypatch):
    """
    If the DB cache has no EAM devices, run_landb() should:
      - log and return early
      - NOT initialize the LanDB REST client
      - NOT fetch LanDB IPs
      - NOT sync anything
    """
    av, logger, dbod = make_avtools_for_tests()

    init_called = False
    fetch_called = False
    sync_called = False

    def fake_init_landb_rest_client(
        self, *, client_id: str, client_secret: str, audience: str, url: str
    ) -> None:
        nonlocal init_called
        init_called = True

    def fake_get_landb_ipaddresses(self, eam_records) -> list[DummyCachedIPAddress]:
        nonlocal fetch_called
        fetch_called = True
        return []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name, **kwargs):
        nonlocal sync_called
        sync_called = True

    av._init_landb_rest_client = fake_init_landb_rest_client.__get__(av, AVTools)
    av._get_landb_ipaddresses = fake_get_landb_ipaddresses.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(client_id="cid", client_secret="secret", audience="aud")

    assert dbod.calls == ["get_all_eam_devices"]
    assert init_called is False
    assert fetch_called is False
    assert sync_called is False
    assert any("skipping landb sync" in m.lower() for m in logger.info_messages)


def test_run_landb_normal_flow_calls_all_steps(monkeypatch):
    """
    Normal flow should:
      1) read EAM devices from DB
      2) init LanDB REST client
      3) fetch LanDB IPs for those EAM devices
      4) read cached LanDB rows from DB
      5) call _sync_entities with name='LanDB IPAddress'
    """
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34"),
        DummyEAMDevice(equipment_no="EQ-38"),
    ]
    landb_cache = [
        DummyCachedLanDBRow(equipmentno="EQ-34", ip="10.0.0.34"),
        DummyCachedLanDBRow(equipmentno="EQ-99", ip="10.0.0.99"),
    ]

    av, logger, dbod = make_avtools_for_tests(eam_devices=eam_devices, landb_cache=landb_cache)

    init_calls: list[dict[str, Any]] = []
    fetch_calls: list[list[DummyEAMDevice]] = []
    sync_calls: list[tuple] = []

    def fake_init_landb_rest_client(
        self, *, client_id: str, client_secret: str, audience: str, url: str
    ) -> None:
        init_calls.append(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "audience": audience,
                "url": url,
            }
        )

    def fake_get_landb_ipaddresses(self, eam_records) -> list[DummyCachedIPAddress]:
        # Capture the exact EAM list we were given
        fetch_calls.append(list(eam_records))
        return [
            DummyCachedIPAddress(equipmentno="EQ-34", ip="10.0.0.34"),
            DummyCachedIPAddress(equipmentno="EQ-38", ip="10.0.0.38"),
        ]

    sync_kwargs: list[dict[str, Any]] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name, **kwargs):
        sync_calls.append((list(api_items), list(cached_items), get_id, sync_func, name))
        sync_kwargs.append(dict(kwargs))

    av._init_landb_rest_client = fake_init_landb_rest_client.__get__(av, AVTools)
    av._get_landb_ipaddresses = fake_get_landb_ipaddresses.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    status = av.run_landb(
        client_id="CID",
        client_secret="CSECRET",
        audience="AUDIENCE",
        base_url="https://example-landb/api/",
    )

    # A healthy fetch reports success and reconciles normally (deletes enabled).
    assert status == "ok"
    assert sync_kwargs == [{"allow_deletes": True}]

    # DB calls happen in a predictable order.
    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]

    # init is called once with the base_url passed into run_landb
    assert init_calls == [
        {
            "client_id": "CID",
            "client_secret": "CSECRET",
            "audience": "AUDIENCE",
            "url": "https://example-landb/api/",
        }
    ]

    # fetch is called once with the full EAM list
    assert len(fetch_calls) == 1
    assert fetch_calls[0] == eam_devices

    # sync is called once and receives the LanDB list + cached rows list
    assert len(sync_calls) == 1
    api_items_arg, cached_items_arg, get_id_arg, sync_func_arg, name_arg = sync_calls[0]

    assert [d.equipmentno for d in api_items_arg] == ["EQ-34", "EQ-38"]
    assert [d.equipmentno for d in cached_items_arg] == ["EQ-34", "EQ-99"]
    assert name_arg == "LanDB IPAddress"

    # sync_func should be dbod_helper.sync_landb_devices (bound)
    assert getattr(sync_func_arg, "__self__", None) is dbod
    assert getattr(sync_func_arg, "__func__", None) is dbod.sync_landb_devices.__func__

    # get_id should behave like AVTools._landb_get_id (equipmentno preferred)
    assert get_id_arg(api_items_arg[0]) == "EQ-34"

    assert logger.exception_messages == []


def test_run_landb_landb_list_empty_still_calls_sync(monkeypatch):
    """
    If LanDB returns no IPs, we still:
      - load cached LanDB rows
      - call _sync_entities (which will likely delete everything, depending on cache)
    """
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34"),
        DummyEAMDevice(equipment_no="EQ-38"),
    ]
    landb_cache = [DummyCachedLanDBRow(equipmentno="EQ-34", ip="10.0.0.34")]

    av, logger, dbod = make_avtools_for_tests(eam_devices=eam_devices, landb_cache=landb_cache)

    av._init_landb_rest_client = (lambda self, **kw: None).__get__(av, AVTools)
    av._get_landb_ipaddresses = (lambda self, eam_records: []).__get__(av, AVTools)

    sync_calls: list[tuple] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name, **kwargs):
        sync_calls.append((list(api_items), list(cached_items), get_id, sync_func, name))

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(client_id="CID", client_secret="CSECRET", audience="AUDIENCE")

    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert len(sync_calls) == 1

    api_items_arg, cached_items_arg, _get_id, _sync_func, name_arg = sync_calls[0]
    assert api_items_arg == []
    assert [d.equipmentno for d in cached_items_arg] == ["EQ-34"]
    assert name_arg == "LanDB IPAddress"


def test_run_landb_does_not_mutate_eam_device_list(monkeypatch):
    """
    run_landb() must not mutate the underlying stored EAM device list in dbod_helper.
    """
    eam_devices = [
        DummyEAMDevice(equipment_no="EQ-34"),
        DummyEAMDevice(equipment_no="EQ-38"),
        DummyEAMDevice(equipment_no="EQ-39"),
    ]

    av, logger, dbod = make_avtools_for_tests(eam_devices=eam_devices, landb_cache=[])

    av._init_landb_rest_client = (lambda self, **kw: None).__get__(av, AVTools)

    def fake_get_landb_ipaddresses(self, eam_records) -> list[DummyCachedIPAddress]:
        # Read-only access
        _ = [e.equipment_no for e in eam_records]
        return [DummyCachedIPAddress(equipmentno="EQ-34", ip="10.0.0.34")]

    av._get_landb_ipaddresses = fake_get_landb_ipaddresses.__get__(av, AVTools)
    av._sync_entities = (lambda self, **kw: None).__get__(av, AVTools)

    original = list(dbod._eam_devices)
    av.run_landb(client_id="CID", client_secret="CSECRET", audience="AUDIENCE")

    assert dbod._eam_devices == original
    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert logger.exception_messages == []


def test_run_landb_init_called_before_landb_fetch(monkeypatch):
    """
    Ensure client initialization happens before _get_landb_ipaddresses().
    """
    eam_devices = [DummyEAMDevice(equipment_no="EQ-34")]
    av, logger, dbod = make_avtools_for_tests(eam_devices=eam_devices, landb_cache=[])

    call_order: list[str] = []

    def fake_init_landb_rest_client(self, **kwargs) -> None:
        call_order.append("init")

    def fake_get_landb_ipaddresses(self, eam_records) -> list[DummyCachedIPAddress]:
        call_order.append("fetch")
        return []

    def fake_sync_entities(self, **kwargs) -> None:
        call_order.append("sync")

    av._init_landb_rest_client = fake_init_landb_rest_client.__get__(av, AVTools)
    av._get_landb_ipaddresses = fake_get_landb_ipaddresses.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(client_id="CID", client_secret="CSECRET", audience="AUDIENCE")

    assert call_order[:1] == ["init"]
    assert "fetch" in call_order
    assert "sync" in call_order
    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]


def test_run_landb_duplicate_landb_ids_passed_to_sync(monkeypatch):
    """
    run_landb should not deduplicate API results; that is _sync_entities' job.
    We assert duplicates are passed through unchanged.
    """
    eam_devices = [DummyEAMDevice(equipment_no="EQ-34")]
    av, logger, dbod = make_avtools_for_tests(eam_devices=eam_devices, landb_cache=[])

    av._init_landb_rest_client = (lambda self, **kw: None).__get__(av, AVTools)

    def fake_get_landb_ipaddresses(self, eam_records) -> list[DummyCachedIPAddress]:
        return [
            DummyCachedIPAddress(equipmentno="EQ-34", ip="10.0.0.1"),
            DummyCachedIPAddress(equipmentno="EQ-34", ip="10.0.0.2"),
        ]

    sync_calls: list[tuple] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name, **kwargs):
        sync_calls.append((list(api_items), list(cached_items), get_id, sync_func, name))

    av._get_landb_ipaddresses = fake_get_landb_ipaddresses.__get__(av, AVTools)
    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(client_id="CID", client_secret="CSECRET", audience="AUDIENCE")

    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert len(sync_calls) == 1

    api_items_arg, _cached_items_arg, get_id_arg, _sync_func, name_arg = sync_calls[0]
    assert [d.equipmentno for d in api_items_arg] == ["EQ-34", "EQ-34"]
    assert get_id_arg(api_items_arg[0]) == "EQ-34"
    assert get_id_arg(api_items_arg[1]) == "EQ-34"
    assert name_arg == "LanDB IPAddress"


def test_run_landb_preserves_unordered_landb_results(monkeypatch):
    """
    run_landb should preserve the ordering returned by _get_landb_ipaddresses()
    (no sorting / reordering at the orchestration layer).
    """
    eam_devices = [DummyEAMDevice(equipment_no="EQ-34")]
    av, logger, dbod = make_avtools_for_tests(eam_devices=eam_devices, landb_cache=[])

    av._init_landb_rest_client = (lambda self, **kw: None).__get__(av, AVTools)

    unordered = [
        DummyCachedIPAddress(equipmentno="EQ-39", ip="10.0.0.39"),
        DummyCachedIPAddress(equipmentno="EQ-34", ip="10.0.0.34"),
        DummyCachedIPAddress(equipmentno="EQ-38", ip="10.0.0.38"),
    ]

    av._get_landb_ipaddresses = (lambda self, eam_records: list(unordered)).__get__(av, AVTools)

    sync_calls: list[tuple] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name, **kwargs):
        sync_calls.append((list(api_items), list(cached_items), get_id, sync_func, name))

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(client_id="CID", client_secret="CSECRET", audience="AUDIENCE")

    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert len(sync_calls) == 1

    api_items_arg, *_ = sync_calls[0]
    assert [d.equipmentno for d in api_items_arg] == ["EQ-39", "EQ-34", "EQ-38"]


def test_run_landb_handles_malformed_landb_cache_entries(monkeypatch):
    """
    Cache entries with missing fields (None) should not crash run_landb.
    They are just passed to _sync_entities as-is.
    """
    eam_devices = [DummyEAMDevice(equipment_no="EQ-34")]
    landb_cache = [
        DummyCachedLanDBRow(equipmentno="EQ-34", ip=None, serialnumber=None),
        DummyCachedLanDBRow(equipmentno="EQ-38", ip="10.0.0.38", serialnumber=None),
    ]

    av, logger, dbod = make_avtools_for_tests(eam_devices=eam_devices, landb_cache=landb_cache)

    av._init_landb_rest_client = (lambda self, **kw: None).__get__(av, AVTools)
    av._get_landb_ipaddresses = (
        lambda self, eam_records: [DummyCachedIPAddress(equipmentno="EQ-34", ip="10.0.0.34")]
    ).__get__(av, AVTools)

    sync_calls: list[tuple] = []

    def fake_sync_entities(self, api_items, cached_items, get_id, sync_func, name, **kwargs):
        sync_calls.append((list(api_items), list(cached_items), get_id, sync_func, name))

    av._sync_entities = fake_sync_entities.__get__(av, AVTools)

    av.run_landb(client_id="CID", client_secret="CSECRET", audience="AUDIENCE")

    assert dbod.calls == ["get_all_eam_devices", "get_all_landb_devices"]
    assert len(sync_calls) == 1

    _api_items_arg, cached_items_arg, _get_id, _sync_func, name_arg = sync_calls[0]
    assert [d.equipmentno for d in cached_items_arg] == ["EQ-34", "EQ-38"]
    assert cached_items_arg[0].ip is None
    assert cached_items_arg[1].ip == "10.0.0.38"
    assert name_arg == "LanDB IPAddress"
    assert logger.exception_messages == []
