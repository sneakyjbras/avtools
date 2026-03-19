"""Metric naming + metadata for AV Tools (Prometheus).

Conventions:
  - All metrics are prefixed with `avtools_`.
  - Naming is aligned with the "logical tables" used by AV Tools:
      - ping_check
      - snmp_probe
      - projector_query
  - Labels: keep it minimal. We always include `equipmentno`.

String values:
  - Prometheus is numeric. To expose strings in Grafana, publish an `*_info`
    gauge with value=1 and the string as a label (e.g. firmware).

All OIDs and SNMP semantics are unchanged; only the sink moved from InfluxDB
to Prometheus via MONIT OTLP.
"""

from __future__ import annotations

from dataclasses import dataclass


METRIC_PREFIX = "avtools_"

# ---------------------------------------------------------------------------
# ping_check
# ---------------------------------------------------------------------------

# 1 = online, 0 = offline
PING_CHECK_STATUS = METRIC_PREFIX + "ping_check_status"

# RTT in milliseconds (only exported when online)
PING_CHECK_RTT_MS = METRIC_PREFIX + "ping_check_rtt_ms"

# ---------------------------------------------------------------------------
# snmp_probe
# ---------------------------------------------------------------------------

# 1 = SNMP reachable/enabled, 0 = SNMP not responding / not supported
SNMP_PROBE_STATUS = METRIC_PREFIX + "snmp_probe_status"

# ---------------------------------------------------------------------------
# projector_query (SNMP)
# ---------------------------------------------------------------------------

PROJECTOR_QUERY_LAMP_HOURS = METRIC_PREFIX + "projector_query_lamp_hours"
PROJECTOR_QUERY_UPTIME_SECONDS = METRIC_PREFIX + "projector_query_uptime_seconds"

# Power status is exported as a numeric gauge (see SNMP handler for mapping).
PROJECTOR_QUERY_POWER_STATUS = METRIC_PREFIX + "projector_query_power_status"

# Firmware is a string; expose it via an info metric with a firmware label.
PROJECTOR_QUERY_FIRMWARE_INFO = METRIC_PREFIX + "projector_query_firmware_info"


@dataclass(frozen=True, slots=True)
class MetricMeta:
    description: str
    unit: str | None = None


METRIC_META: dict[str, MetricMeta] = {
    PING_CHECK_STATUS: MetricMeta("Ping status (1=online, 0=offline)."),
    PING_CHECK_RTT_MS: MetricMeta("Ping round-trip time in milliseconds.", unit="ms"),
    SNMP_PROBE_STATUS: MetricMeta(
        "SNMP probe status (1=SNMP OK, 0=SNMP not responding / not supported)."
    ),
    PROJECTOR_QUERY_LAMP_HOURS: MetricMeta("Projector lamp usage in hours.", unit="h"),
    PROJECTOR_QUERY_UPTIME_SECONDS: MetricMeta(
        "Projector uptime in seconds (from sysUpTime).", unit="s"
    ),
    PROJECTOR_QUERY_POWER_STATUS: MetricMeta("Projector power status (numeric code)."),
    PROJECTOR_QUERY_FIRMWARE_INFO: MetricMeta(
        "Projector firmware version (label) as an info metric."
    ),
}
