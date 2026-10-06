# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""check-blast-radius.py — the archive-time containment check for a `patch` book.

ADR-0077 clause 2 admits the `patch` cycle tier only if a run's changed paths can be
proved against the blast radius it declared before the run started. The ADR left the
evidence source to the dev module and made the tier conditional on choosing one: "If
no source proves which paths a run changed, clause 2 is unmet and the tier does not
ship." This script is that source.

THE EVIDENCE IS GIT, and only git. Clause 2 requires the comparison be drawn from the
repository's own change record "and never from anything the run authors" — so this
reads neither the run snapshot's `artifacts` fields nor its `notes`, both of which the
run writes about itself. It reads exactly one value from the run, `base_commit`, which
`run-promptbook` stamps from `git rev-parse HEAD` at run start, and that value is
PINNED RATHER THAN TRUSTED: this refuses when it diverges from the value in the
snapshot's own committed version (see `base_commit_pin.py`, whose `divergence` the
writer `advance-run.py` calls too). Both ends must enforce it. A run that overshoots
can commit its work, hand-edit `base_commit` forward, and archive without ever calling
the writer — and this gate then drew its diff from the new base and passed with an
empty changed set. The pin binds from the snapshot's first commit onward; before that
there is no committed record and the guard makes no claim, so a run in its first
prompt is not refused for lack of a record. Given the value, this derives the changed
set from the repository:

    git diff --name-only --no-renames <base_commit> --   (base -> working tree)
    git ls-files --others --exclude-standard             (untracked)

`--no-renames` is required for correctness, not tidiness: with rename detection on, git
reports only a rename's DESTINATION, so moving a file out of an undeclared path into a
declared one presents a single declared path while the undeclared source disappears.

`--exclude-standard` names this check's other blind spot: a .gitignored path is invisible
to both commands, so a patch that writes one passes containment without the path ever
being considered.

BOTH FRAMES MUST BE THE SAME REPOSITORY. The diff is drawn through `--repo-root`, while
`base_commit_pin` reads the committed record through the snapshot's own directory. When
those are two different repositories the pin compares a decoy repo's record against
itself and holds nothing. `require_same_frame` refuses that, in the exit-2 lane.

THE DECLARATION IS FROZEN, AND THIS CHECK VERIFIES THAT. `blast_radius` sits inside the
frozen-plan subset that `book_content_hash` covers (see `validate-promptbook.py`), so
widening it mid-run moves the hash. Audit rule CHK-PB-BIND reports such a mismatch — but
audit does not run on the archive path, and this check reads the LIVE declaration, so
the freeze bought nothing here until this script recomputed the hash itself. It now
refuses when the recomputed hash differs from the one the run stored.

THE BOOKKEEPING EXCLUSION SET. Running any book rewrites the tree's own bookkeeping —
the run snapshot, the promptbook index, the log, the journal, the regenerated arch
spine. Those are the machinery's writes, not "the paths the run changed", so they are
excluded. Two deliberate boundaries on that set:

  * Under `<docs_dir>/promptbooks/` the exclusion is BOOK-SCOPED: this book's own run
    directory and plan file, plus the shared index. Another book's plan, and any
    archived book, must reach containment — those are immutable or belong to other
    work, and a blanket exclusion hid edits to both.
  * An ADR path is NOT excluded: a patch that writes an ADR fails this check, which is
    the intent ("work that needs an ADR is not a patch"). Note the residual blind spot
    that stays excluded on purpose — `<docs_dir>/manifest.yml` is bookkeeping (counters
    move on every allocation) and also carries per-tree governance config, so a change
    to it inside a patch run is not surfaced here.

