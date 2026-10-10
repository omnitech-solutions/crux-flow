#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["PyYAML>=6.0,<7"]
# ///
"""generate-flow-surface.py — the regenerator for Crux Flow's surface record.

One authored input, `crux/surface/declaration.json` (what Flow requires, supports and treats as a hard
change), plus the plugin source itself, projected into `crux/surface/record.json`: every `crux-flow` command and
flag, the upstream scripts and symbols Flow depends on, each skill's frontmatter-contract digest, each role's
tools and delegation targets per host and mode, the schema versions Flow reads, the drift roster, the patched
upstream files, the upstream text Flow rewrites and the hooks. It never merges: the record is rewritten whole.

Two verdicts, as in `check-drift`:
    DRIFT   the live surface differs from the record; the JSON lists each change with a severity (hard|notice).
    BROKEN  an invariant Flow needs does not hold (a removed script Flow calls, a role that gained a delegation
            target, a schema version Flow does not read). Regenerating cannot repair it, and it refuses to write.

Scope `plugin-authoring`: outside the plugin's source checkout it prints the `surface_absent` payload and exits 0.

Exit codes (the sibling-regenerator contract):
    0  clean — wrote (or, under --dry-run, found no drift), or the surface is absent
    1  drift (--dry-run) or broken, JSON on stdout
    2  capability/environment error, message on stderr
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import authoring_scope as _scope  # noqa: E402
from crux.flow import surface  # noqa: E402
from crux.flow.common import FlowError  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report drift; write nothing")
    ap.add_argument("--repo-root", default=None, help="repo root to inspect (default: the working directory)")
    args = ap.parse_args(argv)
    root = _scope.resolve_repo_root(args.repo_root)
    if not _scope.is_authoring_checkout(root, __file__):
        return _scope.print_surface_absent()
    try:
        code, payload = surface.check(PLUGIN) if args.dry_run else surface.sync(PLUGIN)
    except FlowError as exc:
        print(json.dumps({"validation_errors": [{"field": "surface", "error": str(exc)}]}, indent=2))
        return 1
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"generate-flow-surface.py: {exc}\n")
        return 2
    print(json.dumps(payload, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
