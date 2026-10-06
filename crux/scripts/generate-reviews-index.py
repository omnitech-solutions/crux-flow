#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""generate-reviews-index.py — the vendored regenerator for
<docs_dir>/adrs/reviews/index.md.

Per ADR-0101 requirement 5, extended by ADR-0106 (the lifecycle grammar, the
raised/standing split and the bounded locator): the reviews index is a
*derived* artifact. Each
dated report under `<docs_dir>/adrs/reviews/YYYY-MM-DD.md` is hand-kept; the
index that lists them is not. One row per report, newest first, carrying the
report's date, what it RAISED, where those findings now STAND, its dismissed
count, and a relative link. Byte-stable — the rendering carries no timestamp —
so a stale index is drift the gate catches rather than a special repair case,
exactly as `generate-adr-index.py` is stable for the same reason.

THE GRAMMAR DISCRIMINATOR. A report declares its grammar through the
frontmatter key `report_grammar`, whose one recognised value is `lifecycle`.
A report carrying no key reads under the frozen legacy counting rule; a report
dated after the boundary carrying none is refused; an unrecognised value is
refused. Neither is defaulted. The whole grammar — the definition rule, the
record table, the note pairing and the standing stream — lives in the sibling
module `review_findings`, which this script imports and never restates. That
module does no I/O after import: it takes text and returns structures, and the
one question needing the filesystem comes back here through an injected probe.

Counting rules, all three read from the report itself:
  raised     what the pass PUT ON THE RECORD. Under the lifecycle grammar it is
             the number of finding DEFINITIONS — `### `-headed entries under
             Propose, Amend, Repair or Revoke — so a summary row, a Coverage
             line and a fenced quote are references that count for nothing.
             Under the legacy grammar it is the FROZEN count of unique
             `adr-review-<slug>` tokens below the frontmatter, and the cell is
             labelled `N (legacy)` so a reader can see which rule produced it.
  standing   the per-standing count over THAT ROW'S raised findings, in the
             fixed order open, resolved, disputed, unknown, rendering only the
             non-zero standings joined by `, `. A legacy row renders `unknown N`
             at its frozen count — a positive value, never blank, never
             omitted, never `0`, and never recomputed from a later report's
             records — and at a ZERO frozen count it renders the bare literal
             `unknown` with no count, which is the only rendering that keeps
             all three of those promises at once. A LIFECYCLE row that raised
             nothing renders the em dash. When a
             report's records name findings ANOTHER report defined, the cell
             gains `; +N elsewhere`: the event is reported on the RECORDING
             row and never projected onto the row that defined the finding,
             whose cells do not move.
  dismissed  the length of the report frontmatter's `dismissed:` list, read in
             either the flow form (`dismissed: [a, b]`) or the block form
             (`dismissed:` followed by indented `- ` items). Owner dismissal is
             rendered BESIDE standing and is never evidence of resolution.

Standing is a CORPUS property, not a per-file one: a resolution written on one
date moves the standing of a finding another date raised. So `collect` parses
every report first, computes standing over the whole corpus, and only then
builds the rows.

THE LOCATOR EXISTENCE PROBE LIVES HERE. A `resolved` record's locator must name
a surface that exists in the tree, and that question is the one part of the
grammar needing the filesystem — which is exactly why `review_findings` asks it
through an injected callable rather than answering it. `_locator_probe` is that
callable. An `ADR-NNNN`, `OBS-NNNN`, `ADR-NNNN/<slug>`, `OBS-NNNN/<slug>` or
`rule:<slug>` locator is a HANDLE and is resolved against the TREE rather than
answered blindly: `_handle_exists` names the surface each handle class checks,
and [SECURITY:S5] every surface it reads goes through `_surface` first — a
symlink there, dangling or not, is refused in the ENVIRONMENT lane by name.
Anything else is a repo-relative path, and four shapes are refused in the
DOCUMENT lane and never followed: an absolute path, a `~`-prefixed path, a
`..` traversal, and a path reaching outside the repo root through a symlink.
The first three of those are refused earlier still, at parse time by
`review_findings`, on EVERY record whatever its event; the fourth is the one
only a resolve can answer, and it stays here. Postcondition: no locator is
ever rendered into the index, so no mined byte reaches the generated surface.

FAIL-CLOSED BEHAVIOUR (the reading of requirement 5 this script implements).
The regenerator refuses to build an index it cannot describe rather than
silently dropping a file it does not understand. Three refusals, all in BOTH
modes — a drift gate that passes on a corpus the writer would refuse is a
false green.

A refusal is a DOCUMENT finding, not an environment problem. Both exit 1 with
a `"validation_errors"` list on stdout, of the `{"file", "error"}` shape
`validate-catalog.py` emits, plus the message on stderr. `check-drift` reads a
non-empty `validation_errors` as BROKEN — the named input must be repaired,
and regenerating cannot fix it — which is the right verdict for a hand-edited
report. Exit 2 stays reserved for a genuine environment failure (an unreadable
file, a bad encoding, a malformed `.bionic.yml`), which `check-drift` reads as
CRASH. The three refusals:

  1. A NAME OUTSIDE THE GRAMMAR. An entry under `<docs_dir>/adrs/reviews/`
     whose name is neither `index.md` nor a valid ISO `YYYY-MM-DD.md` calendar
     date. `2026-02-30.md` is parseable as digits and is still refused: the
     date must exist. A subdirectory is refused for the same reason — the
     grammar admits no nesting. Dotfiles are the one carve-out: a name
     beginning with `.` is editor or filesystem debris (`.DS_Store` occurs in
     this very tree), never a review report, and is skipped without comment.
  2. A REPORT THAT DOES NOT DECLARE ITSELF. Frontmatter missing, `type` not
     `adr-review`, `date` disagreeing with the filename, or — through
     `review_findings` — the grammar discriminator absent past the boundary or
     carrying an unrecognised value.
  3. A CORPUS THAT DOES NOT COHERE. A slug two reports define, a record naming
     a definition that stands on no such date, a record predating the
     definition it names, a second record from one pass, a record against a
     finding an earlier resolution closed, or a locator outside the probe's
     containment. These are refusals about the DIRECTORY rather than about one
     file, so their `validation_errors` entry names the reviews directory:
     the disagreement is between two files and neither one is the offender.
