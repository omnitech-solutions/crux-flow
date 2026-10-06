"""Decision recovery for the arch concern (ADR-0062, SP-3).

Deterministic core: the pure tri-state classifier, the structural-anchor
candidate identity (LLM-drift-proof), and the mutable candidate state file.
The heuristic extraction phase (scanning code for latent decisions) is the
`recover-decisions` skill's job — it feeds candidates into `StateFile` here;
`transition-decision` ratifies them. This module holds the parts that must be
pure and testable per ADR-0062's acceptance criteria.

ADR-0095 (amending ADR-0062) adds the observation terminal and the re-mine
rules around recorded observations: dedup by `anchor_id`, the successor
candidate for a changed claim, and the stale-anchor signal. Every function
here treats the observations directory as READ-ONLY — nothing in this module
creates, mutates, or deletes an observation file (operational schema §17.2:
no scan writes an observation file in any state). All re-mine state lives in
the candidate state file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:                      # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS))

import observation_evidence as _oe          # noqa: E402
from untrusted import parse_problem, redact  # noqa: E402

# ── classification (ADR-0062 Decision 2) — pure, three-way ──────────────────

# load-bearing tag → signal (≥1 present ⇒ load_bearing)
TAG_SIGNALS = {
    "schema": "system-of-record", "data": "system-of-record",
    "storage": "system-of-record", "persistence": "system-of-record",
    "concern": "module-boundary", "module": "module-boundary",
    "boundary": "module-boundary", "layout": "module-boundary",
    "distribution": "external-dependency", "dependency": "external-dependency",
    "runtime": "external-dependency", "provider": "external-dependency",
    "api": "api-contract", "schema-contract": "api-contract",
    "format": "api-contract", "cli": "api-contract",
    "security": "cross-cutting-policy", "auth": "cross-cutting-policy",
    "serialization": "cross-cutting-policy", "concurrency": "cross-cutting-policy",
    "policy": "cross-cutting-policy",
}
# ALL tags in this set (and none load-bearing) ⇒ not_load_bearing
NON_ARCH_TAGS = {
    "meta", "process", "docs", "cleanup", "hygiene", "naming",
    "bugfix", "quick-wins", "workflow", "dx", "audit",
}
# canonical precedence for primary_signal selection
SIGNAL_ORDER = [
    "system-of-record", "module-boundary", "external-dependency",
    "api-contract", "cross-cutting-policy",
]


def signals_for(tags) -> set[str]:
    return {TAG_SIGNALS[t] for t in tags if t in TAG_SIGNALS}


def classify(tags) -> str:
    """Pure tri-state over an ADR's tag set (ADR-0062 AC-1). No filesystem."""
    tags = list(tags or [])
    sigs = signals_for(tags)
    if sigs:
        return "load_bearing"
    if tags and all(t in NON_ARCH_TAGS for t in tags):
        return "not_load_bearing"
    return "unclassifiable"


def primary_signal(sigs) -> str | None:
    for s in SIGNAL_ORDER:
        if s in sigs:
            return s
    return None


def curated_keep(tags) -> bool:
    """Curated decision-index keeps load_bearing + unclassifiable (fail-open),
    excludes not_load_bearing (ADR-0062 Decision 6)."""
    return classify(tags) != "not_load_bearing"


# ── candidate identity (ADR-0062 Decision 3) — anchor-based, no LLM input ────

def candidate_id(anchor_kind: str, canonical_anchor: str) -> str:
    """sha256(anchor_kind + NUL + canonical_anchor)[:16] — stable across prose
    AND signal-classification drift, because no LLM output feeds it."""
    return hashlib.sha256(
        anchor_kind.encode() + b"\x00" + canonical_anchor.encode()
    ).hexdigest()[:16]


# The closed set of structural anchor kinds (ADR-0062 Decision 3). It is the
# `canonical_anchor` dispatch's own key set, hoisted to a name so a reader of a
# candidate row can validate `anchor_kind` WITHOUT constructing an anchor.
# The batch sign-off (ADR-0098 clause 4) refuses a candidate whose kind falls
# outside this set, and that refusal needs the set, not the constructor: a
# sign-off holds a mined row, not the `**parts` each kind's branch requires.
# `SIGNAL_ORDER` is deliberately NOT reused here — it is the classifier's
# precedence list over signals, and the two happen to carry the same five
# strings today for a reason that is not a contract.
ANCHOR_KINDS = (
    "external-dependency",
    "module-boundary",
    "api-contract",
    "system-of-record",
    "cross-cutting-policy",
)


