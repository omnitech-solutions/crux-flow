# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0"]
# ///
"""summaries_projection.py — the importable core of the summaries projection.

Per the governs-block decision (ADR-0085 Decision 2) and its coexistence
follow-on (ADR-0086), one deterministic projection reads ADR `governs` blocks
and run-snapshot artifact bindings and builds three derived artifacts behind one
drift gate: a rule table, a handle->rule resolver, and an ADR<->run
implementation map. This module holds the shared machinery; four drivers
import it:

  - summarize-adrs.py           the regenerator + --dry-run drift gate
  - lint-governs-references.py    the two-tier reference linter
  - check-governs-coverage.py     the governs-coverage gate
  - signoff-backfill.py           the owner sign-off write path (a heavy
                                  consumer: read_reviews, backfill_problems,
                                  content_digest, resolve_entry, and more)

Determinism is the contract: everything sorts, and there are no timestamps
anywhere in the produced artifacts.

Key design points, honored exactly:
  - The input hash covers ADR frontmatter DIRECTLY (glob active ADR-*.md and
    hash the frontmatter blocks). It NEVER reads adrs/index.md — that is a gated
    downstream index, and the summaries hash must not drift behind it (ADR-0086
    Decision 3).
  - Disposition and authority are DERIVED from `provenance`, never stored:
    authored -> decided/prescriptive; recovered|reconstructed ->
    observed/descriptive (ADR-0085 Decision 3).
  - The implementation map reads .yaml run snapshots only.
  - The input domain is active ADRs UNION `ratified` observations
    (docs/AGENTS.md §17; ADR-0095 requirement 4). An observation record is
    read through the SAME record construction as an ADR entry, so the rule
    table, the resolver, and the content digest need no second parser. The
    record dict carries `source_kind` in {"adr", "observation"}; `source_adr`
    holds the HOST RECORD id (`ADR-NNNN` or `OBS-NNNN`) and `adr_num` its
    number. Both field names are kept deliberately: renaming them would move
    the rule table's `source ADR` column header and the resolver's row key,
    and the zero-observation identity postcondition forbids that.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


def _evidence_module():
    """The shared evidence grammar + containment helper, loaded BY PATH.

    The pattern `check_observations._evidence_module` uses, for the same
    reason: this file is executed by `spec_from_file_location` from several
    test modules and from scripts that never put `crux/scripts/` on
    `sys.path`, so a plain `import observation_evidence` cannot be assumed.
    Loaded once, into `sys.modules` under a private key the two loaders share.
    """
    import importlib.util as _ilu
    key = "_observation_evidence"
    m = sys.modules.get(key)
    if m is None:
        path = Path(__file__).resolve().parent / "observation_evidence.py"
        spec = _ilu.spec_from_file_location(key, path)
        m = _ilu.module_from_spec(spec)
        spec.loader.exec_module(m)
        sys.modules[key] = m
    return m


def _untrusted_module():
    """The shared bound-and-redact helper, loaded BY PATH.

    Same reason as `_evidence_module` above: this file is executed by
    `spec_from_file_location` from modules that never put `crux/scripts/` on
    `sys.path`."""
    import importlib.util as _ilu
    key = "_untrusted"
    m = sys.modules.get(key)
    if m is None:
        path = Path(__file__).resolve().parent / "untrusted.py"
        spec = _ilu.spec_from_file_location(key, path)
        m = _ilu.module_from_spec(spec)
        spec.loader.exec_module(m)
        sys.modules[key] = m
    return m


# Bound at import so every call site reads `redact(...)` — the name the
# enrollment gate looks for, and the name the reader's own docstring cites.
redact = _untrusted_module().redact
MESSAGE_LIMIT = _untrusted_module().MESSAGE_LIMIT
parse_problem = _untrusted_module().parse_problem


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
_ADR_ID_RE = re.compile(r"ADR-\d{4}")


# Private operation I/O seam. Parsers and authority policy remain in this module.
def _source_text(path: Path, source_io) -> str:
    return path.read_text(encoding="utf-8") if source_io is None else source_io.read_text(path)


def _source_bytes(path: Path, source_io) -> bytes:
    return path.read_bytes() if source_io is None else source_io.read_bytes(path)


def _source_is_file(path: Path, source_io) -> bool:
    return path.is_file() if source_io is None else source_io.kind(path) == "file"


def _source_is_dir(path: Path, source_io) -> bool:
    return path.is_dir() if source_io is None else source_io.kind(path) == "directory"


def _source_exists(path: Path, source_io) -> bool:
    return path.exists() if source_io is None else source_io.kind(path) is not None


def _source_glob(path: Path, pattern: str, source_io):
    return path.glob(pattern) if source_io is None else source_io.glob(path, pattern)


def _source_resolve(path: Path, source_io) -> Path:
    return path.resolve() if source_io is None else source_io.resolve(path)


def _source_contained(root: Path, name: str, source_io) -> Path | None:
    if source_io is None:
        return _evidence_module().resolve_contained(root, name)
    if not _evidence_module().valid_evidence_path(name):
        return None
    try:
        root_r = source_io.resolve(root)
        resolved = source_io.resolve(root_r / name)
    except (OSError, RuntimeError):
        return None
    return resolved if root_r in resolved.parents else None


# ── tree resolution ────────────────────────────────────────────────────────

def tree_name(root: Path, *, _source_io=None) -> str:
    """Resolve `docs_dir` from `.bionic.yml`, defaulting to `bionic`."""
    cfg = root / ".bionic.yml"
    if _source_exists(cfg, _source_io):
        m = re.search(r"^docs_dir\s*:\s*[\"']?([^\"'\s#]+)", _source_text(cfg, _source_io),
                      re.MULTILINE)
        if m:
            return m.group(1)
    return "bionic"


def artifact_prefix(root: Path, *, _source_io=None) -> str:
    """The §14.3 `artifact_prefix` this tree runs, or "" when it runs none.

    Read the way `tree_name` reads `docs_dir`, from the same two files, and
    NOT through `bionic_config.load_config`: that loader requires
    `config_version`, which this lane's tree resolution has never required, so
    routing the prefix through it would refuse trees the lane supports today.
    The legacy `.crux` is scanned too, because a prefix this reader missed is
    a prefix a caller would treat as absent.

    Deliberately lenient about what counts as a prefix and strict about what
    counts as none: any non-empty captured value is a prefix. A spelling this
    regex reads as `>-` rather than as `CRX` still refuses, which is the safe
    direction for the one caller — `survey_sheet.assert_unprefixed_tree`.
    """
    for name in (".bionic.yml", ".crux"):
        cfg = Path(root) / name
        if not _source_is_file(cfg, _source_io):
            continue
        m = re.search(r"^artifact_prefix\s*:\s*[\"']?([^\"'\s#]+)",
                      _source_text(cfg, _source_io), re.MULTILINE)
        if m:
            return m.group(1)
    return ""


def resolve_tree(root: Path, *, _source_io=None) -> Path:
    """Return the tree path for `root`, proven contained under it.

    A committed `.bionic.yml` is repo content, and every governs lane reads
    and writes under the tree it names — so an escaping value would steer
    those reads/writes outside the repo root. This is §14.2's refusal in code
    for the summaries lane: textual refusal first (an absolute, `~`-prefixed, or
    `..`-carrying `docs_dir` is rejected without touching the filesystem),
    then a containment assertion on the RESOLVED pair (`root.resolve()` vs
    the resolved tree path) — a symlinked tree dir that escapes the root
    fails the leg even though the joined spelling would not. The RETURNED
    path is the plain join `root / docs_dir`: containment is the check, not
    an input to the value, so callers that key maps on joined paths are
    unaffected.

    Raises GovernsValidationError — the document-verdict lane every driver
    already surfaces as exit-1 findings, never a traceback.
    """
    raw = tree_name(root, _source_io=_source_io)
    segments = raw.replace("\\", "/").split("/")
    if (raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", raw)
            or any(seg in ("", ".", "..") for seg in segments)):
        raise GovernsValidationError([_gate_problem(
            f".bionic.yml docs_dir {raw!r} must be a clean repo-root-relative "
            "path — absolute, ~-prefixed, and empty/dot/dot-dot-carrying "
            "values are refused")])
    joined = Path(root) / raw
    root_r = _source_resolve(Path(root), _source_io)
    resolved = _source_resolve(joined, _source_io)
    if resolved == root_r or root_r not in resolved.parents:
        raise GovernsValidationError([_gate_problem(
            f".bionic.yml docs_dir {raw!r} resolves to {resolved}, which is "
            f"not contained under the repo root {root_r} — a symlinked or "
            "escaping tree dir is refused")])
    return joined


def adrs_dir(root: Path, *, _source_io=None) -> Path:
    return resolve_tree(root, _source_io=_source_io) / "adrs"


def runs_dir(root: Path, *, _source_io=None) -> Path:
    return resolve_tree(root, _source_io=_source_io) / "promptbooks" / "runs"


def observations_root(tree: Path, *, _source_io=None) -> Path:
    """The observations concern directory under an ALREADY-RESOLVED tree,
    proven contained under it.

    [SECURITY:S5] The ROOT leg of the containment this concern's readers
    already carry on their entries. `observation_paths`,
    `survey_receipt_paths`, `recover._contained_record` and
    `survey_sheet.record_paths` each resolve a NAME against `obs` — and `obs`
    itself was never validated, so `resolve_contained(obs, name)` computed
    `root_r = obs.resolve()` and compared an outside directory against itself,
    admitting everything under it. A RELATIVE symlink at
    `<docs_dir>/observations` is git-carryable, so a pull request could put a
    file from outside the repository into `resolver.json`, `rule-table.md` and
    the doctrine index compiled from them, at exit 0.

    Decided here because this is the one resolution of the directory, and the
    root has to be decided before anything resolves against it — the shape
    `survey_sheet.receipt_paths` already uses for the holding area. A link
    landing INSIDE the tree stays admissible: containment is a property of the
    resolved path, never of being a link.

    Raises `ValueError` with the sentence its sibling legs use, naming the
    directory and quoting no byte of it. `observations_source_problem` returns
    it as a problem string, exactly as it already does for the two legs one
    level down."""
    tree = Path(tree)
    if _source_contained(tree, "observations", _source_io) is None:
        raise ValueError(
            f"refusing to read the observations concern under "
            f"{redact(tree, quoted=False)}: `observations` does not resolve "
            "to a directory inside the tree — a concern is read from the "
            "tree, never through a link out of it")
    return tree / "observations"


def observations_dir(root: Path, *, _source_io=None) -> Path:
    """The observations concern directory for a repository root, proven
    contained. See `observations_root`."""
    return observations_root(resolve_tree(root, _source_io=_source_io), _source_io=_source_io)


# ── the observations half of the input domain (docs/AGENTS.md §17) ─────────

# The one observation status that projects rule rows, and the one that
# projects a resolver ALIAS row only (ADR-0095 requirement 4). Every other
# status — observed, rejected, retired — projects nothing.
OBSERVATION_PROJECTED_STATUS = "ratified"
OBSERVATION_ALIAS_STATUS = "decided"

# §17.1: the invariants enum minus `authored` — an authored rule is an ADR.
OBSERVATION_PROVENANCE_ENUM = ("recovered", "reconstructed")

# The closed set of source ids a summaries build can declare in
# `_meta.json`'s `input_domain`. A declared member outside this set is a
# projection this build does not understand, and it refuses to rewrite it.
INPUT_DOMAIN_KNOWN = ("adrs", "backfill-reviews", "observations",
                      "survey-receipts", "implementation-migration")


def observation_paths(obs: Path, *, _source_io=None) -> list[Path]:
    """Sorted observation record files under `obs` — every `*.md` except the
    concern index, the same file set `check_observations.py` walks. An absent
    directory yields [].

    [SECURITY:S5] `glob` returns a symlink and every caller `read_text`s what
    this returns, so a link planted in the concern directory published a file
    from OUTSIDE the repository into three committed artifacts — `resolver
    .json`, `rule-table.md`, and the doctrine index compiled from them — at
    exit 0. This is the third reader of this directory;
    `crux.arch.recover._contained_record` and `survey_sheet.record_paths` carry
    the identical leg, and it is the only one that WRITES. Containment is a
    property of the RESOLVED path, never of the spelling or of being a link:
    `resolve_contained` is the one place that decides it, and a link landing
    inside the concern stays admissible.

    Raises `ValueError`, as `recover._contained_record` does, naming the file
    and quoting no byte of it. `observations_source_problem` converts the raise
    into the problem string its own contract promises; every other caller in
    this module runs after that lane has already passed.
    """
    out = []
    for p in sorted(_source_glob(obs, "*.md", _source_io)):
        if p.name == "index.md":
            continue
        resolved = _source_contained(obs, p.name, _source_io)
        if resolved is None:
            raise ValueError(
                f"refusing to read {redact(p.name, quoted=False)}: it does "
                f"not resolve to a file inside {redact(obs, quoted=False)} "
                "— an observation record is read from the "
                "concern directory, never through a link out of it")
        if not _source_is_file(resolved, _source_io):
            continue        # a directory named `*.md`, as the old `is_file`
        out.append(p)       # leg skipped, or a dangling link landing inside
    return out


def observations_sha256(obs: Path, *, _source_io=None) -> str:
    """SHA-256 over the frontmatter blocks of the observation records, in
    filename order — the same construction as `adr_frontmatter_sha256`
    (filename, NUL, frontmatter block, NUL), so a ratification, a retirement,
    or a ratified successor drifts the projection exactly as an ADR
    frontmatter mutation does. An empty corpus hashes to the empty digest."""
    h = hashlib.sha256()
    for path in observation_paths(obs, _source_io=_source_io):
        block = _frontmatter_block(_source_text(path, _source_io))
        if block is None:
            continue
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(block.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def survey_receipt_paths(obs: Path, *, _source_io=None) -> list[Path]:
    """Every batch receipt under `observations/_surveys/*/receipt.yml`, sorted
    by batch id (ADR-0098 clause 2 — the per-batch receipts are one declared
    source in this input domain).

    Only the receipt. The signed sheet beside it is bound BY the receipt's
    digest, so hashing both would move the input hash twice for one change.
    The frozen holding area is invisible to `observation_paths`, whose
    non-recursive `glob("*.md")` is what keeps a `_surveys/` file out of the
    record walk.

    [SECURITY:S4/S5] Two RESOLVED containment legs, on the two ancestors this
    walk traverses. `survey_sheet._load_yaml_doc` guards the LEAF — a receipt
    that is itself a symlink — and that is the only leg there was. A symlink at
    `<obs>/_surveys`, or at one batch directory under it, leaves the receipt a
    real file, so the leaf check passed and the outside file was read; and it
    was read into a message, because `validate_receipt` quotes CELL VALUES
    (`batch receipt digest 'deadbeef' is not 64 lowercase hex digits`), which
    `observations_source_problem` returns to the caller's stderr.
    `survey_sheet._assert_contained` is this leg's write-side twin and cannot
    see this: it guards a write target.
    """
    base = Path(obs) / "_surveys"
    resolved_base = _source_contained(obs, "_surveys", _source_io)
    if resolved_base is None:
        raise ValueError(
            f"refusing to read the survey holding area under "
            f"{redact(obs, quoted=False)}: "
            "`_surveys` does not resolve to a directory inside the concern — "
            "a batch receipt is read from the concern directory, never "
            "through a link out of it")
    if not _source_is_dir(resolved_base, _source_io):
        return []
    out = []
    batches = base.iterdir() if _source_io is None else _source_glob(base, "*", _source_io)
    for batch in sorted(batches):
        if not _source_is_dir(batch, _source_io):
            continue
        resolved = _source_contained(obs, f"_surveys/{batch.name}/receipt.yml", _source_io)
        if resolved is None:
            raise ValueError(
                f"refusing to read {redact(batch.name, quoted=False)}"
                "/receipt.yml: it does not resolve to a file inside "
                f"{redact(base, quoted=False)} — a batch receipt is read "
                "from the concern directory, never through a link out of it")
        if _source_is_file(resolved, _source_io):
            out.append(batch / "receipt.yml")
    return out


def survey_receipts_sha256(obs: Path, *, _source_io=None) -> str | None:
    """SHA-256 over the receipts' raw bytes in batch-id order, or None when the
    tree has signed no batch.

    Mirrors `backfill_reviews_sha256` — primary authored data hashed as raw
    bytes, null when absent — with the one difference the shape forces: there
    are N receipts rather than one file, so each contributes (batch id, NUL,
    bytes, NUL), the same framing `adr_frontmatter_sha256` uses to keep a
    rename from colliding with a content change."""
    paths = survey_receipt_paths(obs, _source_io=_source_io)
    if not paths:
        return None
    h = hashlib.sha256()
    for path in paths:
        h.update(path.parent.name.encode("utf-8"))
        h.update(b"\0")
        h.update(_source_bytes(path, _source_io))
        h.update(b"\0")
    return h.hexdigest()


def survey_receipts_problem(root: Path, manifest: dict, *, _source_io=None) -> str | None:
    """Why this build cannot read the survey receipts, or None when it can.

    §17.5 row 17: while a batch is below S9 the projection refuses rather than
    renders. That is leg 2 of the atomic-publish resolution — a multi-file
    promote cannot make postcondition (a) true by write ordering, so it is
    enforced at the READER. The state is derived through
    `survey_sheet.batch_state`, the same function the sign-off resumes from and
    the audit rules read, so this gate cannot disagree with the writer.

    `survey_sheet` is imported lazily because it imports THIS module: the
    circular edge exists only at call time, never at import time.
    """
    concerns = manifest.get("concerns_enabled") or []
    if "observations" not in concerns:
        return None
    obs = observations_dir(root, _source_io=_source_io)
    if not _source_is_dir(obs, _source_io):
        return None
    import survey_sheet as _ss
    for path in survey_receipt_paths(obs, _source_io=_source_io):
        batch_id = path.parent.name
        try:
            state = (_ss.batch_state(path, obs) if _source_io is None else
                     _ss.batch_state(path, obs, _source_io=_source_io))
        except _ss.SurveySheetError as exc:
            return (f"survey batch receipt "
                    f"{redact(batch_id, quoted=False)}/receipt.yml is not "
                    f"readable: "
                    f"{redact(exc, quoted=False, limit=MESSAGE_LIMIT)}")
        if state != "S9":
            return (f"survey batch {redact(batch_id, quoted=False)} is at "
                    f"{state}, below S9 — its "
                    "publish is unfinished, so rendering it would expose a "
                    "partially applied batch; re-run signoff-survey.py")
    return None


def observations_source_problem(root: Path, manifest: dict, *, _source_io=None) -> str | None:
    """Why this build cannot read the observations source, or None when it
    can: the concern is absent from `concerns_enabled`, the directory is
    absent, a record's frontmatter does not parse to a mapping with an `id`,
    or a survey batch has not finished publishing. Drives both the fresh
    build's domain and the S5 refusal lane."""
    concerns = manifest.get("concerns_enabled") or []
    if "observations" not in concerns:
        return "`observations` is not in manifest.yml concerns_enabled"
    try:
        obs = observations_dir(root, _source_io=_source_io)
    except ValueError as exc:
        # The ROOT containment refusal, on the same terms as the record walk's
        # and the holding area's below: a problem string, not a raise. This
        # function's contract is "why this build cannot read the source, or
        # None", and a raise here would reach each driver's generic handler
        # and be reported as an unhandled type.
        return str(exc)
    if not _source_is_dir(obs, _source_io):
        return ("the observations directory "
                f"{redact(obs, quoted=False)} is absent")
    import yaml
    try:
        paths = observation_paths(obs, _source_io=_source_io)
    except ValueError as exc:
        # The containment refusal arrives here as a PROBLEM STRING, not as a
        # raise. This function's contract is "why this build cannot read the
        # source, or None", and `declared_domain_refusal` calls it for exactly
        # that sentence; a raise would instead reach each script's generic
        # `except Exception` and be reported as an unhandled type. The sentence
        # names the file and quotes no byte of it.
        return str(exc)
    for path in paths:
        try:
            fm = read_frontmatter(_source_text(path, _source_io))
        except yaml.YAMLError as exc:
            # [SECURITY:S4] PyYAML's `str(exc)` embeds `Mark.get_snippet()` —
            # the offending SOURCE LINE. This walk reads a concern directory
            # whose contents it does not own, and this string is returned to
            # stdout, so quoting the line printed a file the caller never
            # named. The shared sentence is `crux.arch.recover
            # .frontmatter_problem`, and it carries the position only.
            from crux.arch.recover import frontmatter_problem
            return (f"observation record {redact(path.name, quoted=False)} "
                    "is not readable: "
                    + frontmatter_problem(path.name, exc))
        if not isinstance(fm, dict) or not fm.get("id"):
            return (f"observation record {redact(path.name, quoted=False)} has "
                "no parseable frontmatter id")
    try:
        return survey_receipts_problem(root, manifest, _source_io=_source_io)
    except ValueError as exc:
        # `survey_receipt_paths`'s containment refusal, on the same terms as
        # the record walk's above: a problem string, not a raise.
        return str(exc)


