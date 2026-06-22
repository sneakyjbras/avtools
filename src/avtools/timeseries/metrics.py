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
# MIB-II interfaces (universal; Phase C) — labelled by ifindex
# ---------------------------------------------------------------------------
# ifOperStatus: 1=up, 2=down, 3=testing, 4=unknown, 5=dormant, 6=notPresent, 7=lowerLayerDown.
DEVICE_IF_OPER_STATUS = METRIC_PREFIX + "device_if_oper_status"
DEVICE_IF_IN_OCTETS = METRIC_PREFIX + "device_if_in_octets"
DEVICE_IF_OUT_OCTETS = METRIC_PREFIX + "device_if_out_octets"
DEVICE_IF_IN_ERRORS = METRIC_PREFIX + "device_if_in_errors"
DEVICE_IF_OUT_ERRORS = METRIC_PREFIX + "device_if_out_errors"
# Interface identity (ifdescr label) as an info metric.
DEVICE_IF_INFO = METRIC_PREFIX + "device_if_info"

# ---------------------------------------------------------------------------
# projector_query (SNMP)
# ---------------------------------------------------------------------------

PROJECTOR_QUERY_LAMP_HOURS = METRIC_PREFIX + "projector_query_lamp_hours"
PROJECTOR_QUERY_UPTIME_SECONDS = METRIC_PREFIX + "projector_query_uptime_seconds"

# Power status is exported as a numeric gauge (see SNMP handler for mapping).
PROJECTOR_QUERY_POWER_STATUS = METRIC_PREFIX + "projector_query_power_status"

# Firmware is a string; expose it via an info metric with a firmware label.
PROJECTOR_QUERY_FIRMWARE_INFO = METRIC_PREFIX + "projector_query_firmware_info"

# ---------------------------------------------------------------------------
# pdu_query (SNMP) — Server Technology rack PDUs (Sentry3 / Sentry4)
# ---------------------------------------------------------------------------

# Raw vendor input-feed status code (enum differs by MIB generation).
PDU_QUERY_INPUT_STATUS = METRIC_PREFIX + "pdu_query_input_status"
# Human-readable input-feed status label (from the MIB enum) as an info metric.
PDU_QUERY_STATUS_INFO = METRIC_PREFIX + "pdu_query_status_info"
# MIB-agnostic health flag (1 = input feed nominal, 0 = not nominal).
PDU_QUERY_HEALTHY = METRIC_PREFIX + "pdu_query_healthy"
# Real power on the input feed (Watts).
PDU_QUERY_ACTIVE_POWER_WATTS = METRIC_PREFIX + "pdu_query_active_power_watts"
# Measured line current (Amps; scaled from MIB hundredth-Amps).
PDU_QUERY_LINE_CURRENT_AMPS = METRIC_PREFIX + "pdu_query_line_current_amps"
# Line current as a percentage of capacity (%; Sentry4 direct, Sentry3 derived).
PDU_QUERY_CURRENT_UTILIZED_PCT = METRIC_PREFIX + "pdu_query_current_utilized_pct"
# Input / phase voltage (Volts; scaled from MIB tenth-Volts where applicable).
PDU_QUERY_VOLTAGE_VOLTS = METRIC_PREFIX + "pdu_query_voltage_volts"
# Uptime in seconds (from MIB-II sysUpTime).
PDU_QUERY_UPTIME_SECONDS = METRIC_PREFIX + "pdu_query_uptime_seconds"
# Firmware is a string; expose it via an info metric with a firmware label.
PDU_QUERY_FIRMWARE_INFO = METRIC_PREFIX + "pdu_query_firmware_info"

# --- Phase A: input-feed power quality -------------------------------------
# Accumulated energy (kWh; counter).
PDU_QUERY_ENERGY_KWH = METRIC_PREFIX + "pdu_query_energy_kwh"
# Line frequency (Hz).
PDU_QUERY_FREQUENCY_HZ = METRIC_PREFIX + "pdu_query_frequency_hz"
# Power factor (ratio 0-1).
PDU_QUERY_POWER_FACTOR = METRIC_PREFIX + "pdu_query_power_factor"
# Apparent power (VA).
PDU_QUERY_APPARENT_POWER_VA = METRIC_PREFIX + "pdu_query_apparent_power_va"
# Phase out-of-balance (%; 3-phase units).
PDU_QUERY_OUT_OF_BALANCE_PCT = METRIC_PREFIX + "pdu_query_out_of_balance_pct"

