from __future__ import annotations

from collections.abc import Iterable

from avtools.postgres.monitoring.models.projector import ProjectorMonitoring
from avtools.postgres.monitoring.models.sysdescr import DeviceSysDescrMonitoring
from avtools.snmp.client import ProbeResult, QueryResult


def projector_records_from_query_results(
    results: Iterable[QueryResult],
) -> list[ProjectorMonitoring]:
    """Extract projector monitoring records (text fields) from query results."""
    out: list[ProjectorMonitoring] = []
    for r in results:
        if r.query != "projector":
            continue
        if not r.equipmentno:
            continue
        rec = ProjectorMonitoring.from_query_stats(
            equipment_no=r.equipmentno,
            ip=r.ip,
            stats=dict(r.stats),
        )
        if rec is not None:
            out.append(rec)
    return out


def device_sysdescr_records_from_probe_results(
    results: Iterable[ProbeResult],
) -> list[DeviceSysDescrMonitoring]:
    """Extract device sysDescr monitoring records from probe results."""
    out: list[DeviceSysDescrMonitoring] = []
    for r in results:
        if not r.equipmentno:
            continue
        if r.up != 1:
            continue
        sysdescr = getattr(r, "sysdescr", None)
        if sysdescr in (None, ""):
            continue
        out.append(
            DeviceSysDescrMonitoring(
                equipment_no=r.equipmentno,
                ip=r.ip,
                eqclass=getattr(r, "eqclass", None),
                category=getattr(r, "category", None),
                sysdescr=str(sysdescr).strip(),
            )
        )
    return out
