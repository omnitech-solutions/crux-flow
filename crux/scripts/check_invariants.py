#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""check_invariants.py — reference checker for the invariants concern (docs/AGENTS.md §15).

Implements the pin-level aggregation and the five CHK-INV audit rules against
the ledger (`<docs_dir>/invariants/<slug>.md` frontmatter) + the reconciliation
manifest (`bionic/invariants/reconciliation.yml`). `audit-docs` describes these
rules in prose; this is the executable reference implementation the CHK-INV
rules may delegate to (the same pattern CHK-CFG-1 → crux-config.py and
CHK-CAT-3 → validate-catalog.py use).

Exit convention (crux standard): 0 = clean, 1 = findings (valid JSON on stdout),
non-zero with empty stdout = crash.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print(json.dumps({"error": "PyYAML required (run under uv)"}))
    raise SystemExit(2)

RATIFICATIONS = {"observed", "ratified", "rejected", "retired"}
RESULTS = {"pass", "fail", "stale", "none"}
# Aggregation precedence (docs/AGENTS.md §15.4): fail > stale > pass > none.
_PRECEDENCE = ["fail", "stale", "pass", "none"]


def _parse_frontmatter(text: str) -> dict | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else None


def aggregate(results: list[str]) -> str:
    """Pin-level last_result from its checks' results (fail>stale>pass>none)."""
    present = {r for r in results if r in RESULTS}
    for level in _PRECEDENCE:
        if level in present:
            return level
    return "none"


def _tree_name(root) -> str:
    """Resolve the tree directory via bionic_config, falling back to the default."""
    _PATH = Path(__file__).resolve().parent / "bionic_config.py"
    import importlib.util as _ilu
    import sys as _sys
    _key = "_bionic_config"
    _m = _sys.modules.get(_key)
    if _m is None:
        _s = _ilu.spec_from_file_location(_key, _PATH)
        _m = _ilu.module_from_spec(_s)
        _s.loader.exec_module(_m)
        _sys.modules[_key] = _m   # cache so class identity holds across calls
    return _m.resolve_tree_name(root)


def _require_supported_schema(tree) -> None:
    """Refuse an unmigrated tree rather than reporting results that would be wrong.

    An invariants audit reporting clean because it cannot find its own
    reconciliation surface is the outcome this refusal exists to prevent.
    """
    import importlib.util as _ilu
    import sys as _sys
    _key = "_bionic_config"
    _m = _sys.modules.get(_key)
    if _m is None:
        _s = _ilu.spec_from_file_location(
            _key, Path(__file__).resolve().parent / "bionic_config.py")
        _m = _ilu.module_from_spec(_s)
        _s.loader.exec_module(_m)
        _sys.modules[_key] = _m
    # A loader failure must NOT disable the gate. An earlier version returned
    # here, which meant a damaged bionic_config.py let an unmigrated tree audit
    # CLEAN — the exact outcome this refusal exists to prevent, reached through
    # the refusal's own machinery.
    _m.require_schema_version(Path(tree))


def check(root: Path, docs_dir: str | None = None) -> dict:
    if docs_dir is None:
        docs_dir = _tree_name(root)
    _require_supported_schema(root / docs_dir)
    ledger_dir = root / docs_dir / "invariants"
    recon_path = root / docs_dir / "invariants" / "reconciliation.yml"

    pins: dict[str, dict] = {}
    broken: list[str] = []
    warning: list[str] = []

    seen_ids: dict[str, str] = {}
    if ledger_dir.is_dir():
        for page in sorted(ledger_dir.glob("*.md")):
            if page.name == "index.md":
                continue
            fm = _parse_frontmatter(page.read_text(encoding="utf-8"))
            if not fm or "id" not in fm:
                broken.append(f"CHK-INV-BIJECTION: {page.name} has no parseable pin id")
                continue
            pid = str(fm["id"])
            if pid in seen_ids:
                broken.append(f"CHK-INV-BIJECTION: duplicate pin id {pid} ({page.name} and {seen_ids[pid]})")
                continue
            seen_ids[pid] = page.name
            pins[pid] = {"ratification": fm.get("ratification", "observed"), "results": []}

    # reconciliation: map checks → pins, collect last_result per pin
    entries = []
    if recon_path.is_file():
        recon = yaml.safe_load(recon_path.read_text(encoding="utf-8")) or {}
        entries = recon.get("checks", []) or []
    for e in entries:
        pid = str(e.get("pin_id", ""))
        cid = e.get("check_id", "<unknown>")
        lr = e.get("last_result", "none")
        if pid not in pins:
            broken.append(f"CHK-INV-ORPHAN-CHECK: check {cid} maps to pin {pid or '<none>'} with no ledger page")
            continue
        pins[pid]["results"].append(lr)

    observed_count = 0
    for pid, p in pins.items():
        agg = aggregate(p["results"])
        has_check = len(p["results"]) > 0
        rat = p["ratification"]
        if rat == "observed":
            observed_count += 1
        if rat == "ratified":
            if not has_check or agg == "none":
                broken.append(f"CHK-INV-DECORATION: ratified pin {pid} has no resolvable check / last_result none")
            elif agg == "fail":
                broken.append(f"CHK-INV-FAILING: ratified pin {pid} aggregate last_result=fail")
            elif agg == "stale":
                warning.append(f"CHK-INV-FAILING: ratified pin {pid} aggregate last_result=stale")
        if rat == "observed" and agg == "pass":
            warning.append(f"CHK-INV-DANGEROUS: observed pin {pid} has aggregate last_result=pass")

    return {"broken": broken, "warning": warning, "survey_debt": observed_count, "pins": len(pins)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_invariants")
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--docs-dir", default=None)
    args = ap.parse_args(argv)
    try:
        result = check(args.root.resolve(), args.docs_dir)
    except Exception as exc:  # noqa: BLE001
        # An unmigrated, unresolvable or unrecognizable tree is an environment
        # problem, not a findings verdict — exit 2 with a message on stderr,
        # never a traceback and never a clean-looking exit 1. Matching the real
        # class rather than its name: the module is cached, so identity holds.
        _bc = sys.modules.get("_bionic_config")
        refusals = tuple(
            c for c in (getattr(_bc, "BionicConfigError", None),
                        getattr(_bc, "SchemaVersionError", None)) if c is not None
        ) or (RuntimeError,)
        if isinstance(exc, refusals) or type(exc).__name__ in (
                "SchemaVersionError", "BionicConfigError"):
            sys.stderr.write(f"check_invariants: {exc}\n")
            return 2
        raise
    print(json.dumps(result, indent=2))
    return 1 if (result["broken"] or result["warning"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