Exit codes (crux convention — three lanes, never conflated):
  0 — clean. JSON on stdout: {"clean": true, "declared": [...], "changed": [...],
      "undeclared": []}.
  1 — FINDINGS or refusal; a DOCUMENT verdict. JSON on stdout:
      {"clean": false, "undeclared": [...], "error": <null or a one-line reason>}.
      Covers: an undeclared changed path; the book is not `cycle_kind: patch`; a
      missing or empty `blast_radius`; a null/missing/malformed `base_commit`; a
      `base_commit` that diverges from the snapshot's committed record; a declared
      entry that fails the repo-relative grammar; an unreadable document.
  2 — capability error; an ENVIRONMENT problem, a distinct lane. Message on stderr,
      NOTHING on stdout. Covers: git absent from PATH, `--repo-root` is not a git work
      tree, the book or run document lies outside `--repo-root`, the base commit is
      unknown to the repository, PyYAML unavailable.

The caller must read these as three lanes. An exit 1 is a real gate failure to act on,
not an environment hiccup to wave through; an exit 2 is fail-closed, because an
unproven blast radius is not a passed one.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - the uv/PEP 723 lane supplies PyYAML
    sys.stderr.write(
        "check-blast-radius.py requires PyYAML — run via `uv run` (PEP 723 supplies it).\n"
    )
    raise SystemExit(2)

# One grammar for a declared path, shared with the authoring-time validator so the
# rule enforced when a patch book is written is the rule enforced when it archives.
_VALIDATOR = Path(__file__).resolve().parent / "validate-promptbook.py"
_spec = importlib.util.spec_from_file_location("_validate_promptbook", _VALIDATOR)
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    sys.stderr.write(f"check-blast-radius.py: cannot load {_VALIDATOR}\n")
    raise SystemExit(2)
_vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vp)
invalid_blast_radius_entry = _vp.invalid_blast_radius_entry
# Same module, same reason: the binding hash this check verifies must be computed by
# the function that WROTE it, or a byte-level difference would read as tampering.
compute_book_hash = _vp.compute_book_hash

# The pin on `base_commit` has two enforcers — the writer (`advance-run.py`) and this
# gate — so it has one implementation, imported by both. A second copy would let the
# two ends disagree about what the snapshot's committed record is.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base_commit_pin as _pin  # noqa: E402  (sys.path insert before import)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Directory prefixes and exact files the run machinery writes for ANY book.
_EXCLUDED_DIRS = ("journal", "arch")
_EXCLUDED_FILES = ("log.md", "index.md", "manifest.yml", "whats_next.md")
# Under `<docs_dir>/promptbooks/` the exclusion is BOOK-SCOPED, not blanket. The
# machinery writes this book's own run snapshot, this book's own plan pointer, and the
# shared index; it does not write any other book. A blanket exclusion also covered
# every other book — including archived ones, which are immutable — so a patch could
# rewrite another book's plan and containment would never see it.
_PROMPTBOOKS_DIR = "promptbooks"
_PROMPTBOOKS_SHARED_FILES = ("index.md",)
_BOOK_LIFECYCLE_DIRS = ("active", "archive")


class CapabilityError(Exception):
    """An environment problem (exit 2), never a document verdict."""


def _segments(path: str) -> list[str]:
    """Normalize a repo-relative path to POSIX segments, dropping '.' and any empty
    segment produced by a trailing or doubled slash. Segment lists are what make the
    containment test exact — a raw `startswith` would let `crux/scriptsX/y` look
    covered by a declaration of `crux/scripts`."""
    return [seg for seg in path.replace("\\", "/").split("/") if seg not in ("", ".")]


def covered_by(changed: str, declared: str) -> bool:
    """True iff `changed` equals `declared` or lies under it as a directory prefix."""
    c, d = _segments(changed), _segments(declared)
    return len(c) >= len(d) and c[: len(d)] == d


