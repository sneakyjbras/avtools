from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from eam_rest_client import Equipment


class EAMTextSanitizer:
    """Sanitize dirty EAM strings (trim leading/trailing junk whitespace).

    Only sanitizes a small whitelist of fields AVTools persists/diffs against.
    """

    # Leading/trailing: standard whitespace + NBSP + figure space + narrow NBSP +
    # zero-width space + LRM/RLM + BOM.
    _TEXT_TRIM_RE = re.compile(r"^[\s   ​‎‏﻿]+|[\s   ​‎‏﻿]+$")

    # Fields that AVTools stores/diffs on (important + optional).
    EAM_TEXT_FIELDS: tuple[str, ...] = (
        # Keys used in downstream joins/filters
        "serial_number",
        "description",
        # Common persisted/diffed device fields
        "model",
        "manufacturer_code",
        "class_code",
        "category_code",
        "department_code",
        "status_code",
        "status_desc",
        # Optional but commonly dirty fields (esp. positions grid)
        "alias",
        "assigned_to",
        "primary_system",
        "hierarchy_position_code",
        "hierarchy_asset_code",
        "hierarchy_location_code",
        "variable2",
    )

    def sanitize_text(self, value: Any) -> str | None:
        """Return a sanitized string or None.

        - Converts non-strings to str only for serial_number use-cases.
        - Trims leading/trailing whitespace (incl. NBSP/BOM/zero-width).
        - Preserves internal spacing.
        """
        if value is None:
            return None

        s = value if isinstance(value, str) else str(value)
        if not s:
            return None

        s2 = self._TEXT_TRIM_RE.sub("", s)
        return s2 or None

    def clean_items(self, items: list[Equipment]) -> list[Equipment]:
        """Sanitize selected text fields for a list of Equipment.

        Returns a new list (prefers non-mutating copies for frozen models).
        """
        out: list[Equipment] = []
        for eq in items:
            updates: dict[str, Any] = {}

            for field in self.EAM_TEXT_FIELDS:
                raw = getattr(eq, field, None)
                if raw is None:
                    continue

                # Avoid stringifying dates/objects. Only serial_number is allowed to
                # be coerced to str.
                if field != "serial_number" and not isinstance(raw, str):
                    continue

                cleaned = self.sanitize_text(raw)
                if raw != cleaned:
                    updates[field] = cleaned

            if not updates:
                out.append(eq)
                continue

            # Prefer non-mutating copies (works for frozen models).
            try:
                if hasattr(eq, "model_copy"):
                    out.append(eq.model_copy(update=updates))  # type: ignore[attr-defined]
                    continue
                if hasattr(eq, "copy"):
                    out.append(eq.copy(update=updates))  # type: ignore[attr-defined]
                    continue
            except Exception:
                pass

            # Last resort: try in-place set, then fall back to a re-construct.
            try:
                for k, v in updates.items():
                    setattr(eq, k, v)
                out.append(eq)
                continue
            except Exception:
                pass

            try:
                payload = eq.dict(exclude_unset=False)  # type: ignore[attr-defined]
                payload.update(updates)
                out.append(eq.__class__(**payload))
            except Exception:
                out.append(eq)

        return out

    def sanitize_dict_in_place(
        self,
        new_data: dict[str, Any],
        *,
        compare_fields: Iterable[str] | None,
    ) -> None:
        """Sanitize a dict (typically produced by Equipment.dict()).

        Only sanitizes keys that are both:
        - in EAM_TEXT_FIELDS
        - and in compare_fields (when compare_fields is provided)
        """
        if compare_fields is None:
            keys = (k for k in self.EAM_TEXT_FIELDS if k in new_data)
        else:
            cf = set(compare_fields)
            keys = (k for k in self.EAM_TEXT_FIELDS if k in cf and k in new_data)

        for k in keys:
            v = new_data.get(k)
            if v is None:
                continue

            if k != "serial_number" and not isinstance(v, str):
                continue

            new_data[k] = self.sanitize_text(v)
