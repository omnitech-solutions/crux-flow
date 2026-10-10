"""The gate check: classify a prompt by position and judge the evidence attached to it.

`advance-run.py` calls this module before it writes anything. Nothing here reads a
prompt's prose to classify it, and nothing reads a result string, a reviewer count or
a council `aggregate` to decide a gate.

Classification is positional (`classify`):

* `council`: ordinal 2 of an `adr-*` or `verify-*` module; the `verify` phase of a
  patch book.
* `module-close`: ordinal 4 of an `adr-*` or `verify-*` module.
* `independent-review`: ordinal 1 of a `review-*` module; the `review` phase of a
  patch book.
* `internal-review`: ordinal 4 of a `dev-*` module. It is not a gate.
* `unclassified`: everything else, every prompt of a `cycle_grandfathered` book, and
  every prompt of a book without `cycle_kind` when the run-start fields are known (an unknown
  start classifies by `module_tag` and `phase`). It is never reported as a passed gate.

`cycle_grandfathered` and `cycle_kind` lie outside the book's content hash, so an edit to
an in-flight book moves neither the hash nor the binding. The execution boundary therefore
passes their run-start values as `StartFields`. With a known start, those values classify
and `unbound_field_changes` names any that moved. With an unknown start, classification
ignores both fields and reads only the hash-bound `module_tag` prefix and `phase`: a
module-tagged gate prompt is never `unclassified` on the strength of a field the run did not
bind.

`start_fields` reads `base_commit` from the snapshot's first committed version that records
one, and flags a live value that differs (`base_rewritten`). That catches a rewrite of
`base_commit` alone, committed or not, because the cycle's git reads turn replace refs and grafts
off. A shallow boundary cannot be turned off, so in a shallow repository the run start is unknown.
Committed tampering with a run record lies outside the local-tool threat model, and this check
does not hold, nor can prose enforce, any of these:

* An edit made before the snapshot's first commit, or while no committed version records
  `base_commit`, is read as the run-start value.
* A history rewrite that replaces that first committed version replaces the reference.
* A committed rewrite of `base_commit` together with `run_id` (or with `book_content_hash`,
  the book edited to match) re-keys the run. Every earlier version at the path then names
  another run and is skipped, so the rewrite reads as the run's first version.
* A committed copy of the snapshot at a new path (`run-RUN-002.yaml`) has a history that
  starts at the copy, so the copy's value is its first version.
* A `git mv` and a rewrite in one commit does the same: history is read without rename
  following, so it starts at the new path.
* A committed hand-edit that marks a gate prompt `done`, or moves `current_prompt` past it,
  is never seen: the gate check judges only the prompt being advanced.

A council-class gate (`council`, `module-close`) admits one kind of evidence: committed council
records that name `run-council.py` as their writer (a self-asserted field), bound to this book,
run and module, whose seats match the current router registry (`evaluate_council_gate`). An
independent-review gate admits one kind: reviewer reports whose reviewed paths are clean
(`evaluate_review_gate`). Neither satisfies the other's gate.

The council gate reads a record only when it is committed: HEAD, the index and the working tree
hold the same bytes (`council_records.path_state`). It refuses when a record committed in HEAD
under the run's `council/` directory and bound to this run is missing from the working tree or
differs from HEAD (one exception: a format-2 council record that names a committed attempt in
scope and is still present but differs from HEAD, as when a hook altered it before the council
runner verified its commit, resolves nothing, so its attempt stays open and stops at 4), and when a `.json` path under that directory named in a prompt's `artifacts` in
the run snapshot is missing. A permanent stop (a held, misnumbered or tied round, a scan-refused
round, an ARCHITECTURAL vote) therefore holds once its record is committed. It then clears only by
committed tampering, outside the local-tool threat model, which no script holds against: a
committed rewrite of its record; a committed deletion of the record, when no prompt's current
artifacts name it; or a switch to a branch without the stop. A re-advance of the blocked prompt
replaces that prompt's artifacts and keeps the old list only in its notes, which the gate does not
read. A committed deletion after a re-advance that attaches only other records therefore clears
the stop. (A could-not-run stop clears on a later converged round; an unauthorized round clears
on a committed owner exception.)

Council attempts close the discard window for format-2 councils. The council runner commits an
attempt record before any council request, so a held council record deleted before its first
commit, a failed commit or a runner crash leaves a committed attempt that no record resolves. The
gate reads attempts before any pass or route: an open committed attempt in scope is a `stop` at 4,
its attempt record decides, and `--outcome blocked` must attach it. A fresh round at the same
number therefore never passes over it. Only the attempt's original council record resolves it: a
format-2 record that names it by path, sha256 and attempt id, whose committed bytes pass the
byte-exact seal and equal its pending copy when one survives, and whose book, run, binding,
round, question, subjects and registry equal the attempt's. Any other format-2 record that names
an attempt is no evidence and takes no place, so a record a hook reformatted leaves its attempt
open. A format-2 record naming an attempt record that is not in scope is refused. An owner's
void-attempt record voids an attempt, and is refused while a council record naming the attempt
survives in HEAD, in the working tree or in a pending copy, because that result can
still resolve or recover it. A voided attempt
takes no place.

What still escapes every check here:

* A format-1 council record was written without a claim, so the discard window stays open for
  it: deleted before its first commit, it leaves no trace. A format-1 record is admitted only in a
  run whose base_commit predates format version 2 (`v1_admissible`); an unknown base refuses it.
* A void-attempt has no vendored writer, so its owner authorship is asserted, not proven. It can
  close an attempt whose result was lost before any copy survived.
* Deleting the run's preflight diagnostics log buys more preflight retries, never a pass: a pass
  still needs a resolved, committed council record.
* The gate never verifies that the owner cleared a could-not-run stop, a `preflight-needs-owner`
  stop or a `preflight-retries-spent` stop: a later converged round passes whether or not the
  owner acted. Advancing the record `blocked` records the stop in the run snapshot; the wait for
  the owner holds by prose.
* Committed tampering lies outside the local-tool threat model: a committed rewrite of an attempt
  record together with its council record, or a committed void the owner never wrote.

Each responding seat of the record that decides (the deciding council record, or the council
record a deciding refutation names) is checked against the CURRENT router registry
(`REGISTRY_PATH`): the role is a gate role, the `registry_key` is the key `model_roles` assigns
that role and is not in `retired_models`, `requested_model` is that entry's `api_string`, and
`served_model` and `served_provider` are in its accepted sets. An errored seat's key and requested
model are checked too. An earlier round is not checked against the registry: it can add only a
stop or a hold, never a pass, so a registry rotation mid-module never strands the module. A
registry edit between the deciding council and its advance makes that record inadmissible, and the
gate fails closed; a new round on the new assignment then decides. `writer` and the seat values
are self-asserted: a forged, committed record that copies the current registry values passes, and
no check here can tell it from the council runner's record.

A verdict is one of:

* `pass`: the gate is satisfied; outcome `done` is allowed.
* `route`: another round is due; `route_to` names the prompt that runs next.
* `stop`: admissible evidence that stops the run. `stops` lists the stop numbers:
  1 is the escalation-loop stop (a held round, a reused or out-of-bound round, a tie, a
  spent round bound, a spent preflight retry bound recorded as `preflight-retries-spent`); 4 is
  the contradicted-premise stop (an open council attempt, the council could not run, a
  `preflight-needs-owner` record, a scan-refused round, an `ARCHITECTURAL` vote).
* `refuse`: the evidence is inadmissible (missing, not attached, uncommitted, invalid, wrongly
  bound, or seated off the current registry).

A round that did not converge at place 3 or later stops with "the round bound is spent"
(stop 1), including a round an owner exception authorizes. That reason is added only when no
other stop applies to the gate; with another exception present (a held seat, a reused round
number) the run stops on that one. The owner acts the same way on either.

Record order is committed order: the commit that introduced each record, never its
`written_at`. Two in-scope records on one commit, or on commits with no ancestry between them,
are a tie and stop the gate. A `written_at` that contradicts the committed order, a record
removed from history and a path with no single introducing commit stop it at 4.

A counted round is a council record with `outcome: ran` that is gate
evidence (a format-2 record only when it resolves the attempt it names); the gate counts places
itself and never trusts a record's own `round`. A could-not-run record, a preflight
could-not-run record and an attempt record take no place.

Imports only the standard library and `council_records`, which supplies the loaders and the
git clean-path checks.
"""
from __future__ import annotations