def is_bookkeeping(changed: str, docs_dir: str, book_stem: str) -> bool:
    """True iff `changed` is a path the run machinery writes while running THIS book,
    and so is not one of "the paths the run changed".

    `book_stem` is the running book's filename stem (e.g. ``PB-0074-some-slug``). It is
    required, not defaulted: the promptbooks exclusion is book-scoped, and a default
    would silently restore the blanket exclusion this parameter exists to remove."""
    segs = _segments(changed)
    root = _segments(docs_dir)
    if len(segs) <= len(root) or segs[: len(root)] != root:
        return False
    rest = segs[len(root):]
    if rest[0] in _EXCLUDED_DIRS:
        return True
    if rest[0] == _PROMPTBOOKS_DIR:
        inner = rest[1:]
        if len(inner) == 1 and inner[0] in _PROMPTBOOKS_SHARED_FILES:
            return True
        # This book's own run snapshots: promptbooks/runs/<stem>/**
        if len(inner) >= 2 and inner[0] == "runs" and inner[1] == book_stem:
            return True
        # This book's own plan, at either lifecycle location — archival moves it from
        # active/ to archive/, and both writes belong to the machinery.
        if (len(inner) == 2 and inner[0] in _BOOK_LIFECYCLE_DIRS
                and Path(inner[1]).stem == book_stem):
            return True
        return False
    return len(rest) == 1 and rest[0] in _EXCLUDED_FILES


def _git(repo_root: Path, *args: str, nul: bool = False) -> list[str]:
    """Run git with an argument LIST (never a shell string) and return its output.

    ``nul=True`` splits on NUL rather than newline. Path-listing commands MUST use
    it: git's default output quotes any path with a non-ASCII or unusual byte
    (`"cr\\303\\274x/a.py"`), and a quoted path would not match its own declaration
    — producing a phantom undeclared path on a tree that never overstepped."""
    if shutil.which("git") is None:
        raise CapabilityError("git is not on PATH")
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:  # pragma: no cover - defensive
        raise CapabilityError(f"could not run git: {exc}") from exc
    if proc.returncode != 0:
        raise CapabilityError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    parts = proc.stdout.split("\0") if nul else proc.stdout.splitlines()
    # Drop only the EMPTY trailing element `-z` output ends with. A `.strip()` test
    # would also drop a path whose whole name is whitespace, which git reports and the
    # filesystem allows — and a dropped path is a path that never reaches containment.
    return [s for s in parts if s]


def _work_tree_top(repo_root: Path) -> Path:
    """Verify `repo_root` IS the git work-tree top and return it resolved.

    git reports paths relative to the WORK-TREE TOP, while a declaration is relative to
    the repo root the book was written against. If the caller points at a subdirectory
    those two are silently different, and every comparison after it is meaningless.
    Refuse rather than compare across frames.

    One implementation, two callers: `changed_paths` (which draws the diff) and `check`
    (which must establish the frame before the pin reads a committed record out of it)."""
    _git(repo_root, "rev-parse", "--is-inside-work-tree")
    toplevel = _git(repo_root, "rev-parse", "--show-toplevel")
    if not toplevel or Path(toplevel[0]).resolve() != repo_root.resolve():
        raise CapabilityError(
            f"--repo-root {repo_root} is not the git work-tree top "
            f"({toplevel[0] if toplevel else '<unknown>'}); declared paths are "
            f"repo-root-relative, so comparing from a subdirectory would be meaningless"
        )
    return repo_root.resolve()


