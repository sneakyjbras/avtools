from __future__ import annotations

import asyncio
import ipaddress
import os
import random
import re
import contextlib
from collections import Counter
from dataclasses import dataclass
from time import time
from typing import Mapping, Sequence

import structlog

from avtools.snmp.factories.device_factory import DeviceHandlerFactory
from avtools.snmp.queries import (
    SNMPQuerySpec,
    default_query_specs,
    get_eqclass_category,
)
from avtools.postgres.inventory.orm.landb_ipaddress import CachedIPAddress
from avtools.exception.errors import SNMPQueryExecutionError

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class PingResult:
    """Result of pinging a single device.

    Attributes:
        device: Original device object as provided to the client.
        ip: IP address string (or None if missing).
        equipmentno: Stable identifier used as metric label (or None if missing).
        up: 1 if ping succeeded, else 0.
        rtt_ms: Round-trip time in milliseconds if available and up=1, else None.
        reason: Failure classification / hint (None if up=1).
        attempts: Number of ping attempts performed.
    """

    device: CachedIPAddress
    ip: str | None
    equipmentno: str | None
    up: int
    rtt_ms: float | None
    reason: str | None
    attempts: int


@dataclass(slots=True)
class ProbeResult:
    """Result of probing SNMP availability for a device.

    If `up=1`, we also (best-effort) collect `sysdescr` (sysDescr.0).
    """

    device: CachedIPAddress
    ip: str | None
    equipmentno: str | None
    up: int
    eqclass: str | None
    category: str | None
    sysdescr: str | None


@dataclass(slots=True)
class QueryResult:
    """Raw SNMP query output for a device.

    `stats` is handler-returned data, unmodified.
    """

    device: CachedIPAddress
    ip: str | None
    equipmentno: str | None
    query: str
    eqclass: str | None
    category: str | None
    stats: Mapping[str, object]


