#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml>=6.0",
# ]
# ///
# `httpx` is REACHED, not called: this script reads the survey-receipt leg of
# the summaries input domain, which resolves batch state through
# `survey_sheet`, which imports `crux.arch.recover` and so pulls the `crux`
# package, whose council module imports the LLM router, which imports httpx.
# Declaring only PyYAML passes in the dev venv, where httpx is ambient, and
# fails under `uv run --no-project` in the release gate — the only place it is
# tested. `summarize-adrs.py` and `compile-doctrine.py` carry the same
# declaration for the same reason.
"""lint-governs-references.py — the citation linter.

Per ADR-0085 Decision 2 (governs blocks), its coexistence follow-on
ADR-0086, and ADR-0099 (the `rule:<slug>` citation form), an ADR frontmatter
`governs` block projects into an in-memory summaries resolver
(`summaries_projection.build_resolver`) and a pair of slug maps
(`summaries_projection.live_and_retired_slugs`). This linter checks that every
*rule handle* and every *rule citation* found in a scanned source resolves.
It writes nothing — it is a linter, not a regenerator, and is deliberately
NOT named `generate-*.py` so it carries no roster row in the
regenerative-outputs table.

This script imports neither `yaml` nor `httpx` itself, and reaches both
through `summaries_projection.py` — PEP 723 inline metadata applies only to
the script `uv run` is invoked on, never to a module that script imports, so
every transitive dependency has to be declared here. PyYAML is reached by the
ADR-frontmatter and run-snapshot parsing. `httpx` is reached by
`observations_source`, whose survey-receipt leg imports `survey_sheet` and so
pulls the `crux` package; the comment beside the block above traces that
chain. Both are declared, which is what puts this script in lock-step with
`summarize-adrs.py` and `compile-doctrine.py` — the claim of lock-step is
worth nothing on its own, and an undeclared reach is only ever caught in a
clean environment, never in the dev venv where both are ambient.

Three token classes, told apart by shape alone (ADR-0099 clause 1):

  - a DOCUMENT reference: `ADR-NNNN` or `OBS-NNNN` NOT followed by `/[a-z]`
    (regex: ``(?:ADR|OBS)-\\d{4}(?!/[a-z])``). They are IGNORED by this
    linter, permanently — they must never break. Measured on 2026-09-01
    against this checkout, the default scope holds 1,317 of them across its
    350 files; the whole repository under the same extension allowlist holds
    14,184. Both numbers are reproducible by running `find_doc_refs` over
    `resolve_scope(root, [])` — the point of stating them is the ORDER of
    magnitude the ignore rule protects, not the exact value.
  - a RULE HANDLE: `ADR-NNNN/handle-slug` or `OBS-NNNN/handle-slug`, where
    the slug starts with a lowercase LETTER (regex:
    ``(?:ADR|OBS)-\\d{4}/[a-z][a-z0-9-]*``). Every such token found in a
    scanned source MUST be a key in the in-memory summaries resolver, built
    fresh on every run from `collect_records` + `build_resolver` over the ADRs
    directory UNION the ratified observation records — never written to disk.
  - a RULE CITATION: `rule:<slug>` (regex: ``rule:[a-z][a-z0-9-]*``) — `rule:`
    followed by a lowercase letter, then lowercase letters, digits and
    hyphens. The token ends at the first character outside that set, so
    `rule:<slug>` with angle brackets is a NON-token: it is the placeholder
    authors write for a rule that does not exist yet. A YAML `rule: "..."`
    key is likewise a non-token (a space follows the colon).

  Both ledger prefixes are matched because the resolver holds ONE namespace
  across both sources (docs/AGENTS.md §17.1: an observation handle is
  namespaced `OBS-NNNN/<rule-slug>`). A matcher that saw `ADR-` alone could
  not see an observation-derived citation at all, so an unresolvable one
  would pass silently — a false green rather than a narrower check.

  The doc-ref and handle regexes are deliberately symmetric and jointly
  exhaustive over any `(ADR|OBS)-NNNN(/...)?` token: the handle regex fires
  exactly when a `/` is followed by a lowercase letter, and the doc-ref
  regex's negative lookahead fires in every other case (no slash at all, or
  a slash followed by anything else — most importantly a digit). Requiring a
  letter-led slug is what makes an anchored rule-handle citation
  unambiguously distinguishable from the digit-list shorthand
  ("ADR-0003/0004/0007"); it is a discrimination rule the linter needs, not a
  stylistic narrowing of some frozen encoding.

Resolution of a `rule:<slug>` token (ADR-0099 clause 6), via the data layer
`live_and_retired_slugs(records)` — never reimplemented here:

  - slug in `slugs` (live slug -> its one live handle): the handle's resolver
    row then decides, through the SAME `_row_resolves` predicate the handle
    leg applies. A resolving row is no finding. A row present but
    `unreviewed` is a finding, `reason: "unreviewed"`, naming the handle —
    ADR-0088 clause 6 withholds a rule whose signed receipt no longer covers
    its live text, and a citation may not resolve where its own handle does
    not. That divergence was a red-to-green flip: ADR-0099 moves every
    human-facing citation onto this leg, so an unchecked leg here means a rule
    edited out from under its signature keeps every citation of it green.
  - slug in `retired_slugs` (retired slug -> the SORTED list of live handles
    that displaced it): a finding, `reason: "retired"`, whose message names
    every displacing rule as a `rule:<slug>` citation (derived from
    `slug_of()` over each displacing handle). The list is legitimately empty
    when every displacer was itself later retired; the message then says so
    rather than printing an empty list.
  - neither: a finding, `reason: "unknown"`.

  A citation claims the code follows a rule; a retired rule makes that claim
  stale, and the failure naming its successor is the cost of the retirement
  made visible where the citation lives.

Default scan scope (ADR-0099 clause 6):

  With no `--path`, the scope is two roots: `<repo_root>/crux` (the plugin
  source) and the documentation tree (`summaries_projection.resolve_tree`;
  `bionic/` here). A root that does not exist is not walked — a downstream
  tree has no `crux/` — and the walked roots are reported under `roots`.
  `--path` narrows and overrides: a named file is scanned regardless of its
  extension, a named directory is walked with the extension allowlist and
  the directory-name exclusions but WITHOUT the prefix exclusions below
  (naming an ADR body explicitly means lint it).

  Extension ALLOWLIST (`SCAN_EXTENSIONS`), never a denylist: `.md`, `.tmpl`,
  `.py`, `.yaml`, `.yml`, `.json`, `.toml`, `.sh`.

  Directory names excluded anywhere in the walk (`EXCLUDED_DIR_NAMES`):
  `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `.pytest_cache`,
  `.ruff_cache`, `.mypy_cache`, `.tox`, `dist`, `build`, `htmlcov`, `.cache`.
  `.cache` is in that set because `crux/scripts/tests/arch-corpus/.cache/`
  holds ten cloned third-party repositories — 631 allowlisted files, a
  1.13 MB YAML, ten nested `.git` directories, and four dangling symlinks.
  The eleven names the security design first named did not cover it.

  Tree-relative excluded prefixes (`TREE_EXCLUDED_PREFIXES`, relative to the
  resolved `<docs_dir>`) and plugin-relative ones
  (`PLUGIN_EXCLUDED_PREFIXES`, relative to `crux/`) carry their reasons as
  comments beside each entry. The unifying reason for every regenerated
  entry: a projection makes no claim of its own, so what gets linted is its
  source, never the projection. The unifying reason for every dated entry:
  a dated record may cite a rule since retired, and that is history, not a
  stale claim.

Two honesty notes beside the exclusion set:

  (a) On `scripts/tests/`. Excluding the test trees is an AMENDMENT of
      ADR-0099 clause 7(b) ("an `ADR-NNNN/slug` handle in code fails the
      lint"), not an interpretation of it. The ruling's record put the
      exemption at 796 of the 800 `ADR-NNNN/slug` handles in the repository.
      Measured on 2026-09-01 against this checkout, each figure with the
      scope that produces it. Apply the dated and regenerated exclusions and
      lift both code exemptions: `crux/` plus the tree hold 316 handle tokens
      across 567 files. `crux/scripts/tests/` holds 309 of them, and note
      (b)'s two named modules hold 4. The shipped scope is therefore 350
      files carrying 3 handles, all three in one brief, and all three
      resolve. Two of three council seats read the test-tree exclusion as an
      amendment, and the owner ruled it in-cycle over that dissent.

  (b) On the two named modules. `crux/scripts/summarize-adrs.py` carries
      three handle tokens and `crux/scripts/survey_sheet.py` carries one.
      Two of the four resolve; the other two, at `summarize-adrs.py:212` —
      an alias on `OBS-0001` and a rule on `ADR-0050`, both bearing the slug
      `foo` — are illustrative DATA in a comment explaining why an alias
      cannot collide with a rule, not citations. (They are not spelled as
      handles here because this file is in scope and would lint itself.)
      They are covered by naming those two modules in the
      exclusion set. This is deliberately NOT a blanket "all code is exempt"
      carve-out, and the four tokens are NOT rewritten. The exemption also
      stops checking the two handles in those files that DO resolve.

R4 — the empty-scope rule, and what it is not:

  An empty resolved SCOPE (zero files after exclusions) is a HARD FAILURE:
  exit 1 with `"error": "empty_scope"` on stdout. The old behaviour returned
  `[]` and passed vacuously, which is the fail-open this whole design exists
  to close. Zero `rule:` TOKENS inside a non-empty scope is a LEGITIMATE
  PASS: the citation form is greenfield by the decision's own premise. Do
  NOT add an anti-vacuity guard that fails on zero tokens, and do not plant
  citations to manufacture work for the gate. `rule_tokens` in the output
  lets a consumer tell "checked, none cited" from "checked, all resolved".

The security design (D5) — every item is a requirement:

  Trust boundary: a trusted checkout on a CI or developer machine. The
  linter reads files an attacker would already need write access to the
  repository to plant. Under that assumption the resolve-then-open race is
  low severity; the mitigation is opening with an `O_NOFOLLOW` descriptor
  and `fstat`-ing it, accepting only a regular file, so the file checked is
  the file read.

  - Symlinks are not followed: `os.walk(..., followlinks=False)`, AND an
    entry that is itself a symlink (file or directory) is skipped. A root
    that is itself a symlink is refused, since `os.walk` would follow it.
  - Non-regular files are REFUSED via `lstat` (`reason: not_regular`). A
    FIFO with an allowlisted extension blocks forever on open and hangs CI;
    it is never opened. Refusal, not a skip.
  - Repo-root containment: the candidate and the repo root are both fully
    resolved (`Path.resolve()`) before comparison, compared with
    `os.path.commonpath` — never `startswith`, which would let `/repo-evil`
    pass as inside `/repo`. A path resolving outside the root is REFUSED
    (`reason: outside_root`); `--path` gets no exemption. Normalization
    policy: `resolve()` follows symlinks and collapses `..`; it applies no
    case folding and no Unicode normalization. On a case-insensitive
    filesystem (APFS, HFS+) or one that folds NFC/NFD, two spellings of one
    path therefore compare UNEQUAL and the candidate is refused — the check
    can false-trigger toward refusal there, never toward acceptance. In the
    default scope every candidate is derived from the root by `os.walk`, so
    the spellings agree and no false trigger occurs; only an absolute
    `--path` spelled in another case or normalization form is affected.
    What the check does not guarantee: a hard link inside the root to a
    file outside it is indistinguishable from a regular file by every
    `lstat` measure and is accepted under the trust boundary above.
  - Resource bounds as named constants: `MAX_FILE_BYTES` (4 MiB) and
    `MAX_SCANNED_FILES` (5000). Measured on 2026-09-01: the default scope
    is 433 files; the largest in-scope file is 203,850 bytes
    (`bionic/research/sources/claude-code-hooks-reference.md`). A breach is
    a reported refusal (`too_large`, `too_many_files`), never a silent skip.
  - A bound breach, a containment refusal, a non-regular-file refusal, a
    decoding refusal, or a `--path` that names nothing (`not_found`) EXITS
    1. Reported-at-exit-0 reproduces the fail-open.
  - Decoding failure is fail-closed: a non-UTF-8 file that passed the
    allowlist is refused (`reason: not_utf8`). Never skipped, never
    `errors='replace'` — that corrupts the byte stream the matcher reads and
    can manufacture or mask a token.
  - Diagnostics carry no file content. A finding reports path, line number,
    token and reason; it NEVER echoes the surrounding line. A linter that
    quotes file content into a CI log turns a citation check into an
    exfiltration surface.

JSON contract (stdout; findings and refusals sorted for determinism):

  - `scanned`: the resolved paths read, sorted.
  - `roots`: the roots walked (default scope), or the `--path` arguments as
    given, sorted.
  - `unresolved`: rule-handle findings, `{path, handle, reason}` with
    `reason` in `unresolved | unreviewed` (unchanged from ADR-0088).
  - `rule_findings`: citation findings, `{path, line, token, reason,
    message}` with `reason` in `unknown | retired`.
  - `rule_tokens`: the number of `rule:` tokens seen across `scanned`.
  - `refusals`: `{path, reason, detail}` with `reason` in `not_regular |
    outside_root | too_large | too_many_files | not_utf8 | not_found |
    symlink_root`. `detail` is a fixed sentence per reason, never content.
  - `error`: present only as `"empty_scope"`.
  - `validation_errors`: the governs-block validation envelope
    (`sp.GovernsValidationError`), emitted INSTEAD of the keys above.

Exit codes: 0 clean · 1 findings, refusals, an empty scope, or a
validation error (JSON on stdout) · 2 crash (stderr).
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summaries_projection as sp  # noqa: E402

# The shared bound-and-redact helper, reached through the module that already
# binds it by path. Every token this linter echoes into a CI log comes out of
# a scanned file, and a scanned file is untrusted by the same reasoning the
# rest of the lane applies: 58 in-scope files are captures of external pages.
# An unbounded token round-trips into `token`, `message` and `handle` — a
# 200,000-character match measured at 601 KB of JSON, a 3,000,000-character
# one at 6 MB. `quoted=False` is the bare-`{}` form, byte-identical to the
# raw value for any token that is short and wholly printable, which every
# legitimate one is.
redact = sp.redact

# Symmetric, jointly-exhaustive pair (see module docstring): a handle's slug
# must start with a lowercase letter, so the doc-ref lookahead need only
# exclude "/" + letter, not "/" + [letter-or-digit].
DOC_REF_RE = re.compile(r"(?:ADR|OBS)-\d{4}(?!/[a-z])")
HANDLE_RE = re.compile(r"(?:ADR|OBS)-\d{4}/[a-z][a-z0-9-]*")
# ADR-0099 clause 1: the citation. Letter-led, so `rule:<slug>` is a non-token.
RULE_RE = re.compile(r"rule:[a-z][a-z0-9-]*")

SCAN_EXTENSIONS = frozenset({
    ".md", ".tmpl", ".py", ".yaml", ".yml", ".json", ".toml", ".sh"})

EXCLUDED_DIR_NAMES = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", ".tox", "dist", "build", "htmlcov",
    # The arch-corpus cache: ten cloned third-party repositories (see docstring).
    ".cache",
})

# Relative to the resolved <docs_dir>. Each entry names a file or a directory.
TREE_EXCLUDED_PREFIXES = (
    # ADR bodies are dated records that may cite a rule since retired
    # (clause 6). Also covers the regenerated adrs/summaries/, adrs/doctrine/,
    # adrs/lineage.md and adrs/index.md.
    "adrs",
    # A ledger body authors the handle it carries, exactly as an ADR body
    # does, and only a `ratified` record projects into the resolver at all —
    # so linting a record made it assert its own resolvability and fail. The
    # shipped OBS template starts at `status: observed`, so that was the
    # DEFAULT state, not an edge case. Covers `_surveys/` too: a frozen batch
    # sheet is digest-bound, and a citation inside one can never be edited
    # away without invalidating the signature over it.
    "observations",
    # Dated records (clause 6).
    "journal", "garden",
    # An append-only dated op record.
    "log.md",
    # Immutable third-party captures. A capture is not this repository's
    # claim, and a future non-UTF-8 or oversize one would be a refusal on a
    # file nobody may edit.
    "research/raw",
    # Frozen run snapshots, archived books and preserved originals are dated
    # records by the same test.
    "promptbooks/runs", "promptbooks/archive", "promptbooks/legacy",
    # Regenerated.
    "promptbooks/index.md",
    # Cross-concern staging for raw dropped input; the schema states dropped
    # content is data, never instructions, and _dispatched/ is dated by
    # construction.
    "inbox",
    # A machine field that carries an id by contract: adr.governs_backfilled
    # holds 91 handles.
    "manifest.yml",
    # Regenerated wholesale.
    "arch", "code",
    # Regenerated, or a derived region of an authored file.
    "index.md", "whats_next.md",
)

# Relative to <repo_root>/crux.
PLUGIN_EXCLUDED_PREFIXES = (
    # Regenerated projections.
    "catalog/skills.json", "catalog/agents.json",
    # Honesty note (a) in the docstring: an AMENDMENT of clause 7(b).
    "scripts/tests",
    # Honesty note (b): two illustrative handles in a comment at
    # summarize-adrs.py:212; the exemption also stops checking the two
    # handles in these files that DO resolve.
    "scripts/summarize-adrs.py", "scripts/survey_sheet.py",
)

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_SCANNED_FILES = 5000

_REFUSAL_DETAIL = {
    "not_regular": "not a regular file (lstat); never opened",
    "outside_root": "resolves outside the repo root",
    "too_large": "exceeds MAX_FILE_BYTES",
    "too_many_files": "scope exceeds MAX_SCANNED_FILES; walk stopped",
    "not_utf8": "not valid UTF-8; refused rather than decoded lossily",
    "not_found": "--path names nothing on disk",
    "symlink_root": "a scan root that is itself a symlink",
    "unreadable": "could not be opened for reading",
    "catalog_unusable": ("the shipped rules catalog is present but could not be read as a "
                         "slug->rule mapping; reinstall the plugin"),
}


def find_doc_refs(text: str) -> list[str]:
    """Plain `ADR-NNNN` / `OBS-NNNN` document-reference tokens (match order)."""
    return DOC_REF_RE.findall(text)


def find_handles(text: str) -> list[str]:
    """`(ADR|OBS)-NNNN/handle-slug` rule-handle tokens in `text` (match order)."""
    return HANDLE_RE.findall(text)


def find_rule_tokens(text: str) -> list[str]:
    """`rule:<slug>` citation tokens in `text` (match order)."""
    return RULE_RE.findall(text)


def _refusal(path: Path | str, reason: str) -> dict:
    return {"path": str(path), "reason": reason, "detail": _REFUSAL_DETAIL[reason]}


def _contained(root_resolved: Path, candidate_resolved: Path) -> bool:
    """True iff `candidate_resolved` is under `root_resolved` — both already
    fully resolved; `commonpath`, never `startswith`."""
    try:
        return os.path.commonpath(
            [str(root_resolved), str(candidate_resolved)]) == str(root_resolved)
    except ValueError:  # different drives (Windows)
        return False


class _Scope:
    """Accumulates the scan set under the security design: every candidate
    passes symlink, regular-file, containment and bound checks BEFORE it is
    admitted, and every failure is a refusal, never a skip."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files: set[Path] = set()
        self.refusals: list[dict] = []
        self.overflow = False

    def admit(self, candidate: Path) -> None:
        if self.overflow:
            return
        if candidate.is_symlink():
            return  # skipped, by design — never followed
        try:
            st = os.lstat(candidate)
        except FileNotFoundError:
            self.refusals.append(_refusal(candidate, "not_found"))
            return
        if not stat.S_ISREG(st.st_mode):
            self.refusals.append(_refusal(candidate.resolve(), "not_regular"))
            return
        resolved = candidate.resolve()
        if not _contained(self.root, resolved):
            self.refusals.append(_refusal(resolved, "outside_root"))
            return
        if st.st_size > MAX_FILE_BYTES:
            self.refusals.append(_refusal(resolved, "too_large"))
            return
        if resolved in self.files:
            return
        if len(self.files) >= MAX_SCANNED_FILES:
            self.refusals.append(_refusal(resolved, "too_many_files"))
            self.overflow = True
            return
        self.files.add(resolved)

    def walk(self, top: Path, excluded: frozenset[Path]) -> None:
        """Walk `top` top-down, pruning excluded directory names and every
        path in `excluded` (compared on the joined, unresolved spelling
        `os.walk` yields, which is the spelling the prefixes were built in)."""
        if top.is_symlink():
            self.refusals.append(_refusal(top, "symlink_root"))
            return
        if not top.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            here = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in EXCLUDED_DIR_NAMES
                and (here / d) not in excluded
                and not (here / d).is_symlink())
            for name in sorted(filenames):
                p = here / name
                if p.suffix not in SCAN_EXTENSIONS or p in excluded:
                    continue
                self.admit(p)
                if self.overflow:
                    return


