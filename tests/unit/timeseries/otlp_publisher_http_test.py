"""OTLPMetricsPublisher on the OTLP/HTTP transport (the default path).

These are the regression guards for the transport migration. The metrics are live
in production at ~42,900 samples/hr, so the properties that matter most are:

  * a confirmed export means MONIT answered 2xx — never a force_flush() return;
  * the retry budget and the ``otlp_export_unconfirmed`` event are unchanged;
  * the chart's legacy ``host:port`` endpoint and ``MONIT_OTLP_INSECURE=true``
    keep working, and the second never silently re-enables cleartext;
  * the payload still carries every sample, with both label layers.

The gRPC rollback path is covered in ``otlp_publisher_test.py`` and
``otlp_publisher_additional_test.py``.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

import avtools.otlp.http_transport as transport_mod
from avtools.otlp import metrics_encoder
from avtools.timeseries import otlp_publisher as mod
from avtools.timeseries.models import MetricSample
from avtools.timeseries.otlp_publisher import OTLPMetricsPublisher, OTLPPublishError

_TENANT = "avtools"
# Not a credential: a fixed dummy so the Basic-auth header is deterministic.
_CREDENTIAL = "unit-test-value"
_LEGACY_ENDPOINT = "monit-otlp.cern.ch:4316"
_HTTP_ENDPOINT = "https://monit-otlp.cern.ch:4319/v1/metrics"


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b'{"partialSuccess":{}}') -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, limit: int | None = None) -> bytes:
        return self._body.read(limit) if limit else self._body.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False


class _Recorder:
    """Capture structlog events so the contract wording can be asserted."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str):
        def log(event: str, **fields: Any) -> None:
            self.events.append((level, event, fields))

        return log

    def __getattr__(self, level: str):
        return self._record(level)

    def named(self, event: str) -> list[dict[str, Any]]:
        return [fields for _level, name, fields in self.events if name == event]

    def levels_for(self, event: str) -> list[str]:
        return [level for level, name, _fields in self.events if name == event]


@pytest.fixture
def recorder(monkeypatch: Any) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(mod, "_log", rec)
    return rec


def _publisher(monkeypatch: Any, urlopen: Any, **kwargs: Any) -> OTLPMetricsPublisher:
    monkeypatch.setattr(transport_mod, "_urlopen", urlopen)
    kwargs.setdefault("endpoint", _LEGACY_ENDPOINT)
    kwargs.setdefault("tenant", _TENANT)
    kwargs.setdefault("password", _CREDENTIAL)
    publisher = OTLPMetricsPublisher(**kwargs)
    # Never pay real backoff in a unit test.
    if publisher._transport is not None:
        publisher._transport._sleep = lambda _s: None
    return publisher


_SAMPLES = [
    MetricSample(name="avtools_ping_check_status", value=1, labels={"equipmentno": "EQ1"}),
    MetricSample(name="avtools_ping_check_rtt_ms", value=12.5, labels={"equipmentno": "EQ1"}),
]


# ---------------------------------------------------------------------------
# Defaults and endpoint migration
# ---------------------------------------------------------------------------


def test_http_is_the_default_transport(monkeypatch: Any) -> None:
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    assert publisher.protocol == mod.PROTOCOL_HTTP
    assert mod.DEFAULT_PROTOCOL == mod.PROTOCOL_HTTP


def test_protobuf_is_the_default_encoding(monkeypatch: Any) -> None:
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    assert publisher.encoding == mod.ENCODING_PROTOBUF


def test_legacy_grpc_endpoint_is_migrated_not_rejected(monkeypatch: Any) -> None:
    """The chart lives in another repo and still passes the gRPC 'host:port'."""
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    assert publisher._endpoint == _HTTP_ENDPOINT


def test_endpoint_migration_is_logged_with_both_values(
    monkeypatch: Any, recorder: _Recorder
) -> None:
    _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    migrated = recorder.named("otlp_endpoint_migrated")
    assert len(migrated) == 1
    assert migrated[0]["given"] == _LEGACY_ENDPOINT
    assert migrated[0]["using"] == _HTTP_ENDPOINT


def test_already_migrated_endpoint_logs_nothing(monkeypatch: Any, recorder: _Recorder) -> None:
    _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200), endpoint=_HTTP_ENDPOINT)
    assert recorder.named("otlp_endpoint_migrated") == []


