from __future__ import annotations

from typing import Mapping

from .base import SNMPQuerySpec


def pdu_query_spec() -> SNMPQuerySpec:
    """Server Technology PDU SNMP query spec (eq_class ``AVS``).

    Matches on ``eq_class`` starting with ``AVS``. Category is intentionally
    not required: unlike projectors, AVS devices do not carry a reliable
    category code in the inventory, so eq_class is the dependable routing key.
    The factory has already narrowed the handler to a Sentry3/Sentry4 PDU.

    Sink-agnostic: returns the raw normalised stats mapping only.
    """

    def match(eqclass: str | None, category: str | None) -> bool:
        cls = (eqclass or "").upper()
        return cls.startswith("AVS")

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

    return SNMPQuerySpec(name="pdu", match=match, fetch=fetch)
