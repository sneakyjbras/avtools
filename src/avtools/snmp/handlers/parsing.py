"""Shared parsing helpers for SNMP device handlers.

These are small, pure functions used by the device-family handlers (PDU, codec,
matrix) to turn raw pysnmp values into Python scalars and to apply the unit
scaling documented in each vendor MIB.

They are intentionally dependency-free (no pysnmp imports) so they can be unit
tested in isolation and reused across handlers without import cost.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def as_int(value: Any) -> int | None:
    """Best-effort conversion of an SNMP value to ``int``.

    Args:
        value: Raw pysnmp value (or anything int-like / digit-bearing string).

    Returns:
        Parsed integer, or ``None`` if no integer could be extracted.
    """
    try:
        return int(value)
    except Exception:
        try:
            s = str(value)
            # Keep a leading sign if present, then digits.
            sign = -1 if s.strip().startswith("-") else 1
            digits = "".join(ch for ch in s if ch.isdigit())
            return sign * int(digits) if digits else None
        except Exception:
            return None


def as_float(value: Any) -> float | None:
    """Best-effort conversion of an SNMP value to ``float``.

    Args:
        value: Raw pysnmp value (or anything float-like / numeric string).

    Returns:
        Parsed float, or ``None`` if no number could be extracted.
    """
    try:
        return float(value)
    except Exception:
        try:
            s = str(value)
            kept = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
            return float(kept) if kept not in ("", "-", ".", "-.") else None
        except Exception:
            return None


def values_from_varbinds(var_binds: Sequence[Any]) -> list[Any]:
    """Extract the value side of each pysnmp var-bind.

    pysnmp var-binds are ``(ObjectName, ObjectValue)`` pairs. This returns the
    value (second element) for each, tolerating both tuple pairs and
    ``ObjectType``-style indexables.

    Args:
        var_binds: Sequence of var-binds as returned by a GET.

    Returns:
        List of raw value objects, one per var-bind.
    """
    out: list[Any] = []
    for vb in var_binds:
        try:
            out.append(vb[1])
        except Exception:
            out.append(None)
    return out


def ticks_to_seconds(ticks: int | None) -> float | None:
    """Convert SNMP TimeTicks (hundredths of a second) to seconds.

    Args:
        ticks: Raw TimeTicks integer, or ``None``.

    Returns:
        Seconds as ``float``, or ``None`` if ``ticks`` is ``None``.
    """
    if ticks is None:
        return None
    return float(ticks) / 100.0


def scaled(value: Any, divisor: float) -> float | None:
    """Parse ``value`` to float and divide by ``divisor`` (MIB unit scaling).

    Used to turn MIB-native integer units (e.g. hundredth-Amps, tenth-Volts,
    tenth-percent) into real SI units. Scaling is done here in Python so the
    published metric carries honest base units; Grafana then only handles
    presentation (decimals, unit suffix).

    Args:
        value: Raw SNMP value.
        divisor: Scale factor from the MIB (e.g. 100 for hundredth-Amps).

    Returns:
        Scaled float, or ``None`` if the value is not numeric.
    """
    n = as_float(value)
    if n is None:
        return None
    if divisor in (0, 0.0):
        return n
    return n / float(divisor)


def non_empty_str(value: Any) -> str | None:
    """Return a trimmed string, or ``None`` if empty/blank.

    Args:
        value: Raw SNMP value (often a DisplayString/OctetString).

    Returns:
        Trimmed string or ``None``.
    """
    if value is None:
        return None
    try:
        s = value.prettyPrint() if hasattr(value, "prettyPrint") else str(value)
    except Exception:
        s = str(value)
    s = str(s).strip()
    if not s:
        return None
    # Drop pysnmp exception-value markers (noSuchObject / noSuchInstance /
    # endOfMibView) so an unsupported OID yields None rather than a sentinel
    # string. These can appear per-varbind in an SNMPv2c GET.
    if s.startswith("No Such ") or s.startswith("No more variables"):
        return None
    return s