class SNMPClient:
    """Collect ping + SNMP metrics for LanDB devices.

    This client is intentionally *algorithmic* and low-cardinality:
    - It labels metrics with `equipmentno` only (stable join key for Postgres).
    - It runs a simple staged pipeline (typically orchestrated by the caller):

        1) Ping all targets -> subset of online devices
        2) SNMP probe on online devices -> subset of SNMP-available devices
        3) SNMP queries on SNMP-available devices (projectors today)

    Concurrency and reliability notes:
    - The caller spawns multiple OS processes (you mentioned 4). All limits here
      are **per process**. Total effective concurrency is roughly:

        total_ping_concurrency ~= processes * ping_concurrency

    - Pings use the system `ping` command via `asyncio.create_subprocess_exec`
      (no thread pool required for ping).
    - SNMP handlers are synchronous; this client offloads handler calls via
      `asyncio.to_thread`, but caps concurrency with semaphores to avoid creating
      an unbounded number of worker threads.

    Args:
        targets: List of CachedIPAddress targets (normalized by the router). Each must provide `equipment_no` and `ip`.
        ping_timeout_s: Per-attempt reply timeout in seconds (maps to `ping -W`).
        ping_retries: Number of retries after the first attempt. Total attempts
            = 1 + ping_retries.
        ping_backoff_s: Base backoff delay (seconds) between attempts.
        ping_backoff_max_s: Maximum backoff delay (seconds) between attempts.
        ping_jitter_s: Random jitter added to each backoff delay, uniformly
            sampled in [0, ping_jitter_s]. Helps avoid retry bursts.
        ping_concurrency: Max number of concurrent ping *attempts* in this
            process.
        snmp_probe_concurrency: Max number of concurrent SNMP probe calls in
            this process.
        snmp_query_concurrency: Max number of concurrent SNMP query calls in
            this process.

    """

    _PING_TIME_RE = re.compile(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms", re.IGNORECASE)
    _PING_RTT_RE = re.compile(
        r"(?:rtt|round-trip).*?=\s*([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )

    _PING_FAIL_SNIPPET_MAX = 160
    _PING_FAIL_EXAMPLES_MAX = 10

    def __init__(
        self,
        targets: list[CachedIPAddress],
        *,
        ping_timeout_s: int = 2,
        ping_retries: int = 3,
        ping_backoff_s: float = 0.2,
        ping_backoff_max_s: float = 0.8,
        ping_jitter_s: float = 0.2,
        ping_concurrency: int = 64,
        snmp_probe_concurrency: int = 32,
        snmp_query_concurrency: int = 16,
    ) -> None:
        self.targets = targets
        self.factory = DeviceHandlerFactory(devices=self.targets)
        self.handlers = self.factory.get_handlers()  # ip -> handler
        self.log = logger.bind(component="SNMPClient")

        # Ping reliability knobs (per process).
        self.ping_timeout_s = int(ping_timeout_s)
        self.ping_retries = int(ping_retries)
        self.ping_backoff_s = float(ping_backoff_s)
        self.ping_backoff_max_s = float(ping_backoff_max_s)
        self.ping_jitter_s = float(ping_jitter_s)
        self.ping_concurrency = int(ping_concurrency)

        # Concurrency caps for synchronous SNMP handler calls.
        self.snmp_probe_concurrency = int(snmp_probe_concurrency)
        self.snmp_query_concurrency = int(snmp_query_concurrency)

        # Semaphores are per-process. Multiply by number of worker processes outside.
        self._ping_sem = asyncio.Semaphore(max(1, self.ping_concurrency))
        self._snmp_probe_sem = asyncio.Semaphore(max(1, self.snmp_probe_concurrency))
        self._snmp_query_sem = asyncio.Semaphore(max(1, self.snmp_query_concurrency))

    # -------------------------
    # Helpers
    # -------------------------

    # -------------------------
    # Ping (POSIX ping, reliable)
    # -------------------------

    def _ping_attempts(self) -> int:
        """Total ping attempts per host."""
        return max(1, self.ping_retries + 1)

    def ping_max_budget_s(self) -> float:
        """Conservative upper bound (seconds) spent per host if all attempts fail.

        This includes:
        - `ping_timeout_s` per attempt
        - backoff between attempts (up to ping_backoff_max_s) + worst-case jitter

        Returns:
            Upper bound in seconds for a single host.
        """
        attempts = self._ping_attempts()
        total = attempts * float(self.ping_timeout_s)

        # Backoff occurs between attempts (attempts - 1 times).
        for i in range(max(0, attempts - 1)):
            backoff = min(self.ping_backoff_max_s, self.ping_backoff_s * (2**i))
            total += backoff + self.ping_jitter_s  # worst-case jitter
        return total

    @classmethod
    def _ping_hint(cls, text: str) -> str | None:
        """Extract a short, useful snippet from ping output (bounded)."""
        if not text:
            return None
        needles = (
            "unreachable",
            "packet loss",
            "unknown",
            "denied",
            "failure",
            "timed out",
        )
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if any(n in lower for n in needles):
                return stripped[: cls._PING_FAIL_SNIPPET_MAX]
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[: cls._PING_FAIL_SNIPPET_MAX]
        return None

    @classmethod
    def _ping_failure_reason(
        cls, *, returncode: int | None, timed_out: bool, text: str
    ) -> tuple[str, str | None]:
        """Classify a ping failure into a stable reason + short hint."""
        if timed_out:
            return ("process_timeout", None)
        t = (text or "").lower()
        if "name or service not known" in t or "temporary failure in name resolution" in t:
            return ("dns_resolution_failed", cls._ping_hint(text))
        if "network is unreachable" in t:
            return ("network_unreachable", cls._ping_hint(text))
        if "destination host unreachable" in t or "host unreachable" in t:
            return ("host_unreachable", cls._ping_hint(text))
        if "100% packet loss" in t:
            return ("packet_loss", cls._ping_hint(text))
        if "operation not permitted" in t or "permission denied" in t:
            return ("permission_denied", cls._ping_hint(text))
        if returncode is None:
            return ("unknown", cls._ping_hint(text))
        return (f"rc_{returncode}", cls._ping_hint(text))

    async def _ping_posix_once(
        self, host: str
    ) -> tuple[bool, float | None, str | None, str | None]:
        """Run a single ping attempt using the system `ping` command.

        Args:
            host: IP address or hostname.

        Returns:
            Tuple (ok, rtt_ms, fail_reason, fail_hint):
              - ok: True if ping succeeded (return code 0).
              - rtt_ms: RTT in milliseconds if parsed, else None.
              - fail_reason: Stable reason string if ok=False, else None.
              - fail_hint: Short output snippet if ok=False, else None.
        """
        # Best-effort v6 detection; if not an IP literal, let ping resolve.
        is_v6 = False
        try:
            is_v6 = ipaddress.ip_address(host).version == 6
        except ValueError:
            pass

        # Prefer ping -4/-6; fallback to ping6 for IPv6 if needed.
        if is_v6:
            candidates: list[list[str]] = [["ping", "-6"], ["ping6"]]
        else:
            candidates = [["ping", "-4"], ["ping"]]

        # -n: numeric output (avoid slow reverse DNS)
        # -c 1: one probe
        # -W: per-reply timeout (seconds) (Linux iputils / busybox)
        args = ["-n", "-c", "1", "-W", str(self.ping_timeout_s), host]
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}  # stable output for parsing

        # Protect ourselves even if ping behaves weirdly.
        per_attempt_process_timeout = float(self.ping_timeout_s) + 2.0

        for cmd in candidates:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *(cmd + args),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except FileNotFoundError:
                continue
            except Exception:
                return (False, None, "spawn_error", None)

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=per_attempt_process_timeout
                )
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.communicate()
                return (False, None, "process_timeout", None)

            out = (stdout_b or b"") + b"\n" + (stderr_b or b"")
            text = out.decode(errors="replace")

            ok = proc.returncode == 0

            rtt_ms: float | None = None
            m1 = self._PING_TIME_RE.search(text)
            if m1:
                try:
                    rtt_ms = float(m1.group(1))
                except Exception:
                    rtt_ms = None
            else:
                m2 = self._PING_RTT_RE.search(text)
                if m2:
                    try:
                        rtt_ms = float(m2.group(2))  # avg
                    except Exception:
                        rtt_ms = None

            if ok:
                return (True, rtt_ms, None, None)
            reason, hint = self._ping_failure_reason(
                returncode=proc.returncode, timed_out=False, text=text
            )
            return (False, None, reason, hint)

        return (False, None, "ping_cmd_not_found", None)

    def _next_backoff_s(self, attempt_index: int) -> float:
        """Compute backoff delay (seconds) before the next retry.

        Args:
            attempt_index: Zero-based index of the *failed* attempt.

        Returns:
            Delay in seconds (includes jitter).
        """
        base = min(self.ping_backoff_max_s, self.ping_backoff_s * (2**attempt_index))
        jitter = random.uniform(0.0, max(0.0, self.ping_jitter_s))
        return base + jitter

    async def _ping_posix(
        self, host: str
    ) -> tuple[bool, float | None, str | None, str | None, int]:
        """Ping a host with retries + backoff + jitter.

        Retries are applied **after** the first attempt. Total attempts:
        `1 + ping_retries`.

        Concurrency is capped by `ping_concurrency` using a semaphore around each
        ping attempt.

        Args:
            host: IP address or hostname.

        Returns:
            Tuple (ok, rtt_ms, reason, hint, attempts_used):
              - ok: True if any attempt succeeds.
              - rtt_ms: RTT from the successful attempt, else None.
              - reason/hint: Last failure classification if ok=False.
              - attempts_used: Number of attempts performed.
        """
        attempts = self._ping_attempts()
        last_reason: str | None = None
        last_hint: str | None = None
        for i in range(attempts):
            async with self._ping_sem:
                ok, rtt, reason, hint = await self._ping_posix_once(host)
            if ok:
                return (True, rtt, None, None, i + 1)

            last_reason = reason
            last_hint = hint

            # Backoff between failed attempts (do not hold semaphore while sleeping).
            if i < attempts - 1:
                await asyncio.sleep(self._next_backoff_s(i))

        return (False, None, last_reason, last_hint, attempts)

    # -------------------------
    # Public collection methods
    # -------------------------

    async def collect_ping(self) -> tuple[list[PingResult], list[CachedIPAddress]]:
        """Ping all targets.

        Returns:
            Tuple (results, alive_devices)
              - results: list of PingResult (raw ping observations)
              - alive_devices: subset of devices that replied to ping
        """
        t0 = time()

        async def ping_one(dev: CachedIPAddress) -> PingResult:
            ip = dev.ip
            eq = dev.equipment_no
            if not eq:
                return PingResult(
                    dev,
                    str(ip) if ip else None,
                    None,
                    0,
                    None,
                    "missing_equipmentno",
                    0,
                )
            if not ip:
                return PingResult(dev, None, eq, 0, None, "missing_ip", 0)

            status, rtt, reason, hint, used = await self._ping_posix(str(ip))
            up = 1 if status else 0
            rtt_ms = float(rtt) if (rtt is not None and up == 1) else None
            if up == 1:
                return PingResult(dev, str(ip), eq, up, rtt_ms, None, used)

            why = (hint or reason or "failed").strip()
            return PingResult(dev, str(ip), eq, up, None, why, used)

        results = await asyncio.gather(*(ping_one(d) for d in self.targets))

        alive: list[CachedIPAddress] = []
        fail_reasons: Counter[str] = Counter()
        fail_examples: list[dict[str, object]] = []
        missing_eq = 0
        missing_ip = 0

        for r in results:
            if not r.equipmentno:
                missing_eq += 1
                continue

            if r.up == 1:
                alive.append(r.device)
            else:
                if r.reason == "missing_ip":
                    missing_ip += 1
                reason = (r.reason or "failed").strip()
                fail_reasons[reason] += 1
                if len(fail_examples) < self._PING_FAIL_EXAMPLES_MAX:
                    fail_examples.append(
                        {
                            "equipmentno": r.equipmentno,
                            "ip": r.ip,
                            "reason": reason,
                            "attempts": r.attempts,
                        }
                    )

        if fail_reasons or missing_eq or missing_ip:
            self.log.warning(
                "snmp_ping_failures",
                devices=len(self.targets),
                missing_equipmentno=missing_eq,
                missing_ip=missing_ip,
                failed=sum(fail_reasons.values()),
                reasons=dict(fail_reasons),
                examples=fail_examples,
            )

        self.log.info(
            "snmp_ping_done",
            devices=len(self.targets),
            alive=len(alive),
            ping_timeout_s=self.ping_timeout_s,
            ping_retries=self.ping_retries,
            ping_attempts=self._ping_attempts(),
            ping_backoff_s=self.ping_backoff_s,
            ping_backoff_max_s=self.ping_backoff_max_s,
            ping_jitter_s=self.ping_jitter_s,
            ping_concurrency=self.ping_concurrency,
            ping_max_budget_s=round(self.ping_max_budget_s(), 3),
            duration_s=round(time() - t0, 3),
            missing_equipmentno=missing_eq,
            missing_ip=missing_ip,
            failed=sum(fail_reasons.values()),
        )
        return results, alive

    async def collect_snmp_probe(
        self, devices: list[CachedIPAddress] | None = None
    ) -> tuple[list[ProbeResult], list[CachedIPAddress]]:
        """SNMP probe (availability check).

        For each device, produce a ProbeResult with up=1 if SNMP responds, else 0.
        Devices without an SNMP handler are reported as up=0.

        Args:
            devices: Optional device list (defaults to all targets).

        Returns:
            Tuple (results, alive_devices)
              - results: list of ProbeResult (raw probe observations)
              - alive_devices: subset of devices with SNMP OK
        """
        t0 = time()
        devices = devices if devices is not None else self.targets

        async def probe_one(dev: CachedIPAddress) -> ProbeResult:
            ip = dev.ip
            eq = dev.equipment_no
            if not eq:
                return ProbeResult(dev, str(ip) if ip else None, None, 0, None, None, None)
            if not ip:
                return ProbeResult(dev, None, eq, 0, None, None, None)
            handler = self.handlers.get(str(ip))
            if handler is None:
                return ProbeResult(dev, str(ip), eq, 0, None, None, None)

            eqclass, category = get_eqclass_category(dev)

            sysdescr: str | None = None
            async with self._snmp_probe_sem:
                ok = await asyncio.to_thread(handler.probe)
                if ok:
                    fetch = getattr(handler, "fetch_sysdescr", None)
                    if callable(fetch):
                        sysdescr = await asyncio.to_thread(fetch)

            return ProbeResult(dev, str(ip), eq, 1 if ok else 0, eqclass, category, sysdescr)

        results = await asyncio.gather(*(probe_one(d) for d in devices))

        alive: list[CachedIPAddress] = []
        for r in results:
            if r.equipmentno and r.up == 1:
                alive.append(r.device)

        self.log.info(
            "snmp_probe_done",
            devices=len(devices),
            alive=len(alive),
            snmp_probe_concurrency=self.snmp_probe_concurrency,
            duration_s=round(time() - t0, 3),
        )
        return results, alive

    async def collect_snmp_queries(
        self,
        devices: list[CachedIPAddress],
        *,
        query_specs: Sequence[SNMPQuerySpec] | None = None,
        log_event: str = "snmp_query_routed_done",
    ) -> list[QueryResult]:
        """Run routed SNMP queries based on (eqclass, category).

        This is the generic "query" stage. It selects the first matching query
        spec for each device, runs it, and returns the raw handler output.

        Args:
            devices: Device list previously confirmed as SNMP-available.
            query_specs: Optional list of SNMPQuerySpec. If None, uses the default set.
            log_event: Structlog event name to use for the completion log.

        Returns:
            List of QueryResult (raw per-device results). Devices without a match
            are omitted.
        """
        t0 = time()
        specs: list[SNMPQuerySpec] = (
            list(query_specs) if query_specs is not None else default_query_specs()
        )

        async def query_one(dev: CachedIPAddress) -> tuple[str, QueryResult | None]:
            ip = dev.ip
            eq = dev.equipment_no
            if not (ip and eq):
                return ("skip_missing_ident", None)

            handler = self.handlers.get(str(ip))
            if handler is None:
                return ("skip_no_handler", None)

            eqclass, category = get_eqclass_category(dev)
            spec = next((s for s in specs if s.match(eqclass, category)), None)
            if spec is None:
                return ("skip_no_match", None)

            async with self._snmp_query_sem:
                try:
                    stats = await asyncio.to_thread(spec.fetch, handler)
                except Exception as exc:
                    raise SNMPQueryExecutionError(
                        f"SNMP query '{spec.name}' failed for equipmentno={eq} ip={ip}"
                    ) from exc

            if not stats:
                return (f"{spec.name}:empty", None)

            return (
                spec.name,
                QueryResult(
                    device=dev,
                    ip=str(ip),
                    equipmentno=eq,
                    query=spec.name,
                    eqclass=eqclass,
                    category=category,
                    stats=dict(stats),
                ),
            )

        results = await asyncio.gather(*(query_one(d) for d in devices))

        out: list[QueryResult] = []
        routes: Counter[str] = Counter()
        for route, maybe in results:
            routes[route] += 1
            if maybe is not None:
                out.append(maybe)

        self.log.info(
            log_event,
            devices=len(devices),
            results=len(out),
            snmp_query_concurrency=self.snmp_query_concurrency,
            routes=dict(routes),
            duration_s=round(time() - t0, 3),
        )
        return out

    async def collect_projector_query(self, devices: list[CachedIPAddress]) -> list[QueryResult]:
        """Backwards-compatible projector-only query.

        This now routes strictly to the projector query spec (and will skip
        devices that don't match AVD+Projector).
        """
        # Import here to avoid unnecessary module import cost when not used.
        from avtools.snmp.queries.projector import projector_query_spec

        return await self.collect_snmp_queries(
            devices,
            query_specs=[projector_query_spec()],
            log_event="snmp_projector_query_done",
        )
