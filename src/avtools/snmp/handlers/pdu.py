"""SNMP handlers for Server Technology rack PDUs.

The CERN AV fleet runs two Sentry generations, which expose **different** MIBs:

- **PRO2** units (model ``CW-*``)  -> Sentry4-MIB  (enterprise ``1.3.6.1.4.1.1718.4``)
- **CDU** units  (model ``AA-*``)  -> Sentry3-MIB  (enterprise ``1.3.6.1.4.1.1718.3``)

Both subclasses normalise their readings to the **same output keys** so the
encoder, dashboards and alerts stay MIB-agnostic:

    input_status          raw vendor status code (int)
    status_text           human-readable status label (from the MIB enum)
    healthy               1 if input_status is the nominal value for that MIB, else 0
    active_power_watts    real power on the input feed (W)
    line_current_amps     measured current on the line (A)
    current_utilized_pct  line current as a percentage of capacity (%)
    voltage_volts         input / phase voltage (V)
    uptime_seconds        MIB-II sysUpTime (s)
    firmware              firmware version string (text)

**Two modes.** Each field is declared with a MIB symbol *and* a hardcoded
fallback OID/scale. When the corresponding MIB has been compiled (see
``scripts/compile_mibs.py``), the OID, unit scaling and status enum come from the
MIB via :class:`~avtools.snmp.mibs.MibResolver`; otherwise the hardcoded values
are used. This keeps the exporter correct even if a future firmware changes a
scaling factor (the MIB drives it) while never failing if a MIB is missing.

Index suffixes ``(1, 1)`` / ``(1, 1, 1)`` address the first unit / input cord /
line, which is correct for these single-feed rack PDUs.
"""

from __future__ import annotations

from abc import ABC
from typing import NamedTuple

from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler
from avtools.snmp.handlers.parsing import (
    as_float,
    as_int,
    non_empty_str,
    ticks_to_seconds,
    values_from_varbinds,
)
from avtools.snmp.mibs import get_resolver

# MIB-II sysUpTime.0 (TimeTicks) — present on all SNMP agents.
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

# Sentry4 DeviceStatus code classes for power-quality severity (Phase B).
# Threshold breaches only; comms/sensor states (noComm, lost, ...) are not PQ alarms.
DEVICE_STATUS_WARN = frozenset({15, 16})  # lowWarning, highWarning
DEVICE_STATUS_CRIT = frozenset(
    {14, 17, 18, 19, 20}
)  # lowAlarm, highAlarm, alarm, underLimit, overLimit


class Field(NamedTuple):
    """Declarative spec for one PDU reading (MIB symbol + hardcoded fallback)."""

    key: str  # output dict key
    symbol: str  # MIB object symbol
    indices: tuple[int, ...]  # table/instance index suffix
    fallback_oid: str  # numeric OID used when the MIB is not compiled
    fallback_scale: float  # unit divisor used when the MIB is not compiled
    kind: str  # "int" | "scaled" | "text"


