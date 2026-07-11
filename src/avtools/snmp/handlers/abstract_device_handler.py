"""Abstract base class for SNMP handlers."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, Union

from pysnmp.hlapi import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
    nextCmd,
)

_SYS_UPTIME_OID = "1.3.6.1.2.1.1.3.0"  # sysUpTime.0 (mandatory on all agents)
_SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"  # sysDescr.0 (mandatory on all agents)

# Thread-local SnmpEngine reuse. SNMP calls are offloaded to a thread pool; pysnmp's
# SnmpEngine is NOT thread-safe, so each worker thread keeps its own engine and reuses
# it across all calls on that thread (the engine is target-independent — the target
# rides on UdpTransportTarget per call). This eliminates the previous per-call engine
# construction, which dominated SNMP overhead. `engine_build_count()` exposes the number
# of constructions so the churn reduction is observable in logs (expect ~= thread count).
_thread_local = threading.local()
_engine_build_lock = threading.Lock()
_engine_build_count = 0


def _thread_engine() -> SnmpEngine:
    """Return this thread's reusable SnmpEngine, building one on first use."""
    eng = getattr(_thread_local, "engine", None)
    if eng is None:
        eng = SnmpEngine()
        _thread_local.engine = eng
        global _engine_build_count
        with _engine_build_lock:
            _engine_build_count += 1
    return eng


def engine_build_count() -> int:
    """Total SnmpEngine constructions so far (observability; expect ~= thread count)."""
    return _engine_build_count