import errno
import json
import os
import posixpath
import re
import shlex
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import council_records as cr
import council_history_v4 as current_policy

GATE_CLASSES = ("council", "module-close", "independent-review")
#: The could-not-run code the council runner writes on the third preflight refusal at one convening
#: prompt. A deciding record carrying it stops at 1; every other could-not-run code stops at 4.
PREFLIGHT_RETRIES_SPENT = "preflight-retries-spent"
APPROVING = ("APPROVE", "APPROVE_WITH_NITS")
SEAT_ROLES = cr.SEAT_ROLES
WITHHELD_PREFIX = "[withheld:"
#: The router registry the seats are checked against: the file `llm_caller.CONFIG_PATH` names.
#: It is read with the standard library, once per gate evaluation, never through `llm_caller`.
REGISTRY_PATH = Path(__file__).resolve().parent / "crux" / "_config" / "llm_router_config.json"


# ───────────────────────────── classification ─────────────────────────────


from council_history_v4 import GateClass


def _tag(prompt: Any) -> Any:
    return prompt.get("module_tag") if isinstance(prompt, dict) else None


UNBOUND_FIELDS = ("cycle_kind", "cycle_grandfathered")


@dataclass(frozen=True)
class StartFields:
    """`cycle_kind` and `cycle_grandfathered` as they stood at run start.

    `known` is false when the run-start book could not be read; classification then
    ignores both fields."""

    known: bool
    cycle_kind: Any = None
    cycle_grandfathered: Any = None
    #: The live `base_commit` differs from the value in the snapshot's first committed
    #: version that records one. The fields above were read at the committed value; the
    #: caller refuses.
    base_rewritten: bool = False
    #: The `base_commit` the fields were read at, or would have been.
    base: Any = None

    @classmethod
    def of(cls, book: Any) -> "StartFields":
        book = book if isinstance(book, dict) else {}
        return cls(True, book.get("cycle_kind"), book.get("cycle_grandfathered"))