def test_unparseable_endpoint_raises_otlp_publish_error(monkeypatch: Any) -> None:
    with pytest.raises(OTLPPublishError, match="non-numeric port"):
        _publisher(
            monkeypatch,
            lambda *_a, **_k: _FakeResponse(200),
            endpoint="monit-otlp.cern.ch:nope",
        )


def test_empty_endpoint_still_raises_value_error(monkeypatch: Any) -> None:
    """Unchanged from before the migration."""
    with pytest.raises(ValueError):
        _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200), endpoint="")


# ---------------------------------------------------------------------------
# MONIT_OTLP_INSECURE must not re-open the cleartext path
# ---------------------------------------------------------------------------


def test_insecure_flag_is_ignored_on_http(monkeypatch: Any, recorder: _Recorder) -> None:
    """The chart still sets MONIT_OTLP_INSECURE=true; honouring it would keep
    equipment numbers and device IPs in cleartext, which is what this fixes."""
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200), insecure=True)
    assert publisher._endpoint.startswith("https://")
    assert publisher._transport.is_tls is True

    warnings = recorder.named("otlp_insecure_ignored")
    assert len(warnings) == 1
    assert "--otlp-protocol grpc" in warnings[0]["detail"]
    assert recorder.levels_for("otlp_insecure_ignored") == ["warning"]


def test_no_insecure_warning_when_the_flag_is_off(monkeypatch: Any, recorder: _Recorder) -> None:
    _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200), insecure=False)
    assert recorder.named("otlp_insecure_ignored") == []


# ---------------------------------------------------------------------------
# Successful publish
# ---------------------------------------------------------------------------


def test_publish_returns_true_on_a_2xx(monkeypatch: Any) -> None:
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    assert publisher.publish(_SAMPLES) is True


def test_publish_posts_every_sample_with_both_label_layers(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, **_kwargs: Any) -> _FakeResponse:
        captured["body"] = request.data
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        return _FakeResponse(200)

    publisher = _publisher(
        monkeypatch,
        fake_urlopen,
        metric_labels={"job": "avtools", "submitter_environment": "qa"},
        instance_id="avtools-shard-2",
    )
    assert publisher.publish(_SAMPLES) is True

    proto = metrics_encoder.load_otlp_proto()
    decoded = proto.ExportMetricsServiceRequest()
    decoded.ParseFromString(captured["body"])

    resource = {
        kv.key: kv.value.string_value for kv in decoded.resource_metrics[0].resource.attributes
    }
    assert resource["service.name"] == "avtools"
    assert resource["service.instance.id"] == "avtools-shard-2"
    assert resource["service.namespace"] == "itdcim"

    metrics = decoded.resource_metrics[0].scope_metrics[0].metrics
    assert {m.name for m in metrics} == {
        "avtools_ping_check_status",
        "avtools_ping_check_rtt_ms",
    }
    for metric in metrics:
        attributes = {
            kv.key: kv.value.string_value for kv in metric.gauge.data_points[0].attributes
        }
        assert attributes["equipmentno"] == "EQ1"
        assert attributes["job"] == "avtools"
        assert attributes["submitter_environment"] == "qa"

    assert captured["headers"]["content-type"] == transport_mod.CONTENT_TYPE_PROTOBUF
    assert captured["headers"]["authorization"].startswith("Basic ")


def test_json_encoding_posts_a_json_body(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, **_kwargs: Any) -> _FakeResponse:
        captured["body"] = request.data
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        return _FakeResponse(200)

    publisher = _publisher(monkeypatch, fake_urlopen, encoding="json")
    assert publisher.publish(_SAMPLES) is True
    assert captured["headers"]["content-type"] == transport_mod.CONTENT_TYPE_JSON
    assert b"resourceMetrics" in captured["body"]


def test_publish_with_no_samples_sends_nothing(monkeypatch: Any) -> None:
    calls = {"n": 0}

    def fake_urlopen(*_a: Any, **_k: Any) -> _FakeResponse:
        calls["n"] += 1
        return _FakeResponse(200)

    publisher = _publisher(monkeypatch, fake_urlopen)
    assert publisher.publish([]) is True
    assert calls["n"] == 0