AN ABSENT REVIEWS DIRECTORY IS NOT A REFUSAL. A tree that has never run a
review carries no `<docs_dir>/adrs/reviews/` at all, and that is the state of
every target repo on the day it installs the plugin. There is no derived
artifact, so there is nothing that can have drifted. Both modes exit 0 and set
`"surface_absent": true` on the JSON. That key is a POSITIVE DISCRIMINATOR, not
a silent pass: `check-drift` reads it as verdict N/A and never as a clean gate,
so an absent surface cannot be mistaken for a measured one. This is the one
place the script distinguishes "no reviews yet" — an empty directory, which
renders `Reports: 0` and is a real measured pass — from "no reviews surface".

ORPHAN ROWS are drift, not a refusal. An on-disk `index.md` row naming a report
file that does not exist is a row the reports cannot justify. In `--dry-run`
that is exit 1 with `"drift": true` and an `"orphan_rows"` list in the JSON; in
write mode the index is rebuilt from the reports on disk, which removes it.
The distinction is deliberate — an orphan row is a stale *output*, which
regeneration fixes, while a malformed *input* is a fact about the corpus that
only a human can resolve.

CONTAINMENT AND REDACTION. Two guards, because this regenerator reads a
directory whose contents a pull request can add to and writes a file whose
path is derived from `.bionic.yml`.

  * [SECURITY:S5] Every resolved path is containment-checked before it is read
    or written. Both directory segments the tree supplies — `adrs` and
    `reviews` — are refused when either IS a symlink, whether or not the link
    resolves; `is_dir()` follows a link, so a dangling one read as an absent
    surface and left the run through the `surface_absent` branch, silently
    reporting N/A on a checkout that had redirected the surface off the tree.
    The refusal names the segment it refused. The tree dir must resolve under
    the repo root, the reviews dir under the tree dir, and — through this
    file's own copy of
    `_atomic_write_text`, which each script carries rather than shares — the
    index's parent under the tree dir, which is ADR-0101 requirement 5's "the
    resolved write path is containment-checked under the resolved
    `<docs_dir>`". An entry under the reviews directory that is a symlink is
    refused rather than followed, so no read leaves the tree either.
  * [SECURITY:S1] Every value this script quotes back — a filename, a
    frontmatter scalar, an orphan row's link text — is a value the report's
    author chose, and all three reach `validation_errors` on stdout AND the
    message on stderr. Each is routed through `untrusted.redact`, which bounds
    the rendered length and replaces every unprintable character, so a 40 KB
    `type:` cannot inflate the envelope and a newline in a filename cannot
    forge a second stderr line. `redact` does no channel escaping and is not a
    substitute for it; nothing here renders into Markdown.

Usage:
  generate-reviews-index.py [--repo-root DIR]     rewrite adrs/reviews/index.md
  generate-reviews-index.py --dry-run [...]       exit 1 + JSON if drift

Exit: 0 clean/written · 1 drift or a report/corpus validation error (JSON on
stdout) · 2 env error (stderr), which includes a symlinked directory segment.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import review_findings  # noqa: E402
from untrusted import MESSAGE_LIMIT, redact  # noqa: E402

#: [SECURITY:S1] `[0-9]`, never `\d`. `\d` matches every Unicode decimal digit
#: in a `str` pattern, so a filename spelled in Arabic-Indic digits matched a
#: pattern whose whole job is to say "this is an ISO-dated report", and the
#: groups then reached `datetime.date`. The same rule holds for the two
#: handle patterns below, whose digits are interpolated into a glob.
DATE_NAME = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})\.md$")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)

#: A locator that is a HANDLE rather than a path. Resolved against the tree
#: by `_handle_exists` rather than followed as a filesystem path.
LOCATOR_HANDLE = re.compile(
    r"\A(?:(?:ADR|OBS)-[0-9]{4}(?:/[a-z0-9][a-z0-9-]*)?|rule:[a-z][a-z0-9-]*)\Z")

#: The 4 digits of an `ADR-NNNN`/`OBS-NNNN` handle. Bounds the glob built from
#: an otherwise-untrusted locator to exactly the digits `LOCATOR_HANDLE`
#: already validated — the slug, if present, never reaches a glob.
_HANDLE_KIND_AND_DIGITS = re.compile(r"\A(ADR|OBS)-([0-9]{4})(?:/|\Z)")

#: A `rule:<slug>` handle's slug, already bounded by `LOCATOR_HANDLE`.
_HANDLE_RULE_SLUG = re.compile(r"\Arule:([a-z][a-z0-9-]*)\Z")

#: An index row linking to a report. FOUR leading cells now — date, raised,
#: standing, dismissed — then the link cell. The count moves with `HEADER`:
#: a regex counting three would stop matching every row and report every
#: report as an orphan-free index, which is drift the gate could not see.
INDEX_ROW_REPORT = re.compile(
    r"^\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|\s*\[([^\]]+)\]\([^)]*\)\s*\|\s*$")

HEADER = [
    "# Decision reviews",
    "",
    "<!-- Generated by crux/scripts/generate-reviews-index.py — do not hand-edit. -->",
    "",
    "One row per `review-decisions` pass, newest first. Each dated report is "
    "hand-kept; this index is derived from the reports in this directory.",
    "",
    "| date | raised | standing | dismissed | report |",
    "|------|--------|----------|-----------|--------|",
]


class ReviewsError(Exception):
    """A report this regenerator refuses to describe — a validation error.

    Carries the offending path and the problem separately so `main` can emit a
    `validation_errors` entry of the same `{"file", "error"}` shape
    `validate-catalog.py` uses, rather than reparsing the message string.

    [SECURITY:S1] `file` is bound-and-redacted HERE rather than at each raise
    site, because it reaches two channels — the `validation_errors[]["file"]`
    key on stdout and the composed message on stderr — and a filename is a
    value the report's author chose. A newline is legal in a POSIX filename
    and forged a second stderr line where the contract is one; every
    unprintable character is now replaced. A short printable path round-trips
    to exactly itself, so no existing assertion moves. Callers redact the
    untrusted parts of `problem` at their own sites, where the surrounding
    prose tells them which value is theirs.
    """

    def __init__(self, file: str, problem: str) -> None:
        safe = redact(file, quoted=False)
        super().__init__(f"{safe}: {problem}")
        self.file = safe
        self.problem = problem