def unbound_field_changes(book: dict, start: StartFields) -> list[str]:
    """The unbound fields whose live value differs from the run-start value. An unknown
    start has nothing to compare against and returns no change."""
    if not start.known:
        return []
    live = StartFields.of(book)
    return [f for f in UNBOUND_FIELDS if getattr(live, f) != getattr(start, f)]


_COMMIT = re.compile(r"[0-9a-f]{7,64}")


_NO_CLAIM = object()  # no committed version of the snapshot records a base_commit
_UNREADABLE = object()  # a committed version exists and cannot be read


def _first_committed_base(repo: Path, run: dict, run_path: Path | str) -> Any:
    """The `base_commit` recorded in the snapshot's first committed version that records one.

    The candidates are the commits that wrote the snapshot's path (a deletion excluded),
    oldest first. A version naming another `run_id` or `book_content_hash` is skipped (another
    run once held the path); the first of this run's versions that carries the key decides.
    Returns `_NO_CLAIM` when no commit wrote the path (the run has not been committed yet) or
    none of this run's versions carries the key (a run older than the field), and
    `_UNREADABLE` when git fails, the repository is shallow, or a version does not parse.

    History is read without rename following (`--no-renames`), so a snapshot renamed or
    copied to a new path has a history that starts there. A committed version that re-keys
    the run makes every earlier version a skipped one. Either way, the committed rewrite is
    read as the first version (the module docstring lists these limits)."""
    try:
        _, rel = cr.resolve_in_repo(repo, run_path)
    except cr.RecordError:
        return _UNREADABLE
    # A shallow boundary hides every commit behind it, and git has no switch that turns it off,
    # so a shallow repository's history is no evidence of the first version.
    shallow = cr.git(repo, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != b"false":
        return _UNREADABLE
    log = cr.git(repo, "log", "--no-renames", "--diff-filter=d", "--format=%H", "--", rel)
    if log.returncode != 0:
        return _UNREADABLE
    vp = cr._validator()
    for commit in reversed(log.stdout.decode("utf-8", "replace").split()):
        blob = cr.git(repo, "cat-file", "blob", f"{commit}:{rel}")
        if blob.returncode != 0:
            return _UNREADABLE
        try:
            doc = vp.load_yaml(blob.stdout.decode("utf-8"))
        except Exception:  # noqa: BLE001 - an unreadable committed snapshot is no evidence
            return _UNREADABLE
        if not isinstance(doc, dict):
            return _UNREADABLE
        if doc.get("run_id") != run.get("run_id") or doc.get("book_content_hash") != run.get("book_content_hash"):
            continue
        if "base_commit" in doc:
            return doc["base_commit"]
    return _NO_CLAIM


def start_fields(run: dict, run_path: Path | str, book_path: Path | str) -> StartFields:
    """The unbound fields as they stood at run start: read from the book at the run's
    `base_commit`, at the resolved book's repository path or its active/archive counterpart.

    The `base_commit` read is the one in the snapshot's first committed version that records
    one (`_first_committed_base`), so a later rewrite of that field alone, committed or not,
    cannot re-bind the fields to a commit where the book carries `cycle_grandfathered`. When
    the live value differs from it, `base_rewritten` is set and the caller refuses. A committed
    rewrite that also re-keys the run, a committed copy at a new path, or a rename and a rewrite
    in one commit does re-bind them, because each makes the rewrite the first version read. Before the snapshot's
    first commit, and while no committed version records a `base_commit`, the live value is
    read: nothing holds the field then, so an edit made in that window is not detected.

    The result is unknown (`known=False`) when the run has no `base_commit`, the run is
    outside a git repository, the snapshot's committed versions cannot be read (a shallow
    repository's cannot), the book is
    outside it or behind a symlinked directory, no candidate exists at that commit, it does
    not load, or its content hash differs from the run's `book_content_hash` (it is then not
    the run's book)."""
    live = run.get("base_commit") if isinstance(run, dict) else None
    repo = cr.repo_root(run_path)
    if repo is None:
        return StartFields(known=False, base=live)
    first = _first_committed_base(repo, run, run_path)
    if first is _UNREADABLE:
        return StartFields(known=False, base=live)
    base, rewritten = (live, False) if first is _NO_CLAIM else (first, first != live)
    return replace(_fields_at(repo, run, base, book_path), base_rewritten=rewritten, base=base)


def _fields_at(repo: Path, run: dict, base: Any, book_path: Path | str) -> StartFields:
    """The unbound fields of the run's book at commit `base`, or an unknown start."""
    unknown = StartFields(known=False)
    if not isinstance(base, str) or not _COMMIT.fullmatch(base):
        return unknown
    try:
        _, rel = cr.resolve_in_repo(repo, book_path)
    except cr.RecordError:
        return unknown
    parts = rel.split("/")
    candidates = [rel]
    if len(parts) >= 2 and parts[-2] in ("active", "archive"):
        candidates.append("/".join(parts[:-2] + ["archive" if parts[-2] == "active" else "active", parts[-1]]))
    vp = cr._validator()
    for cand in candidates:
        blob = cr.git(repo, "cat-file", "blob", f"{base}:{cand}")
        if blob.returncode != 0:
            continue
        try:
            book = vp.load_yaml(blob.stdout.decode("utf-8"))
        except Exception:  # noqa: BLE001 - an unreadable base book is an unknown start
            return unknown
        if not isinstance(book, dict) or vp.compute_book_hash(book) != run.get("book_content_hash"):
            return unknown
        return StartFields.of(book)
    return unknown


def _inferred_kind(prompt: dict) -> str | None:
    """The cycle kind a prompt's hash-bound fields imply, for an unknown run start."""
    tag = _tag(prompt)
    if isinstance(tag, str) and "-" in tag:
        prefix = tag.split("-", 1)[0]
        return prefix if prefix in ("adr", "verify") else "module"
    if isinstance(prompt.get("phase"), str):
        return "patch"
    return None


def classify(book: dict, n: int, start: StartFields | None = None) -> GateClass:
    """The gate class of prompt `n`, by position in `book`.

    `start` carries the run-start values of the two unbound fields. None reads them from
    `book` itself, which only a caller with no run may do; the execution boundary always
    passes a `StartFields`."""
    version_two = isinstance(book, dict) and book.get("format_version") == "2"
    if version_two:
        cr._validator().validate_format_two(book)
        # Kind and slots are hash-bound in format two, independent of whether
        # the legacy run-start source can be located.
        start = StartFields.of(book)
    unclassified = GateClass("unclassified", n)
    prompts = book.get("prompts") if isinstance(book, dict) else None
    if not isinstance(prompts, list):
        return unclassified
    if start is None:
        start = StartFields.of(book)
    if start.known and start.cycle_grandfathered is True:
        return unclassified
    idx = next((i for i, p in enumerate(prompts) if isinstance(p, dict) and p.get("n") == n), None)
    if idx is None:
        return unclassified
    if start.known:
        kind = start.cycle_kind
    else:
        kind = _inferred_kind(prompts[idx])
        if kind == "module":
            kind = "adr"  # a review-* or dev-* prompt: its own prefix decides below
    if kind == "patch":
        phase = prompts[idx].get("phase")
        if phase == "verify":
            return GateClass("council", n, None, None, phase, "patch", n, (n,))
        if phase == "review":
            return GateClass("independent-review", n, None, None, phase, "patch")
        return GateClass("unclassified", n, None, None, phase if isinstance(phase, str) else None)
    if kind not in (("adr", "implementation", "verify") if version_two else ("adr", "verify")):
        return unclassified
    tag = _tag(prompts[idx])
    if not isinstance(tag, str) or "-" not in tag:
        return unclassified
    start = idx
    while start > 0 and _tag(prompts[start - 1]) == tag:
        start -= 1
    ordinal = idx - start + 1
    prefix = tag.split("-", 1)[0]
    ord3 = None
    if start + 2 < len(prompts) and _tag(prompts[start + 2]) == tag:
        ord3 = prompts[start + 2].get("n")
    end = idx
    while end + 1 < len(prompts) and _tag(prompts[end + 1]) == tag:
        end += 1
    members = tuple(p.get("n") for p in prompts[start:end + 1] if isinstance(p, dict))

    def make(cls: str) -> GateClass:
        return GateClass(cls, n, tag, ordinal, None,
                         prefix if prefix in (("adr", "implementation", "verify") if version_two
                                              else ("adr", "verify")) else None, ord3, members)

    if prefix in (("adr", "implementation", "verify") if version_two else ("adr", "verify")):
        if ordinal == 2:
            return make("council")
        if ordinal == 4:
            return make("module-close")
    elif prefix == "review" and ordinal == 1:
        return make("independent-review")
    elif prefix == "dev" and ordinal == 4:
        return make("internal-review")
    return make("unclassified")


# ───────────────────────────── verdicts ─────────────────────────────


from council_history_v4 import Verdict


from council_history_v4 import _refuse


from council_history_v4 import _rel


from council_history_v4 import _bound


# ───────────────────────────── seats and findings ─────────────────────────────


from council_history_v4 import is_blocking


from council_history_v4 import _responding


from council_history_v4 import _scan_reduced


def load_registry() -> tuple[dict | None, str | None]:
    """The router registry at `REGISTRY_PATH` and None, or None and why it cannot be used.

    An unreadable, unparseable or malformed registry is a problem, never an empty registry: the
    gate fails closed."""
    try:
        reg = json.loads(REGISTRY_PATH.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"the router registry {REGISTRY_PATH.name} cannot be read ({type(exc).__name__})"
    if (not isinstance(reg, dict) or not isinstance(reg.get("models"), dict)
            or not isinstance(reg.get("model_roles"), dict)
            or not isinstance(reg.get("retired_models"), list)):
        return None, (f"the router registry {REGISTRY_PATH.name} is malformed: it needs the objects "
                      "models and model_roles and the list retired_models")
    return reg, None


from council_history_v4 import _registry_problem


from council_history_v4 import admissibility_problem


from council_history_v4 import Round


from council_history_v4 import _analyze


from council_history_v4 import _all_findings


# ───────────────────────────── refutation records ─────────────────────────────


from council_history_v4 import Refutation


from council_history_v4 import derive_result


from council_history_v4 import _check_refutation


# ───────────────────────────── the council-class gate ─────────────────────────────


from council_history_v4 import _subject_changes


from council_history_v4 import _attached


from council_history_v4 import _scope


from council_history_v4 import _walk_places


from council_history_v4 import expected_round


from council_history_v4 import committed_order, OrderError


# ───────────────────────────── council attempts ─────────────────────────────


from council_history_v4 import AttemptState


from council_history_v4 import _is_committed


from council_history_v4 import _subject_pairs


from council_history_v4 import resolution_problem


from council_history_v4 import _in_attempt_scope


from council_history_v4 import _void_names


_RUN_WORK_BASE = re.compile(r"[0-9a-f]{7,64}")


def run_work_candidates(run: dict, run_path: Path | str | None, book_path: Path | str | None,
                        repo: Path) -> set[str]:
    """Repository paths the run may have written: those changed in the run's `base_commit`..HEAD
    (the history-pinned base `start_fields` reads) and those named in any prompt's artifacts.
    Membership makes a path a candidate only; a run-work witness decides. The council runner's
    preflight and the run-work witness writer's `commit` share this one reading."""
    out: set[str] = set()
    run = run or {}
    for p in run.get("prompts") or []:
        for a in (p.get("artifacts") or []) if isinstance(p, dict) else []:
            if isinstance(a, str) and a.strip():
                a = a.strip()
                while a.startswith("./"):
                    a = a[2:]
                out.add(posixpath.normpath(a))
    if run_path is None or book_path is None:
        return out
    try:
        start = start_fields(run, run_path, book_path)
    except Exception:  # noqa: BLE001 - an unreadable start leaves only the artifacts
        return out
    base = start.base
    if start.base_rewritten or not isinstance(base, str) or not _RUN_WORK_BASE.fullmatch(base):
        return out
    r = cr.git(repo, "diff", "--name-only", "-z", "--no-renames", base, "HEAD", "--")
    if r.returncode == 0:
        out |= {os.fsdecode(n) for n in r.stdout.split(b"\x00") if n}
    return out


from council_history_v4 import read_pending


from council_history_v4 import attempt_states


from council_history_v4 import open_attempts


from council_history_v4 import holding_attempts


from council_history_v4 import next_ordinal


from council_history_v4 import _evidence_councils


#: The plugin's attempt schema. When the plugin's own source sits inside the repository (the
#: plugin's source checkout), its presence at a run's base_commit marks a run started after the
#: commit that landed format version 2.
_ATTEMPT_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "council-attempt.schema.json"


from council_history_v4 import v1_admissible


from council_history_v4 import _recover_command


from council_history_v4 import _recover_uncommitted


from council_history_v4 import _names_committed_attempt


from council_history_v4 import _uncommitted_problem


from council_history_v4 import _removed_record_problem


from council_history_v4 import _snapshot_named_problem


from council_history_v4 import _head_records_naming


from council_history_v4 import _void_problems


from council_history_v4 import _open_attempt_verdict


def evaluate_council_gate(gate, run, run_path, repo, artifacts, start=None):
    if repo is None:
        return _refuse("the run is not inside a git repository, so no record can be checked")
    try:
        records = cr.discover_records(Path(run_path).resolve().parent)
    except cr.RecordError as exc:
        return _refuse(f"inadmissible record: {exc}")
    return current_policy._evaluate_council_history(gate, run, run_path, repo,
                                                   artifacts, records, load_registry, start)


# ───────────────────────────── the review gate ─────────────────────────────


def _path_problem(repo: Path, rel: str, declared: str) -> str | None:
    try:
        st = cr.path_state(repo, rel)
    except cr.PathRefused as exc:
        return f"{rel}: {exc.message}"
    if st.symlink:
        return f"{rel}: is a symlink"
    if not st.tracked:
        return f"{rel}: is not tracked"
    if st.head != declared:
        return f"{rel}: its committed content differs from what the report reviewed"
    if st.index != st.head:
        return f"{rel}: has a staged change"
    if st.worktree != st.index:
        return f"{rel}: has an unstaged change"
    return None


def _rev_commit(repo: Path, rev: str) -> str | None:
    r = cr.git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return r.stdout.decode().strip() if r.returncode == 0 else None


def _range_problems(repo: Path, rng: str) -> list[str]:
    base, _, end = rng.partition("..")
    base_c, end_c = _rev_commit(repo, base), _rev_commit(repo, end)
    if base_c is None or end_c is None:
        return [f"{rng}: an end of the range is not a commit"]
    if cr.git(repo, "merge-base", "--is-ancestor", end_c, "HEAD").returncode != 0:
        return [f"{rng}: the range end is not HEAD or an ancestor of HEAD"]
    changed = cr.git(repo, "diff", "--name-only", "--no-renames", "-z", base_c, end_c, "--")
    # os.fsdecode, never "replace": a replaced byte names no tree entry, so a reviewed path would
    # read as deleted and its staged change would go unseen. The name encodes back for git.
    paths = sorted(p for p in (os.fsdecode(b) for b in changed.stdout.split(b"\x00")) if p)
    if not paths:
        return [f"{rng}: the range changes no path, so nothing was reviewed"]
    # A tree diff, not a commit log: `git log --name-only` lists no path for a merge commit, so
    # a merge that rewrote a reviewed path after the range end would go unseen.
    later = cr.git(repo, "diff", "--name-only", "--no-renames", "-z", end_c, "HEAD", "--")
    if later.returncode != 0:
        return [f"{rng}: the changes after the range end cannot be read"]
    later_paths = {p for p in (os.fsdecode(b) for b in later.stdout.split(b"\x00")) if p}
    problems = []
    for rel in paths:
        if rel in later_paths:
            problems.append(f"{rel}: a commit after the range end changes it")
            continue
        try:
            st = cr.path_state(repo, rel)
        except cr.PathRefused as exc:
            problems.append(f"{rel}: {exc.message}")
            continue
        if st.head is None and st.index is None and st.worktree is None and not st.symlink:
            if st.tracked:
                problems.append(f"{rel}: is tracked without a regular file")
            continue  # a path the range deleted stays absent
        if st.symlink:
            problems.append(f"{rel}: is a symlink")
        elif not st.tracked:
            problems.append(f"{rel}: is not tracked")
        elif st.index != st.head:
            problems.append(f"{rel}: has a staged change")
        elif st.worktree != st.index:
            problems.append(f"{rel}: has an unstaged change")
    return problems


def subject_problems(repo: Path, subject: dict) -> list[str]:
    """The problems with a reviewer report's `subject` block, empty when it is clean.

    The one implementation of the subject checks: the review gate and the reviewer-report
    writer (`write-review-report.py`) both call it. A `paths` subject needs every path
    tracked, not a symlink, and equal in HEAD, the index and the working tree to its declared
    hash. A `commit-range` subject needs both ends to be commits, the end to be HEAD or an
    ancestor, a range that changes at least one path, no later commit that changes a path
    the range changed, and every changed path to be clean."""
    if subject["form"] == "paths":
        problems = []
        for item in subject["paths"]:
            p = _path_problem(repo, item["path"], item["sha256"])
            if p:
                problems.append(p)
        return problems
    return _range_problems(repo, subject["range"])


def _report_problems(rec: cr.Record, run: dict, n: int, repo: Path) -> list[str]:
    doc = rec.doc
    name = _rel(repo, rec.path)
    problems: list[str] = []
    if not _bound(doc, run):
        problems.append("is not bound to this book and run")
    if doc["prompt"] != n:
        problems.append(f"is bound to prompt {doc['prompt']}, not prompt {n}")
    problems.extend(subject_problems(repo, doc["subject"]))
    return [f"{name}: {p}" for p in problems]


def evaluate_review_gate(gate: GateClass, run: dict, repo: Path | None, artifacts: list[str],
                         outcome: str) -> Verdict:
    """Judge an `independent-review` gate: attached reviewer reports whose reviewed
    paths are clean. A council record, or any other JSON, never satisfies it.

    A blocked review is not a named stop. `outcome == "blocked"` is judged on the same
    admissibility as `done`: a valid report returns `pass` (the caller then advances the
    prompt `blocked`, with no stop number), and an invalid or missing report refuses."""
    if repo is None:
        return _refuse("the run is not inside a git repository, so no report can be checked")
    reports: list[cr.Record] = []
    for a in artifacts:
        try:
            absolute, _ = cr.resolve_in_repo(repo, a)
        except cr.PathRefused as exc:
            return _refuse(f"attached artifact {a}: {exc.message}")
        if not absolute.is_file():
            return _refuse(f"attached artifact {a} does not exist")
        if absolute.is_symlink():
            return _refuse(f"attached artifact {a} is a symlink")
        if not absolute.name.endswith(".json"):
            return _refuse(f"attached artifact {a} is not a reviewer report (.json)")
        try:
            rec = cr.load_reviewer_report(absolute)
        except cr.RecordError as exc:
            return _refuse(f"attached artifact {a} is not a valid reviewer report: {exc.message}")
        if rec is None:
            return _refuse(f"attached artifact {a} is JSON but not a reviewer report")
        if rec.record_type != "reviewer-report":
            return _refuse(f"attached artifact {a} is a {rec.record_type}; "
                           "a council verdict never satisfies an independent-review gate")
        reports.append(rec)
    if not reports:
        return _refuse("no reviewer report is attached")
    problems: list[str] = []
    for rec in reports:
        problems.extend(_report_problems(rec, run, gate.n, repo))
    latest = reports[-1]  # the last attached report names the decision; no wall clock
    name = _rel(repo, latest.path)
    if problems:
        return _refuse(*problems, deciding=name)
    if outcome == "blocked":
        return Verdict("pass", [], [f"the reviewer report {name} is valid; the review advances as "
                                    "blocked with it attached"], name)
    return Verdict("pass", [], [f"the reviewer report {name} decides the gate"], name)


def evaluate_gate(gate: GateClass, run: dict, run_path: Path | str, repo: Path | None,
                  artifacts: list[str], outcome: str, start: StartFields | None = None) -> Verdict:
    """Dispatch on the gate class. `outcome` is the advance the caller asks for."""
    if gate.cls in ("council", "module-close"):
        return evaluate_council_gate(gate, run, run_path, repo, artifacts, start)
    if gate.cls == "independent-review":
        return evaluate_review_gate(gate, run, repo, artifacts, outcome)
    raise ValueError(f"{gate.cls} is not a gate class")


__all__ = ["GATE_CLASSES", "GateClass", "StartFields", "Verdict", "classify", "evaluate_gate",
           "evaluate_council_gate", "evaluate_review_gate", "is_blocking", "derive_result",
           "admissibility_problem", "start_fields", "unbound_field_changes", "subject_problems",
           "expected_round", "AttemptState", "attempt_states", "open_attempts", "next_ordinal",
           "resolution_problem", "read_pending", "v1_admissible"]
