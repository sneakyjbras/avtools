from __future__ import annotations

from avtools.snmp.queries.projector import projector_query_spec


class _Handler:
    def __init__(self, out):
        self._out = out

    def fetch_stats(self):
        return self._out


def test_projector_query_spec_match_rules():
    spec = projector_query_spec()
    assert spec.match("AVD", "AV-PRO") is True
    assert spec.match("avd-something", "xx av-pro yy") is True
    assert spec.match("OTHER", "AV-PRO") is False
    assert spec.match("AVD", "PROJECTOR") is False


def test_projector_query_spec_fetch_converts_dict_like():
    spec = projector_query_spec()

    # Mapping is returned as-is.
    out = spec.fetch(_Handler({"k": 1}))
    assert out == {"k": 1}

    # Non-mapping but dict()-convertible.
    out = spec.fetch(_Handler([("a", 1), ("b", 2)]))
    assert out == {"a": 1, "b": 2}

    # Non-callable handler method yields None.
    class NoFn:
        fetch_stats = 123

    assert spec.fetch(NoFn()) is None
