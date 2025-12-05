from __future__ import annotations

from pydantic import BaseModel

from avtools.av_tools import AVTools


class Dummy(BaseModel):
    a: int
    b: str
    c: float | None = None


def _diff(old: Dummy, new: Dummy) -> dict:
    # _diff_models doesn't use `self`, so we can pass None
    return AVTools._diff_models(None, old, new)  # type: ignore[arg-type]


def test_diff_models_no_changes():
    old = Dummy(a=1, b="x", c=3.14)
    new = Dummy(a=1, b="x", c=3.14)

    diff = _diff(old, new)

    assert diff == {}


def test_diff_models_single_change():
    old = Dummy(a=1, b="x", c=3.14)
    new = Dummy(a=1, b="x", c=2.71)

    diff = _diff(old, new)

    assert diff == {"c": 2.71}


def test_diff_models_multiple_changes():
    old = Dummy(a=1, b="x", c=3.14)
    new = Dummy(a=2, b="y", c=3.14)

    diff = _diff(old, new)

    assert diff == {"a": 2, "b": "y"}


def test_diff_models_ignores_unset_fields_in_new():
    old = Dummy(a=1, b="x", c=None)
    # omit `c` → with exclude_unset=True this field is not in new_data
    new = Dummy(a=1, b="x")

    diff = _diff(old, new)

    # since `c` is not present in new_data, it should not appear in the diff
    assert diff == {}
