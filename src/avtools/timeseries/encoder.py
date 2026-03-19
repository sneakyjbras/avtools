from __future__ import annotations

from typing import Iterable, Mapping

from avtools.timeseries.models import MetricSample
from avtools.timeseries import metrics as m

from avtools.snmp.client import PingResult, ProbeResult, QueryResult


def _labels(equipmentno: str) -> Mapping[str, str]:
    """Prometheus/OTLP labels for a device (keep cardinality low)."""
    return {"equipmentno": equipmentno}


def to_float(value: object) -> float | None:
    """Best-effort conversion for numeric-ish values."""
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        try:
            s = str(value)
            digits = "".join(ch for ch in s if (ch.isdigit() or ch == "."))
            return float(digits) if digits else None
        except Exception:
            return None


def encode_ping(results: Iterable[PingResult]) -> list[MetricSample]:
    """Encode raw ping results into MetricSample objects."""
    out: list[MetricSample] = []
    for r in results:
        if not r.equipmentno:
            continue
        labels = _labels(r.equipmentno)
        out.append(MetricSample(name=m.PING_CHECK_STATUS, value=r.up, labels=labels))
        if r.rtt_ms is not None:
            out.append(
                MetricSample(
                    name=m.PING_CHECK_RTT_MS, value=float(r.rtt_ms), labels=labels
                )
            )
    return out


def encode_probe(results: Iterable[ProbeResult]) -> list[MetricSample]:
    """Encode raw SNMP probe results into MetricSample objects."""
    out: list[MetricSample] = []
    for r in results:
        if not r.equipmentno:
            continue
        out.append(
            MetricSample(
                name=m.SNMP_PROBE_STATUS, value=r.up, labels=_labels(r.equipmentno)
            )
        )
    return out


def encode_queries(results: Iterable[QueryResult]) -> list[MetricSample]:
    """Encode raw SNMP query results into MetricSample objects.

    Policy:
      - Only *numeric* monitoring signals go to Prometheus.
      - Text/identity-like data (e.g. firmware) is handled in Postgres monitoring.
    """
    out: list[MetricSample] = []
    for r in results:
        if not r.equipmentno:
            continue
        labels = _labels(r.equipmentno)

        # Projector stats (raw keys from projector handler):
        # - uptime_seconds: float
        # - lamp_hours: int
        # - power_status: string/int-ish
        # - firmware: string (DO NOT export to Prometheus here)
        if r.query == "projector":
            lamp = r.stats.get("lamp_hours")
            if lamp is not None:
                n = to_float(lamp)
                if n is not None:
                    out.append(
                        MetricSample(
                            name=m.PROJECTOR_QUERY_LAMP_HOURS, value=n, labels=labels
                        )
                    )

            uptime_s = r.stats.get("uptime_seconds")
            if uptime_s is not None:
                n = to_float(uptime_s)
                if n is not None:
                    out.append(
                        MetricSample(
                            name=m.PROJECTOR_QUERY_UPTIME_SECONDS,
                            value=n,
                            labels=labels,
                        )
                    )
            continue
        # Unknown query families: ignore by default to avoid leaking cardinality/semantics.
        # Add explicit encoders as new query families are introduced.
    return out


def encode_all(
    *,
    ping: Iterable[PingResult] = (),
    probe: Iterable[ProbeResult] = (),
    queries: Iterable[QueryResult] = (),
) -> list[MetricSample]:
    """Convenience combiner for the SNMP pipeline."""
    samples: list[MetricSample] = []
    samples.extend(encode_ping(ping))
    samples.extend(encode_probe(probe))
    samples.extend(encode_queries(queries))
    return samples
