"""OTLP metrics payload encoding.

The payload built here replaces what the OpenTelemetry SDK used to serialize on
the gRPC path, so the envelope must carry the same things: Layer-1 resource
attributes, the scope name, METRIC_META descriptions/units, and Layer-2 global
labels merged into every datapoint with the per-sample labels winning.
"""

from __future__ import annotations

import json

import pytest

from avtools.otlp import metrics_encoder
from avtools.otlp.http_transport import CONTENT_TYPE_JSON, CONTENT_TYPE_PROTOBUF
from avtools.timeseries.metrics import METRIC_META, PING_CHECK_RTT_MS, PING_CHECK_STATUS

_RESOURCE = {
    "service.name": "avtools",
    "service.instance.id": "avtools-shard-2",
    "service.version": "1.11.1",
    "service.namespace": "itdcim",
}


@pytest.fixture
def proto():
    return metrics_encoder.load_otlp_proto()


def _build(proto, grouped, metric_labels=None):
    return metrics_encoder.build_metrics_request(
        proto,
        grouped=grouped,
        resource_attributes=_RESOURCE,
        scope_name="avtools",
        metric_labels=metric_labels or {},
        now_ns=1_700_000_000_000_000_000,
    )


def _attrs(data_point) -> dict[str, str]:
    return {kv.key: kv.value.string_value for kv in data_point.attributes}


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def test_resource_attributes_land_on_the_envelope(proto) -> None:
    request = _build(proto, {PING_CHECK_STATUS: {(("equipmentno", "EQ1"),): 1.0}})
    resource = request.resource_metrics[0].resource
    assert {kv.key: kv.value.string_value for kv in resource.attributes} == _RESOURCE


def test_scope_name_is_the_service_name(proto) -> None:
    request = _build(proto, {PING_CHECK_STATUS: {(("equipmentno", "EQ1"),): 1.0}})
    assert request.resource_metrics[0].scope_metrics[0].scope.name == "avtools"


def test_metric_description_and_unit_come_from_metric_meta(proto) -> None:
    request = _build(proto, {PING_CHECK_RTT_MS: {(("equipmentno", "EQ1"),): 12.5}})
    metric = request.resource_metrics[0].scope_metrics[0].metrics[0]
    assert metric.name == PING_CHECK_RTT_MS
    assert metric.description == METRIC_META[PING_CHECK_RTT_MS].description
    assert metric.unit == "ms"


def test_metric_without_meta_gets_empty_description_and_unit(proto) -> None:
    request = _build(proto, {"avtools_unregistered_metric": {(): 1.0}})
    metric = request.resource_metrics[0].scope_metrics[0].metrics[0]
    assert metric.description == ""
    assert metric.unit == ""


def test_metric_with_meta_but_no_unit_gets_an_empty_unit(proto) -> None:
    request = _build(proto, {PING_CHECK_STATUS: {(("equipmentno", "EQ1"),): 1.0}})
    metric = request.resource_metrics[0].scope_metrics[0].metrics[0]
    assert METRIC_META[PING_CHECK_STATUS].unit is None
    assert metric.unit == ""


# ---------------------------------------------------------------------------
# Datapoints
# ---------------------------------------------------------------------------


def test_datapoint_carries_value_and_timestamp(proto) -> None:
    request = _build(proto, {PING_CHECK_RTT_MS: {(("equipmentno", "EQ1"),): 12.5}})
    point = request.resource_metrics[0].scope_metrics[0].metrics[0].gauge.data_points[0]
    assert point.as_double == 12.5
    assert point.time_unix_nano == 1_700_000_000_000_000_000


def test_global_labels_are_merged_into_every_datapoint(proto) -> None:
    grouped = {
        PING_CHECK_STATUS: {
            (("equipmentno", "EQ1"),): 1.0,
            (("equipmentno", "EQ2"),): 0.0,
        }
    }
    request = _build(proto, grouped, metric_labels={"job": "avtools", "region": "cern"})
    points = request.resource_metrics[0].scope_metrics[0].metrics[0].gauge.data_points
    assert len(points) == 2
    for point in points:
        attributes = _attrs(point)
        assert attributes["job"] == "avtools"
        assert attributes["region"] == "cern"
        assert attributes["equipmentno"] in {"EQ1", "EQ2"}