def default_scan_roots(root: Path) -> list[Path]:
    """The two default roots: `<root>/crux` and the documentation tree."""
    return [root / "crux", sp.resolve_tree(root)]


def excluded_paths(root: Path) -> frozenset[Path]:
    """The absolute (joined, unresolved) prefix exclusions for `root`."""
    tree = sp.resolve_tree(root)
    plugin = root / "crux"
    return frozenset(
        [tree / p for p in TREE_EXCLUDED_PREFIXES]
        + [plugin / p for p in PLUGIN_EXCLUDED_PREFIXES])



# The root of the artifact the release stages, resolved from THIS file rather than
# from the caller's repository root. This script ships inside that artifact, so the
# directory holding it is the plugin root wherever the lint runs: `<repo>/crux` in
# the authoring checkout, `<stage>/crux` in a staged artifact, and the versioned
# plugin cache in an installed project.
SHIPPED_ROOT = Path(__file__).resolve().parent.parent


def is_shipped_surface(path: Path) -> bool:
    """True when `path` is part of the artifact the release stages.

    rule:citation-resolves-by-its-citing-surface — "the citing file's location
    decides which rule applies, and the rule being cited never does". A reader's
    own `<repo>/crux/` is NOT this directory unless the reader IS the authoring
    checkout, so a project cannot make its own pages shipped by naming a folder
    `crux`. The release also stages six repository-root files; none is in this
    lint's default scope, and a reader who names one with `--path` gets the
    reader-owned reading, which is the safe direction — it reports more, never
    fewer, citations.

    A path is judged by BOTH its lexical location and its resolved one, and either
    being inside the plugin root makes it shipped. Judging only the resolved path let
    a symlink sitting in the plugin's own skills directory take the reader-owned
    reading, so a reader's rule could answer for a file at a shipped location — and
    the sibling scanner in `generate-rules-catalog.py` refuses such a file outright,
    which left two readers of one boundary giving two answers. Erring toward "shipped"
    is the safe direction: it applies the stricter resolution, never the looser one.
    """
    try:
        # The PARENT is resolved and the final component is not, so the test asks
        # where the file SITS without following the file's own link. Resolving the
        # whole path followed the leaf and lost the location; leaving it unresolved
        # broke on any symlinked ancestor, which every macOS temporary directory has
        # (`/var` -> `/private/var`).
        located = path.parent.resolve(strict=False) / path.name
    except (OSError, ValueError):
        located = Path(os.path.normpath(os.path.abspath(path)))
    if _contained(SHIPPED_ROOT, located):
        return True
    try:
        return _contained(SHIPPED_ROOT, path.resolve())
    except (OSError, ValueError):
        return False


