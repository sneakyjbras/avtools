from __future__ import annotations

import time

import avtools.timeseries.heartbeat as hb
from avtools.timeseries import metrics as m


class _CapPublisher:
    instances: list = []

    def __init__(self, **kw):
        self.kw = kw
        self.published: list = []
        _CapPublisher.instances.append(self)

    def publish(self, samples):
        self.published.extend(samples)


def test_publish_heartbeat_emits_one_now_gauge(monkeypatch):
    _CapPublisher.instances.clear()
    monkeypatch.setattr(hb, "OTLPMetricsPublisher", _CapPublisher)
    before = time.time()
    hb.publish_heartbeat(
        m.EAM_LAST_RUN_TIMESTAMP,
        otlp_endpoint="e",
        tenant="t",
        password="p",
        service_name="avtools",
        otlp_ca_file=None,
        otlp_insecure=True,
        environment="prod",
        hostgroup="itdcim/av",
        availability_zone="cern-geneva-b",
    )
    pub = _CapPublisher.instances[-1]
    assert len(pub.published) == 1
    s = pub.published[0]
    assert s.name == m.EAM_LAST_RUN_TIMESTAMP
    assert before <= s.value <= time.time() + 1
    # carries submitter_environment via global labels
    assert pub.kw["metric_labels"]["submitter_environment"] == "prod"


def test_publish_heartbeat_never_raises(monkeypatch):
    def boom(**kw):
        raise RuntimeError("otlp down")

    monkeypatch.setattr(hb, "OTLPMetricsPublisher", boom)
    # must swallow the error (heartbeat must not fail the sync)
    hb.publish_heartbeat(
        m.LANDB_LAST_RUN_TIMESTAMP,
        otlp_endpoint="e",
        tenant="t",
        password="p",
        service_name="avtools",
        otlp_ca_file=None,
        otlp_insecure=True,
        environment="qa",
        hostgroup="itdcim/av",
        availability_zone="z",
    )
