from __future__ import annotations

from avtools.snmp.queries.codec import codec_query_spec
from avtools.snmp.queries.matrix import matrix_query_spec
from avtools.snmp.queries.pdu import pdu_query_spec
from avtools.snmp.queries.registry import default_query_specs


class _Handler:
    def __init__(self, out):
        self._out = out

    def fetch_stats(self):
        return self._out


def test_pdu_spec_matches_only_avs():
    spec = pdu_query_spec()
    assert spec.name == "pdu"
    assert spec.match("AVS", None) is True
    assert spec.match("avs-123", "anything") is True
    assert spec.match("AVD", "AV-PRO") is False
    assert spec.match("AVV", None) is False
    assert spec.match("AVA", None) is False
    assert spec.match("AVVS", None) is False


def test_codec_spec_matches_avv_but_not_avvs():
    spec = codec_query_spec()
    assert spec.name == "codec"
    assert spec.match("AVV", None) is True
    assert spec.match("avv-9", None) is True
    # critical: AVVS startswith AVV, must be excluded
    assert spec.match("AVVS", None) is False
    assert spec.match("AVA", None) is False
    assert spec.match("AVS", None) is False


def test_matrix_spec_matches_ava_and_avvs():
    spec = matrix_query_spec()
    assert spec.name == "matrix"
    assert spec.match("AVA", None) is True
    assert spec.match("AVVS", None) is True
    assert spec.match("AVV", None) is False
    assert spec.match("AVS", None) is False


def test_specs_are_mutually_exclusive_per_class():
    """Each eq_class must match at most one default spec."""
    specs = default_query_specs()
    for cls, cat in [
        ("AVD", "AV-PRO"),  # projector
        ("AVS", None),  # pdu
        ("AVV", None),  # codec
        ("AVA", None),  # matrix
        ("AVVS", None),  # matrix
    ]:
        matches = [s.name for s in specs if s.match(cls, cat)]
        assert len(matches) == 1, f"{cls} matched {matches}"


def test_fetch_contracts():
    for spec in (pdu_query_spec(), codec_query_spec(), matrix_query_spec()):
        assert spec.fetch(_Handler({"uptime_seconds": 1})) == {"uptime_seconds": 1}
        assert spec.fetch(_Handler(None)) is None

        class NoFn:
            fetch_stats = 5

        assert spec.fetch(NoFn()) is None
