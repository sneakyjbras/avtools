from __future__ import annotations

import threading

from avtools.snmp.handlers import abstract_device_handler as adh


def test_thread_engine_reused_within_thread():
    e1 = adh._thread_engine()
    e2 = adh._thread_engine()
    assert e1 is e2  # one engine per thread, reused across calls (no per-call churn)


def test_thread_engine_isolated_across_threads():
    seen: dict[str, object] = {}

    def worker(name: str) -> None:
        eng = adh._thread_engine()
        assert adh._thread_engine() is eng  # reused within the worker thread
        seen[name] = eng

    ts = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # each worker thread holds a distinct engine (no cross-thread sharing)
    assert len({id(e) for e in seen.values()}) == 4


def test_engine_build_count_is_observable():
    assert isinstance(adh.engine_build_count(), int)
    before = adh.engine_build_count()
    adh._thread_engine()  # main thread already built -> no new build
    assert adh.engine_build_count() == before
