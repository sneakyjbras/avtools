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
from avtools.snmp.client import InterfaceResult, PingResult, ProbeResult, QueryResult

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

            # Power status: the Epson OID returns an integer power code. Publish
            # it numerically when it parses; the exact semantic mapping
            # (0=standby, 1=on, ...) can be remapped via Grafana value mappings.
            power = r.stats.get("power_status")
            if power is not None:
                n = to_float(power)
                if n is not None:
                    out.append(
                        MetricSample(name=m.PROJECTOR_QUERY_POWER_STATUS, value=n, labels=lbl)
                    )

            # Firmware (string) -> info gauge with the version as a label.
            firmware = r.stats.get("firmware")
            if firmware:
                fw_lbl = {**lbl, "firmware": str(firmware)}
                out.append(
                    MetricSample(name=m.PROJECTOR_QUERY_FIRMWARE_INFO, value=1, labels=fw_lbl)
                )
            continue

        if r.query == "pdu":
            for key, name in (
                ("input_status", m.PDU_QUERY_INPUT_STATUS),
                ("healthy", m.PDU_QUERY_HEALTHY),
                ("active_power_watts", m.PDU_QUERY_ACTIVE_POWER_WATTS),
                ("line_current_amps", m.PDU_QUERY_LINE_CURRENT_AMPS),
                ("current_utilized_pct", m.PDU_QUERY_CURRENT_UTILIZED_PCT),
                ("voltage_volts", m.PDU_QUERY_VOLTAGE_VOLTS),
                ("uptime_seconds", m.PDU_QUERY_UPTIME_SECONDS),
                ("energy_kwh", m.PDU_QUERY_ENERGY_KWH),
                ("frequency_hz", m.PDU_QUERY_FREQUENCY_HZ),
                ("power_factor", m.PDU_QUERY_POWER_FACTOR),
                ("apparent_power_va", m.PDU_QUERY_APPARENT_POWER_VA),
                ("out_of_balance_pct", m.PDU_QUERY_OUT_OF_BALANCE_PCT),
                ("active_power_status", m.PDU_QUERY_ACTIVE_POWER_STATUS),
                ("power_factor_status", m.PDU_QUERY_POWER_FACTOR_STATUS),
                ("balance_status", m.PDU_QUERY_BALANCE_STATUS),
                ("load_status", m.PDU_QUERY_LOAD_STATUS),
                ("pq_severity", m.PDU_QUERY_PQ_SEVERITY),
            ):
                val = r.stats.get(key)
                if val is not None:
                    n = to_float(val)
                    if n is not None:
                        out.append(MetricSample(name=name, value=n, labels=lbl))

            firmware = r.stats.get("firmware")
            if firmware:
                fw_lbl = {**lbl, "firmware": str(firmware)}
                out.append(MetricSample(name=m.PDU_QUERY_FIRMWARE_INFO, value=1, labels=fw_lbl))

            # MIB-derived status label -> info gauge (value=1, status label).
            status_text = r.stats.get("status_text")
            if status_text:
                st_lbl = {**lbl, "status": str(status_text)}
                out.append(MetricSample(name=m.PDU_QUERY_STATUS_INFO, value=1, labels=st_lbl))

            # Phase B: per-dimension device-evaluated status labels (info metrics).
            for txt_key, info_metric in (
                ("active_power_status_text", m.PDU_QUERY_ACTIVE_POWER_STATUS_INFO),
                ("power_factor_status_text", m.PDU_QUERY_POWER_FACTOR_STATUS_INFO),
                ("balance_status_text", m.PDU_QUERY_BALANCE_STATUS_INFO),
                ("load_status_text", m.PDU_QUERY_LOAD_STATUS_INFO),
            ):
                txt = r.stats.get(txt_key)
                if txt:
                    out.append(
                        MetricSample(name=info_metric, value=1, labels={**lbl, "status": str(txt)})
                    )

            # Phase D: environmental sensors (per sensor index).
            env = r.stats.get("environment")
            if isinstance(env, dict):
                for temp in env.get("temperature", []):
                    idx = temp.get("index")
                    if idx is None:
                        continue
                    slbl = {**lbl, "sensorindex": str(idx)}
                    celsius = temp.get("celsius")
                    if celsius is not None:
                        out.append(
                            MetricSample(name=m.PDU_QUERY_TEMPERATURE_C, value=celsius, labels=slbl)
                        )
                    name = temp.get("name")
                    if name:
                        out.append(
                            MetricSample(
                                name=m.PDU_QUERY_SENSOR_INFO,
                                value=1,
                                labels={**slbl, "sensor": str(name), "kind": "temperature"},
                            )
                        )
                for hum in env.get("humidity", []):
                    idx = hum.get("index")
                    if idx is None:
                        continue
                    slbl = {**lbl, "sensorindex": str(idx)}
                    percent = hum.get("percent")
                    if percent is not None:
                        out.append(
                            MetricSample(name=m.PDU_QUERY_HUMIDITY_PCT, value=percent, labels=slbl)
                        )
                    name = hum.get("name")
                    if name:
                        out.append(
                            MetricSample(
                                name=m.PDU_QUERY_SENSOR_INFO,
                                value=1,
                                labels={**slbl, "sensor": str(name), "kind": "humidity"},
                            )
                        )

            # Phase E: per-outlet telemetry (per outlet index).
            outlets = r.stats.get("outlets")
            if isinstance(outlets, list):
                for outlet in outlets:
                    idx = outlet.get("index")
                    if idx is None:
                        continue
                    olbl = {**lbl, "outlet": str(idx)}
                    for key, metric in (
                        ("state", m.PDU_QUERY_OUTLET_STATE),
                        ("current", m.PDU_QUERY_OUTLET_CURRENT_AMPS),
                        ("power", m.PDU_QUERY_OUTLET_POWER_WATTS),
                        ("energy", m.PDU_QUERY_OUTLET_ENERGY_WH),
                    ):
                        v = outlet.get(key)
                        if v is not None:
                            out.append(MetricSample(name=metric, value=v, labels=olbl))
                    name = outlet.get("name")
                    if name:
                        out.append(
                            MetricSample(
                                name=m.PDU_QUERY_OUTLET_INFO,
                                value=1,
                                labels={**olbl, "name": str(name)},
                            )
                        )
            continue

        if r.query == "codec":
            uptime_s = r.stats.get("uptime_seconds")
            if uptime_s is not None:
                n = to_float(uptime_s)
                if n is not None:
                    out.append(MetricSample(name=m.CODEC_QUERY_UPTIME_SECONDS, value=n, labels=lbl))
            continue

        if r.query == "matrix":
            uptime_s = r.stats.get("uptime_seconds")
            if uptime_s is not None:
                n = to_float(uptime_s)
                if n is not None:
                    out.append(
                        MetricSample(name=m.MATRIX_QUERY_UPTIME_SECONDS, value=n, labels=lbl)
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
    interfaces: Iterable[InterfaceResult] = (),
    device_lookup: DeviceLookup | None = None,
) -> list[MetricSample]:
    """Convenience combiner for the full SNMP pipeline.

    Args:
        ping:          Ping stage results.
        probe:         SNMP probe stage results.
        queries:       Routed SNMP query stage results.
        interfaces:    MIB-II interface stage results.
        device_lookup: Optional device metadata map (equipment_no -> labels).

    Returns:
        Combined list of :class:`MetricSample` from all stages.
    """
    samples: list[MetricSample] = []
    samples.extend(encode_ping(ping, device_lookup))
    samples.extend(encode_probe(probe, device_lookup))
    samples.extend(encode_queries(queries, device_lookup))
    samples.extend(encode_interfaces(interfaces, device_lookup))
    return samples


