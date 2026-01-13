from __future__ import annotations

from pydantic import BaseModel

from avtools.core.av_tools import AVTools


class Dummy(BaseModel):
    hp: int
    mp: int
    crit: float | None = None
    year: int | None = None


def _diff(old: Dummy, new: Dummy) -> dict:
    # _diff_models doesn't use `self`, so None is fine
    return AVTools._diff_models(None, old, new)  # type: ignore[arg-type]


def test_diff_models_no_changes():
    old = Dummy(hp=34, mp=38, crit=39.0, year=2025)
    new = Dummy(hp=34, mp=38, crit=39.0, year=2025)

    diff = _diff(old, new)

    assert diff == {}


def test_diff_models_single_change():
    old = Dummy(hp=34, mp=38, crit=39.0, year=2025)
    new = Dummy(hp=34, mp=38, crit=404.0, year=2025)

    diff = _diff(old, new)

    assert diff == {"crit": 404.0}


def test_diff_models_multiple_changes():
    # starting values
    old = Dummy(hp=14, mp=1911, crit=39.0, year=1978)
    # changed values
    new = Dummy(hp=2137, mp=1978, crit=38.0, year=2025)

    diff = _diff(old, new)

    assert diff == {
        "hp": 2137,
        "mp": 1978,
        "crit": 38.0,
        "year": 2025,
    }


def test_diff_models_ignores_unset_fields_in_new():
    old = Dummy(hp=34, mp=38, crit=None, year=2025)
    # omit `crit` and `year` → exclude_unset=True means they are ignored
    new = Dummy(hp=34, mp=38)

    diff = _diff(old, new)

    assert diff == {}