def canonical_anchor(kind: str, **parts) -> str:
    """Fully-qualified canonical form so cross-namespace repeats are distinct.

    The kind is validated against `ANCHOR_KINDS` FIRST, so the one refusal is
    stated once rather than falling out of the dispatch's last line — a new
    kind added to the tuple without a branch here now raises on the branch
    fall-through, which is the loud direction."""
    if kind not in ANCHOR_KINDS:
        raise ValueError(f"unknown anchor kind: {redact(kind)}")
    if kind == "external-dependency":
        return parts["name"]
    if kind == "module-boundary":
        return f"{parts['src']}->{parts['dst']}"
    if kind == "api-contract":
        if "method" in parts:
            return f"{parts['method']} {parts['route']}"
        return f"{parts['module']}:{parts['symbol']}"
    if kind == "system-of-record" or kind == "cross-cutting-policy":
        return f"{parts['file']}::{parts['key']}"
    raise ValueError(f"unknown anchor kind: {redact(kind)}")


# ── mutable candidate state file (ADR-0062 Decision 4) — NOT append-only ─────

class StateFile:
    """A mutable, single-writer, keyed state store. Atomic write-temp-rename;
    fail-closed on a parse error (never guesses)."""

    def __init__(self, path: Path, *, contained_under: Path | None = None):
        self.path = Path(path)
        # [SECURITY:S5] The tree this file must stay inside. Every WRITER
        # supplies it; the read-only constructors do not, and `save` guards
        # what it can without one (see `_assert_write_target`).
        self.contained_under = (None if contained_under is None
                                else Path(contained_under))
        self.rows: dict[str, dict] = {}
        # ADR-0095 stale-anchor signal: {anchor_id: {"obs_id", "status"}}. A
        # distinct top-level mapping, not a candidate row — there is nothing to
        # ratify, and keeping it out of `rows` leaves upsert/prune untouched.
        self.stale_anchors: dict[str, dict] = {}
        # ADR-0062 clause 3 successor identity: {anchor_id: highest n issued}.
        # Monotonic per anchor and never decremented, so a successor key is
        # never reissued even after its row is pruned. Persisted beside the
        # rows because the counter IS the identity source — recomputing it from
        # the surviving rows would reissue a pruned key.
        self.successor_counters: dict[str, int] = {}
        if self.path.exists():
            self._load()

    def _load(self):
        text = self.path.read_text(encoding="utf-8")
        try:
            import yaml
            data = yaml.safe_load(text) or {}
        except Exception as exc:  # fail-closed
            raise ValueError(
                parse_problem("candidate state file", self.path.name, exc)
            ) from exc
        rows = data.get("candidates", [])
        self.rows = {r["id"]: r for r in rows}
        self.stale_anchors = dict(data.get("stale_anchors") or {})
        self.successor_counters = {
            str(k): int(v) for k, v in (data.get("successor_counters") or {}).items()}

    def _assert_write_target(self):
        """[SECURITY:S5] Refuse a write steered outside the tree.

        `mkstemp` + `os.replace` make the LEAF and the TEMPORARY FILE safe, but
        neither looks at the directories above them. A symlink planted at an
        ancestor — `<tree>/arch/_recovered` is the reachable one, since `save`
        creates it with `mkdir(parents=True, exist_ok=True)` and an existing
        symlink to a directory satisfies that call — leaves every entry beneath
        it a real file while putting the whole state file somewhere else. Every
        candidate disposition a batch sign-off writes then lands outside the
        tree, at exit 0, with nothing on any surface to show for it.

        Two legs, the same shape `survey_sheet._assert_contained` and
        `survey_sheet.atomic_write_text` use together:

          * the IMMEDIATE PARENT may not be a symlink. Unconditional, because
            it needs no root to check and it closes the reachable attack for
            every caller, including one that forgets to declare a tree.
          * the RESOLVED parent must sit under the resolved `contained_under`,
            when a writer declared one. This is the ancestor-and-root leg: it
            catches a symlink planted higher up (`<tree>/arch`, say) that the
            parent check cannot see, and it resolves both sides so a tree that
            legitimately lives under a symlinked prefix still passes.

        Raises `ValueError`, which is what `_load` already raises for the
        fail-closed parse and what every caller in this lane surfaces."""
        parent = self.path.parent
        if parent.is_symlink():
            raise ValueError(
                f"refusing to write the candidate state file "
                f"{redact(self.path, quoted=False)}: its parent directory "
                f"{redact(parent, quoted=False)} is a symlink — writing through it "
                "would put the tree's dispositions in the link's target")
        root = self.contained_under
        if root is None:
            return
        root_resolved = root.resolve()
        parent_resolved = parent.resolve()
        if (parent_resolved != root_resolved
                and root_resolved not in parent_resolved.parents):
            raise ValueError(
                f"refusing to write the candidate state file "
                f"{redact(self.path, quoted=False)}: its parent resolves to "
                f"{redact(parent_resolved, quoted=False)}, which is not "
                f"contained under {redact(root_resolved, quoted=False)} — a "
                "symlinked intermediate directory "
                "would put the tree's dispositions outside the tree")

    def save(self):
        payload = {"candidates": [self.rows[k] for k in sorted(self.rows)]}
        if self.stale_anchors:
            payload["stale_anchors"] = {
                k: self.stale_anchors[k] for k in sorted(self.stale_anchors)}
        if self.successor_counters:
            payload["successor_counters"] = {
                k: self.successor_counters[k] for k in sorted(self.successor_counters)}
        try:
            import yaml
            body = yaml.safe_dump(payload, sort_keys=True, allow_unicode=True)
        except Exception:
            body = json.dumps(payload, indent=2, sort_keys=True)
        self._assert_write_target()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The PREDICTABLE `<path>.tmp`, `O_EXCL|O_NOFOLLOW`, and no `finally`
        # cleanup — the shape `survey_sheet.atomic_write_text` and
        # `signoff-backfill._atomic_write_text` already use, restated here for
        # the same reason they restate each other: this module cannot import
        # either without a cycle, and hoisting one shared write path is a
        # refactor outside this fix. Recorded as an `iterate` candidate.
        #
        # `tempfile.mkstemp` was the odd one out, and the difference is not
        # cosmetic. Its name is RANDOM, so a process killed between the create
        # and the `os.replace` left a file no later run could name. The old
        # `finally: os.unlink` hid that from the suite rather than fixing it:
        # a `finally` runs for `KeyboardInterrupt` and does NOT run for
        # `SIGKILL`, so the one caller that could observe the leftover — a
        # crash test raising `KeyboardInterrupt` — was the one caller that
        # never saw it. With a predictable name the writer that owns the name
        # removes a stale one on the next run, which is what `open_new_tmp`
        # does in the sibling and what the retry below does here.
        tmp = self.path.with_name(self.path.name + ".tmp")
        for label, candidate in (("target", self.path), ("temporary file", tmp)):
            if candidate.is_symlink():
                raise ValueError(
                    f"refusing to write the candidate state file "
                    f"{redact(self.path, quoted=False)}: the {label} "
                    f"{redact(candidate, quoted=False)} is a symlink — writing through "
                    "it would put the tree's dispositions in the link's target")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            fd = os.open(tmp, flags, 0o644)
        except FileExistsError:
            # A leftover from a kill. `os.unlink` removes the NAME, so a
            # planted symlink loses its link and never its target; the retry
            # carries `O_EXCL`, so a writer that wins the name in between is
            # refused rather than clobbered.
            os.unlink(tmp)
            fd = os.open(tmp, flags, 0o644)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, self.path)          # atomic
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def upsert_observed(self, cand: dict) -> bool:
        """Add an observed candidate. Returns False (suppressed) if the id is
        already disposed rejected, or deferred and not yet expired."""
        cid = cand["id"]
        cur = self.rows.get(cid)
        if cur:
            if cur["state"] == "rejected":
                return False
            if cur["state"] == "deferred":
                return False  # re-surface handled by the caller vs defer_until
            return False      # already present (observed/ratified)
        cand.setdefault("state", "observed")
        # The plain candidate's state-file key IS its anchor id; a successor
        # candidate (emit_candidate) carries a distinct key on the same anchor.
        cand.setdefault("anchor_id", cid)
        self.rows[cid] = cand
        return True

    def dispose(self, cid: str, state: str, **extra):
        row = self.rows[cid]
        row["state"] = state
        row.update(extra)

    def prune(self, present_anchor_ids: set[str]):
        """Drop rejected rows whose anchor is no longer observed (ADR-0062 D4).
        A still-present rejected decision stays suppressed indefinitely. Keys
        on the row's `anchor_id` (a successor row's key is not its anchor)."""
        for cid in list(self.rows):
            row = self.rows[cid]
            if row["state"] == "rejected" and row.get("anchor_id", cid) not in present_anchor_ids:
                del self.rows[cid]

    def next_successor_n(self, anchor_id: str) -> int:
        """Issue the next per-anchor successor ordinal. Monotonic, and the sole
        source of a successor key's suffix — nothing derived from rule prose
        reaches it."""
        n = self.successor_counters.get(anchor_id, 0) + 1
        self.successor_counters[anchor_id] = n
        return n

    def open_successor(self, anchor_id: str) -> dict | None:
        """The one successor row on this anchor still awaiting a human, or None.
        At most one exists: `emit_candidate` refreshes it in place rather than
        opening a second."""
        for cid in sorted(self.rows):
            row = self.rows[cid]
            if (row.get("anchor_id") == anchor_id and "predecessor_id" in row
                    and row.get("state") == "observed"):
                return row
        return None

    def signal_stale_anchors(self, recorded: dict[str, dict],
                             present_anchor_ids: set[str]) -> dict[str, dict]:
        """ADR-0095 req. 2 / schema §17.2: a recorded `anchor_id` absent from
        the mined anchor set is a SIGNAL, never a transition. Recomputed
        wholesale from (recorded − present) on every re-mine, so it is
        idempotent, never accumulates, and clears by itself when the anchor
        returns. A retired, rejected, or decided record's anchor is expected to
        be gone and does not signal — for a decided record the rule is governed
        by the ADR its `decided_by` names from then on. `decided` is also
        terminal in the §17.2 lifecycle, so a signal on one is undischargeable:
        that gate refuses a decided record, and `--phase mine` would sit at
        exit 1 on a step no human can complete. Writes only
        `self.stale_anchors`; touches no observation file.

        `observed` and `ratified` both signal, and the signal carries the
        record's status so the reader can name the transition §17.2 actually
        admits from it — `reject` from `observed`, `retire` from `ratified`.
        Emitting one command for both would be the same undischargeable-step
        defect: `retire` is admitted only from `ratified`."""
        self.stale_anchors = {
            aid: {"obs_id": rec["id"], "status": rec["status"]}
            for aid, rec in sorted(recorded.items())
            if aid not in present_anchor_ids
            and rec.get("status") not in ("retired", "rejected", "decided")
        }
        return self.stale_anchors


