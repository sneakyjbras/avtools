from __future__ import annotations

from avtools.influx.data.projector_stats import ProjectorStats


def test_stats_base_to_influx_separates_tags_and_fields() -> None:
    s = ProjectorStats(uptime=120, firmware="v1", lamp_hours=10, power_status=2)

    payload = s.to_influx()

    assert payload["measurement"] == "projector_stats"
    assert payload["tags"] == {"firmware": "v1"}
    assert payload["fields"]["uptime"] == 120
    assert payload["fields"]["lamp_hours"] == 10
    assert payload["fields"]["power_status"] == 2


def test_stats_base_to_human_title_cases_keys() -> None:
    s = ProjectorStats(uptime=120, firmware="v1", lamp_hours=10, power_status=2)

    human = s.to_human()

    assert human["Uptime"] == "120"
    assert human["Firmware"] == "v1"
    assert human["Lamp Hours"] == "10"
    assert human["Power Status"] == "2"
