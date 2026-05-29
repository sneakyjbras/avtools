from __future__ import annotations

from avtools.utils.eam_sanitizer import EAMTextSanitizer


def test_sanitize_text_none_and_empty_return_none() -> None:
    s = EAMTextSanitizer()

    assert s.sanitize_text(None) is None
    assert s.sanitize_text("") is None
    assert s.sanitize_text("   ") is None


def test_sanitize_text_trims_unicode_whitespace_and_preserves_internal_spacing() -> None:
    s = EAMTextSanitizer()

    # NBSP around the string
    assert s.sanitize_text("\u00a0hello\u00a0") == "hello"

    # Narrow NBSP + zero-width space + BOM in the regex whitelist
    dirty = "\u202f\u200b\ufeffA  B\u202f"
    assert s.sanitize_text(dirty) == "A  B"  # internal double-space preserved


def test_sanitize_text_stringifies_non_strings() -> None:
    s = EAMTextSanitizer()

    assert s.sanitize_text(123) == "123"