def _shipped_catalog_rules() -> tuple[dict[str, str], dict | None, bool]:
    """slug -> rule text, from the catalog the plugin ships, if it is present.

    The catalog is read from BESIDE THIS SCRIPT and from nowhere else. Reading
    `<root>/crux/catalog/rules.json` first, as this did, let a reader's own file
    at that path replace the shipped catalog outright — not merge with it — so a
    project could redefine a rule inside the plugin's own instructions by minting
    the same slug, which is the substitution
    rule:citation-resolves-by-its-citing-surface exists to prevent. In the
    authoring checkout the two paths name the same file, which is why the defect
    was invisible here and reachable in any project holding a `crux/` directory.

    Returns `(rules, refusal, present)`. ABSENT and UNUSABLE are different conditions
    and this draws the line between them, because collapsing them weakened the gate
    silently:

      absent    -> `({}, None, False)`. An older plugin or a partial checkout carries
                   no catalog, and turning this gate red for a reason the reader cannot
                   fix helps nobody. The caller falls back to the reader's projection.
      unusable  -> `({}, refusal, True)`. A file that EXISTS but is corrupt, truncated,
                   missing its `rules` key, or not a mapping is not an older plugin —
                   it is a broken install the reader CAN fix by reinstalling. Treating
                   it as "no catalog" reverted the shipped reading to reader-first and
                   disabled the collision report at once, with no diagnostic anywhere.
                   It is PRESENT, so the caller keeps the strict shipped reading and
                   the refusal gates the exit code: loud AND strict, not merely loud.
      usable    -> `(rules, None, True)`.

    `present` is what the caller keys the fallback on, NOT an empty rule map. Keying on
    emptiness made two mistakes at once: a broken install still resolved shipped
    citations against the reader's projection, and a legitimately empty catalog — the
    exact bytes this project's own regenerator emits for a plugin citing nothing — was
    refused forever for a condition nobody could fix.

    A per-entry value that is not a mapping is refused too. Tolerating it resolved a
    shipped citation to empty rule text with no diagnostic, which is a quieter version
    of the same defect one level down.
    """
    candidate = SHIPPED_ROOT / "catalog" / "rules.json"
    if not candidate.is_file():
        return {}, None, False
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        rules = data["rules"]
        if not isinstance(rules, dict):
            raise TypeError("'rules' is not a mapping")
        out: dict[str, str] = {}
        for k, v in rules.items():
            if not isinstance(v, dict):
                raise TypeError(f"catalog entry {k!r} is not a mapping")
            out[k] = v.get("rule", "")
        return out, None, True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError,
            TypeError, AttributeError):
        return {}, _refusal(candidate, "catalog_unusable"), True


