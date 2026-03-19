from __future__ import annotations

from .base import SNMPQuerySpec
from .projector import projector_query_spec


def default_query_specs() -> list[SNMPQuerySpec]:
    """Default query specs enabled in the SNMP client.

    Add new device families here (e.g., matrix switchers) as more handlers are
    implemented.
    """

    return [
        projector_query_spec(),
    ]
