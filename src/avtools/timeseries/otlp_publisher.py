"""OTLP metrics publisher for AV Tools.

Transport (this is the important part):
    Metrics go out over OTLP/**HTTP** to ``https://monit-otlp.cern.ch:4319/v1/metrics``
    with HTTP Basic auth and TLS — see :mod:`avtools.otlp.http_transport`.

    The previous transport was OTLP/**gRPC** on :4316 with ``insecure=True``,
    i.e. cleartext carrying CERN equipment numbers and device IPs for ~1375
    devices. The MONIT gRPC ports offer no TLS listener at all, so encrypting
    that path was impossible; moving to OTLP/HTTP is the fix. The gRPC path is
    kept as a **deprecated escape hatch** (``protocol="grpc"`` /
    ``--otlp-protocol grpc`` / ``MONIT_OTLP_PROTOCOL=grpc``) so a rollback needs
    no code change — but it can only ever run in plaintext.

Layer 1 — OTel resource attributes (on every ResourceMetrics envelope):
    service.name        Identifies the service ("avtools" by default).
    service.instance.id STABLE instance identity (maps to the `instance` label):
                        an explicit override, else the shard for an Indexed Job,
                        else the hostname. NOT the ephemeral k8s pod name — see
                        `_stable_instance_id`.
    service.version     Package version from avtools.__version__.
    service.namespace   Top-level organisational grouping ("itdcim").

Layer 2 — global metric labels (merged into every MetricSample before export):
    Passed via the ``metric_labels`` constructor argument.  These labels are
    merged into every datapoint so that MONIT/Mimir can route and filter by
    environment, hostgroup, and job without joins.  A collision between a
    global label key and a per-device sample label key raises
    ``OTLPPublishError`` at publish time — the same guard timeseries-dip
    applies in its ``_apply_metric_labels`` step.

Strings:
    Prometheus is numeric.  Publish strings via *_info metrics where the string
    is carried as a label and the metric value is 1.
"""

from __future__ import annotations

import base64
import os
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
from avtools.otlp import metrics_encoder
from avtools.otlp.endpoints import grpc_target, normalize_metrics_endpoint
from avtools.otlp.http_transport import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_PROTOBUF,
    OTLPHttpTransport,
)
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
#
# force_flush() is worse than that, in fact: it has been observed returning True
# while the exporter logged "Failed to export ... UNAVAILABLE". That is why the
# OTLP/HTTP path never asks a flush whether the export worked — it reads MONIT's
# HTTP response status. The retry budget below is shared by both transports so
# the timing envelope is identical either way.
_FLUSH_ATTEMPTS = 3
_FLUSH_BACKOFF_S = 0.75
_FLUSH_TIMEOUT_MS = 10_000

# Transport selection tokens (``--otlp-protocol`` / ``MONIT_OTLP_PROTOCOL``).
PROTOCOL_HTTP = "http"
PROTOCOL_GRPC = "grpc"
DEFAULT_PROTOCOL = PROTOCOL_HTTP
SUPPORTED_PROTOCOLS = (PROTOCOL_HTTP, PROTOCOL_GRPC)

# OTLP/HTTP payload encodings (``--otlp-encoding``). protobuf is the default:
# at ~42,900 samples/hr it is materially smaller on the wire than JSON, and the
# protobuf classes already ship with the OTLP exporter dependency. JSON exists
# to make a payload readable when debugging against MONIT.
ENCODING_PROTOBUF = "protobuf"
ENCODING_JSON = "json"
DEFAULT_ENCODING = ENCODING_PROTOBUF
SUPPORTED_ENCODINGS = (ENCODING_PROTOBUF, ENCODING_JSON)

_CONTENT_TYPES = {
    ENCODING_PROTOBUF: CONTENT_TYPE_PROTOBUF,
    ENCODING_JSON: CONTENT_TYPE_JSON,
}


