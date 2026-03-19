from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol


class RoutedDevice(Protocol):
    """Canonical routing input for SNMP query selection.

    Any object used for routing MUST expose these attributes.
    """

    eq_class: str | None
    category: str | None


@dataclass(frozen=True, slots=True)
class SNMPQuerySpec:
    """Declarative SNMP query description used for routing.

    A query spec is selected based on (eq_class, category) and then executed
    via a device handler.

    Notes:
        - Routing keys come ONLY from the canonical device object.
        - Selection is "first match wins".
        - Specs must be *sink-agnostic*: they only fetch raw data.
    """

    name: str
    match: Callable[[str | None, str | None], bool]
    fetch: Callable[[object], Mapping[str, object] | None]


def get_eqclass_category(device: RoutedDevice) -> tuple[str | None, str | None]:
    """Extract (eq_class, category) from the canonical routing device.

    No handler fallback. No attribute probing. Deterministic.

    Args:
        device: Canonical routing input exposing `eq_class` and `category`.

    Returns:
        Tuple (eq_class, category), both stripped and uppercased if present.
    """
    eq = device.eq_class.strip().upper() if device.eq_class else None
    cat = device.category.strip().upper() if device.category else None
    return eq, cat