class AbstractPdu(AbstractDeviceHandler, ABC):
    """Base class for Server Technology PDU handlers.

    Subclasses provide ``MIB_MODULE``, the ordered ``FIELDS`` spec, the status
    field name, ``NOMINAL_STATUS`` and a fallback status enum. ``fetch_stats`` is
    a template that resolves each field's OID/scale from the MIB when available
    and otherwise from the field's hardcoded fallback.
    """

    MIB_MODULE: str
    FIELDS: tuple[Field, ...]
    STATUS_KEY: str = "input_status"
    STATUS_SYMBOL: str
    FALLBACK_STATUS_ENUM: dict[int, str]
    NOMINAL_STATUS: int
    # Device-evaluated threshold statuses (Phase B):
    #   stat_key -> (mib_symbol, fallback_enum, warning_codes, critical_codes)
    # Each yields a decoded ``<key>_text`` label and contributes to ``pq_severity``.
    QUALITY_STATUS: dict[str, tuple[str, dict[int, str], frozenset[int], frozenset[int]]] = {}
    # Phase D: environmental sensor table config (None if the family has none).
    #   {"temp": {value_base, name_base, scale, scale_oid|scale_base},
    #    "humid": {value_base, name_base, scale}}
    ENV_CONFIG: dict[str, dict[str, object]] | None = None
    # Phase E: per-outlet telemetry. OUTLET_CONFIG declares the outlet columns;
    # COLLECT_OUTLETS gates the (high-cardinality) outlet walk fleet-wide — set it
    # False to disable per-outlet collection without code changes.
    COLLECT_OUTLETS: bool = True
    OUTLET_CONFIG: dict[str, object] | None = None

    DEFAULT_TIMEOUT: float = 1.5
    DEFAULT_RETRIES: int = 1

    def _healthy(self, input_status: int | None) -> int | None:
        """Derive the MIB-agnostic 0/1 health flag from a raw status code."""
        if input_status is None:
            return None
        return 1 if input_status == self.NOMINAL_STATUS else 0

    def _post(self, out: dict[str, object]) -> None:
        """Subclass hook for derived fields (default: no-op)."""

    def fetch_stats(self) -> dict[str, object]:
        """Fetch and normalise PDU stats, MIB-backed where available."""
        res = get_resolver()

        # Build the OID list: sysUpTime first, then each declared field.
        oids: list[str] = [OID_SYS_UPTIME]
        plan: list[tuple[str, float, str]] = [("uptime_seconds", 100.0, "ticks")]
        for f in self.FIELDS:
            oid = res.oid(self.MIB_MODULE, f.symbol, *f.indices) or f.fallback_oid
            scale = res.scale(self.MIB_MODULE, f.symbol)
            if scale is None:
                scale = f.fallback_scale
            oids.append(oid)
            plan.append((f.key, scale, f.kind))

        error_indication, error_status, _error_index, var_binds = self._snmp_get(
            oids, timeout=self.DEFAULT_TIMEOUT, retries=self.DEFAULT_RETRIES
        )
        if error_indication or error_status:
            return {}

        values = values_from_varbinds(var_binds)
        out: dict[str, object] = {}
        for (key, scale, kind), val in zip(plan, values):
            if kind == "ticks":
                v = ticks_to_seconds(as_int(val))
            elif kind == "int":
                v = as_int(val)
            elif kind == "text":
                v = non_empty_str(val)
            else:  # "scaled"
                n = as_float(val)
                v = None if n is None else (n / scale if scale else n)
            if v is not None and v != "":
                out[key] = v

        # Status -> healthy flag + human-readable label (MIB enum or fallback).
        status = out.get(self.STATUS_KEY)
        if status is not None:
            healthy = self._healthy(int(status))
            if healthy is not None:
                out["healthy"] = healthy
            enum = res.enum(self.MIB_MODULE, self.STATUS_SYMBOL) or self.FALLBACK_STATUS_ENUM
            label = enum.get(int(status))
            if label:
                out["status_text"] = label

        # Device-evaluated threshold statuses -> decoded labels + a 0/1/2 severity
        # (0 normal, 1 warning, 2 critical). Non-threshold codes (noComm/lost/...)
        # map to 0 here; comms/sensor faults are covered by the health rule.
        if self.QUALITY_STATUS:
            severity = 0
            seen = False
            for key, (symbol, fb_enum, warn_codes, crit_codes) in self.QUALITY_STATUS.items():
                code = out.get(key)
                if code is None:
                    continue
                seen = True
                code = int(code)
                enum = res.enum(self.MIB_MODULE, symbol) or fb_enum
                label = enum.get(code)
                if label:
                    out[key + "_text"] = label
                if code in crit_codes:
                    severity = max(severity, 2)
                elif code in warn_codes:
                    severity = max(severity, 1)
            if seen:
                out["pq_severity"] = severity

        # Phase D: environmental sensors (temp/humidity), if the family has them.
        env = self.fetch_environment()
        if env:
            out["environment"] = env

        # Phase E: per-outlet telemetry (fleet-wide; gated by COLLECT_OUTLETS).
        if self.COLLECT_OUTLETS:
            outlets = self.fetch_outlets()
            if outlets:
                out["outlets"] = outlets

        self._post(out)
        return out

    def fetch_outlets(self) -> list[dict[str, object]]:
        """Walk the per-outlet table; one dict per outlet (empty if unsupported).

        Returns per-outlet ``index``/``name``/``state``/``current``/``power``/
        ``energy`` where available. ``state`` is normalised to 0=off / 1=on /
        2=other from the vendor enum. Outlets are keyed by their stable index so
        renaming an outlet (``name`` rides on a separate info metric) never churns
        the numeric series.
        """
        cfg = self.OUTLET_CONFIG
        if not cfg:
            return []

        state_cfg = cfg["state"]  # type: ignore[index]
        states = self._walk_indexed(str(state_cfg["base"]))
        currents = self._walk_indexed(str(cfg["current"]["base"]))  # type: ignore[index]
        if not (states or currents):
            return []
        powers = self._walk_indexed(str(cfg["power"]["base"]))  # type: ignore[index]
        energies = self._walk_indexed(str(cfg["energy"]["base"]))  # type: ignore[index]
        names = self._walk_indexed(str(cfg["name"]["base"]))  # type: ignore[index]

        state_map: dict[int, int] = cfg["state_map"]  # type: ignore[index,assignment]
        i_scale = float(cfg["current"]["scale"])  # type: ignore[index]
        p_scale = float(cfg["power"]["scale"])  # type: ignore[index]
        e_scale = float(cfg["energy"]["scale"])  # type: ignore[index]

        indices = set(states) | set(currents) | set(powers) | set(energies)
        outlets: list[dict[str, object]] = []
        for idx in sorted(indices):
            entry: dict[str, object] = {"index": idx}
            raw_state = as_int(states.get(idx))
            if raw_state is not None:
                entry["state"] = state_map.get(raw_state, 2)
            cur = as_float(currents.get(idx))
            if cur is not None:
                entry["current"] = cur / i_scale
            pwr = as_float(powers.get(idx))
            if pwr is not None:
                entry["power"] = pwr / p_scale
            eng = as_float(energies.get(idx))
            if eng is not None:
                entry["energy"] = eng / e_scale
            name = non_empty_str(names.get(idx))
            if name:
                entry["name"] = name
            outlets.append(entry)
        return outlets

    def fetch_environment(self) -> dict[str, list[dict[str, object]]]:
        """Walk temperature/humidity sensor tables; empty if none are fitted.

        Temperatures are normalised to Celsius using the sensor's scale enum
        (0=celsius, 1=fahrenheit). Returns ``{"temperature": [...], "humidity":
        [...]}`` with per-sensor ``index``/``name``/value entries; an empty result
        simply means the PDU exposes no environmental probes.
        """
        cfg = self.ENV_CONFIG
        if not cfg:
            return {}
        out: dict[str, list[dict[str, object]]] = {}

        tcfg = cfg.get("temp")
        if tcfg:
            tvals = self._walk_indexed(str(tcfg["value_base"]))
            if tvals:
                tnames = self._walk_indexed(str(tcfg["name_base"]))
                fmap = self._fahrenheit_map(tcfg, list(tvals))
                temps: list[dict[str, object]] = []
                for idx, raw in tvals.items():
                    val = as_float(raw)
                    if val is None:
                        continue
                    celsius = val / float(tcfg["scale"])
                    if fmap.get(idx):
                        celsius = (celsius - 32.0) * 5.0 / 9.0
                    entry: dict[str, object] = {"index": idx, "celsius": round(celsius, 2)}
                    name = non_empty_str(tnames.get(idx))
                    if name:
                        entry["name"] = name
                    temps.append(entry)
                if temps:
                    out["temperature"] = temps

        hcfg = cfg.get("humid")
        if hcfg:
            hvals = self._walk_indexed(str(hcfg["value_base"]))
            if hvals:
                hnames = self._walk_indexed(str(hcfg["name_base"]))
                hums: list[dict[str, object]] = []
                for idx, raw in hvals.items():
                    val = as_float(raw)
                    if val is None:
                        continue
                    entry = {"index": idx, "percent": val / float(hcfg["scale"])}
                    name = non_empty_str(hnames.get(idx))
                    if name:
                        entry["name"] = name
                    hums.append(entry)
                if hums:
                    out["humidity"] = hums

        return out

    def _fahrenheit_map(self, tcfg: dict[str, object], indices: list[int]) -> dict[int, bool]:
        """Return ``{index: reports_fahrenheit}`` from the sensor scale enum."""
        scale_oid = tcfg.get("scale_oid")
        if scale_oid:  # one scalar scale for all sensors
            err_ind, err_st, _i, vb = self._snmp_get([str(scale_oid)])
            if not (err_ind or err_st) and vb:
                is_f = as_int(vb[0][1]) == 1
                return {idx: is_f for idx in indices}
            return {}
        scale_base = tcfg.get("scale_base")
        if scale_base:  # per-sensor scale column
            sm = self._walk_indexed(str(scale_base))
            return {idx: (as_int(sm.get(idx)) == 1) for idx in indices}
        return {}