def _atomic_write_text(path: Path, body: str, *, contained_under: Path) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    Writes to <path>.tmp then os.replace()s into place. Bytes are written
    directly so Python does NOT translate '\\n' -> '\\r\\n' on Windows; that
    translation would shift the file's sha256 across platforms and break the
    byte-stable regenerative output contract.

    [SECURITY:S5] Every write target's RESOLVED parent directory must sit under
    `contained_under` — the validated tree dir. This guard covers the
    INTERMEDIATE directories (a symlink at <tree>/adrs or <tree>/adrs/reviews
    leaves every entry under it a real file while steering the write outside the
    repo root); the leaf checks below guard the target and its tmp file. Shared
    posture with compile-doctrine.py / summarize-adrs.py / signoff-backfill.py —
    same guards, same order, same exception type (OSError); each names its own
    subject in its messages ("reviews index content" here). Change one, change
    all.
    """
    root_resolved = Path(contained_under).resolve()
    parent_resolved = path.parent.resolve()
    if parent_resolved != root_resolved \
            and root_resolved not in parent_resolved.parents:
        raise OSError(
            f"refusing to write {path}: the parent directory resolves to "
            f"{parent_resolved}, which is not contained under the validated "
            f"tree dir {root_resolved} — a symlinked intermediate directory "
            "would put reviews index content outside the tree"
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise OSError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put reviews index content in the "
                "link's target"
            )
    try:
        # O_EXCL refuses a PRE-EXISTING temporary file rather than
        # clobbering it: a left-over `index.md.tmp` may be another writer's,
        # and the FileExistsError below names it instead of overwriting it.
        # O_NOFOLLOW refuses a SYMLINK at the temporary path, raising ELOOP,
        # so the `is_symlink()` check above is not a TOCTOU window: the tmp
        # path is predictable, and between that check and this open it can be
        # replaced with a link. Only the flag on the open itself closes the
        # window. A planted `index.md.tmp -> /etc/crontab` would otherwise be
        # opened through and then renamed over.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError as exc:
        raise OSError(
            f"refusing to write {path}: the temporary file {tmp} already exists; "
            "remove it after checking what created it"
        ) from exc
    except OSError as exc:  # ELOOP from O_NOFOLLOW, or an unwritable directory
        raise OSError(f"refusing to write {path}: cannot create {tmp} ({exc})") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body.encode("utf-8"))
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_bionic_config():
    """Import the sibling `bionic_config.py` by path and cache it."""
    key = "_bionic_config"
    module = sys.modules.get(key)
    if module is None:
        target = Path(__file__).resolve().parent / "bionic_config.py"
        spec = importlib.util.spec_from_file_location(key, target)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load spec for {target}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[key] = module
    return module


def _tree_name(root: Path) -> str:
    """The documentation tree's directory name, resolved per bionic/AGENTS.md §14.1.

    Resolution runs through `bionic_config`, never an ad-hoc read: a local
    regex over `.bionic.yml` skips the legacy `.crux` tier and skips
    bare-directory discovery, so a zero-config `docs/` tree resolves to
    `bionic` and its real surface reads as absent.
    """
    return _load_bionic_config().resolve_tree_name(root)


def _contained(child: Path, parent: Path, subject: str) -> Path:
    """`child`, refused when it resolves outside `parent`.

    [SECURITY:S5] The READ-side twin of `_atomic_write_text`'s write-side
    guard, applied to the reviews directory. A checkout supplies the
    directories under the tree, so `<tree>/adrs` or `<tree>/adrs/reviews` can
    be a symlink pointing anywhere on the filesystem; without this,
    `iterdir()` would enumerate — and `_read_report` would read — a directory
    outside the repository, and its first line would be quoted back into
    `validation_errors`. Raised as OSError so it lands in `main`'s environment
    lane (exit 2, CRASH to `check-drift`): a symlinked directory is a fact
    about the checkout, not a hand-edited document a human repairs by fixing a
    report.

    BACKSTOP, KNOWINGLY. The per-segment symlink walk in `build` runs BEFORE
    this call and refuses either segment by name, so on a hostile checkout the
    walk fires first and this check never decides the outcome. It stays
    because it is the leg that survives a caller reordering: the walk answers
    "is this segment a link", this answers "does the resolved path land under
    the tree", and only the second is a property of the RESOLVED path. The
    ancestor-window race between the two — a segment swapped for a link after
    the walk and before the read — is NOT closed here. Closing it needs
    `O_DIRECTORY|O_NOFOLLOW` with a `dir_fd`-relative walk, which is a
    deliberate deferral rather than an oversight.
    """
    resolved = child.resolve()
    parent_resolved = parent.resolve()
    if resolved != parent_resolved and parent_resolved not in resolved.parents:
        raise OSError(
            f"refusing to read {child}: the {subject} resolves to {resolved}, "
            f"which is not contained under {parent_resolved}")
    return child


#: The resolver read's size bound. It matches `lint-governs-references.py`'s
#: `MAX_FILE_BYTES`, which is this repository's bound for a MACHINE-GENERATED
#: file a crux script reads.
#:
#: It used to be 64 KiB, taken from the `bionic_config.py` precedent. That was
#: a category error, and it shipped a false premise in its own refusal text.
#: The 64 KiB precedents bound HAND-AUTHORED documents — a `.bionic.yml`
#: config, a catalog document — where "past 64 KiB this is not a plausible
#: <thing>" is true. `resolver.json` is neither hand-authored nor bounded by
#: what a person will type: it is the summaries projection, carrying one
#: record per rule handle, and its size scales with the ADR corpus. Measured
#: on this repository at 169 handles: 125,665 bytes, about 700 bytes per
#: handle, the bulk of it rule text. So the artifact crossed the bound in the
#: ordinary course of the corpus growing, and the refusal then called a
#: correct, freshly regenerated projection "not a plausible rule resolver".
#:
#: The bound is NOT set to "bigger than today's file". 4 MiB is the constant
#: this repository already uses for this class of read, and at the measured
#: ~700 bytes per handle it admits roughly 5,900 handles — about 35x the
#: present corpus — while still refusing a file no projection would produce.
#:
#: Everything the bound protects is unchanged: the read is still bounded
#: (`RESOLVER_LIMIT + 1` through one open handle, so memory is capped and the
#: check-then-read race stays closed), the surface is still containment- and
#: symlink-checked by `_surface`, and every other refusal below — non-regular
#: file, unreadable, non-JSON, non-object, non-map `slugs`/`retired_slugs` —
#: still fires. Only the threshold moved, and only onto a defensible basis.
RESOLVER_LIMIT = 4 * 1024 * 1024


def _surface(tree: Path, rel: str, subject: str) -> Path:
    """A surface the HANDLE probe reads, refused when it is hostile.

    [SECURITY:S5] The per-segment walk in `build` covers `<tree>/adrs` and
    `<tree>/adrs/reviews`. It never covered the four surfaces this probe
    reaches on its own — `adrs/archive`, `observations`, `adrs/summaries` and
    `adrs/summaries/resolver.json` — and each of them is a path a checkout
    supplies. Two refusals, in the order that makes the second meaningful:

      1. A SYMLINK, whether or not it resolves. `is_dir()` and `is_file()` are
         both FALSE through a dangling link, so a dangling link at the archive
         read as an absent tier and a dangling link at the resolver took the
         graceful-degradation branch below and answered True — a handle naming
         nothing resolved clean, on a checkout that had redirected the surface.
         The refusal NAMES the surface it refused.
      2. CONTAINMENT of the resolved path under the resolved tree, on the
         `observation_evidence.resolve_contained` model: resolve both sides and
         require the tree to be an ancestor. Spelling cannot decide
         containment; the resolved path can.

    LANE: environment (exit 2), beside `_contained` and the segment walk and
    for their reason. A redirected surface is a fact about the CHECKOUT, not a
    hand-edited document a human repairs by fixing a report.
    """
    path = tree.joinpath(*rel.split("/"))
    if path.is_symlink():
        raise OSError(
            f"refusing to read {path}: the {subject} is a symlink — the handle "
            "probe reads only real surfaces under the tree, and following one "
            "would read outside it. A dangling link is refused on the same "
            "terms rather than read as an absent surface")
    if path.exists():
        tree_resolved = tree.resolve()
        resolved = path.resolve()
        if resolved != tree_resolved and tree_resolved not in resolved.parents:
            raise OSError(
                f"refusing to read {path}: the {subject} resolves to "
                f"{resolved}, which is not contained under {tree_resolved}")
        return path
    # ABSENT, or absent-LOOKING. `exists()` is false when a component of the
    # path is not a directory — a regular FILE at `adrs/summaries` makes
    # `adrs/summaries/resolver.json` read as an absent ledger, and an absent
    # ledger is the branch that answers True for every `rule:` handle. That is
    # a broken checkout answering a question about a handle, so it takes the
    # environment lane and names the surface that is in the way. `lstat`
    # rather than `is_dir`, so a symlinked parent is refused as what it is
    # rather than followed.
    parent = path.parent
    try:
        mode = os.lstat(parent).st_mode
    except OSError:
        return path  # the parent is absent too: a genuinely absent surface
    if not stat.S_ISDIR(mode):
        raise OSError(
            f"refusing to read {path}: the {subject} is absent because "
            f"{parent} is not a directory; a non-directory there hides the "
            "surface rather than proving it absent")
    return path


def _resolver_slugs(resolver: Path) -> dict | None:
    """The resolver's `slugs` and `retired_slugs` maps, or None when ABSENT.

    ABSENT means the file does not exist AND is not a symlink — `_surface`
    has already refused the symlink case, so a dangling link can no longer
    reach the None branch and be mistaken for a tree that never enabled
    `governs`. Everything else that is not a readable, in-bound, well-shaped
    JSON object is an ENVIRONMENT refusal naming the file: a broken ledger is
    a fact about the checkout, and answering True for one would let a handle
    that names nothing resolve clean.
    """
    if not resolver.exists():
        return None
    if not resolver.is_file():
        raise OSError(
            f"refusing to read {resolver}: resolver.json is present but is not "
            "a regular file, so it is neither an absent ledger nor a readable "
            "one; a directory or a FIFO here is a broken checkout")
    # ONE OPEN, bounded by the read rather than by a prior `stat`. Sizing the
    # file and then reading it left a window in which the file grew between the
    # two calls, and the read that followed was unbounded. Reading
    # `RESOLVER_LIMIT + 1` bytes through the open handle closes the window: a
    # short read is the whole file, and a full one proves the file exceeds the
    # bound whatever `stat` would have said. `st_size` is not read here at all,
    # so the refusal names the bound rather than the file's exact length —
    # deliberately: the message crosses a channel, and a byte count is a fact
    # about a file the caller may not be entitled to.
    try:
        with resolver.open("rb") as handle:
            raw = handle.read(RESOLVER_LIMIT + 1)
    except OSError as exc:
        raise OSError(
            f"refusing to read {resolver}: resolver.json is present but "
            f"cannot be read ({type(exc).__name__})") from exc
    if len(raw) > RESOLVER_LIMIT:
        raise OSError(
            f"refusing to read {resolver}: resolver.json exceeds "
            f"{RESOLVER_LIMIT} bytes ({RESOLVER_LIMIT // (1024 * 1024)} MiB); no "
            "summaries projection is that large, so this is not a rule resolver "
            "this reader will read")
        # The unit is DERIVED from the constant, never spelled beside it. The
        # message this replaced read "(64 KiB)" and stayed literally correct
        # while the sentence around it had become false; a hand-written unit is
        # one more thing that can drift away from the number it describes.
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise OSError(
            f"refusing to read {resolver}: resolver.json is present but does "
            f"not parse as JSON ({type(exc).__name__})") from exc
        # `RecursionError` is caught beside the two value errors deliberately.
        # A deeply nested array is valid JSON that `json.loads` cannot build,
        # and uncaught it left `main` through the generic handler — exit 2 with
        # EMPTY stdout, the lane the exit conventions reserve for a crash. The
        # message names `resolver.json`, so a reader knows which file to open.
    if not isinstance(data, dict):
        raise OSError(
            f"refusing to read {resolver}: resolver.json is a "
            f"{type(data).__name__} at its top level; the rule resolver is an "
            "object carrying `slugs` and `retired_slugs`")
    maps: dict = {}
    for key in ("slugs", "retired_slugs"):
        value = data.get(key, {})
        if not isinstance(value, dict):
            raise OSError(
                f"refusing to read {resolver}: resolver.json's `{key}` is a "
                f"{type(value).__name__}; it is a map keyed by rule slug")
        maps[key] = value
    return maps


def _handle_exists(locator: str, tree: Path, resolver_maps=None) -> bool:
    """Whether a HANDLE locator (already matched by `LOCATOR_HANDLE`) names a
    real surface under `tree`.

    An `ADR-NNNN` or `ADR-NNNN/<slug>` handle exists when `<tree>/adrs/` or
    `<tree>/adrs/archive/` holds an `ADR-NNNN-*.md` file — the archived tier
    counts, because an ADR archived after a report cited it did not stop
    existing. An `OBS-NNNN` or `OBS-NNNN/<slug>` handle exists when
    `<tree>/observations/` holds an `OBS-NNNN-*.md` file. Only the 4 digits
    `LOCATOR_HANDLE` already bounded feed the glob — the slug, if present,
    plays no part in it; never glob on untrusted text.

    A `rule:<slug>` handle is resolved against
    `<tree>/adrs/summaries/resolver.json`'s `slugs` and `retired_slugs` maps
    (both keyed by rule slug; a retired slug counts as existing, because a
    retired rule keeps its record). GRACEFUL DEGRADATION, deliberately: when
    that file is ABSENT, the summaries projection is optional per-tree and a
    tree that never enabled `governs` has no ledger to probe, so refusing here
    would make the reviews index unbuildable on a tree that did nothing wrong
    — answer True. ABSENT is now the narrow thing it says: the file does not
    exist and is not a symlink. Present-but-unreadable, present-but-unparseable,
    present-but-not-a-regular-file, oversize and wrong-shaped are each an
    ENVIRONMENT refusal naming the file, never a silent True for a broken
    ledger, and never an `AttributeError` from `.get` on a list.

    [SECURITY:S5] Every surface reached here goes through `_surface` first.
    Only the surfaces this handle class actually reads are checked, so a
    checkout is refused for a redirection this run would have followed rather
    than for one it would not.
    """
    digits = _HANDLE_KIND_AND_DIGITS.match(locator)
    if digits:
        kind, number = digits.group(1), digits.group(2)
        pattern = f"{kind}-{number}-*.md"
        if kind == "ADR":
            dirs = (_surface(tree, "adrs", "adrs directory"),
                    _surface(tree, "adrs/archive", "adrs archive directory"))
        else:
            dirs = (_surface(tree, "observations", "observations directory"),)
        return any(d.is_dir() and any(d.glob(pattern)) for d in dirs)
    slug = _HANDLE_RULE_SLUG.match(locator).group(1)
    if resolver_maps is None:
        # No cache supplied: walk and read the two surfaces here. `build`
        # supplies one, so this is the direct-call path.
        _surface(tree, "adrs/summaries", "summaries directory")
        maps = _resolver_slugs(_surface(tree, "adrs/summaries/resolver.json",
                                        "rule resolver resolver.json"))
    else:
        maps = resolver_maps()
    if maps is None:
        return True
    return slug in maps["slugs"] or slug in maps["retired_slugs"]


def _locator_probe(root: Path, subject: str, tree: Path):
    """The injected existence probe `standing_by_finding` asks about a locator.

    [SECURITY:S5] The probe is the one part of the finding grammar that
    touches the filesystem, which is why `review_findings` takes it as a
    callable and this script — the module that already owns containment —
    supplies it. A HANDLE is resolved against `tree` by `_handle_exists`.
    Anything else is a repo-relative path, and four shapes are refused and
    NEVER followed: an absolute path, a `~`-prefixed path, a `..` traversal,
    and a path reaching outside the repo root through a symlink. Same
    resolve-then-contain posture as `_contained`.

    The lane differs from `_contained`'s deliberately. A symlinked directory is
    a fact about the CHECKOUT (exit 2); a locator is a value a hand-edited
    REPORT chose, so refusing it is a document finding (exit 1) — the same lane
    every other refusal about a report's content takes. It raises
    `review_findings.ReviewFindingsError` rather than `ReviewsError` so that
    `standing_by_finding`, the layer that knows WHICH record supplied the
    locator, can re-raise it carrying the record's line and report date;
    `build` maps the result to the same `ReviewsError(tree_rel)` envelope the
    probe used to raise itself, so the JSON shape does not move.

    BACKSTOP, NOT THE FRONT LINE. The three TEXTUAL shapes below — absolute,
    `~`-prefixed and `..` — are now refused at parse time by
    `review_findings._refuse_escaping_shape`, on EVERY record whatever its
    event, so the probe never sees one on a corpus that got this far. They stay
    here because this is the layer that resolves, and a guard that only holds
    while a caller behaves is not a guard. The symlink-escape leg is the one
    the probe alone can answer: spelling cannot express it.

    [SECURITY:S1] The locator reaches `validation_errors` and stderr, so it is
    bound-and-redacted at every raise site here.
    """
    root_resolved = root.resolve()
    # ONE resolver read per BUILD. Every `rule:` handle in every report used to
    # re-walk the two surfaces and re-parse the file, so a corpus naming N rule
    # handles opened `resolver.json` N times — and a checkout that changed it
    # mid-run could answer two handles differently. The cache holds the
    # refusal too: a broken ledger is refused once, on the same terms.
    cache: list = []

    def resolver_maps():
        if not cache:
            _surface(tree, "adrs/summaries", "summaries directory")
            cache.append(_resolver_slugs(_surface(
                tree, "adrs/summaries/resolver.json",
                "rule resolver resolver.json")))
        return cache[0]

    def probe(locator: str) -> bool:
        if LOCATOR_HANDLE.match(locator):
            return _handle_exists(locator, tree, resolver_maps)
        if locator.startswith("~") or Path(locator).is_absolute():
            raise review_findings.ReviewFindingsError(
                f"the locator {redact(locator)} is an absolute or "
                "home-relative path; a locator's existence probe resolves only "
                "inside the repository root, and such a path is refused rather "
                "than followed")
        if ".." in Path(locator).parts:
            raise review_findings.ReviewFindingsError(
                f"the locator {redact(locator)} traverses out of the repository "
                "root with `..`; it is refused rather than followed")
        try:
            resolved = (root_resolved / locator).resolve()
            here = resolved.exists()
        except (OSError, ValueError) as exc:
            # [SECURITY:S1] A NUL byte raises `ValueError` and an over-long
            # component raises `OSError` (ENAMETOOLONG). Uncaught, either left
            # `main` through the generic handler: exit 2 with EMPTY stdout, the
            # lane the exit conventions reserve for a crash, for a value a
            # report's author typed. The locator grammar refuses both shapes
            # before this line, so this is the guard that keeps a future
            # grammar change from re-opening the lane.
            raise review_findings.ReviewFindingsError(
                f"the locator {redact(locator)} cannot be resolved as a path "
                f"({type(exc).__name__}); it is refused rather than followed"
            ) from None
        if resolved != root_resolved and root_resolved not in resolved.parents:
            raise review_findings.ReviewFindingsError(
                f"the locator {redact(locator)} resolves outside the repository "
                "root through a symlink; it is refused rather than followed")
        return here

    return probe


def _scalar(raw: str) -> str:
    return raw.strip().strip("\"'").strip()


def _field(fm: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}[ \t]*:[ \t]*(.*)$", fm, re.MULTILINE)
    return _scalar(m.group(1)) if m else None


def _dismissed_count(fm: str, rel: str) -> int:
    """Length of the frontmatter `dismissed:` list, flow or block form."""
    m = re.search(r"^dismissed[ \t]*:[ \t]*(.*)$", fm, re.MULTILINE)
    if not m:
        raise ReviewsError(rel, "frontmatter carries no `dismissed:` list")
    inline = m.group(1).strip()
    if inline.startswith("["):
        body = inline[1:inline.rindex("]")] if "]" in inline else inline[1:]
        return len([item for item in body.split(",") if item.strip()])
    if inline:
        raise ReviewsError(
            rel, f"`dismissed:` must be a list, found the scalar "
                 f"{redact(inline)}")
    count = 0
    for line in fm[m.end():].splitlines():
        if not line.strip():
            continue
        if re.match(r"^[ \t]*-[ \t]", line):
            count += 1
            continue
        break  # the next key at any indent ends the block list
    return count


def _read_untranslated(path: Path) -> str:
    """`path` decoded as UTF-8 with newline translation OFF.

    [SECURITY:S1] `Path.read_text` opens in universal-newline mode, which
    rewrote a lone `\r` to `\n` before the grammar saw the text: one cell split
    into a second row nobody wrote, which hid a `..` traversal and bypassed
    `LOCATOR_LIMIT`. Reading here hands the byte on untouched. INTERIOR to a
    cell it is refused in the document lane — by the C0/C1 class of the locator
    grammar in the locator cell, by its own grammar in each of the four others.
    At a cell's EDGE it is whitespace to the `str.strip` in `_cells` and
    `_record_from_row`, so it is dropped before the grammar reads the cell and
    reaches no channel.

    The index and drift reads call this function too, so the comparison is
    BYTE-exact: a translated read made a CRLF index compare equal to the LF
    text this script renders, hiding the drift a `--dry-run` exists to report.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _read_report(path: Path, rel: str) -> dict:
    # `\r\n` is a LINE ENDING rather than a payload, so it is normalised here
    # and a CRLF report parses to exactly what its LF twin parses to. A LONE
    # `\r` survives this replace and reaches the grammar, which is the point.
    text = _read_untranslated(path).replace("\r\n", "\n")
    m = FRONTMATTER.match(text)
    if not m:
        raise ReviewsError(rel, "no frontmatter block — a review report must open with ---")
    fm, body = m.group(1), text[m.end():]
    kind = _field(fm, "type")
    if kind != "adr-review":
        raise ReviewsError(
            rel, f"frontmatter `type` is {redact(kind)}, expected 'adr-review'")
    stem = path.name[: -len(".md")]
    declared = _field(fm, "date")
    if declared != stem:
        raise ReviewsError(
            rel, f"frontmatter `date` is {redact(declared)} but the filename "
                 f"says {redact(stem)}")
    try:
        # `line_offset` is the frontmatter's own line count, so every line
        # number the grammar reports is file-relative rather than
        # body-relative — a reader opens the file at the number it names.
        parsed = review_findings.parse_report(
            date=stem, frontmatter=fm, body=body,
            line_offset=text[: m.end()].count("\n"))
        review_findings.validate_structure(parsed)
    except review_findings.ReviewFindingsError as exc:
        # A grammar refusal is a DOCUMENT refusal about THIS file, so it takes
        # the same exit-1 `validation_errors` lane every other report refusal
        # takes. The module raises nothing else, and it performs no I/O after
        # import — the text it parses arrives from this function's own read.
        raise ReviewsError(rel, exc.problem) from exc
    return {
        "date": stem,
        "name": path.name,
        "parsed": parsed,
        "dismissed": _dismissed_count(fm, rel),
    }


