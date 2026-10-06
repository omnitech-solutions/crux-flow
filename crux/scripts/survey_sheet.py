#!/usr/bin/env python3
"""survey_sheet.py — the batch review sheet, its receipt, and the batch state.

The shared reader, validator, digest and state-derivation for batch
ratification (ADR-0098; the protocol's source of truth is docs/AGENTS.md
§17.5). Three callers import it and none of them re-derives any of it:

  * `scaffold-survey-sheet.py` writes a sheet with only machine cells filled;
  * `signoff-survey.py` reads the sheet, binds it by digest, and publishes;
  * `check_observations.py` and `summaries_projection.py` read the batch state
    a receipt is in, through the SAME function the sign-off resumes from.

**Every refusal lives here.** That is the load-bearing property: §17.5 makes
`batch_state` the one derivation of batch state from disk precisely so a gate
can never disagree with the writer, and the same argument applies to the
validation — a sign-off that accepted a row the audit rule later called
malformed would publish a batch the tree then reports BROKEN.

Two normalizations are imported rather than restated. `space_fold`
(ADR-0088 clause 1) is the whitespace canonicalization the digest rests on,
and `claim_digest` is the per-row claim binding the scaffold records so that a
candidate whose rule or evidence moved between scaffold and sign-off is
caught. `sheet_digest` is new because ADR-0098 clause 2 names `content_digest`
as the digest's "primitive and normalization" and not as the function to call:
`content_digest` takes a six-field governs entry and cannot express rows plus
scaffold provenance. The primitive (SHA-256 over a canonical string) and the
normalization (`space_fold` per field, NUL-free newline joins) are reused
exactly; only the field list differs.

Rows are canonicalized in ANCHOR ORDER, not document order. `anchor_id` is
unique within a batch (a duplicate refuses), so the sort is total, and it
makes reordering the sheet's rows a non-change — the same order-insensitivity
`claim_digest` already applies to an evidence list. Reordering rows is not a
content edit, and a digest that refused it would refuse a diff nobody made.

`config_version` is validated on read and is deliberately OUTSIDE the digest
DOMAIN: clause 2 fixes that domain as the rows plus the scaffold provenance,
and folding the schema marker in would invalidate every receipt in the tree on
a schema bump. It does, however, SELECT the preimage — the field list and the
per-cell fold are both read off it — which is how the row schema widened
without recomputing the digest of a sheet already archived downstream. Three
versions are read; one is written. Every selector that reads a version is a
keyed lookup that RAISES on a version it does not know (`_versioned`): a
fallback arm — "v1 if the version is 1, else the latest" — is exactly how a
new version re-digests every archived sheet of the one before it.
"""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import observation_evidence as _oe          # noqa: E402
import summaries_projection as _sp          # noqa: E402
from untrusted import MESSAGE_LIMIT, redact  # noqa: E402
from _yaml_min import (                     # noqa: E402
    CatalogYamlError,
    load_catalog_yaml,
    write_catalog_yaml,
)
from crux.arch.recover import (             # noqa: E402
    ANCHOR_KINDS,
    anchor_of,
    claim_digest,
    frontmatter_problem,
)

# The evidence grammar is NOT re-exported here. `observation_evidence` is the
# one declaration and every caller imports it directly; a re-export whose own
# comment claimed a caller that did not exist is a second name for one thing.
space_fold = _sp.space_fold

# The ASCII whitespace set, and only it. `space_fold` folds every character
# `str.isspace()` calls whitespace, which includes the whole Unicode `Zs`
# class — a no-break space, a figure space, an ideographic space. See
# `cell_canon` for why this lane may not do that.
_ASCII_WS_RE = re.compile(r"[\t\n\v\f\r ]+")


def cell_canon(text) -> str:
    """THE canonicalization of one sheet cell — at digest time AND at publish
    time. One function on both sides is the whole property.

    Two rules:

      * **NFC first.** Two spellings of one character are one string. A
        composed `é` and a decomposed `e` + combining acute render
        identically, so a digest that told them apart bound a distinction no
        reader can see.
      * **ASCII whitespace folds; `Zs` does not.** `space_fold` (ADR-0088
        clause 1) folds every `str.isspace()` character, and every Unicode
        `Zs` is one. So `'run time'`, `'run\\xa0time'` and `'run\\u3000time'`
        all folded to one string and digested identically — while the publish
        called `.strip()` and wrote the ORIGINAL to disk. A `Zs` therefore
        reached `domain`, the doctrine's per-domain grouping key, under an
        UNCHANGED digest: the signature covered a string the record does not
        carry. Folding it on both sides would close that gap by deleting the
        distinction instead of binding it, and a domain that renders as
        `run time` on screen would silently become a different one. So a `Zs`
        is CONTENT here: it moves the digest, it survives to disk unchanged,
        and the signature covers exactly what lands.

    `space_fold` is unchanged and stays the ADR-0088 content digest's
    canonicalization; this is the sheet lane's, and a v1 sheet still digests
    under the old one (see `cell_norms`)."""
    s = unicodedata.normalize("NFC", "" if text is None else str(text))
    return _ASCII_WS_RE.sub(" ", s).strip(" ")


def _v1_publish_canon(value) -> str:
    """What the publish did to a cell before `cell_canon` existed: strip, and
    nothing else. Kept ONLY so a v1 sheet resumes byte-identically."""
    return str(value).strip()


def _versioned(table: dict, version, what: str):
    """The one version lookup every selector in this module goes through.

    A keyed lookup with NO fallback. The selectors were once written as
    `V1 if version == "1" else LATEST`, and that `else` is how a new version
    silently re-digests every archived sheet of the one before it: a v2 sheet
    falls into the "latest" arm, picks up v3's key list, and its signed
    receipt reports CHK-OBS-SURVEY-DIGEST BROKEN on a batch nobody touched.
    An unknown version — a future one, an empty one, None — raises instead,
    so the only way a version reaches a preimage is by being declared here
    under its own key."""
    key = str(version)
    if key in table:
        return table[key]
    raise SurveySheetError([
        f"no {what} is declared for config_version {redact(version)} — the "
        f"declared versions are {list(SUPPORTED_CONFIG_VERSIONS)}"])


# `(digest-time, publish-time)` canonicalization, per `config_version`.
# v2 introduced the SAME function twice, and that identity is the fix: what
# the digest hashed is what the publish writes. v1 keeps the pair it always
# had — `space_fold` at digest time, `str.strip` at publish time — because a
# receipt already signed downstream must keep verifying against the sheet
# archived beside it. v3 shares v2's pair: the cell it added is ASCII by
# grammar (`SLUG_CELL_RE`), so no fold had to change.
_CELL_NORMS = {
    "1": (space_fold, _v1_publish_canon),
    "2": (cell_canon, cell_canon),
    "3": (cell_canon, cell_canon),
}


def cell_norms(version) -> tuple:
    """`(digest-time, publish-time)` canonicalization for a sheet of this
    `config_version`. Raises on a version `_CELL_NORMS` does not declare."""
    return _versioned(_CELL_NORMS, version, "cell canonicalization")


# The version this lane WRITES. `SUPPORTED_CONFIG_VERSIONS` is what it reads:
# ADR-0098 clause 2's field list widened (the row gained the seeded `rule` and
# `evidence`, the provenance gained the tree identity), and every previously
# archived sheet would recompute to a different digest and report
# CHK-OBS-SURVEY-DIGEST BROKEN. The migration is handled in the PREIMAGE
# rather than by a refusal to read: `canonical_sheet_string` picks its field
# list and its fold off the sheet's own `config_version`, so a v1 sheet
# digests exactly as it always did and a batch archived at S9 still reads.
# What an older sheet may NOT do is be SIGNED on any publish path
# (`assert_signable`): a v1 row carries no rule and no evidence, so signing
# it is signing an anchor id; a v2 row carries no slug cell, so the sign-off
# has no cell to consume.
#
# v3 (ADR-0099 clause 3, `ADR-0099/survey-sheet-slug-cell`) adds the `slug`
# cell to the row and `slug_source` to the receipt row. The scaffold proposes
# the slug, the human may overwrite it, and the sign-off consumes the cell as
# the one source of both the record filename and the governs handle.
CONFIG_VERSION = "3"
SUPPORTED_CONFIG_VERSIONS = ("1", "2", "3")

# §17.5 row schema: the human authors exactly one of these three verdicts.
VERDICTS = ("ratify", "reject", "defer")

# The two verdicts that DISPOSE a candidate. `defer` is absent on purpose:
# it writes nothing to the candidate state file, which is what makes it the
# one re-scaffoldable verdict (§17.5, "Why `defer` writes nothing").
DISPOSING_VERDICTS = ("ratify", "reject")

# The live sheet's filename under `<docs_dir>/observations/`. A `.yml`, so the
# concern's `*.md` record walks cannot see it.
SHEET_RE = re.compile(r"^survey-(SVY-\d{4})\.yml$")
BATCH_ID_RE = re.compile(r"^SVY-\d{4}$")

