#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""generate-runtime-compat.py — the vendored regenerator for the runtime-compatibility block.

The fifteenth regenerative output (per ADR-0056's enrollment discipline,
established by ADR-0100 point 7). One canonical file, one projected region in
every non-exempt `crux/skills/*/SKILL.md`:

    canonical   crux/templates/runtime-compatibility.md   (the WHOLE file body)
    projection  crux/skills/<name>/SKILL.md   between BEGIN/END GENERATED: runtime-compat

The provenance note above lives HERE and not in the canonical file, and that is
a constraint rather than a preference. `check-public-release-content.py` fails
on the `ADR-\\d{4}` token anywhere under `crux/skills`, `crux/agents` or
`crux/templates`, line by line, with no carve-out for an HTML comment. The
canonical file and all of its projections sit in those roots, so neither may
cite an ADR by number. `crux/scripts/` is not one of those roots, exactly as
`generate-writing-rules.py` relies on.

This file follows `generate-writing-rules.py` deliberately and closely — same
whole-line marker matching, same fail-closed region resolution, same
validate-everything-then-write-anything ordering, same atomic mode-preserving
write. The two regenerators are NOT merged into one shared module in this
change: that refactor would put every writing-rules target at risk for a
benefit this output does not need. The duplication is named rather than hidden.

Properties, each with a test:

1. **No newline translation in transit.** Files are read and written with
   `newline=""`, so nothing rewrites an ending on the way through and a CRLF
   target keeps its CRLF endings everywhere outside the projected region. The
   region's own boundaries are NOT byte-preserving: `_load_canonical` returns
   `"\\n" + body.strip("\\r\\n") + "\\n"`, which hard-codes LF at both ends. A
   CRLF canonical body therefore projects its last content line with a bare LF,
   leaving the region not byte-equivalent to that body and mixing endings
   inside it. That costs nothing today because the canonical file is LF, and
   nothing enforces that it stays LF. `test_crlf_line_endings_survive` converts
   the TARGET only and asserts outside the region, so it covers the half that
   holds and not the half that does not.
2. **Markers are matched as whole lines**, tolerating trailing blanks and a
   `\\r`, so a marker mentioned mid-sentence is never a delimiter.
3. **Every target is validated before any target is written**, and each write is
   atomic (temp file + `os.replace`) with the original mode preserved.
4. **The canonical body may not contain the projection markers**, which would
   inject a second delimiter into every target and wedge every later run.
5. **Target derivation fails closed.** Targets are derived from the BEGIN
   marker, then cross-checked against the skill catalog on three legs: every
   catalogued skill must be either marked or named in `EXEMPT_SKILLS`; no
   exempt skill may carry the markers; and every `EXEMPT_SKILLS` entry must
   still name a catalogued skill. A skill that silently loses its markers
   FAILS rather than dropping out of the projection, a skill that is both
   marked and exempt fails, and an exemption left behind by a deleted skill
   fails — so the exemption list rots in no direction.

Residual risk, stated rather than papered over: a marker on its own line inside
a fenced code block IS counted as a marker. A target carrying real markers PLUS
a fenced example raises a loud duplicate-marker error and writes nothing, which
is the common case and fails closed. A target whose ONLY marker pair lives
inside a fence would be mis-delimited; nothing here prevents that, and what
prevents it today is that no target is authored that way.

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

CANONICAL_FILE = "crux/templates/runtime-compatibility.md"
SKILLS_DIR = "crux/skills"
CATALOG_FILE = "crux/catalog/skills.json"

PROJECTION_BEGIN = "<!-- BEGIN GENERATED: runtime-compat -->"
PROJECTION_END = "<!-- END GENERATED: runtime-compat -->"

# Skills that carry NO runtime-compatibility block, exempt by name. Measured
# against the 60-skill catalog: 56 carry the block byte-identically and these
# four do not. Property 5 above polices this list on all three of its failure
# modes — an exempt skill that grows markers fails, a non-exempt skill that
# loses them fails, and an entry naming a skill the catalog no longer carries
# fails.
EXEMPT_SKILLS = frozenset(
    {
        "escalate-arch-runtime",
        "prose-review",
        "recover-decisions",
        "transition-decision",
    }
)


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

    The original file's permission bits are copied onto the temp file before
    the replace. `mkstemp` creates 0600 and `os.replace` carries that mode onto
    the target, so without this every regeneration would silently turn a 0644
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

    `\\r` is in the trailing class deliberately: reads use `newline=""`, so a
    CRLF file keeps its `\\r`, and `$` in MULTILINE mode matches before the
    `\\n` — with the `\\r` still sitting between the marker and the `$`.
    Omitting it makes every marker in a CRLF file invisible.
    """
    return re.compile(r"^[ \t]*" + re.escape(marker) + r"[ \t\r]*$", re.MULTILINE)


def has_marker(text: str, marker: str = PROJECTION_BEGIN) -> bool:
    return _marker_line(marker).search(text) is not None


def find_region(text: str, begin: str, end: str, label: str) -> tuple[int, int]:
    """Return (start, end) offsets of the bytes strictly between the marker lines.

    Fails closed on anything ambiguous: a missing marker, a duplicate marker,
    or END before BEGIN. Never guesses an insertion point.
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
    """The whole canonical file body, normalized to one leading and one trailing newline.

    The projected region is the bytes strictly between the two marker LINES, so
    it necessarily opens with the newline that ends the BEGIN line and closes
    with the newline that starts the END line. Normalizing here is what makes
    regeneration idempotent regardless of how many blank lines the canonical
    file happens to end with.
    """
    path = root / CANONICAL_FILE
    if not path.is_file():
        raise RegenError(f"{CANONICAL_FILE}: not found at {path}")
    body = read_text_verbatim(path)
    if not body.strip():
        raise RegenError(
            f"{CANONICAL_FILE}: canonical file is empty; refusing to project nothing."
        )
    for marker in (PROJECTION_BEGIN, PROJECTION_END):
        if marker in body:
            raise RegenError(
                f"{CANONICAL_FILE}: the canonical body contains the projection marker "
                f"{marker!r}. Projecting it would write a second delimiter into every "
                "target and wedge every later run. Remove it from the canonical file."
            )
    return "\n" + body.strip("\r\n") + "\n"


