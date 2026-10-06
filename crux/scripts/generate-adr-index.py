#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""generate-adr-index.py — the vendored regenerator for <docs_dir>/adrs/index.md.

Per ADR-0063 (SP-4): the ADR index becomes a *derived* artifact walking BOTH tiers.
The active table lists active ADRs (Proposed/Accepted) from `adrs/`; the collapsed
`## Archived (N)` roster lists Superseded/Deprecated ADRs from `adrs/archive/`,
ordered by id descending. Byte-stable (no date) so a crash-stale index is drift the
gate catches, not a special repair case (ADR-0063 Decision 4). Enrolled in the
regenerative-outputs roster.

Usage:
  generate-adr-index.py [--repo-root DIR]        rewrite adrs/index.md
  generate-adr-index.py --dry-run [...]          exit 1 + JSON diff if drift

Exit: 0 clean/written · 1 drift (JSON on stdout) · 2 env error.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adr_frontmatter  # noqa: E402


def _tree_name(root: Path) -> str:
    cfg = root / ".bionic.yml"
    if cfg.exists():
        m = re.search(r"^docs_dir\s*:\s*[\"']?([^\"'\s#]+)", cfg.read_text(encoding="utf-8"),
                      re.MULTILINE)
        if m:
            return m.group(1)
    return "bionic"


def _load(paths) -> list[dict]:
    recs = []
    for p in sorted(paths):
        path = Path(p)
        # The fence is read through the ONE shared reader. `validated_adr_paths`
        # already refused this run if any input failed it, so `None` here is
        # unreachable — and it raises rather than skipping, because a second
        # copy of the fence test with a bare `continue` is the paired construct
        # this change exists to remove.
        block = adr_frontmatter.frontmatter_block(path.read_text(encoding="utf-8"))
        if block is None:
            raise adr_frontmatter.FenceValidationError(
                [{"file": str(path), "error": adr_frontmatter.FENCE_ERROR}])
        import yaml
        fm = yaml.safe_load(block) or {}
        recs.append({
            "num": int(str(fm["id"]).split("-")[-1]),
            "id": str(fm["id"]),
            "title": str(fm["title"]),
            "status": str(fm["status"]),
            "date": str(fm["date"]),
            "supersedes": fm.get("supersedes") or [],
            "amends": fm.get("amends") or [],
            "superseded_by": fm.get("superseded_by"),
            "tags": fm.get("tags") or [],
        })
    recs.sort(key=lambda r: r["num"], reverse=True)
    return recs


def _sup(r: dict) -> str:
    base = ", ".join(r["supersedes"]) if r["supersedes"] else "—"
    if r["amends"]:
        base += f" (amends {', '.join(r['amends'])})"
    return base


def _tags(r: dict) -> str:
    t = r["tags"]
    if isinstance(t, str):
        return t
    return ", ".join(t) if t else "—"


def render(active: list[dict], archived: list[dict]) -> str:
    out = ["# ADRs", "",
           "| id | title | status | date | supersedes | superseded_by | tags |",
           "|----|-------|--------|------|------------|---------------|------|"]
    for r in active:
        out.append(f"| {r['id']} | {r['title']} | {r['status']} | {r['date']} | "
                   f"{_sup(r)} | {r['superseded_by'] or '—'} | {_tags(r)} |")
    out += ["", f"## Archived ({len(archived)})", "", "| id | title | status |", "|----|-------|--------|"]
    for r in archived:
        out.append(f"| {r['id']} | {r['title']} | {r['status']} |")
    return "\n".join(out) + "\n"


def build(root: Path) -> tuple[Path, str]:
    adrs = root / _tree_name(root) / "adrs"
    if not adrs.is_dir():
        raise FileNotFoundError(f"{adrs} not found")
    # REFUSAL BEFORE WRITING. Both tiers are validated here, before the caller
    # reads the existing index to diff against or opens it to write, so one
    # unreadable ADR cannot shorten the index and cannot create one that did
    # not exist. Raises FenceValidationError, which `main` maps to exit 1.
    adr_frontmatter.validated_adr_paths(adrs, adr_frontmatter.repo_relative(root))
    active = _load(glob.glob(str(adrs / "ADR-*.md")))
    archived = _load(glob.glob(str(adrs / "archive" / "ADR-*.md")))
    return adrs / "index.md", render(active, archived)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate adrs/index.md from both ADR tiers.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        path, want = build(root)
    except adr_frontmatter.FenceValidationError as exc:
        return adr_frontmatter.print_validation_errors(
            exc.errors, "generate-adr-index", sys.stdout, sys.stderr)
    except Exception as exc:
        sys.stderr.write(f"generate-adr-index: {type(exc).__name__}: {exc}\n")
        return 2
    have = path.read_text(encoding="utf-8") if path.exists() else ""
    if args.dry_run:
        drift = have != want
        print(json.dumps({"drift": drift, "path": str(path.relative_to(root))}, sort_keys=True))
        return 1 if drift else 0
    path.write_text(want, encoding="utf-8")
    print(json.dumps({"written": str(path.relative_to(root))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
