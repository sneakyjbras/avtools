"""Tests for OTLPMetricsPublisher — init validation, Layer 1, and Layer 2.

Layer 1 (OTel resource attributes):
    service.name, service.instance.id, service.version, service.namespace
    are verified via the Resource created inside the publisher.

Layer 2 (global metric labels):
    metric_labels are merged into every Observation.
    Collision between a global label key and a per-device sample label key
    raises OTLPPublishError.
"""

from __future__ import annotations

from typing import Any

import avtools.timeseries.otlp_publisher as mod
from avtools import __version__ as _AVTOOLS_VERSION
from avtools.timeseries.models import MetricSample
from avtools.timeseries.otlp_publisher import OTLPMetricsPublisher, OTLPPublishError

import pytest


# ---------------------------------------------------------------------------
# Init validation
# ---------------------------------------------------------------------------


def test_publisher_rejects_empty_endpoint():
    with pytest.raises(ValueError):
        OTLPMetricsPublisher(endpoint="", tenant="t", password="p")


def test_publisher_rejects_endpoint_without_port():
    with pytest.raises(ValueError):
        OTLPMetricsPublisher(endpoint="noport", tenant="t", password="p")


# ---------------------------------------------------------------------------
# Layer 1 — OTel resource attributes
# ---------------------------------------------------------------------------


def test_layer1_resource_contains_service_version(monkeypatch: Any) -> None:
    """service.version must equal avtools.__version__."""
    captured: dict[str, Any] = {}

    original_create = mod.Resource.create

    def fake_resource_create(attrs: dict) -> Any:
        captured.update(attrs)
        return original_create(attrs)

    monkeypatch.setattr(mod.Resource, "create", staticmethod(fake_resource_create))

    OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
    )

    assert captured.get("service.version") == _AVTOOLS_VERSION


def test_layer1_resource_contains_service_namespace(monkeypatch: Any) -> None:
    """service.namespace must be 'itdcim'."""
    captured: dict[str, Any] = {}

    original_create = mod.Resource.create

    def fake_resource_create(attrs: dict) -> Any:
        captured.update(attrs)
        return original_create(attrs)

    monkeypatch.setattr(mod.Resource, "create", staticmethod(fake_resource_create))

    OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
    )

    assert captured.get("service.namespace") == "itdcim"


def test_layer1_resource_contains_service_name_and_instance(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    original_create = mod.Resource.create

    def fake_resource_create(attrs: dict) -> Any:
        captured.update(attrs)
        return original_create(attrs)

    monkeypatch.setattr(mod.Resource, "create", staticmethod(fake_resource_create))

    OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
        service_name="avtools-test",
    )

    assert captured.get("service.name") == "avtools-test"
    assert "service.instance.id" in captured
    assert captured["service.instance.id"] != ""


# ---------------------------------------------------------------------------
# Layer 2 — metric_labels merged into observations
# ---------------------------------------------------------------------------


def test_layer2_metric_labels_stored_on_publisher() -> None:
    labels = {"job": "avtools", "submitter_environment": "prod"}
    pub = OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
        metric_labels=labels,
    )
    assert pub._metric_labels == labels


def test_layer2_no_metric_labels_by_default() -> None:
    pub = OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
    )
    assert pub._metric_labels == {}


def test_layer2_collision_raises_otlp_publish_error(monkeypatch: Any) -> None:
    """A global label key that matches a device-level sample label must raise."""
    pub = OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
        metric_labels={"equipmentno": "GLOBAL"},  # collides with device label
    )
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)

    samples = [
        MetricSample(
            name="avtools_ping_check_status",
            value=1,
            labels={"equipmentno": "EQ1"},
        )
    ]

    with pytest.raises(OTLPPublishError, match="conflict"):
        pub.publish(samples)


def test_layer2_collision_on_room_label_raises(monkeypatch: Any) -> None:
    pub = OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
        metric_labels={"room": "GLOBAL_ROOM"},  # would shadow device label
    )
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)

    samples = [
        MetricSample(
            name="avtools_ping_check_status",
            value=1,
            labels={"equipmentno": "EQ1", "room": "ROOM-101"},
        )
    ]

    with pytest.raises(OTLPPublishError, match="conflict"):
        pub.publish(samples)


def test_layer2_metric_labels_merged_into_observations(monkeypatch: Any) -> None:
    """Global labels must appear in every yielded Observation."""
    global_labels = {"job": "avtools", "submitter_environment": "qa", "region": "cern"}

    pub = OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
        export_interval_s=0.1,
        timeout_s=0.1,
        metric_labels=global_labels,
    )

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(pub._provider, "force_flush", lambda **_k: True)
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)

    observed_attrs: list[dict] = []

    from opentelemetry.metrics import CallbackOptions

    def fake_create_observable_gauge(name: str, *, callbacks, **kwargs: Any):
        for cb in callbacks:
            for obs in cb(CallbackOptions()):
                observed_attrs.append(obs.attributes)

    monkeypatch.setattr(pub._meter, "create_observable_gauge", fake_create_observable_gauge)

    pub.publish(
        [
            MetricSample(
                name="avtools_ping_check_status",
                value=1,
                labels={"equipmentno": "EQ1", "building": "B1"},
            )
        ]
    )

    assert len(observed_attrs) == 1
    attrs = observed_attrs[0]
    assert attrs["job"] == "avtools"
    assert attrs["submitter_environment"] == "qa"
    assert attrs["region"] == "cern"
    # Device-level labels are also present.
    assert attrs["equipmentno"] == "EQ1"
    assert attrs["building"] == "B1"


def test_layer2_empty_metric_labels_no_extra_attrs(monkeypatch: Any) -> None:
    pub = OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
        export_interval_s=0.1,
        timeout_s=0.1,
    )

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(pub._provider, "force_flush", lambda **_k: True)
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)

    from opentelemetry.metrics import CallbackOptions

    observed_attrs: list[dict] = []

    def fake_create_observable_gauge(name: str, *, callbacks, **kwargs: Any):
        for cb in callbacks:
            for obs in cb(CallbackOptions()):
                observed_attrs.append(obs.attributes)

    monkeypatch.setattr(pub._meter, "create_observable_gauge", fake_create_observable_gauge)

    pub.publish(
        [MetricSample(name="avtools_ping_check_status", value=1, labels={"equipmentno": "EQ1"})]
    )

    assert observed_attrs[0] == {"equipmentno": "EQ1"}


# ---------------------------------------------------------------------------
# Export error propagation
# ---------------------------------------------------------------------------


def test_export_error_raises_otlp_publish_error(monkeypatch: Any) -> None:
    pub = OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
        export_interval_s=0.1,
        timeout_s=0.1,
    )
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)

    def boom(**_kwargs):
        raise RuntimeError("export failed")

    monkeypatch.setattr(pub._provider, "force_flush", boom)

    # A flush that keeps erroring no longer raises OTLPPublishError — collection
    # already succeeded, so publish() retries, logs, and returns False.
    assert (
        pub.publish([MetricSample(name="avtools_test", value=1, labels={"equipmentno": "EQ1"})])
        is False
    )
