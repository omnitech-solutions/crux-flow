#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0,<7"]
# ///
"""check-refutation-record.py - the read-only pre-commit check of a refutation record.

    uv run check-refutation-record.py <run-RUN-NNN.yaml> --record <refutation-record.json>

A committed refutation record is final: editing or deleting it stops the module for good, and a
refutation stamp that does not postdate its council record is a permanent contradiction. Run this
check before committing the record. It writes nothing, stages nothing and commits nothing.

Checks. Each failure is a finding with a stable code.

* ``record-path-refused``: the record path escapes the repository, traverses a symlinked
  directory, or is itself a symlink.
* ``unparseable``: the record is not a JSON object.
* ``schema``: one finding per error against the refutation-record schema.
* ``run-binding``: the record's book id, book content hash or run id differs from the run snapshot.
* ``council-record-path-refused``: the named council record path escapes the repository or
  traverses a symlinked directory.
* ``council-record-not-committed``: HEAD, the index and the working tree do not hold the same
  bytes for the council record, or it is a symlink or missing.
* ``council-record-hash-mismatch``: the sha256 the refutation names is not the council record's.
* ``council-record-unparseable``: the council record is not a JSON object.
* ``council-run-binding``: the council record is bound to another book or run.
* ``module-mismatch``: the council record's module differs from the refutation's ``module_tag``.
* ``stamp-not-after-council-record``: the refutation's ``written_at`` is not strictly later than
  the council record's. Both are fixed-width UTC text, so the check compares them as text.

A refutation record that is already committed is reported in ``warnings``: it is final.

Exit codes. 0: no finding; one JSON line on stdout. 1: findings; one JSON line on stdout lists
them. 2: an environment fault (the run snapshot is missing or unparseable, or no repository holds
it); a message on stderr and nothing on stdout.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402  (sys.path insert before import)

import council_records as cr  # noqa: E402

RECORD_TYPE = "refutation-record"
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")


class Fault(Exception):
    """An environment fault: nothing can be judged."""


def _load_json_object(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _bound(doc: dict, run: dict) -> bool:
    book = doc.get("book")
    return (isinstance(book, dict) and book.get("id") == run.get("book_id")
            and book.get("content_hash") == run.get("book_content_hash")
            and doc.get("run_id") == run.get("run_id"))


def check(run_path: Path, record_arg: str) -> tuple[list[dict], list[str], dict]:
    """The findings, warnings and summary for one refutation record."""
    repo = cr.repo_root(run_path)
    if repo is None:
        raise Fault(f"the run snapshot is not inside a git repository: {run_path}")
    try:
        run = yaml.safe_load(run_path.read_bytes().decode("utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise Fault(f"the run snapshot cannot be read ({type(exc).__name__}): {run_path}") from exc
    if not isinstance(run, dict):
        raise Fault(f"the run snapshot is not a mapping: {run_path}")

    findings: list[dict] = []
    warnings: list[str] = []
    summary: dict = {}

    def add(code: str, message: str) -> None:
        findings.append({"code": code, "message": message})

    try:
        absolute, rel = cr.resolve_in_repo(repo, record_arg)
    except cr.PathRefused as exc:
        add("record-path-refused", str(exc))
        return findings, warnings, summary
    if absolute.is_symlink():
        add("record-path-refused", f"{rel} is a symlink")
        return findings, warnings, summary
    summary["record"] = rel
    doc = _load_json_object(absolute)
    if doc is None:
        add("unparseable", f"{rel} is not a readable JSON object")
        return findings, warnings, summary

    for error in cr.schema_errors(doc, RECORD_TYPE):
        add("schema", error)
    if not _bound(doc, run):
        add("run-binding", f"the record's book id, content hash or run id differs from {run_path.name}")

    named = doc.get("council_record")
    if not isinstance(named, dict) or not isinstance(named.get("path"), str):
        return findings, warnings, summary  # the schema finding already names it

    try:
        c_abs, c_rel = cr.resolve_in_repo(repo, named["path"])
    except cr.PathRefused as exc:
        add("council-record-path-refused", str(exc))
        return findings, warnings, summary
    summary["council_record"] = c_rel
    state = cr.path_state(repo, c_rel)
    if not state.clean:
        add("council-record-not-committed",
            f"{c_rel} is not committed with identical bytes in HEAD, the index and the working tree")
        return findings, warnings, summary
    if state.worktree != named.get("sha256"):
        add("council-record-hash-mismatch", f"the sha256 named for {c_rel} is not the committed file's")

    council = _load_json_object(c_abs)
    if council is None:
        add("council-record-unparseable", f"{c_rel} is not a readable JSON object")
        return findings, warnings, summary
    if not _bound(council, run):
        add("council-run-binding", f"{c_rel} is bound to another book or run")
    binding = council.get("binding")
    module = binding.get("module_tag") if isinstance(binding, dict) else None
    if module != doc.get("module_tag"):
        add("module-mismatch", f"{c_rel} is bound to module {module!r}, the record to {doc.get('module_tag')!r}")

    ours, theirs = doc.get("written_at"), council.get("written_at")
    if isinstance(ours, str) and _STAMP.match(ours):
        if not (isinstance(theirs, str) and _STAMP.match(theirs)):
            add("stamp-not-after-council-record", f"{c_rel} carries no fixed-width written_at to compare")
        elif not ours > theirs:
            add("stamp-not-after-council-record",
                f"written_at {ours} is not later than the council record's {theirs}")

    if cr.path_state(repo, rel).head is not None:
        warnings.append(f"{rel} is already committed: a committed refutation record is final")
    return findings, warnings, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-refutation-record", description=__doc__.split("\n")[0])
    parser.add_argument("run", help="the run snapshot, run-RUN-NNN.yaml")
    parser.add_argument("--record", required=True, help="the refutation record to check")
    args = parser.parse_args(argv)
    try:
        findings, warnings, summary = check(Path(args.run), args.record)
    except (Fault, cr.RecordError) as exc:
        # A git state the council records layer cannot read is an environment fault, not a finding.
        print(f"check-refutation-record: {exc}", file=sys.stderr)
        return 2
    out = {"status": "findings" if findings else "clean", **summary,
           "findings": findings, "warnings": warnings}
    print(json.dumps(out))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