_ANCHOR_RE = re.compile(r"^[0-9a-f]{16}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OBS_ID_RE = re.compile(r"^(?:[A-Z][A-Z0-9]{1,9}-)?OBS-\d{4}$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# A candidate key: an anchor, optionally suffixed `+<n>` for a successor
# (`crux.arch.recover.anchor_of` is the split). Declared here because the
# receipt carries the key as data and `read_receipt` is the trust boundary.
_CANDIDATE_ID_RE = re.compile(r"^[0-9a-f]{16}(?:\+\d+)?$")
_RECORD_STEM_RE = re.compile(r"^((?:[A-Z][A-Z0-9]{1,9}-)?OBS-\d{4})-(.+)$")

# The slug CELL's grammar: the handle grammar intersected with the filename
# grammar. One value feeds two consumers — the record filename
# `OBS-NNNN-<slug>.md` (validated by `_SLUG_RE` via `_record_filename_parts`)
# and the governs handle `OBS-NNNN/<slug>` (validated by
# `summaries_projection._OBS_HANDLE_ANCHOR_RE`, `^OBS-(\d{4})/[a-z][a-z0-9-]*$`).
# The two disagree on exactly one point: `_SLUG_RE` admits a digit-led slug
# and the handle grammar does not. `slugify` builds `3-way-merge` from a rule
# whose title opens with a digit, the filename accepts it, the record
# publishes at exit 0 — and the projection then refuses the handle
# fail-closed for the WHOLE tree. So the cell is validated against the
# intersection, and `_SLUG_RE` itself is left alone: it validates the stems of
# record files that already exist, and narrowing it would refuse to read them.
SLUG_CELL_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


def valid_slug_cell(value) -> bool:
    """Whether `value` is a slug both the filename and the handle accept.
    `fullmatch`, because `$` admits a trailing newline and a filename must
    not."""
    return isinstance(value, str) and SLUG_CELL_RE.fullmatch(value) is not None

SHEET_KEYS = ("config_version", "batch_id", "scaffold_provenance", "rows")

# The v1 row: an anchor id, a mined domain, and the three human cells. The
# human signed an ANCHOR ID — no rule, no evidence — and the rule was read
# from the candidate state file at publish time, so a re-mine between the
# signature and the publish landed a claim nobody reviewed.
SHEET_ROW_KEYS_V1 = ("anchor_id", "proposed_domain", "verdict", "domain",
                     "rationale")
# The v2 row. `rule` and `evidence` are MACHINE-seeded verbatim from the
# candidate and read-only: they sit immediately before `verdict`, so the human
# reads the claim beside the cell they sign, and they are inside the digest.
# The sheet is not a second source of truth for either — the record's rule and
# evidence are copied from the CANDIDATE at publish time, and a seeded cell
# that no longer matches the candidate refuses the row (`build_plan`).
SHEET_ROW_KEYS_V2 = ("anchor_id", "proposed_domain", "rule", "evidence",
                     "verdict", "domain", "rationale")
# The v3 row adds `slug`, beside `domain` because the two cells get one
# treatment: the scaffold PROPOSES a value into the cell, the human may
# overwrite it, and the receipt records which of the two happened. Unlike
# `domain`, the proposal lives IN the cell rather than in a `proposed_*`
# twin — the sign-off recomputes the proposal from the bound candidate
# (`proposed_slug`) to tell the two apart, so an empty cell is a refusal and
# never a fallback. The cell is inside the digest: it is the record's
# filename and its governs handle, and a signature that did not cover it
# would cover a record that could be renamed under it.
SHEET_ROW_KEYS_V3 = ("anchor_id", "proposed_domain", "rule", "evidence",
                     "verdict", "domain", "slug", "rationale")

PROVENANCE_KEYS_V1 = ("tool", "scaffolded", "state_file", "candidates")
# v2 adds the identity of the tree the batch was scaffolded into. It is inside
# the digest, so the receipt carries it too, and the sign-off refuses a batch
# whose receipt was signed against another tree. See `tree_identity`. v3 is
# unchanged.
PROVENANCE_KEYS_V2 = PROVENANCE_KEYS_V1 + ("tree", "tree_id")

# Every per-version table below is keyed by the version STRING and read
# through `_versioned`, which raises on a key it does not hold. Each version
# is spelled out — never `LATEST`, never a default — so adding a version is
# an edit to every table and forgetting one is a raise, not a re-digest.
_SHEET_ROW_KEYS = {"1": SHEET_ROW_KEYS_V1, "2": SHEET_ROW_KEYS_V2,
                   "3": SHEET_ROW_KEYS_V3}
_PROVENANCE_KEYS = {"1": PROVENANCE_KEYS_V1, "2": PROVENANCE_KEYS_V2,
                    "3": PROVENANCE_KEYS_V2}


def sheet_row_keys(version) -> tuple:
    """The row key set of a sheet at this `config_version`. Raises on a
    version no table declares."""
    return _versioned(_SHEET_ROW_KEYS, version, "sheet row key set")


def provenance_keys(version) -> tuple:
    """The `scaffold_provenance` key set of a sheet at this
    `config_version`. Raises on a version no table declares."""
    return _versioned(_PROVENANCE_KEYS, version, "scaffold_provenance key set")


#: The two cells the scaffold seeds and no human may author.
SEEDED_ROW_KEYS = ("rule", "evidence")
RECEIPT_KEYS = ("config_version", "batch_id", "sheet_ref", "digest",
                "scaffold_provenance", "signed", "completed", "records", "rows")
# The receipt row is a CLOSED key set (`_keyset_problems`), and the receipt is
# read by `batch_state` on every projection build — so the set is selected
# on the RECEIPT'S OWN `config_version`, never on the version this module
# writes. A v2 receipt archived at S9 carries eight cells; reading it against
# nine would refuse it, drop its batch below S9, and stop both projections
# for the whole tree with nothing written.
RECEIPT_ROW_KEYS_V1 = ("anchor_id", "candidate_id", "verdict", "domain",
                       "domain_source", "record_id", "record_path", "retires")
RECEIPT_ROW_KEYS_V2 = RECEIPT_ROW_KEYS_V1
# v3 records where the published slug came from, beside the same fact about
# the domain. The slug itself is not repeated on the receipt: it is the stem
# of `record_path`, already there.
RECEIPT_ROW_KEYS_V3 = ("anchor_id", "candidate_id", "verdict", "domain",
                       "domain_source", "slug_source", "record_id",
                       "record_path", "retires")
_RECEIPT_ROW_KEYS = {"1": RECEIPT_ROW_KEYS_V1, "2": RECEIPT_ROW_KEYS_V2,
                     "3": RECEIPT_ROW_KEYS_V3}


def receipt_row_keys(version) -> tuple:
    """The row key set of a receipt at this `config_version`. Raises on a
    version no table declares."""
    return _versioned(_RECEIPT_ROW_KEYS, version, "receipt row key set")


# Where a published cell came from: the scaffold's proposal left standing, or
# a human overwrite. One vocabulary for the domain AND the slug — the receipt's
# `domain_source` and `slug_source` answer the same question about two cells,
# so they share the enum rather than each declaring its own.
DOMAIN_SOURCES = ("proposed", "overridden")

# The ordered state names of the §17.5 table. `batch_state` returns one of
# these; `STATES.index(...)` is the ordering a caller compares with, so no
# caller parses the digit out of the string.
STATES = ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9")

# Where the candidate state file lives, relative to the tree dir (§17, §4
# `arch`). Named once here because three readers join on it.
STATE_FILE_REL = ("arch", "_recovered", "state.yml")

SURVEYS_DIRNAME = "_surveys"


class SurveySheetError(Exception):
    """A document verdict on a sheet, a receipt, or a batch plan.

    Carries `problems`, a list of strings. Every caller surfaces it as an
    exit-1 findings payload — never as a traceback, which the crux exit
    convention reserves for a crash (non-zero with empty stdout)."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _refuse(problems):
    if problems:
        raise SurveySheetError(problems)


def _keyset_problems(mapping, expected, label):
    """Exact-keyset validation. An unknown key is refused rather than ignored:
    a typo (`verdit: ratify`) would otherwise read as an EMPTY verdict, and an
    empty verdict is a different refusal with a different remedy."""
    have = set(mapping)
    want = set(expected)
    out = []
    for missing in sorted(want - have):
        out.append(f"{label} is missing the key `{missing}`")
    for unknown in sorted(have - want):
        out.append(f"{label} carries the unknown key `{redact(unknown, quoted=False)}` "
                   f"(the key set is {list(expected)})")
    return out


def _str_cell(row, key, label, problems):
    """A cell's string value, or "" with a problem recorded. A non-string cell
    is a refusal rather than a `str()` coercion: `verdict: 1` coerced to `"1"`
    would fail the enum check with a message about an enum, and the real defect
    is the type."""
    value = row.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        problems.append(f"{label} cell `{key}` is {type(value).__name__}, "
                        "not a string")
        return ""
    return value


# ── the one configuration this lane does not support ────────────────────────

def assert_unprefixed_tree(root: Path) -> None:
    """Refuse a tree running a non-empty §14.3 `artifact_prefix`.

    §17.1 permits an observation id to carry the prefix, and two readers of the
    concern honour that dual form: `check_observations` matches
    `(?:[A-Z][A-Z0-9]{1,9}-)?OBS-\\d{4}`, and `summaries_projection
    .observation_paths` globs `*.md`. The survey lane does not. Both of its
    readers — `record_paths` here and `recover.read_recorded_observations` —
    glob `OBS-*.md`, and `signoff-survey._cell_3_allocate` allocates a bare
    `f"OBS-{n:04d}"`.

    That combination fails OPEN, which is why this refusal exists rather than a
    warning. In a prefixed tree both readers return nothing for every record on
    disk, so `build_plan`'s "would ratify a second live record on an anchor"
    refusal cannot fire, `CHK-OBS-ANCHOR` breaks, and the batch publishes a
    bare-id record into a prefixed tree at exit 0 — a second live record on an
    occupied anchor, reported as success.

    The alternative remedy is to make both readers and the allocation
    prefix-aware. It is not taken here because a half-done version reopens the
    identical hole under a new spelling: every glob, the allocation, and the
    `retires` filename join must agree, and this module's own validators
    (`_OBS_ID_RE`, `_RECORD_STEM_RE`) already accept the prefixed form while
    its readers do not — the exact split that produced the defect. A named
    unsupported configuration is honest; a partly-supported one is not.

    `EnvironmentError`, so both CLIs report it through the lane their
    `concerns_enabled` refusal already uses: exit 2, a sentence on stderr,
    nothing written. The prefix comes from `summaries_projection
    .artifact_prefix`, which reads the config exactly as this lane's
    `resolve_tree` reads `docs_dir` — its docstring carries why, and the short
    version is that `bionic_config.load_config` requires a `config_version`
    this lane never has.
    """
    prefix = _sp.artifact_prefix(root)
    if prefix:
        raise EnvironmentError(
            f"this tree runs artifact_prefix {redact(prefix)}, and the survey batch "
            "lane does not support a prefixed tree: its record readers glob "
            "`OBS-*.md` and its sign-off allocates a bare `OBS-NNNN`, so a "
            f"{redact(prefix, quoted=False)}-OBS-NNNN record on disk is invisible to the batch and a "
            "second live record could be ratified onto an anchor one already "
            "holds. No remedy is offered here on purpose: the prefix is baked "
            "into every id already on disk, so unsetting it is not a fix, and "
            "`recover.find_ratified_observation` globs the same bare form, so "
            "the single-record lane's duplicate check is prefix-blind too. A "
            "prefixed tree needs prefix-aware readers and a prefix-aware "
            "allocation before either lane can ratify into it.")


# ── the seeded claim, and the tree a batch belongs to ───────────────────────

def seed_cells(cand: dict) -> dict:
    """The two machine-seeded cells of one row: the candidate's `rule` and its
    `evidence`, canonicalized by `cell_canon`.

    ONE function, called at two moments — the scaffold writes what it returns
    and the sign-off compares against what it returns — so the sheet cell and
    the candidate can only disagree because the candidate MOVED, never because
    two code paths spelled the same value differently.

    `evidence` is a list on the candidate and one string in the row, because a
    sheet cell is a string: the entries are joined with `", "`, the join
    `scope` already uses. Candidate order is preserved rather than sorted — a
    re-mine that reorders the evidence changed what the human read, and this
    is the cell that says what they read."""
    rule = cell_canon(cand.get("rule") or "")
    evidence = cand.get("evidence")
    entries = evidence if isinstance(evidence, list) else []
    return {"rule": rule,
            "evidence": ", ".join(cell_canon(e) for e in entries)}


def tree_identity(root: Path, tree: Path) -> dict:
    """`{"tree": ..., "tree_id": ...}` — the identity of the tree a batch was
    scaffolded into, recorded in the sheet's provenance and therefore inside
    the digest and on the receipt.

    Nothing bound a receipt to a tree, so a batch signed in one repository was
    copied into another and published there at exit 0. Anchor ids are
    content-derived, so two repositories that depend on the same library carry
    the same anchor and the transplanted rows resolve cleanly.

    Identity is the tree's own COMMITTED CONFIGURATION — the tree path
    relative to the repo root, plus the bytes of the layout config that names
    it — and never its absolute location. That is the deliberate answer to the
    legitimate case: the same tree cloned or moved to another absolute path is
    the same tree, and its batch still publishes. The cost is stated plainly:
    two repositories whose layout config is byte-identical AND whose tree sits
    at the same relative path are one tree by this definition. An absolute
    path would tell them apart and would also refuse every clone, every
    checkout under a different name, and every CI runner — which is a refusal
    on the common case to catch the rare one.

    `manifest.yml` is deliberately NOT hashed, though it would discriminate
    further: the sign-off writes its counters mid-publish, so an identity that
    included it would change between two cells of one batch and refuse its own
    resume."""
    root = Path(root)
    try:
        rel = Path(tree).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:                    # not under the root; name it anyway
        rel = Path(tree).name
    h = hashlib.sha256()
    h.update(rel.encode("utf-8"))
    for name in (".bionic.yml", ".crux"):
        cfg = root / name
        h.update(b"\0")
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(cfg.read_bytes() if cfg.is_file() else b"")
    return {"tree": rel, "tree_id": h.hexdigest()}


def assert_tree_identity(provenance: dict, root: Path, tree: Path, *,
                         subject: str) -> None:
    """Refuse a batch whose provenance names another tree.

    A v1 provenance carries neither key and is skipped — a v1 sheet cannot be
    newly signed (`assert_signable`), so the only thing this would refuse is
    the resume of a batch bound before the identity existed, which is the one
    case with no scripted remedy."""
    prov = provenance or {}
    if prov.get("tree") is None and prov.get("tree_id") is None:
        return
    want = tree_identity(root, tree)
    if (str(prov.get("tree")), str(prov.get("tree_id"))) == (want["tree"],
                                                             want["tree_id"]):
        return
    raise SurveySheetError([
        f"{subject} was signed against tree {redact(prov.get('tree'))} "
        f"({redact(str(prov.get('tree_id'))[:12], quoted=False)}…), and this "
        f"tree is {redact(want['tree'])} "
        f"({redact(want['tree_id'][:12], quoted=False)}…) — a batch publishes "
        "into the tree it was reviewed against, never into another one"])


def assert_signable(sheet: dict) -> None:
    """Refuse to publish a sheet whose schema predates the seeded claim.

    Reading an older sheet stays supported so an archived batch still verifies
    its digest; what is refused is PUBLISHING under a row schema this sign-off
    does not consume. The sign-off calls this on every publish path — a first
    signature and a resume alike — so a transplanted or resumed older batch,
    whose missing tree-identity fields let `assert_tree_identity` return
    early, is still refused here.

    The refusal names the cells the sheet's OWN version lacks, derived from
    the two key sets rather than restated: a v1 row carries no `rule`, no
    `evidence` and no `slug`; a v2 row carries no `slug`. A message that said
    "no rule and no evidence" of a v2 sheet would send the human to the wrong
    remedy."""
    version = str(sheet.get("config_version"))
    if version == CONFIG_VERSION:
        return
    if version in SUPPORTED_CONFIG_VERSIONS:
        missing = [k for k in sheet_row_keys(CONFIG_VERSION)
                   if k not in sheet_row_keys(version)]
        cells = " and ".join(f"no `{k}`" for k in missing)
        why = (f"a config_version {version} row carries {cells}, so this "
               "sign-off has no such cell to consume")
    else:
        why = (f"config_version {redact(version)} is outside "
               f"{list(SUPPORTED_CONFIG_VERSIONS)}")
    raise SurveySheetError([
        f"review sheet {redact(sheet.get('batch_id'))} is "
        f"config_version {redact(version)} and this sign-off signs "
        f"{CONFIG_VERSION!r} — {why}. Re-scaffold the batch with "
        "scaffold-survey-sheet.py."])


# ── paths ───────────────────────────────────────────────────────────────────

def receipt_paths(obs_dir: Path, batch_id: str) -> dict:
    """The five paths one batch owns, derived from the observations directory
    and the batch id alone — the one place the `_surveys/` layout is spelled.

    `live` is the sheet a human edits; `dir`, `sheet`, `receipt` and `staged`
    are the frozen holding area (§3, §17.5). No caller joins these by hand,
    so a relocation is a one-function edit.

    [SECURITY:S4/S5] Being the one place the layout is spelled makes it the one
    place the layout's containment is decided. `_load_yaml_doc` guards the LEAF
    — a sheet or receipt that is itself a symlink — and that was the only leg.
    A symlink at `<obs>/_surveys`, or at one batch directory under it, leaves
    every entry below it a real file, so the leaf check passes and an outside
    file is read; `validate_receipt` then quotes its CELL VALUES into the
    refusal it raises. `_assert_contained` is the write-side twin of this leg
    and cannot see it: it guards a write target, which is contained by
    construction. `summaries_projection.survey_receipt_paths` carries the same
    two legs over the same directory.

    The check also covers `batch_id`, which reaches a path join: a `..`
    spelling is refused by `resolve_contained`'s textual leg before any stat.
    """
    obs_dir = Path(obs_dir)
    base = obs_dir / SURVEYS_DIRNAME / batch_id
    for rel in (SURVEYS_DIRNAME, f"{SURVEYS_DIRNAME}/{batch_id}"):
        if _oe.resolve_contained(obs_dir, rel) is None:
            raise SurveySheetError([
                f"refusing to use the survey holding area "
                f"{redact(rel, quoted=False)} under "
                f"{redact(obs_dir, quoted=False)}: it does not resolve to a path "
                "inside the concern "
                "— a batch's surfaces live in the concern directory, never "
                "through a link out of it"])
    return {
        "live": obs_dir / f"survey-{batch_id}.yml",
        "dir": base,
        "sheet": base / "sheet.yml",
        "receipt": base / "receipt.yml",
        "staged": base / "staged",
    }


def batch_ids(obs_dir: Path) -> list[str]:
    """Every batch id with a directory under `_surveys/`, sorted. A directory
    whose name is not a batch id is ignored rather than refused — the holding
    area is frozen, not owned, and an unrelated directory there is not this
    module's finding to make."""
    base = Path(obs_dir) / SURVEYS_DIRNAME
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and BATCH_ID_RE.match(p.name))


