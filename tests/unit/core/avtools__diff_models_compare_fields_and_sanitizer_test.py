from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def test_diff_models_respects_compare_fields_callable() -> None:
    from avtools.core.av_tools import AVTools

    class M(BaseModel):
        a: int
        b: int

        def avtools_compare_fields(self) -> list[str]:
            return ["a"]

    old = M(a=1, b=1)
    new = M(a=2, b=999)

    diff = AVTools._diff_models(None, old, new)  # type: ignore[arg-type]

    assert diff == {"a": 2}


def test_diff_models_sanitizes_equipment_dict_before_comparing(
    monkeypatch: Any,
) -> None:
    import avtools.core.av_tools as av_mod
    from avtools.core.av_tools import AVTools

    # Patch the Equipment symbol used by isinstance(new, Equipment).
    class DummyEquipment(BaseModel):
        description: str

    monkeypatch.setattr(av_mod, "Equipment", DummyEquipment)

    calls: dict[str, Any] = {}

    class DummySanitizer:
        def sanitize_dict_in_place(self, d: dict[str, Any], *, compare_fields: Any = None) -> None:
            calls["called"] = True
            calls["compare_fields"] = compare_fields
            # Emulate the real sanitizer cleaning dirty EAM values.
            if "description" in d and isinstance(d["description"], str):
                d["description"] = d["description"].replace("\u0000", "").strip()

    av = object.__new__(AVTools)
    av._eam_sanitizer = DummySanitizer()

    class Cached:
        # compare-field whitelist comes from the cached record
        _compare_fields = ["description"]
        description = "CLEAN"

    old = Cached()
    new = DummyEquipment(description="CLEAN\u0000")

    diff = av._diff_models(old, new)
    assert calls.get("called") is True
    assert calls.get("compare_fields") == ["description"]
    assert diff == {}
