from __future__ import annotations

import avtools.main as M
from avtools.timeseries import metrics as m


def test_emit_heartbeat_skips_without_creds(monkeypatch):
    called = {"n": 0}

    def fake_publish(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(M, "publish_heartbeat", fake_publish)
    base = dict(
        otlp_endpoint="e",
        service_name="avtools",
        otlp_ca_file=None,
        otlp_insecure=True,
        environment="prod",
        hostgroup="h",
        availability_zone="z",
    )
    # no tenant/password -> skip
    M._emit_heartbeat(m.EAM_LAST_RUN_TIMESTAMP, tenant=None, monit_password=None, **base)
    assert called["n"] == 0
    # creds present -> publish
    M._emit_heartbeat(m.EAM_LAST_RUN_TIMESTAMP, tenant="t", monit_password="p", **base)
    assert called["n"] == 1
