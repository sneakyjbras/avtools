#!/usr/bin/env python3
"""Compile vendored SNMP MIBs into normalized metadata JSON for av-tools.

For each requested MIB module this produces ``<module>.metadata.json`` under
``src/avtools/mibs/compiled/`` with, per object symbol:

    {
      "oid":   "1.3.6.1.4.1.1718.4.1.4.3.1.3",   # base column OID
      "units": "hundredth Amps",                  # raw MIB UNITS (or null)
      "scale": 100.0,                             # divisor derived from units
      "unit":  "A",                               # canonical base unit token
      "enum":  {"0": "off", "1": "on", ...}       # if the syntax is enumerated
    }

Why two passes: pysmi's JSON code generator preserves ``UNITS`` and inline
enumerations but drops enumerations defined in a TEXTUAL-CONVENTION (e.g.
Sentry4 ``DeviceStatus``); the pysnmp code generator resolves those TC enums but
omits ``UNITS``. We run both and merge, so the resulting metadata is complete and
the runtime resolver needs only to read plain JSON (no pysmi/pysnmp MIB loading
at runtime).

Usage:
    python scripts/compile_mibs.py                # compile the default set
    python scripts/compile_mibs.py Sentry4-MIB    # compile specific module(s)
    python scripts/compile_mibs.py --check        # fail if committed JSON is stale
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCES_DIR = REPO_ROOT / "src" / "avtools" / "mibs" / "sources"
COMPILED_DIR = REPO_ROOT / "src" / "avtools" / "mibs" / "compiled"

# MIB modules to compile to metadata (the ones the exporter can use). Standard
# SMI/MIB-II modules are available as compile dependencies but are not emitted.
DEFAULT_MODULES = ["Sentry3-MIB", "Sentry4-MIB"]


def _units_to_scale(units: str | None) -> tuple[float, str | None]:
    """Map a MIB UNITS string to a numeric divisor and a canonical unit token.

    Args:
        units: Raw UNITS string from the MIB (e.g. ``"hundredth Amps"``).

    Returns:
        ``(scale, unit_token)`` where ``scale`` divides the raw integer into the
        base unit, and ``unit_token`` is a short canonical unit (``A``/``V``/
        ``W``/``%``/``Hz``/``s``) or ``None`` if unknown.
    """
    if not units:
        return 1.0, None
    u = units.strip().lower()
    scale = 1.0
    if "hundredth" in u:
        scale = 100.0
    elif "tenth" in u:
        scale = 10.0
    elif "thousandth" in u or "milli" in u:
        scale = 1000.0

    unit_token: str | None = None
    # Compound units first (so "Kilowatt-Hours"/"Volt-Amps" don't fall through to W/A).
    if "watt-hour" in u or "watthour" in u or "kilowatt-hour" in u:
        unit_token = "kWh" if "kilo" in u else "Wh"
    elif "volt-amp" in u or "voltamp" in u:
        unit_token = "VA"
    elif "amp" in u:
        unit_token = "A"
    elif "volt" in u:
        unit_token = "V"
    elif "watt" in u:
        unit_token = "W"
    elif "percent" in u or u.endswith("%"):
        unit_token = "%"
    elif "hertz" in u or u == "hz":
        unit_token = "Hz"
    elif "second" in u:
        unit_token = "s"
    return scale, unit_token


def _compile_json(modules: list[str], out_dir: Path) -> dict[str, dict]:
    """Compile modules with pysmi JSON codegen (OID + units + inline enums)."""
    from pysmi.codegen import JsonCodeGen
    from pysmi.compiler import MibCompiler
    from pysmi.parser import SmiStarParser
    from pysmi.reader import FileReader
    from pysmi.searcher import StubSearcher
    from pysmi.writer import FileWriter

    comp = MibCompiler(
        SmiStarParser(), JsonCodeGen(), FileWriter(str(out_dir)).set_options(suffix=".json")
    )
    comp.add_sources(FileReader(str(SOURCES_DIR)))
    comp.add_searchers(StubSearcher(*JsonCodeGen.baseMibs))
    comp.compile(*modules, **{"genTexts": True})

    result: dict[str, dict] = {}
    for mod in modules:
        p = out_dir / f"{mod}.json"
        if p.exists():
            result[mod] = json.loads(p.read_text())
    return result


def _compile_enums(modules: list[str]) -> dict[str, dict[str, dict]]:
    """Resolve full enums (incl. TEXTUAL-CONVENTION) via the pysnmp codegen."""
    from pysmi.codegen import PySnmpCodeGen
    from pysmi.compiler import MibCompiler
    from pysmi.parser import SmiStarParser
    from pysmi.reader import FileReader
    from pysmi.searcher import StubSearcher
    from pysmi.writer import PyFileWriter
    from pysnmp.smi import builder

    py_out = Path(tempfile.mkdtemp())
    comp = MibCompiler(SmiStarParser(), PySnmpCodeGen(), PyFileWriter(str(py_out)))
    comp.add_sources(FileReader(str(SOURCES_DIR)))
    comp.add_searchers(StubSearcher(*PySnmpCodeGen.baseMibs))
    comp.compile(*modules)

    mb = builder.MibBuilder()
    mb.addMibSources(builder.DirMibSource(str(py_out)))
    mb.loadModules(*modules)

    enums: dict[str, dict[str, dict]] = {}
    for mod in modules:
        enums[mod] = {}
        syms = mb.mibSymbols.get(mod, {})
        for name, node in syms.items():
            try:
                nv = node.getSyntax().namedValues
                if nv:
                    enums[mod][name] = {str(int(v)): str(k) for k, v in nv.items()}
            except Exception:
                continue
    return enums


def build_metadata(modules: list[str]) -> dict[str, dict]:
    """Compile modules and return ``{module: {symbol: meta}}``."""
    with tempfile.TemporaryDirectory() as td:
        json_mibs = _compile_json(modules, Path(td))
    enums = _compile_enums(modules)

    out: dict[str, dict] = {}
    for mod in modules:
        mj = json_mibs.get(mod, {})
        men = enums.get(mod, {})
        symbols: dict[str, dict] = {}
        for sym, obj in mj.items():
            if not isinstance(obj, dict) or "oid" not in obj:
                continue
            if obj.get("class") not in (
                "objecttype",
                "mibscalar",
                "mibtablecolumn",
                None,
            ):
                # keep only object-types that have an OID worth resolving
                pass
            oid = obj.get("oid")
            if not oid:
                continue
            units = obj.get("units")
            scale, unit_token = _units_to_scale(units)
            entry: dict = {"oid": oid}
            if units:
                entry["units"] = units
            entry["scale"] = scale
            if unit_token:
                entry["unit"] = unit_token
            # inline enum from JSON, else TC-resolved enum from pysnmp pass
            inline = obj.get("syntax", {}).get("constraints", {}).get("enumeration")
            enum = inline or men.get(sym)
            if enum:
                entry["enum"] = {str(int(v)): k for k, v in enum.items()} if inline else enum
            symbols[sym] = entry
        out[mod] = symbols
    return out


def write_metadata(metadata: dict[str, dict]) -> list[Path]:
    """Write one ``<module>.metadata.json`` per module; return written paths."""
    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for mod, symbols in metadata.items():
        p = COMPILED_DIR / f"{mod}.metadata.json"
        p.write_text(json.dumps(symbols, indent=2, sort_keys=True) + "\n")
        written.append(p)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile vendored MIBs to metadata JSON.")
    ap.add_argument("modules", nargs="*", default=None, help="MIB module names")
    ap.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if committed metadata differs from a fresh compile.",
    )
    args = ap.parse_args()
    modules = args.modules or DEFAULT_MODULES

    metadata = build_metadata(modules)

    if args.check:
        stale = []
        for mod, symbols in metadata.items():
            p = COMPILED_DIR / f"{mod}.metadata.json"
            fresh = json.dumps(symbols, indent=2, sort_keys=True) + "\n"
            if not p.exists() or p.read_text() != fresh:
                stale.append(mod)
        if stale:
            print(f"STALE metadata for: {', '.join(stale)} (run scripts/compile_mibs.py)")
            return 1
        print(f"MIB metadata up to date for: {', '.join(metadata)}")
        return 0

    written = write_metadata(metadata)
    for p in written:
        sym_count = len(json.loads(p.read_text()))
        print(f"wrote {p.relative_to(REPO_ROOT)}  ({sym_count} symbols)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
