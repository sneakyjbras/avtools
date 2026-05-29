"""Additional OTLPMetricsPublisher tests — TLS/CA-file path and publish success."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import avtools.timeseries.otlp_publisher as mod
from avtools.timeseries.models import MetricSample


def test_tls_ca_file_path_is_used(monkeypatch: Any, tmp_path: Path) -> None:
    """TLS branch: CA file bytes are passed to grpc.ssl_channel_credentials."""
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"CERT")

    seen: dict[str, Any] = {}

    def fake_ssl_channel_credentials(*, root_certificates: bytes):
        seen["root"] = root_certificates
        return "CREDS"

    monkeypatch.setattr(mod.grpc, "ssl_channel_credentials", fake_ssl_channel_credentials)

    pub = mod.OTLPMetricsPublisher(
        endpoint="example:4317",
        tenant="t",
        password="p",
        insecure=False,
        ca_file=str(ca),
    )

    assert seen["root"] == b"CERT"
    assert pub is not None


def test_publish_success_path_registers_gauge(monkeypatch: Any) -> None:
    pub = mod.OTLPMetricsPublisher(
        endpoint="example:4317",
        tenant="t",
        password="p",
        insecure=True,
        export_interval_s=0.1,
        timeout_s=0.1,
    )

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(pub._provider, "force_flush", lambda: None)
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)

    created: list[str] = []

    def fake_create_observable_gauge(name: str, **kwargs: Any):
        created.append(name)

    monkeypatch.setattr(pub._meter, "create_observable_gauge", fake_create_observable_gauge)

    pub.publish([MetricSample(name="avtools_test_metric", value=1, labels={"equipmentno": "EQ1"})])

    assert created == ["avtools_test_metric"]


def test_second_publish_does_not_re_register_same_gauge(monkeypatch: Any) -> None:
    """Once a gauge is registered it must not be re-registered on subsequent publish calls."""
    pub = mod.OTLPMetricsPublisher(
        endpoint="example:4317",
        tenant="t",
        password="p",
        insecure=True,
        export_interval_s=0.1,
        timeout_s=0.1,
    )

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(pub._provider, "force_flush", lambda: None)
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)

    created: list[str] = []

    def fake_create_observable_gauge(name: str, **kwargs: Any):
        created.append(name)

    monkeypatch.setattr(pub._meter, "create_observable_gauge", fake_create_observable_gauge)

    sample = MetricSample(name="avtools_test_metric", value=1, labels={"equipmentno": "EQ1"})
    pub.publish([sample])
    pub.publish([sample])

    # Registered only once.
    assert created.count("avtools_test_metric") == 1
