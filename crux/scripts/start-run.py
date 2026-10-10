#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["PyYAML>=6.0"]
# ///
"""Create one validated run snapshot; the owning skill updates book/index pointers."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
import council_records as cr


def refuse(code):
    raise ValueError(code)


def create_run(book_path: Path, run_id: str, output: Path) -> dict:
    if cr.reached_through_symlink(book_path) or cr.reached_through_symlink(output):
        refuse("symlink-refused")
    repo = cr.repo_root(book_path)
    if repo is None:
        refuse("repository-required")
    book_path, book_rel = cr.resolve_in_repo(repo, book_path)
    output, _ = cr.resolve_in_repo(repo, output)
    if book_path.suffix != ".yaml" or book_path.parent.name != "active":
        refuse("active-yaml-book-required")
    if not re.fullmatch(r"RUN-[0-9]{3}", run_id):
        refuse("run-identity-refused")
    if output.name != "run-" + run_id + ".yaml" or output.parent.name != book_path.stem or \
            output.parent.parent.name != "runs" or output.parent.parent.parent != book_path.parent.parent:
        refuse("run-layout-refused")
    if output.exists():
        refuse("immutable-run-exists")
    if not cr.is_clean(repo, book_rel):
        refuse("book-not-committed")
    shallow = cr.git(repo, "rev-parse", "--is-shallow-repository")
    base = cr.git(repo, "rev-parse", "HEAD^{commit}")
    if shallow.returncode or shallow.stdout.strip() != b"false" or base.returncode:
        refuse("complete-start-history-required")
    vp = cr._validator()
    book = vp.load_yaml(book_path.read_text())
    errors = []
    vp.validate(book, vp.load_schema(vp.PROMPTBOOK_SCHEMA), "#", "#", errors, "book")
    if isinstance(book, dict):
        vp.post_schema_pass(book, errors, "book")
        vp.cycle_coverage_pass(book, errors, "book")
    if errors or not isinstance(book, dict):
        refuse("book-validation-refused")
    previous = book.get("current_run")
    if previous is not None:
        if not isinstance(previous, str) or not re.fullmatch(r"RUN-[0-9]{3}", previous):
            refuse("previous-run-pointer-refused")
        prior = output.parent / ("run-" + previous + ".yaml")
        if cr.reached_through_symlink(prior) or not prior.is_file():
            refuse("previous-run-unavailable")
        prior_doc = yaml.safe_load(prior.read_bytes())
        if not isinstance(prior_doc, dict) or prior_doc.get("status") not in ("completed", "abandoned"):
            refuse("previous-run-in-progress")
    occupied = [int(path.stem.split("-")[-1]) for path in output.parent.glob("run-RUN-*.*")
                if path.suffix in (".md", ".yaml") and re.fullmatch(r"run-RUN-[0-9]{3}", path.stem)]
    if int(run_id.split("-")[-1]) != max(occupied, default=0) + 1:
        refuse("run-allocation-refused")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    run = {"format_version": book["format_version"], "book_id": book["id"], "run_id": run_id,
           "book_content_hash": vp.compute_book_hash(book), "base_commit": base.stdout.decode().strip(),
           "started_at": now, "completed_at": None, "status": "in_progress", "current_prompt": 1,
           "prompts": [{"n": p["n"], "title": p["title"], "state": "running" if p["n"] == 1 else "pending",
                        "started": now if p["n"] == 1 else None, "completed": None, "result": "", "artifacts": []}
                       for p in book["prompts"]], "notes": "", "pr_draft": "", "summary": ""}
    if book["format_version"] == "2":
        run["implementation_bindings"] = []
        if any("migration_batch" in slot for slot in book["implementation_slots"]):
            run["migration_bindings"] = []
    errors = []
    vp.validate(run, vp.load_schema(vp.RUN_SCHEMA), "#", "#", errors, "run")
    if errors:
        refuse("run-validation-refused")
    if book["format_version"] == "2":
        vp.validate_format_two_run(run, book)
    output.parent.mkdir(parents=True, exist_ok=True)
    owned = False
    try:
        with output.open("xb") as stream:
            owned = True
            stream.write(yaml.safe_dump(run, sort_keys=False).encode())
    except OSError:
        if owned:
            output.unlink(missing_ok=True)
        refuse("run-write-refused")
    return {"run": str(output), "book_content_hash": run["book_content_hash"],
            "format_version": run["format_version"], "book_pointer_updated": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = create_run(Path(args.book), args.run_id, Path(args.output))
    except (ValueError, OSError, TypeError, KeyError, cr.RecordError, yaml.YAMLError) as exc:
        code = str(exc) if type(exc) is ValueError else "start-evidence-invalid"
        print(json.dumps({"error": code, "written": False}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
