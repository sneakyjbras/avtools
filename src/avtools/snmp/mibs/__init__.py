"""Runtime resolver for compiled MIB metadata.

Reads the normalized ``<module>.metadata.json`` files produced by
``scripts/compile_mibs.py`` and exposes per-symbol OID, unit scaling and enum
lookups. This is pure JSON — no pysmi/pysnmp MIB loading at runtime — so it is
safe and fast on the RHEL9 / pysnmp-4.4.12 target.

Handlers use it in a "MIB-backed when available, hardcoded otherwise" pattern:
if a symbol resolves, its OID / scale / enum come from the compiled MIB; if the
MIB is absent (not vendored, or failed to compile) the handler falls back to the
OID and scale baked into the code.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path


class MibResolver:
    """Resolves OID / scale / enum metadata for compiled MIB modules."""

    def __init__(self, metadata: dict[str, dict[str, dict]]) -> None:
        """Initialize from a ``{module: {symbol: meta}}`` mapping."""
        self._meta = metadata

    @classmethod
    def from_dir(cls, compiled_dir: Path) -> "MibResolver":
        """Load every ``*.metadata.json`` under ``compiled_dir``.

        Args:
            compiled_dir: Directory holding ``<module>.metadata.json`` files.

        Returns:
            A resolver populated with whatever compiled MIBs were found (possibly
            empty, which simply means every lookup falls back).
        """
        meta: dict[str, dict[str, dict]] = {}
        if compiled_dir.is_dir():
            for path in sorted(compiled_dir.glob("*.metadata.json")):
                module = path.name[: -len(".metadata.json")]
                try:
                    meta[module] = json.loads(path.read_text())
                except Exception:
                    continue
        return cls(meta)

    def available(self, module: str) -> bool:
        """Return True if the given MIB module was compiled and loaded."""
        return module in self._meta and bool(self._meta[module])

    def _entry(self, module: str, symbol: str) -> dict | None:
        return self._meta.get(module, {}).get(symbol)

    def oid(self, module: str, symbol: str, *indices: int) -> str | None:
        """Return the fully-indexed numeric OID for a symbol, or ``None``.

        Args:
            module: MIB module name (e.g. ``"Sentry4-MIB"``).
            symbol: Object symbol (e.g. ``"st4LineCurrent"``).
            *indices: Table/instance index components appended to the base OID
                (use ``0`` for scalars).

        Returns:
            Dotted OID string, or ``None`` if the symbol is not in a loaded MIB.
        """
        entry = self._entry(module, symbol)
        if not entry or "oid" not in entry:
            return None
        base = entry["oid"]
        if indices:
            base = base + "." + ".".join(str(i) for i in indices)
        return base

    def scale(self, module: str, symbol: str) -> float | None:
        """Return the unit divisor for a symbol (from its MIB UNITS), or ``None``."""
        entry = self._entry(module, symbol)
        if not entry:
            return None
        return entry.get("scale", 1.0)

    def unit(self, module: str, symbol: str) -> str | None:
        """Return the canonical unit token for a symbol (``A``/``V``/...), or ``None``."""
        entry = self._entry(module, symbol)
        if not entry:
            return None
        return entry.get("unit")

    def enum(self, module: str, symbol: str) -> dict[int, str] | None:
        """Return the integer→name enum for a symbol's syntax, or ``None``.

        Args:
            module: MIB module name.
            symbol: Object symbol.

        Returns:
            Mapping of integer code to label (e.g. ``{0: "off", 1: "on"}``), or
            ``None`` if the symbol is not enumerated / not loaded.
        """
        entry = self._entry(module, symbol)
        if not entry or "enum" not in entry:
            return None
        try:
            return {int(k): v for k, v in entry["enum"].items()}
        except Exception:
            return None


def _compiled_dir() -> Path:
    """Locate the packaged ``mibs/compiled`` directory."""
    try:
        return Path(str(resources.files("avtools").joinpath("mibs", "compiled")))
    except Exception:
        return Path(__file__).resolve().parents[2] / "mibs" / "compiled"


@lru_cache(maxsize=1)
def get_resolver() -> MibResolver:
    """Return the process-wide MIB resolver (loaded once from packaged metadata)."""
    return MibResolver.from_dir(_compiled_dir())
