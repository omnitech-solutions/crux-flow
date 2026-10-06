#!/usr/bin/env python3
"""base_commit_pin.py — one implementation of the pin on a run snapshot's `base_commit`.

`base_commit` is the single value the `patch` tier's containment check reads out of a
run snapshot (docs/AGENTS.md §11.C). Every other input to that check comes from the
repository's own change record, so this one value is the only place a run can speak
about itself — and moving it forward shrinks the diff the check proves.

TWO CONSUMERS, ONE IMPLEMENTATION. The pin has to hold at both ends of the run:

  * `advance-run.py` refuses to WRITE a snapshot whose `base_commit` diverges from its
    committed record, on both the advance and the abandon path.
  * `check-blast-radius.py` refuses to PASS a snapshot whose `base_commit` diverges,
    because a run can commit a hand-edited value without going through `advance-run.py`
    at all. A writer-side guard alone left the reader trusting the field.

Both call `committed_base_commit` here. A second copy would let the two ends disagree
about what the committed record is, and the gate is only worth what both ends enforce.

THE NO-CLAIM LANE IS DELIBERATE. `NO_RECORD` means "this snapshot has no committed
version to compare against", and it is never a verdict. It is returned when git is
absent, when the snapshot lies outside a work tree, when the snapshot is untracked or
has no committed version yet, and when the committed blob does not parse. Absence of
evidence is not evidence: a run whose snapshot has not been committed yet has nothing
holding its `base_commit`, and reporting that as tampering would refuse every run
during its first prompt.

`NO_RECORD` is also held apart from a committed `base_commit: null`. Conflating them
would let a run that started outside a git work tree acquire a commit boundary after
the fact, which is the same forward move by another route.

HONEST LIMIT. The pin binds from the snapshot's first commit onward. In the window
between run start and that commit there is no committed record, so nothing holds the
value at either end.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


class NoRecord:
    """Sentinel type: this snapshot has no committed version to compare against."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "<NO_RECORD>"


NO_RECORD = NoRecord()


def committed_base_commit(run_path: Path) -> Any:
    """The ``base_commit`` recorded in the last committed version of this snapshot,
    or ``NO_RECORD`` when there is none to read.

    Reads `HEAD:<path>` through `git -C <the snapshot's own directory>`, so the answer
    comes from the repository the snapshot lives in rather than from a caller-supplied
    root. Returns ``NO_RECORD`` — never a verdict — for every case listed in the module
    docstring's no-claim lane."""
    if shutil.which("git") is None:
        return NO_RECORD
    parent = run_path.resolve().parent

    def _git(*args: str) -> str | None:
        try:
            proc = subprocess.run(["git", "-C", str(parent), *args],
                                  capture_output=True, text=True, check=False)
        except OSError:
            return None
        return proc.stdout if proc.returncode == 0 else None

    top = _git("rev-parse", "--show-toplevel")
    if not top or not top.strip():
        return NO_RECORD
    try:
        rel = run_path.resolve().relative_to(Path(top.strip()).resolve())
    except ValueError:
        return NO_RECORD
    blob = _git("show", f"HEAD:{rel.as_posix()}")
    if blob is None:
        return NO_RECORD
    try:
        doc = yaml.safe_load(blob)
    except yaml.YAMLError:
        return NO_RECORD
    if not isinstance(doc, dict) or "base_commit" not in doc:
        return NO_RECORD
    return doc["base_commit"]


def divergence(run: dict, run_path: Path) -> tuple[Any, Any] | None:
    """``(committed, live)`` when the snapshot's ``base_commit`` diverges from its
    committed record, else ``None``.

    ``None`` covers both clean cases and reports them the same way, because a caller
    must act on neither: the values agree, or there is no committed record to compare
    against and this function makes no claim."""
    pinned = committed_base_commit(run_path)
    if isinstance(pinned, NoRecord):
        return None
    live = run.get("base_commit")
    if live == pinned:
        return None
    return pinned, live