def observations_source(root: Path, manifest: dict, *, _source_io=None) -> Path | None:
    """THE one resolution of the observations input source, shared by every
    reader of the widened domain (ADR-0095 requirement 4).

    Returns the observations directory when the concern is enabled and
    readable, and None when the concern is not enabled. Raises
    `FileNotFoundError` when the concern IS enabled but this build cannot read
    it — an enabled concern a reader cannot read is an environment error, never
    a silently narrower domain.

    It exists as one function because it was briefly three. The regenerators
    (`summarize-adrs.py`, `compile-doctrine.py`) each inlined this predicate
    while the consistency gate (`check-doctrine-reconciliation.py`) omitted it,
    so the gate read a narrower domain than the regenerator and reported clean
    on a tree the regenerator marked BROKEN. A gate that disagrees with the
    artifact it gates is a false green, so the predicate has exactly one
    implementation and every lane calls it.
    """
    concerns = manifest.get("concerns_enabled") or []
    if "observations" not in concerns:
        return None
    problem = observations_source_problem(root, manifest, _source_io=_source_io)
    if problem is not None:
        raise FileNotFoundError(f"cannot read the observations source: {problem}")
    return observations_dir(root, _source_io=_source_io)


def declared_input_domain(summaries: Path, *, _source_io=None) -> list[str] | None:
    """The `input_domain` an existing `<summaries>/_meta.json` declares, or
    None when there is no declaration: the file is absent, is not a JSON
    mapping, or predates the declaration (a schema-2 projection). A
    declaration that is not a list of strings is returned as-is so the
    caller's known-set check refuses it."""
    import json
    path = summaries / "_meta.json"
    if not _source_is_file(path, _source_io):
        return None
    # A guarded read refusal is not a missing declaration. Keep the legacy
    # unguarded read fallback while letting operation I/O failures propagate.
    guarded_text = _source_text(path, _source_io) if _source_io is not None else None
    try:
        doc = json.loads(path.read_text(encoding="utf-8") if _source_io is None else guarded_text)
    except (ValueError, OSError):
        return None
    if not isinstance(doc, dict) or "input_domain" not in doc:
        return None
    return doc["input_domain"]


class _DomainRefusal(str):
    """A refusal reason that also carries the migration refusal's path-free remedy, if any."""
    def __new__(cls, reason: str, remedy: str | None):
        text = super().__new__(cls, reason)
        text.remedy = remedy
        return text


def declared_domain_refusal(root: Path, manifest: dict, summaries: Path) -> str | None:
    """ADR-0095 requirement 6: a regenerator refuses, fail-closed, to rewrite
    a projection whose declared domain names a source it cannot read.
    Returns the reason, or None when the rewrite may proceed. Runs BEFORE
    the build, in write mode and in --dry-run alike — a gate that cannot
    read a declared source must never report clean."""
    declared = declared_input_domain(summaries)
    if declared is None:
        return None
    if not isinstance(declared, list) or not all(isinstance(m, str) for m in declared):
        return f"_meta.json input_domain is not a list of strings: {declared!r}"
    unknown = sorted(set(declared) - set(INPUT_DOMAIN_KNOWN))
    if unknown:
        return (f"_meta.json input_domain names {unknown}, outside the known set "
                f"{list(INPUT_DOMAIN_KNOWN)} — refusing to rewrite a projection "
                "built from a source this build does not understand")
    if "observations" in declared:
        problem = observations_source_problem(root, manifest)
        if problem is not None:
            return (f"_meta.json declares `observations` in its input_domain but "
                    f"this build cannot read that source: {problem} — refusing to "
                    "rewrite the projection without it")
    if "implementation-migration" in declared:
        import implementation_migration as migration
        try:
            view = migration.authority_view(root)
        except migration.Refused as exc:
            return _DomainRefusal(f"_meta.json declares migration inputs but their proof refuses: {exc}",
                                  getattr(exc, "remedy", None))
        if view["state"] != "published":
            return "_meta.json declares migration inputs but no proved publication is available"
    return None


# ── frontmatter + governs ──────────────────────────────────────────────────

def _frontmatter_block(text: str) -> str | None:
    """Return the raw YAML frontmatter text (between the first --- fences)."""
    m = _FRONTMATTER_RE.match(text)
    return m.group(1) if m else None


def read_frontmatter(text: str) -> dict:
    """Parse the YAML frontmatter of an ADR file's text into a dict."""
    block = _frontmatter_block(text)
    if block is None:
        return {}
    import yaml
    return yaml.safe_load(block) or {}


def adr_num(adr_id: str) -> int:
    return int(str(adr_id).split("-")[-1])


def derived_adr_ids(batch: dict) -> list[str]:
    """The batch's affected ADR ids, adr_num-sorted — the one derivation the
    cross-validation leg, the `backfill` log op, and the journal hook all
    join on. Exported so there is exactly one implementation."""
    return sorted({h.split("/")[0] for h in batch["handles"]}
                  | set(batch["no_rule"]), key=adr_num)


def adr_paths(adrs: Path, *, _source_io=None) -> list[Path]:
    """Sorted active ADR files (top-level only; excludes archive/).

    [SECURITY:S5] The old `p.is_file()` leg FOLLOWED a symlink, so an ADR
    linked in from outside the repository published its `governs` block into
    the resolver, the rule table and the doctrine index — and, through the
    citation linter, SATISFIED a `rule:<slug>` citation that must have
    failed. That is a gate flipping red to green on the strength of a file the
    repository does not contain. `observation_paths` carries the identical leg
    one concern over and already resolves it correctly; this is the same
    helper, the same refusal, the same sentence.

    Containment is a property of the RESOLVED path, never of the spelling or
    of being a link: `resolve_contained` is the one place that decides it, and
    a link landing inside `adrs` stays admissible.

    Raises `ValueError`, naming the file and quoting no byte of it. Every
    driver — `summarize-adrs.py`, `lint-governs-references.py`,
    `check-governs-coverage.py`, `compile-doctrine.py` — already funnels a
    raise into its own controlled non-zero exit with the sentence on stderr,
    so the refusal reaches the operator without a traceback. A silent skip was
    rejected deliberately: `summarize-adrs.py` WRITES, and dropping an ADR
    quietly would let a planted link REMOVE that ADR's rules from the
    committed artifacts, which is the same escape pointed the other way.
    """
    out = []
    for p in sorted(_source_glob(adrs, "ADR-*.md", _source_io)):
        resolved = _source_contained(adrs, p.name, _source_io)
        if resolved is None:
            raise ValueError(
                f"refusing to read {redact(p.name, quoted=False)}: it does "
                f"not resolve to a file inside {redact(adrs, quoted=False)} "
                "— an ADR is read from the adrs directory, never through a "
                "link out of it")
        if not _source_is_file(resolved, _source_io):
            continue        # a directory named `ADR-*.md`, as the old `is_file`
        out.append(p)       # leg skipped, or a dangling link landing inside
    return out


def governs_entries(fm: dict) -> list[dict]:
    """The dict entries of the `governs` list, or [] when absent/malformed.

    Non-dict members are filtered here; validation of a present-but-type-wrong
    entry is `_governs_shape_problems`' job, driven by `raw_governs_entries`.
    """
    return [e for e in raw_governs_entries(fm) if isinstance(e, dict)]


def raw_governs_entries(fm: dict) -> list:
    """The `governs` list of an ADR's frontmatter verbatim (may hold non-dict
    members), or [] when the key is absent or not a list. Type-wrong members
    are preserved so `_governs_shape_problems` can reject them rather than
    silently drop them."""
    g = fm.get("governs")
    return g if isinstance(g, list) else []


# ── input hash (frontmatter direct; NEVER the index) ───────────────────────

