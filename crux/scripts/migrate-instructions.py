#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""migrate-instructions.py — report or migrate repository instruction files.

The entry point for the canonical-AGENTS.md discovery and migration contract.
`audit-docs` reports through `--dry-run` in plain mode and applies through
`--migrate`; the logic lives in `instruction_migration.py`, shared by both.

The repo root comes from `--repo-root`, defaulting to the working directory —
never from this file's own path, which names the plugin and never the project.

Exit codes: 0 clean, 1 findings or refusal with JSON on stdout, 2 capability
error with a message on stderr.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "instruction_migration", _HERE / "instruction_migration.py")
im = importlib.util.module_from_spec(_spec)
sys.modules["instruction_migration"] = im
_spec.loader.exec_module(im)


def _payload(root: Path, d, plan) -> dict:
    """The report every run prints, dry runs included."""
    left = {im._rel(sp.scope, sp.winner.name) for sp in plan.scopes
            if sp.winner is not None and sp.winner_left}
    excluded = {k: v for k, v in d.excluded.items() if k not in left}
    for sp in plan.scopes:
        excluded.update(sp.excluded)
    suppressors = []
    for s in d.suppressors:
        rel = s.path.as_posix()
        row = {"path": im.show(rel), "reason": s.reason, "on_chain": s.on_chain,
               "remedy": s.remedy}
        if rel in left:
            # A winner left in place is reported once, as a suppressor whose
            # content AGENTS.md carries, with no next step.
            row["reason"] = "winner left in place; AGENTS.md carries its content"
            row["remedy"] = ""
        suppressors.append(row)
    return {
        "repo_root": str(root),
        "managed": [im.show(p.as_posix()) for p in d.managed_paths()],
        "excluded": {im.show(k): v for k, v in sorted(excluded.items())},
        "suppressors": suppressors,
        "actions": [s.to_json() for s in plan.actions],
        "scopes": [sp.to_json() for sp in plan.scopes],
        "set_asides": [sa for sp in plan.scopes for sa in sp.set_asides],
        "refusals": plan.refusals,
        "temporaries": [t for sp in plan.scopes for t in sp.temporaries],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--working-dir", default=None,
                    help="the directory a host would load instructions from; "
                         "decides which suppressors bear on a verdict")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only")
    mode.add_argument("--migrate", action="store_true", help="apply the plan")
    args = ap.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"repo root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        d = im.discover(root, denylist=im.load_denylist(root),
                        working_dir=Path(args.working_dir) if args.working_dir else root)
        plan = im.build_plan(d)
    except im.CapabilityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"cannot read the repository: {exc}", file=sys.stderr)
        return 2

    if args.migrate:
        try:
            receipt = im.apply_plan(plan, root)
        except im.RunStopped as exc:
            print(str(exc), file=sys.stderr)
            return 2
        payload = _payload(root, d, plan)
        payload["applied"] = True
        payload["receipt"] = receipt.to_json()
        payload["dispositions"] = receipt.dispositions
        payload["staging_note"] = receipt.staging_note
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if plan.findings() else 0

    payload = _payload(root, d, plan)
    clean = not plan.actions and not plan.findings()
    payload["clean"] = clean
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