def test_sample_labels_win_over_global_labels(proto) -> None:
    """Same precedence the gRPC observable-gauge callback applied."""
    request = _build(
        proto,
        {PING_CHECK_STATUS: {(("room", "ROOM-101"),): 1.0}},
        metric_labels={"room": "GLOBAL"},
    )
    point = request.resource_metrics[0].scope_metrics[0].metrics[0].gauge.data_points[0]
    assert _attrs(point)["room"] == "ROOM-101"


def test_attributes_are_emitted_in_a_stable_sorted_order(proto) -> None:
    request = _build(
        proto,
        {PING_CHECK_STATUS: {(("building", "B1"), ("equipmentno", "EQ1")): 1.0}},
        metric_labels={"job": "avtools"},
    )
    point = request.resource_metrics[0].scope_metrics[0].metrics[0].gauge.data_points[0]
    assert [kv.key for kv in point.attributes] == ["building", "equipmentno", "job"]


def test_empty_grouped_map_produces_an_envelope_with_no_metrics(proto) -> None:
    request = _build(proto, {})
    assert len(request.resource_metrics[0].scope_metrics[0].metrics) == 0


def test_datapoint_count(proto) -> None:
    grouped = {
        PING_CHECK_STATUS: {(("equipmentno", "EQ1"),): 1.0, (("equipmentno", "EQ2"),): 0.0},
        PING_CHECK_RTT_MS: {(("equipmentno", "EQ1"),): 3.0},
    }
    assert metrics_encoder.datapoint_count(grouped) == 3
    assert metrics_encoder.datapoint_count({}) == 0


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_protobuf_serialization_round_trips(proto) -> None:
    request = _build(proto, {PING_CHECK_RTT_MS: {(("equipmentno", "EQ1"),): 12.5}})
    payload = metrics_encoder.serialize_request(request, content_type=CONTENT_TYPE_PROTOBUF)
    assert isinstance(payload, bytes)

    decoded = proto.ExportMetricsServiceRequest()
    decoded.ParseFromString(payload)
    metric = decoded.resource_metrics[0].scope_metrics[0].metrics[0]
    assert metric.name == PING_CHECK_RTT_MS
    assert metric.gauge.data_points[0].as_double == 12.5


def test_json_serialization_is_canonical_otlp_json(proto) -> None:
    request = _build(proto, {PING_CHECK_RTT_MS: {(("equipmentno", "EQ1"),): 12.5}})
    payload = metrics_encoder.serialize_request(request, content_type=CONTENT_TYPE_JSON)
    document = json.loads(payload)
    metric = document["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    assert metric["name"] == PING_CHECK_RTT_MS
    assert metric["gauge"]["dataPoints"][0]["asDouble"] == 12.5


def test_protobuf_is_smaller_than_json_at_realistic_width(proto) -> None:
    """Why protobuf is the default at ~42,900 samples/hr."""
    labels = (
        ("building", "B0031"),
        ("equipmentno", "EQ-000123"),
        ("hostname", "avdev-0031-r012"),
        ("room", "0031-R-012"),
    )
    grouped = {PING_CHECK_STATUS: {labels + (("ip", f"10.0.0.{i}"),): 1.0 for i in range(200)}}
    request = _build(proto, grouped, metric_labels={"job": "avtools", "region": "cern"})

    protobuf_bytes = metrics_encoder.serialize_request(request, content_type=CONTENT_TYPE_PROTOBUF)
    json_bytes = metrics_encoder.serialize_request(request, content_type=CONTENT_TYPE_JSON)
    assert len(protobuf_bytes) < len(json_bytes)


def test_unsupported_content_type_is_rejected(proto) -> None:
    request = _build(proto, {PING_CHECK_STATUS: {(): 1.0}})
    with pytest.raises(metrics_encoder.OTLPEncodeError, match="Unsupported OTLP content type"):
        metrics_encoder.serialize_request(request, content_type="text/plain")
