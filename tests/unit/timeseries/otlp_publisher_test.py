from __future__ import annotations

import pytest

from avtools.timeseries.models import MetricSample
from avtools.timeseries.otlp_publisher import OTLPMetricsPublisher, OTLPPublishError


def test_otlp_publisher_rejects_invalid_endpoint():
    with pytest.raises(ValueError):
        OTLPMetricsPublisher(endpoint="", tenant="t", password="p")

    with pytest.raises(ValueError):
        OTLPMetricsPublisher(endpoint="noport", tenant="t", password="p")


def test_otlp_publisher_coerces_label_types_and_raises_on_export_error(monkeypatch):
    pub = OTLPMetricsPublisher(
        endpoint="localhost:4317",
        tenant="t",
        password="p",
        insecure=True,
        export_interval_s=0.1,
        timeout_s=0.1,
    )

    # Ensure shutdown doesn't affect other tests.
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)

    def boom():
        raise RuntimeError("export failed")

    monkeypatch.setattr(pub._provider, "force_flush", boom)

    samples = [
        MetricSample(
            name="avtools_test_metric",
            value=1,
            # Int + None values should be safely coerced to strings.
            labels={"equipmentno": 123, "category": None},  # type: ignore[arg-type]
        )
    ]

    with pytest.raises(OTLPPublishError):
        pub.publish(samples)
