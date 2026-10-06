#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""check-record-numbers.py -- does any promptbook or ADR number appear twice?

Reads filenames only. The namespaces and the `legacy/` exclusion are defined in
`record_numbers.py`, which this script wraps. The output carries `checked`, the count
of records scanned per namespace, so a clean verdict shows what it covered.

A tree with neither a promptbooks concern nor an ADR directory owes nothing and
reports `surface_absent`.

Exit codes: 0 clean, 1 findings with `{"validation_errors": [...]}` on stdout,
2 environment or input error (stderr, no stdout payload).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bionic_config as _cfg  # noqa: E402
import record_numbers as _rn  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--docs-dir", default=None)
    args = ap.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"check-record-numbers: repo root is not a directory: {root}",
              file=sys.stderr)
        return 2
    try:
        docs = root / (args.docs_dir or _cfg.resolve_tree_name(root))
    except Exception as exc:  # noqa: BLE001 - an unresolvable tree is the environment lane
        print(f"check-record-numbers: cannot resolve the documentation tree: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if not (docs / "promptbooks").is_dir() and not (docs / "adrs").is_dir():
        print(json.dumps({"surface_absent": True, "drift": False,
                          "reason": "no promptbooks or adrs directory in this tree"},
                         sort_keys=True))
        return 0

    try:
        problems = _rn.find_duplicate_numbers(docs)
        checked = _rn.count_records(docs)
    except OSError as exc:  # an unreadable directory is the environment lane, not a finding
        print(f"check-record-numbers: cannot read the tree: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"validation_errors": problems,
                      "checked": checked},
                     indent=2, sort_keys=True))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
