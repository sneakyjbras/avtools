"""Encode AV Tools metric samples into an OTLP ``ExportMetricsServiceRequest``.

The OTLP/gRPC path builds this payload indirectly, by handing observable gauges
to the OpenTelemetry SDK and letting ``force_flush()`` serialize them. The
OTLP/HTTP path has no SDK in the loop, so it builds the same envelope here:

    ResourceMetrics(resource attrs)     <- Layer 1 (service.name/instance/version/namespace)
      ScopeMetrics(scope.name=service)
        Metric(name, description, unit)
          Gauge.NumberDataPoint(as_double, time_unix_nano, attributes)

Attributes on each datapoint are the Layer-2 global deployment labels merged with
the per-sample labels — exactly the merge the gRPC observable-gauge callback did,
in the same precedence order (sample labels last), so a payload built here is
wire-equivalent to what MONIT used to receive.

Encoding: protobuf by default (see :func:`serialize_request`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple

from avtools.otlp.http_transport import CONTENT_TYPE_JSON, CONTENT_TYPE_PROTOBUF
from avtools.timeseries.metrics import METRIC_META

LabelsKey = Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class _OtlpProtoRuntime:
    """Protobuf classes loaded from ``opentelemetry-proto``."""

    ExportMetricsServiceRequest: object
    AnyValue: object
    KeyValue: object
    Resource: object


class OTLPEncodeError(RuntimeError):
    """Raised when an OTLP payload cannot be built or serialized."""


def load_otlp_proto() -> _OtlpProtoRuntime:
    """Load the protobuf message classes needed to build a metrics export.

    Returns:
        Container with the protobuf classes.

    Raises:
        OTLPEncodeError: If ``opentelemetry-proto`` is not installed. It ships as
            a dependency of ``opentelemetry-exporter-otlp-proto-grpc``, which is a
            hard runtime requirement, so this should only fire on a broken install.
    """
    try:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
            ExportMetricsServiceRequest,
        )
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
        from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    except ImportError as exc:  # pragma: no cover - depends on a broken install
        raise OTLPEncodeError(
            "opentelemetry-proto is missing; it ships with "
            "opentelemetry-exporter-otlp-proto-grpc. Reinstall the avtools wheel."
        ) from exc

    return _OtlpProtoRuntime(
        ExportMetricsServiceRequest=ExportMetricsServiceRequest,
        AnyValue=AnyValue,
        KeyValue=KeyValue,
        Resource=Resource,
    )


def build_metrics_request(
    proto: _OtlpProtoRuntime,
    *,
    grouped: Mapping[str, Mapping[LabelsKey, float]],
    resource_attributes: Mapping[str, str],
    scope_name: str,
    metric_labels: Mapping[str, str],
    now_ns: int,
):
    """Build one ``ExportMetricsServiceRequest`` from grouped metric samples.

    Args:
        proto: Runtime from :func:`load_otlp_proto`.
        grouped: ``{metric_name: {sorted_label_tuple: value}}``, the same shape
            the publisher already builds for the gRPC path.
        resource_attributes: Layer-1 OTel resource attributes.
        scope_name: Instrumentation scope name (the OTel meter name).
        metric_labels: Layer-2 global deployment labels merged into every
            datapoint.
        now_ns: Timestamp applied to every datapoint, in nanoseconds.

    Returns:
        The populated protobuf request message.
    """
    request = proto.ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add()
    resource_metrics.resource.CopyFrom(
        proto.Resource(attributes=_key_values(proto, resource_attributes))
    )

    scope_metrics = resource_metrics.scope_metrics.add()
    scope_metrics.scope.name = scope_name

    for metric_name, values in grouped.items():
        meta = METRIC_META.get(metric_name)
        metric = scope_metrics.metrics.add()
        metric.name = str(metric_name)
        metric.description = str(getattr(meta, "description", "") or "") if meta else ""
        metric.unit = str(getattr(meta, "unit", "") or "") if meta else ""

        for labels_key, value in values.items():
            attributes = {**metric_labels, **dict(labels_key)}
            data_point = metric.gauge.data_points.add()
            data_point.time_unix_nano = now_ns
            # Values are floats throughout the pipeline (the publisher coerces
            # with float()), so as_double keeps the wire representation the SDK
            # produced for the same samples.
            data_point.as_double = float(value)
            data_point.attributes.extend(_key_values(proto, attributes))

    return request


def serialize_request(request, *, content_type: str = CONTENT_TYPE_PROTOBUF) -> bytes:
    """Serialize an OTLP request message for the wire.

    Args:
        request: A protobuf message built by :func:`build_metrics_request`.
        content_type: ``application/x-protobuf`` (default) or ``application/json``.

    Returns:
        Encoded request body.

    Raises:
        OTLPEncodeError: If ``content_type`` is not an OTLP/HTTP encoding.
    """
    if content_type == CONTENT_TYPE_PROTOBUF:
        return request.SerializeToString()
    if content_type == CONTENT_TYPE_JSON:
        from google.protobuf import json_format

        # Canonical proto3 JSON (lowerCamelCase field names) is exactly what the
        # OTLP/JSON specification prescribes, and what MONIT answered 200 to.
        return json_format.MessageToJson(request, indent=0).encode("utf-8")
    raise OTLPEncodeError(
        f"Unsupported OTLP content type {content_type!r}; expected "
        f"{CONTENT_TYPE_PROTOBUF!r} or {CONTENT_TYPE_JSON!r}."
    )


def datapoint_count(grouped: Mapping[str, Mapping[LabelsKey, float]]) -> int:
    """Total number of datapoints in a grouped sample map (for logging)."""
    return sum(len(values) for values in grouped.values())


def _key_values(proto: _OtlpProtoRuntime, attributes: Mapping[str, str]) -> list[object]:
    """Convert an attribute mapping into sorted OTLP ``KeyValue`` messages."""
    return [
        proto.KeyValue(key=str(key), value=proto.AnyValue(string_value=str(value)))
        for key, value in sorted(attributes.items())
    ]