def collect(reviews: Path, tree_rel: Path, today: datetime.date | None = None) -> list[dict]:
    """Every report under `reviews`, newest first. Raises on a refusal.

    `today` is the clock the future-date refusal reads; `None` means the
    system date. A future-dated report would anchor every cadence check
    (`cleanup-campsite` CLN-ADR-5, the gardener's review-age step) on a date
    that never arrives and suppress the nudge for as long as it sits there.
    """
    today = datetime.date.today() if today is None else today
    reports: list[dict] = []
    for entry in sorted(reviews.iterdir()):
        if entry.name.startswith("."):
            continue
        rel = str(tree_rel / entry.name)
        # [SECURITY:S5] Checked BEFORE the `index.md` skip, so the derived
        # index cannot be a symlink either: `build` reads it back for the
        # orphan-row scan, and that content reaches the JSON envelope.
        # `is_file()` follows the link, so a `2026-01-01.md -> /etc/passwd`
        # entry would otherwise be read as a report and its first line quoted
        # into `validation_errors`.
        #
        # LANE, and why it differs from the segment walk in `build`. A LEAF
        # under the reviews directory takes the DOCUMENT lane (exit 1, a
        # `validation_errors` entry naming the file): a report file is content
        # a pull request added, and a human repairs it by deleting or replacing
        # that one entry. A directory SEGMENT — `adrs`, `reviews`, and the four
        # surfaces `_surface` guards — takes the ENVIRONMENT lane (exit 2): a
        # redirected directory is a fact about the checkout, and `check-drift`
        # reads CRASH, which is the verdict that sends a human to the
        # filesystem rather than to a report.
        if entry.is_symlink():
            raise ReviewsError(
                rel, "is a symlink — the reviews grammar admits only regular "
                     "files, and following one would read outside the tree")
        if entry.name == "index.md":
            if entry.is_file():
                continue
            # NAME THE KIND. This used to fall through to the name refusal
            # below and report "outside the reviews grammar", which is wrong
            # twice over: the name IS in the grammar, and the reader is left
            # looking for a typo in a name that has none.
            raise ReviewsError(
                rel, "is a directory, and the derived index must be a regular "
                     "file this regenerator can rewrite"
                     if entry.is_dir() else
                     "is not a regular file, and the derived index must be one")
        m = DATE_NAME.match(entry.name) if entry.is_file() else None
        if not m:
            # NAME THE KIND, as the `index.md` branch above does. A directory
            # called `2026-09-07.md` has a name the grammar admits; reporting
            # "outside the reviews grammar" sent the reader hunting for a typo
            # in a name that has none.
            if DATE_NAME.match(entry.name):
                raise ReviewsError(
                    rel, "is a directory, and a review report must be a "
                         "regular file this regenerator can read"
                         if entry.is_dir() else
                         "is not a regular file, and a review report must be one")
            raise ReviewsError(
                rel, "name is outside the reviews grammar — expected "
                     "index.md or a YYYY-MM-DD.md report file")
        try:
            report_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise ReviewsError(
                rel, f"{redact(m.group(0)[:-3], quoted=False)} is not a "
                     f"calendar date ({redact(exc, quoted=False)})") from exc
        if report_date > today:
            raise ReviewsError(
                rel, f"{redact(m.group(0)[:-3], quoted=False)} is in the future "
                     f"(today is {today.isoformat()}) — a future-dated report would "
                     f"suppress the decision-review cadence nudge")
        reports.append(_read_report(entry, rel))
    reports.sort(key=lambda r: r["date"], reverse=True)
    return reports