# ── the sheet ───────────────────────────────────────────────────────────────

def _load_yaml_doc(path: Path, label: str) -> dict:
    """Read one strict-minimal-YAML document (§7.A).

    `load_catalog_yaml` is the loader ADR-0098 clause 4 names: it already
    refuses anchors, aliases, merge keys, explicit tags, a second document and
    a duplicate key at any level — the exact refusal set a signed artifact
    needs, and one this module must not reimplement."""
    if not path.is_file():
        raise SurveySheetError(
            [f"{label} {redact(path, quoted=False)} does not exist"])
    if path.is_symlink():
        raise SurveySheetError([
            f"refusing to read {label} {redact(path, quoted=False)}: it is a "
            "symlink — a signed "
            "artifact is read from the tree, never through a link out of it"])
    try:
        return load_catalog_yaml(path.read_text(encoding="utf-8"))
    except CatalogYamlError as exc:
        raise SurveySheetError([
            f"{label} {redact(path, quoted=False)} is not readable: "
            f"{redact(exc, quoted=False)}"]) from exc


def read_sheet(path: Path) -> dict:
    """Read and validate a review sheet. Raises `SurveySheetError`.

    Validates SHAPE only — the cells a machine wrote and the cells a human may
    write. It does NOT require a verdict: the scaffold writes a sheet with
    every human cell empty, and that sheet must read back cleanly. The signed
    validation is `signed_rows`, which the sign-off runs and the scaffold does
    not."""
    path = Path(path)
    doc = _load_yaml_doc(path, "review sheet")
    problems = _keyset_problems(doc, SHEET_KEYS, "the review sheet")
    _refuse(problems)

    version = str(doc.get("config_version"))
    if version not in SUPPORTED_CONFIG_VERSIONS:
        problems.append(
            f"review sheet config_version must be one of "
            f"{list(SUPPORTED_CONFIG_VERSIONS)}, got "
            f"{redact(doc.get('config_version'))}")
        _refuse(problems)

    batch_id = doc.get("batch_id")
    if not isinstance(batch_id, str) or not BATCH_ID_RE.match(batch_id):
        problems.append(
            f"review sheet batch_id {redact(batch_id)} is not SVY-NNNN")
    else:
        # The live sheet's filename carries the batch id; the archived
        # `sheet.yml` does not, so the cross-check runs only on the live name.
        m = SHEET_RE.match(path.name)
        if m and m.group(1) != batch_id:
            problems.append(
                f"review sheet {redact(path.name, quoted=False)} declares "
                f"batch_id {redact(batch_id)} — "
                "the filename and the declared batch must name one batch")

    prov = doc.get("scaffold_provenance")
    if not isinstance(prov, dict):
        problems.append("review sheet scaffold_provenance is not a mapping")
    else:
        problems.extend(_keyset_problems(prov, provenance_keys(version),
                                         "scaffold_provenance"))

    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        problems.append("review sheet rows must be a non-empty list")
        _refuse(problems)

    row_keys = sheet_row_keys(version)
    seen: dict[str, int] = {}
    for i, row in enumerate(rows):
        label = f"review sheet row {i + 1}"
        if not isinstance(row, dict):
            problems.append(f"{label} is not a mapping")
            continue
        problems.extend(_keyset_problems(row, row_keys, label))
        anchor = _str_cell(row, "anchor_id", label, problems)
        for key in row_keys:
            if key != "anchor_id":
                _str_cell(row, key, label, problems)
        if not _ANCHOR_RE.match(anchor):
            problems.append(f"{label} anchor_id {redact(anchor)} is not 16 "
                            "lowercase hex digits")
            continue
        if anchor in seen:
            problems.append(
                f"anchor {anchor} appears on rows {seen[anchor]} and {i + 1} — "
                "one anchor may appear in a batch at most once")
            continue
        seen[anchor] = i + 1
    _refuse(problems)
    return doc