# ── recorded observations (ADR-0095 req. 2) — read-only against the concern ─

def _norm_rule(text) -> str:
    return " ".join(str(text or "").split())


def _norm_evidence(evidence) -> list[str]:
    return sorted(str(e).strip() for e in (evidence or []))


def claim_digest(rule, evidence) -> str:
    """sha256 over the normalized (rule, evidence) claim — the part of a record
    that §17.2 makes immutable once ratified. Whitespace-insensitive on the
    rule; order-insensitive on the evidence list."""
    body = _norm_rule(rule).encode() + b"\x00" + "\n".join(_norm_evidence(evidence)).encode()
    return hashlib.sha256(body).hexdigest()[:16]


def successor_id(anchor_id: str, n: int) -> str:
    """State-file key for a successor candidate: `<anchor_id>+<n>`, where `n` is
    the monotonic per-anchor ordinal `StateFile.next_successor_n` issues.

    `candidate_id` is a pure function of the anchor, so a successor would
    collide with its predecessor's key without a suffix. The suffix is derived
    from the anchor and a state-file counter, and from nothing else — no LLM
    output feeds recovery identity (ADR-0062 Decision 3). The earlier form
    keyed the suffix on `claim_digest(rule, evidence)`, which put generated rule
    prose in the identity and made every re-phrasing of one claim a new
    permanent row.

    The `+` cannot appear in a 16-hex-char anchor id, so the two key forms
    never collide, and the anchor is recoverable from either by splitting at
    the first `+` — which is what makes `find_ratified_observation` exact for
    both."""
    return f"{anchor_id}+{n}"


