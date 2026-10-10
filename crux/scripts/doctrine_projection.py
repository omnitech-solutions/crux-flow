# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0"]
# ///
"""doctrine_projection.py — the importable core of the doctrine layer.

Per the doctrine decision (ADR-0090), doctrine is a deterministic, model-free
templated projection of the summaries projection: one entry per `governs`
domain, carrying each live rule, its disposition, the mechanical BASIS
standing behind it, and the domain's reconciliation state against the ratified
invariants that structurally link to it. The basis names which fact was
measured — `run-bound` or `not-run-bound` from the run bindings
(`sp.read_run_bindings`) the summaries implementation map is built from for an
ADR-sourced rule, `evidence-resolves` or `evidence-missing` from evidence-path
resolution for an observation-sourced one — and no basis value is evidence
that the rule holds (ADR-0097 part 7). This module
holds the shared machinery; three drivers import it:

  - compile-doctrine.py             the regenerator + byte-stable --dry-run gate
  - check-doctrine-reconciliation.py  the consistency gate (BROKEN domains fail)
  - signoff-reconciliation.py       the single human sign-off write path over
                                    the reconciliation ledger

Determinism is the contract: everything sorts, the compile path calls no model
and reads no wall-clock, and the reconciliation content digest reuses
`summaries_projection.space_fold` — the SAME space-folded canonicalization the
ADR-0088 backfill content_digest uses — so the two can never drift apart.

Key design points, honored exactly:
  - The reconciliation content digest binds (invariant text, rule text): the
    invariant ledger page's `## The invariant` section body and the governing
    rule text, each space-folded and newline-joined. A cosmetic edit to any
    OTHER ledger section does not move it (only `## The invariant` is read).
    It deliberately does NOT reuse the six-field governs content_digest —
    doctrine binds a different pair of texts.
  - Pairing is generated deterministically from the existing structural link:
    for each ratified invariant carrying `related_adrs`, and each active ADR in
    that list bearing a `governs` block, every (invariant-id, governs-handle)
    pair is a candidate. No model, no full cross-product.
  - A domain renders its belief only when every candidate pairing in it has a
    digest-current record with verdict `compatible` or `reconciled`. Any pairing
    un-adjudicated, digest-stale, or `collision` marks that DOMAIN broken.
  - The five legible renderings: a domain is `believed` / `backfilled` /
    `no-applicable-invariant` / BROKEN; an exempt ADR (no domain of its own) is
    rendered `exempt(reason)` in a separate roster, reading the ADR-0090 E1
    reason grammar (`sp.governs_exempt_entries`).

The observation widening (ADR-0095 requirement 5), additive over the above:
  - The pairing seed GAINS one class and loses none: the pair (authored rule,
    ratified observation) whose members share a `governs.domain` AND where an
    evidence path of the observation lies inside the authored rule's declared
    `scope` AND, where several rules in the domain contain that path, only the
    rule whose declared scope is the NARROWEST containing one. All three
    conjuncts are load-bearing — a pairing on shared domain alone is the
    rejected Option D. `observation_pairings` implements it; `candidate_pairings`
    returns legacy union added.
  - The ledger key generalizes from (invariant-id, governs-handle) to an ordered
    pair of member identities. Slot B (`handle:`) always holds an authored
    `ADR-NNNN/slug` handle. Slot A (`invariant:`) widens to `INV-NNNN` OR an
    observation handle `OBS-NNNN/slug` (`MEMBER_A_RE`). RECORDED BACK-COMPAT
    DECISION: the on-disk field names stay `invariant:` and `handle:`, and the
    record/pairing dicts keep `invariant` as the slot-A key, because renaming
    them would rewrite every signed record; a record gains `member_kind` in
    {"invariant", "observation"} instead. The reconciliation content digest is
    unchanged as a function — slot-A text then slot-B text — so every existing
    digest recomputes identically; for the added class slot-A text is the
    observation rule's text and slot-B text is the authored rule's text.
  - Per-domain authority rendering: a domain holding an observation-sourced
    rule reads as an observed, ratified description carrying its evidence —
    an `authority: descriptive | mixed` heading suffix, an `_Observed evidence:_`
    table, and a `basis` derived from evidence paths that resolve (never from
    run bindings). Each rendering is emitted ONLY when the domain
    holds an observation rule, so a tree with none renders byte-identically.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import posixpath
import re
import stat
from pathlib import Path

import observation_evidence as oe
import summaries_projection as sp
from untrusted import MESSAGE_LIMIT, redact

DOCTRINE_DIRNAME = "doctrine"
# Re-exported, not redefined: the retirement machinery's signature-orphan
# refusal reads this file from `summaries_projection`, which cannot import this
# module (the dependency runs one way). One spelling, two readers.
RECONCILIATIONS_FILENAME = sp.RECONCILIATIONS_FILENAME

VERDICTS = ("compatible", "reconciled", "collision")

# Per-pairing status vocabulary. The first two render the belief; the last three
# each mark the whole domain BROKEN.
STATUS_COMPATIBLE = "compatible"
STATUS_RECONCILED = "reconciled"
STATUS_COLLISION = "collision"
STATUS_UNADJUDICATED = "un-adjudicated"
STATUS_DIGEST_STALE = "digest-stale"

# The digest-current, unblocking statuses. `collision`, `un-adjudicated`, and
# `digest-stale` each block; the two below both render the belief (ADR-0090
# req 2). This is the ONE vocabulary split — domain_state and broken_domains
# both test membership in it (as `not in _UNBLOCKING`), so "which statuses
# block" cannot drift between the two call sites.
_UNBLOCKING = (STATUS_COMPATIBLE, STATUS_RECONCILED)

# Domain states.
STATE_BELIEVED = "believed"
STATE_BACKFILLED = "backfilled"
STATE_NO_INVARIANT = "no-applicable-invariant"
STATE_BROKEN = "BROKEN"

# The `basis` column's closed value set (ADR-0097 part 7). One column name over
# one concept, and the value names WHICH FACT WAS MEASURED rather than asserting
# that anyone verified the rule. The first pair is the ADR-sourced derivation
# (run bindings), the second the observation-sourced one (evidence resolution);
# a single name over both, which is what `implemented` was, made the column read
# as a verification claim it never earned on either side.
BASIS_RUN_BOUND = "run-bound"
BASIS_NOT_RUN_BOUND = "not-run-bound"
BASIS_EVIDENCE_RESOLVES = "evidence-resolves"
BASIS_EVIDENCE_MISSING = "evidence-missing"
BASIS_VALUES = (BASIS_RUN_BOUND, BASIS_NOT_RUN_BOUND,
                BASIS_EVIDENCE_RESOLVES, BASIS_EVIDENCE_MISSING)

# Bare `INV-NNNN` only — a repo running a non-empty `.bionic.yml` artifact_prefix
# (e.g. `CRX-INV-0001`) silently fails this match and read_invariants() skips
# the page. Prefixed trees are out of scope here, consistent with the same
# gap already filed against the ADR glob elsewhere (BRIEF-five-arch-
# projection-residuals-from-pb-0075) rather than fixed ad hoc per call site.
_INV_ID_RE = re.compile(r"^INV-\d{4}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Slot A of the widened ledger key (ADR-0095 requirement 5): a ratified
# invariant id OR a ratified observation's governs handle. Slot B stays
# `sp._HANDLE_ANCHOR_RE` (an authored ADR handle) unchanged.
MEMBER_A_RE = re.compile(r"^(INV-\d{4}|OBS-\d{4}/[a-z][a-z0-9-]*)$")
MEMBER_KIND_INVARIANT = "invariant"
MEMBER_KIND_OBSERVATION = "observation"

# The `path:line-range` evidence grammar (docs/AGENTS.md §17.1); the path
# half never carries a colon, so the first colon splits it.
_EVIDENCE_RE = re.compile(r"^(?P<path>[^\s:]+):(?P<start>\d+)-(?P<end>\d+)$")


def member_kind(slot_a: str) -> str:
    """Which member kind a slot-A value names — `invariant` for INV-NNNN,
    `observation` for an OBS-NNNN/slug handle."""
    return MEMBER_KIND_INVARIANT if slot_a.startswith("INV-") else MEMBER_KIND_OBSERVATION


class DoctrineValidationError(ValueError):
    """The reconciliation ledger or an invariant ledger page is structurally
    invalid. A DOCUMENT verdict — the corpus itself is wrong — never the
    environment-crash lane. Drivers surface `problems` as exit-1 findings JSON,
    the same shape `summaries_projection.GovernsValidationError` uses."""

    def __init__(self, problems: list[dict]):
        self.problems = problems
        detail = "; ".join(str(p.get("problem")) for p in problems)
        super().__init__(f"invalid doctrine input(s): {detail}")


def _problem(problem: str, invariant=None, handle=None) -> dict:
    return {"invariant": invariant, "handle": handle, "problem": problem}


# ── tree resolution ────────────────────────────────────────────────────────

def invariants_dir(root: Path, *, _source_io=None) -> Path:
    options = {} if _source_io is None else {"_source_io": _source_io}
    return sp.resolve_tree(root, **options) / "invariants"


def doctrine_dir(root: Path, *, _source_io=None) -> Path:
    options = {} if _source_io is None else {"_source_io": _source_io}
    return sp.adrs_dir(root, **options) / DOCTRINE_DIRNAME


def reconciliations_path(root: Path, *, _source_io=None) -> Path:
    options = {} if _source_io is None else {"_source_io": _source_io}
    return doctrine_dir(root, **options) / RECONCILIATIONS_FILENAME


# ── invariant ledger reads ─────────────────────────────────────────────────

def invariant_section(text: str) -> str:
    """The body of the `## The invariant` section of a ledger page, stripped.

    This is THE digest text domain (ADR-0090 commander decision): a cosmetic
    edit to Class / Why / Check / Ratification does not enter it, so it never
    marks a domain digest-stale. Section body = every line after the
    `## The invariant` heading up to the next `## ` heading (or EOF).
    """
    # Reuses summaries_projection's private `_adr_body` rather than
    # reimplementing "strip the frontmatter fence" — same package, same file
    # shape (frontmatter + Markdown body), one parse for both projections.
    body = sp._adr_body(text)  # everything after the closing frontmatter fence
    out: list[str] = []
    capturing = False
    for line in body.splitlines():
        if re.match(r"^##\s+The invariant\s*$", line):
            capturing = True
            continue
        if capturing and re.match(r"^##\s+", line):
            break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def read_invariants(root: Path, *, _source_io=None) -> list[dict]:
    """Every invariant ledger page, sorted by id. Each record:
    {id, ratification, related_adrs (list[str]), invariant_text, path}.

    A ledger page is a `<slug>.md` under invariants/ carrying an `INV-NNNN`
    frontmatter id; index.md and non-ledger files are skipped. `reconciliation.yml`
    is not a `.md` file and never matches. Every page is read regardless of
    `ratification` — an `observed` invariant is filtered out downstream (only
    `ratified` invariants seed pairings, `candidate_pairings`), but it still
    enters `invariants_sha256` below, so an edit to an observed page's text
    still moves doctrine's freshness stamp.
    """
    options = {} if _source_io is None else {"_source_io": _source_io}
    inv_dir = invariants_dir(root, **options)
    out: list[dict] = []
    if not sp._source_is_dir(inv_dir, _source_io):
        return out
    for path in sorted(sp._source_glob(inv_dir, "*.md", _source_io)):
        if not sp._source_is_file(path, _source_io):
            continue
        text = sp._source_text(path, _source_io)
        fm = sp.read_frontmatter(text)
        iid = str(fm.get("id") or "")
        if not _INV_ID_RE.match(iid):
            continue
        related = fm.get("related_adrs")
        related = [str(a) for a in related] if isinstance(related, list) else []
        out.append({
            "id": iid,
            "ratification": str(fm.get("ratification") or ""),
            "related_adrs": related,
            "invariant_text": invariant_section(text),
            "path": path,
        })
    out.sort(key=lambda r: r["id"])
    return out


def invariants_sha256(invariants: list[dict]) -> str:
    """SHA-256 over the doctrine-relevant facets of every invariant, in id
    order: id, ratification, sorted related_adrs, and the space-folded
    `## The invariant` text. A cosmetic edit to any OTHER ledger section leaves
    this stable — doctrine's freshness stamp tracks only what doctrine reads."""
    h = hashlib.sha256()
    for inv in sorted(invariants, key=lambda r: r["id"]):
        # `\0` is unambiguous as a field separator here: none of the four
        # hashed facets (an INV-NNNN id, the ratification enum, a
        # space-space-joined ADR-id list, or space-folded prose) can itself
        # contain a NUL byte, so no two distinct facet tuples can collide by
        # shifting content across the separator.
        h.update(inv["id"].encode("utf-8"))
        h.update(b"\0")
        h.update(inv["ratification"].encode("utf-8"))
        h.update(b"\0")
        h.update(" ".join(sorted(inv["related_adrs"])).encode("utf-8"))
        h.update(b"\0")
        h.update(sp.space_fold(inv["invariant_text"]).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


# ── the reconciliation content digest (ADR-0090 req 2) ──────────────────────

def reconciliation_canonical_string(invariant_text: str, rule_text: str) -> str:
    """The canonical form the reconciliation digest binds: the invariant text
    and the rule text, EACH space-folded per the ADR-0088 canonicalization
    (the shared `sp.space_fold`), joined by a single newline in that fixed
    order. Space-folding before joining makes the newline separator unambiguous
    — no folded field can contain one. This REUSES `sp.space_fold`; it does not
    reimplement space-folding and it is NOT the six-field governs canonical
    string."""
    return sp.space_fold(invariant_text) + "\n" + sp.space_fold(rule_text)


def reconciliation_content_digest(invariant_text: str, rule_text: str) -> str:
    """SHA-256 hex of the reconciliation canonical string — binds the reviewed
    (invariant, rule) text pair, so byte-level whitespace variance is not a
    content change (matching the ADR-0088 content_digest byte-for-byte in its
    canonicalization, over this layer's different text pair)."""
    return hashlib.sha256(
        reconciliation_canonical_string(invariant_text, rule_text).encode("utf-8")
    ).hexdigest()


# ── deterministic pairing seed (ADR-0090 req 2) ─────────────────────────────

def invariant_pairings(records: list[dict], invariants: list[dict]) -> list[dict]:
    """The ADR-0090 seed, retained verbatim: every (ratified-invariant,
    governs-handle) candidate pairing, sorted by (invariant_id, handle). The
    seed is the EXISTING structural link — no model, no full cross-product: for
    each ratified invariant carrying `related_adrs`, and each active ADR in
    that list bearing >=1 governs handle, one pairing per handle of that ADR.
    Each pairing carries the live rule text and the live invariant text, so
    the digest is computed once here. Slot B is always an authored handle:
    only ADR-sourced records enter the map, so a `related_adrs` entry naming
    an OBS-NNNN id seeds nothing (verbatim for every pre-widening record —
    all of them were ADR-sourced).
    """
    records_by_adr: dict[str, list[dict]] = {}
    for rec in records:
        if rec.get("source_kind", "adr") != "adr":
            continue
        records_by_adr.setdefault(rec["source_adr"], []).append(rec)
    pairings: list[dict] = []
    for inv in invariants:
        if inv["ratification"] != "ratified":
            continue
        for adr in inv["related_adrs"]:
            for rec in records_by_adr.get(adr, []):
                pairings.append({
                    "invariant": inv["id"],
                    "member_kind": MEMBER_KIND_INVARIANT,
                    "handle": rec["handle"],
                    "domain": rec["domain"],
                    "source_adr": rec["source_adr"],
                    "rule": rec["rule"],
                    "invariant_text": inv["invariant_text"],
                    "live_digest": reconciliation_content_digest(
                        inv["invariant_text"], rec["rule"]),
                })
    pairings.sort(key=lambda p: (p["invariant"], p["handle"]))
    return pairings


# ── the added pairing class: scope containment (ADR-0095 req 5) ─────────────

_TOKEN_STRIP = "`'\"()[]{},;:.!?*"
_EXT_RE = re.compile(r"^[^./][^/]*\.[A-Za-z0-9]{1,8}$")


def scope_path_tokens(scope: str, tree_name: str) -> list[str]:
    """The repo-relative path tokens a free-text `scope` names, deduped and
    sorted. Deterministic and model-free: substitute `tree_name` for a literal
    `<docs_dir>`; split on whitespace; strip surrounding punctuation; keep
    tokens carrying a `/` or a file extension; truncate each token at its
    first path segment containing `<` or `>`; drop absolute, `~`-prefixed, or
    `..`-carrying tokens. A scope that names no path token yields [] and
    therefore pairs with nothing — fail-closed in the safe direction (a
    missed pairing is the honest limit ADR-0090 records), never relaxed into
    shared-domain matching."""
    text = scope.replace("<docs_dir>", tree_name)
    out: set[str] = set()
    for raw in text.split():
        tok = raw.strip(_TOKEN_STRIP)
        if not tok:
            continue
        if "/" not in tok and not _EXT_RE.match(tok):
            continue
        segments = tok.split("/")
        kept: list[str] = []
        for seg in segments:
            if "<" in seg or ">" in seg:
                break
            kept.append(seg)
        tok = "/".join(kept)
        if not tok.strip("/"):
            continue
        if tok.startswith(("/", "~")) or ".." in tok.split("/"):
            continue
        # The keep test ran on the RAW token: `tools/` carries a slash and
        # survives even though its normalized form `tools` carries none.
        norm = posixpath.normpath(tok)
        if norm in (".", "") or norm.startswith(("/", "../")):
            continue
        out.add(norm)
    return sorted(out)


def scope_contains(token: str, evidence_path: str) -> bool:
    """`p == t or p.startswith(t + "/")` on POSIX-normalized strings."""
    t = posixpath.normpath(token)
    p = posixpath.normpath(evidence_path)
    return p == t or p.startswith(t + "/")


def _narrowness(token: str) -> tuple[int, int]:
    """Segment count, then string length — the tie-break order the decision
    fixes. A genuine tie pairs both rules (the admitted residual)."""
    return (posixpath.normpath(token).count("/") + 1, len(token))


def observation_pairings(records: list[dict], tree_name: str = "bionic") -> list[dict]:
    """The added class: every (authored rule, ratified observation) pairing,
    sorted by (observation handle, authored handle). All THREE conjuncts are
    required — same `domain`; an evidence path of the observation inside the
    authored rule's declared `scope`; and, among the domain's rules containing
    that path, only the NARROWEST containing scope (a tie pairs each). Matching
    runs per evidence path; the pairing set is the union over an observation's
    evidence paths. Slot A is the observation handle, slot-A text its rule;
    slot B the authored handle, slot-B text the authored rule."""
    authored_by_domain: dict[str, list[tuple[dict, list[str]]]] = {}
    for rec in records:
        if rec.get("source_kind", "adr") != "adr":
            continue
        tokens = scope_path_tokens(rec.get("scope", ""), tree_name)
        if tokens:
            authored_by_domain.setdefault(rec["domain"], []).append((rec, tokens))

    pairings: dict[tuple[str, str], dict] = {}
    for obs in records:
        if obs.get("source_kind") != "observation":
            continue
        candidates = authored_by_domain.get(obs["domain"], [])
        if not candidates:
            continue
        for evidence in obs.get("evidence", []):
            m = _EVIDENCE_RE.match(evidence)
            if not m:
                continue
            path = posixpath.normpath(m.group("path"))
            containing: list[tuple[tuple[int, int], dict]] = []
            for rec, tokens in candidates:
                best = None
                for t in tokens:
                    if scope_contains(t, path):
                        n = _narrowness(t)
                        if best is None or n > best:
                            best = n
                if best is not None:
                    containing.append((best, rec))
            if not containing:
                continue
            narrowest = max(n for n, _ in containing)
            for n, rec in containing:
                if n != narrowest:
                    continue
                key = (obs["handle"], rec["handle"])
                if key in pairings:
                    continue
                pairings[key] = {
                    "invariant": obs["handle"],
                    "member_kind": MEMBER_KIND_OBSERVATION,
                    "handle": rec["handle"],
                    "domain": rec["domain"],
                    "source_adr": rec["source_adr"],
                    "rule": rec["rule"],
                    "invariant_text": obs["rule"],
                    "live_digest": reconciliation_content_digest(obs["rule"], rec["rule"]),
                }
    return [pairings[k] for k in sorted(pairings)]


def candidate_pairings(records: list[dict], invariants: list[dict],
                       tree_name: str = "bionic") -> list[dict]:
    """Legacy union added, sorted by (slot A, handle): the ADR-0090
    ratified-invariant seed (`invariant_pairings`, retained verbatim) plus the
    ADR-0095 (authored rule, ratified observation) class
    (`observation_pairings`). With zero ratified observations the added half is
    empty and this is exactly the legacy seed.

    ADR-0097 part 6: a RETIRED rule seeds no pairing. Without the filter a
    displaced rule could still mark its whole domain BROKEN through an
    un-adjudicated pairing, which would withhold the belief of the very domain
    the retirement exists to correct."""
    live = sp.live_records(records)
    pairings = invariant_pairings(live, invariants) \
        + observation_pairings(live, tree_name)
    pairings.sort(key=lambda p: (p["invariant"], p["handle"]))
    return pairings


# ── reconciliation ledger reads ─────────────────────────────────────────────

def read_reconciliations(root: Path, *, _source_io=None) -> list[dict]:
    """Read and shape-validate `adrs/doctrine/reconciliations.yml`.

    Returns the reconciliation records in document order:
        [{invariant, member_kind, handle, verdict, content_digest, rationale, signed}]

    The key is an ordered pair of member identities (ADR-0095 requirement 5).
    Slot A, the on-disk `invariant:` field, holds an `INV-NNNN` id OR an
    `OBS-NNNN/slug` observation handle (`MEMBER_A_RE`); `member_kind` says
    which. Slot B, `handle:`, always holds an authored ADR handle. RECORDED
    BACK-COMPAT DECISION: the field is still spelled `invariant` in the file
    and in this dict even when it holds an observation handle, because
    renaming it would rewrite every signed record; every pre-widening record
    parses unchanged and keeps its identity and digest bytes.

    An absent file is an empty ledger. A present-but-malformed file raises
    DoctrineValidationError: a duplicate mapping key (the composer-node refusal
    the reviews manifest and the catalog reader share), a non-mapping root, a
    config_version other than "1", a record missing/oddly-shaped slot-A member
    or handle, a verdict outside {compatible, reconciled, collision}, a
    content_digest that is not 64 lowercase hex, a `reconciled` record without a
    non-empty rationale, a non-date `signed`, or a duplicate (invariant, handle)
    pair.
    """
    options = {} if _source_io is None else {"_source_io": _source_io}
    path = reconciliations_path(root, **options)
    if not sp._source_is_file(path, _source_io):
        return []
    return parse_reconciliations(sp._source_text(path, _source_io))


def parse_reconciliations(text: str) -> list[dict]:
    """Validate ledger text without reading sources or evaluating governing authority."""
    import yaml
    from _yaml_min import CatalogYamlError, _refuse_duplicate_keys_bounded as _refuse_catalog_duplicate_keys
    try:
        _refuse_catalog_duplicate_keys(text, yaml)
    except CatalogYamlError as exc:
        raise DoctrineValidationError([_problem(str(exc))]) from exc
    doc = yaml.safe_load(text)
    problems: list[dict] = []
    if not isinstance(doc, dict):
        raise DoctrineValidationError([_problem(
            "reconciliations.yml is not a mapping")])
    if str(doc.get("config_version")) != "1":
        problems.append(_problem(
            f"config_version must be \"1\", got {redact(doc.get('config_version'))}"))

    records: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in doc.get("reconciliations") or []:
        if not isinstance(r, dict):
            problems.append(_problem(f"reconciliation entry is not a mapping: {redact(r)}"))
            continue
        invariant = r.get("invariant")
        handle = r.get("handle")
        if not (isinstance(invariant, str) and MEMBER_A_RE.match(invariant)):
            problems.append(_problem(
                f"reconciliation invariant must be an INV-NNNN id or an "
                f"OBS-NNNN/slug observation handle, got {redact(invariant)}",
                invariant=invariant if isinstance(invariant, str) else None,
                handle=handle if isinstance(handle, str) else None))
            continue
        # Reuses summaries_projection's private `_HANDLE_ANCHOR_RE` rather
        # than reimplementing the ADR-NNNN/slug shape — same package, and a
        # reconciliation handle IS a governs handle, so the two must accept
        # the exact same strings or a live handle could fail to reconcile.
        if not (isinstance(handle, str) and sp._HANDLE_ANCHOR_RE.match(handle)):
            problems.append(_problem(
                f"reconciliation handle must be an ADR-anchored handle, got {redact(handle)}",
                invariant=invariant, handle=handle if isinstance(handle, str) else None))
            continue
        verdict = r.get("verdict")
        if verdict not in VERDICTS:
            problems.append(_problem(
                f"verdict must be one of {list(VERDICTS)}, got {redact(verdict)}",
                invariant=invariant, handle=handle))
        digest = r.get("content_digest")
        if not (isinstance(digest, str) and _DIGEST_RE.match(digest)):
            problems.append(_problem(
                f"content_digest must be 64 lowercase hex, got {redact(digest)}",
                invariant=invariant, handle=handle))
        rationale = r.get("rationale")
        rationale = rationale.strip() if isinstance(rationale, str) and rationale.strip() else None
        if verdict == "reconciled" and rationale is None:
            problems.append(_problem(
                "a reconciled verdict requires a non-empty rationale — a human "
                "asserts two apparently-conflicting texts are compatible under it",
                invariant=invariant, handle=handle))
        signed = r.get("signed")
        if signed is not None:
            import datetime
            if isinstance(signed, (datetime.date, datetime.datetime)):
                signed = (signed.date() if isinstance(signed, datetime.datetime)
                          else signed).isoformat()
            elif isinstance(signed, str) and _DATE_RE.match(signed):
                pass
            else:
                problems.append(_problem(
                    f"signed must be a YYYY-MM-DD date or null, got {redact(signed)}",
                    invariant=invariant, handle=handle))
                signed = None
        key = (invariant, handle)
        if key in seen:
            problems.append(_problem(
                f"duplicate reconciliation for ({redact(invariant, quoted=False)}, {redact(handle, quoted=False)}) — one "
                "record per pairing", invariant=invariant, handle=handle))
            continue
        seen.add(key)
        records.append({"invariant": invariant, "member_kind": member_kind(invariant),
                        "handle": handle, "verdict": verdict,
                        "content_digest": digest, "rationale": rationale,
                        "signed": signed})

    if problems:
        problems.sort(key=lambda p: (str(p.get("invariant")), str(p.get("handle")),
                                     str(p.get("problem"))))
        raise DoctrineValidationError(problems)
    return records


def reconciliations_sha256(root: Path, *, _source_io=None) -> str | None:
    """SHA-256 of the reconciliation ledger's raw bytes; None when absent."""
    options = {} if _source_io is None else {"_source_io": _source_io}
    path = reconciliations_path(root, **options)
    if not sp._source_is_file(path, _source_io):
        return None
    raw = path.read_bytes() if _source_io is None else _source_io.read_bytes(path)
    return hashlib.sha256(raw).hexdigest()


# ── per-pairing status + per-domain rollup ──────────────────────────────────

def pairing_status(pairing: dict, records_by_key: dict[tuple[str, str], dict]) -> str:
    """One pairing's status against the ledger. `un-adjudicated` when no record;
    `digest-stale` when the record's content_digest != the live digest;
    otherwise the record's verdict (compatible / reconciled / collision).
    Re-adjudication is always against the CURRENT post-state of both texts, so a
    concurrent ADR-and-invariant change leaves a pairing digest-stale until
    re-adjudicated — it cannot race."""
    rec = records_by_key.get((pairing["invariant"], pairing["handle"]))
    if rec is None:
        return STATUS_UNADJUDICATED
    if rec["content_digest"] != pairing["live_digest"]:
        return STATUS_DIGEST_STALE
    return rec["verdict"]


def domain_state(pairing_statuses: list[str], domain_records: list[dict],
                 governs_from: int | None) -> str:
    """The per-domain state, by precedence (ADR-0090 req 2 + req 4):
      1. BROKEN — any pairing un-adjudicated / digest-stale / collision.
      2. believed — >=1 pairing, all compatible or reconciled.
      3. backfilled — no pairing, every rule admitted via the historic backfill
         (ADR number below governs_from).
      4. no-applicable-invariant — no pairing, at least one prospective rule.
    Blocking is per-domain, never whole-tree.
    """
    if any(s not in _UNBLOCKING for s in pairing_statuses):
        return STATE_BROKEN
    if pairing_statuses:
        return STATE_BELIEVED
    # The backfill test runs over ADR-sourced records ONLY. An observation's
    # host number is its OBS number (`OBS-0001` -> 1), which is below any
    # `governs_from`, so including observations would render a brand-new
    # observed domain as "admitted via the historic backfill". `build_resolver`
    # guards the same hazard by omitting `review_state` on observation rows.
    # A domain with no ADR-sourced rule is therefore never `backfilled`.
    adr_records = [r for r in domain_records if r.get("source_kind", "adr") == "adr"]
    if governs_from is not None and adr_records \
            and all(r["adr_num"] < governs_from for r in adr_records):
        return STATE_BACKFILLED
    return STATE_NO_INVARIANT


def evidence_resolves(evidence: str, repo_root: Path | None, *, _source_io=None) -> bool:
    """Whether one `path:line-range` evidence string resolves INSIDE the repo
    root: the grammar matches, the path is contained, and it names a file.
    The line range is not checked against the file's length — CHK-OBS-EVIDENCE
    (the concern's own audit rule) resolves the path the same way, through the
    same helper. No root, no resolution.

    Containment is decided on the RESOLVED path, not the spelling. This
    function used to stop at the textual refusal and then call `is_file()`,
    which follows symlinks — so a committed `link -> /etc` made
    `link/passwd:1-1` render `resolves: yes`. `observation_evidence` holds the
    one correct implementation; this is one of its two call sites."""
    if _source_io is None:
        return oe.evidence_entry_resolves(repo_root, evidence)
    return oe.evidence_entry_resolves(repo_root, evidence, _source_io=_source_io)


def domain_authority(domain_records: list[dict]) -> str | None:
    """`descriptive` when every rule in the domain is observation-sourced,
    `mixed` when it holds both kinds, None when it holds no observation rule
    (the pre-widening rendering, emitted verbatim)."""
    kinds = {r.get("source_kind", "adr") for r in domain_records}
    if "observation" not in kinds:
        return None
    return "descriptive" if kinds == {"observation"} else "mixed"


def build_domain_entries(records: list[dict], invariants: list[dict],
                         reconciliations: list[dict], bindings: dict,
                         governs_from: int | None, *, tree_name: str = "bionic",
                         repo_root: Path | None = None) -> list[dict]:
    """Preserve the legacy interface; measure files before the pure core."""
    evidence = {(row["handle"], ev): evidence_resolves(ev, repo_root)
                for row in sp.live_records(records) if row.get("source_kind") == "observation"
                for ev in row.get("evidence", [])}
    return _build_domain_entries_loaded(records, invariants, reconciliations, bindings,
        governs_from, tree_name=tree_name, evidence_facts=evidence)


def _build_domain_entries_loaded(records: list[dict], invariants: list[dict],
                                 reconciliations: list[dict], bindings: dict,
                                 governs_from: int | None, *, tree_name: str,
                                 evidence_facts: dict) -> list[dict]:
    """One entry per governs domain, sorted by domain name. Each entry carries
    its rules (sorted by `sp.record_sort_key`) with disposition + basis, its
    candidate pairings with per-pairing status, its provenance
    (source handles, host record ids, slot-A member ids), its state, and —
    the observation widening — its `authority` (None / descriptive / mixed)
    and its `evidence` rows (one per observation handle x evidence path, with
    whether the path resolves under `repo_root`).

    ADR-0097 part 7: the column is `basis`, one name over one concept, and the
    value says WHICH FACT WAS MEASURED. The two computations behind it are
    unchanged — an ADR-sourced rule keeps the run-binding derivation
    (`run-bound` / `not-run-bound`) and an observation-sourced rule keeps
    evidence-path resolution (`evidence-resolves` when the record names at
    least one path and every one resolves, else `evidence-missing`). The rename
    is what stops one name covering two computations, and neither value is
    evidence that the rule holds."""
    # ADR-0097 part 6: a retired rule leaves the doctrine index with the same
    # filter every other artifact applies. Applied once here, before the
    # pairing seed and the per-domain grouping, so no downstream branch can
    # reintroduce it.
    records = sp.live_records(records)
    pairings = candidate_pairings(records, invariants, tree_name)
    records_by_key = {(r["invariant"], r["handle"]): r for r in reconciliations}
    pairings_by_domain: dict[str, list[dict]] = {}
    for p in pairings:
        p = dict(p)
        p["status"] = pairing_status(p, records_by_key)
        rec = records_by_key.get((p["invariant"], p["handle"]))
        p["rationale"] = rec["rationale"] if rec else None
        pairings_by_domain.setdefault(p["domain"], []).append(p)

    records_by_domain: dict[str, list[dict]] = {}
    for rec in records:
        records_by_domain.setdefault(rec["domain"], []).append(rec)

    entries: list[dict] = []
    for domain in sorted(records_by_domain):
        # `sp.record_sort_key` is (source_kind != "adr", adr_num, handle): with
        # zero observations its leading element is constant, so ADR ordering
        # is exactly the pre-widening (adr_num, handle).
        drecs = sorted(records_by_domain[domain], key=sp.record_sort_key)
        dpairings = sorted(pairings_by_domain.get(domain, []),
                           key=lambda p: (p["invariant"], p["handle"]))
        rules = []
        evidence_rows = []
        for rec in drecs:
            if rec.get("source_kind", "adr") == "observation":
                resolved = [(ev, evidence_facts[(rec["handle"], ev)])
                            for ev in rec.get("evidence", [])]
                basis = (BASIS_EVIDENCE_RESOLVES
                         if resolved and all(ok for _, ok in resolved)
                         else BASIS_EVIDENCE_MISSING)
                for ev, ok in resolved:
                    evidence_rows.append({"handle": rec["handle"], "evidence": ev,
                                          "resolves": ok})
            else:
                slot = bindings.get(rec["source_adr"])
                basis = (BASIS_RUN_BOUND if slot and slot["runs"]
                         else BASIS_NOT_RUN_BOUND)
            rules.append({
                "handle": rec["handle"],
                "rule": rec["rule"],
                "source_adr": rec["source_adr"],
                # disposition alone conveys decided-vs-observed per rule; the
                # per-DOMAIN `authority` below is the widening's rendering,
                # emitted only when an observation rule is present.
                "disposition": sp.disposition_for(rec["provenance"]),
                # The source record's lifecycle status, verbatim. Rendered
                # beside `disposition` rather than folded into it: a Proposed
                # ADR's rule resolves and is still not adopted, and those are
                # two different facts.
                "source_status": rec.get("source_status"),
                "basis": basis,
                "source_kind": rec.get("source_kind", "adr"),
            })
        evidence_rows.sort(key=lambda e: (e["handle"], e["evidence"]))
        state = domain_state([p["status"] for p in dpairings], drecs, governs_from)
        entries.append({
            "domain": domain,
            "state": state,
            "authority": domain_authority(drecs),
            "rules": rules,
            "evidence": evidence_rows,
            "pairings": dpairings,
            "source_handles": sorted(r["handle"] for r in drecs),
            "source_adrs": sorted({r["source_adr"] for r in drecs},
                                  key=lambda s: (not s.startswith("ADR-"), sp.adr_num(s))),
            "invariant_ids": sorted({p["invariant"] for p in dpairings}),
        })
    return entries


def broken_domains(entries: list[dict]) -> list[dict]:
    """Every domain whose state is BROKEN, with its offending pairings — the
    consistency gate's finding surface."""
    out: list[dict] = []
    for e in entries:
        if e["state"] != STATE_BROKEN:
            continue
        offending = [{"invariant": p["invariant"], "handle": p["handle"],
                      "status": p["status"]}
                     for p in e["pairings"] if p["status"] not in _UNBLOCKING]
        out.append({"domain": e["domain"], "pairings": offending})
    return out


# ── render builders (byte-stable, model-free) ───────────────────────────────

def _md_escape(value: str) -> str:
    """One value, ready for a Markdown table cell.

    Two layers, and they are not interchangeable. `redact` is the BOUND and
    the REDACTION — it is the shared mechanism, and it is what stops a rule
    text carrying `\\r` or an ANSI escape from rewriting the reader's
    terminal, and a 10 MB one from becoming a 10 MB row. The pipe escape is
    the CHANNEL's own rule, which no other channel wants; it composes on top
    rather than living inside the helper.

    The bound is `MESSAGE_LIMIT`, not `LIMIT`: a cell holds a rule sentence,
    which is legitimately longer than any id or handle, so bounding it at the
    value bound would truncate the artifact rather than a message."""
    folded = str(value).replace("\n", " ")
    return redact(folded, quoted=False,
                  limit=MESSAGE_LIMIT).replace("|", "\\|").strip()


def build_index(entries: list[dict], exempt_entries: list[dict],
                digests: dict) -> str:
    """The doctrine index — one section per governs domain, an exempt-ADR
    roster, and a provenance block carrying the input digests. Templated prose,
    no model, no timestamp: byte-stable across runs on unchanged inputs."""
    out = [
        "# doctrine — current beliefs, per governs domain",
        "",
        "_The per-domain view of what the project currently "
        "holds to be true, compiled deterministically from the summaries "
        "projection and reconciled against the ratified invariants. Doctrine is "
        "the primary read surface for a current-belief question; it holds zero "
        "authority, and the ADR body is the record on any disagreement. "
        "Regenerated; edits are overwritten._",
        "",
        "_State legend: **believed** (every applicable invariant reconciles) · "
        "**backfilled** (admitted via the historic backfill, no applicable "
        "invariant) · **no-applicable-invariant** (a prospective rule no "
        "ratified invariant references) · **BROKEN** (an un-adjudicated, "
        "digest-stale, or collision pairing — the belief is withheld until a "
        "human reconciles)._",
        "",
        "_Basis legend — the mechanical fact measured behind each rule: "
        "**run-bound** (a run snapshot's artifacts reference this rule's "
        "source ADR) · **not-run-bound** (no run snapshot does) · "
        "**evidence-resolves** (the observation record names at least one "
        "evidence path and every one resolves on disk) · **evidence-missing** "
        "(a named evidence path does not resolve, or the record names none). "
        "No basis value is evidence that the rule holds: each says which fact "
        "was measured, and none of them measures the rule._",
        "",
    ]
    for e in entries:
        # The heading suffix, the evidence table, and the evidence-derived
        # basis are the observation widening's three renderings;
        # each appears ONLY when the domain holds an observation rule, so a
        # tree with none renders byte-identically to the pre-widening index.
        # The header prose, the state legend, and the rules-table columns
        # (including the `source ADR` header, which now shows an OBS-NNNN id
        # for an observation-sourced rule) are unchanged for the same reason.
        # The heading is ESCAPED like every cell below it. A `domain` is record
        # content, and `survey.parse_doctrine_entries` reads these headings back
        # to derive the descriptive-only postcondition — so an unescaped newline
        # here injects a whole forged section, swallows the real one, and
        # returns a clean verdict on a broken tree.
        suffix = f" · authority: {redact(e['authority'], quoted=False)}" if e.get("authority") else ""
        out.append(f"## {_md_escape(e['domain'])} — {e['state']}{suffix}")
        out.append("")
        # The citation sits beside the handle (ADR-0099 clause 2). The handle
        # is the ledger's name for the rule and carries the ADR number; the
        # citation is the reader's name for it and carries none. The slug is
        # `sp.slug_of` — the ONE split the summaries projection, this index,
        # and the survey sign-off share — so a citation rendered here NAMES the
        # right rule by construction. The cell is escaped like every other: a
        # slug that passed the handle grammar carries no pipe, but this
        # renderer does not validate and must not be the layer that opens a
        # column on one that did not.
        #
        # What that guarantee does NOT cover, stated rather than implied:
        # naming the rule is not the same as resolving it. The citation linter
        # withholds a rule whose signed backfill receipt no longer covers its
        # live text (ADR-0088 clause 6), and `build_domain_entries` cannot see
        # that facet — deriving it needs the reviews manifest, which is absent
        # from this compile's declared input domain (`input_digests`, schema
        # "3"). So a rule that went unreviewed still renders its citation here,
        # and a reader who copies that citation into a linted file gets a
        # `reason: unreviewed` finding pointing at the rule, which is the
        # correct diagnosis of the correct problem. Widening the input domain
        # to suppress the cell is an ADR-level change, not a renderer change.
        # Nothing here can produce a citation the gate accepts for a rule the
        # gate rejects, which is the direction that would matter.
        out.append("| handle | citation | rule | source ADR | source_status | disposition | basis |")
        out.append("|--------|----------|------|------------|---------------|-------------|-------|")
        for r in e["rules"]:
            out.append(
                f"| {_md_escape(r['handle'])} "
                f"| {_md_escape('rule:' + sp.slug_of(r['handle']))} "
                f"| {_md_escape(r['rule'])} "
                f"| {_md_escape(r['source_adr'])} "
                f"| {_md_escape(r.get('source_status') or 'unknown')} "
                f"| {_md_escape(r['disposition'])} "
                f"| {_md_escape(r['basis'])} |")
        out.append("")
        if e.get("evidence"):
            out.append("_Observed evidence:_")
            out.append("")
            out.append("| observation | evidence | resolves |")
            out.append("|-------------|----------|----------|")
            for ev in e["evidence"]:
                out.append(
                    f"| {_md_escape(ev['handle'])} | {_md_escape(ev['evidence'])} "
                    f"| {'yes' if ev['resolves'] else 'no'} |")
            out.append("")
        if e["pairings"]:
            out.append("_Reconciliation against ratified invariants:_")
            out.append("")
            out.append("| invariant | handle | status |")
            out.append("|-----------|--------|--------|")
            for p in e["pairings"]:
                out.append(
                    f"| {_md_escape(p['invariant'])} | {_md_escape(p['handle'])} "
                    f"| {_md_escape(p['status'])} |")
            out.append("")
        if e["state"] == STATE_BROKEN:
            out.append("> **BROKEN** — this domain withholds its belief. A "
                       "structurally-declared pairing is un-adjudicated, "
                       "digest-stale, or a collision. Reconcile by revising the "
                       "deciding ADR, transitioning the invariant, or recording "
                       "a digest-bound reconciliation.")
            out.append("")

    out.append(f"## Exempt ADRs ({len(exempt_entries)})")
    out.append("")
    if exempt_entries:
        out.append("_Cohort ADRs excused from carrying a governs block, with the "
                   "recorded reason (ADR-0090 exempt reason grammar)._")
        out.append("")
        for ex in exempt_entries:
            reason = ex["reason"] if ex["reason"] else "(no reason recorded)"
            out.append(f"- {_md_escape(ex['adr'])} — {_md_escape(reason)}")
    else:
        out.append("_None._")
    out.append("")

    out.append("## Provenance")
    out.append("")
    out.append("_Freshness is the deterministic input digests below; no "
               "wall-clock timestamp enters this file._")
    out.append("")
    for key in sorted(digests):
        out.append(f"- `{key}`: `{redact(digests[key], quoted=False)}`")
    return "\n".join(out) + "\n"


def build_meta(digests: dict) -> str:
    """The `_meta.json` input-digest + schema block, sorted, byte-stable."""
    import json
    return json.dumps(digests, sort_keys=True, indent=2) + "\n"


def input_digests(root: Path, records: list[dict], invariants: list[dict],
                  governs_from: int | None, observations: Path | None = None, *,
                  _source_io=None) -> dict:
    """The deterministic input-digest map stamped into both the index and
    `_meta.json`. Covers ADR frontmatter (the rule source), the invariant
    ledger facets doctrine reads, the reconciliation ledger bytes, the
    observation corpus the compile reads since schema "2" (ADR-0095
    requirement 5), and — since schema "3" (ADR-0098 clause 2) — the per-batch
    survey receipts (`sp.observations_sha256`; the empty-corpus digest on an enabled
    concern with no records, null when the concern is not read). Every input
    the compile reads, and nothing that varies per run."""
    options = {} if _source_io is None else {"_source_io": _source_io}
    adrs = sp.adrs_dir(root, **options)
    return {
        "adr_frontmatter_sha256": sp.adr_frontmatter_sha256(adrs, **options),
        "invariants_sha256": invariants_sha256(invariants),
        "reconciliations_sha256": reconciliations_sha256(root, **options),
        "observations_sha256": (sp.observations_sha256(observations, **options)
                                if observations is not None else None),
        # ADR-0098 clause 2: the per-batch survey receipts join the input
        # domain the doctrine compiles from, exactly as they join the
        # summaries one. Null when the tree has signed no batch.
        "survey_receipts_sha256": (sp.survey_receipts_sha256(observations, **options)
                                   if observations is not None else None),
        "governs_from": governs_from,
        # Bumped 3 -> 4 when the rule table gained its `source_status` column.
        # The callers overwrite this literal with the published schema "5", which
        # added the run-bindings digest and the implementation-migration input domain.
        # Same rule as the summaries projection's literal: a declared schema that
        # identifies output shape bumps on an additive change, because nothing
        # else tells a reader which contract the file was written against.
        "schema": "4",
        "tool": "compile-doctrine.py",
    }


def _doctrine_source_models(data: dict) -> None:
    """Validate every authority-bearing input before compiling output parts."""
    sp._plain_summary_value(data)
    rows = data["records"]
    sp._projection_require(isinstance(rows, list), "doctrine records must be a list")
    for row in rows:
        sp._projection_require(isinstance(row, dict) and all(isinstance(row.get(k), str)
            for k in ("handle", "source_adr", "domain", "rule", "scope", "provenance"))
            and type(row.get("adr_num")) is int and row["adr_num"] == sp.adr_num(row["source_adr"])
            and row["handle"].startswith(row["source_adr"] + "/") and "historical" not in row
            and (sp._HANDLE_ANCHOR_RE.fullmatch(row["handle"]) or sp._OBS_HANDLE_ANCHOR_RE.fullmatch(row["handle"]))
            and row["provenance"] in sp.PROVENANCE_ENUM
            and row.get("source_kind", "adr") in ("adr", "observation")
            and (row.get("source_status") is None or isinstance(row["source_status"], str))
            and isinstance(row.get("retires", []), list) and all(isinstance(h, str)
                and sp._HANDLE_ANCHOR_RE.fullmatch(h) for h in row.get("retires", []))
            and isinstance(row.get("evidence", []), list) and all(isinstance(ev, str)
                for ev in row.get("evidence", [])), "doctrine source model invalid")
    sp._projection_require(isinstance(data["invariants"], list) and len({i["id"] for i in data["invariants"]})
        == len(data["invariants"]), "doctrine duplicate invariant")
    for inv in data["invariants"]:
        sp._projection_require(isinstance(inv, dict) and set(inv) == {
            "id", "ratification", "related_adrs", "invariant_text", "path"}
            and isinstance(inv["id"], str) and _INV_ID_RE.fullmatch(inv["id"])
            and isinstance(inv["ratification"], str) and isinstance(inv["invariant_text"], str)
            and isinstance(inv["related_adrs"], list) and all(isinstance(a, str) for a in inv["related_adrs"])
            and (inv["path"] is None or isinstance(inv["path"], str)), "doctrine invariant model invalid")
    import yaml
    sp._projection_require(isinstance(data["reconciliations"], list), "doctrine ledger model invalid")
    parse_reconciliations(yaml.safe_dump(dict(config_version="1", reconciliations=data["reconciliations"])))
    ledger = data["ledger"]
    sp._projection_require(isinstance(ledger, dict) and set(ledger) == {"path", "text"}
        and sp._summary_relative_path(ledger["path"]) and Path(ledger["path"]).parts[-3:] ==
        ("adrs", "doctrine", RECONCILIATIONS_FILENAME) and
        (ledger["text"] is None or isinstance(ledger["text"], str)), "doctrine raw ledger model invalid")
    sp._projection_require(data["reconciliations"] ==
        (parse_reconciliations(ledger["text"]) if ledger["text"] is not None else []),
        "doctrine raw and normalized ledger disagree")
    bindings = data["bindings"]
    sp._projection_require(isinstance(bindings, dict), "doctrine bindings model invalid")
    for source, binding in bindings.items():
        sp._projection_require(isinstance(source, str) and re.fullmatch(r"(?:[A-Z][A-Z0-9]{1,9}-)?ADR-\d{4}", source)
            and isinstance(binding, dict) and set(binding) == {"books", "runs"}
            and all(isinstance(binding[k], list) and all(isinstance(s, str) for s in binding[k])
                    for k in ("books", "runs")), "doctrine binding fields invalid")
    sp._projection_require(isinstance(data["tree_name"], str) and isinstance(data["aliases"], dict)
        and isinstance(data["removed"], dict) and isinstance(data["evidence_facts"], list)
        and isinstance(data["exempt_entries"], list)
        and (data["governs_from"] is None or type(data["governs_from"]) is int and data["governs_from"] >= 0),
        "doctrine loaded fields invalid")
    for ex in data["exempt_entries"]:
        sp._projection_require(isinstance(ex, dict) and set(ex) == {"adr", "reason"}
            and isinstance(ex["adr"], str) and (ex["reason"] is None or isinstance(ex["reason"], str)), "doctrine exemption invalid")
    for handle, alias in data["aliases"].items():
        sp._projection_require(isinstance(alias, dict) and set(alias) == {"alias_of", "alias_handles"}
            and sp._OBS_HANDLE_ANCHOR_RE.fullmatch(handle) and isinstance(alias["alias_of"], str)
            and isinstance(alias["alias_handles"], list) and all(h in {r["handle"] for r in rows}
                and h.startswith(alias["alias_of"] + "/") for h in alias["alias_handles"]),
            "doctrine alias model invalid")
    sp._projection_require(isinstance(data["archived_handles"], list) and all(isinstance(h, str)
        and sp._HANDLE_ANCHOR_RE.fullmatch(h) for h in data["archived_handles"]), "doctrine archived handles invalid")
    signed = {row["handle"] for row in data["reconciliations"] if row["signed"] is not None}
    problems = sp.retirement_problems(rows, data["removed"], signed, set(data["archived_handles"]))
    if problems: raise sp.GovernsValidationError(problems)


def _doctrine_migration_frame(facts: dict, pending: dict | None, overlay: dict,
                            history: list) -> dict | None:
    """Bind selected references and raw dependencies; defer provenance alone."""
    sp._plain_summary_value(facts); sp._plain_summary_value(history)
    fields = {"historical_handles", "historical_displacements", "retired_handles", "reserved_slugs",
              "retained_handles", "source_refs", "dependency_fingerprints"}
    sp._projection_require(fields <= set(facts) and isinstance(facts["historical_handles"], dict)
        and all(isinstance(facts[k], list) for k in fields - {"historical_handles"}),
        "doctrine migration facts invalid")
    dependencies = {}
    for ref in facts["dependency_fingerprints"]:
        sp._projection_require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}
            and sp._summary_relative_path(ref["path"]) and isinstance(ref["sha256"], str)
            and sp._DIGEST_RE.fullmatch(ref["sha256"]), "doctrine dependency invalid")
        sp._projection_require(ref["path"] not in dependencies or dependencies[ref["path"]] == ref["sha256"],
                              "doctrine conflicting dependencies")
        dependencies[ref["path"]] = ref["sha256"]
    selected = {}
    for ref in facts["source_refs"]:
        sp._projection_require(isinstance(ref, dict) and set(ref) == {
            "path", "source_adr", "tier", "status", "source_identity", "clause_sha256"}
            and sp._summary_relative_path(ref["path"]) and isinstance(ref["source_adr"], str)
            and sp._ADR_ID_SHAPE_RE.fullmatch(ref["source_adr"]) and ref["tier"] in ("active", "archive")
            and (ref["status"] is None or isinstance(ref["status"], str)) and all(isinstance(ref[k], str)
                and sp._DIGEST_RE.fullmatch(ref[k]) for k in ("source_identity", "clause_sha256")),
            "doctrine selected source invalid")
        sp._projection_require(ref["source_identity"] not in selected, "doctrine duplicate selected identity")
        selected[ref["source_identity"]] = ref
    for fact in facts["historical_handles"].values():
        source = selected.get(fact["source_identity"])
        sp._projection_require(source is not None and fact["destination"] == {
            k: source[k] for k in ("source_adr", "clause_sha256")}, "doctrine historical source mismatch")
    for ref in history:
        sp._projection_require(isinstance(ref, dict) and set(ref) == {"path", "sha256", "publication_commit"}
            and sp._summary_relative_path(ref["path"]) and isinstance(ref["sha256"], str)
            and sp._DIGEST_RE.fullmatch(ref["sha256"]) and isinstance(ref["publication_commit"], str)
            and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", ref["publication_commit"]),
            "doctrine publication history invalid")
    if pending is None:
        sp._projection_require(not selected and not dependencies and not history and
            not facts["historical_handles"] and not facts["historical_displacements"],
            "doctrine migration facts lack publication provenance")
        return None
    sp._summary_pending_publication(pending)
    batch = pending["close_identity"]["batch"]
    sp._projection_require(dependencies.get(batch["path"]) == batch["sha256"], "doctrine batch snapshot missing")
    publication = dict(path=pending["path"], sha256=pending["sha256"], publication_commit=sp._PublicationCommitSlot())
    for group in overlay["historical_displacements"].values():
        for edge in group:
            source = selected.get(edge["source_identity"])
            sp._projection_require(source is not None and edge["source_ref"] == {
                k: source[k] for k in ("path", "source_adr", "clause_sha256")} and
                edge["source_displacer"] in set(facts["historical_handles"]) | set(facts["retained_handles"]),
                "doctrine displacement source mismatch")
            if "publication_ref" in edge: sp._committed_publication_ref(pending, edge["publication_ref"])
            edge["publication_ref"] = publication
    return dict(publication=publication, close_identity=pending["close_identity"], publication_history=history,
        source_refs=sorted(selected.values(), key=lambda s: s["source_identity"]),
        dependency_fingerprints=[dict(path=p, sha256=s) for p, s in sorted(dependencies.items())],
        historical_displacements=overlay["historical_displacements"], historical_slugs=overlay["historical_slugs"])


