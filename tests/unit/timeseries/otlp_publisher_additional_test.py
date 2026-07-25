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
    monkeypatch.setattr(pub._provider, "force_flush", lambda **_k: True)
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
    monkeypatch.setattr(pub._provider, "force_flush", lambda **_k: True)
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


def test_publish_returns_false_and_retries_when_flush_unconfirmed(monkeypatch: Any) -> None:
    """force_flush() returning False (MONIT unreachable / rate-limiting) must be
    retried, logged, and reported as False — never silently swallowed and never
    crash the collection cycle (the DB write already succeeded)."""
    pub = mod.OTLPMetricsPublisher(
        endpoint="host:4317",
        tenant="t",
        password="p",
        insecure=True,
    )
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(pub._provider, "shutdown", lambda: None)

    calls = {"n": 0}

    def never_confirms(**_kwargs: Any) -> bool:
        calls["n"] += 1
        return False

    monkeypatch.setattr(pub._provider, "force_flush", never_confirms)
    monkeypatch.setattr(pub._meter, "create_observable_gauge", lambda *_a, **_k: None)

    result = pub.publish(
        [MetricSample(name="avtools_test_metric", value=1, labels={"equipmentno": "EQ1"})]
    )

    assert result is False  # reported, not swallowed
    assert calls["n"] == mod._FLUSH_ATTEMPTS  # exhausted the retry budget


def test_stable_instance_id_prefers_shard_over_ephemeral_hostname(monkeypatch: Any) -> None:
    """On an Indexed Job the instance id must be the STABLE shard id, not the
    ephemeral pod hostname (the k8s cardinality-explosion / MONIT-gap fix)."""
    monkeypatch.delenv("AVTOOLS_INSTANCE_ID", raising=False)
    monkeypatch.setenv("JOB_COMPLETION_INDEX", "3")
    assert mod._stable_instance_id("avtools") == "avtools-shard-3"


def test_stable_instance_id_explicit_override_wins(monkeypatch: Any) -> None:
    monkeypatch.setenv("AVTOOLS_INSTANCE_ID", "avtools-qa-run-eam")
    monkeypatch.setenv("JOB_COMPLETION_INDEX", "3")
    assert mod._stable_instance_id("avtools") == "avtools-qa-run-eam"


def test_stable_instance_id_falls_back_to_hostname(monkeypatch: Any) -> None:
    """Monolith VM path: no shard env, no override -> stable hostname (unchanged)."""
    monkeypatch.delenv("AVTOOLS_INSTANCE_ID", raising=False)
    monkeypatch.delenv("JOB_COMPLETION_INDEX", raising=False)
    monkeypatch.setattr(mod.socket, "gethostname", lambda: "avtools-vm-01")
    assert mod._stable_instance_id("avtools") == "avtools-vm-01"


def test_publisher_uses_stable_instance_id_in_resource(monkeypatch: Any) -> None:
    """The publisher's OTel Resource must carry the stable instance id."""
    monkeypatch.delenv("AVTOOLS_INSTANCE_ID", raising=False)
    monkeypatch.setenv("JOB_COMPLETION_INDEX", "5")
    captured: dict[str, Any] = {}
    orig = mod.Resource.create

    def fake_create(attrs: dict) -> Any:
        captured.update(attrs)
        return orig(attrs)

    monkeypatch.setattr(mod.Resource, "create", staticmethod(fake_create))
    mod.OTLPMetricsPublisher(endpoint="h:4317", tenant="t", password="p", insecure=True)
    assert captured["service.instance.id"] == "avtools-shard-5"