def _stable_instance_id(service_name: str) -> str:
    """Return a STABLE OTel ``service.instance.id`` (it maps to the Prometheus /
    Mimir ``instance`` label, so it is part of every series' identity).

    On Kubernetes ``socket.gethostname()`` is the *ephemeral pod name* — a new
    value every CronJob cycle, times N shard pods. That mints a fresh set of
    series on every run, so MONIT/Mimir's active-series count climbs until the
    tenant limit is hit and samples get dropped (the ~1h on/off gaps seen ONLY on
    k8s; the Puppet VM's stable hostname never trips it). Prefer a stable identity:

      * ``$AVTOOLS_INSTANCE_ID`` if set (explicit override, e.g. per job),
      * else the shard for an Indexed Job (``<service>-shard-<JOB_COMPLETION_INDEX>``),
      * else the hostname (stable on the monolith VM -- behaviour unchanged there).
    """
    override = os.environ.get("AVTOOLS_INSTANCE_ID")
    if override:
        return override
    shard = os.environ.get("JOB_COMPLETION_INDEX")  # auto-set on Indexed Jobs (snmp shards)
    if shard not in (None, ""):
        return f"{service_name}-shard-{shard}"
    return socket.gethostname()


def normalize_protocol(protocol: str | None) -> str:
    """Validate and normalise an OTLP protocol token.

    Args:
        protocol: ``"http"``, ``"grpc"``, or ``None``/empty for the default.

    Returns:
        A member of :data:`SUPPORTED_PROTOCOLS`.

    Raises:
        ValueError: On an unknown token, naming the accepted values.
    """
    token = (protocol or DEFAULT_PROTOCOL).strip().lower()
    if token not in SUPPORTED_PROTOCOLS:
        raise ValueError(
            f"Unknown OTLP protocol {protocol!r}; expected one of {list(SUPPORTED_PROTOCOLS)}."
        )
    return token


