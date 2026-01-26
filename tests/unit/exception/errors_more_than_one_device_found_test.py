from __future__ import annotations

from avtools.exception.errors import EAMError, MoreThanOneDeviceFound


def test_more_than_one_device_found_inherits_and_uses_default_message() -> None:
    err = MoreThanOneDeviceFound()

    assert isinstance(err, EAMError)
    assert "Multiple devices found" in str(err)


def test_more_than_one_device_found_accepts_custom_message() -> None:
    err = MoreThanOneDeviceFound("boom")
    assert str(err) == "boom"
