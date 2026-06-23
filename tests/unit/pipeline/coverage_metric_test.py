from __future__ import annotations

from avtools.pipeline.snmp_router import SNMPObserverRouter
from avtools.timeseries import metrics as m


class _CapTS:
    def __init__(self) -> None:
        self.samples: list = []

    def publish(self, samples) -> None:
        self.samples.extend(samples)


class _NoPG:
    def upsert_projector_monitoring(self, *_a, **_k):
        pass

    def upsert_device_sysdescr_monitoring(self, *_a, **_k):
        pass


def _router(ts):
    r = SNMPObserverRouter.__new__(SNMPObserverRouter)
    r._ts = ts
    r._pg = _NoPG()
    r.log = __import__("structlog").get_logger()
    return r


def test_coverage_emitted_when_targeted_given():
    ts = _CapTS()
    r = _router(ts)
    # 3 ping results, 4 targeted -> coverage 0.75
    from avtools.snmp.client import PingResult

    pings = [
        PingResult(
            device=None,
            ip=f"10.0.0.{i}",
            equipmentno=f"E{i}",
            up=True,
            rtt_ms=1.0,
            reason=None,
            attempts=1,
        )
        for i in range(3)
    ]
    r.process(ping=pings, probe=[], queries=[], targeted=4)
    by = {s.name: s.value for s in ts.samples if not s.labels}
    assert by[m.SNMP_DEVICES_TARGETED] == 4
    assert by[m.SNMP_DEVICES_POLLED] == 3
    assert by[m.SNMP_COVERAGE_RATIO] == 0.75


def test_no_coverage_when_targeted_zero():
    ts = _CapTS()
    r = _router(ts)
    r.process(ping=[], probe=[], queries=[], targeted=0)
    names = {s.name for s in ts.samples}
    assert m.SNMP_COVERAGE_RATIO not in names
