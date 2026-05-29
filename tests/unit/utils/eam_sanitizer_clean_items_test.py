from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from avtools.utils.eam_sanitizer import EAMTextSanitizer


@dataclass
class DummyEq:
    # Only define a couple of fields used by sanitizer.
    serial_number: Any = None
    description: Any = None
    model: Any = None


class DummyEqWithModelCopy(DummyEq):
    """Simulate a frozen pydantic model with model_copy(update=...)."""

    def model_copy(self, *, update: dict[str, Any]):  # type: ignore[override]
        new = DummyEqWithModelCopy(**{**self.__dict__})
        for k, v in update.items():
            setattr(new, k, v)
        return new


class DummyEqWithCopy(DummyEq):
    """Simulate pydantic v1 .copy(update=...)."""

    def copy(self, *, update: dict[str, Any]):  # type: ignore[override]
        new = DummyEqWithCopy(**{**self.__dict__})
        for k, v in update.items():
            setattr(new, k, v)
        return new


class DummyEqNoSetattr:
    """Force the sanitizer down the dict()/reconstruct path.

    Notes
    - We must allow initial attribute assignment during __init__.
    - After that, any setattr should fail (simulating a frozen model).
    """

    def __init__(
        self, *, serial_number: Any = None, description: Any = None, model: Any = None
    ) -> None:
        object.__setattr__(self, "serial_number", serial_number)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "model", model)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("frozen")

    def dict(self, *, exclude_unset: bool = False):  # type: ignore[override]
        return {
            "serial_number": self.serial_number,
            "description": self.description,
            "model": self.model,
        }


def test_clean_items_uses_model_copy_when_available_and_does_not_mutate_original() -> None:
    s = EAMTextSanitizer()

    original = DummyEqWithModelCopy(serial_number=123, description="  hello  ")
    out = s.clean_items([original])

    assert out[0] is not original
    assert out[0].serial_number == "123"
    assert out[0].description == "hello"

    # Original remains unchanged
    assert original.serial_number == 123
    assert original.description == "  hello  "


def test_clean_items_uses_copy_when_model_copy_is_not_available() -> None:
    s = EAMTextSanitizer()

    original = DummyEqWithCopy(serial_number="  SN  ", description="\u00a0X\u00a0")
    out = s.clean_items([original])

    assert out[0] is not original
    assert out[0].serial_number == "SN"
    assert out[0].description == "X"


def test_clean_items_falls_back_to_reconstruct_when_setattr_fails() -> None:
    s = EAMTextSanitizer()

    original = DummyEqNoSetattr(serial_number=7, description="  D  ", model=123)
    out = s.clean_items([original])

    # New instance created via __class__(**payload)
    assert out[0] is not original

    # serial_number is allowed to stringify; model is not (non-str and not serial_number)
    assert out[0].serial_number == "7"
    assert out[0].description == "D"
    assert out[0].model == 123