def catalog_collisions(tree_slugs: dict[str, str], catalog: dict[str, str],
                       resolver: dict, authoring: bool = False) -> list[dict]:
    """Slugs the reader's projection and the shipped catalog BOTH define, with
    different text — reported wherever the lint runs.

    rule:citation-resolves-by-its-citing-surface: "A slug both sides hold is
    reported wherever the citation lint runs, because a silent substitution and
    a deliberate override are not the same act."

    Texts are compared space-folded, and identical text is not a collision. In
    the authoring checkout the catalog IS a projection of this tree, so all 44
    of its slugs are held on both sides while naming one rule reachable two
    ways. That is neither a substitution nor an override, and reporting it would
    make every authoring run red against itself. A slug whose two texts DIFFER
    is the act the clause names, and it is reported.
    """
    out: list[dict] = []
    for slug, cat_rule in sorted(catalog.items()):
        handle = tree_slugs.get(slug)
        if handle is None:
            continue
        tree_rule = (resolver.get(handle) or {}).get("rule", "")
        if " ".join(tree_rule.split()) == " ".join(cat_rule.split()):
            continue
        # In the authoring checkout the catalog is DERIVED from this very projection,
        # so differing text means the catalog is stale, not that two authorities
        # disagree. Naming it a collision misdescribed the condition and named no
        # remedy, while the regenerator's own drift gate already reports it.
        out.append({
            "slug": redact(slug, quoted=False),
            "reason": ("the shipped catalog is stale for this slug; run "
                       "generate-rules-catalog.py" if authoring else
                       "defined by both this projection and the shipped catalog"),
            "kind": "stale_catalog" if authoring else "competing_definition",
            "tree_handle": redact(handle, quoted=False),
        })
    return out


