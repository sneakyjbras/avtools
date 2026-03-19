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
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter,
)

from avtools.timeseries.models import MetricSample
from avtools.timeseries.metrics import METRIC_META

from avtools.exception.errors import OTLPPublishError


@dataclass(slots=True)
class _Store:
    # map: labels_tuple -> value
    values: Dict[Tuple[Tuple[str, str], ...], float]


class OTLPMetricsPublisher:
    """Publish gauge metrics to MONIT using OTLP/gRPC.

    This implementation is designed for a periodic batch job: collect metrics,
    publish once, exit.

    Security:
      - Supports both TLS (insecure=False) and plaintext (insecure=True) transports.
      - Authentication is Basic auth via gRPC metadata header.

    Labels:
      - Keep labels minimal. We always include `equipmentno`.

    Strings:
      - Prometheus is numeric; publish strings via *_info metrics where the string
        is carried as a label and the metric value is 1.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        tenant: str,
        password: str,
        service_name: str = "avtools",
        export_interval_s: float = 1.0,
        timeout_s: float = 10.0,
        ca_file: str | None = None,
        insecure: bool = False,
    ) -> None:
        if not endpoint or ":" not in endpoint:
            raise ValueError("OTLP endpoint must be in 'host:port' form")

        try:
            token = base64.b64encode(f"{tenant}:{password}".encode()).decode()
            headers = {"authorization": f"Basic {token}"}

            # Transport selection:
            # - insecure=True  -> plaintext gRPC (no TLS)
            # - insecure=False -> TLS gRPC (optionally with a custom CA bundle)
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
                    with open(ca_file, "rb") as f:
                        root_certs = f.read()
                    credentials = grpc.ssl_channel_credentials(
                        root_certificates=root_certs
                    )

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

        resource = Resource.create(
            {
                "service.name": service_name,
                "service.instance.id": socket.gethostname(),
            }
        )

        self._provider = MeterProvider(metric_readers=[reader], resource=resource)
        metrics.set_meter_provider(self._provider)
        self._meter = metrics.get_meter(service_name)

        self._stores: Dict[str, _Store] = {}
        self._instruments: Dict[str, bool] = {}

    def publish(self, samples: Iterable[MetricSample]) -> None:
        # Group samples by metric name
        grouped: Dict[str, Dict[Tuple[Tuple[str, str], ...], float]] = {}
        for s in samples:
            labels_tuple = tuple(sorted((str(k), str(v)) for k, v in s.labels.items()))
            metric_name = str(s.name)
            grouped.setdefault(metric_name, {})[labels_tuple] = float(s.value)

        for metric_name, values in grouped.items():
            store = self._stores.setdefault(metric_name, _Store(values={}))
            store.values.clear()
            store.values.update(values)

            if metric_name not in self._instruments:
                meta = METRIC_META.get(metric_name)
                desc = str(meta.description or "") if meta else ""
                unit = str(meta.unit or "") if meta else ""

                def make_cb(
                    name: str,
                ) -> Callable[[CallbackOptions], Iterable[Observation]]:
                    def cb(options: CallbackOptions):
                        st = self._stores.get(name)
                        if not st:
                            return
                        for lt, val in st.values.items():
                            yield Observation(val, dict(lt))

                    return cb

                self._meter.create_observable_gauge(
                    metric_name,
                    callbacks=[make_cb(metric_name)],
                    description=desc,
                    unit=unit,
                )
                self._instruments[metric_name] = True

        # Force export and shutdown
        try:
            # Give the reader a moment to call callbacks and export.
            self._provider.force_flush()
            time.sleep(1.1)
            self._provider.force_flush()
        except Exception as e:
            raise OTLPPublishError(str(e)) from e
        finally:
            self._provider.shutdown()