def anchor_of(candidate_key: str) -> str:
    """The anchor id inside a candidate key. A plain key IS its anchor; a
    successor key is `<anchor_id>+<n>`. Total, and prose-free."""
    return candidate_key.split("+", 1)[0]


def _contained_record(observations_dir: Path, path: Path) -> Path:
    """The record's resolved path, or a refusal.

    [SECURITY:S5] `glob` returns a symlink and `read_text` follows it, so a
    link planted in the concern directory made this reader open a file OUTSIDE
    the repository — and the fail-closed raise below then printed part of it.
    Containment is a property of the RESOLVED path, never of the spelling, and
    `observation_evidence.resolve_contained` is the one place that decides it.
    `survey_sheet.record_paths` carries the same leg over the same directory.
    """
    resolved = _oe.resolve_contained(observations_dir, path.name)
    if resolved is None or not resolved.is_file():
        raise ValueError(
            f"refusing to read {redact(path.name, quoted=False)}: it does not "
            f"resolve to a file inside "
            f"{redact(observations_dir, quoted=False)} — an observation "
            "record is read from "
            "the concern directory, never through a link out of it")
    return resolved


def frontmatter_problem(name: str, exc: Exception) -> str:
    """The corrupt-frontmatter message, quoting NO byte of the file.

    The name this lane knows the refusal by; the RULE it applies lives in
    `untrusted.parse_problem`, which is also what `StateFile._load` and the
    survey sheet reader raise. It was a second copy of that rule until round
    4 — and a copy is how the state file kept the leak the observation reader
    had already closed. Kept as a name because `survey_sheet.record_paths`
    imports it: two readers of one directory must not disagree about how much
    of a file they are willing to quote."""
    return parse_problem("observation frontmatter", name, exc)


