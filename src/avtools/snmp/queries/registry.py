from __future__ import annotations

from .base import SNMPQuerySpec
from .codec import codec_query_spec
from .matrix import matrix_query_spec
from .pdu import pdu_query_spec
from .projector import projector_query_spec


def default_query_specs() -> list[SNMPQuerySpec]:
    """Default query specs enabled in the SNMP client.

    Specs are evaluated first-match-wins per device; they are kept mutually
    exclusive by eq_class so order is not load-bearing. Add new device families
    here as more handlers are implemented.
    """

    return [
        projector_query_spec(),
        pdu_query_spec(),
        codec_query_spec(),
        matrix_query_spec(),
    ]
