from __future__ import annotations

from avtools.snmp.handlers.codec import CiscoCodec


def _table():
    """Mocked ifTable: idx1 eth0 up, idx2 lo (loopback) up, idx3 eth1 down."""
    return {
        "1.3.6.1.2.1.2.2.1.8": [
            ("1.3.6.1.2.1.2.2.1.8.1", 1),
            ("1.3.6.1.2.1.2.2.1.8.2", 1),
            ("1.3.6.1.2.1.2.2.1.8.3", 2),
        ],
        "1.3.6.1.2.1.2.2.1.2": [
            ("1.3.6.1.2.1.2.2.1.2.1", "eth0"),
            ("1.3.6.1.2.1.2.2.1.2.2", "lo"),
            ("1.3.6.1.2.1.2.2.1.2.3", "eth1"),
        ],
        "1.3.6.1.2.1.2.2.1.3": [
            ("1.3.6.1.2.1.2.2.1.3.1", 6),
            ("1.3.6.1.2.1.2.2.1.3.2", 24),
            ("1.3.6.1.2.1.2.2.1.3.3", 6),
        ],
        "1.3.6.1.2.1.2.2.1.14": [("1.3.6.1.2.1.2.2.1.14.1", 0), ("1.3.6.1.2.1.2.2.1.14.3", 5)],
        "1.3.6.1.2.1.2.2.1.20": [("1.3.6.1.2.1.2.2.1.20.1", 0), ("1.3.6.1.2.1.2.2.1.20.3", 2)],
        "1.3.6.1.2.1.31.1.1.1.6": [
            ("1.3.6.1.2.1.31.1.1.1.6.1", 123456),
            ("1.3.6.1.2.1.31.1.1.1.6.3", 99),
        ],
        "1.3.6.1.2.1.31.1.1.1.10": [
            ("1.3.6.1.2.1.31.1.1.1.10.1", 654321),
            ("1.3.6.1.2.1.31.1.1.1.10.3", 88),
        ],
    }


def test_fetch_interfaces_parses_and_filters_loopback(monkeypatch):
    h = CiscoCodec("192.0.2.50")
    t = _table()
    monkeypatch.setattr(h, "_snmp_walk", lambda base, **k: t.get(base, []))
    ifaces = h.fetch_interfaces()
    assert len(ifaces) == 2  # loopback (idx 2) filtered out
    by_idx = {i["ifindex"]: i for i in ifaces}
    assert 2 not in by_idx
    assert by_idx[1]["oper_status"] == 1
    assert by_idx[1]["ifdescr"] == "eth0"
    assert by_idx[1]["in_octets"] == 123456
    assert by_idx[1]["out_octets"] == 654321
    assert by_idx[3]["oper_status"] == 2
    assert by_idx[3]["in_errors"] == 5
    assert by_idx[3]["out_errors"] == 2


def test_fetch_interfaces_without_counters(monkeypatch):
    h = CiscoCodec("192.0.2.50")
    t = _table()
    monkeypatch.setattr(h, "_snmp_walk", lambda base, **k: t.get(base, []))
    ifaces = h.fetch_interfaces(include_counters=False)
    assert all("in_octets" not in i for i in ifaces)
    assert all("oper_status" in i for i in ifaces)


def test_fetch_interfaces_empty_table(monkeypatch):
    h = CiscoCodec("192.0.2.50")
    monkeypatch.setattr(h, "_snmp_walk", lambda base, **k: [])
    assert h.fetch_interfaces() == []


def test_snmp_walk_stops_on_error(monkeypatch):
    h = CiscoCodec("192.0.2.50")

    def fake_nextcmd(*a, **k):
        yield (None, None, 0, [("1.3.6.1.2.1.2.2.1.8.1", 1)])
        yield ("timeout", None, 0, [])  # error -> walk should stop here
        yield (None, None, 0, [("1.3.6.1.2.1.2.2.1.8.2", 1)])  # never reached

    monkeypatch.setattr("avtools.snmp.handlers.abstract_device_handler.nextCmd", fake_nextcmd)
    rows = h._snmp_walk("1.3.6.1.2.1.2.2.1.8")
    assert rows == [("1.3.6.1.2.1.2.2.1.8.1", 1)]
