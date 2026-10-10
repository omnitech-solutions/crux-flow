#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0,<7", "httpx>=0.27,<1"]
# ///
"""Validate implementation reasoning, append delivery evidence or query source state."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

import bionic_config
import council_records as cr
import implementation_decisions as ids


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("record", type=Path)
    validate.add_argument("--repo-root", type=Path, default=Path.cwd())
    unchecked = commands.add_parser("unchecked-rules")
    unchecked.add_argument("--decision", type=Path, required=True)
    unchecked.add_argument("--output", type=Path, required=True)
    unchecked.add_argument("--repo-root", type=Path, default=Path.cwd())
    audit = commands.add_parser("audit-order", help="report where profile-3 stamp order and committed order differ")
    audit.add_argument("--repo-root", type=Path, default=Path.cwd())
    audit.add_argument("--run", type=Path, default=None)
    for name in ("result", "query", "annotate"):
        command = commands.add_parser(name)
        command.add_argument("--decision", type=Path, required=True)
        command.add_argument("--repo-root", type=Path, default=Path.cwd())
        if name == "query":
            command.add_argument("--revision", default="HEAD")
        else:
            command.add_argument("--evidence", type=Path, required=True)
            command.add_argument("--output", type=Path, required=name == "annotate")
    args = parser.parse_args(argv)
    if args.command == "audit-order":
        # Read-only. Exit 2 only when the audit cannot start; every finding exits 0.
        try:
            print(json.dumps(ids.audit_order(args.repo_root, args.run), sort_keys=True))
            return 0
        except ids.AuditUnusable as exc:
            print("audit-order: " + str(exc), file=sys.stderr)
            return 2
    try:
        if args.command == "validate":
            result = ids.validate_decision(args.repo_root, args.record)
        elif args.command == "unchecked-rules":
            written = ids.write_unchecked_rules(args.repo_root, args.decision, args.output)
            result = {"written": written.relative_to(args.repo_root.resolve()).as_posix(), "authority": "none"}
        elif args.command == "query":
            result = ids.query(args.repo_root, args.decision, args.revision)
        else:
            path, _ = ids.ap.checked_path(args.repo_root.resolve(), str(args.evidence))
            evidence = ids.load(path)
            if args.command == "result":
                written = ids.write_result(args.repo_root, args.decision, evidence, args.output)
            else:
                written = ids.write_annotation(args.repo_root, args.decision, evidence, args.output)
            result = {"written": written.relative_to(args.repo_root.resolve()).as_posix(), "authority": "none"}
        print(json.dumps(result, sort_keys=True))
        return 0
    except ids.Refused as exc:
        print(json.dumps({"refused": exc.code}))
        return 1
    except (cr.RecordError, bionic_config.BionicConfigError, OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        # Invalid private evidence never echoes attacker-controlled prose or credentials.
        print(json.dumps({"refused": "evidence-invalid"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
