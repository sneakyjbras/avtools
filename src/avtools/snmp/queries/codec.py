from __future__ import annotations

from typing import Mapping

from .base import SNMPQuerySpec


def codec_query_spec() -> SNMPQuerySpec:
    """Video-codec SNMP query spec (eq_class ``AVV``).

    Matches on ``eq_class`` starting with ``AVV``. Category is not required
    (not reliably populated for this class). The handler is a codec handler
    exposing ``fetch_stats()`` (MIB-II uptime today).

    Sink-agnostic: returns the raw stats mapping only.
    """

    def match(eqclass: str | None, category: str | None) -> bool:
        cls = (eqclass or "").upper()
        return cls.startswith("AVV") and not cls.startswith("AVVS")

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

    return SNMPQuerySpec(name="codec", match=match, fetch=fetch)
