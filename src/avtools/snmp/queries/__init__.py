"""SNMP query modules.

Each module defines one or more :class:`~avtools.snmp.queries.base.SNMPQuerySpec`
objects that:

- match devices by (eqclass, category)
- execute a handler method to fetch **raw** SNMP stats

The SNMP client routes devices to these specs. Sink-specific encoding (Prometheus
metric naming, Postgres schemas, etc.) is handled outside the SNMP package.
"""

from .base import SNMPQuerySpec, get_eqclass_category
from .registry import default_query_specs

__all__ = ["SNMPQuerySpec", "get_eqclass_category", "default_query_specs"]
