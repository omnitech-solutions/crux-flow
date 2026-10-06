#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""generate-writing-rules.py — the vendored regenerator for the writing-rules block.

The eighth regenerative output (per ADR-0056's enrollment discipline, established
by ADR-0058). One canonical text, two generated projections:

    canonical   AGENTS.md            between BEGIN/END CANONICAL: writing-rules
    projection  docs/AGENTS.md       between BEGIN/END GENERATED: writing-rules
    projection  crux/skills/prose-review/SKILL.md   (ships downstream)

The repo-root file was BOTH the projection target and, after the instruction-file
migration collapsed the two root instruction files into one, the canonical source.
It is the source and no longer a target: a generator whose source and target are
one path either overwrites its own input or drifts forever.

The projected region is BYTE-EQUIVALENT to the canonical region, and only the
bytes between the markers are touched.

Four properties the council review of this file insisted on, each with a test:

1. **No newline translation.** Files are read and written with `newline=""`, so
   a CRLF file stays CRLF. `Path.read_text()` would silently normalize, which
   both defeats byte-equivalence and rewrites bytes outside the region.
2. **Markers are matched as whole lines**, not as substrings anywhere in the
   file, so a marker mentioned mid-sentence is never a delimiter. A marker on
   its own line inside a fence is a separate case — see the residual risk below.
3. **Every target is validated before any target is written**, and each write is
   atomic (temp file + `os.replace`). A malformed third target can no longer
   leave the first two rewritten.
4. **The canonical body may not contain the projection markers.** Without this
   check, documenting the marker syntax inside the canonical block would inject
   delimiters into all three targets on the first run and wedge every run after.

Residual risk, stated rather than papered over: a marker on its own line inside a
fenced code block IS counted as a marker. Distinguishing it would need a Markdown
fence parser, which is more machinery than the risk earns. Two cases follow, and
only one is benign:

- A target with real markers PLUS a fenced example raises a loud duplicate-marker
  error. Nothing is written. This is the common case and it fails closed.
- A target whose ONLY marker pair lives inside a fence would be mis-delimited and
  its fenced content overwritten. Nothing in this script prevents that; what
  prevents it today is that no target is authored that way, which is a property
  of the current targets rather than a guarantee this code enforces.

Anyone adding a projection target must therefore give it real markers outside any
fence before adding it to PROJECTIONS.

Exit codes (the contract every sibling regenerator follows):
    0  clean — wrote (or, under --dry-run, found no drift)
    1  drift (--dry-run) or validation error, with JSON on stdout
    2  capability/environment error, with a message on stderr
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

CANONICAL_FILE = "AGENTS.md"
CANONICAL_BEGIN = "<!-- BEGIN CANONICAL: writing-rules -->"
CANONICAL_END = "<!-- END CANONICAL: writing-rules -->"

PROJECTION_BEGIN = "<!-- BEGIN GENERATED: writing-rules -->"
PROJECTION_END = "<!-- END GENERATED: writing-rules -->"

def _tree_name(root: Path) -> str:
    """Resolve the tree directory; the schema's AGENTS.md lives inside it."""
    _PATH = Path(__file__).resolve().parent / "bionic_config.py"
    import importlib.util as _ilu
    import sys as _sys
    _key = "_bionic_config"
    _m = _sys.modules.get(_key)
    if _m is None:
        _s = _ilu.spec_from_file_location(_key, _PATH)
        _m = _ilu.module_from_spec(_s)
        _s.loader.exec_module(_m)
        _sys.modules[_key] = _m   # cache so class identity holds across calls
    return _m.resolve_tree_name(root)


def projections(root: Path) -> tuple[str, ...]:
    """The two targets. The schema file's directory is resolved, not hardcoded.

    The repo-root AGENTS.md is deliberately ABSENT: it is CANONICAL_FILE. Adding it
    back makes this generator read and write one path.
    """
    return (f"{_tree_name(root)}/AGENTS.md", "crux/skills/prose-review/SKILL.md")


