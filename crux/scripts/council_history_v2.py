"""Trusted production council policy 2, extracted from the shared legacy gate.

Issued profiles are retained. Retained evidence is never executed. Present-day
architectural authority checking is deliberately outside this historical kernel.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import council_records_v2 as cr
import council_schema_v2 as schema_engine

VERSION = "2"
APPROVING = ("APPROVE", "APPROVE_WITH_NITS")
SEAT_ROLES = ("openai_top", "anthropic_top", "google_top")
WITHHELD_PREFIX = "[withheld:"
_SCHEMA_FILES = {kind: Path(__file__).resolve().parent.parent / "schemas" / "council-policy-v2" /
                 (kind + ".schema.json") for kind in cr.RECORD_TYPES}


def schema_errors(doc, record_type):
    schema = schema_engine.load_schema(_SCHEMA_FILES[record_type])
    errors = []
    schema_engine.validate(doc, schema, "#", "#", errors, record_type)
    return [f"{item['instance_path']}: {item['error']}" for item in errors]


def load_record(path):
    return cr._load_record(path, schema_errors)


def discover_records(run_dir):
    return cr._discover_records(run_dir, load_record)


@dataclass(frozen=True)
class GateClass:
    cls: str
    n: int
    module_tag: str | None = None
    ordinal: int | None = None
    phase: str | None = None
    module_kind: str | None = None  # "adr" | "verify" | "patch" for a gate class
    ordinal3: int | None = None  # the module's ordinal-3 prompt, where a route lands
    module_prompts: tuple = ()  # every prompt number of the module (a patch gate: its own n)

    def to_dict(self) -> dict:
        return {"class": self.cls, "module_tag": self.module_tag, "ordinal": self.ordinal,
                "phase": self.phase}


@dataclass
class Verdict:
    verdict: str
    stops: list[int] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    deciding_record: str | None = None
    route_to: int | None = None
    holds: list[str] = field(default_factory=list)
    changed_subjects: list[dict] = field(default_factory=list)
    withheld_subjects: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "stops": sorted(set(self.stops)), "reasons": list(self.reasons),
                "deciding_record": self.deciding_record, "route_to": self.route_to,
                "holds": list(self.holds), "changed_subjects": list(self.changed_subjects),
                "withheld_subjects": list(self.withheld_subjects)}


def _refuse(*reasons: str, deciding: str | None = None) -> Verdict:
    return Verdict("refuse", reasons=list(reasons), deciding_record=deciding)


def _rel(repo: Path, path: Path | str) -> str:
    try:
        return Path(path).resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def _bound(doc: dict, run: dict) -> bool:
    book = doc.get("book") or {}
    return (book.get("id") == run.get("book_id")
            and book.get("content_hash") == run.get("book_content_hash")
            and doc.get("run_id") == run.get("run_id"))


def is_blocking(finding: dict) -> bool:
    """A finding is a nit only when `kind` is `nit`, `dimension` is present and not
    Security, and `safety_adjacent` is false. Every other finding blocks; a missing tag
    counts against the finding."""
    dim = finding.get("dimension")
    return (finding.get("kind") != "nit"
            or not isinstance(dim, str) or not dim.strip()
            or dim.strip().lower() == "security"
            or finding.get("safety_adjacent") is not False)


def _responding(doc: dict) -> list[dict]:
    return [s for s in doc.get("seats", []) if not s.get("errored")]


def _scan_reduced(doc: dict) -> bool:
    reason = doc.get("refusal_reason")
    return isinstance(reason, dict) and reason.get("code") == "secret-scan"


def _registry_problem(seat: dict, reg: dict, served: bool = True) -> str | None:
    """Why a seat does not match the current registry assignment of its role, or None.

    `served` false skips the served-model and served-provider checks: an errored seat carries the
    key and requested model it was resolved to, but no reply."""
    role, key = seat["role"], seat["registry_key"]
    assigned = reg["model_roles"].get(role)
    if key != assigned:
        return f"the seat {role} carries registry_key {key!r}, but the registry assigns {assigned!r} to that role"
    if key in reg["retired_models"]:
        return f"the seat {role} sits on {key!r}, which the registry retired"
    entry = reg["models"].get(key)
    if not isinstance(entry, dict):
        return f"the seat {role} sits on {key!r}, which the registry does not list as a model"
    if seat["requested_model"] != entry.get("api_string"):
        return (f"the seat {role} requested_model {seat['requested_model']!r} is not the registry api_string "
                f"{entry.get('api_string')!r} of {key!r}")
    for name, accepted in (("served_model", "accepted_served_models"),
                            ("served_provider", "accepted_served_providers")) if served else ():
        allowed = entry.get(accepted)
        if not isinstance(allowed, list) or seat[name] not in allowed:
            return (f"the seat {role} {name} {seat[name]!r} is not in the registry's {accepted} for "
                    f"{key!r}: {allowed!r}")
    return None


def admissibility_problem(doc: dict, registry: dict | None = None) -> str | None:
    """Why a counted council record is inadmissible as gate evidence, or None.

    With `registry` (the parsed router registry), each responding seat is also checked against
    the registry's CURRENT assignment (`_registry_problem`), and an errored seat's key and requested
    model too. The gate passes `registry` for the record that decides only. A registry edit between
    that council and its advance makes the record inadmissible; the module then closes on a new
    round seated on the new assignment. `writer` and the seat values are self-asserted: a forged
    record that copies the current registry values passes."""
    seats = doc.get("seats", [])
    if len(seats) != 3 or sorted(s.get("role") for s in seats) != sorted(SEAT_ROLES):
        return "does not seat exactly the three roles " + ", ".join(SEAT_ROLES)
    for s in seats:
        requested = s.get("requested_model")
        claimed = s.get("provider_namespace")
        if (not isinstance(requested, str) or "/" not in requested
                or not isinstance(claimed, str) or not claimed
                or claimed != requested.split("/", 1)[0]):
            return (f"the seat {s.get('role')} claims provider namespace {claimed!r}, which is not the "
                    f"provider namespace of its requested model {requested!r}")
    if len({s["provider_namespace"] for s in seats}) != 3:
        return "does not seat three distinct providers"
    responding = _responding(doc)
    if len(responding) < 2:
        return "fewer than two seats responded"
    # A scan-reduced record nulls a decision the secret scan matched; its refusal reason stops
    # the gate at 4, and a null decision never counts as approval.
    keys = ("registry_key", "requested_model", "served_model", "served_provider")
    if not _scan_reduced(doc):
        keys += ("decision",)
    for s in responding:
        for key in keys:
            if not isinstance(s.get(key), str) or not s[key]:
                return f"the responding seat {s.get('role')} carries no {key}"
    if registry is not None:
        for s in responding:
            problem = _registry_problem(s, registry)
            if problem:
                return problem
        # An errored seat casts no vote, but its key and requested model still name what it was
        # resolved to; a record never names an unregistered assignment.
        for s in seats:
            if s.get("errored") and (s.get("registry_key") is not None or s.get("requested_model") is not None):
                problem = _registry_problem(s, registry, served=False)
                if problem:
                    return problem
    return None


@dataclass
class Round:
    rec: cr.Record
    place: int
    held: list[str]  # roles
    converged: bool
    blocking: list[str]  # finding ids
    architectural: bool
    authorized: bool = False


def _analyze(rec: cr.Record, place: int, verify_kind: bool) -> Round:
    doc = rec.doc
    held: list[str] = []
    blocking: list[str] = []
    architectural = False
    all_approve = True
    any_blocking_seat = False
    for s in _responding(doc):
        seat_blocking = [f["id"] for f in s.get("findings", []) if is_blocking(f)]
        blocking.extend(seat_blocking)
        any_blocking_seat = any_blocking_seat or bool(seat_blocking)
        if s["decision"] not in APPROVING:
            all_approve = False
            # A decision the secret scan nulled is no hold: the seat's refusal reason stops at 4.
            if not seat_blocking and s["decision"] is not None:
                held.append(s["role"])
        if s["decision"] == "ARCHITECTURAL" and verify_kind:
            architectural = True
    converged = bool(doc.get("quorum_met")) and all_approve and not any_blocking_seat
    return Round(rec, place, held, converged, blocking, architectural)


def _all_findings(doc: dict) -> dict[str, dict]:
    return {f["id"]: f for s in doc.get("seats", []) for f in s.get("findings", [])}


@dataclass
class Refutation:
    rec: cr.Record
    valid: bool
    reason: str | None
    named: Round | None
    wholly_refuted: bool
    role: str


def derive_result(entry: dict) -> str:
    """The result a check's token earns: `refuted` when it equals `expect`, `inconclusive`
    for `verdict:INCONCLUSIVE`, `confirmed` otherwise."""
    if entry["result_token"] == entry["expect"]:
        return "refuted"
    if entry["result_token"] == "verdict:INCONCLUSIVE":
        return "inconclusive"
    return "confirmed"


def _check_refutation(rec: cr.Record, repo: Path, counted: list[Round], place: int,
                      conductor_recorders: set[str]) -> Refutation:
    doc = rec.doc
    role = doc["recorder_role"]

    def bad(reason: str) -> Refutation:
        return Refutation(rec, False, reason, None, False, role)

    try:
        named_abs, _ = cr.resolve_in_repo(repo, doc["council_record"]["path"])
    except cr.PathRefused as exc:
        return bad(f"council_record.path {exc.message}")
    if not named_abs.is_file() or named_abs.is_symlink():
        return bad("council_record.path is not an existing regular file")
    if cr.sha256_file(named_abs) != doc["council_record"]["sha256"]:
        return bad("council_record.sha256 differs from the council record's bytes")
    named = next((c for c in counted if c.rec.path.resolve() == named_abs), None)
    if named is None:
        return bad("names no counted round of this module before it")
    if role == "conductor":
        if named is not counted[-1]:
            return bad("names a round that is not the latest counted round")
        want = sorted((s["path"], s["sha256"]) for s in named.rec.doc["subjects"])
        have = sorted((s["path"], s["sha256"]) for s in doc["subjects"])
        if want != have:
            return bad("stale conductor hash: the subjects differ from the round's reviewed subjects")
    else:
        if place != 2 or len(counted) != 2 or named is not counted[1]:
            return bad("an adjudicator record names the module's place-2 council record at place 3")
        if doc["recorder"] in conductor_recorders:
            return bad("conductor claiming adjudicator: the recorder also recorded a conductor refutation")
    findings = _all_findings(named.rec.doc)
    derived: dict[str, str] = {}
    for entry in doc["entries"]:
        fid = entry["finding_id"]
        if fid not in findings:
            return bad(f"entry names finding {fid}, which the council record does not carry")
        if fid in derived:
            return bad(f"two entries for finding {fid}")
        if derive_result(entry) != entry["result"]:
            return bad(f"entry {fid} records result {entry['result']} but its token derives "
                       f"{derive_result(entry)}")
        try:
            art_abs, _ = cr.resolve_in_repo(repo, entry["artifact"])
        except cr.PathRefused as exc:
            return bad(f"entry {fid} artifact {exc.message}")
        if not art_abs.is_file() or art_abs.is_symlink():
            return bad(f"entry {fid} artifact is not an existing regular file")
        derived[fid] = derive_result(entry)
    wholly = all(derived.get(fid) == "refuted" for fid in named.blocking)
    return Refutation(rec, True, None, named, wholly, role)


def _subject_changes(repo: Path, doc: dict) -> tuple[list[dict], list[dict]]:
    """The subjects whose current bytes differ from the recorded hash, and the subjects whose
    path the runner withheld. A withheld path names no file, so it is never reported changed."""
    out, withheld = [], []
    for i, s in enumerate(doc.get("subjects", [])):
        recorded = s.get("sha256")
        if recorded is None:
            continue
        if isinstance(s.get("path"), str) and s["path"].startswith(WITHHELD_PREFIX):
            withheld.append({"index": i, "recorded": recorded})
            continue
        try:
            absolute, _ = cr.resolve_in_repo(repo, s["path"])
        except cr.PathRefused:
            current = None
        else:
            current = cr.sha256_file(absolute) if absolute.is_file() and not absolute.is_symlink() else None
        if current != recorded:
            out.append({"path": s["path"], "recorded": recorded, "current": current})
    return out, withheld


def _attached(repo: Path, artifacts: list[str]) -> set[Path]:
    out: set[Path] = set()
    for a in artifacts:
        try:
            out.add(cr.resolve_in_repo(repo, a)[0])
        except cr.PathRefused:
            continue
    return out


def _scope(records: list[cr.Record], run: dict, module_tag: str | None,
           prompt_n: int) -> tuple[list[cr.Record], list[cr.Record], list[cr.Record]]:
    """The council records, owner exceptions and refutation records in scope for one gate: bound
    to this book and run, and to the module `module_tag`, or with no module tag (a patch book) to
    prompt `prompt_n`. A patch scope holds no refutation record."""
    in_run = [r for r in records if _bound(r.doc, run)]
    if module_tag is None:
        councils = [r for r in in_run if r.record_type == "council-record"
                    and r.doc["binding"]["module_tag"] is None and r.doc["binding"]["prompt"] == prompt_n]
        owners = [r for r in in_run if r.record_type == "owner-exception"
                  and r.doc["module_tag"] is None and r.doc["prompt"] == prompt_n]
        return councils, owners, []
    councils = [r for r in in_run if r.record_type == "council-record"
                and r.doc["binding"]["module_tag"] == module_tag]
    owners = [r for r in in_run if r.record_type == "owner-exception" and r.doc["module_tag"] == module_tag]
    refutes = [r for r in in_run if r.record_type == "refutation-record" and r.doc["module_tag"] == module_tag]
    return councils, owners, refutes


def _walk_places(ordered: list[cr.Record], repo: Path, verify_kind: bool, conductor_recorders: set[str],
                 counted: list[Round]):
    """Walk the in-scope records in order and assign each counted round its place.

    The one implementation of place counting: the gate check and the council runner's round
    check (`expected_round`) both use it. A council record with outcome `ran` takes the next
    place; a could-not-run record takes none; a valid adjudicator refutation record fills
    place 3. Yields ``(record, refutation, None)`` for a refutation record and
    ``(record, None, round)`` for a counted round, appending each round to `counted`."""
    place = 0
    for rec in ordered:
        if rec.record_type == "refutation-record":
            ref = _check_refutation(rec, repo, counted, place, conductor_recorders)
            if ref.valid and ref.role == "adjudicator":
                place = 3
            yield rec, ref, None
            continue
        if rec.doc["outcome"] != "ran":
            continue
        place += 1
        rnd = _analyze(rec, place, verify_kind)
        counted.append(rnd)
        yield rec, None, rnd


def _uncommitted_problem(repo: Path, in_scope: list[cr.Record]) -> str | None:
    """The first in-scope record that is not committed, or None. A record is committed when it is
    tracked and HEAD, the index and the working tree hold the same bytes."""
    for rec in in_scope:
        name = _rel(repo, rec.path)
        try:
            clean = cr.path_state(repo, name).clean
        except cr.PathRefused:
            clean = False
        if not clean:
            return (f"{name}: is not committed (HEAD, the index and the working tree must hold the same "
                    "bytes); commit the record before the advance")
    return None


def _removed_record_problem(repo: Path, run: dict, council_dir: Path) -> str | None:
    """The first record committed in HEAD under `council_dir`, bound to this run, that is missing
    from the working tree or differs from HEAD, or None. A record that was committed is never
    cleared by deleting or editing it: that would let an owner discard a held round."""
    try:
        council_rel = council_dir.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None
    listing = cr.git(repo, "ls-tree", "-z", "--name-only", "HEAD", council_rel + "/")
    if listing.returncode != 0:
        return None
    # os.fsdecode, never "replace": a replaced byte names no tree entry, so the record would be
    # skipped. The surrogate-escaped name encodes back to the same bytes for git.
    for name in sorted(n for n in (os.fsdecode(b) for b in listing.stdout.split(b"\x00"))
                       if n.endswith(".json")):
        blob = cr.git(repo, "cat-file", "blob", f"HEAD:{name}")
        if blob.returncode != 0:
            continue
        try:
            doc = json.loads(blob.stdout.decode("utf-8"))
        except ValueError:
            continue
        if not isinstance(doc, dict) or doc.get("record_type") not in cr.RECORD_TYPES or not _bound(doc, run):
            continue
        try:
            st = cr.path_state(repo, name)
        except cr.PathRefused:
            return f"{name}: a record committed in HEAD and bound to this run cannot be read in the working tree"
        if st.worktree != st.head:
            return (f"{name}: a record committed in HEAD and bound to this run is missing from the working "
                    "tree or differs from HEAD; a record is never cleared by deleting or editing it")
    return None


def _snapshot_named_problem(repo: Path, run: dict, council_dir: Path) -> str | None:
    """The first `.json` path under `council_dir` that a prompt's `artifacts` in the run snapshot
    names and the working tree lacks, or None."""
    council_abs = council_dir.resolve()
    for prompt in run.get("prompts") or []:
        arts = prompt.get("artifacts") if isinstance(prompt, dict) else None
        for a in arts if isinstance(arts, list) else []:
            if not isinstance(a, str) or not a.endswith(".json"):
                continue
            try:
                absolute, rel = cr.resolve_in_repo(repo, a)
                absolute.relative_to(council_abs)
            except (cr.PathRefused, ValueError):
                continue
            if not absolute.is_file():
                return (f"{rel}: the run snapshot names a record that is missing from the working tree; "
                        "a record is never cleared by deleting it")
    return None


def _evaluate_council_history(gate: GateClass, run: dict, run_path: Path | str, repo: Path,
                              artifacts: list[str], records: list[cr.Record], registry_loader) -> Verdict:
    """Shared policy; callers supply only their verified live or committed context.

    Historical callers must validate the context through implementation_approval first.
    This internal function is not an approval API accepting user-selected registries.
    """
    run_dir = Path(run_path).resolve().parent
    patch = gate.module_kind == "patch"
    councils, owners, refutes = _scope(records, run, gate.module_tag if not patch else None, gate.n)
    problem = (_uncommitted_problem(repo, councils + owners + refutes)
               or _removed_record_problem(repo, run, run_dir / "council")
               or _snapshot_named_problem(repo, run, run_dir / "council"))
    if problem:
        return _refuse(problem)
    if gate.module_kind != "adr" and refutes:
        return _refuse("a refutation record is in scope, but only an adr module closes on one: "
                       + ", ".join(_rel(repo, r.path) for r in refutes))
    # Invariant the kind check below (S2) relies on: the runner writes the book's cycle_kind into
    # each record's `council_kind`, and the gate takes `gate.module_kind` from the module-tag
    # prefix. They agree because validate-promptbook enforces that a book is adr or verify, never
    # both; a mixed book would disagree here and fail closed.
    for r in councils:
        if r.doc.get("council_kind") != gate.module_kind:
            return _refuse(f"{_rel(repo, r.path)}: council_kind {r.doc.get('council_kind')!r} is not the "
                           f"kind of this gate ({gate.module_kind}); a council of another kind never "
                           "satisfies it")
        if r.doc["binding"]["prompt"] not in gate.module_prompts:
            return _refuse(f"{_rel(repo, r.path)}: is bound to prompt {r.doc['binding']['prompt']}, "
                           "outside this module's prompts")
    if not councils:
        return _refuse("no council record naming run-council.py as its writer (a self-asserted field) is in "
                       "scope for this book, run and module; a module whose council ran before records "
                       "existed closes only on a round convened at module close with run-council.py")

    # Shape checks run on every counted record. The registry check runs on the record that decides
    # (below): an earlier round can only add a stop or a hold, never a pass, so checking it against
    # a registry that has since rotated would strand the module with no seat substituted.
    for r in councils:
        if r.doc["outcome"] == "ran":
            problem = admissibility_problem(r.doc)
            if problem:
                return _refuse(f"{_rel(repo, r.path)}: {problem}")

    ordered = sorted(councils + refutes, key=lambda r: r.doc["written_at"])
    for a, b in zip(ordered, ordered[1:]):
        if a.doc["written_at"] == b.doc["written_at"]:
            attached = _attached(repo, artifacts)
            missing = [_rel(repo, r.path) for r in (a, b) if r.path.resolve() not in attached]
            if missing:
                return _refuse("record order is a tie, and a stop needs its tied records attached; "
                               "not attached: " + ", ".join(missing))
            return Verdict("stop", [1], [f"records {_rel(repo, a.path)} and {_rel(repo, b.path)} carry "
                                         f"the same written_at; record order is a tie"])

    stops: set[int] = set()
    reasons: list[str] = []
    holds: list[str] = []
    verify_kind = gate.module_kind in ("verify", "implementation") or (patch and run.get("format_version") == "2")
    adr = gate.module_kind == "adr"
    conductor_recorders = {r.doc["recorder"] for r in refutes if r.doc["recorder_role"] == "conductor"}

    def authorized(kind: str, rnd: int) -> bool:
        return any(o.doc["place"]["kind"] == kind and o.doc["place"]["round"] == rnd for o in owners)

    counted: list[Round] = []
    refutations: dict[Path, Refutation] = {}
    for rec, ref, rnd in _walk_places(ordered, repo, verify_kind, conductor_recorders, counted):
        name = _rel(repo, rec.path)
        if ref is not None:
            refutations[rec.path] = ref
            if not ref.valid:
                reasons.append(f"{name} is not admitted: {ref.reason}")
            continue
        place = rnd.place
        if rec.doc["round"] != place:
            stops.add(1)
            reasons.append(f"{name} is numbered round {rec.doc['round']} but takes place {place}")
        if adr and place == 3:
            rnd.authorized = authorized("adr-round-3-council", 3)
            if not rnd.authorized:
                stops.add(1)
                reasons.append(f"{name} is a third adr council; no owner exception authorizes it")
        elif place >= 4:
            rnd.authorized = authorized("round-above-three", place)
            if not rnd.authorized:
                stops.add(1)
                reasons.append(f"{name} takes place {place}, above the round bound; "
                               f"no owner exception authorizes it")
        if rec.doc.get("refusal_reason") is not None:
            stops.add(4)
            reasons.append(f"{name} ran but its full write was refused ({rec.doc['refusal_reason']['code']})")
        if rnd.held:
            stops.add(1)
            holds.extend(f"{name}:{role}" for role in rnd.held)
            reasons.append(f"{name} holds: {', '.join(rnd.held)} neither approved nor named a blocking finding")
        if rnd.architectural:
            stops.add(4)
            reasons.append(f"{name} carries an ARCHITECTURAL vote, which contradicts the premise")

    deciding = ordered[-1]
    deciding_name = _rel(repo, deciding.path)
    if deciding.path.resolve() not in _attached(repo, artifacts):
        return _refuse(f"the deciding record {deciding_name} is not among the attached artifacts",
                       deciding=deciding_name)
    ref = refutations.get(deciding.path)
    if ref is not None and not ref.valid:
        return _refuse(f"{deciding_name}: {ref.reason}", deciding=deciding_name)

    # The registry check: the deciding council record, or the council record a deciding refutation
    # names, must seat every seat on the CURRENT registry assignment of its role.
    seated = (deciding if deciding.record_type == "council-record" and deciding.doc["outcome"] == "ran"
              else ref.named.rec if ref is not None and ref.named is not None else None)
    if seated is not None:
        registry, why = registry_loader()
        if registry is None:
            return _refuse(f"{_rel(repo, seated.path)}: {why}", deciding=deciding_name)
        problem = admissibility_problem(seated.doc, registry)
        if problem:
            return _refuse(f"{_rel(repo, seated.path)}: {problem}", deciding=deciding_name)

    subject_doc = None
    pass_candidate = False
    route_here = False
    route_to = None
    if deciding.record_type == "council-record":
        if deciding.doc["outcome"] != "ran":
            stops.add(4)
            reasons.append(f"the council could not run ({deciding_name}: "
                           f"{deciding.doc['refusal_reason']['code']}); defer to the owner")
        else:
            subject_doc = deciding.doc
            latest = counted[-1]
            if latest.converged:
                pass_candidate = True
            else:
                route_here = True
    else:
        subject_doc = ref.named.rec.doc
        latest = counted[-1]
        if ref.role == "conductor":
            if ref.wholly_refuted and ref.named.place <= 2:
                pass_candidate = True
            else:
                route_here = True
        elif ref.wholly_refuted:
            pass_candidate = True
        else:
            stops.add(1)
            reasons.append(f"{deciding_name} leaves a blocking finding confirmed, inconclusive or unchecked")
    if route_here and not stops:
        if latest.place <= 2:
            route_to = gate.n if patch else gate.ordinal3
        else:
            route_to = None
            stops.add(1)
            reasons.append(f"the round bound is spent: {_rel(repo, latest.rec.path)} at place "
                           f"{latest.place} did not converge")

    changed, withheld = _subject_changes(repo, subject_doc) if subject_doc is not None else ([], [])
    if stops:
        return Verdict("stop", sorted(stops), reasons, deciding_name, None, holds, changed, withheld)
    if pass_candidate:
        return Verdict("pass", [], reasons + [f"{deciding_name} decides the gate"], deciding_name, None,
                       holds, changed, withheld)
    return Verdict("route", [], reasons + [f"{deciding_name} is not converged; another round is due"],
                   deciding_name, route_to, holds, changed, withheld)
