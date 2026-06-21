from __future__ import annotations

from typing import Mapping

from .base import SNMPQuerySpec


def matrix_query_spec() -> SNMPQuerySpec:
    """AV matrix-switcher SNMP query spec (eq_class ``AVA`` / ``AVVS``).

    Matches on ``eq_class`` starting with ``AVA`` or ``AVVS``. Category is not
    required (not reliably populated for these classes). The handler is a matrix
    handler exposing ``fetch_stats()`` (MIB-II uptime today).

    Sink-agnostic: returns the raw stats mapping only.
    """

    def match(eqclass: str | None, category: str | None) -> bool:
        cls = (eqclass or "").upper()
        return cls.startswith("AVA") or cls.startswith("AVVS")

    def fetch(handler: object) -> Mapping[str, object] | None:
        fn = getattr(handler, "fetch_stats", None)
        if not callable(fn):
            return None
        out = fn()
        if out is None:
            return None
        if isinstance(out, Mapping):
            return out  # type: ignore[return-value]
        try:
            return dict(out)  # type: ignore[arg-type]
        except Exception:
            return None

    return SNMPQuerySpec(name="matrix", match=match, fetch=fetch)