def require_same_frame(repo_root: Path, run_path: Path, book_path: Path) -> None:
    """Refuse unless the documents live in the repository whose diff is being proved.

    `changed_paths` already refuses a `--repo-root` that is not the work-tree top,
    because a diff and a declaration drawn from two frames cannot be compared. The pin
    on `base_commit` needs the same care and did not have it: `base_commit_pin` reads
    the committed record through `git -C <the snapshot's own directory>`, deliberately,
    so the answer comes from the repository the snapshot lives in. When that is a
    DIFFERENT repository from `--repo-root`, the pin compares the snapshot against a
    record the decoy repo controls — it agrees with itself — while the diff is still
    drawn from the real one. A snapshot hosted in a decoy repo therefore neutralized
    the pin entirely: commit the overshoot in the real tree, host the snapshot
    elsewhere with `base_commit` already advanced, and the gate drew an empty diff from
    the advanced base and passed.

    The run is checked first because it is the document that carries the pinned value."""
    root = repo_root.resolve()
    for label, path in (("run", run_path), ("book", book_path)):
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise CapabilityError(
                f"the {label} document {resolved} lies outside --repo-root {root}, so it "
                f"belongs to a different git frame than the diff this check draws. The "
                f"pin on base_commit reads the committed record from the repository the "
                f"snapshot lives in, so a snapshot hosted outside this one is pinned "
                f"against a record this repository does not hold"
            )