def adr_frontmatter_sha256(adrs: Path, *, _source_io=None) -> str:
    """SHA-256 over the frontmatter blocks of the active ADRs, in filename order.

    Reads ADR frontmatter DIRECTLY. Deliberately never reads adrs/index.md, so
    the summaries hash cannot drift behind that gated downstream index.
    """
    h = hashlib.sha256()
    for path in adr_paths(adrs, _source_io=_source_io):
        block = _frontmatter_block(_source_text(path, _source_io))
        if block is None:
            continue
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(block.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


# ── space-fold canonicalization (ADR-0088 clause 1) ─────────────────────────

def space_fold(text: str) -> str:
    """THE one canonicalization shared by anchor containment and the content
    digest: collapse each run of whitespace (the Python `str.isspace()` set) to
    a single space and strip the ends. `str.split()` with no separator splits
    on exactly that set, so the two halves of the contract can never drift
    apart."""
    return " ".join(str(text).split())


def _adr_body(text: str) -> str:
    """The ADR's body text — everything after the closing frontmatter fence."""
    m = _FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


# ── governs entry validation (ADR-0085 Decision 3 + the globally-unique
#    handle assertion) ───────────────────────────────────────────────────────

# The invariants concern's provenance enum (docs/AGENTS.md §15.2), reused
# unchanged for `governs` records per ADR-0085 Decision 3 — one vocabulary,
# not a second one minted here.
PROVENANCE_ENUM = ("authored", "recovered", "reconstructed")

# The recommended maximum length of one `governs` rule, in Unicode CODE POINTS of the parsed
# YAML string after trimming. A REVIEW THRESHOLD, NOT A TARGET and not a limit: exceeding it
# warns and nothing else. Nothing in this module or its callers may turn it into a failure,
# and no rule is ever truncated or rewritten.
#
# WHY CODE POINTS. `len()` on the parsed string, with no normalisation. Bytes would punish a rule
# for containing an em dash, and normalising would silently change the count of a composed
# character. Measured on this corpus when the threshold was set: 219 rules, median 207, and 34
# already above 768 — which is why the baseline below is load-bearing rather than cosmetic.
RULE_LENGTH_RECOMMENDED_MAX = 768

# The manifest key naming the ADR identities that predate this advisory. See
# `rule_length_baseline` for why it is an enumerated set rather than a number.
RULE_LENGTH_BASELINE_KEY = "governs_rule_baseline"

# The CLOSED set of `governs` entry sub-fields (docs/AGENTS.md §11.A). An entry
# carrying anything else is a validation error, and that refusal is the
# MISTYPED-KEY half of ADR-0097 part 6's typo rule: before it, an unread key
# was dropped in silence, so a `retire:` where `retires:` was meant left the
# rule it was written to displace publishing behind a green gate. The
# value-side half — a `retires` target that names no handle that has ever been
# live — is `retirement_problems` below. `retires` is last because it is the
# one this list gained; the other six are ADR-0085's and ADR-0088's.
GOVERNS_SUBFIELDS = ("domain", "rule", "scope", "handle", "anchor",
                     "provenance", "retires")

# An OBSERVATION entry draws from the same closed set, minus two sub-fields
# refused in `_observation_shape_problems` for their own stated reasons
# (docs/AGENTS.md §17.1): `anchor`, because a record's claim is bound to code
# by `anchor_id` rather than to a body span, and `retires`, because an
# observation describes what the code does and cannot displace a decided rule.
# Those two refusals name the sub-field; the allowlist below stays the SAME
# seven in both lanes, so a mistyped key reads as a typo in either.

# ADR-0085 Decision 1: a governs handle is ADR-anchored — `ADR-NNNN/handle-slug`
# with a letter-led slug — and its four-digit number must equal the source ADR's
# own number. The shape mirrors lint-governs-references.py's HANDLE_RE, anchored
# end-to-end and capturing the number for the cross-anchor check.
_HANDLE_ANCHOR_RE = re.compile(r"^ADR-(\d{4})/[a-z][a-z0-9-]*$")

# §17.1: an observation handle is namespaced by its host record id —
# `OBS-NNNN/rule-slug` — so it cannot collide across records or with an ADR
# handle. Handle validation widens BY HOST KIND, never by union: an ADR entry
# is still checked against `_HANDLE_ANCHOR_RE` and an observation entry
# against this one, each cross-checked against its own host number.
_OBS_HANDLE_ANCHOR_RE = re.compile(r"^OBS-(\d{4})/[a-z][a-z0-9-]*$")

# The bare ADR-id shape, for surfaces that name ADRs rather than handles (batch
# `no_rule` members, the recorded cohort). Validated BEFORE any `adr_num` call
# so an authored document error is a validation finding, never an int() crash.
_ADR_ID_SHAPE_RE = re.compile(r"^ADR-\d{4}$")

# The observation-id shape, the §17.1 twin of the ADR shape above and validated
# for the same reason: `adr_num` is called on a record's `id`, so an authored
# `id: OBS-alpha` raised ValueError and the drivers reported exit 2 — the
# ENVIRONMENT lane, which CHK-DRIFT-1 states is never a document finding. A
# typo in a record must read as a document finding. §14.3 allows an artifact
# prefix on the id; the governs handle stays bare (`_OBS_HANDLE_ANCHOR_RE`).
_OBS_ID_SHAPE_RE = re.compile(r"^(?:[A-Z][A-Z0-9]{1,9}-)?OBS-\d{4}$")


class GovernsValidationError(ValueError):
    """One or more `governs` blocks are structurally invalid: a handle-less
    (or blank-handle) entry, a `provenance` value outside `PROVENANCE_ENUM`
    (missing counts as outside), or a handle reused across more than one
    entry (ADR-0085's globally-unique handle assertion).

    This is a DOCUMENT verdict — the ADR corpus itself is wrong — never the
    environment-crash lane. Callers surface it as a validation-error result
    (a non-clean exit carrying `problems` as JSON findings), the same way
    `check-governs-coverage.py` already surfaces an uncovered-ADR finding,
    NOT as a silently-defaulted record or a bare stack trace.
    """

    def __init__(self, problems: list[dict]):
        self.problems = problems
        detail = "; ".join(f"{p['source_adr']} {p['handle']!r}: {p['problem']}" for p in problems)
        super().__init__(f"invalid governs block(s): {detail}")


def _domain_line_problems(entries_by_host: list[tuple[str, list]]) -> list[dict]:
    """Refuse a `domain` carrying a line break, on either half of the union.

    A domain is the KEY the doctrine layer groups by, and `build_index`
    renders it as a markdown heading that `survey.parse_doctrine_entries`
    reads back — `phase_verify` then derives its descriptive-only
    postcondition from what that parse found. A domain holding newlines
    therefore injects a whole forged `## <domain> — <state> · authority: ...`
    section into the index, swallowing the real entry beneath it and yielding
    `prescriptive_entries: 0, entries_without_evidence: 0, clean: true` on a
    broken tree.

    `build_index` escapes the heading, which stops the RENDERING. This is the
    other half: refuse the value at the record boundary, so the author gets a
    document verdict naming the record rather than a silently mangled domain.
    Checked over the union because the hazard is the doctrine heading, and an
    ADR reaches it exactly as an observation does.
    """
    problems: list[dict] = []
    for src, raw in entries_by_host:
        for e in raw:
            if not isinstance(e, dict):
                continue
            domain = e.get("domain")
            if isinstance(domain, str) and ("\n" in domain or "\r" in domain):
                problems.append(_gate_problem(
                    f"`domain` {domain!r} spans more than one line; a domain is "
                    "a single-line token (it becomes a doctrine heading that is "
                    "parsed back)",
                    source_adr=src, handle=e.get("handle")))
    return problems


def _governs_shape_problems(entries_by_adr: list[tuple[str, list]]) -> list[dict]:
    """Validate entry type, handle presence + ADR-anchoring, and provenance.

    Each entry must be a mapping (a scalar governs member is rejected rather
    than silently dropped), carry a non-empty `handle` that is ADR-anchored to
    its own source ADR (ADR-0085 Decision 1), and carry a `provenance` in
    `PROVENANCE_ENUM`. Does NOT check for duplicate handles — see
    `_governs_duplicate_problems`. Returns a sorted list of
    `{source_adr, handle, problem}` dicts (empty when every entry is
    well-shaped).
    """
    problems: list[dict] = []
    for source_adr, entries in entries_by_adr:
        for e in entries:
            if not isinstance(e, dict):
                problems.append({
                    "source_adr": source_adr,
                    "handle": None,
                    "problem": f"governs entry is not a mapping: {redact(e)}",
                })
                continue
            handle = e.get("handle")
            handle_ok = isinstance(handle, str) and handle.strip() != ""
            if not handle_ok:
                problems.append({
                    "source_adr": source_adr,
                    "handle": handle if isinstance(handle, str) else None,
                    "problem": "governs entry is missing a non-empty `handle`",
                })
            else:
                m = _HANDLE_ANCHOR_RE.match(handle)
                if m is None:
                    problems.append({
                        "source_adr": source_adr,
                        "handle": handle,
                        "problem": f"handle {redact(handle)} is not ADR-anchored "
                        "(must match ^ADR-NNNN/[a-z][a-z0-9-]*$)",
                    })
                elif int(m.group(1)) != adr_num(source_adr):
                    problems.append({
                        "source_adr": source_adr,
                        "handle": handle,
                        "problem": f"handle {redact(handle)} anchors ADR-{redact(m.group(1), quoted=False)} "
                        f"but is authored by {redact(source_adr, quoted=False)}",
                    })
            provenance = e.get("provenance")
            if provenance not in PROVENANCE_ENUM:
                problems.append({
                    "source_adr": source_adr,
                    "handle": handle if handle_ok else None,
                    "problem": f"provenance {redact(provenance)} is outside {list(PROVENANCE_ENUM)}",
                })
            problems.extend(_subfield_problems(
                e, source_adr, handle if handle_ok else None, GOVERNS_SUBFIELDS))
    problems.sort(key=lambda p: (p["source_adr"], str(p["handle"]), p["problem"]))
    return problems


def _subfield_problems(entry: dict, source: str, handle: str | None,
                       allowed: tuple[str, ...], *,
                       check_retires: bool = True) -> list[dict]:
    """The closed-allowlist check plus `retires`' own shape, shared by the ADR
    and observation shape validators so the two can never admit different
    sub-fields.

    An unknown key is refused rather than dropped (ADR-0097 part 6): a dropped
    key is a silent one, and the whole subject of the retirement machinery is
    that a silent failure lets a false rule keep publishing while the gate
    stays green. `retires`, when present, must be a NON-EMPTY list of
    ADR-anchored handle strings — an empty list is refused in every shape, on
    the same terms `anchor` already is, because an empty retirement declares
    nothing and reads as a half-finished edit.

    `check_retires` is False on the observation lane, whose caller already
    refuses the sub-field BY NAME; re-checking its shape there would report a
    second problem about a sub-field that lane admits under no shape at all.
    """
    problems: list[dict] = []
    for key in sorted(set(entry) - set(allowed)):
        problems.append({
            "source_adr": source, "handle": handle,
            "problem": f"unknown governs sub-field {redact(key)} — the closed set is "
            f"{list(allowed)}; a mistyped sub-field is refused, never dropped"})
    if not check_retires or "retires" not in entry:
        return problems
    retires = entry["retires"]
    if not isinstance(retires, list):
        problems.append({
            "source_adr": source, "handle": handle,
            "problem": f"`retires` must be a list of ADR-anchored handles, got "
            f"{retires!r}"})
        return problems
    if not retires:
        problems.append({
            "source_adr": source, "handle": handle,
            "problem": "`retires` must not be empty — an empty retirement "
            "declares nothing; omit the sub-field instead"})
        return problems
    for member in retires:
        if not (isinstance(member, str) and _HANDLE_ANCHOR_RE.match(member)):
            problems.append({
                "source_adr": source, "handle": handle,
                "problem": f"`retires` member {member!r} is not an ADR-anchored "
                "handle (must match ^ADR-NNNN/[a-z][a-z0-9-]*$)"})
    return problems


def _observation_shape_problems(entries_by_obs: list[tuple[str, str, list]]) -> list[dict]:
    """Validate an observation record's governs entries under the §17.1-only
    constraints, the observation counterpart of `_governs_shape_problems`.

    `entries_by_obs` carries (record id, record provenance, raw governs list).
    Each entry must be a mapping; carry a non-empty `handle` matching
    `_OBS_HANDLE_ANCHOR_RE` whose number equals the host record's; carry a
    `provenance` in `OBSERVATION_PROVENANCE_ENUM` (so `authored` is refused)
    that equals the record's own `provenance`; and carry NO `anchor` — a
    record's claim is bound to code by `anchor_id`, never to a body span. The
    record-level `provenance` is checked once per record on the same terms.
    Returns a sorted list of `{source_adr, handle, problem}` dicts.
    """
    problems: list[dict] = []
    for oid, record_prov, entries in entries_by_obs:
        if record_prov not in OBSERVATION_PROVENANCE_ENUM:
            problems.append({
                "source_adr": oid,
                "handle": None,
                "problem": f"observation provenance {redact(record_prov)} is outside "
                f"{list(OBSERVATION_PROVENANCE_ENUM)} — an authored rule is an ADR",
            })
        for e in entries:
            if not isinstance(e, dict):
                problems.append({
                    "source_adr": oid,
                    "handle": None,
                    "problem": f"governs entry is not a mapping: {redact(e)}",
                })
                continue
            handle = e.get("handle")
            handle_ok = isinstance(handle, str) and handle.strip() != ""
            handle_repr = handle if handle_ok else None
            if not handle_ok:
                problems.append({
                    "source_adr": oid,
                    "handle": handle if isinstance(handle, str) else None,
                    "problem": "governs entry is missing a non-empty `handle`",
                })
            else:
                m = _OBS_HANDLE_ANCHOR_RE.match(handle)
                if m is None:
                    problems.append({
                        "source_adr": oid,
                        "handle": handle,
                        "problem": f"handle {redact(handle)} is not observation-anchored "
                        "(must match ^OBS-NNNN/[a-z][a-z0-9-]*$)",
                    })
                elif int(m.group(1)) != adr_num(oid):
                    problems.append({
                        "source_adr": oid,
                        "handle": handle,
                        "problem": f"handle {redact(handle)} anchors OBS-{redact(m.group(1), quoted=False)} "
                        f"but is hosted by {redact(oid, quoted=False)}",
                    })
            provenance = e.get("provenance")
            if provenance not in OBSERVATION_PROVENANCE_ENUM:
                problems.append({
                    "source_adr": oid,
                    "handle": handle_repr,
                    "problem": f"provenance {redact(provenance)} is outside "
                    f"{list(OBSERVATION_PROVENANCE_ENUM)} — an authored rule is an ADR",
                })
            elif provenance != record_prov:
                problems.append({
                    "source_adr": oid,
                    "handle": handle_repr,
                    "problem": f"entry provenance {redact(provenance)} differs from the "
                    f"record's provenance {redact(record_prov)}",
                })
            if "anchor" in e:
                problems.append({
                    "source_adr": oid,
                    "handle": handle_repr,
                    "problem": "an observation entry carries no `anchor` — its claim "
                    "is bound to code by `anchor_id`, not to a body span",
                })
            if "retires" in e:
                problems.append({
                    "source_adr": oid,
                    "handle": handle_repr,
                    "problem": "an observation entry carries no `retires` — an "
                    "observation describes what the code does and cannot displace "
                    "a decided rule",
                })
            problems.extend(_subfield_problems(
                e, oid, handle_repr, GOVERNS_SUBFIELDS, check_retires=False))
    problems.sort(key=lambda p: (p["source_adr"], str(p["handle"]), p["problem"]))
    return problems


def _governs_duplicate_problems(entries_by_adr: list[tuple[str, list[dict]]]) -> list[dict]:
    """Detect a `handle` reused across more than one governs entry — within
    one ADR or across two — assuming every entry already passed shape
    validation (a malformed entry has no reliable handle to compare)."""
    problems: list[dict] = []
    first_seen: dict[str, str] = {}
    for source_adr, entries in entries_by_adr:
        for e in entries:
            if not isinstance(e, dict):
                continue
            handle = e.get("handle")
            if not (isinstance(handle, str) and handle.strip()):
                continue
            prior = first_seen.get(handle)
            if prior is not None:
                problems.append({
                    "source_adr": source_adr,
                    "handle": handle,
                    "problem": f"handle also used by {redact(prior, quoted=False)} — handles must be globally unique",
                })
            else:
                first_seen[handle] = source_adr
    problems.sort(key=lambda p: (p["source_adr"], str(p["handle"]), p["problem"]))
    return problems


def _governs_anchor_problems(entries_by_adr: list[tuple[str, list, str]],
                             governs_from: int | None) -> list[dict]:
    """Validate the `anchor` sixth sub-field (ADR-0088 clause 1).

    Rules, in order:
      - an anchor PRESENT on any entry must be non-empty in every shape: an
        empty or whitespace-only string, an empty list, or an empty or
        whitespace-only (or non-string) list element are all validation
        errors;
      - a well-formed present anchor must space-fold-contain in the ADR body —
        a list anchor is AND over its elements, and a repeated span satisfies
        containment by membership (occurrence-agnostic);
      - an anchor ABSENT from an entry is valid iff `governs_from` is None or
        the entry's ADR number is >= `governs_from` (the prospective cohort);
        below the boundary the anchor is required.

    `entries_by_adr` carries (source_adr, raw governs list, body text) — the
    body was already read by the caller, so containment never re-reads a file.
    Runs after shape + duplicate validation, so every entry here is a mapping.
    Returns a sorted list of {source_adr, handle, problem} dicts.
    """
    problems: list[dict] = []
    for source_adr, entries, body in entries_by_adr:
        num = adr_num(source_adr)
        folded_body = space_fold(body)
        for e in entries:
            if not isinstance(e, dict):
                continue
            handle = e.get("handle")
            handle_repr = handle if isinstance(handle, str) and handle.strip() else None
            if "anchor" not in e:
                if governs_from is not None and num < governs_from:
                    problems.append({
                        "source_adr": source_adr,
                        "handle": handle_repr,
                        "problem": "anchor is required on an entry whose ADR number "
                        f"is below governs_from ({governs_from})",
                    })
                continue
            anchor = e.get("anchor")
            if isinstance(anchor, str):
                elements = [anchor]
            elif isinstance(anchor, list):
                elements = anchor
            else:
                problems.append({
                    "source_adr": source_adr,
                    "handle": handle_repr,
                    "problem": f"anchor must be a string or a list of strings: {redact(anchor)}",
                })
                continue
            if not elements:
                problems.append({
                    "source_adr": source_adr,
                    "handle": handle_repr,
                    "problem": "anchor is an empty list — an empty anchor is invalid "
                    "in every shape",
                })
                continue
            bad = False
            for el in elements:
                if not isinstance(el, str) or space_fold(el) == "":
                    problems.append({
                        "source_adr": source_adr,
                        "handle": handle_repr,
                        "problem": f"anchor element is empty or not a string: {redact(el)} — "
                        "an empty anchor is invalid in every shape",
                    })
                    bad = True
            if bad:
                continue
            for el in elements:
                if space_fold(el) not in folded_body:
                    problems.append({
                        "source_adr": source_adr,
                        "handle": handle_repr,
                        "problem": f"anchor element {redact(el)} does not space-fold-contain "
                        "in the ADR body",
                    })
    problems.sort(key=lambda p: (p["source_adr"], str(p["handle"]), p["problem"]))
    return problems

# ── records ────────────────────────────────────────────────────────────────

def _record_from_entry(e: dict, src: str, num: int, *, source_kind: str = "adr",
                       evidence=None, anchor_id=None, status=None) -> dict:
    """The one construction site for the record dict feeding `content_digest`.

    `collect_records` is the single record construction; both the
    regenerator lane (summarize-adrs.py) and the gate lane
    (`backfill_problems`, driven by check-governs-coverage.py) read through
    it, so the digest's input shape can never drift between the two lanes.
    `anchor` is carried raw (str | list | None); every other field is coerced
    to str. `source_kind`, `evidence`, and `anchor_id` are the observation
    widening's fields; an ADR record carries "adr", [], and None so the dict
    shape is uniform. `canonical_string` reads none of the three, so
    `content_digest` is untouched by the widening.

    `retires` is carried as a list of handle strings ([] when absent), and
    `canonical_string` reads it no more than it reads the three above — a
    DELIBERATE decision, not an omission (ADR-0097 part 6). A retirement is a
    relation between handles rather than the content of the rule beside it, so
    keeping it out of the content identity leaves every signed backfill receipt
    and every signed reconciliation valid across a retirement. Including it
    would stale the RETIRING entry's own signature for an edit that changed no
    rule text, which is the harm the signature-orphan refusal exists to
    prevent, reached by a second route. `retired_by` is stamped later, by
    `collect_records`, once every entry across the corpus has been read.

    `source_status` carries the host record's raw frontmatter `status` (the
    ADR state-machine value for an ADR record; always `OBSERVATION_PROJECTED_
    STATUS` for an observation record, since only that status ever reaches
    this constructor). It exists so `_stamp_retirements` can require Accepted
    before a retirement takes effect (ADR-0097 part 6) — see that function for
    the rule. It sits OUTSIDE `canonical_string` for the same reason `retires`
    does: it is the record's REVIEW STATE, not its rule content, so an ADR's
    Proposed-to-Accepted transition must not stale a signed backfill receipt
    or reconciliation for a rule whose text never changed.
    """
    retires = e.get("retires")
    return {
        "handle": str(e.get("handle", "")),
        "domain": str(e.get("domain", "")),
        "rule": str(e.get("rule", "")),
        "scope": str(e.get("scope", "")),
        "anchor": e.get("anchor"),
        "provenance": str(e.get("provenance", "")),
        "retires": [str(h) for h in retires] if isinstance(retires, list) else [],
        "retired_by": [],
        "source_adr": src,
        "adr_num": num,
        "source_kind": source_kind,
        "evidence": sorted(str(x) for x in evidence) if isinstance(evidence, list) else [],
        "anchor_id": str(anchor_id) if anchor_id is not None else None,
        "source_status": str(status) if isinstance(status, str) else None,
    }


def record_sort_key(r: dict) -> tuple:
    """THE one ordering for records everywhere they are sorted: ADR records
    first, then observations, each by (host number, handle). With zero
    observations the leading element is constant, so ADR ordering is
    unchanged. Reads `source_kind` tolerantly ("adr" when absent) so a
    hand-built ADR record still orders."""
    return (r.get("source_kind", "adr") != "adr", r["adr_num"], r["handle"])


# ── handle retirement (ADR-0097 part 6) ────────────────────────────────────

def signed_reconciliation_handles(adrs: Path, *, _source_io=None) -> set[str]:
    """The slot-B handles every SIGNED record in `adrs/doctrine/
    reconciliations.yml` names — the input to the signature-orphan refusal.

    This is a deliberately MINIMAL second reader of a file
    `doctrine_projection.read_reconciliations` also reads, and the direction of
    the dependency is why: `doctrine_projection` imports this module, so this
    module cannot import it back. The duplication is bounded to "which handles
    carry a signature", and `test_governs_retirement.py` asserts the two
    readers return the same set on the live tree, so the smaller one cannot
    drift behind the larger.

    Fail-closed: an absent ledger is an empty set (nothing is signed), but a
    ledger this reader cannot interpret raises `GovernsValidationError` rather
    than returning a short set. A short set would silently permit a retirement
    that orphans a human signature, which is exactly the refusal's subject.
    Only a record carrying a non-null `signed` is read; an unsigned record is
    the valid mid-review state and binds nothing.

    A DUPLICATED mapping key (a repeated `handle:` on one record, in
    particular) is the sharpest instance of "cannot interpret": `yaml.
    safe_load` silently collapses it to its last value rather than raising,
    which would let a retirement of the FIRST (shadowed) handle through
    unchecked — the exact harm this refusal exists to prevent. This reader
    runs the SAME composer-node duplicate-key refusal `doctrine_projection.
    read_reconciliations` runs, via the SAME `_yaml_min` helper, so the two
    readers of this file can never disagree about whether it parses.
    """
    path = adrs / "doctrine" / RECONCILIATIONS_FILENAME
    if not _source_is_file(path, _source_io):
        return set()
    import yaml
    text = _source_text(path, _source_io)
    from _yaml_min import CatalogYamlError, _refuse_duplicate_keys_bounded as _refuse_catalog_duplicate_keys
    try:
        _refuse_catalog_duplicate_keys(text, yaml)
    except CatalogYamlError as exc:
        raise GovernsValidationError([{
            "source_adr": f"adrs/doctrine/{RECONCILIATIONS_FILENAME}",
            "handle": None,
            "problem": f"the reconciliation ledger does not parse, so a signed "
            f"reconciliation record cannot be ruled out: {exc}"}]) from exc
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GovernsValidationError([{
            "source_adr": f"adrs/doctrine/{RECONCILIATIONS_FILENAME}",
            "handle": None,
            "problem": f"the reconciliation ledger does not parse, so a signed "
            f"reconciliation record cannot be ruled out: {exc}"}]) from exc
    if doc is None:
        return set()
    if not isinstance(doc, dict):
        raise GovernsValidationError([{
            "source_adr": f"adrs/doctrine/{RECONCILIATIONS_FILENAME}",
            "handle": None,
            "problem": "the reconciliation ledger is not a mapping, so a signed "
            "reconciliation record cannot be ruled out"}])
    out: set[str] = set()
    problems: list[dict] = []
    for r in doc.get("reconciliations") or []:
        if not isinstance(r, dict) or r.get("signed") is None:
            continue
        handle = r.get("handle")
        if isinstance(handle, str) and _HANDLE_ANCHOR_RE.match(handle):
            out.add(handle)
        else:
            problems.append({
                "source_adr": f"adrs/doctrine/{RECONCILIATIONS_FILENAME}",
                "handle": None,
                "problem": f"a signed reconciliation record names {handle!r}, "
                "which is not an ADR-anchored handle — a retirement cannot be "
                "checked against a record this reader cannot interpret"})
    if problems:
        raise GovernsValidationError(problems)
    return out


def archived_handles(adrs: Path, *, _source_io=None) -> set[str]:
    """Every governs handle declared by an ARCHIVED ADR (`adrs/archive/`).

    The fourth leg of the retirement handle history, and it is load-bearing
    rather than defensive. The retired roster is derived from the LIVE records,
    which `adr_paths` reads from the top-level directory only — so the moment a
    retired handle's ADR moves to the cold tier (ADR-0063), that handle leaves
    the roster and, without this set, the retirement that displaced it would
    start reading as a typo and hard-fail the whole projection. ADR-0097 part 6
    names this case explicitly ("because its ADR was archived ... and is
    idempotent"), and archiving a superseded ADR is routine.

    Read for the HISTORY check only. An archived ADR contributes no record, no
    rule row and no resolver row, exactly as before. This mirrors
    `_adr_ids_including_archive`, which resolves the frozen backfill cohort
    against both tiers for the same reason.
    """
    out: set[str] = set()
    archive = adrs / "archive"
    if not _source_is_dir(archive, _source_io):
        return out
    for path in sorted(_source_glob(archive, "ADR-*.md", _source_io)):
        resolved = _source_contained(archive, path.name, _source_io)
        if resolved is None:
            raise ValueError(
                f"refusing to read {redact(path.name, quoted=False)}: it does "
                f"not resolve to a file inside {redact(archive, quoted=False)} "
                "— an archived ADR is read from the archive directory, never "
                "through a link out of it")
        if not _source_is_file(resolved, _source_io):
            continue
        fm = read_frontmatter(_source_text(resolved, _source_io))
        for e in governs_entries(fm):
            handle = e.get("handle")
            if isinstance(handle, str) and handle.strip():
                out.add(handle)
    return out


def retirement_problems(records: list[dict], removed: dict[str, dict],
                        signed_handles: set[str],
                        archived: set[str] | None = None) -> list[dict]:
    """ADR-0097 part 6's four fail-closed validity rules, as ONE pure function
    over the collected records, ADR-0088's removed roster, and the signed
    reconciliation handles — so every lane drives one implementation and no
    gate can report clean where the regenerator fails. (A fifth fail-closed
    refusal — an unknown/mistyped governs sub-field, which is how a typoed
    `retire:` is caught — lives in `_subfield_problems`, called from
    `_governs_shape_problems`/`_observation_shape_problems`, not here; this
    function's four are the ones that fire specifically on a well-formed
    `retires` target.)

    The rules, each stated here because a silent one would make the retirement
    itself unauditable:

      1. OWN BLOCK — an entry may not retire a handle authored by its own ADR.
         A decision does not displace itself; that edit is a rewrite of the
         entry, not a retirement.
      2. SIGNED SIGNATURE — an entry may not retire a handle any signed
         reconciliation record names, so a retirement cannot orphan a human
         signature. An UNSIGNED record binds nothing.
      3. NEVER BEEN LIVE — a target found in none of the handle histories
         (the live handles, the retired roster, the ARCHIVED tier, and
         ADR-0088's removed roster) is the typo case and FAILS. Recording a
         mistyped handle as merely absent would let the rule it was meant to
         displace keep publishing while the gate stayed green. The archived
         tier is in the history because a retirement must survive its target's
         ADR being archived — see `archived_handles`.
      4. BOTH LANES — a handle in the removed roster AND named by a retirement
         is a validation error. `removed` is a human withdrawing an admission
         through the receipts manifest and presupposes the handle has left its
         ADR's frontmatter; `retired` is a later decision displacing a rule
         that stays on the record. One handle cannot be in both.

    IDEMPOTENCE is the absence of a rule, named here so it is not read as an
    omission rather than as a fifth item in the numbered list above: two ADRs
    retiring one handle, and one ADR naming one handle twice, are a STATE
    declared twice, never an error.

    Rule 4 is evaluated BEFORE rule 3, not in numeric order: a removed
    handle's admission is typically gone from `known`/`archived` too (the
    withdrawal already removed it from its ADR's frontmatter), so if rule 3
    ran first it would read a removed-and-retired handle as a typo. Checking
    rule 4 first lets that handle report the lane collision it actually is.
    """
    # `known` is the handle history rule 3 resolves against: every handle a
    # collected record claims. A RETIRED record stays in `records` (retirement
    # never mutates frontmatter), so this set is the live handles UNION the
    # retired roster; `removed` supplies the third history.
    known = {r["handle"]: r["source_adr"] for r in records}
    history = set(known) | set(archived or ())
    problems: list[dict] = []
    for r in sorted(records, key=record_sort_key):
        source = r["source_adr"]
        for target in r.get("retires") or []:
            if known.get(target) == source:
                problems.append({
                    "source_adr": source, "handle": r["handle"],
                    "problem": f"{source} retires a handle of its own governs "
                    f"block ({target}) — a decision does not displace itself"})
                continue
            if target in removed:
                problems.append({
                    "source_adr": source, "handle": r["handle"],
                    "problem": f"handle {target} appears in both the removed and "
                    "the retired lane — a withdrawn admission and a displaced "
                    "rule are different facts and cannot both hold"})
                continue
            if target in signed_handles:
                problems.append({
                    "source_adr": source, "handle": r["handle"],
                    "problem": f"a signed reconciliation record names {target}, "
                    "so retiring it would orphan a human signature"})
                continue
            if target not in history:
                problems.append({
                    "source_adr": source, "handle": r["handle"],
                    "problem": f"`retires` target {target} has never been a live "
                    "handle — it is in none of the live handles, the retired "
                    "roster, the archived ADR tier, or the removed roster, so it "
                    "reads as a typo"})
    problems.sort(key=lambda p: (str(p["source_adr"]), str(p["handle"]), p["problem"]))
    return problems


def _stamp_retirements(records: list[dict]) -> None:
    """Stamp each record's `retired_by`: the sorted, deduplicated ids of the
    active source records whose `retires` names its handle ([] when live).
    Retirement is a STATE, so the same declaration made twice contributes one
    id, and two ADRs retiring one handle contribute two.

    ADR-0097 part 6 calls a retirement "a later decision displacing a rule",
    and Accepted is the point in the ADR state machine (§11) where a
    proposal BECOMES a decision — so a `retires` entry takes EFFECT only
    when its own host ADR's `source_status` is `Accepted`. The asymmetry is
    deliberate: publishing a Proposed ADR's own rule is additive and
    reversible (a later Deprecated/rejection just stops citing it), while a
    retirement silently drops another ADR's live row — so a retirement that
    fired on a mere proposal would let an unreviewed ADR displace an accepted
    rule before anyone reviewed it. `propose-adr` regenerates the summaries
    projection at proposal time, so this gate is what stops that regeneration
    from silently dropping a live rule.

    Observation-sourced records need no extra condition here: `collect_records`
    only ever constructs one at `OBSERVATION_PROJECTED_STATUS` ("ratified"),
    and an observation entry can never carry `retires` at all —
    `_observation_shape_problems` refuses it outright — so an observation
    record's `retires` list is always empty and this loop never reaches its
    `source_status` check for one.

    Validation (`retirement_problems`) is UNCHANGED by this gate and still
    runs over every record regardless of status — a typo in a Proposed ADR's
    `retires` list must fail at proposal time, loudly, not lie dormant until
    acceptance.
    """
    retired_by: dict[str, set[str]] = {}
    for r in records:
        if r.get("source_status") != "Accepted":
            continue
        for target in r.get("retires") or []:
            retired_by.setdefault(target, set()).add(r["source_adr"])
    for r in records:
        r["retired_by"] = sorted(retired_by.get(r["handle"], ()), key=adr_num)


def live_records(records: list[dict]) -> list[dict]:
    """`records` minus every retired one — THE one filter, applied by each
    artifact builder at its own entry rather than at collection.

    It is not applied inside `collect_records` because the backfill/coverage
    lane (`backfill_problems`) reads that function as LIVE FRONTMATTER, and
    retirement never mutates frontmatter: a retired handle is still admitted by
    its ADR and still enumerated in `adr.governs_backfilled`. Filtering at
    collection would make that gate read a retired handle as an admission
    withdrawn without a tombstone. Idempotent, so double application is a
    no-op.
    """
    return [r for r in records if not r.get("retired_by") and not r.get("historical")]


def retired_records(records: list[dict]) -> list[dict]:
    """The complement of `live_records`, in `record_sort_key` order — the
    retired roster's rows."""
    return sorted((r for r in records if r.get("retired_by") and not r.get("historical")),
                  key=record_sort_key)


def slug_of(handle: str) -> str:
    """The slug half of a governs handle — everything after the single `/`.

    ONE implementation, so the summaries projection, the doctrine index and
    the survey sign-off cannot spell the split three ways. Every handle that
    reaches here has already passed `_HANDLE_ANCHOR_RE` or
    `_OBS_HANDLE_ANCHOR_RE`, each of which admits exactly one `/`. A string
    carrying no `/` yields itself, which keeps the helper total for a caller
    holding a token it has not yet validated.
    """
    return str(handle).split("/", 1)[-1]


def live_and_retired_slugs(
        records: list[dict]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """`(slugs, retired_slugs)` — the two slug maps the resolver carries
    beside its handle rows.

    `slugs` maps a live slug to the one live handle that owns it.
    `retired_slugs` maps a retired slug to the sorted list of LIVE handles
    that displaced it. An empty list is legal and means every displacer was
    itself later retired.

    The map and the gate are ONE traversal on purpose. A builder that
    silently resolved a collision (first-wins, last-wins) beside a separate
    checker is a map that can disagree with its own gate, so a live-slug
    collision — two live handles sharing a slug, or a live slug equal to a
    retired one — raises `GovernsValidationError` here rather than
    serializing a map nobody can trust. Pure in the sense `build_resolver` is
    pure: no filesystem, no globals, raises on invalid input.

    Three derivation details carry the whole correctness of `retired_slugs`:

    - The displacing handles come from the `retires` lists of LIVE records,
      never from `retired_by`. `retired_by` holds source ADR *ids*, and this
      map must resolve a citation to the rule that displaced it, not to the
      document that carried the retirement.
    - Only `live_records` contribute displacers, so a displacer that was
      itself later retired drops out and the citation stops resolving
      through it.
    - The Accepted-host predicate is `_stamp_retirements`' own. If these two
      disagreed, the resolver's retired map and the rule table's
      `## Retired handles` roster would describe different corpora.

    Retired handles MAY share a slug — a frozen handle is never renamed — so
    a retired slug unions the displacers of every retired handle bearing it.
    An empty corpus yields `({}, {})`; both keys are always emitted, never
    omitted.
    """
    # The displacers of a retired handle: the live, Accepted-hosted rules
    # whose `retires` names it.
    displacers: dict[str, set[str]] = {}
    for r in live_records(records):
        if r.get("source_status") != "Accepted":
            continue
        for target in r.get("retires") or []:
            displacers.setdefault(str(target), set()).add(r["handle"])

    problems: list[dict] = []
    slugs: dict[str, str] = {}
    for r in sorted(live_records(records), key=record_sort_key):
        slug = slug_of(r["handle"])
        if slug in slugs:
            problems.append({
                "source_adr": r["source_adr"], "handle": r["handle"],
                "problem": f"live slug {redact(slug)} is already used by live "
                f"handle {redact(slugs[slug])} — the slug half of every live "
                "handle must be unique across the whole resolver"})
            continue
        slugs[slug] = r["handle"]

    retired: dict[str, set[str]] = {}
    for r in retired_records(records):
        retired.setdefault(slug_of(r["handle"]), set()).update(
            displacers.get(r["handle"], ()))
    for slug in sorted(retired):
        if slug in slugs:
            problems.append({
                "source_adr": None, "handle": slugs[slug],
                "problem": f"live slug {redact(slug)} is also a retired slug — "
                "a retired slug is never reused, because a citation of it must "
                "keep naming the rules that displaced it"})

    if problems:
        problems.sort(key=lambda p: (str(p["source_adr"]), str(p["handle"]),
                                     p["problem"]))
        raise GovernsValidationError(problems)
    return slugs, {slug: sorted(handles) for slug, handles in retired.items()}


def collect_records(adrs: Path, governs_from: int | None = None,
                    observations: Path | None = None, *, _source_io=None) -> list[dict]:
    """One record per governs entry across active ADRs UNION `ratified`
    observations (docs/AGENTS.md §17; ADR-0095 requirement 4).

    Each record: handle, domain, rule, scope, anchor (raw: str | list | None),
    provenance, source_adr (the HOST record id), adr_num, source_kind,
    evidence, anchor_id, source_status (the host record's raw frontmatter
    `status`, consumed by `_stamp_retirements`'s Accepted-only gate). Sorted
    by `record_sort_key`.

    `governs_from` is the backfill discriminator (ADR-0088 clause 1): when it
    is not None, an ADR entry whose number is below it MUST carry a valid
    anchor. None leaves the anchor requirement inert (a present anchor is still
    validated for shape and containment). The anchor contract never reaches an
    observation entry, which may carry no anchor at all.

    `observations` is the concern directory, or None to read the ADR half
    alone (every pre-widening caller). A record whose `status` is not
    `OBSERVATION_PROJECTED_STATUS` projects nothing here — `decided` records
    project a resolver alias through `collect_observation_alias_rows` instead.

    Raises `GovernsValidationError` when any governs entry across the corpus
    is malformed (no handle), carries an out-of-enum/missing `provenance`,
    reuses a handle another entry already claims (ADR-0085 Decision 3 and the
    globally-unique handle assertion — enforced over the UNION), fails the
    anchor contract, or breaks a §17.1-only constraint — never silently
    defaults or admits a duplicate as two resolver rows collapsing to one key.
    A projecting observation whose `evidence` is not a list of strings is
    refused on the same terms, so the evidence half fails closed exactly as
    the `governs` half does instead of coercing to an empty list. It also
    raises on any of `retirement_problems`' four rules, and on a fifth
    fail-closed refusal — a sub-field outside `GOVERNS_SUBFIELDS`, which is
    how a mistyped `retires` is caught (ADR-0097 part 6).

    Every returned record carries `retired_by`. The RETIRED records are
    returned here rather than filtered out, because this function's subject is
    live FRONTMATTER, which retirement never mutates — `backfill_problems`
    reads it on exactly those terms. `live_records` is the filter, and each
    artifact builder applies it at its own entry.
    """
    records: list[dict] = []
    entries_by_adr: list[tuple[str, list]] = []
    anchored_by_adr: list[tuple[str, list, str]] = []
    for path in adr_paths(adrs, _source_io=_source_io):
        text = _source_text(path, _source_io)
        fm = read_frontmatter(text)
        src = str(fm.get("id") or "")
        _projection_require("record_type" not in fm,
                            "summaries typed non-ADR payload cannot supply architectural authority")
        if not src:
            continue
        num = adr_num(src)
        raw = raw_governs_entries(fm)
        entries_by_adr.append((src, raw))
        anchored_by_adr.append((src, raw, _adr_body(text)))
        for e in raw:
            if not isinstance(e, dict):
                continue
            records.append(_record_from_entry(e, src, num, status=fm.get("status")))

    entries_by_obs: list[tuple[str, str, list]] = []
    id_problems: list[dict] = []
    if observations is not None:
        for path in observation_paths(observations, _source_io=_source_io):
            fm = read_frontmatter(_source_text(path, _source_io))
            _projection_require("record_type" not in fm,
                                "summaries implementation payload cannot supply observation authority")
            oid = str(fm.get("id") or "")
            if not oid or fm.get("status") != OBSERVATION_PROJECTED_STATUS:
                continue
            if not _OBS_ID_SHAPE_RE.match(oid):
                id_problems.append({
                    "source_adr": oid, "handle": None,
                    "problem": f"observation id {redact(oid)} is not OBS-NNNN "
                    "(an optional §14.3 artifact prefix is allowed)"})
                continue
            num = adr_num(oid)
            raw = raw_governs_entries(fm)
            record_prov = str(fm.get("provenance") or "")
            # §17.1: `evidence` is a LIST of `path:line-range` strings. Check
            # the type here, on the same fail-closed terms as the `governs`
            # half, because `_record_from_entry` coerces anything else to `[]`
            # — so a record written `evidence: path:1-2` (a plausible
            # hand-edit) used to project a rule row that seeded no pairing and
            # rendered `implemented: no`, with nothing in this lane saying why.
            # The `path:line-range` GRAMMAR stays CHK-OBS-EVIDENCE's, and is
            # deliberately not restated here; this is the type contract only.
            evidence = fm.get("evidence")
            if evidence is not None and not isinstance(evidence, list):
                id_problems.append({
                    "source_adr": oid, "handle": None,
                    "problem": f"`evidence` is {type(evidence).__name__}, not a "
                    "list of `path:line-range` strings"})
            elif isinstance(evidence, list):
                bad = [m for m in evidence if not isinstance(m, str)]
                if bad:
                    id_problems.append({
                        "source_adr": oid, "handle": None,
                        "problem": f"`evidence` holds non-string member(s) {bad!r}; "
                        "every member is a `path:line-range` string"})
            entries_by_obs.append((oid, record_prov, raw))
            for e in raw:
                if not isinstance(e, dict):
                    continue
                records.append(_record_from_entry(
                    e, oid, num, source_kind="observation",
                    evidence=fm.get("evidence"), anchor_id=fm.get("anchor_id"),
                    status=fm.get("status")))

    shape_problems = id_problems + _governs_shape_problems(entries_by_adr) \
        + _observation_shape_problems(entries_by_obs) \
        + _domain_line_problems(
            entries_by_adr + [(oid, raw) for oid, _prov, raw in entries_by_obs])
    if shape_problems:
        raise GovernsValidationError(shape_problems)
    duplicate_problems = _governs_duplicate_problems(
        entries_by_adr + [(oid, raw) for oid, _prov, raw in entries_by_obs])
    if duplicate_problems:
        raise GovernsValidationError(duplicate_problems)
    anchor_problems = _governs_anchor_problems(anchored_by_adr, governs_from)
    if anchor_problems:
        raise GovernsValidationError(anchor_problems)

    records.sort(key=record_sort_key)
    # ADR-0097 part 6. The retirement rules are validated in EVERY lane that
    # collects records — a gate that skipped them would report clean on a tree
    # the regenerator refuses — while the FILTER is applied per artifact by
    # `live_records`. The two inputs the rules need beyond the records are read
    # here rather than threaded by each caller, so no caller can supply a
    # narrower history than the one the refusals are stated over.
    retirement = retirement_problems(
        records, removed_handles(read_reviews(adrs, _source_io=_source_io)),
        signed_reconciliation_handles(adrs, _source_io=_source_io), archived_handles(adrs, _source_io=_source_io))
    if retirement:
        raise GovernsValidationError(retirement)
    _stamp_retirements(records)
    return records


def _projection_require(condition: bool, problem: str) -> None:
    if not condition:
        raise GovernsValidationError([{"source_adr": None, "handle": None,
                                       "problem": problem}])


def _summary_authority_records(records: list[dict], facts: dict, removed: dict,
                               aliases: dict) -> dict:
    """Pure overlay of already validated migration facts, never an approval API.

    Raw admission records remain available to receipt readers. Original retirements
    precede selected historical edges; demotion cannot resurrect a predecessor.
    """
    import copy
    rows = copy.deepcopy(records)
    _projection_require(len({r["handle"] for r in rows}) == len(rows),
                        "summaries duplicate source handle")
    _stamp_retirements(rows)
    original_retired = {r["handle"] for r in retired_records(rows)}
    historical = copy.deepcopy(facts["historical_handles"])
    retired = set(facts["retired_handles"]) | original_retired
    _projection_require(not set(historical).intersection(retired | set(removed)),
                        "summaries historical handle conflicts with retirement or removal")
    _projection_require(not retired.intersection(removed), "summaries retirement conflicts with removal")
    for row in rows:
        _projection_require(not set(row.get("retires") or []).intersection(historical),
                            "summaries historical handle cannot be retired again")
    edges = {}
    for fact in facts["historical_displacements"]:
        _projection_require(set(fact) in ({"target_handle", "source_displacer", "source_identity", "source_ref"},
            {"target_handle", "source_displacer", "source_identity", "source_ref", "publication_ref"}),
            "summaries displacement shape invalid")
        target, displacer = fact["target_handle"], fact["source_displacer"]
        _projection_require(isinstance(target, str) and isinstance(displacer, str) and
            _HANDLE_ANCHOR_RE.fullmatch(target) is not None and
            _HANDLE_ANCHOR_RE.fullmatch(displacer) is not None and target in retired and
            displacer not in removed and target not in historical and
            isinstance(fact["source_identity"], str) and _DIGEST_RE.fullmatch(fact["source_identity"]),
            "summaries displacement identities invalid")
        ref = fact["source_ref"]
        _projection_require(isinstance(ref, dict) and set(ref) == {"path", "source_adr", "clause_sha256"}
            and all(isinstance(v, str) for v in ref.values()) and
            _DIGEST_RE.fullmatch(ref["clause_sha256"]) and
            displacer.split("/")[0] == ref["source_adr"], "summaries displacement source invalid")
        key = (target, displacer)
        _projection_require(key not in edges or edges[key] == fact,
                            "summaries conflicting displacement provenance")
        edges[key] = copy.deepcopy(fact)
    proven_targets = {target for target, _ in edges}
    _projection_require(retired - original_retired <= proven_targets,
                        "summaries retirement lacks historical displacement provenance")
    for row in rows:
        if row["handle"] in retired:
            row["retired_by"] = sorted(set(row.get("retired_by", [])) | {
                fact["source_ref"]["source_adr"] for (target, _), fact in edges.items()
                if target == row["handle"]}, key=adr_num)
        if row["handle"] in historical:
            row["historical"] = True
    slugs, retired_slugs = live_and_retired_slugs(rows)
    live_displacers = {}
    for row in live_records(rows):
        if row.get("source_status") == "Accepted":
            for target in row.get("retires") or []:
                live_displacers.setdefault(slug_of(target), set()).add(row["handle"])
    for target in proven_targets:
        slug = slug_of(target)
        retired_slugs[slug] = sorted(set(retired_slugs.get(slug, [])) |
                                     live_displacers.get(slug, set()))
    historical_slugs = {}
    for handle, fact in sorted(historical.items()):
        _projection_require(isinstance(fact, dict) and set(fact) == {
            "destination", "source_identity", "replacements"} and
            _HANDLE_ANCHOR_RE.fullmatch(handle) is not None and
            isinstance(fact["source_identity"], str) and _DIGEST_RE.fullmatch(fact["source_identity"]),
            "summaries historical identity invalid")
        destination = fact["destination"]
        _projection_require(isinstance(destination, dict) and set(destination) == {"source_adr", "clause_sha256"}
            and destination["source_adr"] == handle.split("/")[0] and
            isinstance(destination["clause_sha256"], str) and _DIGEST_RE.fullmatch(destination["clause_sha256"]),
            "summaries historical destination invalid")
        replacements = fact["replacements"]
        live_handles = {row["handle"] for row in live_records(rows)
                        if row.get("source_status") == "Accepted" and row.get("source_kind", "adr") == "adr"}
        _projection_require(isinstance(replacements, list) and replacements and
            all(isinstance(h, str) and h in live_handles for h in replacements) and
            len(set(replacements)) == len(replacements), "summaries replacement is not live architecture")
        slug = slug_of(handle)
        _projection_require(slug not in historical_slugs and slug not in slugs and
            slug not in retired_slugs and slug not in {slug_of(h) for h in removed},
            "summaries historical slug conflicts with another lane")
        historical_slugs[slug] = dict(source_handle=handle, **fact)
    _projection_require(set(historical_slugs) == set(facts["reserved_slugs"]),
                        "summaries historical reservation mismatch")
    _projection_require(not {r["handle"] for r in live_records(rows)}.intersection(removed) and
        not (set(slugs) | set(retired_slugs)).intersection({slug_of(h) for h in removed}),
        "summaries live or retired lane conflicts with removal")
    _projection_require(not set(slugs).intersection(retired_slugs),
                        "summaries live slug conflicts with retirement")
    for handle, alias in aliases.items():
        _projection_require(handle not in historical and handle not in retired and handle not in removed and
            not set(alias["alias_handles"]).intersection(set(historical) | retired | set(removed)) and
            slug_of(handle) not in historical_slugs,
            "summaries alias conflicts with historical, retired or removed lane")
    by_target = {}
    for (target, _), fact in sorted(edges.items()):
        by_target.setdefault(target, []).append(fact)
    return dict(records=rows, slugs=slugs, retired_slugs=retired_slugs,
                historical_slugs=historical_slugs, historical_displacements=by_target)


class _PublicationCommitSlot:
    """Private ASCII commit substitution; never an authority-bearing value."""
    __slots__ = ()


class _SummaryHashSlot(tuple):
    __slots__ = ()

    def __new__(cls, frame):
        return tuple.__new__(cls, frame)

    @property
    def frame(self): return self


class _SummaryOutputPlan(tuple):
    __slots__ = ()

    def __new__(cls, outputs):
        return tuple.__new__(cls, outputs)

    @property
    def outputs(self): return self


class _CommittedPublicationRef(tuple):
    __slots__ = ()

    def __new__(cls, commit):
        _projection_require(isinstance(commit, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit),
                            "summaries committed publication domain invalid")
        return tuple.__new__(cls, (commit,))

    @property
    def commit(self): return self[0]


def _plain_summary_value(value, ancestors=()) -> None:
    """Validate finite plain models and encoding before any deferred slot exists."""
    _projection_require(type(value) in (dict, list, str, int, bool, type(None)),
                        "summaries model contains a non-plain or deferred input")
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            _projection_require(False, "summaries model is not UTF-8 encodable")
    elif isinstance(value, (dict, list)):
        _projection_require(id(value) not in ancestors, "summaries cyclic model refused")
        if isinstance(value, dict):
            _projection_require(all(type(key) is str for key in value), "summaries model key is not text")
            for key in value: _plain_summary_value(key)
        for item in (value.values() if isinstance(value, dict) else value):
            _plain_summary_value(item, ancestors + (id(value),))


def _summary_relative_path(value) -> bool:
    return (isinstance(value, str) and bool(value) and not value.startswith("/")
            and not any(part in ("", ".", "..") for part in value.split("/"))
            and "\\" not in value and ":" not in value)


def _summary_pending_publication(pending) -> None:
    _plain_summary_value(pending)
    _projection_require(isinstance(pending, dict) and set(pending) == {"path", "sha256", "close_identity"}
        and _summary_relative_path(pending["path"]) and isinstance(pending["sha256"], str)
        and _DIGEST_RE.fullmatch(pending["sha256"]), "summaries pending witness invalid")
    close = pending["close_identity"]
    _projection_require(isinstance(close, dict) and set(close) == {
        "batch", "book_id", "run_id", "book_content_hash", "slot", "gate_prompt"},
        "summaries pending close identity invalid")
    _projection_require(isinstance(close["batch"], dict) and set(close["batch"]) == {"path", "sha256"}
        and _summary_relative_path(close["batch"]["path"]) and
        isinstance(close["batch"]["sha256"], str) and _DIGEST_RE.fullmatch(close["batch"]["sha256"])
        and isinstance(close["book_id"], str) and re.fullmatch(r"(?:[A-Z][A-Z0-9]{1,9}-)?PB-\d{4}", close["book_id"])
        and isinstance(close["run_id"], str) and re.fullmatch(r"RUN-\d{3,}", close["run_id"])
        and isinstance(close["book_content_hash"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", close["book_content_hash"])
        and isinstance(close["slot"], str) and re.fullmatch(r"implementation-\d+", close["slot"])
        and type(close["gate_prompt"]) is int and close["gate_prompt"] > 0,
        "summaries pending close fields invalid")


def _committed_publication_ref(pending: dict, actual: dict) -> _CommittedPublicationRef:
    """Domain check only. External proof boundary establishes this commit's provenance."""
    _summary_pending_publication(pending)
    _plain_summary_value(actual)
    _projection_require(isinstance(actual, dict) and set(actual) == {"path", "sha256", "publication_commit"}
        and actual["path"] == pending["path"] and actual["sha256"] == pending["sha256"] and
        isinstance(actual["publication_commit"], str) and
        re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", actual["publication_commit"]),
        "summaries committed witness substitution invalid")
    return _CommittedPublicationRef(actual["publication_commit"])


def _compile_summary_json(value, *, pretty=True, level=0):
    """Compile validated JSON into literal segments and closed private substitutions."""
    if isinstance(value, (_PublicationCommitSlot, _SummaryHashSlot)):
        return ('"', value, '"')
    if not isinstance(value, (dict, list)):
        return (json.dumps(value, ensure_ascii=True, allow_nan=False),)
    if not value:
        return ("{}" if isinstance(value, dict) else "[]",)
    mapping = isinstance(value, dict)
    parts = ["{" if mapping else "["]
    items = sorted(value.items()) if mapping else [(None, v) for v in value]
    for index, (key, item) in enumerate(items):
        if index: parts.append(",")
        if pretty: parts.append("\n" + "  " * (level + 1))
        if mapping: parts.extend((json.dumps(key), ": " if pretty else ":"))
        parts.extend(_compile_summary_json(item, pretty=pretty, level=level + 1))
    if pretty: parts.append("\n" + "  " * level)
    parts.append("}" if mapping else "]")
    return tuple(parts)


def _render_summary_parts(parts, commit):
    return "".join(commit if isinstance(part, _PublicationCommitSlot) else
        hashlib.sha256(_render_summary_parts(part.frame, commit).encode("utf-8")).hexdigest()
        if isinstance(part, _SummaryHashSlot) else part for part in parts)


def _materialize_summary_outputs(plan: _SummaryOutputPlan,
                                 committed_publication_ref: _CommittedPublicationRef | None) -> dict[Path, bytes]:
    """Only substitutions, canonical hashes and serialization; no content decisions."""
    commit = committed_publication_ref.commit if committed_publication_ref is not None else None
    return {path: _render_summary_parts(parts, commit).encode("utf-8") for path, parts in plan.outputs}


def _plan_summary_outputs(loaded_inputs: dict, migration_facts: dict,
                          pending_publication: dict | None) -> _SummaryOutputPlan:
    """Compile four output contracts before visibility, with provenance-only slots.

    Inputs are private loaded facts, not public approval overrides. U9 constructs a
    candidate only after the migration proof boundary has validated those facts.
    """
    expected = {"tree", "paths", "records", "reviews", "aliases", "governs_from",
        "implementation_map", "metadata", "archived_handles", "signed_handles",
        "historical_clauses", "publication_history"}
    _projection_require(isinstance(loaded_inputs, dict) and set(loaded_inputs) == expected,
                        "summaries loaded input shape invalid")
    data = {k: v for k, v in loaded_inputs.items() if k not in ("tree", "paths")}
    _plain_summary_value(data); _plain_summary_value(migration_facts)
    fact_keys = {"historical_handles", "historical_displacements", "retired_handles", "reserved_slugs",
                 "retained_handles", "source_refs", "dependency_fingerprints"}
    _projection_require(isinstance(migration_facts, dict) and fact_keys <= set(migration_facts)
        and isinstance(migration_facts["historical_handles"], dict) and all(isinstance(migration_facts[k], list)
            for k in fact_keys - {"historical_handles"}), "summaries migration fact shape invalid")
    migration_facts = dict(migration_facts)
    dependencies = {}
    for ref in migration_facts["dependency_fingerprints"]:
        _projection_require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}
            and _summary_relative_path(ref["path"]) and isinstance(ref["sha256"], str)
            and _DIGEST_RE.fullmatch(ref["sha256"]), "summaries dependency reference invalid")
        _projection_require(ref["path"] not in dependencies or dependencies[ref["path"]] == ref["sha256"],
                            "summaries conflicting dependency fingerprints")
        dependencies[ref["path"]] = ref["sha256"]
    migration_facts["dependency_fingerprints"] = [dict(path=p, sha256=s) for p, s in sorted(dependencies.items())]
    selected = {}
    for ref in migration_facts["source_refs"]:
        _projection_require(isinstance(ref, dict) and set(ref) == {
            "path", "source_adr", "tier", "status", "source_identity", "clause_sha256"}
            and _summary_relative_path(ref["path"]) and isinstance(ref["source_adr"], str)
            and _ADR_ID_SHAPE_RE.fullmatch(ref["source_adr"]) and ref["tier"] in ("active", "archive")
            and (ref["status"] is None or isinstance(ref["status"], str)) and
            all(isinstance(ref[k], str) and _DIGEST_RE.fullmatch(ref[k])
                for k in ("source_identity", "clause_sha256")), "summaries selected source reference invalid")
        _projection_require(ref["source_identity"] not in selected, "summaries duplicate selected source")
        selected[ref["source_identity"]] = ref
    tree, paths = loaded_inputs["tree"], loaded_inputs["paths"]
    _plain_summary_value(str(tree))
    names = {"rule-table.md", "resolver.json", "implementation-map.md", "_meta.json"}
    _projection_require(isinstance(tree, Path) and tree.is_absolute() and isinstance(paths, dict)
        and set(paths) == names and all(isinstance(path, Path) and path.is_absolute() and
            path == tree / "adrs/summaries" / name and ".." not in path.parts
            for name, path in paths.items()), "summaries destinations are not contained canonical paths")
    rows, reviews, aliases, gf = (data[k] for k in ("records", "reviews", "aliases", "governs_from"))
    _projection_require(isinstance(rows, list) and isinstance(reviews, dict) and isinstance(aliases, dict)
        and (gf is None or type(gf) is int and gf >= 0) and isinstance(data["implementation_map"], str),
        "summaries corpus fields invalid")
    _projection_require(set(reviews) == {"batches", "receipts", "no_rule"} and
        isinstance(reviews["batches"], dict) and isinstance(reviews["receipts"], list) and
        isinstance(reviews["no_rule"], list), "summaries loaded review model invalid")
    _projection_require(all(isinstance(h, str) and _HANDLE_ANCHOR_RE.fullmatch(h)
        for h in migration_facts["retired_handles"] + migration_facts["retained_handles"]) and
        all(isinstance(slug, str) for slug in migration_facts["reserved_slugs"]),
        "summaries migration handle lanes invalid")
    for row in rows:
        _projection_require(isinstance(row, dict) and all(isinstance(row.get(k), str)
            for k in ("handle", "source_adr", "domain", "rule", "scope", "provenance"))
            and type(row.get("adr_num")) is int and row["adr_num"] == adr_num(row["source_adr"])
            and row["handle"].startswith(row["source_adr"] + "/")
            and "historical" not in row and (row.get("source_status") is None or isinstance(row["source_status"], str))
            and (_HANDLE_ANCHOR_RE.fullmatch(row["handle"]) or _OBS_HANDLE_ANCHOR_RE.fullmatch(row["handle"]))
            and row["provenance"] in PROVENANCE_ENUM and
            row.get("source_kind", "adr") in ("adr", "observation") and
            isinstance(row.get("retires", []), list) and all(isinstance(h, str)
                and _HANDLE_ANCHOR_RE.fullmatch(h) for h in row.get("retires", [])),
            "summaries source record invalid")
    for key in ("archived_handles", "signed_handles"):
        _projection_require(isinstance(data[key], list) and all(isinstance(h, str)
            and _HANDLE_ANCHOR_RE.fullmatch(h) for h in data[key]), "summaries retirement history invalid")
    for handle, alias in aliases.items():
        _projection_require(isinstance(alias, dict) and set(alias) == {"alias_of", "alias_handles"}
            and _OBS_HANDLE_ANCHOR_RE.fullmatch(handle) and isinstance(alias["alias_of"], str)
            and _ADR_ID_SHAPE_RE.fullmatch(alias["alias_of"]) and isinstance(alias["alias_handles"], list)
            and all(isinstance(h, str) and h in {r["handle"] for r in rows}
                and h.startswith(alias["alias_of"] + "/") for h in alias["alias_handles"])
            and len(set(alias["alias_handles"])) == len(alias["alias_handles"]), "summaries alias shape invalid")
    problems = retirement_problems(rows, removed_handles(reviews), set(data["signed_handles"]),
                                   set(data["archived_handles"]))
    if problems: raise GovernsValidationError(problems)
    overlay = _summary_authority_records(rows, migration_facts, removed_handles(reviews), aliases)
    for group in overlay["historical_displacements"].values():
        for edge in group:
            source = selected.get(edge["source_identity"])
            _projection_require(source is not None and edge["source_ref"] == {
                k: source[k] for k in ("path", "source_adr", "clause_sha256")} and
                edge["source_displacer"] in (set(migration_facts["historical_handles"]) |
                                             set(migration_facts["retained_handles"])) and
                edge["target_handle"] not in data["signed_handles"] and
                edge["target_handle"].split("/")[0] != source["source_adr"],
                "summaries historical retirement source or signature invalid")
    for fact in migration_facts["historical_handles"].values():
        source = selected.get(fact["source_identity"])
        _projection_require(source is not None and fact["destination"] == {
            "source_adr": source["source_adr"], "clause_sha256": source["clause_sha256"]},
            "summaries historical handle is not a selected source")
    metadata = dict(data["metadata"])
    _projection_require(set(metadata) == {"adr_frontmatter_sha256", "backfill_reviews_sha256",
        "observations_sha256", "survey_receipts_sha256", "input_domain", "schema", "tool"}
        and metadata["schema"] == "6" and metadata["tool"] == "summarize-adrs.py"
        and isinstance(metadata["input_domain"], list) and all(isinstance(x, str)
            and x in INPUT_DOMAIN_KNOWN for x in metadata["input_domain"])
        and len(set(metadata["input_domain"])) == len(metadata["input_domain"]),
        "summaries metadata contract invalid")
    for key in ("adr_frontmatter_sha256", "backfill_reviews_sha256", "observations_sha256", "survey_receipts_sha256"):
        _projection_require((key != "adr_frontmatter_sha256" and metadata[key] is None) or
            isinstance(metadata[key], str) and _DIGEST_RE.fullmatch(metadata[key]), "summaries input digest invalid")
    clauses = data["historical_clauses"]
    _projection_require(isinstance(clauses, list), "summaries historical clause roster invalid")
    accepted = {r["handle"] for r in live_records(overlay["records"])
                if r.get("source_status") == "Accepted" and r.get("source_kind", "adr") == "adr"}
    for clause in clauses:
        _projection_require(isinstance(clause, dict) and set(clause) == {
            "source_identity", "source_ref", "destination", "replacements"} and
            isinstance(clause["source_identity"], str) and _DIGEST_RE.fullmatch(clause["source_identity"])
            and clause["source_ref"] in [{k: s[k] for k in ("path", "source_adr", "clause_sha256")}
                                        for s in migration_facts["source_refs"]]
            and clause["destination"] == {k: clause["source_ref"][k] for k in ("source_adr", "clause_sha256")}
            and selected.get(clause["source_identity"]) is not None and
            {k: selected[clause["source_identity"]][k] for k in ("path", "source_adr", "clause_sha256")} == clause["source_ref"]
            and isinstance(clause["replacements"], list) and all(isinstance(h, str) and h in accepted
                for h in clause["replacements"]) and len(set(clause["replacements"])) == len(clause["replacements"]),
            "summaries historical clause is not a selected source")
    _projection_require(len({c["source_identity"] for c in clauses}) == len(clauses),
                        "summaries duplicate historical clause")
    _projection_require(isinstance(data["publication_history"], list), "summaries publication history invalid")
    for ref in data["publication_history"]:
        _projection_require(isinstance(ref, dict) and set(ref) == {"path", "sha256", "publication_commit"}
            and _summary_relative_path(ref["path"]) and isinstance(ref["sha256"], str)
            and _DIGEST_RE.fullmatch(ref["sha256"]) and isinstance(ref["publication_commit"], str)
            and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", ref["publication_commit"]),
            "summaries publication history fields invalid")
    rows = overlay["records"]
    table = build_rule_table(rows, reviews, gf)
    resolver = {**build_resolver(rows, reviews, gf, aliases), "slugs": overlay["slugs"],
        "retired_slugs": overlay["retired_slugs"], "historical_slugs": overlay["historical_slugs"],
        "historical_displacements": overlay["historical_displacements"], "historical_clauses": clauses}
    if overlay["historical_slugs"]:
        table += "\n## Historical implementation handles\n\n| handle | source identity | replacements |\n|---|---|---|\n"
        for value in overlay["historical_slugs"].values():
            table += f"| {_cell(value['source_handle'])} | {value['source_identity']} | {_cell(', '.join(value['replacements']))} |\n"
    if clauses:
        table += "\n## Historical implementation clauses\n\n| source | clause digest | source identity |\n|---|---|---|\n"
        for clause in sorted(clauses, key=lambda c: c["source_identity"]):
            table += f"| {_cell(clause['source_ref']['source_adr'])} | {clause['source_ref']['clause_sha256']} | {clause['source_identity']} |\n"
    if pending_publication is None:
        _projection_require(not migration_facts["historical_handles"] and not
            migration_facts["historical_displacements"] and not migration_facts["source_refs"] and
            not migration_facts["dependency_fingerprints"] and not clauses and not data["publication_history"],
            "summaries migration facts lack publication provenance")
    else:
        _summary_pending_publication(pending_publication)
        _projection_require(dependencies.get(pending_publication["close_identity"]["batch"]["path"]) ==
            pending_publication["close_identity"]["batch"]["sha256"], "summaries pending batch absent from input snapshot")
        publication = dict(path=pending_publication["path"], sha256=pending_publication["sha256"],
                           publication_commit=_PublicationCommitSlot())
        for group in resolver["historical_displacements"].values():
            for edge in group:
                if "publication_ref" in edge:
                    _committed_publication_ref(pending_publication, edge["publication_ref"])
                edge["publication_ref"] = publication
        frame = dict(publication=publication, close_identity=pending_publication["close_identity"],
            publication_history=data["publication_history"], source_refs=migration_facts["source_refs"],
            dependency_fingerprints=migration_facts["dependency_fingerprints"],
            historical_displacements=resolver["historical_displacements"],
            historical_slugs=resolver["historical_slugs"], historical_clauses=clauses)
        metadata["migration"] = frame
        metadata["migration_inputs_sha256"] = _SummaryHashSlot(_compile_summary_json(frame, pretty=False))
        metadata["input_domain"] = sorted(set(metadata["input_domain"]) | {"implementation-migration"})
    output_parts = ((paths["rule-table.md"], (table,)),
        (paths["resolver.json"], _compile_summary_json(resolver) + ("\n",)),
        (paths["implementation-map.md"], (data["implementation_map"],)),
        (paths["_meta.json"], _compile_summary_json(metadata) + ("\n",)))
    for _, parts in output_parts:
        for part in parts:
            if isinstance(part, str): part.encode("utf-8")
    return _SummaryOutputPlan(output_parts)


def collect_observation_alias_rows(observations: Path | None,
                                   records: list[dict], *, _source_io=None) -> dict[str, dict]:
    """Resolver ALIAS rows for `decided` observations (ADR-0095 requirement 4).

    A decided record contributes no rule row but keeps each of its handles in
    the resolver as an alias onto the ADR its `decided_by` names, so a
    citation written against the observation still resolves after promotion.
    Each row has exactly two keys: `alias_of` (the decided_by ADR id) and
    `alias_handles` (that ADR's governs handles from `records`, sorted; []
    when the ADR carries no governs block — the alias still resolves the
    citation to the ADR). `records` is the collected union, so the ADR half
    is not re-read here. None or an absent directory yields {}.

    Raises `GovernsValidationError` on a decided record with no `decided_by`
    ADR id, or an entry whose handle is not observation-anchored to its host.
    """
    if observations is None:
        return {}
    rows: dict[str, dict] = {}
    problems: list[dict] = []
    for path in observation_paths(observations, _source_io=_source_io):
        fm = read_frontmatter(_source_text(path, _source_io))
        _projection_require("record_type" not in fm,
                            "summaries implementation payload cannot supply an observation alias")
        oid = str(fm.get("id") or "")
        if not oid or fm.get("status") != OBSERVATION_ALIAS_STATUS:
            continue
        if not _OBS_ID_SHAPE_RE.match(oid):
            problems.append({
                "source_adr": oid, "handle": None,
                "problem": f"observation id {redact(oid)} is not OBS-NNNN "
                "(an optional §14.3 artifact prefix is allowed)"})
            continue
        decided_by = fm.get("decided_by")
        if not (isinstance(decided_by, str) and _ADR_ID_SHAPE_RE.match(decided_by)):
            problems.append({
                "source_adr": oid, "handle": None,
                "problem": f"a decided observation must name its decided_by ADR id, "
                f"got {redact(decided_by)}"})
            continue
        # `live_records` here for the same reason every artifact builder
        # applies it: an alias row is a RESOLVER row, so listing a retired
        # handle in it would republish through the alias exactly the handle the
        # retirement removed from the resolver (ADR-0097 part 6). The alias
        # still resolves the citation to its ADR when every one of that ADR's
        # handles is retired — `alias_handles` is then [], which is already the
        # documented no-governs-block case.
        alias_handles = sorted(r["handle"] for r in live_records(records)
                               if r.get("source_kind", "adr") == "adr"
                               and r["source_adr"] == decided_by)
        for e in governs_entries(fm):
            handle = e.get("handle")
            m = _OBS_HANDLE_ANCHOR_RE.match(handle) if isinstance(handle, str) else None
            if m is None or int(m.group(1)) != adr_num(oid):
                problems.append({
                    "source_adr": oid, "handle": handle if isinstance(handle, str) else None,
                    "problem": f"handle {redact(handle)} is not observation-anchored to {redact(oid, quoted=False)}"})
                continue
            if handle in rows:
                problems.append({
                    "source_adr": oid, "handle": handle,
                    "problem": "handle also used by another decided record — handles "
                    "must be globally unique"})
                continue
            rows[handle] = {"alias_of": decided_by, "alias_handles": alias_handles}
    if problems:
        problems.sort(key=lambda p: (p["source_adr"], str(p["handle"]), p["problem"]))
        raise GovernsValidationError(problems)
    return rows


def collect_adr_ids(adrs: Path, *, _source_io=None) -> list[str]:
    """Sorted active ADR ids (by number)."""
    ids: list[str] = []
    for path in adr_paths(adrs, _source_io=_source_io):
        fm = read_frontmatter(_source_text(path, _source_io))
        aid = fm.get("id")
        if aid:
            ids.append(str(aid))
    ids.sort(key=adr_num)
    return ids


# ── derived disposition / authority ────────────────────────────────────────

def disposition_for(provenance: str) -> str:
    """authored -> decided; recovered|reconstructed -> observed."""
    return "decided" if provenance == "authored" else "observed"


def authority_for(provenance: str) -> str:
    """authored -> prescriptive; recovered|reconstructed -> descriptive."""
    return "prescriptive" if provenance == "authored" else "descriptive"


# ── content digest (ADR-0088 clause 3) ─────────────────────────────────────

def canonical_string(entry: dict) -> str:
    """The canonical form of one governs entry — the content identity under
    the backfill contract: the six content fields (handle, domain, rule,
    scope, anchor, provenance) space-folded per clause 1 and joined by single
    newlines in that fixed order, a list anchor contributing its elements in
    order joined by single newlines, with no trailing newline. An ABSENT
    anchor is omitted entirely (the five-field join) — a pinned decision, so
    a prospective entry's canonical form is unambiguous. Space-folding before
    joining makes the newline separator unambiguous, since no folded field can
    contain one.
    """
    lines = [
        space_fold(entry["handle"]),
        space_fold(entry["domain"]),
        space_fold(entry["rule"]),
        space_fold(entry["scope"]),
    ]
    anchor = entry.get("anchor")
    if anchor is not None:
        elements = anchor if isinstance(anchor, list) else [anchor]
        lines.extend(space_fold(el) for el in elements)
    lines.append(space_fold(entry["provenance"]))
    return "\n".join(lines)


def content_digest(entry: dict) -> str:
    """SHA-256 hex of the entry's canonical string — binds the reviewed
    content, so byte-level whitespace variance is not a content change."""
    return hashlib.sha256(canonical_string(entry).encode("utf-8")).hexdigest()


# ── the backfill reviews manifest (ADR-0088 clauses 3-4) ───────────────────

REVIEWS_FILENAME = "backfill-reviews.yml"

# The reconciliation ledger's filename, defined HERE rather than in
# `doctrine_projection` because the retirement machinery's signature-orphan
# refusal reads that file and the import runs one way only (doctrine imports
# summaries, never the reverse). `doctrine_projection` re-exports this name so
# there is one spelling of the filename in the codebase.
RECONCILIATIONS_FILENAME = "reconciliations.yml"

RECEIPT_VERDICTS = ("pass", "fail", "removed")

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Sentinel for an absent receipt `anchor` key — a real object, never the
# string "ABSENT": a literal `anchor: ABSENT` is anchor DATA (refused on a
# tombstone, honored on a pass/fail receipt), not absence.
_MISSING = object()


def reviews_path(adrs: Path) -> Path:
    return adrs / "summaries" / REVIEWS_FILENAME


def backfill_reviews_sha256(adrs: Path, *, _source_io=None) -> str | None:
    """SHA-256 of the reviews manifest's raw bytes; None when the file is
    absent (the input hash covers it as primary authored data per the ADR-0086
    Decision 3 amendment)."""
    path = reviews_path(adrs)
    if not _source_is_file(path, _source_io):
        return None
    return hashlib.sha256(_source_bytes(path, _source_io)).hexdigest()


def _reviews_problem(handle: str | None, problem: str) -> dict:
    return {"source_adr": "adrs/summaries/backfill-reviews.yml",
            "handle": handle, "problem": problem}


def _normalize_signed(value, batch_id: str, problems: list[dict]) -> str | None:
    """A batch's `signed` field: absent/null -> unsigned (None); a YAML date
    or an ISO date string -> its YYYY-MM-DD form. Anything else is a shape
    error. A timestamp-valued date (PyYAML parses `2026-08-27 09:00:00` as a
    datetime) normalizes to its date part — the log/journal join is by day."""
    import datetime
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        value = value.date()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, str) and _DATE_RE.match(value):
        return value
    problems.append(_reviews_problem(
        None, f"batch {batch_id!r}: signed must be a YYYY-MM-DD date or null, "
        f"got {value!r}"))
    return None


def read_reviews(adrs: Path, *, _source_io=None) -> dict:
    """Read and shape-validate `adrs/summaries/backfill-reviews.yml`.

    Returns a normalized manifest:
        {"batches":  {id: {"signed": str | None, "handles": [...],
                           "no_rule": [...]}},
         "receipts": [...],   # document order, exact duplicates deduped
         "no_rule":  [...]}

    An absent file is an empty manifest. A present-but-malformed file raises
    GovernsValidationError: a duplicate mapping key (safe_load would silently
    collapse it — the refusal runs on the YAML composer node graph before any
    construction), unknown batch id, a verdict outside
    {pass, fail, removed}, a digest that is not 64 lowercase hex, a tombstone
    carrying an anchor, a non-tombstone missing one, a receipt (or no-rule
    entry) its signed batch does not enumerate, a duplicate batch id, a batch
    `handles` member outside the ADR-anchored handle shape, a batch `no_rule`
    member or receipt `handle` outside its ADR shape (validated here so no
    `adr_num` call downstream can crash on an authored document error), a
    timestamp-shaped or otherwise non-date `signed`, or a config_version other
    than "1". An exactly-duplicated receipt is an idempotent no-op (deduped),
    never an error.
    """
    path = reviews_path(adrs)
    if not _source_is_file(path, _source_io):
        return {"batches": {}, "receipts": [], "no_rule": []}
    return parse_reviews(_source_text(path, _source_io))


def parse_reviews(text: str) -> dict:
    """Parse the existing backfill grammar from supplied source text, without authority."""
    import yaml
    # The duplicate-key refusal runs on the composer node graph (no object
    # construction), the same guard the catalog read path carries — the
    # reviews manifest is likewise an authored source of truth. The full
    # strict catalog loader is NOT used: it refuses the manifest's flow
    # sequences, which are legal here.
    from _yaml_min import CatalogYamlError, _refuse_duplicate_keys_bounded as _refuse_catalog_duplicate_keys
    try:
        _refuse_catalog_duplicate_keys(text, yaml)
    except CatalogYamlError as exc:
        raise GovernsValidationError([_reviews_problem(None, str(exc))]) from exc
    doc = yaml.safe_load(text)
    problems: list[dict] = []
    if not isinstance(doc, dict):
        raise GovernsValidationError([_reviews_problem(
            None, "reviews manifest is not a mapping")])
    if str(doc.get("config_version")) != "1":
        problems.append(_reviews_problem(
            None, f"config_version must be \"1\", got {doc.get('config_version')!r}"))

    batches: dict[str, dict] = {}
    for b in doc.get("batches") or []:
        if not isinstance(b, dict) or not isinstance(b.get("id"), str) or not b["id"].strip():
            problems.append(_reviews_problem(None, f"batch is not a mapping with a "
                                             f"non-empty string id: {b!r}"))
            continue
        bid = b["id"]
        if bid in batches:
            problems.append(_reviews_problem(None, f"duplicate batch id {bid!r}"))
            continue
        handles = b.get("handles") or []
        no_rule = b.get("no_rule") or []
        if not (isinstance(handles, list) and all(isinstance(h, str) for h in handles)):
            problems.append(_reviews_problem(None, f"batch {bid!r}: handles must be a "
                                             "list of strings"))
            handles = [h for h in handles if isinstance(h, str)] if isinstance(handles, list) else []
        else:
            bad_handles = [h for h in handles if not _HANDLE_ANCHOR_RE.match(h)]
            if bad_handles:
                problems.append(_reviews_problem(
                    None, f"batch {bid!r}: handles members must be ADR-anchored "
                    "handles matching ^ADR-NNNN/[a-z][a-z0-9-]*$; "
                    f"non-conforming: {bad_handles}"))
        if not (isinstance(no_rule, list) and all(isinstance(a, str) for a in no_rule)):
            problems.append(_reviews_problem(None, f"batch {bid!r}: no_rule must be a "
                                             "list of strings"))
            no_rule = [a for a in no_rule if isinstance(a, str)] if isinstance(no_rule, list) else []
        else:
            bad_no_rule = [a for a in no_rule if not _ADR_ID_SHAPE_RE.match(a)]
            if bad_no_rule:
                problems.append(_reviews_problem(
                    None, f"batch {bid!r}: no_rule members must be ADR ids "
                    f"matching ^ADR-\\d{{4}}$; non-conforming: {bad_no_rule}"))
        batches[bid] = {"signed": _normalize_signed(b.get("signed"), bid, problems),
                        "handles": list(handles), "no_rule": list(no_rule)}

    receipts: list[dict] = []
    seen_receipts: set[tuple] = set()
    for r in doc.get("receipts") or []:
        if not isinstance(r, dict):
            problems.append(_reviews_problem(None, f"receipt is not a mapping: {r!r}"))
            continue
        handle = r.get("handle")
        if not isinstance(handle, str) or not handle.strip():
            problems.append(_reviews_problem(None, f"receipt is missing a non-empty "
                                             f"handle: {r!r}"))
            continue
        if not _HANDLE_ANCHOR_RE.match(handle):
            problems.append(_reviews_problem(
                handle, f"receipt handle {handle!r} is not ADR-anchored "
                "(must match ^ADR-NNNN/[a-z][a-z0-9-]*$)"))
            continue
        digest = r.get("digest")
        if not (isinstance(digest, str) and _DIGEST_RE.match(digest)):
            problems.append(_reviews_problem(
                handle, f"digest must be 64 lowercase hex, got {digest!r}"))
        verdict = r.get("verdict")
        if verdict not in RECEIPT_VERDICTS:
            problems.append(_reviews_problem(
                handle, f"verdict must be one of {list(RECEIPT_VERDICTS)}, "
                f"got {verdict!r}"))
        batch = r.get("batch")
        if batch not in batches:
            problems.append(_reviews_problem(
                handle, f"receipt names an unknown batch id: {batch!r}"))
        anchor = r.get("anchor", _MISSING)
        if verdict == "removed":
            if anchor is not _MISSING:
                problems.append(_reviews_problem(
                    handle, "a tombstone receipt (verdict: removed) must NOT "
                    "carry an anchor"))
        else:
            if anchor is _MISSING:
                problems.append(_reviews_problem(
                    handle, "a non-tombstone receipt is missing its anchor"))
            elif isinstance(anchor, str):
                pass
            elif isinstance(anchor, list) and all(isinstance(el, str) for el in anchor):
                pass
            else:
                problems.append(_reviews_problem(
                    handle, f"receipt anchor must be a string or a list of strings, "
                    f"got {anchor!r}"))
        # A receipt is signed only through its batch's owner sign-off, which
        # approves ENUMERATED spans: a signed batch that does not enumerate
        # this receipt's handle is a shape error (an unsigned batch is the
        # valid mid-batch state and demands nothing).
        if batch in batches and batches[batch]["signed"] is not None \
                and handle not in batches[batch]["handles"]:
            problems.append(_reviews_problem(
                handle, f"signed batch {batch!r} does not enumerate this "
                "receipt's handle"))
        key = (handle, digest, verdict,
               anchor if isinstance(anchor, str) else tuple(anchor) if isinstance(anchor, list) else None,
               batch)
        if key in seen_receipts:
            continue  # a replayed receipt identical to an existing one: idempotent no-op
        seen_receipts.add(key)
        receipts.append({"handle": handle, "digest": digest, "verdict": verdict,
                         "anchor": None if anchor is _MISSING else anchor,
                         "batch": batch})

    no_rule: list[dict] = []
    for n in doc.get("no_rule") or []:
        if not isinstance(n, dict) or not isinstance(n.get("adr"), str):
            problems.append(_reviews_problem(None, f"no_rule entry is not a mapping "
                                             f"with an adr id: {n!r}"))
            continue
        adr = n["adr"]
        if n.get("verdict") != "confirmed":
            problems.append(_reviews_problem(
                None, f"no_rule {adr}: verdict must be \"confirmed\", got "
                f"{n.get('verdict')!r}"))
        if not isinstance(n.get("reason"), str) or not n["reason"].strip():
            problems.append(_reviews_problem(
                None, f"no_rule {adr}: reason must be a non-empty string"))
        batch = n.get("batch")
        if batch not in batches:
            problems.append(_reviews_problem(
                None, f"no_rule {adr} names an unknown batch id: {batch!r}"))
        elif batches[batch]["signed"] is not None and adr not in batches[batch]["no_rule"]:
            problems.append(_reviews_problem(
                None, f"signed batch {batch!r} does not enumerate no_rule "
                f"ADR {adr}"))
        no_rule.append({"adr": adr, "reason": n.get("reason"), "batch": batch})

    if problems:
        problems.sort(key=lambda p: (str(p["handle"]), p["problem"]))
        raise GovernsValidationError(problems)
    return {"batches": batches, "receipts": receipts, "no_rule": no_rule}


# ── receipt-chain derivations (ADR-0088 clauses 3, 6, 8) ───────────────────

def receipt_signed(receipt: dict, batches: dict) -> bool:
    """A receipt is SIGNED iff its batch exists, carries a signed date, and
    enumerates the receipt's handle."""
    b = batches.get(receipt.get("batch"))
    return b is not None and b["signed"] is not None and receipt.get("handle") in b["handles"]


def current_receipt(handle: str, reviews: dict) -> dict | None:
    """The latest SIGNED receipt in the handle's append-only chain (document
    order), or None when the chain holds no signed receipt."""
    cur = None
    for r in reviews["receipts"]:
        if r["handle"] == handle and receipt_signed(r, reviews["batches"]):
            cur = r
    return cur


def resolve_entry(entry: dict, reviews: dict) -> bool:
    """Clause 3's current-receipt rule: an entry resolves iff its current
    receipt (the latest SIGNED one in its append-only chain) is a PASS whose
    digest equals the live digest recomputed from the frontmatter. Anything
    else derives `unreviewed` and soft-excludes until re-reviewed. The
    prospective carve-out (`accepted-with-adr`) lives in `review_state`, which
    short-circuits before this rule — a prospective entry is never
    receipt-checked."""
    cur = current_receipt(entry["handle"], reviews)
    return cur is not None and cur["verdict"] == "pass" \
        and cur["digest"] == content_digest(entry)


def review_state(entry: dict, reviews: dict, governs_from: int | None) -> str:
    """The resolver-row facet, derived from the receipt chain at projection
    time (never stored or mutated), in the fixed clause-6 order: prospective
    entries carry `accepted-with-adr`; else `unreviewed` when the entry does
    not resolve under clause 3's current-receipt rule; else `corrected` when
    the chain holds more than one signed receipt; else `anchored-reviewed`.
    The fifth value, `removed`, is carried by a removed handle — which drops
    out of resolver.json entirely, so it never appears on a row here.

    The prospective short-circuit PRECEDES clause 6's literal derivation order
    deliberately: a prospective entry has no receipts and would otherwise
    derive `unreviewed`, soft-excluding ADR-0087's conformance exemplar. Do
    not "fix" the order back.
    """
    if governs_from is None or entry["adr_num"] >= governs_from:
        return "accepted-with-adr"
    cur = current_receipt(entry["handle"], reviews)
    if not (cur is not None and cur["verdict"] == "pass"
            and cur["digest"] == content_digest(entry)):
        return "unreviewed"
    chain = [r for r in reviews["receipts"]
             if r["handle"] == entry["handle"] and receipt_signed(r, reviews["batches"])]
    if len(chain) > 1:
        return "corrected"
    return "anchored-reviewed"


def removed_handles(reviews: dict) -> dict[str, dict]:
    """Handles whose CURRENT signed receipt is a tombstone (verdict removed),
    mapped to that tombstone receipt — the terminal record, carrying the last
    live digest and the batch ref, validated from the receipt and never
    recomputed from live frontmatter."""
    out: dict[str, dict] = {}
    order: list[str] = []
    for r in reviews["receipts"]:
        if r["handle"] not in order:
            order.append(r["handle"])
    for handle in order:
        cur = current_receipt(handle, reviews)
        if cur is not None and cur["verdict"] == "removed":
            out[handle] = cur
    return out


def no_rule_signed(adr: str, reviews: dict) -> bool:
    """An ADR holds a signed no-rule receipt iff a no_rule entry names it AND
    its batch carries a signed date AND the batch enumerates the ADR id."""
    for n in reviews["no_rule"]:
        if n["adr"] != adr:
            continue
        b = reviews["batches"].get(n["batch"])
        if b is not None and b["signed"] is not None and adr in b["no_rule"]:
            return True
    return False


# ── the backfill gate (ADR-0088 clauses 2, 4, 7, 9) ────────────────────────

def backfill_cohort_live(adrs: Path, governs_from: int) -> list[str]:
    """The clause-9 filter evaluated live: Accepted, non-archived (top-level
    adrs/), numbered below `governs_from`. Sorted by number."""
    ids: list[str] = []
    for path in adr_paths(adrs):
        fm = read_frontmatter(path.read_text(encoding="utf-8"))
        aid = str(fm.get("id") or "")
        if aid and fm.get("status") == "Accepted" and adr_num(aid) < governs_from:
            ids.append(aid)
    ids.sort(key=adr_num)
    return ids


def _adr_ids_including_archive(adrs: Path) -> set[str]:
    """Every ADR id any ADR file supplies — active (top-level adrs/) or
    archived (adrs/archive/). The frozen cohort's existence domain: a recorded
    member resolves against BOTH, since a post-snapshot archival never leaves
    the list (ADR-0088 clause 7)."""
    ids: set[str] = set()
    for d in (adrs, adrs / "archive"):
        for path in sorted(d.glob("ADR-*.md")):
            resolved = _evidence_module().resolve_contained(d, path.name)
            if resolved is None:
                raise ValueError(
                    f"refusing to read {redact(path.name, quoted=False)}: it "
                    f"does not resolve to a file inside {redact(d, quoted=False)} "
                    "— an ADR is read from its tier directory, never through "
                    "a link out of it")
            if not resolved.is_file():
                continue
            fm = read_frontmatter(resolved.read_text(encoding="utf-8"))
            aid = str(fm.get("id") or "")
            if aid:
                ids.add(aid)
    return ids


def _folded_anchor(anchor) -> str:
    """An anchor under the space-fold, for equality comparison: a list
    contributes its elements in order; None folds to empty.

    The space join makes the fold insensitive to element BOUNDARIES:
    ["alpha beta", "gamma"] and ["alpha", "beta gamma"] fold equal, while
    the content digest's newline-joined canonical string keeps them apart.
    That asymmetry is why the digest is the binding comparison and this
    fold is only the weaker rendered-anchor attestation check (clause 5).
    """
    if anchor is None:
        return ""
    elements = anchor if isinstance(anchor, list) else [anchor]
    return space_fold(" ".join(str(el) for el in elements))


_LOG_HEADING_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\S+) \| (.*)$", re.M)
_JOURNAL_HEADING_RE = re.compile(
    r"^## \[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\] (\S+) \| (.*)$", re.M)