class Sentry4Pdu(AbstractPdu):
    """Sentry4-MIB handler for PRO2-series PDUs (model ``CW-*``)."""

    MIB_MODULE = "Sentry4-MIB"
    STATUS_SYMBOL = "st4InputCordStatus"
    NOMINAL_STATUS = 0  # Sentry4 DeviceStatus: normal(0)
    FALLBACK_STATUS_ENUM = {
        0: "normal",
        1: "disabled",
        2: "purged",
        5: "reading",
        6: "settle",
        7: "notFound",
        8: "lost",
        9: "readError",
        10: "noComm",
        11: "pwrError",
        12: "breakerTripped",
        13: "fuseBlown",
        14: "lowAlarm",
        15: "lowWarning",
        16: "highWarning",
        17: "highAlarm",
        18: "alarm",
        19: "underLimit",
        20: "overLimit",
        21: "nvmFail",
        22: "profileError",
        23: "conflict",
    }
    FIELDS = (
        Field(
            "input_status",
            "st4InputCordStatus",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.2.1.1",
            1.0,
            "int",
        ),
        Field(
            "active_power_watts",
            "st4InputCordActivePower",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.3.1.1",
            1.0,
            "scaled",
        ),
        Field(
            "line_current_amps",
            "st4LineCurrent",
            (1, 1, 1),
            "1.3.6.1.4.1.1718.4.1.4.3.1.3.1.1.1",
            100.0,
            "scaled",
        ),
        Field(
            "current_utilized_pct",
            "st4LineCurrentUtilized",
            (1, 1, 1),
            "1.3.6.1.4.1.1718.4.1.4.3.1.5.1.1.1",
            10.0,
            "scaled",
        ),
        Field(
            "voltage_volts",
            "st4PhaseVoltage",
            (1, 1, 1),
            "1.3.6.1.4.1.1718.4.1.5.3.1.3.1.1.1",
            10.0,
            "scaled",
        ),
        Field(
            "firmware",
            "st4SystemFirmwareVersion",
            (0,),
            "1.3.6.1.4.1.1718.4.1.1.1.3.0",
            1.0,
            "text",
        ),
        # Phase A: input-feed power quality (st4InputCordMonitorEntry, unit 1 / cord 1).
        Field(
            "energy_kwh",
            "st4InputCordEnergy",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.10.1.1",
            10.0,
            "scaled",
        ),
        Field(
            "frequency_hz",
            "st4InputCordFrequency",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.11.1.1",
            10.0,
            "scaled",
        ),
        Field(
            "power_factor",
            "st4InputCordPowerFactor",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.8.1.1",
            100.0,
            "scaled",
        ),
        Field(
            "apparent_power_va",
            "st4InputCordApparentPower",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.5.1.1",
            1.0,
            "scaled",
        ),
        Field(
            "out_of_balance_pct",
            "st4InputCordOutOfBalance",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.12.1.1",
            10.0,
            "scaled",
        ),
        # Phase B: device-evaluated threshold status codes (DeviceStatus enum).
        Field(
            "active_power_status",
            "st4InputCordActivePowerStatus",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.4.1.1",
            1.0,
            "int",
        ),
        Field(
            "power_factor_status",
            "st4InputCordPowerFactorStatus",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.9.1.1",
            1.0,
            "int",
        ),
        Field(
            "balance_status",
            "st4InputCordOutOfBalanceStatus",
            (1, 1),
            "1.3.6.1.4.1.1718.4.1.3.3.1.13.1.1",
            1.0,
            "int",
        ),
        Field(
            "load_status",
            "st4LineCurrentStatus",
            (1, 1, 1),
            "1.3.6.1.4.1.1718.4.1.4.3.1.4.1.1.1",
            1.0,
            "int",
        ),
    )
    QUALITY_STATUS = {
        "active_power_status": (
            "st4InputCordActivePowerStatus",
            FALLBACK_STATUS_ENUM,
            DEVICE_STATUS_WARN,
            DEVICE_STATUS_CRIT,
        ),
        "power_factor_status": (
            "st4InputCordPowerFactorStatus",
            FALLBACK_STATUS_ENUM,
            DEVICE_STATUS_WARN,
            DEVICE_STATUS_CRIT,
        ),
        "balance_status": (
            "st4InputCordOutOfBalanceStatus",
            FALLBACK_STATUS_ENUM,
            DEVICE_STATUS_WARN,
            DEVICE_STATUS_CRIT,
        ),
        "load_status": (
            "st4LineCurrentStatus",
            FALLBACK_STATUS_ENUM,
            DEVICE_STATUS_WARN,
            DEVICE_STATUS_CRIT,
        ),
    }
    # Separate temp + humidity tables; temperature scale is a common-config scalar.
    ENV_CONFIG = {
        "temp": {
            "value_base": "1.3.6.1.4.1.1718.4.1.9.3.1.1",  # st4TempSensorValue (tenth-deg)
            "name_base": "1.3.6.1.4.1.1718.4.1.9.2.1.3",  # st4TempSensorName
            "scale": 10.0,
            "scale_oid": "1.3.6.1.4.1.1718.4.1.9.1.10.0",  # st4TempSensorScale (celsius/fahrenheit)
        },
        "humid": {
            "value_base": "1.3.6.1.4.1.1718.4.1.10.3.1.1",  # st4HumidSensorValue (%)
            "name_base": "1.3.6.1.4.1.1718.4.1.10.2.1.3",  # st4HumidSensorName
            "scale": 1.0,
        },
    }
    OUTLET_CONFIG = {
        "state": {"base": "1.3.6.1.4.1.1718.4.1.8.3.1.1"},  # st4OutletState
        "current": {"base": "1.3.6.1.4.1.1718.4.1.8.3.1.3", "scale": 100.0},  # hundredth-Amps
        "power": {"base": "1.3.6.1.4.1.1718.4.1.8.3.1.7", "scale": 1.0},  # Watts
        "energy": {"base": "1.3.6.1.4.1.1718.4.1.8.3.1.14", "scale": 1.0},  # Watt-hours
        "name": {"base": "1.3.6.1.4.1.1718.4.1.8.2.1.3"},  # st4OutletName
        "state_map": {1: 1, 2: 0, 0: 2},  # st4OutletState on(1)/off(2)/unknown(0)
    }