def signed_rows(sheet: dict) -> list[dict]:
    """The sheet's rows with the seed applied, refusing every unsigned or
    malformed human cell (ADR-0098 clause 4).

    Returns one dict per row carrying `anchor_id`, `verdict`, the resolved
    `domain`, and `domain_source` of `proposed` or `overridden`. The receipt
    records `domain_source`; the sheet never carries it, so the seeded-or-
    overridden question is answerable from the receipt alone.

    Four refusals, in this order, because the order is the semantics:

      1. an EMPTY verdict — the human has not signed this row;
      2. a verdict outside the enum;
      3. a WHITESPACE-ONLY domain — the human wrote something, and it is not a
         domain. Checked BEFORE the seed fallback: stripping it to empty and
         silently accepting the seed would turn a typed cell into an untyped
         one, which is a machine filling a human cell;
      4. a MULTI-LINE domain — a domain is one token, and a newline in it
         reaches the doctrine's per-domain grouping key;
      5. a domain still EMPTY after the seed is applied — the candidate carried
         no mined domain and the human supplied none.
    """
    out = []
    problems: list[str] = []
    version = str(sheet.get("config_version"))
    _digest_canon, publish_canon = cell_norms(version)
    for row in sheet["rows"]:
        anchor = str(row["anchor_id"])
        verdict = str(row.get("verdict") or "")
        raw_domain = row.get("domain")
        raw_domain = "" if raw_domain is None else str(raw_domain)
        seed = str(row.get("proposed_domain") or "")
        label = f"row {anchor}"

        if not verdict.strip():
            problems.append(f"{label} carries no verdict — every row is signed "
                            f"{' | '.join(VERDICTS)} before the batch is")
            continue
        if verdict not in VERDICTS:
            problems.append(f"{label} verdict {redact(verdict)} is outside "
                            f"{list(VERDICTS)}")
            continue
        if raw_domain and not raw_domain.strip():
            problems.append(f"{label} domain is whitespace only — write a "
                            "domain or leave the cell empty to accept the seed")
            continue
        if "\n" in raw_domain or "\r" in raw_domain:
            problems.append(f"{label} domain {redact(raw_domain)} spans more than one "
                            "line — a domain is a single token")
            continue
        domain = raw_domain if raw_domain else seed
        if not domain.strip():
            problems.append(
                f"{label} has no domain after the seed is applied — the "
                "candidate carried no mined domain, so the sheet must name one")
            continue
        # The resolved domain, seed included, must survive `read_receipt`.
        # Without this a tab in the cell — or in a mined `proposed_domain` —
        # bound cleanly and then wrote a receipt this module's own reader
        # refuses, with the live sheet already deleted.
        # The character class is decided on the RAW cell, before any fold. A
        # tab folds to a space, so checking the folded value would turn what
        # was a refusal naming the character into a silent rewrite of a cell
        # a human typed.
        if not _valid_domain(domain.strip()):
            problems.append(
                f"{label} domain {redact(domain)} carries a control, format or "
                "separator character, or opens with a combining mark — a "
                "domain is one line of printable text")
            continue
        # The canonicalization the DIGEST used, applied to the value that
        # lands on disk. For a v2 sheet these are one function, so what was
        # signed is what is published; for a v1 sheet this is `.strip()`,
        # exactly as it was, so a bound v1 batch resumes unchanged. A raw
        # value that passed the check above passes it folded too: the fold
        # removes only ASCII whitespace, and NFC composes rather than
        # introducing a refused category.
        domain = publish_canon(domain)
        entry = {
            "anchor_id": anchor,
            "verdict": verdict,
            "domain": domain,
            "domain_source": "overridden" if raw_domain else "proposed",
        }
        # The seeded claim, carried through for `build_plan` to compare with
        # the candidate. A v1 row has no seed and carries None, which that
        # comparison reads as "nothing was seeded, so nothing can have moved".
        for key in SEEDED_ROW_KEYS:
            entry[key] = (publish_canon(row[key]) if key in row else None)
        # The slug cell, carried through VERBATIM — no fold, no strip. The
        # grammar admits no whitespace, so a cell that needs folding is a
        # malformed cell, and `build_plan` refuses it by name rather than
        # rewriting what the human typed. A row with no slug cell (v1, v2)
        # carries None, on the seeded-cell precedent: `build_plan` reads None
        # as "no cell to consume" and derives the slug from the title as those
        # versions always did. An EMPTY cell is "" and is a refusal.
        entry["slug"] = str(row["slug"]) if "slug" in row else None
        out.append(entry)
    _refuse(problems)
    return out


# ── the digest (ADR-0098 clause 2) ──────────────────────────────────────────

def canonical_sheet_string(sheet: dict) -> str:
    """The sheet's canonical string: the batch id, the scaffold provenance, and
    every row's cells, each canonicalized by the sheet version's fold and
    newline-joined in anchor order.

    The batch id is inside the domain so a sheet cannot be renamed into another
    batch under an unchanged digest. `anchor_id` and `verdict` are both inside
    it, so neither a swapped candidate under an unchanged row nor a row
    authored `reject` and edited to `ratify` leaves the digest matching.
    `config_version` is outside the DOMAIN and inside the PREIMAGE SELECTION:
    the field list and the fold are both read off it. That is the digest
    migration — widening the row schema would otherwise recompute every sheet
    already archived downstream and report CHK-OBS-SURVEY-DIGEST BROKEN on a
    batch nobody touched. A v1 sheet hashes its five cells with `space_fold`,
    exactly as it always did; a v2 sheet hashes its seven with `cell_canon`."""
    version = str(sheet.get("config_version"))
    fold, _publish = cell_norms(version)
    lines = [fold(sheet["batch_id"])]
    prov = sheet.get("scaffold_provenance") or {}
    for key in provenance_keys(version):
        lines.append(f"{key}={fold(prov.get(key, ''))}")
    for row in sorted(sheet["rows"], key=lambda r: str(r["anchor_id"])):
        lines.append("--")
        for key in sheet_row_keys(version):
            value = row.get(key)
            lines.append(f"{key}={fold('' if value is None else value)}")
    return "\n".join(lines)


def sheet_digest(sheet: dict) -> str:
    """SHA-256 hex of the canonical sheet string — ADR-0088's primitive and
    normalization over ADR-0098 clause 2's field list. One digest over the
    whole sheet, never one per row."""
    return hashlib.sha256(canonical_sheet_string(sheet).encode("utf-8")).hexdigest()


# ── the receipt ─────────────────────────────────────────────────────────────

def _record_filename_parts(name: str) -> tuple[str, str] | None:
    """`OBS-NNNN-<slug>.md` -> `(record id, slug)`, or None.

    Composed from `_OBS_ID_RE` and `_SLUG_RE` rather than from a third regex:
    those two ARE the declarations the id allocation and `slugify` produce, and
    a filename grammar that restated them could drift from either."""
    if not name.endswith(".md"):
        return None
    m = _RECORD_STEM_RE.match(name[: -len(".md")])
    if not m or not _OBS_ID_RE.match(m.group(1)) or not _SLUG_RE.match(m.group(2)):
        return None
    return m.group(1), m.group(2)


def _concern_dir_of(receipt_path) -> str:
    """The concern directory a receipt is filed under.

    A receipt lives at `<concern>/_surveys/<SVY-NNNN>/receipt.yml`, so the
    concern is the third parent's name. Read off the receipt's LOCATION and
    never off a cell inside it: a hand-edited receipt can restate every cell it
    carries, and the directory it sits in is the one fact about it an editor
    cannot forge without moving the file."""
    return Path(receipt_path).parent.parent.parent.name


def _tree_relative_record(value, concern_dir: str | None = None) -> tuple[str, str] | None:
    """`<concern-dir>/OBS-NNNN-<slug>.md` -> `(directory, record id)`, or None.

    Three legs. The TEXTUAL one is `observation_evidence.valid_evidence_path` —
    the one declaration of "repo-relative" this codebase has, which already
    refuses an absolute path, a `~` prefix, a `..` component and a Windows
    drive letter — widened here with a control-character and backslash refusal,
    because these values reach `log.md`, a filename and a file copy, and a
    newline in one of them forges a document rather than naming a path. The
    STRUCTURAL leg is exactly two components whose second is a record
    filename: `_cell_6_stage` joins the value onto the tree and
    `planned_record_names` takes its basename, so a deeper or shallower path
    names a file the concern does not own.

    The third leg compares that directory to `concern_dir` when the caller
    knows it. Counting the components and never checking the first admitted
    `adrs/OBS-0100-x.md` — two components, a well-formed record filename, and
    resolving comfortably inside the tree — so the sign-off copied a SIBLING
    concern's file into `observations/` at exit 0, while the error text this
    function drives already promised `<concern-dir>/`. `read_receipt` and
    `write_receipt` both know the concern from the receipt's own path
    (`_concern_dir_of`); `concern_dir=None` is the document-only lane, where
    the two-component leg is all a caller holding no path can check.

    Grammar only. Containment of the RESOLVED path is decided at the read, by
    `observation_evidence.resolve_contained` — a spelling cannot express what a
    symlink does."""
    if not isinstance(value, str) or not value:
        return None
    if "\\" in value or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        return None
    if not _oe.valid_evidence_path(value):
        return None
    parts = Path(value).parts
    if len(parts) != 2:
        return None
    if concern_dir is not None and parts[0] != concern_dir:
        return None
    parsed = _record_filename_parts(parts[1])
    return None if parsed is None else (parts[0], parsed[0])


