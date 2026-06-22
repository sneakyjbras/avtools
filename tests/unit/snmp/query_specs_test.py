from __future__ import annotations

from avtools.snmp.queries import default_query_specs


class DummyProjectorHandler:
    def fetch_stats(self):
        return {"uptime_seconds": 1}


class DummyBadHandler:
    pass


def test_default_query_specs_contains_projector_spec():
    specs = default_query_specs()
    assert [s.name for s in specs] == ["projector", "pdu", "codec", "matrix"]


def test_projector_query_spec_match_policy():
    spec = default_query_specs()[0]

    assert spec.match("AVD123", "AV-PROJECTOR") is True
    assert spec.match("AVD", "something av-pro something") is True

    assert spec.match("SW", "AV-PROJECTOR") is False
    assert spec.match("AVD", "OTHER") is False


def test_projector_query_spec_fetch_contract():
    spec = default_query_specs()[0]

    out = spec.fetch(DummyProjectorHandler())
    assert out == {"uptime_seconds": 1}

    # Missing fetch_stats => None
    assert spec.fetch(DummyBadHandler()) is None