# --- Phase B: device-evaluated threshold statuses --------------------------
# Raw DeviceStatus code per dimension (for alerting != normal).
PDU_QUERY_ACTIVE_POWER_STATUS = METRIC_PREFIX + "pdu_query_active_power_status"
PDU_QUERY_POWER_FACTOR_STATUS = METRIC_PREFIX + "pdu_query_power_factor_status"
PDU_QUERY_BALANCE_STATUS = METRIC_PREFIX + "pdu_query_balance_status"
PDU_QUERY_LOAD_STATUS = METRIC_PREFIX + "pdu_query_load_status"
# Decoded label per dimension (info metric; status label).
PDU_QUERY_ACTIVE_POWER_STATUS_INFO = METRIC_PREFIX + "pdu_query_active_power_status_info"
PDU_QUERY_POWER_FACTOR_STATUS_INFO = METRIC_PREFIX + "pdu_query_power_factor_status_info"
PDU_QUERY_BALANCE_STATUS_INFO = METRIC_PREFIX + "pdu_query_balance_status_info"
PDU_QUERY_LOAD_STATUS_INFO = METRIC_PREFIX + "pdu_query_load_status_info"
# Consolidated power-quality severity (0 normal / 1 warning / 2 critical) for alerting.
PDU_QUERY_PQ_SEVERITY = METRIC_PREFIX + "pdu_query_pq_severity"

# --- Phase D: environmental sensors (temp/humidity), per sensor index ---------
PDU_QUERY_TEMPERATURE_C = METRIC_PREFIX + "pdu_query_temperature_c"
PDU_QUERY_HUMIDITY_PCT = METRIC_PREFIX + "pdu_query_humidity_pct"
# Sensor identity (name + kind labels) as an info metric.
PDU_QUERY_SENSOR_INFO = METRIC_PREFIX + "pdu_query_sensor_info"

# --- Phase E: per-outlet telemetry (labelled by stable outlet index) ----------
# Normalized outlet power state: 0=off, 1=on, 2=other (unknown/transitional).
PDU_QUERY_OUTLET_STATE = METRIC_PREFIX + "pdu_query_outlet_state"
PDU_QUERY_OUTLET_CURRENT_AMPS = METRIC_PREFIX + "pdu_query_outlet_current_amps"
PDU_QUERY_OUTLET_POWER_WATTS = METRIC_PREFIX + "pdu_query_outlet_power_watts"
PDU_QUERY_OUTLET_ENERGY_WH = METRIC_PREFIX + "pdu_query_outlet_energy_wh"
# Outlet identity (name label) as an info metric.
PDU_QUERY_OUTLET_INFO = METRIC_PREFIX + "pdu_query_outlet_info"

# ---------------------------------------------------------------------------
# codec_query (SNMP) — video-conferencing endpoints (MIB-II only)
# ---------------------------------------------------------------------------

CODEC_QUERY_UPTIME_SECONDS = METRIC_PREFIX + "codec_query_uptime_seconds"

# ---------------------------------------------------------------------------
# matrix_query (SNMP) — AV matrix switchers (MIB-II only for now)
# ---------------------------------------------------------------------------