#: Unicode general categories a domain may not contain. `Cc`/`Cf`/`Cs`/`Co`/
#: `Cn` are the control, format, surrogate, private-use and unassigned classes;
#: `Zl`/`Zp` are the line and paragraph separators. Refusing by CATEGORY rather
#: than by codepoint is what makes the rule total: an enumeration would have to
#: be revisited on every Unicode release, and the character that got through
#: last time (U+200B) is not special — it is one member of `Cf`, alongside the
#: bidi overrides and the byte-order mark.
_DOMAIN_REFUSED_CATEGORIES = frozenset(
    {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def _valid_domain(value) -> bool:
    """A domain is one line of PRINTABLE text with something in it.

    The receipt-side leg of the rule `signed_rows` applies to the sheet. It is
    restated here rather than assumed because the receipt is read by three
    callers that never see a sheet, and the value reaches record frontmatter
    through `_yaml_str` and the doctrine's per-domain grouping key.

    Refusing only C0, C1 and DEL left the whole Unicode FORMAT class admitted:
    a zero-width space, a bidi override, a byte-order mark. None of them is
    whitespace by Python's definition, so `.strip()` does not remove one and a
    domain of nothing but a zero-width space read as a signed value — reaching
    two record bodies, the receipt, the archived sheet, the concern index and
    the doctrine's grouping key, where two domains that render identically and
    group separately split the doctrine with nothing on screen to show for it.

    A LEADING combining mark is refused too. It has no base character to
    compose with, so it renders onto whatever precedes the domain wherever the
    domain is interpolated — a table cell, a heading — which is the same defect
    from the other end. A combining mark elsewhere in the string is ordinary
    text and stays."""
    if not isinstance(value, str) or not value.strip():
        return False
    if any(unicodedata.category(ch) in _DOMAIN_REFUSED_CATEGORIES
           for ch in value):
        return False
    return not unicodedata.category(value[0]).startswith("M")


def validate_receipt(doc, subject: str = "batch receipt", *,
                     concern_dir: str | None = None) -> None:
    """Refuse a receipt document that is malformed. Raises `SurveySheetError`.

    Split out of `read_receipt` so the WRITER runs the same rules the reader
    does, at both moments it needs them: over the whole batch before the first
    write (`signoff-survey.planned_receipt`) and over the bytes of each write
    (`write_receipt`). A writer that could emit a receipt this module's own
    reader refuses puts the tree in a state only a human can leave: cell 1
    writes the receipt and deletes the live sheet, and the very next
    `batch_state` then raises on the file it just wrote.

    **The receipt is trusted input on every read.** `batch_state`, the
    CHK-OBS-SURVEY-* audit rules and both projections parse it, and its row
    cells reach a file copy (`retires`), a filename (`record_path`), a log
    entry and a journal hook (`record_id`), a state-file disposition
    (`candidate_id`) and record frontmatter (`domain`). Every key in the
    receipt's row key set is therefore validated against a declared shape —
    validating a subset of them left the rest as unchecked input on a path with
    a signature on it."""
    problems = _keyset_problems(doc, RECEIPT_KEYS, f"the {subject}")
    _refuse(problems)

    # Every version reads. A receipt signed before the row schema widened must
    # stay readable: `batch_state` parses it, and a receipt this module
    # refused would put the whole tree's summaries and doctrine behind a
    # refusal no command can clear. The row key set is selected on the
    # receipt's OWN version below, and an unknown version refuses HERE so the
    # selector is never asked about one.
    version = str(doc.get("config_version"))
    if version not in SUPPORTED_CONFIG_VERSIONS:
        problems.append(
            f"{subject} config_version must be one of "
            f"{list(SUPPORTED_CONFIG_VERSIONS)}, got "
            f"{redact(doc.get('config_version'))}")
        _refuse(problems)
    row_keys = receipt_row_keys(version)
    batch_id = doc.get("batch_id")
    if not isinstance(batch_id, str) or not BATCH_ID_RE.match(batch_id):
        problems.append(
            f"{subject} batch_id {redact(batch_id)} is not SVY-NNNN")
    digest = doc.get("digest")
    if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
        problems.append(f"{subject} digest {redact(digest)} is not 64 lowercase "
                        "hex digits")
    signed = doc.get("signed")
    if not isinstance(signed, str) or not _DATE_RE.match(signed):
        problems.append(f"{subject} signed {redact(signed)} is not a YYYY-MM-DD "
                        "date")
    completed = doc.get("completed")
    if completed is not None and (not isinstance(completed, str)
                                  or not _DATE_RE.match(completed)):
        problems.append(f"{subject} completed {redact(completed)} is neither null "
                        "nor a YYYY-MM-DD date")
    records = doc.get("records")
    if not isinstance(records, list) or not all(isinstance(r, str) for r in records):
        problems.append(f"{subject} records is not a list of strings")
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        problems.append(f"{subject} rows must be a non-empty list")
        _refuse(problems)

    seen = set()
    for i, row in enumerate(rows):
        label = f"{subject} row {i + 1}"
        if not isinstance(row, dict):
            problems.append(f"{label} is not a mapping")
            continue
        problems.extend(_keyset_problems(row, row_keys, label))
        anchor = _str_cell(row, "anchor_id", label, problems)
        if not _ANCHOR_RE.match(anchor):
            problems.append(f"{label} anchor_id {redact(anchor)} is not 16 "
                            "lowercase hex digits")
            continue
        if anchor in seen:
            problems.append(f"{subject} names anchor {anchor} more than once")
            continue
        seen.add(anchor)
        verdict = row.get("verdict")
        if verdict not in VERDICTS:
            problems.append(f"{label} verdict {redact(verdict)} is outside "
                            f"{list(VERDICTS)}")
        # `domain_source` on every version; `slug_source` where the row
        # carries it. Both take `DOMAIN_SOURCES`: one question, one enum.
        for key in ("domain_source", "slug_source"):
            if key in row_keys and row.get(key) not in DOMAIN_SOURCES:
                problems.append(
                    f"{label} {key} {redact(row.get(key))} "
                    f"is outside {list(DOMAIN_SOURCES)}")

        cand = _str_cell(row, "candidate_id", label, problems)
        if not _CANDIDATE_ID_RE.match(cand):
            problems.append(
                f"{label} candidate_id {redact(cand)} is not a 16-hex candidate key, "
                "optionally suffixed `+<n>` for a successor")
        elif anchor_of(cand) != anchor:
            problems.append(
                f"{label} candidate_id {redact(cand)} sits on anchor "
                f"{redact(anchor_of(cand))} rather than on this row's anchor "
                f"{redact(anchor)} — the disposition join writes the candidate the "
                "receipt names, so a row could dispose one it never reviewed")

        domain = _str_cell(row, "domain", label, problems)
        if not _valid_domain(domain):
            problems.append(
                f"{label} domain {redact(domain)} is empty, blank, or carries a "
                "control, format or separator character — a domain is one line "
                "of printable text, and it is written into record frontmatter "
                "and the doctrine's per-domain grouping key")

        record_id = _str_cell(row, "record_id", label, problems)
        if record_id and not _OBS_ID_RE.match(record_id):
            problems.append(
                f"{label} record_id {redact(record_id)} is not OBS-NNNN — the id is "
                "written verbatim into log.md and the journal hook, where a "
                "newline in it forges an entry the summaries projection then "
                "parses as genuine")

        record_path = _str_cell(row, "record_path", label, problems)
        if record_path:
            parsed = _tree_relative_record(record_path, concern_dir)
            if parsed is None:
                problems.append(
                    f"{label} record_path {redact(record_path)} is not a "
                    "tree-relative `<concern-dir>/OBS-NNNN-<slug>.md`")
            elif record_id and parsed[1] != record_id:
                problems.append(
                    f"{label} record_path {redact(record_path)} names record "
                    f"{redact(parsed[1], quoted=False)}, not this row's "
                    f"record_id {redact(record_id, quoted=False)}")

        retires = _str_cell(row, "retires", label, problems)
        if retires and _tree_relative_record(retires, concern_dir) is None:
            problems.append(
                f"{label} retires {redact(retires)} is not a tree-relative "
                "`<concern-dir>/OBS-NNNN-<slug>.md` — the sign-off READS this "
                "path and copies the file it names into the concern")

        # Only a `ratify` row allocates. `new_receipt` and `_cell_3_allocate`
        # leave all three cells empty on the other two verdicts, and a receipt
        # carrying one anyway plans a staged file and a promotion for a row
        # that published nothing.
        if verdict in VERDICTS and verdict != "ratify":
            for key in ("record_id", "record_path", "retires"):
                if row.get(key):
                    problems.append(
                        f"{label} carries {key} {redact(row.get(key))} on a "
                        f"{verdict} row — only a ratify row allocates a record")
    _refuse(problems)


def read_receipt(path: Path) -> dict:
    """Read one receipt off disk and validate it. Raises `SurveySheetError`."""
    path = Path(path)
    doc = _load_yaml_doc(path, "batch receipt")
    validate_receipt(doc, concern_dir=_concern_dir_of(path))
    return doc


def _assert_contained(path: Path, contained_under: Path, subject: str) -> None:
    """[SECURITY:S5] The write target's RESOLVED parent must sit under the
    validated tree dir. `write_catalog_yaml` and `atomic_write_text` guard the
    LEAF (target and tmp, O_NOFOLLOW|O_EXCL); this guards the INTERMEDIATE
    directories, which have no leaf link to catch — a symlink at
    `<obs>/_surveys` leaves every entry under it a real file while steering the
    write outside the tree. Same shape as
    `signoff-backfill._atomic_write_text`'s containment leg."""
    root_resolved = Path(contained_under).resolve()
    parent_resolved = Path(path).parent.resolve()
    if parent_resolved != root_resolved and root_resolved not in parent_resolved.parents:
        raise SurveySheetError([
            f"refusing to write {redact(path, quoted=False)}: the parent "
            f"directory resolves to {redact(parent_resolved, quoted=False)}, "
            f"which is not contained under {redact(root_resolved, quoted=False)} — "
            f"a symlinked intermediate directory would put {subject} outside "
            "the tree"])


def open_new_tmp(tmp: Path):
    """Create the predictable temporary file `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW`,
    removing a STALE one first, and return the file descriptor.

    `O_EXCL` is what stops this writer from writing through a file it does not
    own, and it stays. What it cannot do on its own is survive a crash. A
    process killed between this create and the `os.replace` that follows leaves
    `<path>.tmp` on disk — the `except Exception` rollback below does not run,
    because a kill is not an `Exception` — and every later run then refused,
    permanently: nothing in the protocol removes that file, this script is the
    only write path for the surfaces it owns, and both projections refuse the
    whole tree while the batch sits below S9. The old refusal's own remedy was
    "remove it after checking what created it", which `survey-signoff` forbids
    for every surface under `_surveys/`. The two surfaces contradicted each
    other and the batch had no remedy at all.

    So a leftover is removed HERE, by the writer that owns the name, and the
    create is retried once. Unlinking is safe where truncating would not be:
    `os.unlink` removes the NAME, so a planted symlink loses its link and never
    its target, and a hardlink loses one of its names and no content. A
    directory or an entry that will not unlink raises `OSError` and still
    refuses — this never forces. The retry carries `O_EXCL` too, so a writer
    that wins the name between the unlink and the retry is refused rather than
    clobbered."""
    import os

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        return os.open(tmp, flags, 0o644)
    except FileExistsError:
        os.unlink(tmp)
        return os.open(tmp, flags, 0o644)


def atomic_write_text(path: Path, body: str, *, contained_under: Path) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    A near-copy of `signoff-backfill._atomic_write_text`, which is module
    private and cannot be imported. The guards are the same guards in the same
    order — resolved-parent containment, then an explicit symlink refusal on
    the target and on the predictable `<path>.tmp`, then an
    `O_NOFOLLOW|O_EXCL` create so the check is not a TOCTOU window — and the
    duplication is deliberate rather than resolved here: hoisting a signed
    write path shared with the backfill sign-off is a refactor outside
    ADR-0098's scope. It is recorded as an `iterate` candidate.

    Raises `SurveySheetError` rather than `OSError`, because every caller in
    this lane surfaces one exception type as an exit-1 findings payload."""
    import os

    path = Path(path)
    _assert_contained(path, contained_under, "signed batch content")
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise SurveySheetError([
                f"refusing to write {redact(path, quoted=False)}: the {label} "
                f"{redact(candidate, quoted=False)} is a "
                "symlink — writing through it would put signed batch content "
                "in the link's target"])
    try:
        fd = open_new_tmp(tmp)
    except FileExistsError as exc:
        raise SurveySheetError([
            f"refusing to write {redact(path, quoted=False)}: the temporary "
            f"file {redact(tmp, quoted=False)} reappeared "
            "between its removal and the retry — another process is writing "
            "this tree. Re-run once nothing else is; never remove a file under "
            "_surveys/ by hand"]) from exc
    except OSError as exc:
        raise SurveySheetError([
            f"refusing to write {redact(path, quoted=False)}: cannot create "
            f"{redact(tmp, quoted=False)} ({redact(exc, quoted=False, limit=MESSAGE_LIMIT)})"]) from exc
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


# ── the manifest counters (§7) ─────────────────────────────────────────────

_BLOCK_RE_CACHE: dict[str, re.Pattern] = {}


def read_counter(manifest: dict, block: str, key: str, default: int) -> int:
    """A monotonic counter from the parsed manifest, or `default` when the
    block or the key is absent. An absent counter is a tree that has never
    allocated one, which is the same position as a counter at its default —
    never an error."""
    section = manifest.get(block)
    if not isinstance(section, dict):
        return default
    value = section.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def write_counter(path: Path, block: str, key: str, value: int, *,
                  contained_under: Path) -> None:
    """Set `<block>.<key>` to `value` in `manifest.yml`, textually.

    Line-oriented on purpose, on `signoff-backfill._write_ledger_and_marker`'s
    model: the manifest is hand-authored and its comments are content, so a
    parse-and-redump would delete every one of them. Three cases, all lossless:
    the key exists in the block (rewrite the number, keeping any trailing
    comment), the block exists without the key (insert the key at the block's
    end), and the block is absent (append the block).
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.rstrip() == f"{block}:"), None)
    if start is None:
        body = "\n".join(lines).rstrip("\n")
        atomic_write_text(path, f"{body}\n\n{block}:\n  {key}: {value}\n",
                          contained_under=contained_under)
        return
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = i
            break
    key_re = re.compile(rf"^(\s+){re.escape(key)}(\s*):(\s*)(\S+)(.*)$")
    for i in range(start + 1, end):
        m = key_re.match(lines[i])
        if m:
            lines[i] = f"{m.group(1)}{key}{m.group(2)}:{m.group(3)}{value}{m.group(5)}"
            atomic_write_text(path, "\n".join(lines) + "\n",
                              contained_under=contained_under)
            return
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    lines.insert(insert, f"  {key}: {value}")
    atomic_write_text(path, "\n".join(lines) + "\n",
                      contained_under=contained_under)


