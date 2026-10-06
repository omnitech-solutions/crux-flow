#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Generate Codex agents into an explicitly selected output directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codex_agents import SpecViolation, diff, generate, write


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="report drift without writing")
    args = parser.parse_args(argv)

    # One try/except covers generate() AND diff()/write(). It used to wrap
    # generate() alone, so a SpecViolation raised by the other two — a managed
    # leaf that is a symlink, an output path that is not a directory — escaped as
    # a traceback and exit 1, which this lane reserves for --dry-run drift. Every
    # SpecViolation is the same class of failure and owes the caller exit 2 with a
    # message on stderr and nothing on stdout.
    try:
        generated = generate()
        added, changed, removed = diff(args.output_dir, generated)
        if args.dry_run:
            report = {"added": added, "changed": changed, "removed": removed}
            print(json.dumps(report, indent=2))
            return 1 if any(report.values()) else 0
        written, removed = write(args.output_dir, generated)
    except SpecViolation as exc:
        print(f"generate-codex-agents: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"written": written, "removed": removed}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