class Sentry3Pdu(AbstractPdu):
    """Sentry3-MIB handler for CDU-series PDUs (model ``AA-*``).

    Sentry3 has no direct "percent utilised" object, so it is derived from line
    current and infeed capacity.
    """

    MIB_MODULE = "Sentry3-MIB"
    STATUS_SYMBOL = "infeedStatus"
    NOMINAL_STATUS = 1  # Sentry3 infeedStatus: on(1)
    FALLBACK_STATUS_ENUM = {
        0: "off",
        1: "on",
        2: "offWait",
        3: "onWait",
        4: "offError",
        5: "onError",
        6: "noComm",
        7: "reading",
        8: "offFuse",
        9: "onFuse",
    }
    FIELDS = (
        Field("input_status", "infeedStatus", (1, 1), "1.3.6.1.4.1.1718.3.2.2.1.5.1.1", 1.0, "int"),
        Field(
            "line_current_amps",
            "infeedLoadValue",
            (1, 1),
            "1.3.6.1.4.1.1718.3.2.2.1.7.1.1",
            100.0,
            "scaled",
        ),
        Field(
            "capacity_amps",
            "infeedCapacity",
            (1, 1),
            "1.3.6.1.4.1.1718.3.2.2.1.10.1.1",
            1.0,
            "scaled",
        ),
        # infeedVoltage is "tenth Volts" in the MIB -> scale 10 (fallback matches).
        Field(
            "voltage_volts",
            "infeedVoltage",
            (1, 1),
            "1.3.6.1.4.1.1718.3.2.2.1.11.1.1",
            10.0,
            "scaled",
        ),
        Field(
            "active_power_watts",
            "infeedPower",
            (1, 1),
            "1.3.6.1.4.1.1718.3.2.2.1.12.1.1",
            1.0,
            "scaled",
        ),
        Field("firmware", "systemVersion", (0,), "1.3.6.1.4.1.1718.3.1.1.0", 1.0, "text"),
        # Phase A: Sentry3 exposes energy (per-infeed) and a system-wide power factor.
        Field(
            "energy_kwh", "infeedEnergy", (1, 1), "1.3.6.1.4.1.1718.3.2.2.1.16.1.1", 10.0, "scaled"
        ),
        Field(
            "power_factor", "systemPowerFactor", (0,), "1.3.6.1.4.1.1718.3.1.10.0", 100.0, "scaled"
        ),
        # Phase B: Sentry3 device-evaluated load status (its own enum).
        Field(
            "load_status", "infeedLoadStatus", (1, 1), "1.3.6.1.4.1.1718.3.2.2.1.6.1.1", 1.0, "int"
        ),
    )
    # infeedLoadStatus enum: loadLow(3)/loadHigh(4) -> warning, overLoad(5) -> critical.
    QUALITY_STATUS = {
        "load_status": (
            "infeedLoadStatus",
            {
                0: "normal",
                1: "notOn",
                2: "reading",
                3: "loadLow",
                4: "loadHigh",
                5: "overLoad",
                6: "readError",
                7: "noComm",
            },
            frozenset({3, 4}),
            frozenset({5}),
        ),
    }
    # Combined temp+humidity table; temperature scale is a per-sensor column.
    ENV_CONFIG = {
        "temp": {
            "value_base": "1.3.6.1.4.1.1718.3.2.5.1.6",  # tempHumidSensorTempValue (tenth-deg)
            "name_base": "1.3.6.1.4.1.1718.3.2.5.1.3",  # tempHumidSensorName
            "scale": 10.0,
            "scale_base": "1.3.6.1.4.1.1718.3.2.5.1.13",  # tempHumidSensorTempScale (per-sensor)
        },
        "humid": {
            "value_base": "1.3.6.1.4.1.1718.3.2.5.1.10",  # tempHumidSensorHumidValue (%)
            "name_base": "1.3.6.1.4.1.1718.3.2.5.1.3",  # tempHumidSensorName
            "scale": 1.0,
        },
    }
    OUTLET_CONFIG = {
        "state": {"base": "1.3.6.1.4.1.1718.3.2.3.1.5"},  # outletStatus
        "current": {"base": "1.3.6.1.4.1.1718.3.2.3.1.7", "scale": 100.0},  # hundredth-Amps
        "power": {"base": "1.3.6.1.4.1.1718.3.2.3.1.14", "scale": 1.0},  # Watts
        "energy": {"base": "1.3.6.1.4.1.1718.3.2.3.1.18", "scale": 1.0},  # Watt-hours
        "name": {"base": "1.3.6.1.4.1.1718.3.2.3.1.3"},  # outletName
        "state_map": {0: 0, 1: 1},  # outletStatus off(0)/on(1); others -> 2
    }

    def _post(self, out: dict[str, object]) -> None:
        """Derive percent-utilised from line current and capacity."""
        cur = out.get("line_current_amps")
        cap = out.pop("capacity_amps", None)
        if isinstance(cur, (int, float)) and isinstance(cap, (int, float)) and cap > 0:
            out["current_utilized_pct"] = 100.0 * float(cur) / float(cap)