def raised_cell(parsed) -> str:
    """`raised`, labelled `(legacy)` when the number is the frozen count.

    The label is not decoration: 6 definitions and 6 legacy tokens are two
    different measurements, and a reader comparing rows must be able to see
    which rule produced each.
    """
    return f"{parsed.raised} (legacy)" if parsed.is_legacy else str(parsed.raised)


def standing_cell(parsed, counts: dict[str, int], elsewhere: int) -> str:
    """The per-standing count, in the fixed order, plus any elsewhere events.

    A LEGACY row is decided first, because its clause is absolute: it carries
    the literal `unknown`, never blank, never omitted, never `0`, and never
    recomputed from a later report's records. At a nonzero frozen count that
    reads `unknown N`; at zero it reads the BARE literal `unknown` with no
    count, which is the only rendering that satisfies literal-`unknown`,
    never-omitted and never-`0` at once. The em-dash branch used to run first
    and rendered a blank-shaped cell for exactly that row.

    A LIFECYCLE row that raised nothing renders the em dash — there is no
    finding to be in a standing, and `open 0` would claim a measurement nobody
    made.
    """
    if parsed.is_legacy:
        cell = f"unknown {parsed.raised}" if parsed.raised else "unknown"
    elif parsed.raised == 0:
        cell = "\u2014"
    else:
        cell = ", ".join(f"{state} {counts[state]}"
                         for state in review_findings.STANDINGS
                         if counts.get(state))
    if elsewhere:
        cell += f"; +{elsewhere} elsewhere"
    return cell


