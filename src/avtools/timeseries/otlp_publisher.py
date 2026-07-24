"""OTLP/gRPC metrics publisher for AV Tools.

Layer 1 — OTel resource attributes (on every ResourceMetrics envelope):
    service.name        Identifies the service ("avtools" by default).
    service.instance.id Hostname of the publishing process.
    service.version     Package version from avtools.__version__.
    service.namespace   Top-level organisational grouping ("itdcim").

Layer 2 — global metric labels (merged into every MetricSample before export):
    Passed via the ``metric_labels`` constructor argument.  These labels are
    merged into every Observation so that MONIT/Mimir can route and filter by
    environment, hostgroup, and job without joins.  A collision between a
    global label key and a per-device sample label key raises
    ``OTLPPublishError`` at publish time — the same guard timeseries-dip
    applies in its ``_apply_metric_labels`` step.

Security:
    Supports both TLS (insecure=False) and plaintext (insecure=True) transports.
    Authentication is HTTP Basic auth via gRPC metadata header.

Strings:
    Prometheus is numeric.  Publish strings via *_info metrics where the string
    is carried as a label and the metric value is 1.
"""

from __future__ import annotations

import base64
import socket
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Mapping, Tuple

import grpc
from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

import structlog

from avtools import __version__ as _AVTOOLS_VERSION
from avtools.timeseries.models import MetricSample
from avtools.timeseries.metrics import METRIC_META
from avtools.exception.errors import OTLPPublishError

_log = structlog.get_logger(__name__)

# Batch-job export reliability. MeterProvider.force_flush() returns False (it
# does NOT raise) when the export times out or MONIT rejects it. The old code
# ignored that return, so a dropped batch still logged status=ok — the silent
# failure mode behind the ~1h gaps seen only on k8s (8 shard pods exporting
# concurrently) and never on the single-stream Puppet VM. Retry a bounded number
# of times before giving up.
_FLUSH_ATTEMPTS = 3
_FLUSH_BACKOFF_S = 0.75
_FLUSH_TIMEOUT_MS = 10_000

# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

_LabelsKey = Tuple[Tuple[str, str], ...]


@dataclass(slots=True)
class _Store:
    """Per-metric value store: labels_tuple -> numeric value."""

    values: Dict[_LabelsKey, float]


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------


