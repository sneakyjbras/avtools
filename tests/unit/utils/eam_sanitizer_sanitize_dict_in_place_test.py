from __future__ import annotations

from avtools.utils.eam_sanitizer import EAMTextSanitizer


def test_sanitize_dict_in_place_filters_by_compare_fields_and_skips_non_strings() -> None:
    s = EAMTextSanitizer()

    data = {
        "serial_number": 123,
        "description": "  hello  ",
        "model": 999,  # non-str and not serial_number -> must be left as-is
        "status_desc": "\u00a0Installed\u00a0",
        "extra": "  should_not_change  ",
    }

    s.sanitize_dict_in_place(data, compare_fields={"serial_number", "description"})

    assert data["serial_number"] == "123"
    assert data["description"] == "hello"
    assert data["model"] == 999
    assert data["status_desc"] == "\u00a0Installed\u00a0"  # not in compare_fields
    assert data["extra"] == "  should_not_change  "


def test_sanitize_dict_in_place_when_compare_fields_is_none_sanitizes_whitelist_keys_present() -> (
    None
):
    s = EAMTextSanitizer()

    data = {
        "description": "  hi  ",
        "status_desc": "\u00a0Installed\u00a0",
        "model": "  M1  ",
    }

    s.sanitize_dict_in_place(data, compare_fields=None)

    assert data["description"] == "hi"
    assert data["status_desc"] == "Installed"
    assert data["model"] == "M1"
