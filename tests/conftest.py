from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Optional dependency stubs
#
# AVTools uses structlog heavily. In minimal CI environments, structlog may not
# be installed. These stubs keep unit tests importable without affecting real
# environments where structlog exists.
# ---------------------------------------------------------------------------


def _ensure_structlog_stub() -> None:
    """
    Ensure the 'structlog' import is available during tests.

    Returns:
        None.
    """
    import sys
    import types

    if "structlog" in sys.modules:
        return

    try:
        __import__("structlog")
        return
    except Exception:
        pass

    def _get_logger(*args, **kwargs):
        class _L:
            def bind(self, **kw):
                return self

            def info(self, *a, **k):
                return None

            def warning(self, *a, **k):
                return None

            def error(self, *a, **k):
                return None

            def exception(self, *a, **k):
                return None

            def debug(self, *a, **k):
                return None

        return _L()

    stub = types.SimpleNamespace(
        get_logger=_get_logger,
        configure=lambda **kw: None,
        make_filtering_bound_logger=lambda lvl: object,
    )
    sys.modules["structlog"] = stub


_ensure_structlog_stub()


@pytest.fixture(autouse=True)
def _mock_otlp_metric_exporter(monkeypatch):
    """Disable real OTLP exports during unit tests.

    The OTLP publisher constructs an OpenTelemetry PeriodicExportingMetricReader.
    That reader expects the exporter to expose some internal attributes, such as
    `_preferred_temporality`.

    We patch the *import site* used by AVTools (avtools.timeseries.otlp_publisher)
    so tests never attempt a real gRPC connection (e.g. localhost:4317).
    """

    import avtools.timeseries.otlp_publisher as otlp_mod

    class _DummyOTLPMetricExporter:
        # OpenTelemetry SDK expects these attributes on OTLPMetricExporter instances.
        _preferred_temporality = {}
        _preferred_aggregation = {}

        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def export(self, *args, **kwargs):
            # Indicate success to the reader.
            try:
                from opentelemetry.sdk.metrics.export import MetricExportResult

                return MetricExportResult.SUCCESS
            except Exception:
                return True

        def force_flush(self, *args, **kwargs):
            return True

        def shutdown(self, *args, **kwargs):
            return True

    monkeypatch.setattr(otlp_mod, "OTLPMetricExporter", _DummyOTLPMetricExporter, raising=True)