class _DoctrineOutputPlan(tuple):
    """Immutable compiled parts; every policy decision precedes materialization."""
    __slots__ = ()


def _plan_doctrine_outputs(loaded_inputs: dict, migration_facts: dict,
                           pending_publication: dict | None) -> _DoctrineOutputPlan:
    expected = {"tree", "paths", "records", "invariants", "reconciliations", "bindings", "governs_from",
        "tree_name", "evidence_facts", "exempt_entries", "digests", "aliases", "removed", "publication_history", "ledger", "archived_handles"}
    sp._projection_require(isinstance(loaded_inputs, dict) and set(loaded_inputs) == expected,
                          "doctrine loaded shape invalid")
    data = {k: v for k, v in loaded_inputs.items() if k not in ("tree", "paths")}
    _doctrine_source_models(data)
    tree, paths = loaded_inputs["tree"], loaded_inputs["paths"]
    sp._plain_summary_value(str(tree))
    sp._projection_require(isinstance(tree, Path) and tree.is_absolute() and isinstance(paths, dict)
        and set(paths) == {"index.md", "_meta.json"} and all(isinstance(p, Path) and p.is_absolute()
            and p == tree / "adrs/doctrine" / name and ".." not in p.parts for name, p in paths.items()),
        "doctrine destinations invalid")
    for path in paths.values(): sp._plain_summary_value(str(path))
    sp._plain_summary_value(migration_facts)
    fields = {"historical_handles", "historical_displacements", "retired_handles", "reserved_slugs",
              "retained_handles", "source_refs", "dependency_fingerprints"}
    sp._projection_require(isinstance(migration_facts, dict) and fields <= set(migration_facts)
        and isinstance(migration_facts["historical_handles"], dict)
        and all(isinstance(migration_facts[k], list) for k in fields - {"historical_handles"})
        and all(isinstance(h, str) and sp._HANDLE_ANCHOR_RE.fullmatch(h)
            for h in migration_facts["retired_handles"] + migration_facts["retained_handles"])
        and all(isinstance(s, str) for s in migration_facts["reserved_slugs"]), "doctrine migration fields invalid")
    overlay = sp._summary_authority_records(data["records"], migration_facts, data["removed"], data["aliases"])
    signed = {row["handle"] for row in data["reconciliations"] if row["signed"] is not None}
    sp._projection_require(not signed.intersection(edge["target_handle"] for edge in migration_facts["historical_displacements"]),
                          "doctrine displacement would orphan a signed pairing")
    evidence = {}
    for ev in data["evidence_facts"]:
        sp._projection_require(isinstance(ev, dict) and set(ev) == {"handle", "evidence", "resolves"}
            and isinstance(ev["handle"], str) and isinstance(ev["evidence"], str) and type(ev["resolves"]) is bool
            and (ev["handle"], ev["evidence"]) not in evidence, "doctrine evidence fact invalid")
        evidence[ev["handle"], ev["evidence"]] = ev["resolves"]
    needed = {(r["handle"], ev) for r in sp.live_records(overlay["records"])
        if r.get("source_kind") == "observation" for ev in r.get("evidence", [])}
    sp._projection_require(set(evidence) == needed, "doctrine evidence snapshot incomplete")
    entries = _build_domain_entries_loaded(overlay["records"], data["invariants"], data["reconciliations"],
        data["bindings"], data["governs_from"], tree_name=data["tree_name"], evidence_facts=evidence)
    digests = dict(data["digests"])
    sp._projection_require(set(digests) == {"adr_frontmatter_sha256", "invariants_sha256", "reconciliations_sha256",
        "observations_sha256", "survey_receipts_sha256", "governs_from", "schema", "tool", "run_bindings_sha256",
        "input_domain"} and digests["schema"] == "5" and digests["tool"] == "compile-doctrine.py"
        and digests["governs_from"] == data["governs_from"] and isinstance(digests["input_domain"], list)
        and len(set(digests["input_domain"])) == len(digests["input_domain"])
        and set(digests["input_domain"]) <= {"adrs", "invariants", "reconciliations", "run-bindings", "observations", "survey-receipts"},
        "doctrine digest model invalid")
    for key, value in digests.items():
        if key.endswith("_sha256"):
            sp._projection_require(value is None and key in {"observations_sha256", "survey_receipts_sha256", "reconciliations_sha256"} or
                isinstance(value, str) and sp._DIGEST_RE.fullmatch(value), "doctrine input digest invalid")
    sp._projection_require(digests["invariants_sha256"] == invariants_sha256(data["invariants"])
        and digests["run_bindings_sha256"] == hashlib.sha256(json.dumps(data["bindings"], sort_keys=True,
            separators=(",", ":")).encode()).hexdigest(), "doctrine loaded input frame mismatch")
    ledger_digest = hashlib.sha256(data["ledger"]["text"].encode()).hexdigest() if data["ledger"]["text"] is not None else None
    sp._projection_require(digests["reconciliations_sha256"] == ledger_digest and
        all(ref["sha256"] == ledger_digest for ref in migration_facts["dependency_fingerprints"]
            if ref["path"] == data["ledger"]["path"]), "doctrine bound raw ledger frame mismatch")
    frame = _doctrine_migration_frame(migration_facts, pending_publication, overlay, data["publication_history"])
    index_parts = (build_index(entries, data["exempt_entries"], digests),)
    if frame is not None:
        fingerprint = sp._SummaryHashSlot(sp._compile_summary_json(frame, pretty=False))
        digests.update(migration=frame, migration_inputs_sha256=fingerprint,
            input_domain=sorted(set(digests["input_domain"]) | {"implementation-migration"}))
        index_parts += ("\n- `migration_inputs_sha256`: `", fingerprint, "`\n")
    parts = ((paths["index.md"], index_parts),
             (paths["_meta.json"], sp._compile_summary_json(digests) + ("\n",)))
    for _, chunks in parts:
        for chunk in chunks:
            if isinstance(chunk, str): chunk.encode("utf-8")
    return _DoctrineOutputPlan(parts)