def _catalogued_skills(root: Path) -> list[str]:
    path = root / CATALOG_FILE
    if not path.is_file():
        raise RegenError(f"{CATALOG_FILE}: not found at {path}")
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegenError(f"{CATALOG_FILE}: not valid JSON ({exc})") from exc
    if not isinstance(records, list) or not records:
        raise RegenError(f"{CATALOG_FILE}: expected a non-empty list of skill records.")
    try:
        return sorted(str(record["name"]) for record in records)
    except (TypeError, KeyError) as exc:
        raise RegenError(f"{CATALOG_FILE}: a record has no `name` key ({exc})") from exc


def resolve_targets(root: Path) -> list[str]:
    """Derive the target list from the BEGIN marker, then cross-check the catalog.

    Deriving from the marker alone would let a skill that lost its markers drop
    silently out of the projection — the exact failure this gate exists to
    catch. So the marker scan decides the targets, and the catalog decides
    whether that set is COMPLETE.
    """
    skills_root = root / SKILLS_DIR
    if not skills_root.is_dir():
        raise RegenError(f"{SKILLS_DIR}: not found at {skills_root}")

    marked: set[str] = set()
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        if has_marker(read_text_verbatim(skill_md)):
            marked.add(skill_md.parent.name)

    catalogued = _catalogued_skills(root)
    stale_exempt = sorted(EXEMPT_SKILLS - set(catalogued))
    if stale_exempt:
        raise RegenError(
            "EXEMPT_SKILLS entries naming skills the catalog does not carry: "
            f"{', '.join(stale_exempt)}. An exemption outliving its skill is the one "
            "way this list rots undetected — the other two legs only look at skills "
            "that still exist. Delete the entry, or run `validate-catalog.py` if the "
            "skill is present but uncatalogued."
        )
    unmarked = [n for n in catalogued if n not in marked and n not in EXEMPT_SKILLS]
    if unmarked:
        raise RegenError(
            "skills carrying neither the runtime-compat markers nor an exemption: "
            f"{', '.join(unmarked)}. A skill that loses its markers must fail here "
            "rather than drop silently out of the projection. Restore the markers, or "
            "add the skill to EXEMPT_SKILLS if it genuinely carries no block."
        )
    exempt_but_marked = sorted(marked & EXEMPT_SKILLS)
    if exempt_but_marked:
        raise RegenError(
            "skills listed in EXEMPT_SKILLS that DO carry the runtime-compat markers: "
            f"{', '.join(exempt_but_marked)}. Remove them from EXEMPT_SKILLS so the "
            "projection reaches them."
        )
    uncatalogued = sorted(marked - set(catalogued))
    if uncatalogued:
        raise RegenError(
            "marked skills absent from the catalog: "
            f"{', '.join(uncatalogued)}. Run `validate-catalog.py` first — projecting "
            "into a skill the catalog does not know would hide a catalog fault."
        )
    return [f"{SKILLS_DIR}/{name}/SKILL.md" for name in sorted(marked)]


def run(root: Path, dry_run: bool) -> tuple[int, dict]:
    canonical = _load_canonical(root)
    targets = resolve_targets(root)

    # Pass 1 — validate EVERY target before mutating ANY of them, so a
    # malformed later target cannot leave earlier ones rewritten.
    planned: list[tuple[str, Path, str]] = []
    drifted: list[str] = []
    for rel in targets:
        path = root / rel
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
                "targets": len(targets),
                "remediation": "run `python3 crux/scripts/generate-runtime-compat.py`",
            }
        return 0, {"drifted": [], "canonical": CANONICAL_FILE, "targets": len(targets)}

    # Pass 2 — write. Every target already parsed cleanly, so no validation
    # failure can strike mid-write. An I/O error still can: each individual
    # write is atomic, but the set is not, so a failure here can leave earlier
    # targets updated and later ones stale. That state is safe and
    # self-correcting — the drift gate reports it and a re-run converges,
    # because regeneration is idempotent.
    for _rel, path, text in planned:
        write_text_atomically(path, text)
    return 0, {"written": drifted, "canonical": CANONICAL_FILE, "targets": len(targets)}


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
        # Environment/encoding problem, not a document verdict. A non-UTF-8
        # byte pasted into any target must not escape as an uncaught traceback
        # — that would satisfy neither the exit-1 (JSON on stdout) nor the
        # exit-2 (message on stderr) half of the contract.
        sys.stderr.write(f"generate-runtime-compat.py: {exc}\n")
        return 2

    print(json.dumps(payload, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
