#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""adr-signals.py — the eight mechanical signals of the periodic decision review.

The script computes; it grades nothing. It emits eight verdict envelopes and no
severity and no recommendation. The architect makes the judgment against the
objectives; this script only says what was measured and what could not be.

WHAT "NO MINED CONTENT" MEANS HERE, STATED EXACTLY. No prose is mined: no
journal sentence, no run-snapshot note, no ADR body line, and no doctrine rule
sentence reaches the envelope. What the envelope carries beyond counts and
dates is IDENTIFIERS — the ADR ids that key the three ADR-keyed maps, the
`EXEMPT*` variable names and script filenames named in `carve_out_count`'s
basis, and the tree-relative paths in every `basis`. Two of those three are
bounded by the grammar that produced them (`\\bADR-\\d{4}\\b` for a mention,
`EXEMPT[A-Za-z0-9_]*` for a literal); the map KEYS are not, because they are
the verbatim `id:` scalar of an ADR's frontmatter and this script reads that
scalar with `re` rather than validating it. A hostile `id:` therefore reaches
the envelope at its own length. That is the residue of this claim, named here
rather than left for a reader to discover. It is also why every value reaching
STDERR is routed through `untrusted.redact` instead: a forged line there
changes what a human reads.

The CHANGELOG version string is the FOURTH class of value the envelope carries,
and the one whose grammar bounds nothing: the heading pattern captures any run
of non-bracket characters. `_dated_release_headings` hands the RAW capture back
and EACH CONSUMER RENDERS IT, because rendering at entry collapsed two distinct
versions onto one string: `redact(v, quoted=False)` appends its note to the
value, so a heading spelling that note verbatim became byte-identical to a
hostile heading the redaction had just annotated, and one release's
`prep_commits` count silently overwrote another's. The render is
`redact(v, quoted=True)`, which puts the note OUTSIDE the quotes and is what
keeps the two apart; it happens at the `prep_commits` key and in every
`schema_growth` baseline sentence. Two consumers take the RAW value and must:
the match against a commit subject, which is `re.escape`d and is the only form
that matches a real `1.2.3` heading, and the tag lookup, whose bound is the
ARGUMENT GRAMMAR — and `baseline.ref` therefore carries that bound rather than
`redact`'s.

DEPENDENCY FLOOR, AND THE READ SURFACE. Stdlib only, `dependencies = []`,
`requires-python = ">=3.11"`. It imports neither `summaries_projection.py` nor
`doctrine_projection.py`: both declare `requires-python = ">=3.13"` plus
PyYAML, and importing either would force that floor onto this script. It
imports no `yaml` itself. THREE sibling modules load, by TWO mechanisms and
not one. This file inserts its own directory at the head of `sys.path` and
imports `untrusted.py` from there. It loads `bionic_config.py` by explicit file
path instead, through `importlib.util.spec_from_file_location` in
`_load_bionic_config`. `bionic_config.py` in turn reaches `_yaml_min.py` and
`untrusted.py` by plain import when the script directory is already on
`sys.path`, and falls back to the same explicit file-path load when it is not.
All three run without PyYAML — `_yaml_min.py` takes a PyYAML fast path when one
is installed and falls back to its own stdlib reader when none is — and
`untrusted.py` carries no PEP 723 header of its own. The small amount of
frontmatter this file needs — a two-key read of the tree manifest, and `id` /
`amends` / `supersedes` /
`status` out of ADR frontmatter — is parsed with `re`. No network access, and
it writes nothing.

The read surface is the committed in-repo artifacts under the repository root
it was given PLUS that repository's own history, read through a subprocess —
that is rule:signal-script-read-surface. A root-level name that RESOLVES
outside that root is not one of those artifacts: `_read_contained` refuses it,
and the signal reading it reports `unmeasurable` with the refusal in its
`filter`. That helper refuses on FOUR conditions and not one, and every
refusal sentence in this file names the disjunction rather than asserting the
containment leg alone. Stdlib-only is unchanged, because
`subprocess` is stdlib; a shell string is never used, and every invocation is
an argument LIST. Four requirements ride with the widened surface, and all four
live in the substrate section below: containment (the work tree resolved must
BE the given root, which also bounds the discovery walk), the environment
allowlist enumerated by name (`PATH` inherited, `LC_ALL` set to `C`,
`GIT_CONFIG_NOSYSTEM` set to `1`, and on Windows `SYSTEMROOT` inherited — no
other name, and no `GIT_`-prefixed name inherited), the argument grammar every
repo-supplied value must match before it reaches an argument after
`--end-of-options`, and the leg-scoped failure verdict. That last one decides
by LEG rather than by signal: a signal whose measurability rests on an affected
leg reports `unmeasurable` and never a zero, and every other signal keeps its
`computed` verdict and nulls only the members that leg fills. Which of the three
delivery signals sits in which class is now stated per signal — see
rule:span-and-prep-are-different-counts for release_cadence, whose git
dependence is per MEMBER; rule:baseline-is-a-release-mark-and-unavailable-is-named
for schema_growth, whose measurability rests on the git session; and
rule:gate-count-reads-the-roster-header-row for gate_count, which reads no git.

NOT A REGENERATOR, AND THE NAME IS PART OF THAT. This script derives no
artifact: it writes no file, and its output is an envelope on stdout that
nothing on disk is compared against. Every `crux/scripts/generate-*.py` is a
regenerator, and `RegeneratorEnrollmentTests` in
`tools/tests/test_schema_invariants.py` globs exactly that name and fails on
one missing a row in the `AGENTS.md` roster table. Renaming this file to
`generate-adr-signals.py` would therefore redden that gate, and the repair is
not to add a roster row — there is nothing to regenerate and nothing to
drift-check. `generate-reviews-index.py`, the review's OTHER script, is the
regenerator and does carry a roster row.

THE ENVELOPE. With `--json`, stdout is one object:

    {"active_adrs": <int>, "signals": [<eight records>]}

Each record carries EXACTLY the five members `signal`, `verdict`, `value`,
`basis`, `filter`, all present on every record whatever the verdict. `verdict`
is "computed" or "unmeasurable"; `value` is null when unmeasurable; `basis`
names the file(s) read; `filter` names the filter applied and is null when none
was. No record carries a `severity` field and no record carries a
`recommendation` field, now or later — that is rule:signal-verdict-envelope-shape.

`filter` IS ONE SLOT HOLDING FIVE SHAPES, and a reader who expects only the
narrow reading ("which rows were dropped") will misread six of the eight
records. The slot holds the qualification `value` cannot be read correctly
without, and across the eight signals that is:

  1. AN EXCLUSION — which inputs were dropped before counting.
     `amendment_fan_in` (archived ADRs) and `dormancy_days` (bulk entries,
     with the count excluded on this run) are the narrow reading.
  2. A DEDUPLICATION — which inputs were counted once rather than twice.
     `carve_out_count`, where the manifest key and its doctrine projection are
     one surface.
  3. A VALUE-DOMAIN NOTE — what a member of `value` means. `paper_only`, where
     a per-ADR `null` is a third state and not a falsy `false` — that per-ADR
     shape applies only once the doctrine index HAS been read; `schema_growth`,
     where a null member means the path was absent at a ref that did resolve;
     and `gate_count`, where an empty roster the header row located reports 0.
  4. THE REASON A MEASUREMENT WAS IMPOSSIBLE — mandatory on every
     `unmeasurable` record, which is the one verdict where `filter` may not be
     null. `friction_citations` when the tree records no adoption date, or
     when the queried window lies wholly before the one it records; `paper_only`
     WHOLE-RECORD (not the per-ADR null above) when the doctrine index has
     never been compiled, was not read, carries no rule table, or carries a
     line its reader cannot account for; the section on `paper_only` below
     states each shape exactly.
  5. WHICH MARKER WAS CHOSEN, and what each rejected marker's coverage measured
     on this run. `release_cadence` alone, which names the chosen marker (the
     changelog's dated version headings) and both rejected ones (tags, and the
     release-prep commit-subject prefix). A marker covering one release in
     forty is a number indistinguishable from its own absence, so the coverage
     is measured on the run rather than asserted in prose.

All five are "the filter applied" in the envelope contract's sense. The five
names above are for the reader, not for the JSON: no record declares which
shape it carries, and none should be added.

`active_adrs` sits at the envelope level rather than inside a record, so the
record contract stays exactly five members across all eight records. THREE of
the eight signals are ADR-keyed — `amendment_fan_in`, `paper_only` and
`dormancy_days` — and each of those mappings carries exactly `active_adrs`
keys. `release_cadence` and `schema_growth` also carry mapping values, and
neither is ADR-keyed, so a reader selecting the ADR-keyed set must select it by
NAME rather than by value shape.

USAGE.
  adr-signals.py --repo-root DIR              human-readable table
  adr-signals.py --repo-root DIR --json       the JSON envelope
  adr-signals.py --repo-root DIR --today D    pin the reference date

`--today YYYY-MM-DD` is the reference date `dormancy_days` measures against and
defaults to the system date. It exists so the tests are deterministic: a
dormancy measured against a moving clock cannot be asserted.

EXIT LANES.
  0 — the envelope was emitted and every signal record is well-formed. A verdict
      of `unmeasurable` is a reported measurement state, not a failure: the
      script grades nothing, so it does not change the exit code.
  1 — the envelope could not be emitted in full because a required input was
      malformed, refused by the containment read, or inconsistent with its
      siblings, and the script refuses to guess around it. Partial JSON with
      an `errors` array on stdout.
  2 — environment error: `--repo-root` absent, the resolved root is not a
      crux repo root (no `<docs_dir>/adrs` directory), or `.bionic.yml` is
      malformed / names a `docs_dir` that resolves outside the repo root. A
      `--today` that is not an ISO calendar date joins the same lane through
      this script's own check, which returns 2 to match argparse's usage-error
      code. Message on stderr, empty stdout.

An absent `CHANGELOG.md`, an absent repo-root `AGENTS.md`, an absent
version-control binary, and a NEVER-COMPILED doctrine index are `unmeasurable`
verdicts at exit 0. None of the four is an exit-1 error and none is an exit-2
environment failure: they are the reported measurement states of
`release_cadence`, `gate_count`, `schema_growth` and `paper_only` respectively.
A fresh `init-docs` tree has never run `compile-doctrine`. It carries neither
`adrs/doctrine/index.md` nor `adrs/doctrine/_meta.json`, so its `paper_only`
record is `unmeasurable` by this rule rather than by a defect.

WHICH ABSENT INPUT FORCES `paper_only` UNMEASURABLE, STATED EXACTLY. Three
absent inputs do (A, B and D), and one malformed input does (C):

  A. The index was never compiled: `unmeasurable` at exit 0, in one of the
     two shapes stated below.
  B. The index was not read, because the guarded read below refused it:
     `unmeasurable`, plus an `errors` row, at exit 1.
  C. The index was read and carries a line its reader cannot account for —
     a line naming an ADR rule, or carrying a basis value as a cell, that is
     not a rule-table row, a reconciliation row or an exempt bullet; a
     malformed table line; or a line that opens a fence or an HTML comment.
     This is the malformed-input case: `unmeasurable`,
     with one `errors` row per such line, at exit 1. `read_doctrine_index`
     states the accounting rule in full.
  D. The index was read and carries no rule table: `unmeasurable` at exit
     0. A rule table's header names `basis`, or names `handle` with a first
     cell other than `observation` or `invariant`. That header is this
     signal's input, as the roster header row is `gate_count`'s.

A read index with at least one rule table and no such line is `computed`.
A rule row that names its ADR and cannot be read — a cell count unlike its
header's, or a basis outside the four values — adds an `errors` row (exit 1)
and nulls that ADR whatever its other rows say; every other ADR is computed.

The never-compiled check uses `os.lstat`, which does not follow a symlink,
and it fails closed. Exactly two shapes are never-compiled:

  1. `os.lstat` raises `FileNotFoundError` for `adrs/doctrine`: the doctrine
     directory does not exist.
  2. `adrs/doctrine` is a real directory, not a symlink, and `os.lstat` raises
     `FileNotFoundError` for both `adrs/doctrine/index.md` and
     `adrs/doctrine/_meta.json`.

Every other shape takes the guarded read of `adrs/doctrine/index.md` through
`_read_contained`. That includes any symlink at `adrs/doctrine`, whether live,
escaping, dangling or looping; a regular file at `adrs/doctrine`; any other
`OSError` from `os.lstat` on any of the three paths; any entry at either leaf,
including a dangling symlink; and a `_meta.json` without an `index.md`. The
guarded read either returns the index text, which is then parsed, or refuses.
A refusal is shape B: it exits 1 with an `errors` row whose `input` names
adrs/doctrine/index.md. An `index.md` that reads cleanly is read whether or
not `_meta.json` exists. `carve_out_count` stays `computed` on a never-compiled
tree, because it reads `adr.governs_exempt` from the tree manifest. Its
`basis` then names the roster as unread and does not list it as a source.

`friction_citations`' journal leg counts `Friction:` lines, never `### `
headings, and only over entries dated at or after the adoption date recorded
at `journal.friction_line_from` in `<docs_dir>/manifest.yml` — that is
rule:friction-signal-counts-friction-lines. A tree that records no adoption
date, or whose queried window lies wholly before it, reads `unmeasurable`
rather than a count. Both entry scanners — that leg and `_dated_entries` —
track fences through `_fenced_entries`: a line inside a fenced block is
content, never an entry heading and never a `Friction:` line.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from md_fences import closes_fence as _closes_fence, \
    fence_marker as _fence_marker, \
    split_lines as _lines  # noqa: E402
from untrusted import LIMIT, MESSAGE_LIMIT, redact  # noqa: E402

# An entry naming this many or more distinct ADR ids is a roster, not a mention.
# Measured over bionic/log.md: 1371 entries, 713 naming at least one ADR id —
# 656 name one to three, 57 name four or more, and the tail above three is flat.
BULK_ENTRY_THRESHOLD = 4

ADR_ID = re.compile(r"\bADR-\d{4}\b")
ADR_FILE = re.compile(r"^ADR-\d{4}-.*\.md$")
#: A doctrine rule handle as a whole cell; group 1 is the source ADR or OBS id.
DOCTRINE_HANDLE = re.compile(r"((?:ADR|OBS)-\d{4})/[a-z0-9][a-z0-9-]*")
#: An ADR-prefixed rule handle anywhere in a line: the identity of an ADR rule.
ADR_HANDLE_IN_LINE = re.compile(r"ADR-\d{4}/[a-z0-9][a-z0-9-]*")
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
#: One cell of a Markdown table's delimiter row.
SEPARATOR_CELL = re.compile(r":?-+:?")
DOCTRINE_EXEMPT_HEADING = re.compile(r"^##\s+Exempt ADRs\b")
EXEMPT_ROSTER_ROW = re.compile(r"^-\s+((?:[A-Z][A-Z0-9]{1,9}-)?ADR-\d{4})\b")
LOG_HEADING = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})[^\]]*\]")
FORGE_ENTRY = re.compile(r"^## \[[^\]]+\]\s+([a-z]+)\s+\|")
# One entry per runtime's project-local skills directory, in the order the
# runtime-compatibility block names them. The forge log lives at
# `<dir>/forge-log.md` in each.
LOCAL_SKILLS_DIRS = (".claude/skills", ".agents/skills", ".opencode/skills", ".opencode/skill")
BASIS_VALUES = {"run-bound", "not-run-bound", "evidence-resolves", "evidence-missing"}
NOT_RUN_BOUND = "not-run-bound"
#: The five statuses the doctrine compiler writes into a reconciliation row.
#: Restated rather than imported, because importing the compiler would raise
#: this script's dependency floor; a test holds the two spellings equal.
RECONCILIATION_STATUSES = frozenset(
    {"compatible", "reconciled", "collision", "un-adjudicated", "digest-stale"})