_ADRS_LINE_RE = re.compile(r"^ADRs:\s*(.*)$", re.M)


def _log_entries(text: str) -> list[dict]:
    """Parse a log.md-style document into [{date, op, subject, body}]."""
    matches = list(_LOG_HEADING_RE.finditer(text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append({"date": m.group(1), "op": m.group(2),
                    "subject": m.group(3).strip(), "body": text[m.end():end]})
    return out


def _journal_entries(text: str) -> list[dict]:
    """Parse a journal file into [{date, category, subject, block}] where
    `block` is the heading line plus the entry body (the surface a hook names
    its ids on)."""
    matches = list(_JOURNAL_HEADING_RE.finditer(text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append({"date": m.group(1), "category": m.group(2),
                    "subject": m.group(3).strip(), "block": text[m.start():end]})
    return out


def _gate_problem(problem: str, source_adr=None, handle=None) -> dict:
    return {"source_adr": source_adr, "handle": handle, "problem": problem}


def backfill_problems(root: Path, adrs: Path, manifest: dict) -> tuple[list[dict], dict | None]:
    """The backfill contract, run by check-governs-coverage.py.

    Active only when `adr.governs_from` is set AND `adr.governs_backfill_cohort`
    is present — a tree that adopted the governs block but never the backfill
    machinery stays inert. Returns (problems, info); info is None when inert,
    else the informational per-ADR-disposition counts
    {"backfilled": n, "no_rule": n, "pending": n} over the frozen cohort.

    Records come from `collect_records(adrs, governs_from=gf)` — the same
    construction the regenerator lane uses — so this gate enforces the anchor
    contract (clause 1) too, rather than re-deriving a parallel record shape
    that would drift behind it.

    Checks (ADR-0088):
      (i)   at-most-once — every live cohort handle appears in
            `adr.governs_backfilled[ADR]`; every ledgered handle absent from
            live frontmatter holds a current signed tombstone; a live handle
            whose current receipt is a tombstone fails (re-admission). A
            tombstone must carry the LAST LIVE digest: the digest of the
            signed receipt preceding it in the chain — compared against the
            chain, never recomputed from live frontmatter.
      (i.a) rendered-anchor attestation (clause 5) — a current non-tombstone
            receipt whose digest equals the live digest must record the
            anchor rendered to the owner at sign-off: space-fold-equal (a
            list joined in order) to the entry's live anchor.
      (ii)  frozen cohort — direction A: a member the clause-9 filter yields
            live (Accepted, non-archived, below governs_from) but the
            recorded list lacks is a mis-snapshot. Direction B is
            existence-and-boundary, NEVER a live re-filter: every recorded
            member must resolve to an ADR file under adrs/ OR adrs/archive/
            (a recorded id no ADR file supplies is fabricated) and must
            number below governs_from (at/above the boundary is a
            mis-snapshot). A post-snapshot Superseded/Deprecated/archived
            member is NOT an error — clause 7 closes the list forever with
            each member's recorded disposition, so the denominator survives
            later transitions.
      (iii) the marker — `governs_backfill_complete: true` fails while any
            cohort ADR is pending; Σ backfilled + Σ no-rule = |cohort| over
            distinct ADRs, Σ no-rule counting only ADRs holding a signed
            no-rule receipt; absent marker -> informational only. The
            per-ADR disposition reads clause 7 ADR-literally on removals:
            ANY tombstoned entry among an ADR's admitted handles leaves the
            ADR pending, never backfilled — the surviving entries' resolution
            does not close it. A signed no-rule receipt on an ADR carrying
            live governs entries is a contract failure (clause 8's three
            legible states — exempt, not-yet-backfilled, no-rule — do not
            collapse), and the laundered receipt counts for nothing in the
            disposition.
      (iv)  cross-validation — each signed batch needs its `backfill` log op
            (heading date == signed date, body `ADRs:` set == the batch's
            enumerated ADR ids) and its journal hook (`review` category,
            `backfill sign-off <batch-id>` subject, naming batch id + ADR
            ids); every `backfill` log entry names a recorded batch; and the
            batch's receipt set covers every enumerated entry (clause 2): a
            signed batch enumerating a receipt-less handle is one problem.

    Raises GovernsValidationError when the reviews manifest itself is
    malformed (via `read_reviews`) or any governs entry fails validation
    (via `collect_records`).
    """
    adr_cfg = manifest.get("adr") or {}
    gf = governs_from(manifest)
    cohort_key = adr_cfg.get("governs_backfill_cohort")
    if gf is None or cohort_key is None:
        return [], None

    problems: list[dict] = []
    reviews = read_reviews(adrs)

    # Live frontmatter, read through the SAME record construction the
    # regenerator lane uses (collect_records with the boundary threaded) —
    # so the anchor contract is enforced in this lane too, and the digest's
    # input shape has one construction site. Handles and full records per ADR
    # (below the boundary for the ledger checks; every ADR for the removal
    # checks).
    records = collect_records(adrs, governs_from=gf)
    records_by_adr: dict[str, list[dict]] = {}
    live_handles_below: dict[str, str] = {}   # handle -> source_adr
    all_live_handles: set[str] = set()
    for rec in records:
        records_by_adr.setdefault(rec["source_adr"], []).append(rec)
        all_live_handles.add(rec["handle"])
        if rec["adr_num"] < gf:
            live_handles_below[rec["handle"]] = rec["source_adr"]

    # (ii) frozen cohort: direction A stays a live-filter comparison (a live
    # member MISSING from the recorded list is a mis-snapshot); direction B is
    # existence-and-boundary, never a live re-filter — clause 7 closes the
    # list forever, so a member superseded, deprecated, or archived AFTER the
    # snapshot stays in it with its recorded disposition and is NOT an error.
    live_cohort = backfill_cohort_live(adrs, gf)
    recorded_cohort = [str(x) for x in cohort_key] if isinstance(cohort_key, list) else []
    if not isinstance(cohort_key, list):
        problems.append(_gate_problem(
            "adr.governs_backfill_cohort must be a list of ADR ids"))
    bad_cohort_members: set[str] = set()
    for aid in recorded_cohort:
        if not _ADR_ID_SHAPE_RE.match(aid):
            bad_cohort_members.add(aid)
            problems.append(_gate_problem(
                f"adr.governs_backfill_cohort member {aid!r} is not an "
                "ADR-NNNN id"))
    known_ids = _adr_ids_including_archive(adrs)
    for aid in live_cohort:
        if aid not in recorded_cohort:
            problems.append(_gate_problem(
                f"cohort member {aid} (Accepted, non-archived, below "
                f"governs_from={gf}) is missing from adr.governs_backfill_cohort",
                source_adr=aid))
    for aid in recorded_cohort:
        if aid in bad_cohort_members:
            continue
        if adr_num(aid) >= gf:
            problems.append(_gate_problem(
                f"adr.governs_backfill_cohort records {aid}, numbered "
                f"at/above governs_from ({gf}) — the snapshot filter admits "
                "only numbers below the boundary (mis-snapshot)",
                source_adr=aid))
        elif aid not in known_ids:
            problems.append(_gate_problem(
                f"adr.governs_backfill_cohort records {aid}, which no ADR "
                "file (active or archived) supplies — a fabricated member",
                source_adr=aid))
        # else: the member exists and is below the boundary. A post-snapshot
        # Superseded/Deprecated status or a move to adrs/archive/ is clause
        # 7's recorded disposition, not a mis-snapshot — no error.

    # (i) at-most-once, keyed per entry.
    raw_ledger = adr_cfg.get("governs_backfilled") or {}
    if not isinstance(raw_ledger, dict):
        problems.append(_gate_problem(
            "adr.governs_backfilled must be a mapping of ADR id -> [handles]"))
        raw_ledger = {}
    ledger = {str(k): [str(h) for h in (v or [])] for k, v in raw_ledger.items()
              if isinstance(v, list)}
    removed = removed_handles(reviews)
    for handle, src in sorted(live_handles_below.items()):
        if handle not in ledger.get(src, []):
            problems.append(_gate_problem(
                f"live cohort handle {handle} is absent from the "
                "adr.governs_backfilled ledger — an admission the ledger does "
                "not record fails (at-most-once admission)",
                source_adr=src, handle=handle))
        cur = current_receipt(handle, reviews)
        if cur is not None and cur["verdict"] == "removed":
            problems.append(_gate_problem(
                f"live handle {handle}'s current receipt is a tombstone — "
                "re-admission after a recorded removal routes only through "
                "the correction lane",
                source_adr=src, handle=handle))
    for aid in sorted(ledger):
        for handle in sorted(ledger[aid]):
            if handle not in all_live_handles and handle not in removed:
                problems.append(_gate_problem(
                    f"ledgered handle {handle} is absent from live frontmatter "
                    "and holds no current signed tombstone — an unrecorded "
                    "removal",
                    source_adr=aid, handle=handle))

    # (i, tombstone leg) A tombstone carries the LAST LIVE digest: it must
    # equal the digest of the signed receipt preceding it in the chain —
    # compared against the chain, never recomputed from live frontmatter.
    for handle in sorted(removed):
        tomb = removed[handle]
        chain = [r for r in reviews["receipts"]
                 if r["handle"] == handle and receipt_signed(r, reviews["batches"])]
        prior = chain[-2] if len(chain) >= 2 else None
        if prior is None:
            problems.append(_gate_problem(
                f"tombstone for {handle} is the chain's first signed receipt "
                "— a removal records the last live digest, which only a "
                "prior signed receipt can supply",
                handle=handle))
        elif prior["digest"] != tomb["digest"]:
            problems.append(_gate_problem(
                f"tombstone for {handle} does not carry the last live digest "
                "— it differs from the chain's prior signed receipt",
                handle=handle))

    # (i.a) rendered-anchor attestation (clause 5): a current non-tombstone
    # receipt whose digest equals the live digest must record the anchor that
    # was rendered to the owner at sign-off — space-fold-equal to the live
    # entry's anchor. A matching digest with a different anchor fabricates
    # the attestation (the digest binds content; this binds the rendering).
    for src, recs in records_by_adr.items():
        for rec in recs:
            cur = current_receipt(rec["handle"], reviews)
            if cur is None or cur["verdict"] == "removed":
                continue
            if cur["digest"] != content_digest(rec):
                continue
            if _folded_anchor(cur["anchor"]) != _folded_anchor(rec["anchor"]):
                problems.append(_gate_problem(
                    f"the current receipt for {rec['handle']} records an "
                    "anchor that does not space-fold-equal the live entry's "
                    "anchor — the receipt must record the anchor rendered at "
                    "sign-off",
                    source_adr=src, handle=rec["handle"]))

    # (iii) the completion marker and the distinct-ADR arithmetic.
    dispositions: dict[str, str] = {}
    for aid in recorded_cohort:
        if aid in bad_cohort_members:
            continue  # the malformed member already carries its (ii) problem
        entries = records_by_adr.get(aid, [])
        if no_rule_signed(aid, reviews):
            if entries:
                # Clause 8's three legible states — exempt,
                # not-yet-backfilled, no-rule — do not collapse: a signed
                # no-rule receipt cannot stand on an ADR carrying live
                # governs entries. The laundered receipt counts for nothing;
                # the disposition falls through to the entries.
                problems.append(_gate_problem(
                    f"cohort ADR {aid} holds a signed no-rule receipt AND "
                    "live governs entries — the three legible states do not "
                    "collapse; a no-rule receipt cannot stand on an ADR "
                    "carrying rules",
                    source_adr=aid))
            else:
                dispositions[aid] = "no_rule"
                continue
        ledgered = set(ledger.get(aid, []))
        # Clause 7, read ADR-literally: a removal leaves its ADR pending.
        # ANY tombstoned entry among the ADR's admitted handles defeats
        # `backfilled` — the surviving entries' resolution does not close it.
        any_tombstone = any(h in removed for h in ledgered)
        if not any_tombstone and entries \
                and all(e["handle"] in ledgered for e in entries) \
                and all(resolve_entry(e, reviews) for e in entries):
            dispositions[aid] = "backfilled"
        else:
            dispositions[aid] = "pending"
    info = {
        "backfilled": sum(1 for d in dispositions.values() if d == "backfilled"),
        "no_rule": sum(1 for d in dispositions.values() if d == "no_rule"),
        "pending": sum(1 for d in dispositions.values() if d == "pending"),
    }
    if adr_cfg.get("governs_backfill_complete") is True:
        pending = sorted((a for a, d in dispositions.items() if d == "pending"),
                         key=adr_num)
        if pending:
            problems.append(_gate_problem(
                "governs_backfill_complete: true cannot stand while cohort "
                f"ADR(s) are pending: {', '.join(pending)} — Σ backfilled + "
                "Σ no-rule must equal |cohort| over distinct ADRs"))

    # (iv) cross-validation: receipts x log ops x journal hooks, joined by
    # batch id and date.
    tree_dir = resolve_tree(root)
    tree = tree_name(root)  # display spelling only; resolve_tree validated it
    log_path = tree_dir / "log.md"
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    log_entries = _log_entries(log_text)
    for entry in log_entries:
        if entry["op"] == "backfill" and entry["subject"] not in reviews["batches"]:
            problems.append(_gate_problem(
                f"log.md entry names batch {entry['subject']!r}, which the "
                "reviews manifest does not record"))
    for bid in sorted(reviews["batches"]):
        batch = reviews["batches"][bid]
        signed = batch["signed"]
        if signed is None:
            continue  # mid-batch state owes no corroboration until signed
        # Clause 2: the batch's receipt set covers every enumerated entry — a
        # signed batch enumerating a receipt-less handle is one problem.
        receipted = {r["handle"] for r in reviews["receipts"] if r["batch"] == bid}
        for h in sorted(batch["handles"]):
            if h not in receipted:
                problems.append(_gate_problem(
                    f"signed batch {bid!r} enumerates handle {h} but records "
                    "no receipt for it — the receipt set covers every entry",
                    handle=h))
        derived = derived_adr_ids(batch)
        corroborating = [e for e in log_entries
                         if e["op"] == "backfill" and e["subject"] == bid
                         and e["date"] == signed]
        if not corroborating:
            problems.append(_gate_problem(
                f"signed batch {bid!r} has no corroborating log.md entry "
                f"`## [{signed}] backfill | {bid}`"))
        else:
            ok = False
            for e in corroborating:
                m = _ADRS_LINE_RE.search(e["body"])
                if m:
                    ids = m.group(1).split()
                    # The shape guard runs BEFORE the numeric sort: a garbage
                    # token is an authored document error (this entry fails to
                    # corroborate), never an adr_num crash.
                    if all(re.fullmatch(r"ADR-\d{4}", i) for i in ids) \
                            and sorted(ids, key=adr_num) == derived:
                        ok = True
                        break
            if not ok:
                problems.append(_gate_problem(
                    f"no log.md entry for batch {bid!r} carries `ADRs: "
                    f"{' '.join(derived)}` — the log op must name the batch's "
                    "enumerated ADR ids"))
        journal_path = tree_dir / "journal" / f"{signed[:7]}.md"
        journal_text = (journal_path.read_text(encoding="utf-8")
                        if journal_path.is_file() else "")
        hooks = [e for e in _journal_entries(journal_text)
                 if e["category"] == "review"
                 and e["subject"] == f"backfill sign-off {bid}"
                 and e["date"] == signed]
        if not hooks:
            problems.append(_gate_problem(
                f"signed batch {bid!r} has no corroborating journal hook "
                f"`## [{signed} HH:MM] review | backfill sign-off {bid}` in "
                f"{tree}/journal/{signed[:7]}.md"))
        else:
            # The subject match above already pins the batch id inside the
            # hook's block, so only the ADR ids are checked here.
            named = [h for h in hooks
                     if all(a in h["block"] for a in derived)]
            if not named:
                problems.append(_gate_problem(
                    f"the journal hook for batch {bid!r} must name the batch "
                    f"id and its ADR ids ({', '.join(derived)})"))

    problems.sort(key=lambda p: (str(p["source_adr"]), str(p["handle"]),
                                 p["problem"]))
    return problems, info


# ── run-snapshot artifact bindings (.yaml only) ────────────────────────────

def _run_binding_domain(runs: Path, repo_root: Path | None, *, _source_io=None) -> tuple[str, set[str], Path | None]:
    """Resolve only explicit configuration or immediately adjacent source identities."""
    prefix = ""
    tree = runs.parent.parent if runs.name == "runs" and runs.parent.name == "promptbooks" else None
    if repo_root is not None:
        root = _source_resolve(Path(repo_root), _source_io)
        tree = resolve_tree(root, _source_io=_source_io)
        expected = tree / "promptbooks" / "runs"
        resolved = _source_resolve(runs, _source_io)
        if resolved != _source_resolve(expected, _source_io) or not resolved.is_relative_to(_source_resolve(tree, _source_io)):
            raise GovernsValidationError([_gate_problem(
                "run bindings require the configured tree's promptbooks/runs path")])
        prefix = artifact_prefix(root, _source_io=_source_io)
    identities: set[str] = set()
    if tree is not None:
        adrs = tree / "adrs"
        if _source_contained(tree, "adrs", _source_io) is None:
            raise GovernsValidationError([_gate_problem("run-binding sources escape their tree")])
        for path in sorted(_source_glob(adrs, "*.md", _source_io)):
            if not re.match(r"(?:[A-Z][A-Z0-9]{1,9}-)?ADR-\d{4}(?:-|\.md$)", path.name):
                continue
            if _source_contained(adrs, path.name, _source_io) is None:
                raise GovernsValidationError([_gate_problem("run-binding source escapes its ADR directory")])
            if not _source_is_file(path, _source_io):
                continue
            aid = read_frontmatter(_source_text(path, _source_io)).get("id")
            if isinstance(aid, str) and re.fullmatch(r"(?:[A-Z][A-Z0-9]{1,9}-)?ADR-\d{4}", aid):
                identities.add(aid)
    return prefix, identities, tree


def _run_artifact_is_held(tree: Path | None, artifact, repo_root, source_io) -> bool:
    """Classify before identity matching, without discovering parent config."""
    if tree is None:
        return False
    import importlib.util
    key = "_retained_evidence"
    retained = sys.modules.get(key)
    if retained is None:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("retained_evidence.py"))
        retained = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(retained)
        sys.modules[key] = retained
    try:
        # Traversal refuses before any not-held answer, in every mode.
        path = retained._candidate(Path(str(artifact)))
    except retained.RetainedEvidenceRefusal:
        raise GovernsValidationError([_gate_problem("retained-evidence-layout-refused")]) from None
    # An ASCII code point has exactly one encoding and no normalization or
    # case-fold length variant. A leading name of more than 255 ASCII
    # characters therefore exceeds the 255-unit name limit of APFS and ext4
    # under every lookup, and cannot name an existing entry. Nothing beneath
    # it exists either, so it is an ordinary reference and is never probed.
    # Only the leading name qualifies, because a shorter prefix can exist and
    # reach a holding, and that spelling keeps the probe's answer. A
    # non-ASCII name keeps the fail-closed probe: APFS matches names
    # normalization-insensitively, so a long spelling can resolve to a
    # shorter stored name.
    names = [part for part in path.parts if part != path.anchor]
    if names and names[0].isascii() and len(names[0]) > 255:
        return False
    resolved_root = None
    if repo_root is not None:
        resolved_root = _source_resolve(Path(repo_root), source_io)
    excluded = ()
    if source_io is not None:
        from admission_source_io import SourceIOExcluded
        excluded = (SourceIOExcluded,)
    try:
        if resolved_root is not None:
            if not path.is_absolute():
                path = resolved_root / path
            elif source_io is not None and not (
                    path.is_relative_to(Path(repo_root)) or path.is_relative_to(resolved_root)):
                # Guarded IO cannot see outside its root, so an outside
                # absolute is classified by spelling alone, with no filesystem
                # access. A spelled holding refuses. An outside symlink that
                # spells no holding reads as an ordinary reference: an
                # accepted, recorded mode divergence from the default probe.
                if retained.spells_holding(path):
                    retained._refuse()
                return False
        return retained.is_retained_evidence_path(tree, path, _source_io=source_io)
    except retained.RetainedEvidenceRefusal:
        raise GovernsValidationError([_gate_problem("retained-evidence-layout-refused")]) from None
    except excluded:
        return True


def read_run_bindings(runs: Path, *, repo_root: Path | None = None, _source_io=None) -> dict[str, dict]:
    """Map ADR id -> {"books": set, "runs": set} from run-snapshot artifacts.

    Scans every `run-*.yaml` snapshot, walks each prompt's `artifacts` list, and
    binds complete identities in the configured namespace or active source domain.
    Without an explicit root, complete bare legacy references remain readable,
    including absent source records; no parent configuration is searched.
    Legacy `.md` runs are ignored (ADR-0085 Decision 2, .yaml runs only).
    """
    import yaml
    runs = Path(runs)
    prefix, identities, tree = _run_binding_domain(runs, repo_root, _source_io=_source_io)
    namespace = f"{prefix}-ADR-" if prefix else "ADR-"
    tokens = re.compile(r"(?<![\w-])(?:[A-Z][A-Z0-9]{1,9}-)?ADR-\d{4}(?!\w)")
    bindings: dict[str, dict] = {}
    if not _source_is_dir(runs, _source_io):
        return bindings
    if _source_io is None:
        snapshots = runs.glob("*/run-*.yaml")
    else:
        snapshots = [snap for directory in _source_glob(runs, "*", _source_io)
                     if _source_is_dir(directory, _source_io)
                     for snap in _source_glob(directory, "run-*.yaml", _source_io)]
    for snap in sorted(snapshots):
        try:
            doc = yaml.safe_load(_source_text(snap, _source_io)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        book_id = str(doc.get("book_id") or "")
        run_id = str(doc.get("run_id") or "")
        ref = f"{book_id}/{run_id}"
        for prompt in doc.get("prompts") or []:
            if not isinstance(prompt, dict):
                continue
            for art in prompt.get("artifacts") or []:
                if _run_artifact_is_held(tree, art, repo_root, _source_io):
                    continue
                for aid in tokens.findall(str(art)):
                    if not aid.startswith(namespace) and aid not in identities:
                        continue
                    slot = bindings.setdefault(aid, {"books": set(), "runs": set()})
                    if book_id:
                        slot["books"].add(book_id)
                    if book_id and run_id:
                        slot["runs"].add(ref)
    return bindings


# ── artifact builders ──────────────────────────────────────────────────────

def _cell(value: str) -> str:
    """Sanitize a value for a Markdown table cell (one line, escaped pipes).

    `redact` is the bound and the redaction — the shared mechanism, at the
    `MESSAGE_LIMIT` bound because a cell holds a rule sentence and the value
    bound would truncate the artifact. The pipe escape is this channel's own
    rule and composes on top; `doctrine_projection._md_escape` is the same
    two layers over the same rows."""
    folded = str(value).replace("\n", " ")
    return redact(folded, quoted=False,
                  limit=MESSAGE_LIMIT).replace("|", "\\|").strip()


def build_rule_table(records: list[dict], reviews: dict | None = None,
                     governs_from: int | None = None) -> str:
    """The rule table: rows sorted by (ADR num, handle).

    With `reviews` threaded (ADR-0088 clause 6), an unreviewed row's handle
    cell carries an `**unreviewed**` suffix — soft-exclusion is exclusion from
    resolution, not from the file — and a trailing `## Removed handles` roster
    (handle, last live digest, batch; sorted) is emitted when, and only when,
    the reviews manifest holds current signed tombstones. A tree whose entries
    are all prospective (or vacuous) renders byte-identically to the
    reviews-less build.

    An observation record's row renders its `OBS-NNNN` id in the `source ADR`
    column. The header is kept: renaming it would move bytes at zero
    observations and break the identity postcondition. The `**unreviewed**`
    suffix never lands on an observation row — see `build_resolver` for why
    `review_state` is omitted there.

    ADR-0097 part 6: a RETIRED handle leaves the live rows above and renders in
    a trailing `## Retired handles` roster instead, so the record survives the
    retirement. That roster is emitted independently of `reviews` — retirement
    reads no receipt — and its columns differ from the removed roster's on
    purpose: the two lanes mean different things and a reader must be able to
    tell them apart at a glance. Every column is derivable WITHOUT reading the
    retired ADR's body, which is what makes the roster compatible with
    retirement being one-way: `handle`, `rule` and `source ADR` come from the
    retired entry's own untouched frontmatter, and `retired by` from the
    retiring entries'. A tree with no retirement renders byte-identically to
    one built before this section existed.
    """
    ordered = sorted(live_records(records), key=record_sort_key)
    out = [
        "# summaries rule table",
        "",
        "_Handle -> governing rule, projected from ADR `governs` blocks. "
        "Regenerated; edits are overwritten._",
        "",
        "| handle | domain | rule | scope | provenance | source ADR |",
        "|--------|--------|------|-------|------------|------------|",
    ]
    for r in ordered:
        handle_cell = _cell(r["handle"])
        if reviews is not None and r.get("source_kind", "adr") == "adr" \
                and review_state(r, reviews, governs_from) == "unreviewed":
            handle_cell += " **unreviewed**"
        out.append(
            f"| {handle_cell} | {_cell(r['domain'])} | {_cell(r['rule'])} "
            f"| {_cell(r['scope'])} | {_cell(r['provenance'])} | {_cell(r['source_adr'])} |"
        )
    if reviews is not None:
        removed = removed_handles(reviews)
        if removed:
            out += [
                "",
                "## Removed handles",
                "",
                "_Handles whose current signed receipt is a tombstone: dropped "
                "from the resolver, retained in the ledger against "
                "re-admission._",
                "",
                "| handle | last live digest | batch |",
                "|--------|------------------|-------|",
            ]
            for handle in sorted(removed):
                tomb = removed[handle]
                out.append(f"| {_cell(handle)} | {tomb['digest']} | {_cell(tomb['batch'])} |")
    retired = retired_records(records)
    if retired:
        out += [
            "",
            "## Retired handles",
            "",
            "_Handles a later decision displaced: dropped from the live rows "
            "above and from the resolver, retained here with their rule text "
            "so the record survives the retirement. Distinct from the removed "
            "lane above — a removal withdraws an admission a human made, a "
            "retirement displaces a rule that stays on the record._",
            "",
            "| handle | rule | source ADR | retired by |",
            "|--------|------|------------|------------|",
        ]
        for r in retired:
            out.append(
                f"| {_cell(r['handle'])} | {_cell(r['rule'])} "
                f"| {_cell(r['source_adr'])} | {_cell(', '.join(r['retired_by']))} |")
    return "\n".join(out) + "\n"


def build_resolver(records: list[dict], reviews: dict | None = None,
                   governs_from: int | None = None,
                   alias_rows: dict[str, dict] | None = None) -> dict:
    """handle -> {rule, provenance, disposition, authority, source_adr,
    [review_state]}, plus one alias row per `decided` observation handle.

    disposition and authority are DERIVED from provenance, never stored —
    `authority_for` already yields `descriptive` for observed provenance, so
    an observation row needs no branch of its own. With `reviews` threaded
    (ADR-0088 clause 6), each ADR row also carries the `review_state` facet
    derived from the receipt chain at projection time — and an unreviewed
    entry stays in the file as a non-resolving row.

    `review_state` is OMITTED on an observation row. The facet derives from
    ADR-0088 backfill receipts, and an observation sits outside that
    machinery entirely: its host number (OBS-0001 -> 1) is below any
    `governs_from`, so the ADR derivation would read it as an unreviewed
    backfill entry and mislabel a ratified observation. Omission is the fix,
    not a new enum value — the ADR-0088 vocabulary stays closed.

    `alias_rows` (from `collect_observation_alias_rows`) are merged last; an
    alias handle colliding with a rule handle is a validation error.

    ADR-0097 part 6: a RETIRED handle is absent from the resolver entirely, so
    a citation of it stops resolving — the same terminal state a tombstoned
    handle already reaches. Its record is retained by the rule table's
    `## Retired handles` roster.
    """
    resolver: dict[str, dict] = {}
    for r in sorted(live_records(records), key=record_sort_key):
        prov = r["provenance"]
        row = {
            "rule": r["rule"],
            "provenance": prov,
            "disposition": disposition_for(prov),
            "authority": authority_for(prov),
            "source_adr": r["source_adr"],
            # RAW frontmatter `status`, never a normalised enum. The value
            # set is the union of the ADR state-machine vocabulary and the
            # observation constant, and comparison is CASE-SENSITIVE — a reader
            # lower-casing before comparing would merge states that differ.
            # Do not "tidy" this into a closed enum: normalising would put a
            # derived value where a copied one belongs, and the field's whole
            # purpose is to report what the source record said.
            # The host record's lifecycle status, verbatim and unnormalised:
            # the ADR state-machine value for an ADR row, the observation
            # constant for an observation row. It says which record the rule
            # came from, never whether the rule is adopted — `disposition`,
            # `authority` and `review_state` keep their own axes. Absent
            # status projects JSON null rather than a sentinel string, so a
            # consumer cannot mistake a placeholder for a real value.
            "source_status": r.get("source_status"),
        }
        if reviews is not None and r.get("source_kind", "adr") == "adr":
            row["review_state"] = review_state(r, reviews, governs_from)
        resolver[r["handle"]] = row
    for handle in sorted(alias_rows or {}):
        if handle in resolver:
            raise GovernsValidationError([{
                "source_adr": None, "handle": handle,
                "problem": "alias handle also used by a rule row — handles must be "
                "globally unique"}])
        resolver[handle] = dict(alias_rows[handle])
    return resolver


def build_implementation_map(adrs: Path, runs: Path, *, repo_root: Path | None = None, _source_io=None) -> str:
    """ADR <-> run map: one row per active ADR, sorted by ADR number.

    implemented = yes when the ADR has >=1 run binding (an artifact of some
    .yaml run references it).
    """
    bindings = (read_run_bindings(runs, repo_root=repo_root) if _source_io is None else
                read_run_bindings(runs, repo_root=repo_root, _source_io=_source_io))
    out = [
        "# ADR <-> run implementation map",
        "",
        "_Each ADR bound to the promptbooks and runs whose artifacts reference "
        "it. Regenerated; edits are overwritten._",
        "",
        "| ADR | implemented | promptbooks | runs |",
        "|-----|-------------|-------------|------|",
    ]
    for aid in collect_adr_ids(adrs, _source_io=_source_io):
        slot = bindings.get(aid)
        if slot and slot["runs"]:
            implemented = "yes"
            books = ", ".join(sorted(slot["books"])) or "—"
            run_refs = ", ".join(sorted(slot["runs"])) or "—"
        else:
            implemented, books, run_refs = "no", "—", "—"
        out.append(f"| {aid} | {implemented} | {books} | {run_refs} |")
    return "\n".join(out) + "\n"


# ── manifest + coverage ────────────────────────────────────────────────────

def read_manifest(root: Path, *, _source_io=None) -> dict:
    """Parse <docs_dir>/manifest.yml into a dict ({} when absent)."""
    path = resolve_tree(root, _source_io=_source_io) / "manifest.yml"
    if not _source_exists(path, _source_io):
        return {}
    import yaml
    return yaml.safe_load(_source_text(path, _source_io)) or {}


def governs_from(manifest: dict) -> int | None:
    """The `adr.governs_from` cohort boundary (None when absent/null)."""
    adr = manifest.get("adr") or {}
    val = adr.get("governs_from")
    return int(val) if val is not None else None


def governs_exempt_entries(manifest: dict) -> list[dict]:
    """The OPTIONAL `adr.governs_exempt` list parsed into `{adr, reason}`
    records, in document order (read-tolerant of absence).

    Two member shapes coexist (ADR-0090's E1 grammar, deferred from ADR-0088):
      - a bare ADR-id string `"ADR-NNNN"` -> `{"adr": "ADR-NNNN", "reason": None}`
        (the back-compatible form `governs_exempt` has always accepted);
      - a mapping `{adr: ADR-NNNN, reason: "..."}` -> its id and reason, the
        reason-bearing form the honest `exempt(reason)` doctrine state needs.
    A mapping missing a string `adr` is skipped read-tolerantly, mirroring the
    tolerance `governs_exempt` applies to a non-list value; `reason` is coerced
    to a stripped string, or None when absent/blank. This is the ONE parse of
    the exempt grammar — `governs_exempt` derives its bare-id list from it, so
    the two can never disagree about which ADRs are exempt.
    """
    adr = manifest.get("adr") or {}
    val = adr.get("governs_exempt")
    if not isinstance(val, list):
        return []
    entries: list[dict] = []
    for member in val:
        if isinstance(member, dict):
            aid = member.get("adr")
            if not isinstance(aid, str) or not aid.strip():
                continue
            reason = member.get("reason")
            reason = reason.strip() if isinstance(reason, str) and reason.strip() else None
            entries.append({"adr": aid.strip(), "reason": reason})
        else:
            entries.append({"adr": str(member), "reason": None})
    return entries


def governs_exempt(manifest: dict) -> list[str]:
    """The OPTIONAL `adr.governs_exempt` list as bare ADR ids (read-tolerant of
    absence). Derived from `governs_exempt_entries` so the coverage lane and the
    doctrine reason lane read one parse of the same grammar. For the bare-string
    form this is byte-identical to the historic `[str(x) for x in val]`; a
    reason-bearing mapping member contributes its `adr` id."""
    return [e["adr"] for e in governs_exempt_entries(manifest)]


def coverage(adrs: Path, manifest: dict) -> dict:
    """Governs-coverage report for the cohort at or above `governs_from`.

    An ADR >= governs_from is covered iff it carries a non-empty `governs` list
    OR is listed in `adr.governs_exempt`. Absent `governs_from` => inert
    (empty cohort, nothing uncovered).

    Raises `GovernsValidationError` (FIX #3) when an in-cohort ADR's governs
    list carries a structurally malformed entry (no handle) or an
    out-of-enum/missing `provenance` — such a block must NOT silently satisfy
    coverage via a merely non-empty list.
    """
    threshold = governs_from(manifest)
    exempt = set(governs_exempt(manifest))
    cohort: list[str] = []
    covered: list[str] = []
    uncovered: list[str] = []
    if threshold is None:
        return {"governs_from": None, "cohort": [], "covered": [], "uncovered": []}
    entries_by_adr: list[tuple[str, list]] = []
    for path in adr_paths(adrs):
        fm = read_frontmatter(path.read_text(encoding="utf-8"))
        aid = str(fm.get("id") or "")
        if not aid or adr_num(aid) < threshold:
            continue
        cohort.append(aid)
        raw = raw_governs_entries(fm)
        entries_by_adr.append((aid, raw))
        entries = [e for e in raw if isinstance(e, dict)]
        if entries or aid in exempt:
            covered.append(aid)
        else:
            uncovered.append(aid)
    shape_problems = _governs_shape_problems(entries_by_adr)
    if shape_problems:
        raise GovernsValidationError(shape_problems)
    cohort.sort(key=adr_num)
    covered.sort(key=adr_num)
    uncovered.sort(key=adr_num)
    return {"governs_from": threshold, "cohort": cohort,
            "covered": covered, "uncovered": uncovered}


def rule_length_baseline(manifest: dict) -> tuple[set[str], bool]:
    """`(ids, declared)` — the ADR identities that predate the rule-length advisory.

    AN ENUMERATED SET, NOT A NUMBER, and the difference is the whole point. This tree already
    carries two numeric cohort boundaries for adjacent rules, and both are `>= N` predicates. An
    ADR numbered BELOW such a boundary but added after rollout escapes it, and the policy this
    serves requires a newly added ADR to be checked whatever its number or its date. Only an
    enumerated identity set gives that. The precedent for the SHAPE is the frozen backfill cohort
    in the same manifest block: enumerated once, recorded, closed from then on.

    `declared` distinguishes "the key is present and empty" from "the key is absent". An empty
    baseline is the correct and self-maintaining state for a NEW tree — every ADR is new relative
    to nothing — while an absent key means a tree that has not snapshotted yet, and the caller
    reports that as a discoverable advisory rather than leaving the check silently inert.
    """
    adr = manifest.get("adr")
    if not isinstance(adr, dict) or RULE_LENGTH_BASELINE_KEY not in adr:
        return set(), False
    raw = adr.get(RULE_LENGTH_BASELINE_KEY)
    if raw is None:
        return set(), False
    if not isinstance(raw, list):
        # Read-tolerant, like the sibling exemption reader: a mis-typed value yields an empty
        # baseline rather than an exception, because this advisory may never raise. It reports
        # NOT-DECLARED rather than an empty declaration, so a tree whose key is a bare string
        # falls into the inert lane and the caller says the key needs attention — which is true,
        # if imprecisely worded for that case. The alternative, treating a mis-typed value as an
        # empty baseline, would switch the advisory ON for the whole corpus on a typo.
        return set(), False
    return {str(x) for x in raw if isinstance(x, str)}, True


def rule_length_advisories(adrs: Path, manifest: dict) -> list[dict]:
    """One advisory row per `governs` rule longer than the recommended maximum. Never raises.

    ADVISORY MEANS ADVISORY. This returns rows and nothing else: it never raises, never mutates,
    and deliberately does NOT route through `_governs_shape_problems` or `GovernsValidationError`,
    because that class is a DOCUMENT verdict every caller turns into exit 1. A rule being long is
    not a defect in the corpus; it is a signal to the author that the rule probably wants
    rewriting.

    PER-FILE TOLERANCE, stated because "never raises" is otherwise not implementable. A file this
    cannot read, a frontmatter block it cannot parse, and a `rule` value that is not a string each
    contribute NO row and NO error. That is not the advisory forgiving a malformed ADR — the
    existing validators still fail on exactly those inputs, in their own lane, unchanged. The
    advisory neither introduces a failure nor suppresses one.

    GRANDFATHERING is by identity, never by date or number: an ADR whose id is in the baseline is
    skipped outright, so an existing ADR and every later edit to it stay exempt, while an ADR
    absent from the baseline is checked however it is numbered.
    """
    baseline, declared = rule_length_baseline(manifest)
    if not declared:
        return []
    rows: list[dict] = []
    try:
        paths = adr_paths(adrs)
    except Exception:  # noqa: BLE001 — an unreadable corpus yields no advice, never an error
        return []
    for path in paths:
        try:
            fm = read_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — see PER-FILE TOLERANCE above
            continue
        aid = str(fm.get("id") or "")
        if not aid or aid in baseline:
            continue
        for e in governs_entries(fm):
            rule = e.get("rule")
            if not isinstance(rule, str):
                continue
            length = len(rule.strip())
            if length <= RULE_LENGTH_RECOMMENDED_MAX:
                continue
            handle = e.get("handle")
            rows.append({
                "file": str(path),
                "handle": redact(handle, quoted=False) if isinstance(handle, str) else None,
                "length": length,
                "recommended_max": RULE_LENGTH_RECOMMENDED_MAX,
                "error": f"governs rule is {length} characters; the recommended maximum is "
                         f"{RULE_LENGTH_RECOMMENDED_MAX}. A rule this long is unlikely to be one "
                         "obligation. Keep the obligation and the conditions necessary to it, move "
                         "rationale and examples into the body, and split it only where the parts "
                         "are independently enforceable.",
            })
    rows.sort(key=lambda r: (r["file"], r["handle"] or ""))
    return rows