def _materialize_doctrine_outputs(plan: _DoctrineOutputPlan,
                                  committed_publication_ref: sp._CommittedPublicationRef | None) -> dict[Path, bytes]:
    commit = committed_publication_ref.commit if committed_publication_ref is not None else None
    return {path: sp._render_summary_parts(parts, commit).encode("utf-8") for path, parts in plan}


def _load_doctrine_inputs(root: Path, manifest: dict) -> tuple[dict, dict, dict | None, dict | None]:
    """Obtain fresh public proof, then preload all evidence and source facts."""
    import implementation_migration as migration
    root = Path(root).absolute()
    view = migration.authority_view(root)
    declared = sp.declared_input_domain(doctrine_dir(root))
    sp._projection_require(declared is None or isinstance(declared, list) and
        set(declared) <= {"adrs", "invariants", "reconciliations", "run-bindings", "observations", "survey-receipts", "implementation-migration"},
        "doctrine declared input domain invalid")
    sp._projection_require(not declared or "implementation-migration" not in declared or view["state"] == "published",
                          "doctrine declared migration input unavailable")
    sp._projection_require(not declared or "observations" not in declared or
        sp.observations_source_problem(root, sp.read_manifest(root)) is None,
        "doctrine declared observations input unavailable")
    adrs = sp.adrs_dir(root); tree = sp.resolve_tree(root)
    concerns = manifest.get("concerns_enabled") or []
    if not adrs.is_dir() and not ("adrs" not in concerns and "observations" in concerns):
        raise FileNotFoundError(f"{adrs} not found")
    gf = sp.governs_from(manifest); observations = sp.observations_source(root, manifest)
    records = sp.collect_records(adrs, governs_from=gf, observations=observations)
    reviews = sp.read_reviews(adrs); removed = sp.removed_handles(reviews)
    aliases = sp.collect_observation_alias_rows(observations, records)
    overlay = sp._summary_authority_records(records, view, removed, aliases)
    invariants = read_invariants(root)
    ledger_path = reconciliations_path(root)
    ledger_text = ledger_path.read_bytes().decode("utf-8") if ledger_path.is_file() else None
    reconciliations = parse_reconciliations(ledger_text) if ledger_text is not None else []
    bindings = {source: {key: sorted(value) for key, value in binding.items()} for source, binding
                in sp.read_run_bindings(sp.runs_dir(root), repo_root=root).items()}
    evidence = [dict(handle=row["handle"], evidence=ev, resolves=evidence_resolves(ev, root))
        for row in sp.live_records(overlay["records"]) if row.get("source_kind") == "observation"
        for ev in sorted(set(row.get("evidence", [])))]
    digests = input_digests(root, records, invariants, gf, observations)
    digests.update(schema="5", input_domain=["adrs", "invariants", "reconciliations", "run-bindings"] +
        (["observations", "survey-receipts"] if observations is not None else []),
        run_bindings_sha256=hashlib.sha256(json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    loaded = dict(tree=tree, paths={name: tree / "adrs/doctrine" / name for name in ("index.md", "_meta.json")},
        records=records, invariants=[{**inv, "path": str(inv["path"]) if inv["path"] is not None else None} for inv in invariants],
        reconciliations=reconciliations, bindings=bindings, governs_from=gf, tree_name=sp.tree_name(root),
        evidence_facts=evidence, exempt_entries=sp.governs_exempt_entries(manifest), digests=digests,
        aliases=aliases, removed=removed, ledger=dict(path=ledger_path.relative_to(root).as_posix(), text=ledger_text),
        archived_handles=sorted(sp.archived_handles(adrs)),
        publication_history=[{k: row[k] for k in ("path", "sha256", "publication_commit")}
            for row in view["publications"][:-1]])
    pending = actual = None
    if view["state"] == "published":
        # Reuse the accepted exact batch/selected-clause loader; no proof parser fork.
        spec = importlib.util.spec_from_file_location("_doctrine_summary_inputs", Path(__file__).with_name("summarize-adrs.py"))
        driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
        _, locator = driver._historical_summary_clauses(root, view)
        actual = {k: view["publications"][-1][k] for k in ("path", "sha256", "publication_commit")}
        pending = dict(path=actual["path"], sha256=actual["sha256"], close_identity={k: locator[k] for k in
            ("batch", "book_id", "run_id", "book_content_hash", "slot", "gate_prompt")})
        migration._unchanged_inputs(root, view["dependency_fingerprints"] +
            [dict(path=actual["path"], sha256=actual["sha256"])])
    return loaded, view, pending, actual


def _doctrine_destinations(root: Path, *, _source_io=None) -> None:
    """Read-only preflight of both outputs before the first mutation."""
    options = {} if _source_io is None else {"_source_io": _source_io}
    tree = sp.resolve_tree(root, **options)
    for name in ("index.md", "_meta.json"):
        path = tree / "adrs/doctrine" / name
        if sp._source_resolve(tree, _source_io) not in sp._source_resolve(path.parent, _source_io).parents:
            raise OSError("doctrine destination escapes the validated tree")
        if _source_io is not None:
            entry = _source_io.metadata(path)
            temporary = _source_io.metadata(path.with_suffix(path.suffix + ".tmp"))
            if (entry is not None and not stat.S_ISREG(entry["st_mode"])) or temporary is not None:
                raise OSError("doctrine destination is symlinked or has an owned temporary path")
            continue
        if path.is_symlink() or path.exists() and not path.is_file() or \
                path.with_suffix(path.suffix + ".tmp").exists() or path.with_suffix(path.suffix + ".tmp").is_symlink():
            raise OSError("doctrine destination is symlinked or has an owned temporary path")


def _bound_reconciliation_write_refusal(root: Path, proposed_text: str) -> str | None:
    """Compare proposed raw ledger bytes to fresh binding before mkdir or write."""
    import implementation_migration as migration
    view = migration.authority_view(root)
    relative = reconciliations_path(root).relative_to(root).as_posix()
    bound = {ref["sha256"] for ref in view["dependency_fingerprints"] if ref["path"] == relative}
    sp._projection_require(len(bound) <= 1, "doctrine conflicting bound ledger fingerprints")
    if bound and hashlib.sha256(proposed_text.encode("utf-8")).hexdigest() not in bound:
        return "reconciliation ledger is bound by a published migration; use the reviewed successor/disposition process before changing its bytes"
    return None
