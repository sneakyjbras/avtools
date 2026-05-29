from __future__ import annotations

from dataclasses import dataclass

import pytest

import avtools.postgres.inventory.orm.landb_ipaddress as mod
from avtools.postgres.inventory.orm.landb_ipaddress import (
    CachedIPAddress,
    LanDBIPAddressORM,
    LanDBIPAddressORMError,
)


@dataclass
class DummyEquipment:
    code: str
    serial_number: str | None = None
    description: str | None = None
    class_code: str | None = None
    class_desc: str | None = None
    manufacturer_code: str | None = None
    manufacturer_desc: str | None = None
    model: str | None = None


@dataclass
class DummyIPAddress:
    ipv4: str | None = None
    ipv6: str | None = None
    name: str | None = None


@dataclass
class DummyLocation:
    building: str | None = None
    floor: str | None = None
    room: str | None = None


@dataclass
class DummyDevice:
    serial_number: str | None = None
    name: str | None = None
    location: object | None = None


def test_module_parse_location_accepts_dict_object_and_list() -> None:
    assert mod._parse_location({"building": "B", "floor": "F", "room": "R"}) == (
        "B",
        "F",
        "R",
    )
    assert mod._parse_location(DummyLocation(building="B", floor="F", room="R")) == (
        "B",
        "F",
        "R",
    )
    assert mod._parse_location(["B", "F", "R"]) == ("B", "F", "R")


def test_cached_ip_from_equipment_and_ipaddress_prefers_landb_name_and_selects_ipv4() -> None:
    eq = DummyEquipment(
        code="EQ-1",
        serial_number="EAM-SN",
        description="EAM DESC",
        class_code="AVD",
        manufacturer_desc="Epson",
        model="P1",
    )
    ip = DummyIPAddress(ipv4="192.0.2.1", ipv6=None, name="host.example")
    dev = DummyDevice(
        serial_number="LANDB-SN",
        name="LanDB Name",
        location={"building": "B", "floor": "1", "room": "101"},
    )

    cached = CachedIPAddress.from_equipment_and_ipaddress(eq, ip, landb_device=dev)  # type: ignore[arg-type]

    assert cached.equipment_no == "EQ-1"
    assert cached.serial_number == "EAM-SN"
    assert cached.landb_serial == "LANDB-SN"
    assert cached.landb_description == "LanDB Name"
    assert cached.hostname == "host.example"

    # display name preference: LanDB device name first
    assert cached.name == "LanDB Name"

    # IP preference: ipv4 first
    assert cached.ip == "192.0.2.1"

    assert cached.building == "B"
    assert cached.floor == "1"
    assert cached.room == "101"
    assert cached.eq_class == "AVD"
    assert cached.manufacturer == "Epson"
    assert cached.model == "P1"


def test_from_ipaddress_requires_equipment_no() -> None:
    cached = CachedIPAddress(
        equipment_no=None,
        serial_number=None,
        ip=None,
        name=None,
        hostname=None,
        landb_serial=None,
        landb_description=None,
        building=None,
        floor=None,
        room=None,
        eq_class=None,
        manufacturer=None,
        model=None,
    )

    with pytest.raises(LanDBIPAddressORMError):
        LanDBIPAddressORM.from_ipaddress(cached)


def test_to_ipaddress_sets_compare_fields() -> None:
    row = LanDBIPAddressORM(
        equipment_no="EQ-1",
        serial_number="SN",
        ip="192.0.2.1",
        name="n",
        hostname="h",
        landb_serial="lsn",
        landb_description="ld",
        building="B",
        floor="1",
        room="101",
        eq_class="AVD",
        manufacturer="Epson",
        model="P1",
    )

    cached = row.to_ipaddress()
    assert cached.equipment_no == "EQ-1"

    fields = cached.avtools_compare_fields()
    # A couple of representative fields
    assert "equipment_no" in fields
    assert "ip" in fields
    assert "landb_description" in fields