def read_recorded_observations(observations_dir: Path) -> dict[str, dict]:
    """Collect `{anchor_id: {"id", "status", "rule", "evidence"}}` from every
    `OBS-*.md` under the observations concern. Read-only; a missing directory
    is an empty map. Fail-closed on frontmatter that does not parse — guessing
    could propose a duplicate of a recorded fact. `rule` is the record's
    `governs` rule text, normalized, joined in sorted order (the field set is
    the operational schema §17.1's). A file without a leading frontmatter
    block or without an `anchor_id` is skipped, as `find_ratified_adr` skips.
    Caller threads the result into `emit_candidate`; `StateFile` never reads
    the concern itself."""
    out: dict[str, dict] = {}
    d = Path(observations_dir)
    if not d.is_dir():
        return out
    for op in sorted(d.glob("OBS-*.md")):
        resolved = _contained_record(d, op)
        m = re.match(r"^---\n(.*?)\n---\n",
                     resolved.read_text(encoding="utf-8"), re.DOTALL)
        if not m:
            continue
        try:
            import yaml
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception as exc:  # fail-closed
            raise ValueError(frontmatter_problem(op.name, exc)) from exc
        aid = fm.get("anchor_id")
        if not aid:
            continue
        rules = sorted(_norm_rule(g.get("rule")) for g in (fm.get("governs") or [])
                       if isinstance(g, dict))
        out[str(aid)] = {
            "id": str(fm.get("id") or op.stem),
            "status": str(fm.get("status") or ""),
            "rule": "\n".join(rules),
            "evidence": _norm_evidence(fm.get("evidence")),
        }
    return out


def emit_candidate(state: StateFile, recorded: dict[str, dict], cand: dict) -> str:
    """The emit path for one mined candidate, deduplicated against recorded
    observations (ADR-0095 req. 2). `cand` carries `id` (= candidate_id),
    `rule` (the redaction-scanned statement) and `evidence` (path:line-range
    list). Returns one of:

    - `"recorded"`  — the anchor is recorded with the same claim: NO candidate.
    - `"successor"` — the anchor is recorded with a changed rule or evidence,
      and no successor is open on it: one opens in `observed` on the same
      `anchor_id`, keyed by `successor_id`, carrying `predecessor_id` and the
      claim's digest. The record itself is not touched.
    - `"observed"`  — unrecorded anchor: a plain candidate, as before ADR-0095.
    - `"present"`   — the state file already holds this claim, or suppresses it,
      or holds an open successor on this anchor that was refreshed in place.

    Dedup ignores the record's status: a human disposed the record for a
    reason, and a re-mine proposes only what changed.

    **At most one successor per anchor is ever open.** A re-mine that finds the
    claim changed AGAIN before a human has disposed the open successor rewrites
    that row's claim rather than opening a second — a candidate is a proposal,
    not a record, so refreshing it is not the claim mutation §17.2 forbids. That
    bound is what the counter identity buys: keying on the claim made three
    phrasings of one claim three permanent rows, growing without limit while the
    anchor persisted.

    `claim_digest` is stored on the row as DATA, never as identity. It is the
    suppression key: a claim a human already rejected stays rejected across
    re-mines, exactly as a rejected plain candidate does."""
    aid = cand.get("anchor_id") or cand["id"]
    rec = recorded.get(aid)
    if rec is None:
        cand.setdefault("anchor_id", aid)
        return "observed" if state.upsert_observed(cand) else "present"
    digest = claim_digest(cand.get("rule"), cand.get("evidence"))
    if digest == claim_digest(rec.get("rule"), rec.get("evidence")):
        return "recorded"
    for cid in sorted(state.rows):
        row = state.rows[cid]
        if row.get("anchor_id") == aid and row.get("claim_digest") == digest:
            return "present"          # already proposed, or disposed and suppressed
    open_row = state.open_successor(aid)
    if open_row is not None:
        open_row["rule"] = cand.get("rule")
        open_row["evidence"] = cand.get("evidence")
        open_row["claim_digest"] = digest
        open_row["predecessor_id"] = rec["id"]
        return "present"
    succ = dict(cand)
    succ["id"] = successor_id(aid, state.next_successor_n(aid))
    succ["anchor_id"] = aid
    succ["predecessor_id"] = rec["id"]
    succ["claim_digest"] = digest
    succ["state"] = "observed"
    return "successor" if state.upsert_observed(succ) else "present"