def test_successful_publish_logs_the_payload_shape(monkeypatch: Any, recorder: _Recorder) -> None:
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    publisher.publish(_SAMPLES)
    logged = recorder.named("otlp_http_export_ok")
    assert len(logged) == 1
    assert logged[0]["metrics"] == 2
    assert logged[0]["datapoints"] == 2
    assert logged[0]["encoding"] == "protobuf"
    assert logged[0]["payload_bytes"] > 0


# ---------------------------------------------------------------------------
# Failure -> unconfirmed, never a silent drop
# ---------------------------------------------------------------------------


def test_http_error_status_reports_unconfirmed(monkeypatch: Any) -> None:
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(500))
    assert publisher.publish(_SAMPLES) is False


def test_unconfirmed_export_keeps_its_contract_event(monkeypatch: Any, recorder: _Recorder) -> None:
    """Field set and wording are load-bearing: alerting keys off this event."""
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(503))
    assert publisher.publish(_SAMPLES) is False

    events = recorder.named("otlp_export_unconfirmed")
    assert len(events) == 1
    assert recorder.levels_for("otlp_export_unconfirmed") == ["error"]
    fields = events[0]
    assert fields["endpoint"] == _HTTP_ENDPOINT
    assert fields["attempts"] == mod._FLUSH_ATTEMPTS
    assert "503" in fields["error"]
    assert fields["detail"] == (
        "force_flush did not confirm the export; MONIT may be "
        "unreachable or rate-limiting the concurrent shard exports — "
        "this cycle's metrics were likely dropped"
    )


def test_no_unconfirmed_event_on_success(monkeypatch: Any, recorder: _Recorder) -> None:
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    publisher.publish(_SAMPLES)
    assert recorder.named("otlp_export_unconfirmed") == []


def test_timeout_reports_unconfirmed(monkeypatch: Any, recorder: _Recorder) -> None:
    def timing_out(*_a: Any, **_k: Any):
        raise TimeoutError("timed out")

    publisher = _publisher(monkeypatch, timing_out)
    assert publisher.publish(_SAMPLES) is False
    assert "timed out" in recorder.named("otlp_export_unconfirmed")[0]["error"]


def test_retry_then_succeed(monkeypatch: Any) -> None:
    attempts = {"n": 0}

    def flaky(*_a: Any, **_k: Any) -> _FakeResponse:
        attempts["n"] += 1
        return _FakeResponse(200 if attempts["n"] == 2 else 503)

    publisher = _publisher(monkeypatch, flaky)
    assert publisher.publish(_SAMPLES) is True
    assert attempts["n"] == 2


def test_retry_exhausted_uses_the_full_flush_budget(monkeypatch: Any) -> None:
    attempts = {"n": 0}

    def always_down(*_a: Any, **_k: Any) -> _FakeResponse:
        attempts["n"] += 1
        return _FakeResponse(503)

    publisher = _publisher(monkeypatch, always_down)
    assert publisher.publish(_SAMPLES) is False
    assert attempts["n"] == mod._FLUSH_ATTEMPTS


def test_bad_credentials_fail_fast_but_still_report_unconfirmed(
    monkeypatch: Any, recorder: _Recorder
) -> None:
    """A 401 cannot resolve itself; burning the backoff budget only delays the job."""
    attempts = {"n": 0}

    def unauthorized(*_a: Any, **_k: Any) -> _FakeResponse:
        attempts["n"] += 1
        return _FakeResponse(401)

    publisher = _publisher(monkeypatch, unauthorized)
    assert publisher.publish(_SAMPLES) is False
    assert attempts["n"] == 1
    assert len(recorder.named("otlp_export_unconfirmed")) == 1


# ---------------------------------------------------------------------------
# CA wiring
# ---------------------------------------------------------------------------


def test_bundled_ca_chain_is_used_by_default(monkeypatch: Any) -> None:
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200))
    assert publisher._transport.ca_file == transport_mod.default_ca_file()
    assert Path(publisher._transport.ca_file).is_file()


def test_explicit_ca_file_is_used(monkeypatch: Any, tmp_path: Path) -> None:
    custom = tmp_path / "chain.pem"
    custom.write_text(Path(transport_mod.default_ca_file()).read_text())
    publisher = _publisher(monkeypatch, lambda *_a, **_k: _FakeResponse(200), ca_file=str(custom))
    assert publisher._transport.ca_file == str(custom)