def encode_interfaces(
    results: Iterable[InterfaceResult],
    device_lookup: DeviceLookup | None = None,
) -> list[MetricSample]:
    """Encode MIB-II interface results into per-interface MetricSamples.

    Each interface contributes an oper-status gauge (and optional 64-bit octet /
    error counters), all labelled with a stable ``ifindex``. ``ifdescr`` rides on
    a separate info metric so renaming an interface never churns the numeric
    series.

    Args:
        results:       Iterable of :class:`~avtools.snmp.client.InterfaceResult`.
        device_lookup: Optional mapping from equipment_no to label dict.

    Returns:
        List of :class:`MetricSample` (per interface, per metric).
    """
    dl = device_lookup or {}
    out: list[MetricSample] = []
    for r in results:
        if not r.equipmentno:
            continue
        base = _labels(r.equipmentno, dl.get(r.equipmentno))
        for iface in r.interfaces:
            idx = iface.get("ifindex")
            if idx is None:
                continue
            lbl = {**base, "ifindex": str(idx)}
            oper = iface.get("oper_status")
            if oper is not None:
                out.append(MetricSample(name=m.DEVICE_IF_OPER_STATUS, value=oper, labels=lbl))
            for key, metric in (
                ("in_octets", m.DEVICE_IF_IN_OCTETS),
                ("out_octets", m.DEVICE_IF_OUT_OCTETS),
                ("in_errors", m.DEVICE_IF_IN_ERRORS),
                ("out_errors", m.DEVICE_IF_OUT_ERRORS),
            ):
                v = iface.get(key)
                if v is not None:
                    out.append(MetricSample(name=metric, value=v, labels=lbl))
            descr = iface.get("ifdescr")
            if descr:
                out.append(
                    MetricSample(
                        name=m.DEVICE_IF_INFO, value=1, labels={**lbl, "ifdescr": str(descr)}
                    )
                )
    return out
