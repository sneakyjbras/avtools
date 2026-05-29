"""Encode raw SNMP collector results into MetricSample objects.

Device-level label enrichment
------------------------------
The ``device_lookup`` argument accepted by every ``encode_*`` function is a
``dict[str, dict[str, str]]`` mapping ``equipment_no`` → pre-computed label
dict.  The caller (``SNMPObserverRouter``) builds this map from the
``CachedIPAddress`` objects loaded from Postgres so that the encoder remains
free of Postgres ORM imports.

When a device is present in the lookup the following fields are added to its
labels (any field that is ``None`` or empty is omitted):

    building   LanDB Device.location building code
    room       LanDB Device.location room code
    eq_class   EAM Equipment class code  (e.g. "PROJ")
    model      EAM Equipment model string
    category   EAM Equipment category code
    hostname   LanDB IPAddress DNS hostname

Labels policy
--------------
- ``equipmentno`` is always the first and mandatory label.
- Device metadata labels are optional; their absence never causes an error.
- Firmware and power_status are intentionally excluded from timeseries; they
  are routed to Postgres monitoring tables by ``SNMPObserverRouter``.
- Global deployment labels (Layer 2) are applied by ``OTLPMetricsPublisher``,
  not here.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from avtools.timeseries.models import MetricSample
from avtools.timeseries import metrics as m
from avtools.snmp.client import PingResult, ProbeResult, QueryResult

# Type alias for the device metadata lookup built by the orchestrator.
DeviceLookup = dict[str, dict[str, str]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _labels(
    equipmentno: str,
    extra: dict[str, str] | None = None,
) -> Mapping[str, str]:
    """Build the full Prometheus label set for one device.

    Args:
        equipmentno: Mandatory EAM primary key.
        extra:       Optional pre-computed device-metadata labels
                     (building, room, eq_class, model, category, hostname).

    Returns:
        Immutable label mapping for use in :class:`MetricSample`.
    """
    base: dict[str, str] = {"equipmentno": equipmentno}
    if extra:
        base.update(extra)
    return base


def to_float(value: object) -> float | None:
    """Best-effort conversion for numeric-ish values.

    Args:
        value: Raw value from an SNMP query result stat.

    Returns:
        Float if conversion succeeds, ``None`` otherwise.
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        try:
            s = str(value)
            digits = "".join(ch for ch in s if ch.isdigit() or ch == ".")
            return float(digits) if digits else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Per-stage encoders
# ---------------------------------------------------------------------------


def encode_ping(
    results: Iterable[PingResult],
    device_lookup: DeviceLookup | None = None,
) -> list[MetricSample]:
    """Encode raw ping results into MetricSample objects.

    Args:
        results:       Iterable of :class:`~avtools.snmp.client.PingResult`.
        device_lookup: Optional mapping from equipment_no to label dict,
                       used to enrich labels with EAM/LanDB metadata.

    Returns:
        List of :class:`MetricSample` (status + RTT when online).
    """
    dl = device_lookup or {}
    out: list[MetricSample] = []
    for r in results:
        if not r.equipmentno:
            continue
        lbl = _labels(r.equipmentno, dl.get(r.equipmentno))
        out.append(MetricSample(name=m.PING_CHECK_STATUS, value=r.up, labels=lbl))
        if r.rtt_ms is not None:
            out.append(MetricSample(name=m.PING_CHECK_RTT_MS, value=float(r.rtt_ms), labels=lbl))
    return out


def encode_probe(
    results: Iterable[ProbeResult],
    device_lookup: DeviceLookup | None = None,
) -> list[MetricSample]:
    """Encode raw SNMP probe results into MetricSample objects.

    Args:
        results:       Iterable of :class:`~avtools.snmp.client.ProbeResult`.
        device_lookup: Optional mapping from equipment_no to label dict.

    Returns:
        List of :class:`MetricSample` (one per probe result).
    """
    dl = device_lookup or {}
    out: list[MetricSample] = []
    for r in results:
        if not r.equipmentno:
            continue
        lbl = _labels(r.equipmentno, dl.get(r.equipmentno))
        out.append(MetricSample(name=m.SNMP_PROBE_STATUS, value=r.up, labels=lbl))
    return out


def encode_queries(
    results: Iterable[QueryResult],
    device_lookup: DeviceLookup | None = None,
) -> list[MetricSample]:
    """Encode raw SNMP query results into MetricSample objects.

    Policy:
        - Only *numeric* monitoring signals go to Prometheus.
        - Text / identity-like fields (firmware, power_status) are handled in
          Postgres monitoring and are intentionally excluded here.

    Args:
        results:       Iterable of :class:`~avtools.snmp.client.QueryResult`.
        device_lookup: Optional mapping from equipment_no to label dict.

    Returns:
        List of :class:`MetricSample` for numeric projector fields.
    """
    dl = device_lookup or {}
    out: list[MetricSample] = []
    for r in results:
        if not r.equipmentno:
            continue
        lbl = _labels(r.equipmentno, dl.get(r.equipmentno))

        if r.query == "projector":
            lamp = r.stats.get("lamp_hours")
            if lamp is not None:
                n = to_float(lamp)
                if n is not None:
                    out.append(MetricSample(name=m.PROJECTOR_QUERY_LAMP_HOURS, value=n, labels=lbl))

            uptime_s = r.stats.get("uptime_seconds")
            if uptime_s is not None:
                n = to_float(uptime_s)
                if n is not None:
                    out.append(
                        MetricSample(name=m.PROJECTOR_QUERY_UPTIME_SECONDS, value=n, labels=lbl)
                    )
            continue
        # Unknown query families: ignore to avoid leaking cardinality.
    return out


# ---------------------------------------------------------------------------
# Combined encoder
# ---------------------------------------------------------------------------


def encode_all(
    *,
    ping: Iterable[PingResult] = (),
    probe: Iterable[ProbeResult] = (),
    queries: Iterable[QueryResult] = (),
    device_lookup: DeviceLookup | None = None,
) -> list[MetricSample]:
    """Convenience combiner for the full SNMP pipeline.

    Args:
        ping:          Ping stage results.
        probe:         SNMP probe stage results.
        queries:       Routed SNMP query stage results.
        device_lookup: Optional device metadata map (equipment_no -> labels).

    Returns:
        Combined list of :class:`MetricSample` from all three stages.
    """
    samples: list[MetricSample] = []
    samples.extend(encode_ping(ping, device_lookup))
    samples.extend(encode_probe(probe, device_lookup))
    samples.extend(encode_queries(queries, device_lookup))
    return samples
