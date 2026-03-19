from __future__ import annotations

from typing import Mapping

from .base import SNMPQuerySpec


def projector_query_spec() -> SNMPQuerySpec:
    """Projector SNMP query spec.

    Matches only devices that look like:
      - eqclass starts with "AVD"
      - category contains "PROJECTOR"

    The handler is expected to be a projector-like device handler exposing
    `fetch_stats()`.

    This spec is **sink-agnostic**: it only returns the raw stats mapping.
    """

    def match(eqclass: str | None, category: str | None) -> bool:
        cls = (eqclass or "").upper()
        cat = (category or "").upper()
        return cls.startswith("AVD") and ("AV-PRO" in cat)

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

    return SNMPQuerySpec(name="projector", match=match, fetch=fetch)
