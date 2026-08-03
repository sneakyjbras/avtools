"""Endpoint-form migration: the deployed charts still pass the OTLP/gRPC form.

``MONIT_OTLP_ENDPOINT=monit-otlp.cern.ch:4316`` comes from a chart in another
repository, so the OTLP/HTTP publisher has to accept it and derive the URL rather
than hard-fail — a hard failure would stop production metrics the moment the new
image rolls out.
"""

from __future__ import annotations

import pytest

from avtools.otlp import endpoints


# ---------------------------------------------------------------------------
# Legacy 'host:port' -> OTLP/HTTP URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("legacy_port", [4316, 4317])
def test_legacy_grpc_target_migrates_to_the_tls_http_url(legacy_port: int) -> None:
    """The gRPC ports have no TLS listener, so they migrate to :4319 over https."""
    assert (
        endpoints.normalize_metrics_endpoint(f"monit-otlp.cern.ch:{legacy_port}")
        == "https://monit-otlp.cern.ch:4319/v1/metrics"
    )


def test_legacy_grpc_target_migrates_for_the_logs_signal_too() -> None:
    """The same transport carries logs next; only the signal path differs."""
    assert (
        endpoints.normalize_logs_endpoint("monit-otlp.cern.ch:4316")
        == "https://monit-otlp.cern.ch:4319/v1/logs"
    )


def test_plaintext_http_port_keeps_its_scheme() -> None:
    """4318 is plaintext-only; promoting it to https would fail confusingly."""
    assert (
        endpoints.normalize_metrics_endpoint("localhost:4318") == "http://localhost:4318/v1/metrics"
    )


def test_unknown_port_is_assumed_to_be_tls() -> None:
    assert (
        endpoints.normalize_metrics_endpoint("collector.example:9999")
        == "https://collector.example:9999/v1/metrics"
    )


def test_trailing_slash_on_a_bare_target_is_tolerated() -> None:
    assert (
        endpoints.normalize_metrics_endpoint("monit-otlp.cern.ch:4316/")
        == "https://monit-otlp.cern.ch:4319/v1/metrics"
    )


# ---------------------------------------------------------------------------
# URL forms
# ---------------------------------------------------------------------------


def test_full_signal_url_is_returned_unchanged() -> None:
    url = "https://monit-otlp.cern.ch:4319/v1/metrics"
    assert endpoints.normalize_metrics_endpoint(url) == url


def test_base_url_gains_the_signal_path() -> None:
    assert (
        endpoints.normalize_metrics_endpoint("https://monit-otlp.cern.ch:4319")
        == "https://monit-otlp.cern.ch:4319/v1/metrics"
    )


def test_base_url_with_trailing_slash_gains_the_signal_path() -> None:
    assert (
        endpoints.normalize_logs_endpoint("https://monit-otlp.cern.ch:4319/")
        == "https://monit-otlp.cern.ch:4319/v1/logs"
    )


def test_custom_path_is_preserved() -> None:
    """A deliberate collector path must not be rewritten to /v1/metrics."""
    url = "https://gateway.example/otlp/v1/metrics"
    assert endpoints.normalize_metrics_endpoint(url) == url


def test_is_http_url() -> None:
    assert endpoints.is_http_url("https://host:4319/v1/metrics") is True
    assert endpoints.is_http_url("monit-otlp.cern.ch:4316") is False
    assert endpoints.is_http_url("") is False


# ---------------------------------------------------------------------------
# Actionable failures
# ---------------------------------------------------------------------------


def test_empty_endpoint_names_both_supported_forms() -> None:
    with pytest.raises(endpoints.OTLPEndpointError) as excinfo:
        endpoints.normalize_metrics_endpoint("")
    message = str(excinfo.value)
    assert "MONIT_OTLP_ENDPOINT" in message
    assert "monit-otlp.cern.ch:4319" in message


def test_endpoint_error_is_a_value_error() -> None:
    """Callers already catch ValueError on a bad endpoint; keep that contract."""
    assert issubclass(endpoints.OTLPEndpointError, ValueError)


def test_bare_host_without_a_port_is_rejected() -> None:
    """Far more often a truncated value than a deliberate default port."""
    with pytest.raises(endpoints.OTLPEndpointError, match="host:port"):
        endpoints.normalize_metrics_endpoint("monit-otlp.cern.ch")


def test_non_numeric_port_is_rejected_with_both_examples() -> None:
    with pytest.raises(endpoints.OTLPEndpointError) as excinfo:
        endpoints.normalize_metrics_endpoint("monit-otlp.cern.ch:notaport")
    message = str(excinfo.value)
    assert "non-numeric port" in message
    assert "/v1/metrics" in message


def test_unsupported_scheme_is_rejected() -> None:
    with pytest.raises(endpoints.OTLPEndpointError, match="unsupported scheme"):
        endpoints.normalize_metrics_endpoint("grpc://monit-otlp.cern.ch:4316")


def test_url_without_a_host_is_rejected() -> None:
    with pytest.raises(endpoints.OTLPEndpointError, match="no host"):
        endpoints.normalize_metrics_endpoint("https:///v1/metrics")


def test_target_without_a_host_is_rejected() -> None:
    with pytest.raises(endpoints.OTLPEndpointError, match="no host"):
        endpoints.normalize_metrics_endpoint(":4316")


def test_unknown_signal_is_rejected() -> None:
    with pytest.raises(endpoints.OTLPEndpointError, match="Unknown OTLP signal"):
        endpoints.normalize_http_endpoint("monit-otlp.cern.ch:4316", signal="profiles")


# ---------------------------------------------------------------------------
# grpc_target — the rollback path must survive an already-migrated endpoint
# ---------------------------------------------------------------------------


def test_grpc_target_passes_through_a_bare_target() -> None:
    assert endpoints.grpc_target("monit-otlp.cern.ch:4316") == "monit-otlp.cern.ch:4316"


def test_grpc_target_reduces_a_url_to_its_authority() -> None:
    assert (
        endpoints.grpc_target("https://monit-otlp.cern.ch:4319/v1/metrics")
        == "monit-otlp.cern.ch:4319"
    )


def test_grpc_target_rejects_an_empty_endpoint() -> None:
    with pytest.raises(endpoints.OTLPEndpointError, match="MONIT_OTLP_ENDPOINT"):
        endpoints.grpc_target("   ")


def test_grpc_target_rejects_a_url_without_a_host() -> None:
    with pytest.raises(endpoints.OTLPEndpointError, match="no host"):
        endpoints.grpc_target("https:///v1/metrics")