def changed_paths(repo_root: Path, base_commit: str) -> list[str]:
    """The repository's own record of what moved since `base_commit`.

    `base_commit` is regex-validated as 40 lowercase hex before it reaches an argv
    position, and the diff is terminated with `--`, so a crafted value can be read
    neither as a git option nor as a pathspec."""
    if not _SHA_RE.match(base_commit):
        raise ValueError("base_commit is not a 40-character lowercase hex commit id")
    # Kept here as well as at `check`'s frame gate: this function is public and drawing
    # a diff across two frames is meaningless however it was reached.
    _work_tree_top(repo_root)

    try:
        _git(repo_root, "cat-file", "-e", f"{base_commit}^{{commit}}")
    except CapabilityError as exc:
        raise CapabilityError(
            f"base commit {base_commit} is unknown to this repository ({exc})"
        ) from exc
    # `--no-renames` is load-bearing, not a style choice. With rename detection on,
    # git collapses a rename to its DESTINATION only: `git mv secret/x.py
    # crux/scripts/x.py` reports one path — the declared one — while the undeclared
    # source is deleted and never appears. Disabling detection reports the rename as
    # what it is on disk, a delete plus an add, so both paths reach containment.
    tracked = _git(repo_root, "diff", "--name-only", "--no-renames", "-z",
                   base_commit, "--", nul=True)
    untracked = _git(repo_root, "ls-files", "--others", "--exclude-standard", "-z", nul=True)
    return sorted(set(tracked) | set(untracked))


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def check(book_path: Path, run_path: Path, repo_root: Path, docs_dir: str) -> tuple[int, dict]:
    """Return (exit_code, stdout_payload). Raises CapabilityError for the exit-2 lane."""

    def refuse(reason: str, undeclared: list[str] | None = None) -> tuple[int, dict]:
        return 1, {"clean": False, "undeclared": undeclared or [], "error": reason}

    for label, path in (("book", book_path), ("run", run_path)):
        if not path.is_file():
            return refuse(f"{label} not found: {path}")
    try:
        book = _load(book_path)
        run = _load(run_path)
    except Exception as exc:  # noqa: BLE001 - a parse failure is a document verdict
        return refuse(f"could not parse the book or run document: {exc}")
    if not isinstance(book, dict) or not isinstance(run, dict):
        return refuse("the book or run document did not parse to a mapping")

    if book.get("cycle_kind") != "patch":
        return refuse(
            f"blast-radius containment applies only to a cycle_kind: 'patch' book "
            f"(this book is {book.get('cycle_kind')!r})"
        )

    # The declaration this check reads is the LIVE one, so the check must itself
    # verify that the live plan is still the plan the run was bound to. `blast_radius`
    # is inside the frozen-plan subset `book_content_hash` covers, and audit rule
    # CHK-PB-BIND reports a mismatch — but audit is not on the archive path, so
    # nothing here evaluated the binding and widening the declaration mid-run turned a
    # refusal into a pass. The freeze is only worth what the consuming gate checks.
    stored = run.get("book_content_hash")
    if not isinstance(stored, str) or not stored:
        return refuse(
            "the run records no book_content_hash, so the declaration it was bound to "
            "cannot be proved; an unbound declaration is not a frozen one"
        )
    recomputed = compute_book_hash(book)
    if recomputed != stored:
        return refuse(
            f"the book's frozen plan no longer hashes to the run's book_content_hash "
            f"(stored {stored}, recomputed {recomputed}). blast_radius is inside the "
            f"frozen-plan subset, so the declaration has moved since the run started. "
            f"Never widen a declaration to clear this check: abandon the run and "
            f"re-author the work in the tier that fits it"
        )

    declared = book.get("blast_radius")
    if not isinstance(declared, list) or not declared:
        return refuse("the patch book declares no blast_radius")
    for entry in declared:
        reason = invalid_blast_radius_entry(entry)
        if reason is not None:
            return refuse(f"declared blast_radius entry {entry!r} is not a repo-relative path: {reason}")

    base = run.get("base_commit")
    if not isinstance(base, str) or not _SHA_RE.match(base):
        return refuse(
            "the run records no usable base_commit, so no evidence source proves which "
            "paths it changed; a patch run without a commit boundary cannot archive as completed"
        )

    # `base_commit` is the one value this check reads out of the run, and it was PINNED
    # BUT NOT VERIFIED HERE. `advance-run.py` refuses to write a divergent value, but a
    # run that overshoots can commit its work, hand-edit `base_commit` forward, and
    # archive without ever calling that writer — and this gate then drew its diff from
    # the new base, found nothing, and passed with `changed: []`. A pin only one end
    # enforces is a pin the consuming end still trusts.
    #
    # The check runs BEFORE `changed_paths`, so a value is verified before it is used.
    # That ordering also keeps the lanes honest: a tampered value unknown to the
    # repository is a document verdict about the snapshot, not an environment error.
    #
    # The FRAME is established first, because the pin's claim is only about the
    # repository the snapshot lives in. `_work_tree_top` runs before the containment
    # assertion so a `--repo-root` below the top still reports that, its own diagnosis,
    # rather than the frame message.
    _work_tree_top(repo_root)
    require_same_frame(repo_root, run_path, book_path)
    diverged = _pin.divergence(run, run_path)
    if diverged is not None:
        pinned, live = diverged
        return refuse(
            f"the run's base_commit diverges from the value in its own committed version "
            f"(committed {pinned!r}, on disk {live!r}). It is written once at run start and "
            f"never rewritten, and it is the one value this check reads out of the run, so "
            f"moving it forward shrinks the diff proved here. Restore the committed value, "
            f"or abandon the run and re-author the work in the tier that fits it"
        )

    changed = changed_paths(repo_root, base)
    book_stem = book_path.stem
    considered = [p for p in changed if not is_bookkeeping(p, docs_dir, book_stem)]
    undeclared = sorted(p for p in considered if not any(covered_by(p, d) for d in declared))

    if undeclared:
        return 1, {"clean": False, "undeclared": undeclared, "error": None}
    return 0, {
        "clean": True,
        "declared": list(declared),
        "changed": considered,
        "undeclared": [],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="check-blast-radius",
        description="Compare a patch run's git-recorded changed paths against the "
                    "blast radius its book declared before the run started.",
    )
    ap.add_argument("--book", required=True, type=Path, help="path to the patch book .yaml")
    ap.add_argument("--run", required=True, type=Path, help="path to its run-RUN-NNN.yaml")
    ap.add_argument("--repo-root", type=Path, default=Path.cwd(),
                    help="repository root (default: the process working directory)")
    ap.add_argument("--docs-dir", default="bionic",
                    help="the documentation tree's directory name (default: bionic)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        code, payload = check(
            args.book.resolve(), args.run.resolve(), args.repo_root.resolve(), args.docs_dir
        )
    except CapabilityError as exc:
        print(f"check-blast-radius: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(json.dumps({"clean": False, "undeclared": [], "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(payload, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