def write_receipt(path: Path, receipt: dict, *, contained_under: Path) -> None:
    """Write a receipt atomically through the §7.A strict-minimal-YAML path.

    `write_catalog_yaml` re-reads the emitted text through BOTH parse paths and
    refuses to commit output it cannot read back, and it carries the symlink /
    predictable-tmp guards ([SECURITY:S5]) a signed artifact needs. It does not
    guard the INTERMEDIATE directories, so containment on the resolved parent
    is asserted here.

    The receipt is validated before it is written, through `validate_receipt` —
    the same rules `read_receipt` runs. This is the LAST of two checks, not the
    only one: it sees one write at a time, and cell 1's write is the one that
    deletes the live sheet, so a row only cell 3 can express (an allocated
    `record_id`, a derived `retires`) was refused here with the batch already
    bound and no scripted remedy. `signoff-survey.planned_receipt` runs the
    same rules over the WHOLE batch in the pre-write lane, which is where the
    refusal costs nothing; this check stays as the guard on the actual bytes."""
    path = Path(path)
    _assert_contained(path, contained_under, "a signed receipt")
    ordered = {k: receipt[k] for k in RECEIPT_KEYS}
    # The receipt's OWN version picks the row key set: a resume rewrites a
    # receipt on every cell, and a v2 receipt written back with v3's keys
    # would be refused by its own next read.
    row_keys = receipt_row_keys(receipt.get("config_version"))
    ordered["rows"] = [{k: r[k] for k in row_keys} for r in receipt["rows"]]
    validate_receipt(ordered, "the receipt this sign-off is about to write",
                     concern_dir=_concern_dir_of(path))
    try:
        write_catalog_yaml(path, ordered)
    except CatalogYamlError as exc:
        raise SurveySheetError([
            f"refusing to write {redact(path, quoted=False)}: "
            f"{redact(exc, quoted=False, limit=MESSAGE_LIMIT)}"]) from exc


def write_sheet(path: Path, sheet: dict, *, contained_under: Path) -> None:
    """Write a review sheet through the same guarded, re-read-verified path the
    receipt takes. Key order is `SHEET_KEYS` then `SHEET_ROW_KEYS`, so a human
    opening the file finds the cells they author in one place and in one
    order."""
    path = Path(path)
    _assert_contained(path, contained_under, "a review sheet")
    row_keys = sheet_row_keys(sheet.get("config_version"))
    ordered = {k: sheet[k] for k in SHEET_KEYS}
    ordered["rows"] = [{k: r[k] for k in row_keys} for r in sheet["rows"]]
    try:
        write_catalog_yaml(path, ordered)
    except CatalogYamlError as exc:
        raise SurveySheetError([
            f"refusing to write {redact(path, quoted=False)}: "
            f"{redact(exc, quoted=False, limit=MESSAGE_LIMIT)}"]) from exc


def new_receipt(sheet: dict, rows: list[dict], *, signed: str,
                sheet_ref: str) -> dict:
    """The S1 receipt: bound by the digest, carrying every row's outcome, and
    recording NO completion and NO records — those land at the commit point."""
    return {
        "config_version": CONFIG_VERSION,
        "batch_id": sheet["batch_id"],
        "sheet_ref": sheet_ref,
        "digest": sheet_digest(sheet),
        "scaffold_provenance": dict(sheet.get("scaffold_provenance") or {}),
        "signed": signed,
        "completed": None,
        "records": [],
        "rows": [
            {
                "anchor_id": r["anchor_id"],
                "candidate_id": r.get("candidate_id", r["anchor_id"]),
                "verdict": r["verdict"],
                "domain": r["domain"],
                "domain_source": r["domain_source"],
                "slug_source": r["slug_source"],
                "record_id": "",
                "record_path": "",
                "retires": "",
            }
            for r in sorted(rows, key=lambda r: r["anchor_id"])
        ],
    }


# ── disposition, across every batch the tree has signed ─────────────────────

def _disposed(obs_dir: Path, key: str) -> dict:
    out: dict[str, str] = {}
    obs_dir = Path(obs_dir)
    for bid in batch_ids(obs_dir):
        receipt_path = receipt_paths(obs_dir, bid)["receipt"]
        if not receipt_path.is_file():
            continue
        for row in read_receipt(receipt_path)["rows"]:
            if row["verdict"] in DISPOSING_VERDICTS:
                out[str(row[key] or row["anchor_id"])] = bid
    return out


def disposed_candidates(obs_dir: Path) -> dict:
    """`{candidate key: batch_id}` for every CANDIDATE a receipt has disposed.

    `ratify` and `reject` dispose; `defer` does not, which is the whole of
    "defer is the only re-scaffoldable verdict" (§17.5). An unreadable receipt
    raises: a reader that silently skipped one would treat every candidate that
    receipt disposed as undisposed.

    The key is the CANDIDATE, never its anchor. ADR-0098 clause 2 covers
    "candidates no receipt has disposed", and a candidate is not its anchor. A re-mine that
    finds a ratified record's claim changed opens a SUCCESSOR candidate on that
    same anchor, keyed `<anchor>+<n>`. Filtering on the anchor would make that
    successor permanently unscaffoldable — the anchor was disposed by the batch
    that ratified its predecessor — and the successor lane is the only route a
    changed claim has. Filtering on the candidate key admits it, while the two
    guards that matter still hold: `build_plan` refuses a second LIVE record on
    one anchor, and one anchor may still appear in a batch at most once.
    """
    return _disposed(obs_dir, "candidate_id")


def record_paths(obs_dir: Path) -> dict:
    """`{record id: path}` over every `OBS-*.md` under the concern.

    `read_recorded_observations` keys on `anchor_id` and never returns the
    path, and a batch that retires a predecessor needs the FILE — so the map
    is built here rather than by widening a shared reader three other lanes
    depend on. Reads only the id line's frontmatter, on that reader's shape."""
    out: dict[str, Path] = {}
    d = Path(obs_dir)
    if not d.is_dir():
        return out
    for path in sorted(d.glob("OBS-*.md")):
        # [SECURITY:S5] `glob` returns a symlink and `read_text` follows it, so
        # a link planted in the concern directory put a file from OUTSIDE the
        # repository into this map — and `build_plan` turns a map entry into
        # the `retires` cell the sign-off then copies into the concern. Only a
        # RESOLVED containment check decides this; `_assert_contained` never
        # sees it, because it guards a write target, which is contained by
        # construction. `resolve_contained` is the shared remedy, and its own
        # docstring names this case.
        if _oe.resolve_contained(d, path.name) is None:
            raise SurveySheetError([
                f"refusing to read {redact(path.name, quoted=False)}: it "
                f"does not resolve to a file inside {redact(d, quoted=False)} "
                "— an observation record is read from the concern "
                "directory, never through a link out of it"])
        m = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"),
                     re.DOTALL)
        if not m:
            continue
        import yaml
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception as exc:  # fail-closed, as the sibling reader is
            # [SECURITY:S4] The message is the sibling reader's, verbatim:
            # PyYAML's `str(exc)` embeds the offending SOURCE LINE, and the
            # two readers of this directory must not disagree about how much
            # of a file they are willing to quote.
            raise SurveySheetError([
                frontmatter_problem(path.name, exc)]) from exc
        if isinstance(fm, dict) and fm.get("id"):
            out[str(fm["id"])] = path
    return out


def unfinished_batch_anchors(obs_dir: Path) -> dict:
    """`{anchor_id: batch_id}` for every anchor a batch BELOW S9 has bound.

    The scaffold's "one live sheet at a time" refusal is the interlock that
    stops two sheets from ratifying one anchor, and cell 1 DELETES the live
    sheet — so from S1 on the interlock was gone while the batch still had
    every write it makes ahead of it. A second batch could then open a
    successor on a bound anchor and ratify it, leaving the earlier batch's
    resume a choice between refusing forever and publishing a second live
    record on that anchor. Neither is a state the protocol has a remedy for,
    and both are decided BEFORE the second signature or not at all.

    So the receipt carries the interlock the live sheet used to: until a batch
    reaches S9, its anchors are held. A hold, not a disposal — `defer` still
    writes nothing, and the anchor is offered again the moment the batch
    converges, which is what one re-run does.

    Keyed by ANCHOR rather than by candidate: a successor sits on its
    predecessor's anchor, and it is the anchor that at most one live record may
    hold."""
    out: dict[str, str] = {}
    obs_dir = Path(obs_dir)
    for bid in batch_ids(obs_dir):
        receipt_path = receipt_paths(obs_dir, bid)["receipt"]
        if not receipt_path.is_file():
            continue
        if batch_state(receipt_path, obs_dir) == "S9":
            continue
        for row in read_receipt(receipt_path)["rows"]:
            out.setdefault(str(row["anchor_id"]), bid)
    return out


def receipt_covered_anchors(obs_dir: Path) -> set[str]:
    """Every anchor any receipt names, whatever the verdict — the set
    CHK-OBS-SURVEY-RECORD's first disjunct tests membership in."""
    covered: set[str] = set()
    obs_dir = Path(obs_dir)
    for bid in batch_ids(obs_dir):
        receipt_path = receipt_paths(obs_dir, bid)["receipt"]
        if not receipt_path.is_file():
            continue
        for row in read_receipt(receipt_path)["rows"]:
            covered.add(str(row["anchor_id"]))
    return covered


# ── the state derivation (§17.5's state table) ──────────────────────────────

def _log_has_batch(tree_dir: Path, batch_id: str) -> bool:
    log_path = Path(tree_dir) / "log.md"
    if not log_path.is_file():
        return False
    subject = f"survey batch {batch_id}"
    return any(e["op"] == "observation" and e["subject"] == subject
               for e in _sp._log_entries(log_path.read_text(encoding="utf-8")))