# --------------------------------------------------------------------------
# layout + small parsers
# --------------------------------------------------------------------------

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


def _read(path: Path) -> str:
    # `newline=""` DISABLES UNIVERSAL-NEWLINE TRANSLATION, and that is a
    # line-grammar decision, not an encoding detail. Python's text mode
    # rewrites a lone `\r` and a `\r\n` to `\n` BEFORE the caller sees the
    # text, so a `\r` spelled mid-sentence arrived as a real line boundary
    # that `split_lines` could not refuse — it was already gone. Measured:
    # a `\r` before a fence run forged a fence and turned a live parity
    # clause into an exit-0 P3 stale, exactly as U+000B did through the
    # split. Reading untranslated also makes `split_lines`' trailing-`\r`
    # strip REACHABLE: through `read_text` no `\r` ever survived to it, so
    # the branch that documents CRLF handling never ran.
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read()


def _read_contained(root: Path, path: Path) -> str | None:
    """The text of `path`, or None when it was not read as a contained artifact.

    Resolves the whole name — ancestors included, so a symlinked directory
    escapes the same way a symlinked leaf does — then opens the RESOLVED name
    once with `O_NOFOLLOW` and reads through that one descriptor. Checking a
    name and then re-opening it reads whatever the name points at by the time
    of the second lookup: 4,000 check-then-read pairs raced against a symlink
    flip served attacker content 411 times. One descriptor closes that window,
    and `O_NOFOLLOW` refuses a leaf swapped to a symlink after the resolve.
    Opening the RESOLVED name rather than the name as given is what carries the
    ancestor half of that claim: `O_NOFOLLOW` guards the LEAF alone, so an
    ancestor directory swapped to a symlink after the resolve is refused only
    because the open never traverses it.

    FOUR CONDITIONS RETURN None, AND EVERY CALLER'S PROSE NAMES THE
    DISJUNCTION, because a sentence naming one of them is false whenever
    another fired:

      1. the name did not RESOLVE — absent, or a symlink loop;
      2. it resolved OUTSIDE the given root;
      3. `O_NOFOLLOW` refused the resolved name, which is the post-resolve
         leaf swap above;
      4. the open or the read failed for any other `OSError` — a mode-000
         file, a directory, a device, a full descriptor table.

    A contained, readable-in-principle file at mode 000 takes lane 4, and the
    caller sentence that read "resolves outside that root" was simply wrong
    about it.

    BOTH sides are resolved before the containment comparison. A macOS
    temporary directory is handed back as a `/var/...` path that is itself a
    symlink to `/private/var/...`, so comparing an unresolved root against a
    resolved file refuses a file that really is inside the root.
    """
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        # `RuntimeError` is what `Path.resolve(strict=True)` raises on a
        # symlink loop under Python 3.11 and 3.12, both inside this file's
        # declared floor. `OSError` alone left that case unrefused.
        return None
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(str(resolved), flags)
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace",
                       newline="") as handle:   # see `_read` on newline=""
            fd = None  # ownership passes to the file object on success
            return handle.read()
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _frontmatter(text: str) -> str | None:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else None


def _fm_scalar(fm: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}\s*:\s*(.*)$", fm, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip("'\"") or None