def find_ratified_adr(adrs_dir: Path, cid: str) -> str | None:
    """Idempotent-ratify correlation (ADR-0062 Decision 5): find a Proposed ADR
    whose frontmatter carries `recovered_id: <cid>`. None if not yet created."""
    for ap in sorted(Path(adrs_dir).glob("ADR-*.md")):
        text = ap.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            continue
        fm = m.group(1)
        if re.search(rf"^recovered_id:\s*{re.escape(cid)}\s*$", fm, re.MULTILINE):
            mid = re.search(r"^id:\s*(\S+)", fm, re.MULTILINE)
            return mid.group(1) if mid else ap.stem
    return None


def find_ratified_observation(observations_dir: Path, cid: str,
                              predecessor_id: str | None = None) -> str | None:
    """Observation twin of `find_ratified_adr` (ADR-0095 req. 3a): the
    idempotency check before `transition-decision ratify --as observation`
    creates a record. Correlation key is `anchor_id`, which schema §17.1
    requires on every record. Returns the record's `id` (its stem if the `id`
    is unreadable), else None. Read-only; a missing directory is a miss.

    Frontmatter is parsed with `yaml.safe_load`, as `read_recorded_observations`
    already does. A regex over the raw block reads only ONE spelling, and
    `OBS-template.md` — which both on-ramps fill from — writes `anchor_id`
    QUOTED, so the regex form matched no record this tree can actually produce.
    Two readers of one field must not disagree about the field's grammar, so
    there is now one parser.

    Correlation is EXACT for both candidate forms. A plain candidate's key IS
    the record's `anchor_id`. A successor candidate's key is `<anchor_id>+<n>`,
    a pure function of the anchor and a state-file counter, so the anchor is
    recoverable by splitting at the first `+`. Telling a successor's record from
    its predecessor's then needs one more value — the predecessor's id — and
    `emit_candidate` writes exactly that onto every successor row as
    `predecessor_id`, so the caller ratifying the successor already holds it.
    Pass it; a successor correlated without it hits the predecessor and would
    block the successor from ever being created.

    (The earlier form of this function documented the successor leg as an
    unclosable KNOWN LIMIT, because closing it appeared to need a match on
    `claim_digest(rule, evidence)` and that keys identity on generated prose,
    which ADR-0062 Decision 3 forbids. Re-keying the successor to the anchor
    plus a counter removed the prose from the key, and with it the reason the
    gap could not be closed.)"""
    d = Path(observations_dir)
    if not d.is_dir():
        return None
    anchor = anchor_of(cid)
    for op in sorted(d.glob("OBS-*.md")):
        resolved = _contained_record(d, op)
        m = re.match(r"^---\n(.*?)\n---\n",
                     resolved.read_text(encoding="utf-8"), re.DOTALL)
        if not m:
            continue
        try:
            import yaml
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception as exc:  # fail-closed, as read_recorded_observations is
            raise ValueError(frontmatter_problem(op.name, exc)) from exc
        if not isinstance(fm, dict) or str(fm.get("anchor_id", "")) != anchor:
            continue
        rid = str(fm.get("id") or op.stem)
        if predecessor_id is not None and rid == predecessor_id:
            continue
        return rid
    return None