def _journal_has_batch(tree_dir: Path, batch_id: str, signed: str) -> bool:
    """Whether the journal carries this batch's review hook.

    The month file is keyed on the receipt's OWN `signed` date, never on today.
    A resume that crosses a month boundary must look where the batch's recorded
    date says the hook is; keying on today would look in the new month, find
    nothing, and re-write a duplicate hook on every later day.
    `signoff-survey.write_journal_hook` writes to the same key."""
    path = Path(tree_dir) / "journal" / f"{str(signed)[:7]}.md"
    if not path.is_file():
        return False
    subject = f"survey sign-off {batch_id}"
    return any(e["category"] == "review" and e["subject"] == subject
               for e in _sp._journal_entries(path.read_text(encoding="utf-8")))


def _state_file_disposed(tree_dir: Path, receipt: dict) -> bool:
    """Whether every disposing row's candidate carries its disposed state.

    An ABSENT state file is disposed by definition — there is nothing left to
    write, and a batch signed against a tree whose state file was since deleted
    must still reach S9 rather than loop forever on a write it cannot make.
    A row whose candidate is gone is likewise disposed: `prune` removes rows,
    and a pruned row is not a pending write.

    The join is on the row's `candidate_id`, never on its anchor. A SUCCESSOR
    candidate sits on its predecessor's anchor, so an anchor join reaches both
    the row this receipt disposed and its predecessor's row — reading a batch
    as undisposed because a neighbouring candidate on the same anchor still
    carries another state. `_cell_14_dispose` joins the same way, so the reader
    and the writer agree about which row a verdict owns."""
    from crux.arch.recover import StateFile
    path = Path(tree_dir).joinpath(*STATE_FILE_REL)
    if not path.is_file():
        return True
    rows = StateFile(path).rows
    wanted = {"ratify": "ratified", "reject": "rejected"}
    for row in receipt["rows"]:
        want = wanted.get(row["verdict"])
        if want is None:
            continue
        cand = rows.get(str(row.get("candidate_id") or row["anchor_id"]))
        if cand is not None and cand.get("state") != want:
            return False
    return True


def planned_record_names(receipt: dict) -> list[str]:
    """The staged filenames one batch plans, sorted: every new record body plus
    every predecessor the batch retires. The index is planned too and is named
    separately, because it promotes LAST (cell 11)."""
    names = set()
    for row in receipt["rows"]:
        if row.get("record_path"):
            names.add(Path(str(row["record_path"])).name)
        if row.get("retires"):
            names.add(Path(str(row["retires"])).name)
    return sorted(names)


def batch_state(receipt_path: Path, obs_dir: Path) -> str:
    """The §17.5 state this batch is in, derived from disk on every call.

    This is the one derivation. The sign-off's resume, the CHK-OBS-SURVEY-*
    audit rules and the summaries/doctrine refusal all call it, so a gate can
    never disagree with the writer about where a batch stands.

    `obs_dir` fixes the tree: the log, the journal and the candidate state file
    are all read relative to its parent, which is what lets the signature stay
    the two arguments §17.5 names.
    """
    receipt_path = Path(receipt_path)
    obs_dir = Path(obs_dir)
    if not receipt_path.is_file():
        return "S0"
    receipt = read_receipt(receipt_path)
    tree_dir = obs_dir.parent
    batch_id = receipt["batch_id"]
    staged = receipt_paths(obs_dir, batch_id)["staged"]

    ratify_rows = [r for r in receipt["rows"] if r["verdict"] == "ratify"]
    # No `bool(ratify_rows)` conjunct: ADR-0098 clause 1 admits an all-`reject`
    # / all-`defer` batch, which allocates NO ids because neither verdict
    # writes a record. `all([])` is True, and that is the correct reading —
    # requiring at least one allocated id would pin such a batch at S1 forever,
    # with no scripted remedy and both projections refusing the whole tree.
    ids_allocated = all(r.get("record_id") for r in ratify_rows)
    planned = planned_record_names(receipt)

    if receipt.get("completed") is None:
        if not ids_allocated:
            return "S1"
        staged_ok = (staged.is_dir()
                     and all((staged / name).is_file() for name in planned)
                     and (staged / "index.md").is_file())
        return "S3" if staged_ok else "S2"

    # Past the commit point. Visibility is what separates S4 from S5.
    promoted = all((obs_dir / name).is_file() for name in planned)
    if not promoted:
        return "S4"
    if staged.exists():
        return "S5"
    if not _log_has_batch(tree_dir, batch_id):
        return "S6"
    if not _journal_has_batch(tree_dir, batch_id, receipt["signed"]):
        return "S7"
    if not _state_file_disposed(tree_dir, receipt):
        return "S8"
    return "S9"


def state_index(state: str) -> int:
    return STATES.index(state)


# ── slug + title derivation (no human string reaches a filename) ────────────

def derive_title(rule: str) -> str:
    """A record's title, derived from the candidate's mined rule.

    ADR-0098 clause 1: no human-authored string reaches a filename, because a
    record's title and slug derive from the CANDIDATE. The rule is machine
    output that a redaction scan already passed (`recover-decisions` step 2),
    so it is the one string available that describes the claim.

    `cell_canon`, not `space_fold`: the title and the seeded cell derive from
    one candidate through one canonicalization, so a rule whose only
    non-ASCII whitespace is a `Zs` cannot title one way and seed another."""
    text = cell_canon(rule)
    head = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0].rstrip(".")
    if len(head) > 80:
        head = head[:80].rsplit(" ", 1)[0]
    return head


def slugify(text: str) -> str:
    """§9's slug rule: kebab-case, ASCII only, max 60 characters.

    Total by construction — a rule made entirely of non-ASCII characters slugs
    to the empty string, and the caller refuses that rather than writing a file
    named `OBS-0001-.md`."""
    ascii_only = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    if len(slug) > 60:
        slug = slug[:60].rstrip("-")
    return slug


def proposed_slug(cand: dict) -> str:
    """The slug the scaffold PROPOSES for one candidate: the title-derived
    slug the record filename and handle carried before the cell existed.

    ONE function, called at two moments — the scaffold writes what it
    returns into the v3 `slug` cell, and the sign-off compares the cell
    against what it returns to record `slug_source` — so `proposed` on a
    receipt means exactly "the human left the scaffold's value", never "two
    code paths happened to agree". The candidate's rule is bound by the
    seeded-cell check, so the recomputation at sign-off sees the rule the
    scaffold saw.

    The proposal is NOT guaranteed to satisfy `SLUG_CELL_RE`: a rule whose
    title opens with a digit proposes a digit-led slug, and a rule with no
    ASCII letter proposes "". Both are refused at sign-off by name, which is
    the human's cue to write the slug — the scaffold fills no human cell
    with anything but the proposal (ADR-0098 clause 4)."""
    return slugify(derive_title(str(cand.get("rule") or "")))


# ── the batch plan (every pre-write refusal) ────────────────────────────────