def test_missing_ca_file_fails_construction(monkeypatch: Any, tmp_path: Path) -> None:
    with pytest.raises(OTLPPublishError, match="Failed to initialize OTLP exporter"):
        _publisher(
            monkeypatch,
            lambda *_a, **_k: _FakeResponse(200),
            ca_file=str(tmp_path / "absent.pem"),
        )


# ---------------------------------------------------------------------------
# Shared behaviour: the Layer-2 collision guard applies to both transports
# ---------------------------------------------------------------------------


def test_label_collision_still_raises_on_http(monkeypatch: Any) -> None:
    publisher = _publisher(
        monkeypatch,
        lambda *_a, **_k: _FakeResponse(200),
        metric_labels={"equipmentno": "GLOBAL"},
    )
    with pytest.raises(OTLPPublishError, match="conflict"):
        publisher.publish(_SAMPLES)


# ---------------------------------------------------------------------------
# Protocol / encoding tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["http", "HTTP", " http ", None, ""])
def test_protocol_tokens_normalize_to_http(token: Any) -> None:
    assert mod.normalize_protocol(token) == mod.PROTOCOL_HTTP


def test_grpc_protocol_token_is_accepted() -> None:
    assert mod.normalize_protocol("GRPC") == mod.PROTOCOL_GRPC


def test_unknown_protocol_names_the_valid_values() -> None:
    with pytest.raises(ValueError, match=r"\['http', 'grpc'\]"):
        mod.normalize_protocol("thrift")


@pytest.mark.parametrize("token", ["protobuf", "PROTOBUF", None, ""])
def test_encoding_tokens_normalize_to_protobuf(token: Any) -> None:
    assert mod.normalize_encoding(token) == mod.ENCODING_PROTOBUF


def test_json_encoding_token_is_accepted() -> None:
    assert mod.normalize_encoding("json") == mod.ENCODING_JSON


def test_unknown_encoding_names_the_valid_values() -> None:
    with pytest.raises(ValueError, match=r"\['protobuf', 'json'\]"):
        mod.normalize_encoding("msgpack")


# ---------------------------------------------------------------------------
# The gRPC escape hatch stays reachable and says it is deprecated
# ---------------------------------------------------------------------------


def test_grpc_protocol_builds_the_sdk_pipeline(recorder: _Recorder) -> None:
    publisher = OTLPMetricsPublisher(
        endpoint=_LEGACY_ENDPOINT,
        tenant=_TENANT,
        password=_CREDENTIAL,
        insecure=True,
        protocol="grpc",
    )
    assert publisher._provider is not None
    assert publisher._transport is None
    assert publisher._endpoint == _LEGACY_ENDPOINT


def test_grpc_protocol_warns_that_it_is_cleartext(recorder: _Recorder) -> None:
    OTLPMetricsPublisher(
        endpoint=_LEGACY_ENDPOINT,
        tenant=_TENANT,
        password=_CREDENTIAL,
        insecure=True,
        protocol="grpc",
    )
    warnings = recorder.named("otlp_grpc_transport_deprecated")
    assert len(warnings) == 1
    assert recorder.levels_for("otlp_grpc_transport_deprecated") == ["warning"]
    assert "cleartext" in warnings[0]["detail"]


def test_grpc_protocol_accepts_an_already_migrated_url(recorder: _Recorder) -> None:
    """Rolling back must not require also reverting MONIT_OTLP_ENDPOINT."""
    publisher = OTLPMetricsPublisher(
        endpoint=_HTTP_ENDPOINT,
        tenant=_TENANT,
        password=_CREDENTIAL,
        insecure=True,
        protocol="grpc",
    )
    assert publisher._endpoint == "monit-otlp.cern.ch:4319"


def test_both_transports_build_the_same_resource_attributes(monkeypatch: Any) -> None:
    """Layer 1 is transport-independent, so series identity cannot drift."""
    http_publisher = _publisher(
        monkeypatch, lambda *_a, **_k: _FakeResponse(200), instance_id="avtools-shard-7"
    )
    grpc_publisher = OTLPMetricsPublisher(
        endpoint=_LEGACY_ENDPOINT,
        tenant=_TENANT,
        password=_CREDENTIAL,
        insecure=True,
        protocol="grpc",
        instance_id="avtools-shard-7",
    )
    assert http_publisher._resource_attributes == grpc_publisher._resource_attributes
