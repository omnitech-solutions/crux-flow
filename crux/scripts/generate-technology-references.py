#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["PyYAML>=6.0,<7"]
# ///
"""generate-technology-references.py — the regenerator for a project's technology router.

One source of truth — the `technology` section of the project's `.crux-flow.yml`, the shipped
catalog `crux/catalog/flow-technology.json`, and the versions the project's manifests declare —
projected into the router skill `.agents/skills/<router>/SKILL.md` (and a copy for each host that
reads only its own skill folder), between the markers:

    <!-- BEGIN GENERATED: technology-routes -->
    <!-- END GENERATED: technology-routes -->

The whole file is rendered from the plugin's template; the one hand-written section, between the
`PROJECT: notes` markers, is carried over unchanged. It never merges.

Beside the byte comparison it checks CLOSURE, and a closure failure is BROKEN, never drift: every
referenced page, pin and command exists; every technology a manifest declares is routed or
explicitly excluded; every routed technology is declared; exactly one router carries the name.
Regenerating does not repair those — the named input does.

With `owner: project` the router is the project's own file. This script then only checks it and
writes nothing, in either mode.

Scope `project`. A project with no `technology` section owns no surface: the script prints the
`surface_absent` payload and exits 0, which `check-drift` renders N/A.

Exit codes (the sibling-regenerator contract):
    0  clean — wrote (or, under --dry-run, found no drift), or the surface is absent
    1  drift (--dry-run) or validation error, JSON on stdout
    2  capability/environment error, message on stderr
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import authoring_scope as _scope  # noqa: E402
from crux.flow import technology  # noqa: E402
from crux.flow.common import FlowError  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report drift; write nothing")
    ap.add_argument("--repo-root", default=None, help="repo root to inspect (default: the working directory)")
    args = ap.parse_args(argv)
    root = _scope.resolve_repo_root(args.repo_root)
    try:
        code, payload = technology.check(PLUGIN, root) if args.dry_run else technology.sync(PLUGIN, root)
    except FlowError as exc:
        print(json.dumps({"validation_errors": [{"path": ".crux-flow.yml", "field": "technology", "error": str(exc)}]}, indent=2))
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"generate-technology-references.py: {exc}\n")
        return 2
    print(json.dumps(payload, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