def build_plan(sheet: dict, candidates: dict, recorded: dict, *,
               root: Path, obs_dir: Path,
               disposed_elsewhere: dict | None = None,
               own_record_ids: dict | None = None,
               bound_candidates: dict | None = None,
               live_slugs: dict | None = None,
               retired_slugs: dict | None = None) -> list[dict]:
    """Validate one batch end-to-end and return its per-row plan.

    `candidates` is `StateFile.rows` (candidate key -> row); `recorded` is
    `read_recorded_observations(obs_dir)` (anchor id -> record). Refuses, as
    one findings list over the WHOLE batch — a batch is signed or it is not,
    and publishing the clean rows of a sheet with a bad one would leave the
    human's signature covering a batch they did not sign:

      * a row whose anchor names no candidate in the state file, or whose
        BOUND candidate is no longer in it;
      * a candidate whose `anchor_kind` is outside `ANCHOR_KINDS`;
      * a candidate with no `rule` or no `evidence`;
      * an evidence entry outside the `path:line-range` grammar, or one that
        does not resolve to a file INSIDE the repository root — a symlinked or
        escaping path refuses the whole batch;
      * a SUCCESSOR candidate naming no predecessor;
      * a successor whose `predecessor_id` matches no recorded record;
      * a plain `ratify` on an anchor a live record already holds (the
        successor lane is the only way onto an occupied anchor, and admitting
        this would leave two live records on one anchor: CHK-OBS-ANCHOR);
      * a row another batch's receipt has already disposed;
      * a claim that MOVED between scaffold and sign-off — the candidate's
        `claim_digest` no longer matches the digest the scaffold recorded;
      * a `ratify` row whose slug is EMPTY, MALFORMED (outside
        `SLUG_CELL_RE`), ALREADY CLAIMED by an earlier `ratify` row of the
        SAME batch, or ALREADY TAKEN by a live slug or a retired one
        (ADR-0099 clause 3). The slug is the v3 `slug` cell; a v1/v2 row
        carries no cell and derives it from the title as it always did, and
        the same refusals then apply to the derived value.

    `live_slugs` and `retired_slugs` are the two maps
    `summaries_projection.live_and_retired_slugs` returns — live slug to the
    handle that owns it, retired slug to the handles that displaced it. Both
    default to empty, which is the document-only lane a caller holding no
    corpus gets; the sign-off always passes both.

    Those two maps are built ONCE, from the corpus as it stood before the
    batch, so they cannot see the batch's own rows — which is why the loop
    also carries a per-batch slug ledger. Two candidates whose rules derive
    one title propose one slug, both rows pass the corpus check, and both
    records publish two live handles on one slug. The projection then refuses
    the WHOLE tree fail-closed and the batch wedges. The ledger is what makes
    the refusal fire here, on the sheet, in the same lane as every other
    sheet finding.

    `own_record_ids` is `{anchor_id: record_id}` off the batch's OWN receipt:
    the record THIS batch allocated for THAT row. It is row-scoped on
    purpose. A live record on a row's anchor, or a live slug owned by that
    record, is the row's own output on a resume and is not a collision — but
    it is only ever the OWNING row's. Paired against every record id in the
    batch it excused a PEER row proposing another row's slug, which is the
    intra-batch collision above wearing the exemption as cover.

    A `reject` or `defer` row writes no record, so it is validated for its
    anchor and its candidate's existence and no further: refusing a batch
    because a candidate the human is REJECTING has unresolvable evidence would
    make a bad candidate unrejectable.

    `bound_candidates` is `{anchor_id: candidate_id}` off the batch's OWN
    receipt, and it is the row's candidate whenever the receipt exists. A row
    is bound at cell 1; every later run re-derives its plan, and re-RESOLVING
    the anchor there let a candidate that appeared after the signature take the
    row. `_pick_candidate` prefers a successor, so a re-mine that opened one on
    a bound anchor swapped the claim under the signature: the receipt named the
    plain candidate, cell 3 allocated a record id and a slug for ITS rule, and
    the resume staged the successor's rule under that id. A signature covers
    the candidate it named, so the receipt decides and the state file is only
    asked for that candidate's row.
    """
    problems: list[str] = []
    plan: list[dict] = []
    disposed_elsewhere = disposed_elsewhere or {}
    live_slugs = live_slugs or {}
    retired_slugs = retired_slugs or {}
    own_record_ids = own_record_ids or {}
    # slug -> the anchor of the row that claimed it, over THIS batch only.
    batch_slugs: dict[str, str] = {}
    batch_id = sheet["batch_id"]
    _record_index = record_paths(obs_dir)
    by_anchor: dict[str, list[tuple[str, dict]]] = {}
    for cid, cand in candidates.items():
        by_anchor.setdefault(anchor_of(cid), []).append((cid, cand))

    for signed in signed_rows(sheet):
        anchor = signed["anchor_id"]
        label = f"row {anchor}"
        matches = sorted(by_anchor.get(anchor, []))
        bound = (bound_candidates or {}).get(anchor)
        if bound is not None:
            matches = [m for m in matches if m[0] == bound]
            if not matches:
                problems.append(
                    f"{label} is bound by this batch's receipt to candidate "
                    f"{redact(bound, quoted=False)}, which the candidate "
                    "state file no longer "
                    "carries — a signature covers the candidate it named, so "
                    "the row is never re-resolved onto another one")
                continue
        if not matches:
            problems.append(f"{label} names an anchor no candidate in the "
                            "state file carries")
            continue
        cid, cand = matches[-1] if len(matches) == 1 else _pick_candidate(matches)
        # The already-disposed refusal applies to the rows that WRITE, which is
        # exactly `DISPOSING_VERDICTS`. A `defer` row writes nothing — no
        # record, and nothing in the candidate state file — so another batch
        # having disposed the same candidate takes nothing away from it.
        #
        # Running it on a `defer` row wedged the deferring batch permanently.
        # `defer` is the one verdict that leaves a candidate re-scaffoldable
        # (§17.5), so a later batch disposing it is the DESIGNED path, not a
        # conflict; and cell 1 deletes the live sheet, so the one-live-sheet
        # interlock is already gone by the time the earlier batch sits bound.
        # The earlier batch could then never leave that state — no scripted
        # remedy, and `summaries_projection` refusing the whole tree's
        # summaries and doctrine for as long as it stayed below S9, printing a
        # remedy that is the command that refuses.
        other = disposed_elsewhere.get(cid)
        if (signed["verdict"] in DISPOSING_VERDICTS
                and other is not None and other != batch_id):
            problems.append(
                    f"{label} candidate {redact(cid, quoted=False)} was "
                    f"already disposed by batch {redact(other, quoted=False)}"
                    " — a candidate is disposed once")
            continue
        # The seeded claim, on EVERY verdict. `rule` and `evidence` are what
        # the human read; the candidate is what the record carries. A
        # divergence between them is a refusal and never a silent preference
        # for either — whether it arrived by a re-mine (the candidate moved)
        # or by an edit to the machine-written cell (the sheet moved). It runs
        # on `reject` and `defer` too: those verdicts dispose a candidate, and
        # disposing a claim nobody read is the same defect as publishing one.
        seeded = seed_cells(cand)
        moved = False
        for key in SEEDED_ROW_KEYS:
            if signed.get(key) is None:      # a v1 row seeds nothing
                continue
            if signed[key] != seeded[key]:
                moved = True
                problems.append(
                    f"{label} sheet cell `{key}` no longer matches the "
                    f"candidate state file: the sheet reads "
                    f"{redact(signed[key], limit=MESSAGE_LIMIT)} and candidate "
                    f"{redact(cid, quoted=False)} now reads "
                    f"{redact(seeded[key], limit=MESSAGE_LIMIT)} — the sheet is "
                    "what the human read and the candidate is what the record "
                    "carries, so a batch refuses rather than publishing "
                    "either. Re-scaffold to review the claim as it stands.")
        if moved:
            continue

        kind = cand.get("anchor_kind")
        if kind not in ANCHOR_KINDS:
            problems.append(
                    f"{label} candidate {redact(cid, quoted=False)} carries "
                    f"anchor_kind {redact(kind)}, outside {list(ANCHOR_KINDS)}")
            continue
        # The slug: the v3 cell, or the title-derived value a row with no
        # cell always had. `slug_source` is decided by comparing the cell
        # with the proposal recomputed from the bound candidate, through the
        # same `proposed_slug` the scaffold wrote it with — recorded on every
        # verdict, because the receipt row carries it on every verdict.
        proposal = proposed_slug(cand)
        cell = signed.get("slug")
        slug = proposal if cell is None else cell
        entry = {
            "anchor_id": anchor,
            "candidate_id": cid,
            "verdict": signed["verdict"],
            "domain": signed["domain"],
            "domain_source": signed["domain_source"],
            "slug_source": "proposed" if slug == proposal else "overridden",
            "record_id": "",
            "record_path": "",
            "retires": "",
        }
        if signed["verdict"] != "ratify":
            plan.append(entry)
            continue

        rule = cand.get("rule")
        evidence = cand.get("evidence")
        if not rule or not str(rule).strip():
            problems.append(
                f"{label} candidate {redact(cid, quoted=False)} carries no rule")
            continue
        if not isinstance(evidence, list) or not evidence:
            problems.append(
                f"{label} candidate {redact(cid, quoted=False)} carries no evidence")
            continue
        bad_evidence = False
        for item in evidence:
            parsed = _oe.parse_evidence(item)
            if parsed is None:
                problems.append(f"{label} evidence entry {redact(item)} is outside the "
                                "path:line-range grammar")
                bad_evidence = True
                continue
            if not _oe.evidence_path_resolves(root, parsed[0]):
                problems.append(
                    f"{label} evidence path {redact(parsed[0])} does not resolve to a "
                    "file inside the repository root")
                bad_evidence = True
        if bad_evidence:
            continue

        # The record THIS row allocated on a previous run of this batch, and
        # the only record any exemption below admits.
        own_id = own_record_ids.get(anchor)
        recorded_digest = cand.get("claim_digest")
        live_digest = claim_digest(rule, evidence)
        predecessor = cand.get("predecessor_id")
        is_successor = cid != anchor
        if is_successor:
            if not predecessor:
                problems.append(f"{label} candidate {redact(cid, quoted=False)} is a "
                                "successor naming "
                                "no predecessor — the batch cannot derive the "
                                "retirement it implies")
                continue
            # Resolved through the ID-keyed index, never through `recorded`.
            # `read_recorded_observations` keys on `anchor_id`, and a successor
            # sits on its predecessor's anchor — so from the moment the
            # successor is promoted the predecessor is no longer reachable in
            # that map. A resume validating against it refused its own output
            # and live-locked the batch below S9.
            pred_path = _record_index.get(str(predecessor))
            if pred_path is None:
                problems.append(f"{label} names predecessor {redact(predecessor)}, "
                                "which is no recorded observation")
                continue
            if recorded_digest and recorded_digest != live_digest:
                problems.append(
                    f"{label} candidate {redact(cid, quoted=False)} carries "
                    f"claim_digest {redact(recorded_digest, quoted=False)} "
                    f"but its live claim digests to "
                    f"{redact(live_digest, quoted=False)} — the claim moved "
                    "since it was mined")
                continue
            # `retires` carries the predecessor's tree-relative PATH, not its
            # id: the retirement stages and promotes through the same
            # `staged/<filename>` mechanism the new records use, and
            # `planned_record_names` joins the two on the filename. The id is
            # recoverable from the stem.
            entry["retires"] = f"{Path(obs_dir).name}/{pred_path.name}"
        else:
            live = recorded.get(anchor)
            # A record THIS batch already promoted is not a second live record
            # on the anchor — it IS this row's record. `build_plan` re-runs on
            # every resume, and from S5 on the batch's own output is visible,
            # so without this the protocol's own publish would refuse the
            # remaining cells of the batch that wrote it. Scoped to THIS
            # row's own record: a record another row of the batch published
            # sits on another anchor, so it is a second live record here.
            if live is not None and own_id and live.get("id") == own_id:
                live = None
            if live is not None and live.get("status") in ("observed", "ratified"):
                problems.append(
                    f"{label} would ratify a second live record on an anchor "
                    f"{redact(live['id'], quoted=False)} already holds — a "
                    "changed claim comes through "
                    "the successor lane, which retires its predecessor")
                continue

        title = derive_title(str(rule))
        # The slug is the record's filename AND its governs handle, so it is
        # refused on the grammar both accept (`SLUG_CELL_RE`) and on the
        # uniqueness the resolver enforces (ADR-0099 clause 2): a live slug
        # is owned by one handle, and a retired slug is never reused because
        # a citation of it must keep naming the rules that displaced it. The
        # refusal fires HERE, with the sheet still in the human's hands,
        # rather than at the projection after the batch published.
        where = ("cell `slug`" if cell is not None
                 else "title-derived slug")
        if not slug:
            problems.append(
                f"{label} {where} is empty — the slug is the record's "
                "filename and its governs handle, so the sheet must name one")
            continue
        if not valid_slug_cell(slug):
            problems.append(
                f"{label} {where} {redact(slug)} is malformed — a slug is a "
                "lowercase letter followed by lowercase letters, digits and "
                "single hyphens, because it must satisfy both the record "
                "filename grammar and the governs handle grammar")
            continue
        # The batch's own rows, which the corpus maps cannot see: they were
        # built before the batch and a first sign-off has published nothing.
        # The second row to name a slug refuses, both rows are named, and the
        # findings list refuses the whole batch — so neither publishes.
        claimed_by = batch_slugs.get(slug)
        if claimed_by is not None:
            problems.append(
                f"{label} {where} {redact(slug)} is already claimed by row "
                f"{redact(claimed_by, quoted=False)} in this same batch — the "
                "slug half of every live handle is unique across the whole "
                "resolver, so one batch cannot publish two records under one "
                "slug; change one of the two rows and neither is published "
                "until you do")
            continue
        batch_slugs[slug] = anchor
        owner = live_slugs.get(slug)
        # The exemption is this row's own record and nothing else: a peer
        # row's record owns a slug this row may not take.
        own_handle = f"{own_id}/{slug}" if own_id else None
        if owner is not None and owner != own_handle:
            problems.append(
                f"{label} {where} {redact(slug)} is already taken by the "
                f"live handle {redact(owner, quoted=False)} — the slug half "
                "of every live handle is unique across the whole resolver")
            continue
        if slug in retired_slugs:
            displaced_by = ", ".join(retired_slugs[slug]) or "(no live displacer)"
            problems.append(
                f"{label} {where} {redact(slug)} is a retired slug, displaced "
                f"by {redact(displaced_by, quoted=False)} — a retired slug is "
                "never reused, because a citation of it must keep naming the "
                "rules that displaced it")
            continue
        entry["title"] = title
        entry["slug"] = slug
        # Copied from the CANDIDATE, through the same `cell_canon` the
        # seeded cell took — never from the sheet cell, which is what the
        # human READ and was checked against this same value above.
        entry["rule"] = cell_canon(rule)
        entry["evidence"] = [cell_canon(e) for e in evidence]
        entry["scope"] = ", ".join(
            _oe.parse_evidence(e)[0] for e in entry["evidence"])
        plan.append(entry)

    _refuse(problems)
    return sorted(plan, key=lambda e: e["anchor_id"])


def _pick_candidate(matches):
    """The candidate a row means when an anchor carries both a plain row and a
    successor row: the SUCCESSOR. `emit_candidate` opens at most one successor
    per anchor and opens it only because the recorded claim changed, so the
    successor is the newer proposal and the plain row is the one the record
    already covers."""
    successors = [m for m in matches if "+" in m[0]]
    return successors[-1] if successors else matches[-1]