# Back-compat for tests and readers that want the shape without a root.
PROJECTIONS = ("bionic/AGENTS.md", "crux/skills/prose-review/SKILL.md")


sys.path.insert(0, str(Path(__file__).resolve().parent))
import authoring_scope as _scope  # noqa: E402

class RegenError(Exception):
    """A validation failure that belongs on the exit-1 findings lane."""


def _repo_root(explicit: str | None) -> Path:
    """The project root under inspection -- never this script's own location.

    `Path(__file__).parents[2]` used to stand here. In the authoring checkout it
    lands on the repo root and looks right; in an installed plugin it lands on the
    plugin cache, so a gate run from a consuming project inspected the plugin's own
    copy of itself and reported the result as if it were the project's.
    """
    return _scope.resolve_repo_root(explicit)


def read_text_verbatim(path: Path) -> str:
    """Read without universal-newline translation, so line endings survive."""
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text_atomically(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then atomically replace.

    A truncating in-place write can leave a target half-written if the process
    dies mid-write. One of the targets ships to third parties, so a torn file is
    not an acceptable failure mode.

    The original file's permission bits are copied onto the temp file before the
    replace. `mkstemp` creates 0600 and `os.replace` carries that mode onto the
    target, so without this every regeneration would silently turn a 0644
    document into an owner-only one.
    """
    mode = path.stat().st_mode
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, stat.S_IMODE(mode))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _marker_line(marker: str) -> re.Pattern[str]:
    """Match the marker only as a whole line (surrounding blanks tolerated).

    `\\r` is in the trailing class deliberately: reads use `newline=""`, so a CRLF
    file keeps its `\\r`, and `$` in MULTILINE mode matches before the `\\n` — with
    the `\\r` still sitting between the marker and the `$`. Omitting it makes every
    marker in a CRLF file invisible and the whole file unparseable.
    """
    return re.compile(r"^[ \t]*" + re.escape(marker) + r"[ \t\r]*$", re.MULTILINE)


def find_region(text: str, begin: str, end: str, label: str) -> tuple[int, int]:
    """Return (start, end) offsets of the bytes strictly between the marker lines.

    Fails closed on anything ambiguous: a missing marker, a duplicate marker, or
    END before BEGIN. Matching is line-anchored, so a marker quoted inside a
    fenced example is not a delimiter.
    """
    b = _marker_line(begin).findall(text)
    e = _marker_line(end).findall(text)
    if len(b) == 0 or len(e) == 0:
        missing = "BEGIN" if len(b) == 0 else "END"
        raise RegenError(
            f"{label}: missing marker ({missing} not found as its own line). "
            f"Expected exactly one line {begin!r} and one line {end!r}."
        )
    if len(b) > 1 or len(e) > 1:
        raise RegenError(
            f"{label}: duplicate marker (BEGIN x{len(b)}, END x{len(e)}). "
            "Exactly one of each is required; refusing to guess which pair is real."
        )
    mb = _marker_line(begin).search(text)
    me = _marker_line(end).search(text)
    assert mb is not None and me is not None  # counts above guarantee both
    if me.start() < mb.end():
        raise RegenError(f"{label}: END marker precedes BEGIN marker.")
    return mb.end(), me.start()


def extract_region(text: str, begin: str, end: str, label: str) -> str:
    i, j = find_region(text, begin, end, label)
    return text[i:j]


def replace_region(text: str, begin: str, end: str, body: str, label: str) -> str:
    i, j = find_region(text, begin, end, label)
    return text[:i] + body + text[j:]


def _load_canonical(root: Path) -> str:
    path = root / CANONICAL_FILE
    if not path.is_file():
        raise RegenError(f"{CANONICAL_FILE}: not found at {path}")
    canonical = extract_region(
        read_text_verbatim(path), CANONICAL_BEGIN, CANONICAL_END, CANONICAL_FILE
    )
    if not canonical.strip():
        raise RegenError(
            f"{CANONICAL_FILE}: canonical region is empty; refusing to project nothing."
        )
    for marker in (PROJECTION_BEGIN, PROJECTION_END):
        if marker in canonical:
            raise RegenError(
                f"{CANONICAL_FILE}: the canonical region contains the projection marker "
                f"{marker!r}. Projecting it would write a second delimiter into every "
                "target and wedge every later run. Remove it from the canonical block."
            )
    return canonical


def run(root: Path, dry_run: bool) -> tuple[int, dict]:
    # SURFACE-ABSENT LANE, second trigger. rule:out-of-scope-is-surface-absent names
    # two: a plugin-authoring gate outside the authoring checkout, AND a projection
    # regenerator whose marked region exists nowhere. Only the first was implemented,
    # so the public clone and the staged release artifact -- both of which carry
    # `crux/scripts/` and so pass the first probe -- got exit 1 with an error naming a
    # file that never ships. A missing FILE is an absent surface; a file that is
    # present but carries no marker is still BROKEN, because that is a real defect in
    # a tree that owns the surface.
    if not (root / CANONICAL_FILE).is_file():
        return 0, _scope.surface_absent_payload(
            reason=f"{CANONICAL_FILE} carries the canonical writing-rules block and is "
                   "not present here; this tree owns no projection of it")
    canonical = _load_canonical(root)

    # Pass 1 — validate EVERY target before mutating ANY of them, so a malformed
    # later target cannot leave earlier ones rewritten.
    planned: list[tuple[str, Path, str]] = []
    drifted: list[str] = []
    for rel in projections(root):
        path = root / rel
        if not path.is_file():
            raise RegenError(f"{rel}: projection target not found at {path}")
        text = read_text_verbatim(path)
        if extract_region(text, PROJECTION_BEGIN, PROJECTION_END, rel) == canonical:
            continue
        drifted.append(rel)
        planned.append(
            (rel, path, replace_region(text, PROJECTION_BEGIN, PROJECTION_END, canonical, rel))
        )

    if dry_run:
        if drifted:
            return 1, {
                "drifted": drifted,
                "canonical": CANONICAL_FILE,
                "remediation": "run `python3 crux/scripts/generate-writing-rules.py`",
            }
        return 0, {"drifted": [], "canonical": CANONICAL_FILE}

    # Pass 2 — write. Every target already parsed cleanly, so no validation
    # failure can strike mid-write. An I/O error still can: each individual write
    # is atomic, but the set is not, so a failure here can leave earlier targets
    # updated and later ones stale. That state is safe and self-correcting — the
    # drift gate reports it and a re-run converges, because regeneration is
    # idempotent.
    for _rel, path, text in planned:
        write_text_atomically(path, text)
    return 0, {"written": drifted, "canonical": CANONICAL_FILE}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="report drift; write nothing")
    ap.add_argument(
        "--repo-root", default=None, help="repo root to inspect (default: the working directory)"
    )
    args = ap.parse_args(argv)

    root = _repo_root(args.repo_root)

    # SURFACE-ABSENT LANE, on the `extract-code-docs` model. Every surface this
    # regenerator reads and every region it projects into lives in the plugin's own
    # source checkout. A consuming project owns none of them, so nothing here can
    # have drifted for that project and nothing can have been verified for it either.
    # Reporting a failure would file an inapplicable check as a defect in the reader's
    # repository; reporting a clean pass would file it as verified. This lane says
    # neither, and `check-drift` renders it N/A with the reason.
    if not _scope.is_authoring_checkout(root, __file__):
        return _scope.print_surface_absent()

    try:
        code, payload = run(root, args.dry_run)
    except RegenError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        # Environment/encoding problem, not a document verdict. A non-UTF-8 byte
        # pasted into any target must not escape as an uncaught traceback — that
        # would satisfy neither the exit-1 (JSON on stdout) nor the exit-2
        # (message on stderr) half of the contract.
        sys.stderr.write(f"generate-writing-rules.py: {exc}\n")
        return 2

    print(json.dumps(payload, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