MATRIX_QUERY_UPTIME_SECONDS = METRIC_PREFIX + "matrix_query_uptime_seconds"


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
    DEVICE_IF_OPER_STATUS: MetricMeta(
        "MIB-II interface operational status (1=up, 2=down, ...) per ifindex."
    ),
    DEVICE_IF_IN_OCTETS: MetricMeta("MIB-II interface inbound octets (64-bit counter).", unit="By"),
    DEVICE_IF_OUT_OCTETS: MetricMeta(
        "MIB-II interface outbound octets (64-bit counter).", unit="By"
    ),
    DEVICE_IF_IN_ERRORS: MetricMeta("MIB-II interface inbound errors (counter)."),
    DEVICE_IF_OUT_ERRORS: MetricMeta("MIB-II interface outbound errors (counter)."),
    DEVICE_IF_INFO: MetricMeta("MIB-II interface description (ifdescr label) as an info metric."),
    PROJECTOR_QUERY_LAMP_HOURS: MetricMeta("Projector lamp usage in hours.", unit="h"),
    PROJECTOR_QUERY_UPTIME_SECONDS: MetricMeta(
        "Projector uptime in seconds (from sysUpTime).", unit="s"
    ),
    PROJECTOR_QUERY_POWER_STATUS: MetricMeta("Projector power status (numeric code)."),
    PROJECTOR_QUERY_FIRMWARE_INFO: MetricMeta(
        "Projector firmware version (label) as an info metric."
    ),
    PDU_QUERY_INPUT_STATUS: MetricMeta(
        "PDU input-feed status (raw vendor code; enum differs by Sentry MIB generation)."
    ),
    PDU_QUERY_STATUS_INFO: MetricMeta(
        "PDU input-feed status label (from MIB enum) as an info metric."
    ),
    PDU_QUERY_HEALTHY: MetricMeta(
        "PDU input-feed health (1=nominal, 0=not nominal). MIB-agnostic."
    ),
    PDU_QUERY_ACTIVE_POWER_WATTS: MetricMeta("PDU input-feed active power.", unit="W"),
    PDU_QUERY_LINE_CURRENT_AMPS: MetricMeta("PDU line current.", unit="A"),
    PDU_QUERY_CURRENT_UTILIZED_PCT: MetricMeta(
        "PDU line current as a percentage of capacity.", unit="%"
    ),
    PDU_QUERY_VOLTAGE_VOLTS: MetricMeta("PDU input / phase voltage.", unit="V"),
    PDU_QUERY_UPTIME_SECONDS: MetricMeta("PDU uptime in seconds (from sysUpTime).", unit="s"),
    PDU_QUERY_FIRMWARE_INFO: MetricMeta("PDU firmware version (label) as an info metric."),
    PDU_QUERY_ENERGY_KWH: MetricMeta("PDU input-feed accumulated energy (counter).", unit="kWh"),
    PDU_QUERY_FREQUENCY_HZ: MetricMeta("PDU input-feed line frequency.", unit="Hz"),
    PDU_QUERY_POWER_FACTOR: MetricMeta("PDU input-feed power factor (0-1 ratio)."),
    PDU_QUERY_APPARENT_POWER_VA: MetricMeta("PDU input-feed apparent power.", unit="VA"),
    PDU_QUERY_OUT_OF_BALANCE_PCT: MetricMeta(
        "PDU input-feed phase current out-of-balance.", unit="%"
    ),
    PDU_QUERY_ACTIVE_POWER_STATUS: MetricMeta(
        "PDU active-power threshold status (DeviceStatus code)."
    ),
    PDU_QUERY_POWER_FACTOR_STATUS: MetricMeta(
        "PDU power-factor threshold status (DeviceStatus code)."
    ),
    PDU_QUERY_BALANCE_STATUS: MetricMeta(
        "PDU out-of-balance threshold status (DeviceStatus code)."
    ),
    PDU_QUERY_LOAD_STATUS: MetricMeta("PDU line-current/load threshold status code."),
    PDU_QUERY_ACTIVE_POWER_STATUS_INFO: MetricMeta("PDU active-power status label (info metric)."),
    PDU_QUERY_POWER_FACTOR_STATUS_INFO: MetricMeta("PDU power-factor status label (info metric)."),
    PDU_QUERY_BALANCE_STATUS_INFO: MetricMeta("PDU out-of-balance status label (info metric)."),
    PDU_QUERY_LOAD_STATUS_INFO: MetricMeta("PDU load status label (info metric)."),
    PDU_QUERY_PQ_SEVERITY: MetricMeta(
        "PDU power-quality severity across device thresholds (0 normal / 1 warning / 2 critical)."
    ),
    PDU_QUERY_TEMPERATURE_C: MetricMeta(
        "PDU environmental temperature sensor reading (normalized to Celsius).", unit="Cel"
    ),
    PDU_QUERY_HUMIDITY_PCT: MetricMeta("PDU environmental humidity sensor reading.", unit="%"),
    PDU_QUERY_SENSOR_INFO: MetricMeta("PDU environmental sensor identity (name/kind) info metric."),
    PDU_QUERY_OUTLET_STATE: MetricMeta("PDU outlet power state (0=off, 1=on, 2=other) per outlet."),
    PDU_QUERY_OUTLET_CURRENT_AMPS: MetricMeta("PDU outlet current draw.", unit="A"),
    PDU_QUERY_OUTLET_POWER_WATTS: MetricMeta("PDU outlet active power.", unit="W"),
    PDU_QUERY_OUTLET_ENERGY_WH: MetricMeta("PDU outlet accumulated energy (counter).", unit="W.h"),
    PDU_QUERY_OUTLET_INFO: MetricMeta("PDU outlet identity (name label) as an info metric."),
    CODEC_QUERY_UPTIME_SECONDS: MetricMeta(
        "Video codec uptime in seconds (from sysUpTime).", unit="s"
    ),
    MATRIX_QUERY_UPTIME_SECONDS: MetricMeta(
        "AV matrix switcher uptime in seconds (from sysUpTime).", unit="s"
    ),
}
