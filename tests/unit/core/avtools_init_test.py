from __future__ import annotations

from typing import Any


def test_init_sets_expected_fields(monkeypatch: Any) -> None:
    """AVTools.__init__ wires up helper objects and internal flags."""
    import avtools.core.av_tools as av_mod

    created: dict[str, Any] = {}

    class DummyPostgresClient:
        def __init__(self, dbod_url: str) -> None:
            created["dbod_url"] = dbod_url

    class DummySanitizer:
        pass

    class DummyLogger:
        def __init__(self, name: str) -> None:
            self.name = name

    monkeypatch.setattr(av_mod, "PostgresClient", DummyPostgresClient)
    monkeypatch.setattr(av_mod, "EAMTextSanitizer", DummySanitizer)
    monkeypatch.setattr(av_mod.structlog, "get_logger", lambda name: DummyLogger(name))

    av = av_mod.AVTools("postgresql://example", logs=True)

    assert created["dbod_url"] == "postgresql://example"
    assert av.logs is True
    assert av._landb_initialized is False
    assert isinstance(av._eam_sanitizer, DummySanitizer)
    assert getattr(av.logger, "name", "") == "AVTools"