class OTLPMetricsPublisher:
    """Publish gauge metrics to MONIT using OTLP/gRPC.

    This implementation is designed for a periodic batch job: collect metrics,
    publish once, exit.

    Args:
        endpoint:             OTLP gRPC endpoint in ``host:port`` form.
        tenant:               MONIT tenant name (Basic-auth username).
        password:             MONIT tenant password (Basic-auth password).
        service_name:         OTel ``service.name`` resource attribute.
        export_interval_s:    Metric-reader export interval in seconds.
        timeout_s:            gRPC call timeout in seconds.
        ca_file:              Optional path to a PEM CA bundle for TLS.
        insecure:             If True, use plaintext gRPC (no TLS).
        metric_labels:        Global deployment labels merged into every
                              Observation (Layer 2).  Keys must not overlap
                              with per-device sample label keys.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        tenant: str,
        password: str,
        service_name: str = "avtools",
        # Batch job: the metrics are only ready after publish() runs, and the
        # single export is driven by force_flush(). A short periodic interval
        # just adds redundant, concurrent export traffic to MONIT (8 shard pods
        # at once) for no benefit — so the reader interval is set well beyond a
        # job's lifetime and force_flush() is the sole, deterministic export.
        export_interval_s: float = 300.0,
        timeout_s: float = 10.0,
        ca_file: str | None = None,
        insecure: bool = False,
        metric_labels: Mapping[str, str] | None = None,
    ) -> None:
        if not endpoint or ":" not in endpoint:
            raise ValueError("OTLP endpoint must be in 'host:port' form")

        # Store global metric labels (Layer 2).
        self._metric_labels: dict[str, str] = dict(metric_labels or {})
        self._endpoint = endpoint

        try:
            token = base64.b64encode(f"{tenant}:{password}".encode()).decode()
            headers = {"authorization": f"Basic {token}"}

            if insecure:
                exporter = OTLPMetricExporter(
                    endpoint=endpoint,
                    insecure=True,
                    headers=headers,
                    timeout=timeout_s,
                )
            else:
                credentials = None
                if ca_file:
                    with open(ca_file, "rb") as fh:
                        root_certs = fh.read()
                    credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
                exporter = OTLPMetricExporter(
                    endpoint=endpoint,
                    insecure=False,
                    headers=headers,
                    timeout=timeout_s,
                    credentials=credentials,
                )
        except Exception as exc:
            raise OTLPPublishError("Failed to initialize OTLP exporter") from exc

        reader = PeriodicExportingMetricReader(
            exporter, export_interval_millis=int(export_interval_s * 1000)
        )

        # Layer 1 — OTel resource attributes.
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.instance.id": socket.gethostname(),
                "service.version": _AVTOOLS_VERSION,
                "service.namespace": "itdcim",
            }
        )

        self._provider = MeterProvider(metric_readers=[reader], resource=resource)
        metrics.set_meter_provider(self._provider)
        self._meter = metrics.get_meter(service_name)

        self._stores: Dict[str, _Store] = {}
        self._instruments: Dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(self, samples: Iterable[MetricSample]) -> bool:
        """Publish one batch of metric samples through OTLP/gRPC.

        Args:
            samples: Iterable of :class:`~avtools.timeseries.models.MetricSample`.

        Returns:
            True if the export to MONIT was confirmed (force_flush succeeded
            within the retry budget); False if it could not be confirmed (MONIT
            unreachable / rate-limiting). A False return is logged loudly
            (``otlp_export_unconfirmed``) so the caller can surface it — the
            collection + DB write already succeeded, so an unconfirmed metric
            export must not crash the job.

        Raises:
            OTLPPublishError: If a global metric label key collides with a
                per-device sample label key (a config error, fail loudly).
        """
        # Group samples by metric name, coercing label values to strings.
        grouped: Dict[str, Dict[_LabelsKey, float]] = {}
        for s in samples:
            labels_tuple: _LabelsKey = tuple(
                sorted((str(k), str(v)) for k, v in s.labels.items() if v is not None)
            )
            metric_name = str(s.name)
            grouped.setdefault(metric_name, {})[labels_tuple] = float(s.value)

        # Layer 2 collision guard — fail loudly before any data is stored.
        if self._metric_labels:
            global_keys = set(self._metric_labels)
            for metric_name, values in grouped.items():
                sample_keys: set[str] = set()
                for lt in values:
                    sample_keys.update(k for k, _ in lt)
                collisions = global_keys & sample_keys
                if collisions:
                    raise OTLPPublishError(
                        f"Global metric labels conflict with sample labels for "
                        f"'{metric_name}': {', '.join(sorted(collisions))}. "
                        f"Rename either the global label or the device-level label."
                    )

        # Populate stores and register observable gauges.
        for metric_name, values in grouped.items():
            store = self._stores.setdefault(metric_name, _Store(values={}))
            store.values.clear()
            store.values.update(values)

            if metric_name not in self._instruments:
                meta = METRIC_META.get(metric_name)
                desc = str(meta.description or "") if meta else ""
                unit = str(meta.unit or "") if meta else ""

                def _make_cb(
                    name: str,
                ) -> Callable[[CallbackOptions], Iterable[Observation]]:
                    def cb(options: CallbackOptions) -> Iterable[Observation]:
                        st = self._stores.get(name)
                        if not st:
                            return
                        for lt, val in st.values.items():
                            # Merge Layer-2 global labels into every observation.
                            attrs = {**self._metric_labels, **dict(lt)}
                            yield Observation(val, attrs)

                    return cb

                self._meter.create_observable_gauge(
                    metric_name,
                    callbacks=[_make_cb(metric_name)],
                    description=desc,
                    unit=unit,
                )
                self._instruments[metric_name] = True

        # Force a single, deterministic export and CONFIRM it landed. Retry a
        # bounded number of times on a False/errored flush, then report the
        # outcome. shutdown() always runs so the process can exit cleanly.
        confirmed = False
        last_exc: Exception | None = None
        try:
            for attempt in range(1, _FLUSH_ATTEMPTS + 1):
                try:
                    if self._provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS):
                        confirmed = True
                        break
                except Exception as exc:  # transient exporter / gRPC failure
                    last_exc = exc
                if attempt < _FLUSH_ATTEMPTS:
                    time.sleep(_FLUSH_BACKOFF_S * attempt)
        finally:
            self._provider.shutdown()

        if not confirmed:
            _log.error(
                "otlp_export_unconfirmed",
                endpoint=self._endpoint,
                attempts=_FLUSH_ATTEMPTS,
                error=str(last_exc) if last_exc else None,
                detail=(
                    "force_flush did not confirm the export; MONIT may be "
                    "unreachable or rate-limiting the concurrent shard exports — "
                    "this cycle's metrics were likely dropped"
                ),
            )
        return confirmed