def _as_int(value: Any) -> int | None:
    """Best-effort int conversion for pysnmp values; ``None`` on failure/empty."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_snmp_failure(error_indication: object, error_status: object) -> str:
    """Map a pysnmp ``(errorIndication, errorStatus)`` to a stable reason keyword.

    Enum: ``timeout`` | ``host_unreachable`` | ``network_unreachable`` |
    ``auth_failure`` | ``mib_error`` | ``unknown``.

    NOTE: this stack is SNMPv2c, so a rotated/wrong community manifests as
    ``timeout`` (the agent drops the request silently), not ``auth_failure``
    (a v3/USM condition, matched here only for completeness).
    """
    if error_indication:
        t = str(error_indication).lower()
        if "timeout" in t or "timed out" in t or "timedout" in t or "no snmp response" in t:
            return "timeout"
        if "no route to host" in t or "host unreachable" in t or "host is unreachable" in t:
            return "host_unreachable"
        if "network is unreachable" in t or "network unreachable" in t:
            return "network_unreachable"
        if any(
            s in t
            for s in (
                "unknown user",
                "unknownuser",
                "wrong digest",
                "wrongdigest",
                "authentication",
                "decryption",
                "security level",
                "securitylevel",
                "wrong security",
                "wrongsecurity",
            )
        ):
            return "auth_failure"
        return "unknown"
    if error_status:
        # The agent answered but returned an SNMP-level error (noSuchName, genErr, ...).
        return "mib_error"
    return "unknown"


class AbstractDeviceHandler(ABC):
    """
    Base class for all SNMP device handlers.

    This class stores the connection parameters shared by every handler and
    provides a lightweight `probe` method that verifies whether the SNMP agent
    is reachable.  Subclasses must supply their own implementation of
    :meth:`fetch_stats`.

    Args:
        ip (str): IP address or DNS name of the target device.
        community (str): SNMP v2c community string. Defaults to ``"public"``.
        port (int, optional): UDP port on which the agent listens. Defaults
            to ``161``.
    """

    def __init__(self, ip: str, community: str = "public", port: int = 161) -> None:
        """Initialize a base SNMP handler with connection parameters."""
        self.ip: str = ip
        self.community: str = community
        self.port: int = port
        self.engine: SnmpEngine | None = None

    def set_engine(self, engine: SnmpEngine) -> None:
        """Attach a shared PySNMP engine to this handler."""
        self.engine = engine

    def _snmp_get(
        self,
        oids: Sequence[Union[str, ObjectIdentity]],
        *,
        timeout: float = 1.0,
        retries: int = 1,  # cut dead-device probe/query timeout tail (was 2)
    ):
        """Perform a single SNMP GET for multiple OIDs.

        Convenience wrapper so subclasses don't duplicate PySNMP boilerplate,
        and so we can reuse a shared engine when provided via `set_engine()`.
        """
        obj_types = [
            ObjectType(ObjectIdentity(oid) if isinstance(oid, str) else oid) for oid in oids
        ]

        iterator = getCmd(
            self.engine or _thread_engine(),
            CommunityData(self.community, mpModel=1),  # SNMP v2c
            UdpTransportTarget((self.ip, self.port), timeout=timeout, retries=retries),
            ContextData(),
            *obj_types,
        )

        return next(iterator)

    def _snmp_walk(
        self,
        base_oid: str,
        *,
        timeout: float = 1.5,
        retries: int = 1,
        max_rows: int = 256,
    ) -> list[tuple[str, Any]]:
        """Walk an OID subtree (SNMP GETNEXT), returning ``(oid, value)`` rows.

        Stops at the end of the subtree, on error, or after ``max_rows`` (a safety
        cap so a misbehaving agent can't stream unbounded rows).

        Args:
            base_oid: Root OID of the column/table to walk.
            timeout: Per-request timeout in seconds.
            retries: Retries after the first attempt.
            max_rows: Hard cap on the number of rows returned.

        Returns:
            List of ``(oid_string, value)`` for entries under ``base_oid``; empty
            on error.
        """
        rows: list[tuple[str, Any]] = []
        iterator = nextCmd(
            self.engine or _thread_engine(),
            CommunityData(self.community, mpModel=1),  # SNMP v2c
            UdpTransportTarget((self.ip, self.port), timeout=timeout, retries=retries),
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False,  # stop when we leave base_oid's subtree
            maxRows=max_rows,
        )
        for error_indication, error_status, _error_index, var_binds in iterator:
            if error_indication or error_status:
                break
            for name, value in var_binds:
                rows.append((str(name), value))
        return rows

    def _walk_indexed(self, base_oid: str, *, max_rows: int = 256) -> dict[int, Any]:
        """Walk a single table column, returning ``{last_index_component: value}``.

        Convenience over :meth:`_snmp_walk` for simple single-integer-indexed
        columns (interface tables, sensor tables); rows whose final OID arc isn't
        an integer are skipped.
        """
        out: dict[int, Any] = {}
        for oid, value in self._snmp_walk(base_oid, max_rows=max_rows):
            try:
                out[int(oid.rsplit(".", 1)[-1])] = value
            except ValueError:
                continue
        return out

    def fetch_interfaces(
        self, *, include_counters: bool = True, max_rows: int = 256
    ) -> list[dict[str, Any]]:
        """Walk the MIB-II interface table and return per-interface readings.

        Universal across vendors (MIB-II is implemented by essentially every SNMP
        agent). Loopback interfaces (ifType 24) are filtered out. Each interface is
        labelled by its stable ``ifindex``; ``ifdescr`` is returned for an info
        metric rather than used as a series label (names can change).

        Args:
            include_counters: Also walk 64-bit octet + error counters.
            max_rows: Row cap passed through to each column walk.

        Returns:
            List of dicts with keys ``ifindex``, ``ifdescr``, ``oper_status`` and
            (when available) ``in_octets``/``out_octets``/``in_errors``/
            ``out_errors``. Empty if the agent exposes no interface table.
        """
        # ifTable / ifXTable column base OIDs.
        oid_descr = "1.3.6.1.2.1.2.2.1.2"  # ifDescr
        oid_type = "1.3.6.1.2.1.2.2.1.3"  # ifType
        oid_oper = "1.3.6.1.2.1.2.2.1.8"  # ifOperStatus
        oid_in_err = "1.3.6.1.2.1.2.2.1.14"  # ifInErrors
        oid_out_err = "1.3.6.1.2.1.2.2.1.20"  # ifOutErrors
        oid_hc_in = "1.3.6.1.2.1.31.1.1.1.6"  # ifHCInOctets
        oid_hc_out = "1.3.6.1.2.1.31.1.1.1.10"  # ifHCOutOctets

        def walk_by_index(base: str) -> dict[int, Any]:
            return self._walk_indexed(base, max_rows=max_rows)

        oper = walk_by_index(oid_oper)
        if not oper:
            return []
        descr = walk_by_index(oid_descr)
        iftype = walk_by_index(oid_type)
        in_err = walk_by_index(oid_in_err) if include_counters else {}
        out_err = walk_by_index(oid_out_err) if include_counters else {}
        hc_in = walk_by_index(oid_hc_in) if include_counters else {}
        hc_out = walk_by_index(oid_hc_out) if include_counters else {}

        interfaces: list[dict[str, Any]] = []
        for idx, oper_val in oper.items():
            # Skip loopback interfaces (ifType softwareLoopback = 24).
            try:
                if iftype.get(idx) is not None and int(iftype[idx]) == 24:
                    continue
            except (TypeError, ValueError):
                pass

            entry: dict[str, Any] = {"ifindex": idx, "oper_status": _as_int(oper_val)}
            d = descr.get(idx)
            if d is not None:
                text = str(d).strip()
                if text:
                    entry["ifdescr"] = text
            if include_counters:
                for key, src in (
                    ("in_octets", hc_in),
                    ("out_octets", hc_out),
                    ("in_errors", in_err),
                    ("out_errors", out_err),
                ):
                    v = _as_int(src.get(idx))
                    if v is not None:
                        entry[key] = v
            interfaces.append(entry)
        return interfaces

    def probe(
        self,
        oid: str = _SYS_UPTIME_OID,
        *,
        timeout: int = 1,
        retries: int = 1,  # cut dead-device probe/query timeout tail (was 2)
    ) -> bool:
        """
        Check whether the device responds to a simple SNMP GET request.
        """
        ok, _reason = self.probe_with_reason(oid, timeout=timeout, retries=retries)
        return ok

    def probe_with_reason(
        self,
        oid: str = _SYS_UPTIME_OID,
        *,
        timeout: int = 1,
        retries: int = 1,
    ) -> tuple[bool, str | None]:
        """Probe SNMP availability, returning ``(ok, reason)``.

        ``reason`` is None on success, else a stable failure classification
        (see :func:`classify_snmp_failure`).

        NOTE: this stack uses SNMPv2c (community strings). A wrong/rotated
        community causes the agent to drop the request silently, which surfaces
        here as ``"timeout"`` — there is no distinct ``auth_failure`` for v2c
        (that is a v3/USM condition).
        """
        error_indication, error_status, *_ = self._snmp_get(
            [oid],
            timeout=timeout,
            retries=retries,
        )
        if not (error_indication or error_status):
            return True, None
        return False, classify_snmp_failure(error_indication, error_status)

    def fetch_sysdescr(
        self,
        oid: str = _SYS_DESCR_OID,
        *,
        timeout: float = 1.0,
        retries: int = 1,  # cut dead-device probe/query timeout tail (was 2)
    ) -> str | None:
        """Fetch sysDescr.0 (device description string).

        This is a generic identity-like field present on virtually all SNMP agents.
        It is intentionally **not** exported to time-series sinks; it is meant for
        Postgres monitoring tables (latest-known snapshot).

        Args:
            oid: OID to fetch (defaults to sysDescr.0).
            timeout: SNMP timeout in seconds.
            retries: SNMP retries.

        Returns:
            sysDescr string, or None if unavailable.
        """
        try:
            error_indication, error_status, *_rest = self._snmp_get(
                [oid],
                timeout=timeout,
                retries=retries,
            )
            if error_indication or error_status:
                return None

            # _snmp_get returns (errorIndication, errorStatus, errorIndex, varBinds)
            # where varBinds is typically a list of (ObjectName, ObjectValue).
            var_binds = _rest[-1] if _rest else None
            if not var_binds:
                return None

            vb0 = var_binds[0]
            val = None
            try:
                # Common case: tuple pair
                if isinstance(vb0, tuple) and len(vb0) == 2:
                    val = vb0[1]
                else:
                    # pysnmp ObjectType supports indexing
                    val = vb0[1]  # type: ignore[index]
            except Exception:
                # Best-effort fallback
                try:
                    val = getattr(vb0, "getComponentByPosition")(1)
                except Exception:
                    val = None

            if val is None:
                return None

            s = val.prettyPrint() if hasattr(val, "prettyPrint") else str(val)
            s = str(s).strip()
            return s or None
        except Exception:
            return None

    @abstractmethod
    def fetch_stats(self) -> Mapping[str, Any]:
        """
        Retrieve device-specific metrics.
        """
        ...