def normalize_encoding(encoding: str | None) -> str:
    """Validate and normalise an OTLP/HTTP payload encoding token.

    Args:
        encoding: ``"protobuf"``, ``"json"``, or ``None``/empty for the default.

    Returns:
        A member of :data:`SUPPORTED_ENCODINGS`.

    Raises:
        ValueError: On an unknown token, naming the accepted values.
    """
    token = (encoding or DEFAULT_ENCODING).strip().lower()
    if token not in SUPPORTED_ENCODINGS:
        raise ValueError(
            f"Unknown OTLP encoding {encoding!r}; expected one of {list(SUPPORTED_ENCODINGS)}."
        )
    return token


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
    """Publish gauge metrics to MONIT over OTLP/HTTP (or, deprecated, OTLP/gRPC).

    This implementation is designed for a periodic batch job: collect metrics,
    publish once, exit.

    Args:
        endpoint:             MONIT OTLP endpoint. Either a full OTLP/HTTP URL
                              (``https://host:4319/v1/metrics``) or the legacy
                              OTLP/gRPC ``host:port`` form, which is migrated
                              automatically — the deployed charts still pass it.
        tenant:               MONIT tenant name (Basic-auth username).
        password:             MONIT tenant password (Basic-auth password).
        service_name:         OTel ``service.name`` resource attribute.
        export_interval_s:    Metric-reader export interval, gRPC path only.
        timeout_s:            Per-export timeout in seconds.
        ca_file:              Optional PEM CA bundle. On the HTTP path this
                              defaults to the CERN chain bundled in the wheel,
                              because MONIT does not send its intermediate.
        insecure:             Plaintext gRPC. Only meaningful for
                              ``protocol="grpc"``; ignored (with a warning) on
                              the HTTP path, which is always TLS-verified.
        metric_labels:        Global deployment labels merged into every
                              datapoint (Layer 2).  Keys must not overlap
                              with per-device sample label keys.
        instance_id:          Explicit ``service.instance.id`` override.
        protocol:             ``"http"`` (default) or ``"grpc"`` (deprecated).
        encoding:             OTLP/HTTP payload encoding: ``"protobuf"``
                              (default) or ``"json"``.
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
        instance_id: str | None = None,
        protocol: str = DEFAULT_PROTOCOL,
        encoding: str = DEFAULT_ENCODING,
    ) -> None:
        if not endpoint or ":" not in endpoint:
            raise ValueError("OTLP endpoint must be in 'host:port' form")

        self.protocol = normalize_protocol(protocol)
        self.encoding = normalize_encoding(encoding)

        # Store global metric labels (Layer 2).
        self._metric_labels: dict[str, str] = dict(metric_labels or {})
        self._service_name = service_name

        # Layer 1 — OTel resource attributes. Identical for both transports.
        self._resource_attributes: dict[str, str] = {
            "service.name": service_name,
            # STABLE instance id (not the ephemeral k8s pod name) -> bounded
            # `instance`-label cardinality in Mimir. See _stable_instance_id.
            "service.instance.id": instance_id or _stable_instance_id(service_name),
            "service.version": _AVTOOLS_VERSION,
            "service.namespace": "itdcim",
        }

        self._stores: Dict[str, _Store] = {}
        self._instruments: Dict[str, bool] = {}

        if self.protocol == PROTOCOL_GRPC:
            self._init_grpc(
                endpoint=endpoint,
                tenant=tenant,
                password=password,
                service_name=service_name,
                export_interval_s=export_interval_s,
                timeout_s=timeout_s,
                ca_file=ca_file,
                insecure=insecure,
            )
        else:
            self._init_http(
                endpoint=endpoint,
                tenant=tenant,
                password=password,
                timeout_s=timeout_s,
                ca_file=ca_file,
                insecure=insecure,
            )

    # ------------------------------------------------------------------
    # Transport setup
    # ------------------------------------------------------------------

    def _init_http(
        self,
        *,
        endpoint: str,
        tenant: str,
        password: str,
        timeout_s: float,
        ca_file: str | None,
        insecure: bool,
    ) -> None:
        """Wire up the OTLP/HTTP transport (the default, TLS-verified path)."""
        self._provider = None
        self._meter = None

        try:
            self._endpoint = normalize_metrics_endpoint(endpoint)
        except ValueError as exc:
            raise OTLPPublishError(str(exc)) from exc

        if self._endpoint != endpoint:
            # The deployed charts still pass the OTLP/gRPC 'host:port' form; say
            # out loud what it was migrated to, so an operator reading a CronJob
            # log can see which URL actually received the metrics.
            _log.info(
                "otlp_endpoint_migrated",
                given=endpoint,
                using=self._endpoint,
                detail=(
                    "MONIT_OTLP_ENDPOINT is in the legacy OTLP/gRPC 'host:port' form; "
                    "derived the OTLP/HTTP URL from it"
                ),
            )

        if insecure:
            # MONIT_OTLP_INSECURE=true is still set by the deployed charts. On
            # the HTTP path it is deliberately NOT honoured: honouring it would
            # keep sending equipment numbers and device IPs in cleartext, which
            # is the whole reason this transport exists. Rollback is
            # --otlp-protocol grpc, not a silent downgrade.
            _log.warning(
                "otlp_insecure_ignored",
                endpoint=self._endpoint,
                detail=(
                    "MONIT_OTLP_INSECURE / --otlp-insecure has no effect on the OTLP/HTTP "
                    "transport, which always verifies TLS; use --otlp-protocol grpc to fall "
                    "back to the deprecated plaintext gRPC path"
                ),
            )

        try:
            self._transport = OTLPHttpTransport(
                endpoint=self._endpoint,
                tenant=tenant,
                password=password,
                ca_file=ca_file,
                timeout_s=timeout_s,
                max_attempts=_FLUSH_ATTEMPTS,
                backoff_s=_FLUSH_BACKOFF_S,
            )
            self._proto = metrics_encoder.load_otlp_proto()
        except Exception as exc:
            raise OTLPPublishError("Failed to initialize OTLP exporter") from exc

    def _init_grpc(
        self,
        *,
        endpoint: str,
        tenant: str,
        password: str,
        service_name: str,
        export_interval_s: float,
        timeout_s: float,
        ca_file: str | None,
        insecure: bool,
    ) -> None:
        """Wire up the deprecated OTLP/gRPC transport (plaintext-only in practice).

        Unchanged from the pre-OTLP/HTTP implementation on purpose: this is the
        rollback path, so it must behave exactly as it did in production.
        """
        self._transport = None
        self._proto = None
        self._endpoint = grpc_target(endpoint) if "://" in endpoint else endpoint

        _log.warning(
            "otlp_grpc_transport_deprecated",
            endpoint=self._endpoint,
            insecure=insecure,
            detail=(
                "OTLP/gRPC is deprecated: MONIT offers no TLS listener on the gRPC "
                "ports, so this export runs in cleartext. Use --otlp-protocol http "
                "(the default) to publish over TLS on :4319"
            ),
        )

        try:
            token = base64.b64encode(f"{tenant}:{password}".encode()).decode()
            headers = {"authorization": f"Basic {token}"}

            if insecure:
                exporter = OTLPMetricExporter(
                    endpoint=self._endpoint,
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
                    endpoint=self._endpoint,
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

        resource = Resource.create(dict(self._resource_attributes))

        self._provider = MeterProvider(metric_readers=[reader], resource=resource)
        metrics.set_meter_provider(self._provider)
        self._meter = metrics.get_meter(service_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(self, samples: Iterable[MetricSample]) -> bool:
        """Publish one batch of metric samples to MONIT.

        Args:
            samples: Iterable of :class:`~avtools.timeseries.models.MetricSample`.

        Returns:
            True if the export to MONIT was confirmed (a 2xx OTLP/HTTP response,
            or a successful force_flush on the deprecated gRPC path, within the
            retry budget); False if it could not be confirmed (MONIT
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

        if self.protocol == PROTOCOL_GRPC:
            confirmed, last_exc = self._export_grpc(grouped)
        else:
            confirmed, last_exc = self._export_http(grouped)

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

    # ------------------------------------------------------------------
    # Transports
    # ------------------------------------------------------------------

    def _export_http(
        self, grouped: Dict[str, Dict[_LabelsKey, float]]
    ) -> tuple[bool, Exception | None]:
        """Export one grouped batch over OTLP/HTTP.

        Confirmation comes from MONIT's HTTP response status and nothing else.

        Args:
            grouped: ``{metric_name: {sorted_label_tuple: value}}``.

        Returns:
            ``(confirmed, last_error)``.
        """
        if not grouped:
            # Nothing to send. The gRPC path also had nothing to flush here, and
            # an empty ResourceMetrics envelope is pointless traffic.
            return True, None

        content_type = _CONTENT_TYPES[self.encoding]
        try:
            request = metrics_encoder.build_metrics_request(
                self._proto,
                grouped=grouped,
                resource_attributes=self._resource_attributes,
                scope_name=self._service_name,
                metric_labels=self._metric_labels,
                now_ns=time.time_ns(),
            )
            payload = metrics_encoder.serialize_request(request, content_type=content_type)
        except Exception as exc:
            raise OTLPPublishError(f"Failed to encode OTLP metrics payload: {exc}") from exc

        confirmed, last_exc = self._transport.post_with_retry(payload, content_type=content_type)
        if confirmed:
            _log.info(
                "otlp_http_export_ok",
                endpoint=self._endpoint,
                metrics=len(grouped),
                datapoints=metrics_encoder.datapoint_count(grouped),
                payload_bytes=len(payload),
                encoding=self.encoding,
            )
        return confirmed, last_exc

    def _export_grpc(
        self, grouped: Dict[str, Dict[_LabelsKey, float]]
    ) -> tuple[bool, Exception | None]:
        """Export one grouped batch over the deprecated plaintext OTLP/gRPC path.

        Registers observable gauges on the SDK meter, then forces a single
        deterministic flush and retries a bounded number of times. Behaviour is
        byte-for-byte what production ran before the OTLP/HTTP migration.

        Args:
            grouped: ``{metric_name: {sorted_label_tuple: value}}``.

        Returns:
            ``(confirmed, last_error)``.
        """
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

        return confirmed, last_exc