def resolve_scope(root: Path, extra: list[str]) -> tuple[list[Path], list[Path], list[dict]]:
    """`(files, roots, refusals)` for the run. With `extra` empty, the default
    scope (both roots, prefix exclusions applied). Otherwise each `--path`
    item: a file is admitted as named, a directory is walked with the
    allowlist and the directory-name exclusions only."""
    root = root.resolve()
    scope = _Scope(root)
    if not extra:
        roots = [r for r in default_scan_roots(root) if r.exists() or r.is_symlink()]
        excluded = excluded_paths(root)
        for r in roots:
            scope.walk(r, excluded)
        reported = sorted(r.resolve() for r in roots if not r.is_symlink())
    else:
        reported = []
        for item in extra:
            p = Path(item)
            if not p.is_absolute():
                p = root / p
            reported.append(p)
            # A `--path` argument IS a scan root, so a link named there is
            # refused on the same terms `scope.walk` refuses a symlinked root.
            # The default walk's silent skip is right for a candidate the walk
            # derived itself; it is wrong here, because an operator named this
            # exact path and is owed an answer about it. Skipping it listed the
            # path under `roots`, listed nothing under `scanned`, and exited 0.
            # Checked before `is_dir()`, which follows the link and would send
            # a symlinked directory down the walk branch.
            if p.is_symlink():
                scope.refusals.append(_refusal(p, "symlink_root"))
                continue
            if p.is_dir():
                scope.walk(p, frozenset())
            else:
                scope.admit(p)
    return sorted(scope.files), reported, scope.refusals


