from __future__ import annotations

from avtools.postgres.monitoring.models.projector import ProjectorMonitoring


def test_projector_monitoring_from_query_stats_none_if_no_relevant_fields():
    rec = ProjectorMonitoring.from_query_stats(
        equipment_no="EQ1", ip="10.0.0.1", stats={"uptime_seconds": 123}
    )
    assert rec is None


def test_projector_monitoring_from_query_stats_strips_and_keeps_fields():
    rec = ProjectorMonitoring.from_query_stats(
        equipment_no="EQ1",
        ip="10.0.0.1",
        stats={"firmware": "  FW1  ", "power_status": " 1 "},
    )

    assert rec is not None
    assert rec.equipment_no == "EQ1"
    assert rec.ip == "10.0.0.1"
    assert rec.firmware == "FW1"
    assert rec.power_status == "1"