def render(reports: list[dict]) -> str:
    out = list(HEADER)
    for r in reports:
        out.append(f"| {r['date']} | {r['raised']} | {r['standing']} | "
                   f"{r['dismissed']} | [{r['name']}](./{r['name']}) |")
    out += ["", f"Reports: {len(reports)}"]
    return "\n".join(out) + "\n"


def orphan_rows(index_text: str, known: set[str]) -> list[str]:
    """Report filenames an existing index links to that are not on disk.

    [SECURITY:S1] The link text is hand-editable content that reaches the
    `orphan_rows` array on stdout, so it is bound-and-redacted on the way out.
    The membership test runs on the RAW text and the redaction applies only to
    what is emitted, so redaction can neither create nor mask an orphan. A
    legitimate `YYYY-MM-DD.md` name round-trips to exactly itself.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in index_text.splitlines():
        m = INDEX_ROW_REPORT.match(line)
        if m and m.group(1) not in known and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(redact(m.group(1), quoted=False))
    return out


def build(root: Path, today: datetime.date | None = None) -> tuple[Path | None, str, list[str], Path]:
    """The index path, its wanted body, any orphan rows, and the tree dir.

    A `None` path means the reviews surface is absent — not an error, and not
    a clean gate either. `main` turns it into the `surface_absent` discriminator.
    The tree dir comes back so `main` passes the SAME validated directory to
    `_atomic_write_text` that the reads were checked against, rather than
    recomputing it and risking the two disagreeing.
    """
    # The TREE dir's own containment is `bionic_config.load_config`'s job and
    # is not repeated here: it refuses a `docs_dir` resolving outside the repo
    # root on BOTH resolution paths (`.bionic.yml` and bare-directory
    # discovery), and `_tree_name` above raises before this line is reached.
    # What is left to this file is everything UNDER the tree dir, which
    # nothing else checks.
    tree = root / _tree_name(root)
    tree_rel = Path(_tree_name(root)) / "adrs" / "reviews"
    reviews = root / tree_rel
    # [SECURITY:S5] The per-segment symlink walk, BEFORE any `is_dir()` or
    # `resolve()`. `Path.is_symlink()` is true for a DANGLING link while
    # `is_dir()` is false through one, so a dangling link at either segment
    # used to leave the run through the `surface_absent` branch below: exit 0,
    # a positive discriminator asserting there is no derived artifact, on a
    # checkout that had redirected the surface off the tree. The walk also
    # NAMES the segment it refused; `_contained` alone reported "reviews
    # directory" when `adrs` was the link. Shape follows the dormancy-surface
    # repair in adr-signals.py.
    #
    # LANE: environment (exit 2), beside `_contained` and for its reason. A
    # symlinked directory segment is a fact about the CHECKOUT, not a
    # hand-edited document a human repairs by fixing a report — `check-drift`
    # reads it as CRASH, which is the verdict that sends a human to the
    # filesystem rather than to a report.
    for segment, subject in ((tree / "adrs", "adrs directory"),
                             (reviews, "reviews directory")):
        if segment.is_symlink():
            raise OSError(
                f"refusing to read {segment}: the {subject} is a symlink — the "
                "reviews grammar admits only real directories under the tree, "
                "and following one would read or write outside it. A dangling "
                "link is refused on the same terms rather than read as an "
                "absent surface")
    if not reviews.is_dir():
        return None, "", [], tree
    _contained(reviews, tree, "reviews directory")
    reports = collect(reviews, tree_rel, today)
    # Standing is a CORPUS property: every report is parsed before any row is
    # built, because a resolution written on one date moves the standing of a
    # finding another date raised. Nothing is written until this returns, so
    # no corpus that later refuses can leave a partial index behind.
    parsed = [row["parsed"] for row in reports]
    try:
        # The cross-date half of `a-blocked-part-is-counted-in-its-own-right`.
        # `validate_structure` already checked the single-report half as each
        # report was read; this one needs the whole corpus in date order, so it
        # cannot run until every report is parsed.
        review_findings.check_part_carryover(parsed)
        standing = review_findings.standing_by_finding(
            parsed, locator_exists=_locator_probe(root, str(tree_rel), tree))
        for row in reports:
            counts = review_findings.report_standing(row["parsed"], standing)
            row["raised"] = raised_cell(row["parsed"])
            row["standing"] = standing_cell(
                row["parsed"], counts,
                review_findings.events_elsewhere(row["parsed"], parsed))
    except review_findings.ReviewFindingsError as exc:
        # A corpus refusal is about the DIRECTORY: it names two reports, and
        # neither one of them is the offender on its own. `_locator_probe`
        # raises `ReviewFindingsError` too, and arrives here through the same
        # branch — `standing_by_finding` re-raises it carrying the record's
        # line and report date, which the probe itself cannot know.
        raise ReviewsError(str(tree_rel), exc.problem) from exc
    index = reviews / "index.md"
    have = _read_untranslated(index) if index.is_file() else ""
    orphans = orphan_rows(have, {r["name"] for r in reports})
    return index, render(reports), orphans, tree


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate adrs/reviews/index.md from the dated review reports.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--today", default=None,
                    help="ISO date the future-date check reads; default: the system date")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    today: datetime.date | None = None
    if args.today is not None:
        try:
            today = datetime.date.fromisoformat(args.today)
        except ValueError:
            sys.stderr.write("generate-reviews-index: --today must be an ISO calendar date "
                             "(YYYY-MM-DD)\n")
            return 2
    try:
        # INSIDE the try. `_tree_name` reads `.bionic.yml` and raises
        # `BionicConfigError` on a malformed one or on a `docs_dir` that
        # resolves outside the repo root. Outside the try it escaped as an
        # uncaught traceback — exit 1 with EMPTY stdout, which reads to
        # `check-drift` as neither the documented drift lane (exit 1 with JSON)
        # nor the documented crash lane (exit 2). Both belong in the
        # environment lane below.
        rel_index = str(Path(_tree_name(root)) / "adrs" / "reviews" / "index.md")
        path, want, orphans, tree_dir = build(root, today)
    except ReviewsError as exc:
        # A hand-edited report is a DOCUMENT finding, not an environment
        # problem: exit 1 with `validation_errors` on stdout, which
        # `check-drift` classifies BROKEN. Exit 2 stays reserved for a
        # genuine environment failure, which it reads as CRASH.
        payload = {"path": rel_index,
                   "validation_errors": [{"file": exc.file, "error": exc.problem}]}
        if args.dry_run:
            payload |= {"drift": False, "orphan_rows": []}
        else:
            payload |= {"written": None}
        print(json.dumps(payload, sort_keys=True))
        sys.stderr.write(f"generate-reviews-index: {exc}\n")
        return 1
    except Exception as exc:  # unreadable file, bad encoding — still an env error
        # [SECURITY:S1] The composed message is redacted at MESSAGE_LIMIT
        # rather than LIMIT: its untrusted parts were already bounded where
        # they were interpolated, and the value bound would cut a real
        # two-sentence containment refusal in half. What is still wanted here
        # is the control-character replacement, so an unrouted path cannot
        # forge a line on stderr.
        sys.stderr.write(
            f"generate-reviews-index: {type(exc).__name__}: "
            f"{redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2
    if path is None:
        rel = str(Path(_tree_name(root)) / "adrs" / "reviews" / "index.md")
        payload = {"path": rel, "surface_absent": True}
        if args.dry_run:
            payload |= {"drift": False, "orphan_rows": []}
        else:
            payload |= {"written": None}
        print(json.dumps(payload, sort_keys=True))
        return 0
    rel = str(path.relative_to(root))
    have = _read_untranslated(path) if path.exists() else ""
    if args.dry_run:
        drift = have != want or bool(orphans)
        print(json.dumps({"drift": drift, "path": rel, "orphan_rows": orphans},
                         sort_keys=True))
        return 1 if drift else 0
    try:
        _atomic_write_text(path, want, contained_under=tree_dir)
    except OSError as exc:
        sys.stderr.write(
            f"generate-reviews-index: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2
    print(json.dumps({"written": rel}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