def read_source(path: Path) -> str | dict:
    """The text of `path`, or a refusal dict. Opens with `O_NOFOLLOW`, then
    `fstat`s the descriptor and accepts only a regular file within
    `MAX_FILE_BYTES`, so the file checked is the file read. Decodes strict
    UTF-8; a decoding failure is a refusal, never a lossy read.

    `O_NONBLOCK` closes the FIFO race the security design documents. `_Scope`
    already refuses a FIFO by `lstat`, but a regular file replaced by one
    between that check and this open would block the process forever. With
    the flag, the open returns immediately and the `fstat` below refuses it as
    non-regular. On a regular file the flag has no effect.

    An open failure is diagnosed by errno rather than lumped under one reason.
    The blanket `except OSError: return not_regular` was fail-closed, but its
    diagnostic lied about three distinct conditions — an unreadable file, a
    file that vanished mid-scan, and descriptor exhaustion — all of which are
    regular files. `ELOOP` keeps `not_regular` because `O_NOFOLLOW` raises it
    on a symlink, which IS precisely a non-regular file.
    """
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return _refusal(path, "not_found")
        if exc.errno in (errno.ELOOP, errno.EISDIR):
            return _refusal(path, "not_regular")
        return _refusal(path, "unreadable")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return _refusal(path, "not_regular")
        if st.st_size > MAX_FILE_BYTES:
            return _refusal(path, "too_large")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read(MAX_FILE_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > MAX_FILE_BYTES:
        return _refusal(path, "too_large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return _refusal(path, "not_utf8")


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _retired_message(token: str, displacers: list[str]) -> str:
    cites = sorted({f"rule:{sp.slug_of(h)}" for h in displacers})
    if not cites:
        return (f"{token} cites a retired rule; every rule that displaced it was "
                "itself later retired, so no live rule replaces it")
    return f"{token} cites a retired rule; displaced by {', '.join(cites)}"


def _row_resolves(row: dict | None) -> bool:
    """[SECURITY:S3] The ONE resolution predicate, applied by BOTH legs to the
    resolver row a token names.

    A row resolves when it exists and is not `unreviewed`. ADR-0088 clause 6
    makes an unreviewed entry a row that is PRESENT in `resolver.json` and does
    not resolve: its human sign-off no longer covers its live rule text, so the
    rule is withheld until it is re-reviewed.

    This lives in one function because the two legs diverging is precisely the
    vulnerability. The handle leg checked the facet from the day it existed;
    the `rule:<slug>` leg looked its slug up in `slugs` and passed, and
    `live_and_retired_slugs` walks `live_records` with no review-state filter.
    ADR-0099 then moved every human-facing citation onto that second leg — so
    editing a signed backfill entry's rule text out from under its signature
    left the handle token failing `unreviewed` while a citation of the same
    rule passed with zero findings. The gate whose whole purpose is to notice
    a rule changing under its sign-off reported green on exactly that.

    The filter is NOT pushed down into `live_and_retired_slugs`. That builder
    is also the slug-UNIQUENESS gate `signoff-survey.py` consults, and a slug
    an unreviewed rule owns is still taken: dropping it from the map would let
    a new observation claim it and manufacture a collision the moment the rule
    is re-reviewed. Resolution and uniqueness are different questions about the
    same map, and only resolution is review-gated.

    A row with no `review_state` key resolves. The facet is omitted on an
    observation row by construction (`build_resolver` states why), and an
    observation sits outside the ADR-0088 receipt machinery entirely.
    """
    return row is not None and row.get("review_state") != "unreviewed"


def find_unresolved(paths: list[Path], resolver: dict,
                    slugs: dict[str, str] | None = None,
                    retired_slugs: dict[str, list[str]] | None = None,
                    shipped_slugs: dict[str, str] | None = None,
                    ) -> tuple[list[dict], list[dict], int, list[dict]]:
    """`(handle_findings, rule_findings, rule_tokens, refusals)` across `paths`.

    A handle token fails when absent from the resolver (`reason: unresolved`
    — unknown, or dropped out under a removal tombstone) or when its row is a
    non-resolving one (`reason: unreviewed` — ADR-0088 clause 6). One entry
    per distinct (path, handle), sorted.

    A `rule:` token naming a live slug is resolved to that slug's handle and
    put through the SAME `_row_resolves` predicate, so the two legs cannot
    reach different verdicts about one rule.

    A `rule:` token fails when the rule its slug names does not resolve
    (`reason: unreviewed`, or `unresolved` for the handle absent from the
    resolver entirely), when its slug is retired (`reason: retired`, the
    message naming the displacing rules), or when the slug is unknown
    (`reason: unknown`). One entry per distinct (path, line, token), sorted.
    `rule_tokens` counts every token seen, resolving or not.

    Resolution is keyed by the CITING SURFACE, per
    rule:citation-resolves-by-its-citing-surface. `slugs` is the reader-owned
    reading (the reader's own projection first, the shipped catalog second) and
    `shipped_slugs` the shipped one (the shipped catalog first, whatever the
    reader's projection holds). Each path is read under the map its own location
    selects. `shipped_slugs` omitted means one map for every path, which is what
    a caller asking a single-surface question wants.
    """
    slugs = slugs or {}
    retired_slugs = retired_slugs or {}
    seen: set[tuple[str, str]] = set()
    handle_findings: list[dict] = []
    rule_seen: set[tuple[str, int, str]] = set()
    rule_findings: list[dict] = []
    refusals: list[dict] = []
    rule_tokens = 0
    for path in paths:
        text = read_source(path)
        if isinstance(text, dict):
            refusals.append(text)
            continue
        # The citing file's location decides which rule applies; the rule being
        # cited never does.
        here = (shipped_slugs if shipped_slugs is not None and is_shipped_surface(path)
                else slugs)
        for handle in find_handles(text):
            key = (str(path), handle)
            if key in seen:
                continue
            row = resolver.get(handle)
            if _row_resolves(row):
                continue
            seen.add(key)
            handle_findings.append({
                "path": str(path),
                "handle": redact(handle, quoted=False),
                "reason": "unresolved" if row is None else "unreviewed",
            })
        for m in RULE_RE.finditer(text):
            rule_tokens += 1
            token = m.group(0)
            slug = token[len("rule:"):]
            named = here.get(slug)
            row = resolver.get(named) if named is not None else None
            if named is not None and _row_resolves(row):
                continue
            key3 = (str(path), _line_of(text, m.start()), token)
            if key3 in rule_seen:
                continue
            rule_seen.add(key3)
            safe = redact(token, quoted=False)
            if named is not None:
                # A live slug whose rule does not resolve. The two reasons are
                # the handle leg's own words, so one condition carries one name
                # whichever token shape reported it.
                held = redact(named, quoted=False)
                if row is None:
                    reason = "unresolved"
                    message = (f"{safe} names {held}, which is absent from the "
                               "resolver")
                else:
                    reason = "unreviewed"
                    message = (f"{safe} names {held}, whose current signed "
                               "receipt does not cover its live rule text — the "
                               "rule is withheld until it is re-reviewed")
            elif slug in retired_slugs:
                reason = "retired"
                message = _retired_message(safe, retired_slugs[slug])
            else:
                reason = "unknown"
                message = f"{safe} resolves to no live or retired rule"
            rule_findings.append({
                "path": key3[0], "line": key3[1], "token": safe,
                "reason": reason, "message": message,
            })
    handle_findings.sort(key=lambda f: (f["path"], f["handle"]))
    rule_findings.sort(key=lambda f: (f["path"], f["line"], f["token"]))
    return handle_findings, rule_findings, rule_tokens, refusals


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Lint ADR-NNNN/handle-slug rule handles and rule:<slug> "
        "citations against the in-memory summaries resolver. Plain ADR-NNNN "
        "document references are always ignored."
    )
    ap.add_argument("--repo-root", default=".")
    ap.add_argument(
        "--path",
        action="append",
        default=[],
        help="File or directory to scan (repeatable). Overrides the default "
        "scope (crux/ + the documentation tree) — see module docstring.",
    )
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        adrs = sp.adrs_dir(root)
        # ADR-0088: the resolver is built WITH review state — a citation of a
        # non-resolving handle (unknown, removed, or unreviewed) fails.
        manifest = sp.read_manifest(root)
        governs_from = sp.governs_from(manifest)
        # The input domain is active ADRs UNION ratified observations
        # (docs/AGENTS.md §4, ADR-0095 requirement 4). `observations_source`
        # is THE one resolution of that half, shared with `summarize-adrs.py`
        # and `compile-doctrine.py`. Omitting it built a resolver holding zero
        # OBS handles while the on-disk resolver held three, so every
        # observation-derived citation was unresolvable — a gate reading a
        # narrower domain than the artifact it gates.
        observations = sp.observations_source(root, manifest)
        reviews = sp.read_reviews(adrs)
        records = sp.collect_records(adrs, governs_from=governs_from,
                                     observations=observations)
        # ADR-0095 requirement 4: a `decided` observation contributes no rule
        # row but keeps each of its handles in the resolver as an ALIAS onto
        # the ADR its `decided_by` names — the alias exists precisely "so a
        # citation written against the observation still resolves after
        # promotion". Omitting it built a resolver narrower than the artifact
        # this gate exists to police: a decided observation's handle resolved
        # on disk, in `resolver.json`, and failed here. The producer at
        # `summarize-adrs.py` passes the same rows into the same builder.
        alias_rows = sp.collect_observation_alias_rows(observations, records)
        resolver = sp.build_resolver(records, reviews, governs_from,
                                     alias_rows=alias_rows)
        # ADR-0099 clause 2: the slug maps AND the collision gate, one traversal.
        slugs, retired_slugs = sp.live_and_retired_slugs(records)
        # rule:citation-resolves-by-its-citing-surface. Two readings of the same
        # citation grammar, and the citing file's location picks between them:
        #
        #   shipped surface -> the shipped catalog FIRST, whatever this projection
        #                      holds, so a project cannot redefine a rule inside the
        #                      plugin's own instructions by minting the same slug;
        #   reader-owned    -> this projection FIRST and the catalog second, so the
        #                      reader's own rules keep their meaning and the plugin's
        #                      stay reachable.
        #
        # Building ONE merged map, as this did, implements neither: it gave every
        # surface the reader-first reading, so the substitution the clause forbids
        # resolved silently and the clause's central requirement had no code at all.
        catalog_rules, catalog_refusal, catalog_present = _shipped_catalog_rules()
        # This repo IS the plugin's authoring checkout when the shipped tree sits at
        # EXACTLY `<root>/crux` — the same test `generate-rules-catalog.py` applies
        # before it projects anything. Containment under root was not that test: a
        # plugin vendored at `<repo>/vendor/plugin` read as authoring here while the
        # regenerator took its surface-absent lane, so the lint named a remedy that
        # does nothing in the project it was named to.
        try:
            authoring = SHIPPED_ROOT == (root / "crux").resolve()
        except (OSError, ValueError):
            authoring = False
        catalog_slugs: dict[str, str] = {}
        for _slug, _rule in catalog_rules.items():
            _handle = f"catalog/{_slug}"
            catalog_slugs[_slug] = _handle
            # the resolver row the handle must land in, or `_row_resolves` reports the
            # slug as naming a handle absent from the resolver. The catalog carries no
            # decision-record identifier, so `source_adr` is null rather than invented.
            resolver.setdefault(_handle, {
                "authority": "prescriptive",
                "disposition": "decided",
                "provenance": "authored",
                "review_state": "shipped-catalog",
                "rule": _rule,
                "source_adr": None,
            })
        collisions = catalog_collisions(slugs, catalog_rules, resolver, authoring)
        # "whatever the reader's own projection holds" — so on a shipped surface the
        # catalog is not merely preferred, it is the ONLY source. Merging this
        # projection in behind it would let a reader's record answer for a slug the
        # catalog does not carry, which is the same substitution by a slower route.
        # The one exception is a catalog that is absent entirely: an older plugin or a
        # partial checkout leaves no shipped source to consult, and turning every
        # shipped citation red for a reason the reader cannot fix helps nobody.
        shipped_slugs = dict(catalog_slugs) if catalog_present else dict(slugs)
        reader_slugs = {**catalog_slugs, **slugs}    # this projection wins
        scan_paths, roots, refusals = resolve_scope(root, args.path)
        handle_findings, rule_findings, rule_tokens, read_refusals = find_unresolved(
            scan_paths, resolver, reader_slugs, retired_slugs,
            shipped_slugs=shipped_slugs)
    except sp.GovernsValidationError as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"lint-governs-references: {type(exc).__name__}: {exc}\n")
        return 2
    # A present-but-unusable catalog is reported rather than silently treated as
    # absent; it rides the refusals list, which already gates the exit code.
    if catalog_refusal is not None:
        refusals = refusals + [catalog_refusal]
    refused = {r["path"] for r in read_refusals}
    scanned = [str(p) for p in scan_paths if str(p) not in refused]
    refusals = sorted(refusals + read_refusals, key=lambda r: (r["path"], r["reason"]))
    payload = {
        "scanned": scanned,
        "roots": sorted(str(r) for r in roots),
        "unresolved": handle_findings,
        "rule_findings": rule_findings,
        "rule_tokens": rule_tokens,
        "collisions": collisions,
        "refusals": refusals,
    }
    # R4: an empty resolved scope is a hard failure, never a vacuous pass.
    if not scan_paths:
        payload["error"] = "empty_scope"
    print(json.dumps(payload, sort_keys=True))
    # A collision is a finding. Reporting it into the payload while exiting 0 is the
    # silent form of exactly the act the clause distinguishes from a deliberate one.
    return 1 if (handle_findings or rule_findings or refusals or collisions
                 or "error" in payload) else 0


if __name__ == "__main__":
    raise SystemExit(main())