def _fm_list(fm: str, key: str) -> list[str]:
    """An inline `key: [a, b]` list or a block `key:` / `- a` list."""
    lines = _lines(fm)
    for i, line in enumerate(lines):
        m = re.match(rf"^{re.escape(key)}\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("["):
            body = rest[1:rest.rindex("]")] if "]" in rest else rest[1:]
            return [t.strip().strip("'\"") for t in body.split(",") if t.strip()]
        if rest:
            return [rest.strip("'\"")]
        out = []
        for nxt in lines[i + 1:]:
            mm = re.match(r"^\s*-\s*(.+?)\s*$", nxt)
            if not mm:
                break
            out.append(mm.group(1).strip("'\""))
        return out
    return []


def _yaml_top_value(text: str, key: str) -> str:
    """The value of a top-level YAML key: inline scalar, or a block scalar body.

    Enough to answer "is this key non-empty", which is all this script asks of
    a run snapshot's `notes`.
    """
    lines = _lines(text)
    for i, line in enumerate(lines):
        m = re.match(rf"^{re.escape(key)}\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest and rest[0] not in "|>":
            return rest.strip("'\"")
        body = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt.startswith((" ", "\t")):
                break
            body.append(nxt.strip())
        return "\n".join(body).strip()
    return ""


# --------------------------------------------------------------------------
# input readers
# --------------------------------------------------------------------------

def read_active_adrs(root: Path, adrs_dir: Path, errors: list[dict]) -> dict[str, dict]:
    """Top-level `<docs_dir>/adrs/ADR-*.md` only; `archive/` excluded.

    A file `_read_contained` refuses — it did not resolve, resolves outside
    `root`, is a symlink `O_NOFOLLOW` refused, or would not open — is not one
    of the declared artifacts and is skipped rather than reported: this is a
    directory walk, and a refusal here is a narrower read of the corpus, not a
    malformed one.
    """
    out: dict[str, dict] = {}
    for path in sorted(adrs_dir.iterdir()):
        if not path.is_file() or not ADR_FILE.match(path.name):
            continue
        text = _read_contained(root, path)
        if text is None:
            continue
        fm = _frontmatter(text)
        if fm is None:
            errors.append({"input": redact(path.name, quoted=False),
                           "problem": "ADR file carries no YAML frontmatter block"})
            continue
        adr_id = _fm_scalar(fm, "id")
        if not adr_id:
            errors.append({"input": redact(path.name, quoted=False),
                           "problem": "ADR frontmatter carries no `id`"})
            continue
        out[adr_id] = {
            "path": path,
            "status": _fm_scalar(fm, "status"),
            "amends": _fm_list(fm, "amends"),
            "supersedes": _fm_list(fm, "supersedes"),
        }
    return out


def _table_cells(line: str) -> list[str] | None:
    """The cells of one Markdown table line, or None when it is not one.

    THE ONE CELL SPLIT both table readers use. A table line starts with `|`
    and, once trailing whitespace is stripped, ends with an unescaped `|`. It
    is split on unescaped pipes, the empty first and last parts are dropped,
    and each cell is unescaped (`\\|` to `|`) and stripped. The doctrine
    compiler escapes a literal pipe inside a cell as `\\|`, so a split on
    every pipe would read one cell too many.
    """
    s = line.rstrip()
    if len(s) < 2 or not s.startswith("|") or not s.endswith("|") or s.endswith("\\|"):
        return None
    return [c.strip().replace("\\|", "|") for c in UNESCAPED_PIPE.split(s)][1:-1]


def _leading_cells(line: str) -> list[str]:
    """The cells of a line that fails `_table_cells`, dropping only the first part."""
    return [c.strip().replace("\\|", "|") for c in UNESCAPED_PIPE.split(line.rstrip())][1:]


def _separator_shaped(line: str) -> bool:
    """Whether a line reads as a Markdown table's delimiter row.

    The leading and trailing pipes are both optional, as a GFM delimiter row
    allows: the stripped line loses one leading `|` and one trailing unescaped
    `|`, and every cell between unescaped pipes must match `:?-+:?`.
    """
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    if not s:
        return False
    return all(SEPARATOR_CELL.fullmatch(c.strip()) for c in UNESCAPED_PIPE.split(s))


def _doctrine_header(lines: list[str], k: int) -> list[str] | None:
    """The header cells when line `k` opens a doctrine table, else None.

    A header is a table line whose next line is a table line of the same cell
    count with every cell matching `:?-+:?`.
    """
    if k + 1 >= len(lines):
        return None
    header, sep = _table_cells(lines[k]), _table_cells(lines[k + 1])
    if not header or sep is None or len(sep) != len(header):
        return None
    if not all(SEPARATOR_CELL.fullmatch(c) for c in sep):
        return None
    return header


class DoctrineRead(NamedTuple):
    """What `read_doctrine_index` read, and what it could not account for."""

    #: Basis values keyed by the source ADR or OBS id, one per readable rule row.
    rows: dict[str, list[str]]
    #: The `## Exempt ADRs` roster.
    exempt: list[str]
    #: Ids with a rule row that names them and cannot be read. They read null.
    nulled: frozenset[str]
    #: `(line number, cause)` for each line the reader cannot account for.
    failures: list[tuple[int, str]]
    #: Rule tables found, and the body rows under them.
    rule_tables: int
    rule_rows: int


def read_doctrine_index(text: str, name: str, errors: list[dict]) -> DoctrineRead:
    """Read the doctrine index's rule tables by column name.

    `text` is already read through `_read_contained` by the caller, and `name`
    is the file's basename, used only in error messages below.

    THE READER KEYS ON HEADER NAMES, NEVER ON A FIXED CELL COUNT OR A FIXED
    POSITION. It
    once kept a row only when the row split into exactly eight parts and read
    `basis` from one fixed position. The compiler then added a
    `source_status` column, every rule row split into nine parts, and the
    reader kept none of them. An empty row set raised no error, so
    `paper_only` reported `computed` over 133 nulls.

    Tables. A header is a table line (`_table_cells`) whose next line is a
    delimiter row with the same cell count. A header always ends the open
    table and starts a new one. A body runs from the line after the delimiter
    to the first line that does not start with `|` or is itself a header. Each
    header is classified in this order: any cell `basis` makes a RULE table;
    else a first cell `observation` or `invariant` makes an EXCLUDED table;
    else any cell `handle` makes a RULE table; else it is an OTHER table. A
    rule table must name `handle` once and `basis` once.

    Rule rows. Each body row of a rule table yields a basis for its ADR, or
    nulls its ADR with one `errors` row when its handle cell is valid but its
    cell count differs from the header's or its basis is not one of the four
    values. A nulled ADR stays null whatever its other rows say. A row whose
    handle cell fails the handle grammar names no ADR, and is a failure.

    The accounting invariant. A line carries an ADR rule's identity when it
    holds an ADR-prefixed handle anywhere, or when it is a `|` line holding a
    basis value as a whole cell. Every such line is consumed exactly once: as
    a rule-table body row; as a reconciliation row, which sits under an
    `invariant` table naming `handle` once and `status` once, carries an ADR
    handle in its `handle` cell and nowhere else, and one of the five
    statuses in its `status` cell; or, for the handle half only, as a `- `
    line of the `## Exempt ADRs` section. Any other such line is a failure.
    So is a malformed rule header, a row of an excluded or other table whose
    cell count differs from its header's, a rule handle in a cell of an other
    table, and any `|` line outside every table or inside the exempt section.
    So is a line that opens a fence or an HTML comment: the reader does not
    track either block, so a table inside one would read as a real table.
    Each failing line gets one `errors` row naming the first cause found,
    and `paper_only` then reads `unmeasurable` rather than computing any ADR
    from a partial read.
    """
    lines = _lines(text)
    label = redact(name, quoted=False)
    rows: dict[str, list[str]] = {}
    nulled: set[str] = set()
    failures: dict[int, str] = {}
    exempt: list[str] = []
    consumed: set[int] = set()
    rule_body: set[int] = set()
    rule_tables = rule_rows = 0

    def fail(k: int, cause: str) -> None:
        failures.setdefault(k, cause)

    def null(adr: str, k: int, problem: str) -> None:
        nulled.add(adr)
        errors.append({"input": label,
                       "problem": f"line {k + 1}: rule row for "
                                  f"{redact(adr, quoted=False)} {problem}; "
                                  f"paper_only reads that ADR as null"})

    in_exempt = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _fence_marker(line) is not None or line.lstrip().startswith("<!--"):
            fail(i, "a fence or HTML comment, which this reader does not parse")
            i += 1
            continue
        if line.startswith("## "):
            in_exempt = bool(DOCTRINE_EXEMPT_HEADING.match(line))
            i += 1
            continue
        if not line.startswith("|"):
            if in_exempt:
                m = EXEMPT_ROSTER_ROW.match(line)
                if m:
                    exempt.append(m.group(1))
                if line.startswith("- "):
                    consumed.add(i)
            i += 1
            continue
        if in_exempt:
            fail(i, "a table line inside the Exempt ADRs section")
            i += 1
            continue
        header = _doctrine_header(lines, i)
        if header is None:
            fail(i, "a table line outside any table")
            i += 1
            continue
        if "basis" in header:
            kind = "rule"
        elif header[0] in ("observation", "invariant"):
            kind = "excluded"
        elif "handle" in header:
            kind = "rule"
        else:
            kind = "other"
        handle_at = basis_at = None
        if kind == "rule":
            rule_tables += 1
            if header.count("handle") != 1 or header.count("basis") != 1:
                fail(i, "a rule-table header that does not name `handle` once "
                        "and `basis` once")
            else:
                handle_at, basis_at = header.index("handle"), header.index("basis")
        reconciliation = (kind == "excluded" and header[0] == "invariant"
                          and header.count("handle") == 1 and header.count("status") == 1)
        j = i + 2
        while j < len(lines) and lines[j].startswith("|") and _doctrine_header(lines, j) is None:
            cells = _table_cells(lines[j])
            if kind == "rule":
                rule_rows += 1
                consumed.add(j)
                rule_body.add(j)
                if handle_at is not None:
                    read = cells if cells is not None else _leading_cells(lines[j])
                    m = (DOCTRINE_HANDLE.fullmatch(read[handle_at])
                         if len(read) > handle_at else None)
                    if m is None:
                        fail(j, "a rule-table row whose handle cell names no rule")
                    elif cells is None:
                        null(m.group(1), j, "does not end with an unescaped pipe")
                    elif len(cells) != len(header):
                        null(m.group(1), j,
                             f"carries {len(cells)} cells where its header carries "
                             f"{len(header)}")
                    elif cells[basis_at] not in BASIS_VALUES:
                        null(m.group(1), j,
                             f"carries an unknown basis value in cell {basis_at + 1}, "
                             f"{len(cells[basis_at])} characters long (its content "
                             f"is not quoted here)")
                    else:
                        rows.setdefault(m.group(1), []).append(cells[basis_at])
            elif cells is None or len(cells) != len(header):
                fail(j, f"a row of an {kind} table whose cells do not match its header")
            elif kind == "other" and any(DOCTRINE_HANDLE.fullmatch(c) for c in cells):
                fail(j, "a rule handle in a table that is not a rule table")
            elif reconciliation:
                at = header.index("handle")
                if (ADR_HANDLE_IN_LINE.fullmatch(cells[at])
                        and cells[header.index("status")] in RECONCILIATION_STATUSES
                        and not any(ADR_HANDLE_IN_LINE.search(c)
                                    for n, c in enumerate(cells) if n != at)):
                    consumed.add(j)
            j += 1
        i = j

    for k, line in enumerate(lines):
        if k not in consumed and ADR_HANDLE_IN_LINE.search(line):
            fail(k, "an ADR rule handle on a line that is not a rule-table row, "
                    "a reconciliation row or an exempt bullet")
        if k not in rule_body and line.startswith("|"):
            cells = _table_cells(line)
            if any(c in BASIS_VALUES for c in (cells if cells is not None
                                               else _leading_cells(line))):
                fail(k, "a basis value in a cell outside every rule-table row")

    ordered = sorted(failures.items())
    for k, cause in ordered:
        errors.append({"input": label,
                       "problem": f"line {k + 1}: {cause}; paper_only cannot "
                                  f"account for this line and reads unmeasurable"})
    return DoctrineRead(rows, exempt, frozenset(nulled),
                        [(k + 1, cause) for k, cause in ordered], rule_tables, rule_rows)


#: The `adr:` key of a `{adr: ADR-NNNN, reason: "..."}` exemption member.
ADR_KEY = re.compile(r"\badr\s*:\s*(ADR-\d{4})\b")


def _split_flow_members(body: str) -> list[str]:
    """Split a YAML flow-sequence body on TOP-LEVEL commas only.

    A `{adr: ..., reason: ...}` mapping member carries its own comma, so
    splitting on every comma made the reason half a member of its own — and a
    reason that cites an ADR then read as a second exemption and inflated
    `carve_out_count`. Depth counts brackets and braces; a quoted run is
    passed through whole, so a comma inside a reason string is not a split
    point either.
    """
    members: list[str] = []
    current = ""
    depth = 0
    quote = ""
    for ch in body:
        if quote:
            current += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 0:
            members.append(current)
            current = ""
            continue
        current += ch
    members.append(current)
    return [m.strip() for m in members if m.strip()]


def read_governs_exempt(text: str) -> list[str]:
    """A two-key stdlib read of `adr.governs_exempt` — no YAML parser.

    `text` is the tree manifest's content, already read through
    `_read_contained` by the caller.

    The key admits two member shapes (`docs/AGENTS.md` §7): a bare `ADR-NNNN`
    string and a mapping `{adr: ADR-NNNN, reason: "..."}`, in flow or block
    form. The block key admits a trailing `#` comment, for the same reason
    `read_journal_friction_from`'s does: the shipped template comments every
    column-0 block key, and a commented `adr:` never opened the block, so a
    recorded exemption read as absent and the carve-out count silently fell to
    zero. This reader returns the ids only, in order, deduplicated — the
    carve-out signal counts exemptions and never reads a reason. A member
    carrying no ADR id is dropped rather than returned as a fragment: a
    comma-split flow mapping would otherwise yield `reason: "..."}` as an
    "exemption" and inflate the count.
    """
    lines = _lines(text)
    in_adr = False
    raw: list[str] = []
    for i, line in enumerate(lines):
        if re.match(r"^adr\s*:\s*(#.*)?$", line):
            in_adr = True
            continue
        if in_adr and line[:1].strip():
            break
        if not in_adr:
            continue
        m = re.match(r"^\s+governs_exempt\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("["):
            body = rest[1:rest.rindex("]")] if "]" in rest else rest[1:]
            raw = _split_flow_members(body)
        elif rest:
            raw = [] if rest in ("~", "null") else [rest]
        else:
            dash_indent = None
            for nxt in lines[i + 1:]:
                mm = re.match(r"^(\s+)-\s*(.+?)\s*$", nxt)
                if mm and (dash_indent is None or len(mm.group(1)) == dash_indent):
                    dash_indent = len(mm.group(1))
                    raw.append(mm.group(2))
                    continue
                stripped = nxt.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if dash_indent is not None and len(nxt) - len(nxt.lstrip()) > dash_indent:
                    continue  # a continuation line of a mapping member (`reason: ...`)
                break
        break
    out: list[str] = []
    for entry in raw:
        # The `adr:` key decides a mapping member's id. Falling straight to
        # `ADR_ID.search` took the FIRST id in the member, which is the
        # reason's when the reason is written first.
        keyed = ADR_KEY.search(entry)
        found = keyed.group(1) if keyed else None
        if found is None:
            bare = ADR_ID.search(entry)
            found = bare.group(0) if bare else None
        if found and found not in out:
            out.append(found)
    return out


def read_journal_friction_from(text: str) -> str | None:
    """A two-key stdlib read of `journal.friction_line_from` — no YAML parser.

    `text` is the tree manifest's content, already read through
    `_read_contained` by the caller.

    Walks to the column-0 `journal:` block, stops at the next column-0
    non-blank line, and matches the indented `friction_line_from` scalar
    within it. The block key admits a trailing `#` comment, because the
    shipped template comments every block key and a commented `journal:`
    otherwise never opened the block — the adoption date then read as absent
    and every friction reader went `unmeasurable` against a manifest that
    records one. Strips a trailing `#` comment and surrounding quotes. Returns
    the value only when it matches `^\\d{4}-\\d{2}-\\d{2}$`; an absent key, an
    absent block, `null`/`~`, or any non-ISO value all return `None` — the
    friction-citations signal reads that as "no adoption date recorded", not
    as an error.
    """
    lines = _lines(text)
    in_journal = False
    for line in lines:
        if re.match(r"^journal\s*:\s*(#.*)?$", line):
            in_journal = True
            continue
        if in_journal and line[:1].strip():
            break
        if not in_journal:
            continue
        m = re.match(r"^\s+friction_line_from\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).split("#", 1)[0].strip().strip("'\"")
        if rest in ("", "~", "null"):
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", rest):
            return rest
        return None
    return None


def read_exempt_literals(root: Path, scripts_dir: Path) -> list[tuple[str, str, list[str]]]:
    """Module-level `EXEMPT*` set/frozenset/tuple/list literals under a scripts dir.

    `ast` rather than `re`: the shipped instance is `frozenset({...})`, a call
    wrapping a set literal, which a regex reads as a set literal only by luck.

    A file `_read_contained` refuses — for any of its four reasons, of which
    resolving outside `root` is one — is skipped, not reported: this is a
    directory walk over a declared surface, not a single named input.
    """
    found: list[tuple[str, str, list[str]]] = []
    for path in sorted(scripts_dir.glob("*.py")):
        text = _read_contained(root, path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            else:
                continue
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                    and value.func.id in {"frozenset", "set", "tuple", "list"} \
                    and len(value.args) == 1:
                value = value.args[0]
            if not isinstance(value, (ast.Set, ast.List, ast.Tuple)):
                continue
            try:
                members = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                continue
            entries = sorted({m for m in members if isinstance(m, str)})
            for name in names:
                if name.startswith("EXEMPT") and entries:
                    found.append((path.name, name, entries))
    return found


#: A dated entry heading, the split point both entry scanners share.
ENTRY_HEADING = re.compile(r"^## \[\d{4}-\d{2}-\d{2}[^\]]*\].*$")

# The CommonMark fenced-code-block subset — the fence characters, the run
# length, the 0-3 column indent bound with tabs expanded at four, the
# info-string backtick rule and the closer's run length — lives in ONE place,
# `crux/scripts/md_fences.py`, and is imported at the head of this file as
# `_fence_marker` and `_closes_fence`. It was two hand-copied functions, here
# and in `check_template_parity.py`, and the copies carried the SAME defect:
# the indent was measured on spaces while the run was matched after stripping
# ALL Unicode whitespace, so one U+2028 in front of a four-space indent forged
# a phantom fence. A differential test between the two copies could not find
# it, because both copies were wrong the same way. `md_fences` enumerates the
# subset as a contract and both callers are pinned against its conformance
# suite.


def _fenced_entries(text: str) -> list[tuple[str, list[tuple[str, bool]]]]:
    """(entry heading, body lines each paired with its fence state), fence-aware.

    A fence opens and closes over the whole document, and a line inside one is
    content: never an entry heading, and never a `Friction:` line. A scanner
    blind to fences moves the count in BOTH directions. A fenced back-dated
    heading opens an entry that was never written and reattributes the lines
    after it to it; a fenced quotation of the friction grammar adds a citation
    nobody made — and that second one needs no attacker, because the cycle that
    introduces the grammar is the work whose journal entry quotes it. A
    scanner that recognizes only ``` also misreads a `~~~` fence, four
    backticks wrapping three-backtick content, and a fence indented one to
    three spaces.

    An opener is a line indented ZERO TO THREE COLUMNS — tabs expanded at four
    — which then begins with three or more of the same fence character. A
    closer is a LATER line under the SAME indent bound, with the same
    character, a run at least as long as the opener's, and nothing but
    whitespace after the run; while a fence is open, no other line opens or
    closes one, and an unclosed fence leaves the remainder fenced. The indent
    bound is CommonMark's and it is what `_fence_marker` enforces: a line
    indented four or more columns is an indented code block, so it neither
    opens a fence nor closes one.

    The fence tracking is modelled on `_fence_marker` in
    `crux/scripts/check_template_parity.py`, which carries the same guard.

    LINES ARE SPLIT BY `_lines`, never by `str.splitlines()` — the same
    discipline `_git` applies to git's stdout, and for the same reason:
    `splitlines()` also breaks on U+2028, U+2029, U+0085, U+000B, U+000C,
    U+000D and U+001C-U+001E, so a journal sentence carrying U+2028 forged a
    `Friction:` line that CommonMark renders as mid-sentence prose. The split
    used to be written out here rather than called, which left the file with
    two copies of one rule; `_lines` is now the single one, and it carries the
    trailing-`\r` strip this FILE lane needs and the git lane does not.

    Lines before the first heading belong to no entry and are dropped.
    """
    entries: list[tuple[str, list[tuple[str, bool]]]] = []
    fence: tuple[str, int] | None = None  # (char, run length) of the open fence
    for line in _lines(text):
        marker = _fence_marker(line)
        if fence is not None:
            if _closes_fence(marker, fence):
                fence = None
            if entries:
                entries[-1][1].append((line, True))
            continue
        if marker is not None:
            fence = (marker[0], marker[1])
            if entries:
                entries[-1][1].append((line, True))
            continue
        if ENTRY_HEADING.match(line):
            entries.append((line, []))
            continue
        if entries:
            entries[-1][1].append((line, False))
    return entries


def _dated_entries(root: Path,
                   docs: Path) -> tuple[list[tuple[str, str, set[str]]], list[str]]:
    """The dated entries, plus the tree-relative paths of the surfaces refused.

    Each entry is (source label, ISO date, distinct ADR ids), over every dated
    mention surface.

    A fenced ADR mention is excluded from a dated entry's id set for the same
    reason `signal_friction_citations` excludes a fenced `Friction:` line: a
    quoted example is not a real mention, and counting it would reset
    `dormancy_days`' clock exactly as a prose mention does.

    THE SECOND RETURN VALUE IS THE NARROWING, NAMED. A refused surface
    contributes no dated mention, so every ADR mentioned only there reads a
    `null` dormancy — and `null` there means "no dated mention", which is FALSE
    when the mention sits in a file that was not read. Symlinking `log.md` out
    of the root moved those ADRs to `null` with the basis unchanged. The
    refused paths therefore come back with the entries, and
    `signal_dormancy_days` names them in its `filter`.
    """
    entries: list[tuple[str, str, set[str]]] = []
    refused: list[str] = []

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return path.name

    def _split(path: Path, label: str) -> None:
        # A symlink at a surface path is REFUSED AND NAMED whether or not it
        # resolves. `is_file()`/`is_dir()` follow the link, so a dangling one
        # used to read as "absent" and drop the surface from the narrowing
        # silently — a null that then claimed "no dated mention exists".
        if path.is_symlink():
            refused.append(_rel(path))
            return
        text = _read_contained(root, path)
        if text is None:
            refused.append(_rel(path))
            return
        for head, lines in _fenced_entries(text):
            m = LOG_HEADING.match(head)
            if m:
                body = "\n".join([head] + [line for line, fenced in lines if not fenced])
                entries.append((label, m.group(1), set(ADR_ID.findall(body))))

    log = docs / "log.md"
    if log.is_symlink() or log.is_file():
        _split(log, "log.md")
    journal = docs / "journal"
    if journal.is_symlink():
        refused.append(_rel(journal))
    elif journal.is_dir():
        for path in sorted(journal.glob("*.md")):
            if path.name != "index.md":
                _split(path, "journal")
    runs = docs / "promptbooks" / "runs"
    if runs.is_symlink():
        refused.append(_rel(runs))
    elif runs.is_dir():
        for path in sorted(runs.glob("**/*.yaml")):
            if path.is_symlink():
                refused.append(_rel(path))
                continue
            text = _read_contained(root, path)
            if text is None:
                refused.append(_rel(path))
                continue
            started = _yaml_top_value(text, "started_at")[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", started):
                entries.append(("run snapshot", started, set(ADR_ID.findall(text))))
    return entries, refused


# --------------------------------------------------------------------------
# the git substrate — the ONLY subprocess use in this file
# --------------------------------------------------------------------------
#
# rule:signal-script-read-surface widens the read surface by exactly one input
# class: this repository's own history, through a `git` subprocess invoked with
# an argument LIST and never a shell string. Four requirements ride with it,
# and all four live in this section — containment, the enumerated environment
# allowlist, the argument grammar, and the leg-scoped failure verdict. Nothing
# outside this section calls `subprocess`.

#: The argument grammar: one to 256 characters drawn from the ASCII letters,
#: the digits, `.`, `_`, `-` and `/`, opening on a letter or a digit. The `..`
#: refusal is a separate test, because a character class cannot express a
#: forbidden PAIR. A value outside the grammar is refused, never passed — so
#: no repo-supplied value can open with a hyphen and be read as an option.
GIT_ARG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class GitUnavailable(Exception):
    """No usable `git`: absent from PATH, or the process would not start."""


class GitLegFailed(Exception):
    """A git invocation ran and refused, or resolved outside the given root."""


def _git_environment() -> dict[str, str]:
    """The subprocess environment, as an ALLOWLIST enumerated by name.

    Membership is `PATH`, inherited; `LC_ALL`, set to `C` so the output grammar
    is stable; `GIT_CONFIG_NOSYSTEM`, set to `1`; and on Windows `SYSTEMROOT`,
    inherited, because `git` cannot start without it. No other name is a
    member.

    The list inherits NO variable whose name begins with `GIT_`, and its one
    `GIT_`-named member is SUPPLIED rather than inherited — the system
    configuration at `/etc/gitconfig` is a channel no absent variable closes.
    `HOME`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH` and `XDG_CONFIG_HOME` are
    absent on every platform, because `git` derives its user-configuration path
    from them. So neither the user configuration nor the system configuration
    is read, and no repository/work-tree redirect, object-store redirect or
    configuration channel reaches it.

    THE NAMES BELOW ARE ILLUSTRATIVE, NOT EXHAUSTIVE, and the allowlist is what
    makes that safe: the list is built from nothing, so NO `GIT_`-prefixed name
    is inherited at all and a name missing from this paragraph is still absent
    from the environment. Naming a subset was the defect — the enumeration read
    as the closed set it never was, and three names it omitted are ones a
    reader would check for: `GIT_CONFIG_PARAMETERS`, which outranks every
    configuration FILE so `GIT_CONFIG_GLOBAL=/dev/null` does not close it;
    `GIT_COMMON_DIR`, which relocates the object store and was measured sending
    36 loose objects into a canary repository; and
    `GIT_ALTERNATE_OBJECT_DIRECTORIES`, which adds one. The illustrations:
    repository/work-tree redirects (`GIT_DIR`, `GIT_WORK_TREE`,
    `GIT_COMMON_DIR`, `GIT_CEILING_DIRECTORIES`,
    `GIT_DISCOVERY_ACROSS_FILESYSTEM`), object-store redirects
    (`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`) and
    configuration channels (`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`,
    `GIT_CONFIG_PARAMETERS`).

    An allowlist rather than an exclusion list, because an exclusion list is
    correct only while it enumerates every redirecting name. The one channel
    left open by design is the repository's own `.git/config` with any
    `include` it carries, bounded by the containment check and the argument
    grammar below.
    """
    env = {"LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1"}
    path = os.environ.get("PATH")
    if path is not None:
        env["PATH"] = path
    if sys.platform == "win32":
        systemroot = os.environ.get("SYSTEMROOT")
        if systemroot is not None:
            env["SYSTEMROOT"] = systemroot
    return env


def _valid_git_arg(value: str) -> bool:
    """True when `value` matches the argument grammar and carries no `..`."""
    return bool(GIT_ARG.match(value)) and ".." not in value


def _git(root: Path, *args: str) -> list[str]:
    """Run git with an argument LIST (never a shell string); return its lines.

    Modelled on `_git` in `crux/scripts/check-blast-radius.py`. Two failure
    classes, and every caller catches both, so neither escapes as a traceback
    and neither reaches the exit-1 `errors` array: this script grades nothing,
    and a signal that could not measure reports `unmeasurable`, not an error.

    git's stderr is deliberately NOT interpolated into either message. It
    carries values this script did not author, and a signal record's `basis` is
    prose a human reads.

    The blob is decoded with `errors="replace"`, matching `_read` at the file
    lane. `text=True` decodes strictly, and the `UnicodeDecodeError` it raises
    subclasses `ValueError` rather than `OSError`, so one invalid byte in a
    tracked file escaped the two failure classes below and collapsed all eight
    signals into the exit-2 environment lane.

    Lines are split on `"\n"` alone, never `str.splitlines()`. Git's
    `--format=%s` emits one line per commit separated by `\n` only, but
    `splitlines()` also breaks on U+2028, U+2029, U+0085, U+000B, U+000C,
    U+000D and U+001C–U+001E — any of which in a commit subject forged an
    extra "line" that was never a commit boundary. U+000D is in that list and
    was missing from it: measured on 3.13.14, `splitlines()` breaks on a lone
    carriage return and `split("\n")` does not. The trailing empty element `split`
    leaves after the final `\n` is dropped, matching `splitlines()`'s own
    convention of not returning one.
    """
    if shutil.which("git") is None:
        raise GitUnavailable("git is not on PATH")
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, check=False,
            encoding="utf-8", errors="replace",
            env=_git_environment(),
        )
    except OSError as exc:
        raise GitUnavailable(f"the git process would not start ({type(exc).__name__})")
    if proc.returncode != 0:
        raise GitLegFailed(f"`git {args[0]}` exited {proc.returncode}")
    lines = proc.stdout.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


NO_HISTORY_YET = "no history yet: the repository has no commits"


def _unborn_head(root: Path) -> bool:
    """True only when `root` is a repository with no commit and no ref at all.

    Three fixed-argument probes. The first, `rev-parse --verify HEAD`, must
    fail: that failure is the unborn signal. The second (HEAD names a branch)
    and the third (no ref exists anywhere) must succeed. A probe that cannot
    start, or a failure of the second or third, answers False, so a failed
    lookup is never read as an unborn HEAD.
    """
    try:
        try:
            _git(root, "rev-parse", "--verify", "--quiet", "HEAD")
            return False
        except GitLegFailed:
            pass
        _git(root, "symbolic-ref", "--quiet", "HEAD")
        return _git(root, "for-each-ref", "--count=1") == []
    except (GitUnavailable, GitLegFailed):
        return False


class _GitLegs:
    """One CONTAINED git session over `root`, or the recorded reason it is not.

    Construction runs the two preconditions once. First containment: the work
    tree `git` resolves must BE the given root. A resolved work tree that is
    not that root is a failure condition rather than a fallback, because
    containment does not follow from naming the root — a plain subdirectory of
    an enclosing checkout is inside a work tree, so a check for *some* work
    tree passes there and reads the enclosing repository's history. That check
    is also what bounds the discovery walk, since no `GIT_CEILING_DIRECTORIES`
    is inheritable through `_git_environment`.

    Then the `--end-of-options` probe. A git too old to accept it puts every
    repo-supplied-value leg in the failed class. `--` is NEVER substituted: it
    is the pathspec separator, and a ref placed after it is read as a path,
    returning an empty result indistinguishable from a null baseline.

    `reason` is None when the session is usable, and otherwise a
    script-authored sentence naming the condition, which a caller puts in its
    own `basis`. `unborn` is True when the HEAD probe failed because the
    repository has no commits yet; `reason` is then `NO_HISTORY_YET` and
    `usable` stays False.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.reason: str | None = None
        self.unborn = False
        try:
            top = _git(root, "rev-parse", "--show-toplevel")
        except (GitUnavailable, GitLegFailed) as exc:
            self.reason = f"git could not resolve a work tree at the repository root: {exc}"
            return
        if not top or Path(top[0]).resolve() != root.resolve():
            self.reason = ("the work tree git resolves is not the given repository "
                           "root, so the read is not contained")
            return
        try:
            _git(root, "rev-list", "-n", "1", "--end-of-options", "HEAD")
        except (GitUnavailable, GitLegFailed) as exc:
            self.unborn = _unborn_head(root)
            if self.unborn:
                self.reason = NO_HISTORY_YET
            else:
                self.reason = (f"the HEAD probe failed in this work tree: HEAD does not "
                               f"resolve to a commit, or git does not accept "
                               f"--end-of-options ({exc})")

    @property
    def usable(self) -> bool:
        return self.reason is None

    def lines(self, *args: str) -> list[str] | None:
        """Run one leg; return its output lines, or None when it did not run."""
        if self.reason is not None:
            return None
        try:
            return _git(self.root, *args)
        except (GitUnavailable, GitLegFailed):
            return None

    def blob(self, rev: str, path: str) -> list[str] | None:
        """Read `<rev>:<path>` after `--end-of-options`, or None.

        BOTH halves are validated against the argument grammar BEFORE they are
        composed — a grammar checked on the composed token would admit a
        hyphen-leading half. None covers two cases the caller must not
        distinguish here: the path is absent at that ref, or the leg failed.
        The caller reports null for both, and never zero.
        """
        if not (_valid_git_arg(rev) and _valid_git_arg(path)):
            return None
        return self.lines("show", "--end-of-options", f"{rev}:{path}")

    def tag_commit(self, spelling: str) -> str | None:
        """Resolve one tag spelling in the `refs/tags/` namespace ALONE, to a COMMIT.

        Fully qualified so a branch of the same name is never read in its
        place. None when the spelling is outside the grammar or does not
        resolve.

        `rev-list -n 1` rather than `rev-parse --verify`, because the rule this
        serves compares COMMITS: `rev-parse` on an ANNOTATED tag yields the tag
        object, so a lightweight and an annotated spelling of one commit read
        as two and nulled a baseline that resolves. `rev-list` peels both to
        the commit. Same argument grammar, same `--end-of-options` placement,
        and the same probe shape `__init__` already runs; a missing ref still
        exits non-zero and surfaces here as None.
        """
        ref = f"refs/tags/{spelling}"
        if not _valid_git_arg(ref):
            return None
        out = self.lines("rev-list", "-n", "1", "--end-of-options", ref)
        return out[0] if out else None


# --------------------------------------------------------------------------
# the three delivery signals
# --------------------------------------------------------------------------

#: A dated version heading is `## [<version>] — <YYYY-MM-DD>` with an EM DASH,
#: the grammar `crux/scripts/promote-changelog.py` emits. `## [Unreleased]`
#: carries no date and is deliberately not one. The version capture is an
#: UNBOUNDED run of non-bracket characters out of a file this script does not
#: author, so `_dated_release_headings` routes it through `redact` before it
#: reaches a mapping key, a `basis`, a `filter` or a `baseline.ref`.
CHANGELOG_HEADING = re.compile(r"^##\s+\[([^\]]+)\]\s+—\s+(\d{4}-\d{2}-\d{2})\s*$")
#: The declared release-prep commit-subject prefix. Counting the commits that
#: match a DECLARED prefix is not mined content; a commit subject is, and none
#: reaches an envelope.
RELEASE_PREP_SUBJECT = re.compile(r"^release prep\b", re.IGNORECASE)
#: The regenerator roster's header row is the unfenced table row whose FIRST
#: FOUR cells are these, in this order, whatever cells follow them. `gate_count`
#: locates the roster by that row and never by the heading above it, which
#: spells its count as an English word — and mining a word into a record is
#: forbidden. An exact match on the whole row read `unmeasurable` on every run
#: once the roster gained its `Scope` column.
ROSTER_HEADER_CELLS = ("Output", "Source of truth", "Regenerator", "Drift check")


def _dated_release_headings(text: str) -> list[tuple[str, str]]:
    """(version string, ISO date) for each dated version heading, in file order.

    `text` is the changelog's content, already read through `_read_contained`
    by the caller.

    The version is mined content and is returned RAW: this function bounds
    nothing, and every consumer that renders it into prose or into a mapping
    key calls `redact(version, quoted=True)` itself. Redacting HERE was wrong
    rather than merely early. `redact(..., quoted=False)` appends its note to
    the value, so a hostile heading and a heading that spells that note
    verbatim left this function as ONE string, and no later transform could
    separate them — a second redaction is a no-op, because U+FFFD is
    printable. One release's `prep_commits` count therefore overwrote
    another's. Rendering at the consumer puts the note outside the quotes and
    keeps them apart. The two consumers that take the raw value take it
    deliberately: the commit-subject match, which is `re.escape`d and is the
    only form that matches a real `1.2.3` heading, and the tag lookup, which
    `_valid_git_arg` bounds — so a hostile version still resolves no tag.

    The heading captures its bracketed text without checking that it LOOKS
    like a version: a prose-shaped run of characters is mined exactly as a
    real version would be, bounded and redacted before it reaches a key.
    """
    out: list[tuple[str, str]] = []
    for line in _lines(text):
        m = CHANGELOG_HEADING.match(line)
        if not m:
            continue
        try:
            dt.date.fromisoformat(m.group(2))
        except ValueError:
            # `2026-13-45` matches the SHAPE and is no calendar date. Dropping
            # it here is what keeps a repo-content typo out of the exit-2
            # environment lane: the heading is not a dated heading, so this
            # file reports the fewer-than-two condition requirement 1 names,
            # and every other signal keeps its own verdict.
            continue
        out.append((m.group(1).strip(), m.group(2)))
    return out


def _skill_count(lines: list[str] | None) -> int | None:
    """The catalogued skill count, or None when the catalog was not read."""
    if lines is None:
        return None
    try:
        data = json.loads("\n".join(lines))
    except ValueError:
        return None
    return len(data) if isinstance(data, list) else None


#: How many hexadecimal characters of the digest go into a lossy key. 64 is
#: the WHOLE SHA-256 digest: the value is not truncated at all, so the
#: injectivity `_prep_key` claims is exactly SHA-256 collision resistance and
#: there is no separate truncation bound to state or defend.
#:
#: THE BOUND THAT MATTERS HERE IS ADVERSARIAL, NOT ACCIDENTAL. This was 16
#: characters, justified as "a collision needs ~2**32 distinct versions in ONE
#: changelog" — which is the ACCIDENTAL bound, the odds of two honestly-written
#: version strings colliding. Nobody has to write them honestly. The version
#: strings are mined from a `CHANGELOG.md` heading, the heading grammar takes
#: an unbounded run of non-bracket characters, and an attacker searches for the
#: pair offline and commits the winner. At 64 bits that search is ~2**32
#: evaluations; measured on one core at 3.23M SHA-256/s in pure Python, about
#: 22 minutes. Demonstrated end to end at a reduced width with only the width
#: changed — key shape and counting half shipping as they are — a collision
#: turned up after 7,223 hashes at 24 bits, and the two versions keyed ONE
#: slot: `two versions in, 1 key(s) out; matched=2`.
#:
#: A collision is not a rendering nuisance. The key names a slot whose value is
#: a COUNT, so one release's `prep_commits` number silently overwrites
#: another's, and the reader sees a plausible number for a release that never
#: had it. The cost of the full digest is 48 more characters on the lossy key
#: shape ONLY — a benign version still keys as its bare `repr`, and the lossy
#: shape was already carrying a redaction note no human reads for pleasure.
_PREP_KEY_DIGEST_CHARS = 64


def _prep_key(version: str) -> str:
    """The `prep_commits` mapping key for one mined version string.

    THE PROPERTY THIS KEY CARRIES IS INJECTIVITY: two distinct version
    strings never produce the same key. Nothing weaker will do, because the
    key names a slot whose value is a count — two versions sharing a slot do
    not merely render alike, they make one release's `prep_commits` number
    silently overwrite another's.

    AND IT IS CLAIMED AGAINST AN ATTACKER, NOT AGAINST CHANCE. The versions
    are mined from a file this reader does not own, so the question is never
    "how unlikely is a collision" but "how much work is one" — see
    `_PREP_KEY_DIGEST_CHARS` for the measured answer at the width this used to
    ship. The digest is untruncated, so the claim above is exactly SHA-256's.

    `redact(version, quoted=True)` alone does NOT carry that property, and
    could not. It is a BOUNDED, RENDERED form — it truncates at 120 characters
    and replaces every unprintable character with U+FFFD — and any bounded
    rendering of an unbounded input is many-to-one by construction. Measured:
    two distinct hostile versions of equal total length sharing a 120-character
    prefix produced ONE key, because the shown head matched and the
    `truncated from N characters` discriminator matched too.

    The obvious repair — key on the full raw value — is REFUSED here. That
    would put an unbounded, unredacted mined string into a JSON mapping key,
    undoing the redaction the rest of this lane exists to apply: a key is an
    output channel like any other, and a 10 MB key carrying ESC and CR is the
    class `untrusted.redact` was written for.

    So the key is made injective INSTEAD OF unbounded, in two shapes:

      * A version the rendering does not lose — at most 120 characters, every
        one of them printable — keys as `repr(version)` exactly, as before.
        `repr` over a string is injective on its own, so nothing is added, and
        a benign changelog's keys stay the plain `'1.2.3'` a human reads.
      * A version the rendering DOES lose — too long, or carrying an
        unprintable character — keys as the redacted form followed by a
        SHA-256 digest of the whole raw value. The digest is what separates
        two values that render alike; the redacted head is what keeps the key
        legible and escape-free.

    The two shapes cannot collide with each other: the first ends in the
    closing quote `repr` always writes, and the second ends in `]`.
    """
    if len(version) <= LIMIT and version.isprintable():
        return redact(version, quoted=True)
    digest = hashlib.sha256(version.encode("utf-8")).hexdigest()
    return (f"{redact(version, quoted=True)} "
            f"[sha256:{digest[:_PREP_KEY_DIGEST_CHARS]}]")


#: Bounds the release-mark walk over CHANGELOG.md's touching commits (per
#: the `release-mark-is-the-boundary` rule). The walk reads one or
#: two blobs per touching first-parent commit — the commit's own
#: CHANGELOG.md and, where one exists, its first-parent parent's — because
#: the predicate is a blob comparison and not a subject scan. Measured on
#: this repository: 94 blob reads, 0.87s, all 47 dated versions resolve. A
#: version whose introducing commit lies past the cap is treated exactly as
#: one no commit satisfies the predicate for: null, never a guess.
RELEASE_MARK_WALK_CAP = 2000


def _mark_blob_headings(legs: "_GitLegs", sha: str,
                        cache: dict[str, set[str]]) -> set[str]:
    """The dated-heading version strings CHANGELOG.md carries at `sha`.

    An absent path or a failed single read is the empty set. This is called
    only once the git SESSION is already known usable, so a miss here means
    the path did not exist yet at that commit — a legitimate state for any
    commit before CHANGELOG.md was created, and the empty set is exactly
    what "no heading is present" should read as, root commit included.
    """
    if sha in cache:
        return cache[sha]
    blob = legs.blob(sha, "CHANGELOG.md")
    headings = ({v for v, _d in _dated_release_headings("\n".join(blob))}
                if blob is not None else set())
    cache[sha] = headings
    return headings


def resolve_release_marks(
        legs: "_GitLegs") -> tuple[dict[str, str], dict[str, str], bool] | None:
    """version -> its release mark commit, and version -> why it has none.

    A RELEASE MARK is the first-parent commit whose CHANGELOG.md blob carries
    that version's dated heading and whose first-parent parent's blob does
    not. A commit with no first-parent parent — the repository root —
    satisfies the second half VACUOUSLY, because nothing precedes it: a
    heading present there was introduced there.

    Returns `(marks, conditions)`, two mappings with disjoint keysets. A
    version with a usable mark is a key of `marks` alone, valued with the
    resolving commit sha. A version with no usable mark is a key of
    `conditions` alone, naming one of two reasons: `"baseline-ref-unresolved"`
    (no commit satisfies the predicate for it — including a version this walk
    never reaches within `RELEASE_MARK_WALK_CAP`) or `"baseline-ref-ambiguous"`
    (the mark is shared with one or more other versions, or more than one
    commit satisfies the predicate for this one version — a heading removed
    and later restored produces the latter). Marks order releases whose marks
    are DISTINCT; a shared mark separates none of the versions sharing it, so
    every one of them reads `"baseline-ref-ambiguous"` rather than one winning
    an arbitrary tie-break.

    The third return value says whether the walk was TRUNCATED by
    `RELEASE_MARK_WALK_CAP`. It matters to the caller's prose: a version past
    the cap reads `baseline-ref-unresolved`, and glossing that as "no commit
    satisfies the predicate" would be false when a commit does and the walk
    simply stopped short of it.

    Returns `None` when the touching-commit walk itself could not run — the
    git leg failed outright, distinct from a version this walk ran but could
    not resolve.
    """
    touch = legs.lines("log", "--first-parent", "--format=%H %P",
                       "HEAD", "--", "CHANGELOG.md")
    if touch is None:
        return None
    capped = len(touch) > RELEASE_MARK_WALK_CAP
    touch = touch[:RELEASE_MARK_WALK_CAP]
    # The DECLARED versions: the dated headings at HEAD, the same release record
    # the signal reads. A version declared here but marked by no commit is what
    # `baseline-ref-unresolved` names, and it cannot be seen from the walk alone.
    declared_versions = set(_mark_blob_headings(legs, "HEAD", {}))
    cache: dict[str, set[str]] = {}
    candidates: dict[str, list[str]] = {}
    for line in touch:
        parts = line.split()
        if not parts:
            continue
        sha = parts[0]
        parent = parts[1] if len(parts) > 1 else None
        own = _mark_blob_headings(legs, sha, cache)
        # No first-parent parent (the root commit) satisfies the predicate's
        # second half vacuously: treat it as carrying no heading at all.
        parent_headings = _mark_blob_headings(legs, parent, cache) if parent else set()
        for version in own - parent_headings:
            candidates.setdefault(version, []).append(sha)

    marks: dict[str, str] = {}
    conditions: dict[str, str] = {}
    # Seed from every DECLARED version, not from `candidates`: a version no
    # commit marks has no entry in `candidates` at all, so building the mapping
    # from that dict alone could never emit `baseline-ref-unresolved` and left
    # the caller with a bare `no-baseline` — an unnamed null, which is the one
    # thing the closed condition set exists to remove.
    for version in declared_versions:
        shas = candidates.get(version, [])
        if not shas:
            conditions[version] = "baseline-ref-unresolved"
        elif len(shas) > 1:
            conditions[version] = "baseline-ref-ambiguous"
        else:
            marks[version] = shas[0]

    # A mark shared by two or more versions separates none of them.
    by_commit: dict[str, list[str]] = {}
    for version, sha in marks.items():
        by_commit.setdefault(sha, []).append(version)
    for sha, versions in by_commit.items():
        if len(versions) > 1:
            for version in versions:
                del marks[version]
                conditions[version] = "baseline-ref-ambiguous"
    return marks, conditions, capped


def _first_parent_commits(legs: "_GitLegs") -> list[tuple[str, str]] | None:
    """(sha, subject) for every first-parent commit from HEAD, newest first."""
    lines = legs.lines("log", "--first-parent", "--format=%H%x09%s", "HEAD")
    if lines is None:
        return None
    out: list[tuple[str, str]] = []
    for line in lines:
        sha, _tab, subject = line.partition("\t")
        out.append((sha, subject))
    return out


def _release_span_and_prep(ordered_versions: list[str],
                           marks: dict[str, str],
                           full_commits: list[tuple[str, str]] | None,
                           ) -> tuple[dict[str, int | None], dict[str, int | None]]:
    """Per-release `span_commits` and `prep_commits` over the mark interval.

    `ordered_versions` is every dated version, oldest first. `marks` carries
    ONLY the versions whose own release mark is a single, unshared, resolved
    commit (`resolve_release_marks`'s first return value) — a version absent
    from it has no usable mark of its own. `full_commits` is every
    first-parent commit from HEAD, newest first, as (sha, subject) pairs, or
    `None` when that git leg did not run.

    Per `span-and-prep-are-different-counts`, a release's
    interval EXCLUDES the previous release's mark and INCLUDES its own, so
    consecutive intervals partition history without overlap. `span_commits`
    counts every first-parent commit in that interval and establishes nothing
    about whether any of them was preparation. `prep_commits` counts, in that
    same interval, the commits whose subject matches the declared preparation
    prefix — a count of declarations, never of the span.

    Both mappings are keyed by the RAW version string; the caller applies
    `_prep_key`. A release's interval is undefined — both members `null`,
    never `0` — whenever any of these hold: this is the oldest release (no
    previous release exists at all, so the older bound is missing); this
    release's own mark is unusable; the previous release's mark is unusable;
    the two marks do not order as older-then-newer in `full_commits`; or
    `full_commits` itself is unavailable. `0` on either member means the leg
    ran, the interval is bounded, and nothing of that kind fell inside it —
    `prep_commits: 0` says nobody declared preparation, not that the release
    had none.
    """
    if full_commits is None:
        return ({v: None for v in ordered_versions},
                {v: None for v in ordered_versions})
    index = {sha: i for i, (sha, _s) in enumerate(full_commits)}
    span: dict[str, int | None] = {}
    prep: dict[str, int | None] = {}
    for i, version in enumerate(ordered_versions):
        own = marks.get(version)
        prev_version = ordered_versions[i - 1] if i > 0 else None
        prev = marks.get(prev_version) if prev_version is not None else None
        own_idx = index.get(own) if own is not None else None
        prev_idx = index.get(prev) if prev is not None else None
        if own_idx is None or prev_idx is None or own_idx >= prev_idx:
            span[version] = None
            prep[version] = None
            continue
        window = full_commits[own_idx:prev_idx]
        span[version] = len(window)
        prep[version] = sum(1 for _sha, subject in window
                            if RELEASE_PREP_SUBJECT.match(subject))
    return span, prep


def signal_release_cadence(root: Path, tree: str,
                           errors: list[dict] | None = None) -> dict:
    """Release cadence and, per release, its span and its declared preparation.

    `errors` is `build`'s findings list; accepted for call-site symmetry with
    the other signal functions. Nothing currently reaches it — the mined
    version string no longer drives a regex match against a commit subject,
    so the `re.error` lane the previous shape guarded against is gone.

    Measurability rests on the CHANGELOG alone, never on git: the verdict is
    `unmeasurable` when the file is absent or carries fewer than two dated
    headings, and only then. `releases`, `intervals_days`,
    `mean_interval_days` and `median_interval_days` compute from the
    changelog with no git leg at all. GIT-DEPENDENCE IS PER MEMBER: only
    `span_commits` and `prep_commits` rest on the release-mark predicate
    (`resolve_release_marks`, a first-parent commit test) and therefore need a
    contained git session. Where that session cannot be established, those
    two members are `null` for every release and the four changelog-only
    members still compute — the verdict stays `computed`, because an absent
    git session is an absent input for two members rather than for the
    signal.
    """
    changelog = root / "CHANGELOG.md"
    surface = "the dated version headings of CHANGELOG.md at the repository root"
    if not changelog.is_file():
        return _record(
            "release_cadence", "unmeasurable", None,
            f"{surface}; the file is absent",
            "CHANGELOG.md is absent from the repository root, so the release record "
            "a cadence is computed over does not exist",
        )
    changelog_text = _read_contained(root, changelog)
    if changelog_text is None:
        return _record(
            "release_cadence", "unmeasurable", None,
            f"{surface}; the file was not read as a contained artifact",
            "CHANGELOG.md at the repository root was not read as a contained artifact "
            "— the name did not resolve, or resolves outside that root, or the "
            "resolved name is a symlink O_NOFOLLOW refused, or the open failed for "
            "another reason — so no release record is read",
        )
    headings = _dated_release_headings(changelog_text)
    if len(headings) < 2:
        return _record(
            "release_cadence", "unmeasurable", None,
            f"{surface}; {len(headings)} dated heading(s) read",
            f"CHANGELOG.md carries fewer than two dated version headings "
            f"({len(headings)} read), and an interval needs two dated points",
        )

    ordered = sorted(headings, key=lambda h: h[1])
    versions = [v for v, _d in ordered]
    dates = [dt.date.fromisoformat(d) for _v, d in ordered]
    intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    ranked = sorted(intervals)
    mid = len(ranked) // 2
    median = float(ranked[mid]) if len(ranked) % 2 else (ranked[mid - 1] + ranked[mid]) / 2

    # A SEPARATE ordering, for span_commits/prep_commits adjacency ONLY.
    # `ordered` above breaks a same-date tie by PRESERVING the raw file
    # order, which is newest-first — so two headings dated on the same
    # calendar day sort with the file's LATER (older, by convention) one
    # FIRST in `versions`, backwards from the oldest-first order the day
    # count alone cannot express. That is harmless for `intervals_days` (a
    # same-day pair's interval is 0 either way), but a wrong "previous
    # release" identity is not harmless here: it would bound one release's
    # interval against a mark that is not actually its predecessor.
    # Reversing the raw (newest-first) file order to oldest-first BEFORE the
    # same stable sort breaks a same-date tie by file position instead,
    # which is how this project's own changelog convention orders same-day
    # releases relative to each other.
    mark_order = [v for v, _d in sorted(reversed(headings), key=lambda h: h[1])]

    legs = _GitLegs(root)
    span_by_key: dict[str, int | None] = {_prep_key(v): None for v in versions}
    prep_by_key: dict[str, int | None] = {_prep_key(v): None for v in versions}
    if legs.unborn:
        mark_note = "coverage not measured, because there is no history yet"
    elif not legs.usable:
        mark_note = (f"coverage not measured, because the contained git session could "
                     f"not be established ({legs.reason})")
    else:
        marks_result = resolve_release_marks(legs)
        if marks_result is None:
            mark_note = ("coverage not measured, because the release-mark walk over "
                        "CHANGELOG.md's touching commits did not run")
        else:
            marks, conditions, capped = marks_result
            full_commits = _first_parent_commits(legs)
            span, prep = _release_span_and_prep(mark_order, marks, full_commits)
            span_by_key = {_prep_key(v): span[v] for v in versions}
            prep_by_key = {_prep_key(v): prep[v] for v in versions}
            resolved = sum(1 for v in versions if v in marks)
            # Every version DECLARED at HEAD is seeded into one of the two
            # mappings, including one past the walk cap, which
            # `resolve_release_marks` seeds as `baseline-ref-unresolved`.
            ambiguous = sum(1 for v in versions if v not in marks
                            and conditions.get(v) == "baseline-ref-ambiguous")
            unresolved = len(versions) - resolved - ambiguous
            bounded = sum(1 for v in versions if span[v] is not None)
            # The gloss on `unresolved` is only true while the walk read the
            # whole history. Past the cap a commit MAY satisfy the predicate
            # and the walk simply stopped short of it, so the note says that
            # instead of asserting an absence it did not establish.
            cap_note = (f"the walk stopped at the cap of {RELEASE_MARK_WALK_CAP} "
                        f"touching commits, so an unresolved version may be marked "
                        f"by a commit beyond it"
                        if capped else
                        f"the walk read every touching commit, under the cap of "
                        f"{RELEASE_MARK_WALK_CAP}")
            unresolved_gloss = ("no commit the walk read satisfies the mark predicate"
                                if capped else
                                "no commit satisfies the mark predicate")
            mark_note = (f"{resolved} of {len(versions)} dated headings resolve to a "
                        f"single, unshared release mark ({cap_note}); "
                        f"{unresolved} unresolved ({unresolved_gloss}), "
                        f"{ambiguous} ambiguous (a shared mark, or more "
                        f"than one commit satisfying the predicate for one version); "
                        f"{bounded} of {len(versions)} releases have a bounded interval "
                        f"(both their own mark and their immediate predecessor's are "
                        f"resolved and unshared)")

    tags = legs.lines("for-each-ref", "--format=%(refname:short)", "refs/tags/")
    if tags is None:
        tag_note = (f"coverage not measured, because the tag leg did not run "
                    f"({legs.reason or 'the leg failed'})")
    else:
        names = set(tags)
        covered = sum(1 for v in versions if v in names or f"v{v}" in names)
        tag_note = (f"{covered} of {len(versions)} dated headings carry a tag of the "
                    f"same name, in either admitted spelling")

    value = {
        "releases": len(versions),
        "intervals_days": intervals,
        "mean_interval_days": round(sum(intervals) / len(intervals), 1),
        "median_interval_days": median,
        "span_commits": span_by_key,
        "prep_commits": prep_by_key,
    }
    basis = (f"{surface}, plus — for span_commits and prep_commits only — the "
             f"release-mark walk and the first-parent commit history of HEAD, both "
             f"read through a contained git work tree; the partition of that history "
             f"into per-release intervals happens in this script, over the marks and "
             f"commits it read")
    filt = (f"chosen marker: the dated version headings of CHANGELOG.md — "
            f"{len(versions)} dated headings. Each heading yields its version string, "
            f"which keys span_commits and prep_commits after the redaction bound is "
            f"applied to it; the heading grammar does not check that the captured text "
            f"looks like a version, so a prose-shaped bracketed run is mined exactly as "
            f"a real version would be, and nothing else in the heading or file is "
            f"mined. Rejected marker, git tags: {tag_note}. "
            f"Rejected marker, the release-prep commit-subject prefix as a BOUNDARY "
            f"(it is still read, inside a resolved interval, to count prep_commits "
            f"declarations): {mark_note}. "
            f"span_commits establishes only that its commits lie between two release "
            f"marks; it says nothing about whether any of them was preparation. "
            f"A null on either member means the interval is not bounded — the release "
            f"has no previous release, either mark is unresolved or ambiguous, the "
            f"contained git session could not be established, or there is no history "
            f"yet; a null is never rendered "
            f"as a 0. A 0 means the leg ran, the interval is bounded, and nothing of "
            f"that kind fell inside it.")
    return _record("release_cadence", "computed", value, basis, filt)


def _schema_growth_is_dev_repo(root: Path) -> bool:
    """True when this checkout is (or resembles) the crux development repo.

    `crux/catalog/skills.json` is a concept that exists only in THAT
    repository; a downstream target repo never carries it, at any commit,
    which is exactly what `surface-not-comparable` means. Detected by a
    structural marker on the CURRENT working tree — the `crux/scripts`
    directory this very script ships under — rather than by asking whether
    the catalog file happens to be present at one ref, which would conflate
    "not yet added" (a real `surface-absent`) with "never a concept here".
    """
    return (root / "crux" / "scripts").is_dir()


def signal_schema_growth(root: Path, docs: Path, tree: str) -> dict:
    """The docs-tree AGENTS.md line count and the catalogued skill count,
    compared between HEAD and the RELEASE MARK of the second-newest dated
    changelog version — rule:baseline-is-a-release-mark-and-unavailable-is-named.

    THE ONE DELIVERY SIGNAL WHOSE MEASURABILITY RESTS ON THE GIT SESSION
    rather than on any one read. What forces `unmeasurable` is the
    `_GitLegs` precondition failing — git absent, the work tree not resolving,
    containment refused, or the HEAD probe failing on a state that is not an
    unborn HEAD — reported as `history-unavailable`, which takes precedence
    over every other condition. An unborn HEAD is not that failure: it yields
    `computed` with null members and "no history yet".
    A read failing at one end is a different case: that member is null with
    its condition named in `value["conditions"]`, and the verdict stays
    `computed`.

    The baseline's locator is a COMMIT (40 lowercase hex), never a tag: the
    release act does not tag this repository (the release script mints the
    tag inside a clone of the TARGET repository), so a tag-based locator
    resolves for one release in forty-seven and none other. It never slides
    to a neighbouring version when the intended one has no single mark —
    sliding would substitute one release's boundary for another's.
    """
    claude_rel = f"{tree}/AGENTS.md"
    catalog_rel = "crux/catalog/skills.json"
    surface = (f"{claude_rel} and {catalog_rel}, read at HEAD and at the release mark "
               f"of the second-newest dated changelog version, through a contained git "
               f"work tree")

    legs = _GitLegs(root)
    if not legs.usable and not legs.unborn:
        return _record(
            "schema_growth", "unmeasurable", None,
            f"{surface}; history-unavailable: the HEAD leg did not run: {legs.reason}",
            f"history-unavailable: the contained git session could not be established "
            f"({legs.reason}), which forces this record's verdict to unmeasurable rather "
            f"than leaving one surface null, and takes precedence over every other "
            f"condition this signal recognizes",
        )

    conditions: list[dict] = []
    notes: list[str] = []
    surface_notes: dict[str, dict[str, str]] = {}

    def _add(condition: str, *, surface_name: str | None = None, detail: str | None = None) -> None:
        entry: dict = {"condition": condition}
        if surface_name is not None:
            entry["surface"] = surface_name
        conditions.append(entry)
        if detail:
            notes.append(detail)

    def _note_surface(condition: str, endpoint: str, surface_name: str) -> None:
        surface_notes.setdefault(surface_name, {})[endpoint] = condition

    dev_repo = _schema_growth_is_dev_repo(root)
    not_comparable = {"claude_md_lines": False, "skills": not dev_repo}
    if not dev_repo:
        _add("surface-not-comparable", surface_name="skills")

    def _read_surface(rev: str, path: str, surface_name: str, endpoint: str, parse):
        if not_comparable[surface_name] or legs.unborn:
            return None
        listing = legs.lines("ls-tree", "--name-only", rev, "--", path)
        if listing is None:
            _note_surface("surface-unreadable", endpoint, surface_name)
            return None
        if not listing:
            _note_surface("surface-absent", endpoint, surface_name)
            return None
        lines = legs.blob(rev, path)
        if lines is None:
            _note_surface("surface-unreadable", endpoint, surface_name)
            return None
        parsed = parse(lines)
        if parsed is None:
            _note_surface("surface-malformed", endpoint, surface_name)
            return None
        return parsed

    current = {
        "claude_md_lines": _read_surface("HEAD", claude_rel, "claude_md_lines",
                                         "current", lambda lines: len(lines)),
        "skills": _read_surface("HEAD", catalog_rel, "skills", "current", _skill_count),
    }

    changelog = root / "CHANGELOG.md"
    no_release_record: str | None = None
    headings: list[tuple[str, str]] = []
    if not changelog.is_file():
        no_release_record = "CHANGELOG.md is absent from the repository root"
    else:
        changelog_text = _read_contained(root, changelog)
        if changelog_text is None:
            no_release_record = (
                "CHANGELOG.md at the repository root was not read as a contained "
                "artifact — the name did not resolve, or resolves outside that root, "
                "or the resolved name is a symlink O_NOFOLLOW refused, or the open "
                "failed for another reason")
        else:
            headings = _dated_release_headings(changelog_text)
            if len(headings) < 2:
                no_release_record = (f"CHANGELOG.md carries fewer than two dated "
                                     f"version headings ({len(headings)} read)")

    baseline_commit: str | None = None
    baseline_version: str | None = None
    if no_release_record is not None:
        _add("no-release-record", detail=f"no-release-record: {no_release_record}")
    else:
        newest_first = sorted(headings, key=lambda h: h[1], reverse=True)
        newest_version = newest_first[0][0]
        baseline_version = newest_first[1][0]
        # ONE mark predicate for both delivery signals. ADR-0109 makes the mark
        # "the single boundary used by the two signals that take one", so a
        # second implementation here could drift from release_cadence's and
        # give two answers for one repository.
        marks_result = None if legs.unborn else resolve_release_marks(legs)
        if marks_result is None:
            _add("baseline-ref-unresolved",
                detail=f"baseline-ref-unresolved: {NO_HISTORY_YET}, so no commit could be "
                       f"checked against the mark predicate" if legs.unborn else
                       "baseline-ref-unresolved: the changelog's touch-commit history leg "
                       "did not run, so no commit could be checked against the mark "
                       "predicate")
        else:
            marks, mark_conditions, _capped = marks_result
            candidate_commit = marks.get(baseline_version)

            def _problem(version: str) -> str | None:
                """The resolution condition for one declared version, or None.

                `resolve_release_marks` seeds its conditions from the versions
                HEAD's CHANGELOG.md blob declares; this signal reads the
                headings from the WORK TREE. A version declared in the work
                tree and absent from HEAD is therefore in neither mapping, and
                reading that absence as "resolved" would hand the caller a
                bare `no-baseline` — the unnamed null ADR-0109 refuses. It is
                unresolved: no commit satisfies the mark predicate for it.
                """
                if version in mark_conditions:
                    return mark_conditions[version]
                if version in marks:
                    return None
                return "baseline-ref-unresolved"

            newest_problem = _problem(newest_version)
            baseline_problem = _problem(baseline_version)
            if newest_problem is not None:
                _add(newest_problem,
                    detail=f"{newest_problem}: the newest dated version heading "
                           f"{redact(newest_version, quoted=True)} does not yield a single "
                           f"release mark")
            elif baseline_problem is not None:
                _add(baseline_problem,
                    detail=f"{baseline_problem}: the second-newest dated version heading "
                           f"{redact(baseline_version, quoted=True)} does not yield a "
                           f"single release mark")
            else:
                baseline_commit = candidate_commit

    if baseline_commit is None:
        for surface_name in ("claude_md_lines", "skills"):
            if not not_comparable[surface_name]:
                _add("no-baseline", surface_name=surface_name)
        baseline = {"ref": None, "release": None, "claude_md_lines": None, "skills": None}
    else:
        baseline = {
            "ref": baseline_commit,
            "release": redact(baseline_version, quoted=True),
            "claude_md_lines": _read_surface(baseline_commit, claude_rel, "claude_md_lines",
                                             "baseline", lambda lines: len(lines)),
            "skills": _read_surface(baseline_commit, catalog_rel, "skills",
                                    "baseline", _skill_count),
        }

    # ONE condition per member, so a surface failing at BOTH ends has to pick
    # an endpoint — and it picks `baseline`: that is the end a reader can
    # change, and substituting it is what would make the comparison possible.
    # Naming both is not admitted. The five resolution conditions above carry
    # no endpoint at all, because they describe resolving the baseline or the
    # session rather than a read at one end; `surface-not-comparable` carries
    # none either, being a relation between the two ends rather than a failure
    # at one.
    for surface_name, by_endpoint in surface_notes.items():
        if len(by_endpoint) == 2:
            conditions.append({"condition": by_endpoint["baseline"],
                               "endpoint": "baseline", "surface": surface_name})
        else:
            ((endpoint, condition),) = by_endpoint.items()
            conditions.append({"condition": condition, "endpoint": endpoint,
                               "surface": surface_name})

    value = {"current": current, "baseline": baseline, "conditions": conditions}

    if not conditions:
        condition_note = "no condition fired: both surfaces compared cleanly at both ends"
    else:
        rendered = []
        for entry in conditions:
            bits = [entry["condition"]]
            if "surface" in entry:
                bits.append(f"surface={entry['surface']}")
            if "endpoint" in entry:
                bits.append(f"endpoint={entry['endpoint']}")
            rendered.append(" ".join(bits))
        condition_note = "conditions: " + "; ".join(rendered)
    baseline_note = (f"baseline ref {baseline['ref']}, the release mark of "
                     f"{baseline['release']}" if baseline_commit is not None
                     else "no baseline ref resolved")
    notes_note = (" " + " ".join(notes)) if notes else ""
    filt = (f"the baseline locator is the release mark (a first-parent commit, never a "
            f"tag) of the changelog's SECOND-NEWEST dated version heading; {baseline_note}."
            f"{notes_note} "
            f"A closed set of nine conditions replaces a bare null — five resolution "
            f"conditions with no endpoint (no-release-record, baseline-ref-unresolved, "
            f"baseline-ref-ambiguous, history-unavailable, no-baseline) and four surface "
            f"conditions (surface-absent, surface-unreadable, surface-malformed, each "
            f"named with the endpoint it occurred at except surface-not-comparable, a "
            f"relation between the two endpoints) — never a bare zero for a member no "
            f"successful measurement was made for. {condition_note}. {catalog_rel} exists "
            f"only in the crux development repository, so its surface is "
            f"surface-not-comparable rather than surface-absent in a downstream target "
            f"repository.")
    if legs.unborn:
        filt = (f"{NO_HISTORY_YET}; every member is null because no measurement could be "
                f"made. {condition_note}.{notes_note}")
        surface = f"{NO_HISTORY_YET}; {surface}"
    return _record("schema_growth", "computed", value, surface, filt)


def signal_gate_count(root: Path, tree: str) -> dict:
    """The enrolled regenerator rows in the repo-root AGENTS.md roster.

    Reads no git. The roster is located by its header row, the table row
    whose first four cells are `ROSTER_HEADER_CELLS` whatever cells follow
    them, and never by the heading above it. A row inside a fence, an HTML
    comment or an indented code block is content, never a candidate. THAT ROW
    is the input whose absence forces `unmeasurable`. A roster the header row
    locates carrying no enrolled row is a count of zero rather than an absence.

    The body is the contiguous lines after the header that open with `|` once
    leading whitespace is stripped. A delimiter-shaped first body line is the
    delimiter row and is not counted. Any later delimiter-shaped body line, or
    a delimiter-shaped line carrying a pipe that ends the body, means a second
    table is glued to the roster or its delimiter row is doubled. Any other
    line that ends the body while carrying a pipe is refused as well: a row
    written without its leading pipe renders as a further roster row, and the
    reader does not tell such a row from a line that opens another block. A
    blank line followed by a line opening with `|` that no delimiter row
    follows splits the roster: those rows have no header of their own. In
    each case the roster's extent is ambiguous, and the record is
    `unmeasurable` rather than a count that takes in another table's rows or
    stops above one of its own.
    """
    path = root / "AGENTS.md"
    surface = (f"the regenerator roster in the repo-root AGENTS.md — not "
               f"{tree}/AGENTS.md, which is the tree's operational schema")
    if not path.is_file():
        return _record(
            "gate_count", "unmeasurable", None,
            f"{surface}; the file is absent",
            "AGENTS.md is absent from the repository root, so the roster's header "
            "row — this signal's whole input — does not exist",
        )
    text = _read_contained(root, path)
    if text is None:
        return _record(
            "gate_count", "unmeasurable", None,
            f"{surface}; the file was not read as a contained artifact",
            "AGENTS.md at the repository root was not read as a contained artifact — "
            "the name did not resolve, or resolves outside that root, or the resolved "
            "name is a symlink O_NOFOLLOW refused, or the open failed for another "
            "reason — so no roster is located",
        )
    lines = _lines(text)
    # Fence-aware: a decoy header row inside a fenced code block is
    # content, never a candidate match. `next(...)` over the unguarded scan
    # took the FIRST textual match wherever it sat, so a decoy placed first
    # would win outright.
    matches: list[int] = []
    fence: tuple[str, int] | None = None
    comment = False
    for i, line in enumerate(lines):
        if comment:
            comment = "-->" not in line
            continue
        marker = _fence_marker(line)
        if fence is not None:
            if _closes_fence(marker, fence):
                fence = None
            continue
        if marker is not None:
            fence = (marker[0], marker[1])
            continue
        if line.lstrip().startswith("<!--"):
            comment = "-->" not in line.lstrip()[4:]
            continue
        expanded = line.expandtabs(4)
        if len(expanded) - len(expanded.lstrip(" ")) > 3:
            continue
        cells = _table_cells(line.strip())
        if cells is not None and tuple(cells[:4]) == ROSTER_HEADER_CELLS:
            matches.append(i)
    if not matches:
        return _record(
            "gate_count", "unmeasurable", None,
            f"{surface}; the roster header row is absent",
            "the repo-root AGENTS.md carries no roster header row: no unfenced table "
            "row whose first four cells are Output, Source of truth, Regenerator and "
            "Drift check, in that order; that header row is the input, and the heading "
            "above the table is never read, because it spells its count as an English "
            "word",
        )
    if len(matches) > 1:
        return _record(
            "gate_count", "unmeasurable", None,
            f"{surface}; {len(matches)} unfenced candidate header rows found",
            f"the repo-root AGENTS.md carries {len(matches)} lines matching the roster "
            f"header row outside any fenced block; a first-match read could be won by a "
            f"decoy, so an ambiguous roster is refused rather than guessed at",
        )
    header = matches[0]
    end = header + 1
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    body = list(range(header + 1, end))
    if body and _separator_shaped(lines[body[0]]):
        body = body[1:]
    stray = [k for k in body if _separator_shaped(lines[k])]
    if (end < len(lines) and UNESCAPED_PIPE.search(lines[end])
            and _separator_shaped(lines[end])):
        stray.append(end)
    if stray:
        return _record(
            "gate_count", "unmeasurable", None,
            f"{surface}, located at line {header + 1}; a delimiter-shaped line at "
            f"line {stray[0] + 1}",
            "a delimiter-shaped line inside the roster body makes the roster's extent "
            "ambiguous: a table is glued under the roster, or its delimiter row is "
            "doubled, so a count could take in another table's rows and is refused "
            "rather than guessed at",
        )
    if end < len(lines) and UNESCAPED_PIPE.search(lines[end]):
        return _record(
            "gate_count", "unmeasurable", None,
            f"{surface}, located at line {header + 1}; line {end + 1} ends the body "
            f"and carries a pipe",
            "the line that ends the roster body carries a pipe without opening with "
            "one; a renderer draws such a line as a further roster row unless it "
            "opens another block, so the roster's extent is ambiguous and a count "
            "that stops above it is refused rather than guessed at",
        )
    after = end
    while after < len(lines) and not lines[after].strip():
        after += 1
    if (after > end and after < len(lines) and lines[after].lstrip().startswith("|")
            and not (after + 1 < len(lines) and _separator_shaped(lines[after + 1]))):
        return _record(
            "gate_count", "unmeasurable", None,
            f"{surface}, located at line {header + 1}; line {after + 1} opens with a "
            f"pipe after a blank line",
            "a line opening with a pipe follows the roster body after a blank line, "
            "with no delimiter row under it, so it opens no new table: the rows from "
            "it on read as roster rows split off by the blank line, and a count that "
            "stops at the blank line is refused rather than guessed at",
        )
    return _record(
        "gate_count", "computed", len(body),
        f"{surface}, located at line {header + 1}",
        "counted as the contiguous lines after the header row that open with a pipe "
        "once leading whitespace is stripped, less the delimiter row directly under the "
        "header, stopping at the first other line; the heading above the table is never "
        "read, because it spells its count as an English word, and a roster the header "
        "row locates carrying no enrolled row reports 0 rather than reading as an "
        "absence",
    )


# --------------------------------------------------------------------------
# the five ADR-corpus signals
# --------------------------------------------------------------------------

def _record(signal: str, verdict: str, value, basis: str, filt: str | None) -> dict:
    return {"signal": signal, "verdict": verdict, "value": value,
            "basis": basis, "filter": filt}


def signal_amendment_fan_in(active: dict[str, dict], tree: str) -> dict:
    fan_in = {adr_id: 0 for adr_id in active}
    for meta in active.values():
        for target in set(meta["amends"]) | set(meta["supersedes"]):
            if target in fan_in:
                fan_in[target] += 1
    return _record(
        "amendment_fan_in", "computed", fan_in,
        f"the `amends` and `supersedes` frontmatter of the {len(active)} active ADRs "
        f"under {tree}/adrs/",
        f"archive exclusion: only top-level {tree}/adrs/ADR-*.md are read, so an "
        f"archived ADR neither carries a count nor contributes one",
    )


def signal_carve_out_count(governs_exempt: list[str], doctrine_exempt: list[str],
                           literals: list[tuple[str, str, list[str]]], tree: str,
                           scripts_present: bool = True,
                           doctrine_never_compiled: bool = False) -> dict:
    manifest_surface = sorted(set(governs_exempt) | set(doctrine_exempt))
    literal_total = sum(len(entries) for _, _, entries in literals)
    total = len(manifest_surface) + literal_total
    # [SECURITY:S1] `name` is an identifier lifted out of a source file by
    # `ast` and `fname` is a filename off the filesystem, and both land in
    # `basis` — an envelope member the report renders. Redacted for the same
    # reason as the redacted values in the `errors` rows of
    # `read_doctrine_index`: a value this script did not author does not reach
    # a channel unbounded. Legitimate values (`EXEMPT_THINGS`,
    # `summarize-adrs.py`) round-trip to exactly themselves.
    named = ", ".join(
        f"{redact(name, quoted=False)} in "
        f"crux/scripts/{redact(fname, quoted=False)}"
        for fname, name, _ in literals)
    # A never-compiled doctrine index carries no `## Exempt ADRs` roster to
    # read, so `doctrine_exempt` is always empty in that branch. The signal
    # STAYS `computed` — `adr.governs_exempt` alone is still a real read —
    # but the basis says so, because the roster half of the union was never
    # attempted rather than attempted and found empty. Two sources take no
    # serial comma; three do, and the compiled path keeps its bytes.
    sources = (f"`adr.governs_exempt` in {tree}/manifest.yml and "
               if doctrine_never_compiled else
               f"`adr.governs_exempt` in {tree}/manifest.yml, the `## Exempt ADRs` "
               f"roster in {tree}/adrs/doctrine/index.md, and ")
    doctrine_note = (
        f"; the `## Exempt ADRs` roster was not read, because "
        f"{tree}/adrs/doctrine/index.md and its _meta.json are both absent"
        if doctrine_never_compiled else "")
    return _record(
        "carve_out_count", "computed", total,
        f"{sources}"
        f"{len(literals)} module-level EXEMPT* "
        f"literal(s) under crux/scripts/*.py"
        + ("" if scripts_present else " (surface absent in this repo)")
        + (f" ({named})" if named else "")
        + doctrine_note,
        "deduped: `adr.governs_exempt` and the doctrine index's `## Exempt ADRs` roster "
        "are ONE surface and are counted ONCE, because the roster projects that same "
        "manifest key and counting both would double-count one fact; entries are deduped "
        "within each EXEMPT* literal and not across literals, because two literals carve "
        "out two different things",
    )


def signal_paper_only(active: dict[str, dict], doctrine: DoctrineRead | None, tree: str,
                      doctrine_never_compiled: bool = False) -> dict:
    """`True` when no doctrine rule row sourced from this ADR is run-bound.

    THE NAME OVERSTATES THE MEASUREMENT, so read the value and not the name.
    "Paper-only" suggests "decided but never implemented", and the doctrine
    `basis` column cannot support that claim: it records whether a rule
    RESOLVES TO EVIDENCE — a run-snapshot artifact binding — and deliberately
    makes no implementation claim at all. An ADR fully implemented in code,
    whose rules nobody bound to a run, reads `True` here. What the signal
    licenses is "go look at this ADR", which is all a signal ever licenses:
    the judgment is the architect's, in step 4 of the skill.

    `doctrine_never_compiled` forces the WHOLE record `unmeasurable`, rather
    than the per-ADR `null` the rowless branch below already produces. A
    fresh tree with no `adrs/doctrine/` at all has never had the chance to
    name any ADR's rules `not-run-bound` or otherwise. That is a different
    statement than "every active ADR happens to carry no doctrine row". The
    per-ADR `null` map that would otherwise result reads exactly like the
    latter. The whole-record `unmeasurable` says which one this is.

    Three more shapes force the whole record `unmeasurable`, because a
    per-ADR map computed from them would read as a measurement of rows that
    were never read. `doctrine` is None when the index was not read. A
    `doctrine` carrying failures holds a line the reader cannot account for.
    A `doctrine` with no rule table holds no rule-table header, which is this
    signal's input. A rule table with no body rows is none of these: the leg
    ran and matched nothing, so the record stays `computed` with every ADR
    null, and its basis names the zero.
    """
    index = f"{tree}/adrs/doctrine/index.md"
    if doctrine_never_compiled:
        return _record(
            "paper_only", "unmeasurable", None,
            f"{index} and {tree}/adrs/doctrine/_meta.json",
            "the doctrine index and its _meta.json are both absent: "
            f"{index} and {tree}/adrs/doctrine/_meta.json",
        )
    if doctrine is None:
        return _record(
            "paper_only", "unmeasurable", None,
            f"{index}; the index was not read",
            "the doctrine index was not read, so no rule row is known and no ADR's "
            "value is computed; the errors row names the reasons a read is refused",
        )
    if doctrine.failures:
        return _record(
            "paper_only", "unmeasurable", None,
            f"{index}; {len(doctrine.failures)} line(s) the reader cannot account "
            f"for, the first at line {doctrine.failures[0][0]}",
            "the doctrine index carries a line the reader cannot account for: a line "
            "naming an ADR rule, or carrying a basis value as a cell, that is not a "
            "rule-table row, a reconciliation row or an exempt bullet, or a malformed "
            "table line; no ADR's value is computed from a partial read, and each such "
            "line has its own errors row",
        )
    if not doctrine.rule_tables:
        return _record(
            "paper_only", "unmeasurable", None,
            f"{index}; it carries no rule table",
            "the doctrine index carries no rule table — no table header naming "
            "`basis`, or naming `handle` without a first cell of `observation` or "
            "`invariant` — and that header is this signal's input, so its absence "
            "is not read as a count of zero rule rows",
        )
    value: dict[str, bool | None] = {}
    for adr_id in active:
        basis_values = doctrine.rows.get(adr_id)
        if adr_id in doctrine.nulled or not basis_values:
            value[adr_id] = None
        else:
            value[adr_id] = all(b == NOT_RUN_BOUND for b in basis_values)
    tables, rule_rows = doctrine.rule_tables, doctrine.rule_rows
    return _record(
        "paper_only", "computed", value,
        f"the per-rule `basis` column of {index}, joined to the "
        f"{len(active)} active ADRs by rule-handle prefix; {rule_rows} rule "
        f"row{'' if rule_rows == 1 else 's'} from {tables} rule "
        f"table{'' if tables == 1 else 's'}",
        "value-domain note. An ADR with a rule row the reader could not read — a cell "
        "count unlike its header's, or a basis outside the four values — maps to null "
        "whatever its other rows say, and that row has an errors row. "
        "`true` means every doctrine rule row sourced from this ADR "
        "carries basis `not-run-bound`, which is a statement about evidence bindings and "
        "NOT a claim that the ADR is unimplemented — the basis column makes no "
        "implementation claim. And an ADR carrying NO doctrine rule row maps to null "
        "rather than to false: the "
        "doctrine index is the only surface read, so an ADR it never names is "
        "unmeasurable at the ADR level, and null is what makes those ADRs visible as a "
        "class instead of silently reading as not-paper-only; the null entries are the "
        "rowless cohort and are enumerated on their own Coverage line, so this record's "
        "`computed` verdict is not a claim about them",
    )


def signal_dormancy_days(active: dict[str, dict], entries, today: dt.date, tree: str,
                         refused: list[str] | tuple[str, ...] = ()) -> dict:
    """Days since each active ADR's newest surviving dated mention.

    `refused` is `_dated_entries`' second return value — the surfaces that were
    not read. A `null` day count means "no dated mention was READ", and when a
    surface was refused that is not the same statement as "no dated mention
    exists", so the `filter` says which it is.
    """
    excluded = sum(1 for _l, _d, ids in entries if len(ids) >= BULK_ENTRY_THRESHOLD)
    newest: dict[str, str] = {}
    for _label, date, ids in entries:
        if len(ids) >= BULK_ENTRY_THRESHOLD:
            continue
        for adr_id in ids:
            if adr_id in active and date > newest.get(adr_id, ""):
                newest[adr_id] = date
    value: dict[str, int | None] = {}
    for adr_id in active:
        date = newest.get(adr_id)
        if date is None:
            value[adr_id] = None
        else:
            value[adr_id] = (today - dt.date.fromisoformat(date)).days
    filt = (f"an entry naming {BULK_ENTRY_THRESHOLD} or more distinct ADR ids is a roster, "
            f"not a mention, and is excluded; {excluded} of {len(entries)} dated entries "
            f"were excluded on this run. The threshold is a judgment: the measured tail "
            f"above three has no gap.")
    if refused:
        # The narrowing, named. Without this sentence a `null` day count reads
        # as "no dated mention" while the mention sits in a file that was not
        # read, and the basis above says nothing about it.
        names = ", ".join(redact(p, quoted=False) for p in refused)
        filt += (f" {len(refused)} named surface(s) were refused and contribute no dated "
                 f"mention — the name did not resolve, or resolves outside the repository "
                 f"root, or the resolved name is a symlink O_NOFOLLOW refused, or the open "
                 f"failed for another reason — so a null day count here is not a claim "
                 f"that no dated mention exists: {names}.")
    return _record(
        "dormancy_days", "computed", value,
        f"dated entries in {tree}/log.md, the monthly files under {tree}/journal/, and the "
        f"run snapshots under {tree}/promptbooks/runs/, measured against {today.isoformat()}",
        filt,
    )


def signal_friction_citations(root: Path, docs: Path, tree: str,
                              friction_from: str | None) -> dict:
    journal = docs / "journal"
    journal_files = sorted(p for p in journal.glob("*.md")
                           if p.name != "index.md") if journal.is_dir() else []

    # Per rule:journal-friction-line, a `Friction:` line — never a `### `
    # heading — is the countable marker, and only over entries dated at or
    # after the recorded adoption date. `_fenced_entries` is the shared split:
    # each heading is paired with the body lines that follow it up to the next
    # heading, and every line inside a ``` fence is marked as content. A
    # `Friction:` line before the first heading belongs to no entry and is
    # dropped there.
    friction_count = 0
    any_at_or_after = False
    any_before = False
    for path in journal_files:
        text = _read_contained(root, path)
        if text is None:
            continue
        for head, lines in _fenced_entries(text):
            m = LOG_HEADING.match(head)
            if not m:
                continue
            date = m.group(1)
            at_or_after = friction_from is not None and date >= friction_from
            if friction_from is not None:
                if at_or_after:
                    any_at_or_after = True
                else:
                    any_before = True
            for line, fenced in lines:
                if fenced:
                    continue  # a quotation of the grammar, not a citation
                fm = re.match(r"^Friction:\s*(.*)$", line)
                if not fm:
                    continue
                if not fm.group(1).strip():
                    continue  # empty remainder: a contract violation, skipped
                if at_or_after:
                    friction_count += 1

    runs = docs / "promptbooks" / "runs"
    run_files = sorted(runs.glob("**/*.yaml")) if runs.is_dir() else []
    with_notes_key, notes = 0, 0
    for path in run_files:
        text = _read_contained(root, path)
        if text is None:
            continue
        if re.search(r"(?m)^notes\s*:", text):
            with_notes_key += 1
            if _yaml_top_value(text, "notes"):
                notes += 1

    whats_next = docs / "whats_next.md"
    dismissals, has_dismissed_block = 0, False
    if whats_next.is_file():
        whats_next_text = _read_contained(root, whats_next)
        if whats_next_text is not None:
            fm = _frontmatter(whats_next_text) or ""
            idx = fm.find("dismissed:")
            if idx >= 0:
                has_dismissed_block = True
                block = fm[idx:]
                for entry in re.split(r"(?m)^\s*-\s*id:", block)[1:]:
                    if ADR_ID.search(entry):
                        dismissals += 1

    # forge-skill writes its log under the runtime's local skills directory:
    # `.claude/skills` in Claude Code, `.agents/skills` in Codex, and
    # `.opencode/skills` in OpenCode, which also reads the singular
    # `.opencode/skill`. A tree driven from more than one runtime carries more
    # than one log, so the leg is a union over every log present.
    forge_logs = [root / rel / "forge-log.md" for rel in LOCAL_SKILLS_DIRS]
    # PRESENCE IS A SUCCESSFUL CONTAINED READ, never `is_file()`. `is_file()`
    # FOLLOWS a symlink, so a forge log symlinked out of the root reported the
    # leg "present" while `_read_contained` refused it and the count silently
    # dropped the entries that log carries. The predicate now agrees with the
    # read: a log that was not read is an absent leg.
    present_logs = [p for p in forge_logs if p.is_file()]
    forge, read_logs = 0, 0
    for forge_log in present_logs:
        forge_text = _read_contained(root, forge_log)
        if forge_text is None:
            continue
        read_logs += 1
        for line in _lines(forge_text):
            m = FORGE_ENTRY.match(line)
            if m and m.group(1) in {"fallback", "escalated"}:
                forge += 1

    legs = [
        (f"journal friction lines (`Friction:` lines across {tree}/journal/*.md, dated at or "
         f"after the recorded adoption date)",
         friction_from is not None and any_at_or_after, friction_count),
        (f"run-snapshot notes ({tree}/promptbooks/runs/**/*.yaml; {with_notes_key} snapshot(s) "
         f"carry a `notes` key, {notes} of them non-empty)", bool(run_files), notes),
        (f"ADR references in {tree}/whats_next.md dismissals", has_dismissed_block, dismissals),
        ("fallback-or-escalated entries in forge-log.md under the local skills directory "
         "(" + ", ".join(LOCAL_SKILLS_DIRS) + ")", read_logs > 0, forge),
    ]
    present = "; ".join(f"{name} — {'present' if ok else 'absent'}" for name, ok, _ in legs)
    union_basis = f"a union over four legs: {present}"

    if friction_from is None:
        basis = (f"{union_basis}; no adoption date recorded: {tree}/manifest.yml carries no "
                 f"`journal.friction_line_from` key under `journal:`")
        return _record(
            "friction_citations", "unmeasurable", None, basis,
            "the tree records no adoption date for rule:journal-friction-line, so friction "
            "lines cannot be counted against a boundary",
        )

    if not any_at_or_after:
        basis = (f"{union_basis}; recorded adoption date: {friction_from} "
                 f"({tree}/manifest.yml `journal.friction_line_from`)")
        return _record(
            "friction_citations", "unmeasurable", None, basis,
            f"the queried window lies wholly before the recorded adoption date {friction_from}: "
            f"no journal entry is dated at or after it",
        )

    basis = (f"{union_basis}; recorded adoption date: {friction_from} "
             f"({tree}/manifest.yml `journal.friction_line_from`)")
    if any_before:
        basis += ("; truncated: entries dated before the adoption date were excluded "
                  "from the count")
    return _record("friction_citations", "computed", sum(n for _, _, n in legs), basis, None)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _render_value(value) -> str:
    """One envelope `value` member, rendered for the TABLE lane.

    THE KEY IS MINED CONTENT AND IS RENDERED AS SUCH. The value went through
    `json.dumps` and the key through a bare f-string, and the three ADR-keyed
    signals — `amendment_fan_in`, `paper_only`, `dormancy_days` — key on the
    verbatim `id:` scalar of an ADR's frontmatter, which `read_active_adrs`
    lifts with `re` and never checks against the `ADR-\\d{4}` grammar the rest
    of this file uses. So an ADR file's author chose every byte of that key,
    and `render_table` wrote it to a terminal: measured, `\\x1b[2J` cleared the
    screen and `\\x1b[31m...\\x1b[0m` coloured forged text on all three signals.

    `redact(k, quoted=True)` and not `quoted=False`, for the reason
    `_prep_key` records twenty lines up — the same choice, on the same kind of
    value, in the same module. Unquoted, the redaction note lands INSIDE the
    `k=v` pair with nothing marking where the key ends, so the key and the
    sentence describing it read as one run of text. Quoted, `repr` delimits
    the key and the note sits outside it. The delimiting also closes the
    PRINTABLE half of the class, which the redaction alone does not touch: a
    key spelling `=` and `, ` — the sample's own structure — added a pair the
    mapping never held, and a forged `ADR-9999=0` is a row the architect goes
    and looks at. Channel structure is the channel's job, per `untrusted`'s
    own docstring, and here the quotes are that job done.

    A benign id renders `'ADR-0001'=3`: `repr`'s quotes are the delimiter and
    are not a redaction note. The JSON lane keeps the RAW key, escaped by
    `json.dumps` and unbounded — the residue this module's header docstring
    already names.
    """
    if value is None:
        return "null"
    if isinstance(value, dict):
        sample = ", ".join(f"{redact(k, quoted=True)}={json.dumps(v)}"
                           for k, v in list(value.items())[:3])
        more = len(value) - 3
        return f"{len(value)} entries" + (f"; {sample}" + (f", +{more} more" if more > 0 else "")
                                          if sample else "")
    return json.dumps(value)


def render_table(envelope: dict) -> str:
    out = [f"active_adrs: {envelope['active_adrs']}", ""]
    for rec in envelope["signals"]:
        out.append(f"{rec['signal']}")
        out.append(f"  verdict: {rec['verdict']}")
        out.append(f"  value:   {_render_value(rec['value'])}")
        out.append(f"  basis:   {rec['basis']}")
        out.append(f"  filter:  {rec['filter'] if rec['filter'] is not None else 'null'}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def _doctrine_never_compiled(doctrine_dir: Path, index: Path, meta: Path) -> bool:
    """True only for the two never-compiled shapes; False for every other.

    The two shapes: `os.lstat` raises `FileNotFoundError` for `doctrine_dir`;
    or `doctrine_dir` is a real directory, not a symlink, and `os.lstat`
    raises `FileNotFoundError` for both `index` and `meta`. The check fails
    closed. A symlink at `doctrine_dir`, a non-directory there, any other
    `OSError`, or any entry at either leaf returns False. The caller then
    takes the guarded read, which reads the index or reports an error.
    """
    try:
        mode = os.lstat(doctrine_dir).st_mode
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not stat.S_ISDIR(mode):
        return False
    for leaf in (index, meta):
        try:
            os.lstat(leaf)
        except FileNotFoundError:
            continue
        except OSError:
            return False
        return False
    return True


def build(root: Path, today: dt.date) -> tuple[dict, list[dict]]:
    tree = _tree_name(root)
    docs = root / tree
    adrs = docs / "adrs"
    errors: list[dict] = []

    active = read_active_adrs(root, adrs, errors)

    # A doctrine index `_read_contained` refuses — for any of its four reasons
    # — takes the SAME branch as a compiled-then-deleted one: both leave
    # `paper_only` and `carve_out_count` unable to read it, and the
    # distinction between those two is not worth a second error shape.
    #
    # A NEVER-COMPILED doctrine index is a THIRD shape and is not an error.
    # `_doctrine_never_compiled` names its two exact shapes and fails closed
    # on everything else, which then takes the guarded read below.
    doctrine_dir = adrs / "doctrine"
    doctrine = doctrine_dir / "index.md"
    doctrine_meta = doctrine_dir / "_meta.json"
    doctrine_never_compiled = _doctrine_never_compiled(doctrine_dir, doctrine,
                                                       doctrine_meta)
    doctrine_read: DoctrineRead | None = None
    if doctrine_never_compiled:
        doctrine_exempt = []
    else:
        # On 3.13 `is_file()` re-raises a `PermissionError` from `stat`, such as
        # a mode-000 `adrs/doctrine`; 3.14 returns False. Either way the shape
        # is a refusal, so it takes the errors row below and never exits 2.
        try:
            doctrine_is_file = doctrine.is_file()
        except OSError:
            doctrine_is_file = False
        doctrine_text = _read_contained(root, doctrine) if doctrine_is_file else None
        if doctrine_text is not None:
            doctrine_read = read_doctrine_index(doctrine_text, doctrine.name, errors)
            doctrine_exempt = doctrine_read.exempt
        else:
            doctrine_exempt = []
            errors.append({"input": f"{tree}/adrs/doctrine/index.md",
                           "problem": "the doctrine index was not read. It is absent "
                                      "while adrs/doctrine is not a real directory or "
                                      "adrs/doctrine/_meta.json exists, or it is not a "
                                      "regular file, does not resolve, resolves outside "
                                      "the repository root, or cannot be opened. "
                                      "paper_only and carve_out_count cannot be "
                                      "computed from it"})

    # Same rule for the tree manifest: a refused read takes the absent-file
    # branch, so `adr.governs_exempt` and `journal.friction_line_from` are
    # unavailable together rather than each getting its own refusal shape.
    manifest = docs / "manifest.yml"
    manifest_text = _read_contained(root, manifest) if manifest.is_file() else None
    if manifest_text is not None:
        governs_exempt = read_governs_exempt(manifest_text)
        friction_from = read_journal_friction_from(manifest_text)
    else:
        governs_exempt = []
        friction_from = None
        errors.append({"input": f"{tree}/manifest.yml",
                       "problem": "the tree manifest is missing; `adr.governs_exempt` "
                                  "cannot be read"})

    scripts_dir = root / "crux" / "scripts"
    scripts_present = scripts_dir.is_dir()
    literals = read_exempt_literals(root, scripts_dir)
    entries, refused_surfaces = _dated_entries(root, docs)

    envelope = {
        "active_adrs": len(active),
        "signals": [
            signal_amendment_fan_in(active, tree),
            signal_carve_out_count(governs_exempt, doctrine_exempt, literals, tree,
                                   scripts_present, doctrine_never_compiled),
            signal_paper_only(active, doctrine_read, tree, doctrine_never_compiled),
            signal_dormancy_days(active, entries, today, tree, refused_surfaces),
            signal_friction_citations(root, docs, tree, friction_from),
            # The three delivery signals are APPENDED, so the five above keep
            # the positions their existing readers index by.
            signal_release_cadence(root, tree, errors),
            signal_schema_growth(root, docs, tree),
            signal_gate_count(root, tree),
        ],
    }
    return envelope, errors


def _environment_error(exc: Exception) -> int:
    """Report an environment failure on stderr and return the exit-2 code.

    [SECURITY:S1] The composed message is redacted at MESSAGE_LIMIT rather
    than LIMIT: its untrusted parts were already bounded where they were
    interpolated, and the value bound would cut a real two-sentence
    containment refusal in half. What is still wanted here is the
    control-character replacement, so an unrouted path cannot forge a line on
    stderr. Shared posture with generate-reviews-index.py — same lane, same
    message shape, each naming its own script. Change one, change all.
    """
    sys.stderr.write(
        f"adr-signals: {type(exc).__name__}: "
        f"{redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Compute the eight mechanical signals of the decision review.")
    ap.add_argument("--repo-root", required=True,
                    help="the repository root holding the documentation tree")
    ap.add_argument("--json", action="store_true",
                    help="emit the JSON envelope on stdout instead of the table")
    ap.add_argument("--today", default=None, metavar="YYYY-MM-DD",
                    help="reference date for dormancy_days (default: the system date)")
    args = ap.parse_args(argv)

    root = Path(args.repo_root).resolve()
    try:
        # INSIDE the try. `_tree_name` reads `.bionic.yml` and raises
        # `BionicConfigError` on a malformed one or on a `docs_dir` that
        # resolves outside the repo root. Outside the try it escaped as an
        # uncaught traceback — exit 1 with EMPTY stdout, which is neither
        # documented lane: exit 1 carries partial JSON on stdout and exit 2
        # carries a message. The traceback also printed absolute filesystem
        # paths and the raw `docs_dir` through an unrouted `{value!r}`.
        # Mirrors generate-reviews-index.py's handler.
        tree = _tree_name(root)
    except Exception as exc:  # unreadable config, bad encoding — env error
        return _environment_error(exc)
    adrs = root / tree / "adrs"
    if not adrs.is_dir():
        sys.stderr.write(
            f"adr-signals: not a crux repo root — no adrs directory at "
            f"{redact(str(adrs), quoted=False)}\n")
        return 2

    if args.today is None:
        today = dt.date.today()
    else:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            sys.stderr.write(
                f"adr-signals: --today must be an ISO calendar date, got "
                f"{redact(args.today, quoted=False)}\n")
            return 2

    try:
        # `build` calls `_tree_name` a second time, so it carries the same
        # lane; an unreadable ADR or a bad encoding lands here too.
        envelope, errors = build(root, today)
    except Exception as exc:
        return _environment_error(exc)
    if errors:
        payload = dict(envelope)
        payload["errors"] = errors
        print(json.dumps(payload, sort_keys=True))
        return 1
    if args.json:
        print(json.dumps(envelope, sort_keys=True))
    else:
        sys.stdout.write(render_table(envelope))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
