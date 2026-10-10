"""Current council policy: attempt-aware, with records ordered by committed history.

Profile three orders records by their wall-clock stamp. This module orders them by the commits
that introduced them, and owns context format three paired with contract version four.

This module is a copy of council_history_v3 with the order changed, not an edit of it. That
module is profile three's frozen kernel: gate_contract_bytes("3") hashes it, so it stays
byte-unchanged, and its docstring still calls it current. Never fold a fix made here back into
it; an approval issued under profile three must replay against the bytes it was issued with."""
from __future__ import annotations
import errno
import json
import os
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import council_records as cr
import council_yaml_v3 as start_snapshot_yaml

VERSION = "4"
PREFLIGHT_RETRIES_SPENT = "preflight-retries-spent"
APPROVING = ("APPROVE", "APPROVE_WITH_NITS")
SEAT_ROLES = cr.SEAT_ROLES
WITHHELD_PREFIX = "[withheld:"
_RUN_WORK_BASE = re.compile(r"[0-9a-f]{7,64}")
_COMMIT = re.compile(r"[0-9a-f]{7,64}")
_ATTEMPT_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "council-attempt.schema.json"
schema_engine = cr._validator()

def discover_records(run_dir):
    return cr.discover_records(run_dir)


def _proof_require(condition, code):
    if not condition:
        raise cr.RecordError("retained-approval", code)


def _proof_ref(item):
    _proof_require(isinstance(item, dict) and set(item) == {"path", "sha256"} and
        isinstance(item["path"], str) and bool(item["path"]) and
        not Path(item["path"]).is_absolute() and ".." not in Path(item["path"]).parts and
        isinstance(item["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]),
        "context-reference-refused")


def validate_context(context):
    """Own the closed context-three grammar paired exclusively with profile four."""
    _proof_require(isinstance(context, dict) and set(context) == {
        "record_type", "format_version", "contract", "registry", "records", "artifacts", "start_identity"},
        "context-shape-refused")
    contract = context["contract"]
    _proof_require(context["record_type"] == "implementation-gate-context" and
        context["format_version"] == "3" and isinstance(contract, dict) and
        set(contract) == {"version", "path", "sha256"} and contract["version"] == VERSION,
        "context-version-unsupported")
    _proof_ref({k: contract[k] for k in ("path", "sha256")})
    _proof_ref(context["registry"])
    _proof_require(isinstance(context["records"], list), "context-records-refused")
    for item in context["records"]:
        _proof_ref(item)
    _proof_require(len({item["path"] for item in context["records"]}) == len(context["records"]),
                   "context-records-refused")
    _proof_require(isinstance(context["artifacts"], list) and all(isinstance(path, str) and path and
        not Path(path).is_absolute() and ".." not in Path(path).parts for path in context["artifacts"]),
        "context-artifacts-refused")
    identity = context["start_identity"]
    _proof_require(isinstance(identity, dict) and set(identity) == {"base_commit", "commit", "run"} and
        all(isinstance(identity[k], str) and _COMMIT.fullmatch(identity[k]) for k in ("base_commit", "commit")),
        "context-start-identity-refused")
    _proof_ref(identity["run"])


def retained_path(repo, run_path, given):
    """Map the runner namespace deterministically into the exact owning run."""
    _proof_require(isinstance(given, str), "retained-path-refused")
    run_dir, run_rel = cr.resolve_in_repo(repo, Path(run_path).parent)
    prefix = "council/subjects/"
    _proof_require(given.startswith(prefix) or given.startswith(run_rel + "/" + prefix), "retained-path-refused")
    base = run_dir if given.startswith(prefix) else repo
    raw = base / given
    _proof_require(".." not in raw.parts and not cr.reached_through_symlink(raw), "retained-path-refused")
    absolute, rel = cr.resolve_in_repo(repo, raw)
    _proof_require(absolute.is_relative_to(run_dir / "council/subjects"), "retained-path-refused")
    return rel


def context_records(records, run, gate):
    """Include every scoped council, exception, refutation and attempt."""
    scoped = sum((list(group) for group in _scope(records, run, gate.module_tag, gate.n)), [])
    return scoped + [record for record in records if record.record_type == "council-attempt" and
                     _in_attempt_scope(record.doc, run, gate.module_tag, gate.n)]


def deciding_subject(repo, run_path, deciding, revision):
    """One exact deciding subject supplies the normalized retained-copy reference."""
    _proof_require(isinstance(deciding, dict) and deciding.get("record_type") == "council-record" and
        deciding.get("outcome") == "ran", "deciding-council-required")
    _proof_ref(revision)
    subjects = [item for item in deciding.get("subjects", []) if isinstance(item, dict) and
                item.get("path") == revision["path"] and item.get("sha256") == revision["sha256"]]
    _proof_require(len(subjects) == 1, "deciding-revision-missing")
    return {"path": retained_path(repo, run_path, subjects[0].get("retained_copy")),
            "sha256": revision["sha256"]}


def start_identity(repo, run, run_path):
    """Derive the first committed start claim from isolated reachable history."""
    _, rel = cr.resolve_in_repo(repo, run_path)
    shallow = cr.git(repo, "rev-parse", "--is-shallow-repository")
    history = cr.git(repo, "log", "--reverse", "--no-renames", "--diff-filter=d", "--format=%H", "--", rel)
    if shallow.returncode or shallow.stdout.strip() != b"false" or history.returncode:
        raise cr.RecordError(run_path, "the run-start history is unavailable")
    for commit in history.stdout.decode().split():
        blob = cr.git(repo, "cat-file", "blob", commit + ":" + rel)
        if blob.returncode:
            raise cr.RecordError(run_path, "the run-start snapshot is unavailable")
        try:
            snapshot = start_snapshot_yaml.load_yaml(blob.stdout.decode())
        except Exception:
            raise cr.RecordError(run_path, "the run-start snapshot is unreadable") from None
        if not isinstance(snapshot, dict):
            raise cr.RecordError(run_path, "the run-start snapshot is malformed")
        if any(snapshot.get(key) != run.get(key) for key in ("book_id", "run_id", "book_content_hash")):
            continue
        if "base_commit" not in snapshot:
            continue
        base = snapshot["base_commit"]
        if not isinstance(base, str) or not _COMMIT.fullmatch(base) or base != run.get("base_commit") or \
                cr.git(repo, "cat-file", "-e", base + "^{commit}").returncode:
            raise cr.RecordError(run_path, "the run-start base is unknown or rewritten")
        return {"base_commit": base, "commit": commit,
                "run": {"path": rel, "sha256": cr.sha256_bytes(blob.stdout)}}
    raise cr.RecordError(run_path, "no committed run-start identity is available")

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


# ───────────────────────────── committed order ─────────────────────────────

# A delete entry's new object id is all zeros: 40 digits under SHA-1, 64 under SHA-256.
_NULL_OID = re.compile(r"0{40}|0{64}")
# A full object id: 40 hex digits under SHA-1, 64 under SHA-256.
_FULL_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_PERMANENT = "this stop is permanent for the module: abandon the run and author a successor book"
_INCOMPLETE = "the commit history is shallow or unavailable, so record order is unreadable; fetch the complete " \
              "history (git fetch --unshallow) and advance again"
_MISSING = "a history object of the council directory is missing from the local object store, as in a " \
           "partial clone, so record order is unreadable; refetch the full history without a filter " \
           "(git fetch --refetch --no-filter) and advance again"


class OrderError(cr.RecordError):
    """The committed history cannot order a gate's records. The gate stops at 4.

    A record of the run removed from the history, or a path with no single introducing commit, is
    permanent for the module: no later commit restores what the history held. A shallow history, a
    grafts file, or a history object the local object store lacks clears once the history is
    complete, and the message names that remedy."""


@dataclass
class _PathHistory:
    entries: list = field(default_factory=list)  # (commit, status, new oid), newest first


@dataclass
class CommittedOrder:
    """The order of one gate's council and refutation records, read from committed history.

    `commit` maps a record's repo-relative path to the one non-merge commit that introduced its
    current bytes. `stamp` maps it to its `written_at`. `rtype` maps it to its record type.
    `before(a, b)` is true when a's commit is a strict ancestor of b's. Two records on one commit,
    or on commits with no ancestry between them, are a tie.

    Ancestry is read from `_parents`, the unrewritten graph of the region between the listed
    commits (every commit of a record path's history) and their common ancestors, or every ancestor
    of the listed commits when they share none (`committed_order`). The answers are exact for any
    pair of listed commits. A query naming a commit that is neither listed nor a common ancestor
    refuses, except that any commit is its own ancestor. No caller asks about a common ancestor. No
    ancestry question starts another git process.
    `_parents` maps each region commit to its true parents; a commit at the region's edge has none.
    `_masks` maps each tracked commit (`_tracked`: the listed commits and the common ancestors) to
    a bitmask of the tracked commits that are it or its ancestors. `committed_order` builds the masks
    once, before it returns, so a self-check refusal surfaces there."""

    commit: dict = field(default_factory=dict)
    stamp: dict = field(default_factory=dict)
    rtype: dict = field(default_factory=dict)
    named: dict = field(default_factory=dict)  # refutation path -> council record path it names
    _parents: dict = field(default_factory=dict)  # region commit -> its true parents (edge commits: none)
    _tracked: tuple = ()  # the listed commits and the common ancestors, sorted
    _listed: frozenset = frozenset()  # the commits of every record path's history
    _bases: tuple = ()  # the common ancestors of the listed commits that bound the region
    _masks: dict = field(default_factory=dict)  # tracked commit -> bitmask of tracked ancestors-or-self
    _bit: dict = field(default_factory=dict)  # tracked commit -> its bit position
    _repo: Path | None = None

    def _build(self) -> None:
        """Fold the region graph into one bitmask per tracked commit, in one pass over the region.

        An iterative post-order walk (a deep linear history never recurses) gives each region commit
        the union of its parents' masks and its own bit. The per-commit working masks are dropped
        after, and only the tracked commits' masks stay. A tracked commit absent from the region, a
        parent absent from it, or a common ancestor some listed commit does not reach, refuses."""
        if self._masks or not self._tracked:
            return
        failed = OrderError("<history>", "an ancestry read failed, so the committed order is unavailable")
        parents = self._parents
        bit = {c: i for i, c in enumerate(self._tracked)}
        work: dict[str, int] = {}
        budget = 2 * (len(parents) + sum(len(p) for p in parents.values())) + len(self._tracked) + 1
        for start in self._tracked:
            stack = [start]
            while stack:
                budget -= 1
                if budget < 0:
                    raise failed
                c = stack[-1]
                if c in work:
                    stack.pop()
                    continue
                if c not in parents:
                    raise failed
                pending = [p for p in parents[c] if p not in work]
                if pending:
                    stack.extend(pending)
                    continue
                mask = (1 << bit[c]) if c in bit else 0
                for p in parents[c]:
                    mask |= work[p]
                work[c] = mask
                stack.pop()
        masks = {c: work[c] for c in self._tracked}
        listed = [c for c in self._tracked if c in self._listed]
        for base in self._bases:
            if not all(masks[c] >> bit[base] & 1 for c in listed):
                raise failed
        self._bit = bit
        self._masks = masks

    def is_ancestor(self, older: str, newer: str) -> bool:
        """True when `older` is `newer` or one of its ancestors in the unrewritten commit graph, as
        `git merge-base --is-ancestor` answers over the same graph. Both commits must be listed or
        common ancestors of the listed commits; any other commit refuses, except that a commit equals
        itself."""
        if older == newer:
            return True
        self._build()
        if older not in self._bit or newer not in self._bit:
            raise OrderError("<history>", "an ancestry read failed, so the committed order is unavailable")
        return bool(self._masks[newer] >> self._bit[older] & 1)

    def before(self, a: str, b: str) -> bool:
        ca, cb = self.commit.get(a), self.commit.get(b)
        return ca is not None and cb is not None and ca != cb and self.is_ancestor(ca, cb)

    def tied(self, a: str, b: str) -> bool:
        ca, cb = self.commit.get(a), self.commit.get(b)
        if ca is None or cb is None:
            return False
        return ca == cb or not (self.is_ancestor(ca, cb) or self.is_ancestor(cb, ca))

    def sort(self, paths: list[str]) -> list[str]:
        """`paths` in committed order. A path with no committed place is later than every committed
        one. Tied paths keep a fixed order (stamp, then path) that no verdict relies on."""
        committed = [p for p in paths if p in self.commit]
        distinct = sorted(set(self.commit[p] for p in committed))
        depth = {c: sum(1 for o in distinct if o != c and self.is_ancestor(o, c)) for c in distinct}
        committed.sort(key=lambda p: (depth[self.commit[p]], self.stamp.get(p, ""), p))
        loose = sorted((p for p in paths if p not in self.commit), key=lambda p: (self.stamp.get(p, ""), p))
        return committed + loose

    def contradictions(self) -> list[tuple[str, str]]:
        """Every ordered pair whose stamps run against the committed order. Stamps compare as text:
        the record schemas fix one UTC form, so text order is time order."""
        paths = self.sort(list(self.commit))
        return [(a, b) for i, a in enumerate(paths) for b in paths[i + 1:]
                if self.before(a, b) and self.stamp[a] > self.stamp[b]]

    def descent_problems(self) -> list[str]:
        """Refutation records whose commit does not descend from the council record they name."""
        out = []
        for path, council in self.named.items():
            if council not in self.commit:
                continue
            if self.commit[path] != self.commit[council] and not self.before(council, path):
                out.append(f"{path}: the refutation record was not committed after the council record it "
                           f"names ({council}); {_PERMANENT}")
        return out


def _order_env() -> dict[str, str]:
    """The isolated git environment, with lazy fetching off. An order read never reaches a promisor
    remote: a history object the local store lacks fails the read, and the order refuses."""
    env = cr._git_env()
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def _order_git(repo: Path, *args: str, stdin: bytes | None = None):
    """A git read for the order. Each `-c` pin outranks repository-local configuration for a key that
    would change what the read prints: a log option, the log's output encoding (the `%x01%H` headers
    parse as ASCII), or the commit-graph file, whose parent data git would trust over the commit
    objects. A key no pin names is not covered; a configuration that breaks the parse refuses."""
    return subprocess.run(["git", "-C", str(repo), "-c", "log.showSignature=false", "-c", "log.follow=false",
                           "-c", "diff.renames=false", "-c", "log.diffMerges=off", "-c", "log.showRoot=true",
                           "-c", "i18n.logOutputEncoding=UTF-8", "-c", "core.commitGraph=false", *args],
                          input=stdin, capture_output=True, env=_order_env())


def _parse_history(data: bytes) -> dict[str, _PathHistory]:
    """Each path's raw entries, from `git log -z --raw --format=%x01%H` output.

    The log carries no `--parents`, so git rewrites no parent and diffs each non-merge commit
    against its true parent. The parse is positional. A header token opens a commit, and a raw
    entry's metadata token is always followed by exactly one path token, which is never read as a
    header. A path may hold any byte but NUL, so splitting the output on the header byte would let a
    file name forge a commit. A header holds one full object id, and no commit repeats."""
    histories: dict[str, _PathHistory] = {}
    seen: set[str] = set()
    tokens = data.split(b"\x00")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    commit = None
    i = 0
    while i < len(tokens):
        token = tokens[i].lstrip(b"\n")
        if token.startswith(b"\x01"):
            ids = token[1:].decode("ascii", "replace").split()
            if len(ids) != 1 or not _FULL_OID.fullmatch(ids[0]) or ids[0] in seen:
                raise OrderError("<history>", "the council directory history cannot be parsed")
            commit = ids[0]
            seen.add(commit)
            i += 1
            continue
        if commit is None or not token.startswith(b":") or i + 1 >= len(tokens):
            raise OrderError("<history>", "the council directory history cannot be parsed")
        fields = token[1:].decode("ascii", "replace").split(" ")
        # --no-renames leaves one path per entry; a rename or copy status would carry two.
        if len(fields) != 5 or fields[4] not in ("A", "D", "M", "T"):
            raise OrderError("<history>", "the council directory history cannot be parsed")
        histories.setdefault(os.fsdecode(tokens[i + 1]), _PathHistory()).entries.append(
            (commit, fields[4], fields[3]))
        i += 2
    return histories


def _parse_region(data: bytes) -> dict[str, tuple[str, ...]]:
    """The region's commits from `git rev-list --parents --boundary` output.

    A line is full object ids only, its first the commit. A boundary line starts `-`; the commit is
    excluded from the region and enters with no parents, so it ends every walk. A malformed line or
    a repeated commit refuses."""
    graph: dict[str, tuple[str, ...]] = {}
    for line in data.decode("ascii", "replace").split("\n"):
        if not line:
            continue
        edge = line.startswith("-")
        ids = (line[1:] if edge else line).split(" ")
        if not all(_FULL_OID.fullmatch(x) for x in ids) or ids[0] in graph:
            raise OrderError("<history>", "the commit graph cannot be parsed")
        graph[ids[0]] = () if edge else tuple(ids[1:])
    if data and not data.endswith(b"\n"):
        raise OrderError("<history>", "the commit graph cannot be parsed")
    return graph


def _read_region(repo: Path, listed: list[str]) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """The unrewritten commit graph between `listed` and their common ancestors, and those ancestors.

    `git merge-base --octopus --all` names the common ancestors M of the listed commits (exit 1 with
    no output when they share none; M is then empty). `git rev-list --parents --boundary --stdin`
    reads the listed commits with `^m` for each m in M: no path limit and no history simplification,
    so every parent is true. A boundary commit enters the graph with no parents.

    Why the region answers exactly. Every m in M is an ancestor of every listed commit, so a listed
    commit at or below m is m itself. A commit strictly between two listed commits x (older) and y
    on any path is a strict descendant of x, so it is no ancestor of any m and lies in the region
    with its true parents, and x lies in the region or is a boundary commit. Ancestry between listed
    commits inside the region is then the ancestry of the whole history. The reads below do not
    trust that argument or merge-base: each listed commit must be a region commit (or an m), each
    parent must be a region commit, and each listed commit must reach each m in the region
    (`CommittedOrder._build`)."""
    refused = OrderError("<history>", "the council directory history cannot be read, as in a partial clone "
                                      "that lacks history objects; refetch the full history without a filter "
                                      "(git fetch --refetch --no-filter) and advance again")
    if not listed:
        return {}, ()
    found = _order_git(repo, "merge-base", "--octopus", "--all", "--", *listed)
    out = found.stdout.decode("ascii", "replace")
    if found.returncode == 0:
        bases = tuple(sorted(out.split("\n")[:-1])) if out.endswith("\n") else None
        if not bases or not all(_FULL_OID.fullmatch(b) for b in bases) or len(set(bases)) != len(bases):
            raise refused
    elif found.returncode == 1 and not found.stdout:
        bases = ()
    else:
        raise refused
    revs = "".join(c + "\n" for c in listed) + "".join("^" + b + "\n" for b in bases)
    # The revisions travel on stdin, which git never reads as a path; the closing `--` keeps any
    # argv revision a later edit adds from colliding with a working-tree file of the same name.
    read = _order_git(repo, "rev-list", "--parents", "--boundary", "--stdin", "--", stdin=revs.encode("ascii"))
    if read.returncode:
        raise refused
    graph = _parse_region(read.stdout)
    for commit in (*listed, *bases):
        if commit not in graph:
            if commit not in bases or commit not in listed:
                raise OrderError("<history>", "an ancestry read failed, so the committed order is unavailable")
            graph[commit] = ()  # a listed common ancestor no region commit has as a parent
    for commit, parents in graph.items():
        if not all(p in graph for p in parents):
            raise OrderError("<history>", "an ancestry read failed, so the committed order is unavailable")
    return graph, bases


def _read_blobs(repo: Path, oids: list[str], where: str) -> dict[str, bytes]:
    """Every named blob, read in one `git cat-file --batch`. A blob the local object store lacks
    refuses: an unread record would otherwise drop out of the order unseen."""
    if not oids:
        return {}
    r = _order_git(repo, "cat-file", "--batch", stdin="".join(o + "\n" for o in oids).encode("ascii"))
    data = r.stdout
    out: dict[str, bytes] = {}
    pos = 0
    for oid in oids:
        end = data.find(b"\n", pos)
        header = data[pos:end].split(b" ") if end >= 0 else []
        if (r.returncode or len(header) != 3 or header[0] != oid.encode("ascii") or header[1] != b"blob"
                or not header[2].isdigit()):
            raise OrderError(where, _MISSING)
        start = end + 1
        stop = start + int(header[2])
        if data[stop:stop + 1] != b"\n":
            raise OrderError(where, _MISSING)
        out[oid] = data[start:stop]
        pos = stop + 1
    if pos != len(data):
        raise OrderError(where, _MISSING)
    return out


def _of_run(doc: Any, run: dict) -> str | None:
    """The record type when `doc` is a council or refutation record bound to this run, in any scope."""
    if isinstance(doc, dict) and _bound(doc, run) and doc.get("record_type") in ("council-record",
                                                                                   "refutation-record"):
        return doc["record_type"]
    return None


def _applies(doc: Any, run: dict, module_tag: str | None, prompt_n: int) -> str | None:
    """The record type when `doc` is a council or refutation record bound to this run and in this
    gate's scope (`_scope`), else None."""
    if not isinstance(doc, dict) or not _bound(doc, run):
        return None
    kind = doc.get("record_type")
    try:
        if kind == "council-record":
            binding = doc["binding"]
            if module_tag is None:
                return kind if binding["module_tag"] is None and binding["prompt"] == prompt_n else None
            return kind if binding["module_tag"] == module_tag else None
        if kind == "refutation-record" and module_tag is not None:
            return kind if doc["module_tag"] == module_tag else None
    except (KeyError, TypeError):
        return None
    return None


def committed_order(run: dict, run_dir: Path | str, repo: Path | str, module_tag: str | None,
                    prompt_n: int, rev: str = "HEAD") -> CommittedOrder:
    """Order the council and refutation records of one gate scope by the commits that introduced them.

    Every path under `<run_dir>/council/` that any commit reachable from `rev` held as a council or
    refutation record of this run, in any module, takes part in two checks: a participating path
    absent at `rev` refuses, and so does one with no single introducing commit. The checks are
    run-wide so that removing a record never clears a stop, whichever scope the record served. The
    order itself holds only the records of this gate's scope (`_applies`).

    The introducing commit is the one non-merge commit that gives the path its bytes at `rev`; merge
    diffs are not read. A council record whose current bytes are format 2 with a holding seal may
    have earlier blobs, each with a failing seal and each on a strict ancestor of the introducing
    commit; any other earlier blob, second add or delete refuses. A shallow history, a grafts file
    and a history object the local store lacks refuse as well. Every refusal is an `OrderError`.

    The history log carries no `--parents`, so no parent is rewritten and each commit is diffed
    against its true parent. The log walk still visits every commit reachable from `rev`, so each
    record's add and remove is seen. Ancestry is read from the unrewritten graph of the region
    between the commits the log lists and their common ancestors (`_read_region`), or from every
    ancestor of the listed commits when they share none. The ancestry read holds the region's
    commits, plus one bitmask per region commit over the listed commits and common ancestors while
    it builds. The log walk's time stays linear in every commit reachable from `rev`. Ancestry
    answers are exact for any pair of listed commits; a question naming an untracked commit
    refuses."""
    repo = Path(repo).resolve()
    council_rel = (Path(run_dir).resolve() / "council").relative_to(repo).as_posix()
    head = _order_git(repo, "rev-parse", "--is-shallow-repository", "--git-common-dir", "--verify", "--quiet",
                      rev + "^{commit}")
    lines = head.stdout.decode("utf-8", "replace").splitlines() if head.returncode == 0 else []
    if len(lines) != 3 or lines[0] != "false" or not _FULL_OID.fullmatch(lines[2]):
        raise OrderError(council_rel, _INCOMPLETE)
    common, sha = lines[1], lines[2]
    if (repo / common / "info" / "grafts").exists():
        raise OrderError(council_rel, "the repository holds a grafts file, which rewrites history; remove the "
                                      "grafts file and advance again")
    log = _order_git(repo, "log", "--full-history", "--no-renames", "--diff-merges=off",
                     "--no-show-signature", "--no-color", "--raw", "--no-abbrev", "-z", "--format=%x01%H",
                     sha, "--", council_rel + "/")
    tree = _order_git(repo, "ls-tree", "-r", "-z", sha, "--", council_rel + "/")
    if log.returncode or tree.returncode:
        raise OrderError(council_rel, "the council directory history cannot be read, as in a partial clone "
                                      "that lacks history objects; refetch the full history without a filter "
                                      "(git fetch --refetch --no-filter) and advance again")
    history = _parse_history(log.stdout)
    current: dict[str, str] = {}
    for line in tree.stdout.split(b"\x00"):
        if not line:
            continue
        meta, _, name = line.partition(b"\t")
        fields = meta.split(b" ")
        if len(fields) == 3 and fields[1] == b"blob":
            current[os.fsdecode(name)] = fields[2].decode("ascii", "replace")
    paths = sorted(p for p in set(history) | set(current) if p.endswith(".json"))
    held: dict[str, set[str]] = {}
    for path in paths:
        held[path] = {oid for _, _, oid in history.get(path, _PathHistory()).entries
                      if not _NULL_OID.fullmatch(oid)}
        if path in current:
            held[path].add(current[path])
    if not all(_FULL_OID.fullmatch(oid) for oids in held.values() for oid in oids):
        raise OrderError(council_rel, "the council directory history cannot be parsed")
    blobs = _read_blobs(repo, sorted(set().union(*held.values())), council_rel)
    # Every commit an ancestry question names is the introducing commit of a record path or an
    # earlier blob's commit at one, so it is in this list.
    listed = sorted({c for p in paths for c, _, _ in history.get(p, _PathHistory()).entries})
    parents, bases = _read_region(repo, listed)

    def doc_of(oid: str):
        try:
            return json.loads(blobs[oid].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None

    order = CommittedOrder(_parents=parents, _tracked=tuple(sorted(set(listed) | set(bases))),
                           _listed=frozenset(listed), _bases=bases, _repo=repo)
    for path in paths:
        if not any(_of_run(doc_of(oid), run) for oid in sorted(held[path])):
            continue
        if path not in current:
            raise OrderError(path, "a record of this run was removed from the committed history, and deleting a "
                                   f"record never clears a stop; {_PERMANENT}")
        cur = current[path]
        doc = doc_of(cur)
        kind = _of_run(doc, run)
        stamp = doc.get("written_at") if isinstance(doc, dict) else None
        if kind is None or not isinstance(stamp, str):
            raise OrderError(path, "the record's current bytes are not a council or refutation record of this run "
                                   f"with a written_at, so it cannot be ordered; {_PERMANENT}")
        entries = history.get(path, _PathHistory()).entries
        mine = [e for e in entries if e[1] in ("A", "M") and e[2] == cur]
        if len(mine) != 1:
            raise OrderError(path, "no single commit introduced the record's current bytes; the history "
                                   f"holds {len(mine)} commits that give the path them; {_PERMANENT}")
        others = [e for e in entries if e is not mine[0]]
        intro = mine[0][0]
        if not others:
            if mine[0][1] != "A":
                raise OrderError(path, "no single commit introduced the record's current bytes; the path "
                                       f"was changed, not added; {_PERMANENT}")
        else:
            # The hook-altered carve-out. Recovery's --owner-commit-pending remedy writes exactly this
            # history over a record a hook altered: failed-seal blobs first, the sealed bytes last. A
            # blob whose seal fails was never evidence, so it takes no place in the order.
            hook_altered = (kind == "council-record" and doc.get("format_version") == "2"
                            and cr.seal_holds(blobs[cur]))
            if not hook_altered:
                raise OrderError(path, "no single commit introduced the record's current bytes; the path "
                                       f"has earlier history; {_PERMANENT}")
            for commit, status, oid in others:
                earlier = blobs.get(oid) if status in ("A", "M") else None
                try:
                    sealed = earlier is not None and cr.seal_holds(earlier)
                except RecursionError:
                    raise OrderError(path, "no single commit introduced the record's current bytes; an earlier "
                                           "blob at the path is nested too deeply to read, so it cannot be "
                                           f"shown to be a failed-seal copy; {_PERMANENT}") from None
                if status not in ("A", "M") or earlier is None or sealed:
                    raise OrderError(path, "no single commit introduced the record's current bytes; an earlier "
                                           f"blob or a delete at the path is not a failed-seal copy; {_PERMANENT}")
                if commit == intro or not order.is_ancestor(commit, intro):
                    raise OrderError(path, "no single commit introduced the record's current bytes; an earlier "
                                           f"blob does not precede the sealed bytes; {_PERMANENT}")
        if _applies(doc, run, module_tag, prompt_n) is None:
            continue
        order.commit[path] = intro
        order.stamp[path] = stamp
        order.rtype[path] = kind
        if kind == "refutation-record":
            named = doc.get("council_record")
            if isinstance(named, dict) and isinstance(named.get("path"), str):
                order.named[path] = named["path"]
    order._build()
    return order


def _ordered_records(order: CommittedOrder, repo: Path, records: list[cr.Record]) -> list[cr.Record]:
    by_rel = {_rel(repo, r.path): r for r in records}
    return [by_rel[p] for p in order.sort(list(by_rel))]


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


def expected_round(run: dict, run_dir: Path | str, repo: Path, module_tag: str | None,
                   prompt_n: int, rev: str = "HEAD") -> int:
    """The round number the next council of this module must carry: the places its counted
    rounds already take, plus one. Counted the way the gate check counts (`_walk_places` over
    `_evidence_councils`), so a could-not-run record takes no place, a valid adjudicator refutation
    record fills place 3, and a format-2 council record that does not resolve the attempt it names
    (a failed seal, bytes that differ from its pending copy, a binding that differs from the
    attempt's) takes no place. `run_dir` is the directory holding the run snapshot and its
    `council/` directory. `module_tag` None scopes a patch book's council to prompt `prompt_n`.

    A format-1 council record is counted here even in a run where the gate refuses it as
    inadmissible: the gate then refuses the whole scope, so there is no place count to agree with.

    Records are ordered by the commits that introduced them (`committed_order`); a record not yet
    committed is later than every committed one. A tie or a stamp that contradicts the committed
    order raises nothing here: the gate stops on it.

    Raises `council_records.RecordError` when a record under the council directory or a pending
    copy cannot be read, or `OrderError` when the committed history cannot order the records, as
    the gate check would refuse it."""
    run_dir = Path(run_dir).resolve()
    records = cr.discover_records(run_dir)
    councils, _, refutes = _scope(records, run, module_tag, prompt_n)
    if module_tag is None or not module_tag.startswith("adr-"):
        refutes = []
    states = attempt_states(run, run_dir, repo, module_tag, prompt_n, records=records)
    councils, _ = _evidence_councils(repo, councils, states)
    order = committed_order(run, run_dir, repo, module_tag, prompt_n, rev=rev)
    ordered = _ordered_records(order, Path(repo).resolve(), councils + refutes)
    recorders = {r.doc["recorder"] for r in refutes if r.doc["recorder_role"] == "conductor"}
    counted: list[Round] = []
    place = 0
    for _, ref, rnd in _walk_places(ordered, repo, False, recorders, counted):
        if rnd is not None:
            place = rnd.place
        elif ref.valid and ref.role == "adjudicator":
            place = 3
    return place + 1


@dataclass
class AttemptState:
    """One council attempt record in a gate's scope, and what resolves or voids it.

    `state` is `resolved`, `voided` or `open`. `committed` is true when HEAD, the index and the
    working tree hold the same bytes for the attempt record. `naming` lists every council record
    in the council directory that names this attempt's path, committed or not; `pending` lists
    every pending copy under the git directory that names it. `problems` says, for an open
    attempt, why each record naming it does not resolve it."""

    rec: cr.Record
    rel: str
    sha256: str
    state: str
    committed: bool
    resolved_by: cr.Record | None = None
    voided_by: cr.Record | None = None
    naming: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    @property
    def open(self) -> bool:
        return self.state == "open"


def _is_committed(repo: Path, path: Path) -> bool:
    try:
        return cr.path_state(repo, _rel(repo, path)).clean
    except cr.RecordError:
        return False


def _subject_pairs(doc: dict) -> list[tuple]:
    return [(s.get("path"), s.get("sha256")) for s in doc.get("subjects") or [] if isinstance(s, dict)]


def resolution_problem(attempt: dict, attempt_rel: str, attempt_sha: str, record: cr.Record,
                       record_bytes: bytes, pending_bytes: bytes | None) -> str | None:
    """Why `record` does not resolve the attempt, or None when it does.

    The record must be a format-2 council record that names the attempt by path, sha256 and
    attempt id; its seal must hold on `record_bytes`; its book, run, binding, round, question,
    subjects and registry must equal the attempt's; and, when a pending copy exists, its bytes must
    equal `pending_bytes`. Committedness is the caller's check."""
    doc = record.doc
    if doc.get("format_version") != "2":
        return "a format-1 council record never resolves an attempt"
    ref = doc.get("attempt")
    if not isinstance(ref, dict) or ref.get("path") != attempt_rel:
        return "the record names another attempt or none"
    if ref.get("sha256") != attempt_sha:
        return "the record names the attempt with another sha256"
    if ref.get("attempt_id") != attempt.get("attempt_id"):
        return "the record names the attempt with another attempt id"
    if not cr.seal_holds(record_bytes):
        return "the record's seal does not hold on its bytes"
    if pending_bytes is not None and pending_bytes != record_bytes:
        return "the record's bytes differ from its pending copy"
    for key in ("book", "run_id", "binding", "round", "question", "registry"):
        if doc.get(key) != attempt.get(key):
            return f"the record's {key} differs from the attempt's"
    if doc.get("council_kind") != attempt.get("council_kind"):
        return "the record's council_kind differs from the attempt's"
    if _subject_pairs(doc) != _subject_pairs(attempt):
        return "the record's subjects differ from the attempt's"
    return None


def _in_attempt_scope(doc: dict, run: dict, module_tag: str | None, prompt_n: int) -> bool:
    if not _bound(doc, run):
        return False
    binding = doc.get("binding") or {}
    if module_tag is None:
        return binding.get("module_tag") is None and binding.get("prompt") == prompt_n
    return binding.get("module_tag") == module_tag


def _void_names(doc: dict, run: dict, module_tag: str | None, prompt_n: int) -> dict | None:
    if not _bound(doc, run) or not isinstance(doc.get("void_attempt"), dict):
        return None
    if module_tag is None:
        if doc.get("module_tag") is not None or doc.get("prompt") != prompt_n:
            return None
    elif doc.get("module_tag") != module_tag:
        return None
    return doc["void_attempt"]




def read_pending(repo: Path, run: dict) -> dict[str, bytes]:
    """The pending copies for this book and run, by file name. A missing directory holds none.

    The directory opens through `council_records.open_pending_dir`, the walk the pending-copy
    writer uses, so a symlink at any component under the git directory raises
    `council_records.SymlinkRefused`. A book id or run id that is not one path component raises
    `council_records.PathRefused` before the walk. Each child is read through the directory
    descriptor with O_NOFOLLOW: a symlinked child raises `council_records.SymlinkRefused`, and a
    non-regular child, an unreadable file or an unlistable directory raises
    `council_records.PathRefused`. A pending copy is never read through a link."""
    book_id, run_id = str(run.get("book_id")), str(run.get("run_id"))
    for value, what in ((book_id, "book id"), (run_id, "run id")):
        try:
            cr.pending_component(value, what)
        except ValueError as exc:
            raise cr.PathRefused(Path(value), str(exc)) from None
    where = Path("<git dir>", *cr.PENDING_SUBDIR, book_id, run_id)
    try:
        dfd = cr.open_pending_dir(repo, book_id, run_id, create=False)
    except NotADirectoryError:
        raise cr.SymlinkRefused(where, "the pending-copy directory is a symlink or not a directory") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise cr.SymlinkRefused(where, "the pending-copy directory is a symlink or not a directory") from None
        raise cr.PathRefused(where, f"the pending-copy directory cannot be opened ({type(exc).__name__})") from None
    if dfd is None:
        return {}
    out: dict[str, bytes] = {}
    try:
        try:
            names = sorted(os.listdir(dfd))
        except OSError as exc:
            raise cr.PathRefused(where, f"the pending-copy directory cannot be listed ({type(exc).__name__})") from None
        for name in names:
            if not name.endswith(".json"):
                continue
            child = where / name
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise cr.SymlinkRefused(child, "a pending copy is a symlink") from None
                raise cr.PathRefused(child, f"a pending copy cannot be read ({type(exc).__name__})") from None
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise cr.PathRefused(child, "a pending copy is not a regular file")
                with os.fdopen(fd, "rb", closefd=False) as fh:
                    out[name] = fh.read()
            except OSError as exc:
                raise cr.PathRefused(child, f"a pending copy cannot be read ({type(exc).__name__})") from None
            finally:
                os.close(fd)
    finally:
        os.close(dfd)
    return out


def attempt_states(run: dict, run_dir: Path | str, repo: Path, module_tag: str | None, prompt_n: int,
                   records: list[cr.Record] | None = None,
                   pending: dict[str, bytes] | None = None) -> list[AttemptState]:
    """Every council attempt record in scope, with its state, in file-name order.

    The one implementation of attempt resolution, shared by the council runner, the gate check and
    recovery, as `expected_round` is. The scope is the module `module_tag` (a claim's scope is the
    module, because an adr module convenes at two prompts), or prompt `prompt_n` when `module_tag`
    is None. An attempt is resolved when exactly one committed council record names it and passes
    `resolution_problem`; voided when a committed owner exception's `void_attempt` names its path
    and sha256 (and it is not resolved); open otherwise. `records` and `pending` default to a fresh
    discovery of `<run_dir>/council/` and of the run's pending copies.

    Raises `council_records.RecordError` when a record or a pending copy cannot be read."""
    run_dir = Path(run_dir).resolve()
    if records is None:
        records = cr.discover_records(run_dir)
    if pending is None:
        pending = read_pending(repo, run)
    pending_docs: dict[str, dict] = {}
    for name, data in pending.items():
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(doc, dict):
            pending_docs[name] = doc
    councils = [r for r in records if r.record_type == "council-record"]
    voids = [r for r in records if r.record_type == "owner-exception"
             and _void_names(r.doc, run, module_tag, prompt_n) is not None]
    out: list[AttemptState] = []
    for rec in records:
        if rec.record_type != "council-attempt" or not _in_attempt_scope(rec.doc, run, module_tag, prompt_n):
            continue
        data = rec.path.read_bytes()
        rel = _rel(repo, rec.path)
        sha = cr.sha256_bytes(data)
        st = AttemptState(rec, rel, sha, "open", _is_committed(repo, rec.path))
        st.naming = [c for c in councils if isinstance(c.doc.get("attempt"), dict)
                     and c.doc["attempt"].get("path") == rel]
        st.pending = [name for name, doc in pending_docs.items()
                      if isinstance(doc.get("attempt"), dict) and doc["attempt"].get("path") == rel]
        resolving = []
        for c in st.naming:
            cbytes = c.path.read_bytes()
            problem = resolution_problem(rec.doc, rel, sha, c, cbytes, pending.get(c.path.name))
            if problem is None and not _is_committed(repo, c.path):
                problem = "the record is not committed"
            if problem is None:
                resolving.append(c)
            else:
                st.problems.append(f"{_rel(repo, c.path)}: {problem}")
        if len(resolving) == 1:
            st.state, st.resolved_by = "resolved", resolving[0]
        else:
            if len(resolving) > 1:
                st.problems.append("more than one committed council record names the attempt")
            for v in voids:
                named = _void_names(v.doc, run, module_tag, prompt_n)
                if named.get("path") == rel and named.get("sha256") == sha and _is_committed(repo, v.path):
                    st.state, st.voided_by = "voided", v
                    break
        out.append(st)
    return out


def open_attempts(run: dict, run_dir: Path | str, repo: Path, module_tag: str | None, prompt_n: int,
                  records: list[cr.Record] | None = None,
                  pending: dict[str, bytes] | None = None) -> list[AttemptState]:
    """The attempts in scope that are neither resolved nor voided (`attempt_states`)."""
    return [a for a in attempt_states(run, run_dir, repo, module_tag, prompt_n, records, pending) if a.open]


def holding_attempts(run: dict, run_dir: Path | str, repo: Path, module_tag: str | None, prompt_n: int,
                     states: list[AttemptState]) -> list[str]:
    """The attempt record paths that hold the scope against a new claim, in file-name order.

    An attempt holds when it is open; when it is voided while a council record or a pending copy
    naming it survives (the gate refuses that void, `_void_problems`); or when HEAD holds it in
    scope and its working-tree file is gone, which discovery cannot see. A held attempt is never
    claimed past: its name is never reused and its bytes in HEAD are never committed over."""
    held = {st.rel for st in states if st.open or (st.state == "voided" and (st.pending or st.naming))}
    seen = {st.rel for st in states}
    try:
        council_rel = (Path(run_dir).resolve() / "council").relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        council_rel = None
    if council_rel is not None:
        listing = cr.git(repo, "ls-tree", "-z", "--name-only", "HEAD", "--", council_rel + "/")
        names = [os.fsdecode(b) for b in listing.stdout.split(b"\x00") if b] if listing.returncode == 0 else []
        for name in names:
            if not name.endswith(".attempt.json") or name in seen or os.path.lexists(Path(repo) / name):
                continue
            blob = cr.git(repo, "cat-file", "blob", f"HEAD:{name}")
            try:
                doc = json.loads(blob.stdout.decode("utf-8")) if blob.returncode == 0 else None
            except (UnicodeDecodeError, ValueError):
                doc = None
            if isinstance(doc, dict) and doc.get("record_type") == "council-attempt" and \
                    _in_attempt_scope(doc, run, module_tag, prompt_n):
                held.add(name)
    return sorted(held)


def next_ordinal(states: list[AttemptState], round_: int) -> int:
    """One more than the attempts at round `round_` already resolved or voided."""
    return 1 + sum(1 for a in states if a.rec.doc.get("round") == round_ and not a.open)


def _evidence_councils(repo: Path, councils: list[cr.Record],
                       states: list[AttemptState]) -> tuple[list[cr.Record], list[str]]:
    """The council records that are gate evidence, and why each other one is not.

    The one rule, shared by the gate check and `expected_round`, so the council runner and the
    gate count places alike. A format-1 record and a format-2 record that names no attempt (a
    preflight could-not-run record) pass through. A format-2 record that names an attempt counts
    only when it is the record that resolves that attempt (`attempt_states`). Any other (a failed
    seal, bytes that differ from its pending copy, a binding that differs from the attempt's, or
    one of two records that both name one attempt) is no evidence, takes no place and resolves
    nothing. A format-2 record naming an attempt outside the scope's attempt records is dropped
    here too; the gate check refuses it."""
    resolvers = {st.resolved_by.path for st in states if st.resolved_by is not None}
    problems = {}
    for st in states:
        for p in st.problems:
            problems.setdefault(p.split(": ", 1)[0], p)
    kept, dropped = [], []
    for r in councils:
        ref = r.doc.get("attempt")
        if r.doc.get("format_version") != "2" or ref is None or r.path in resolvers:
            kept.append(r)
            continue
        name = _rel(repo, r.path)
        dropped.append(problems.get(name, f"{name}: does not resolve the attempt it names"))
    return kept, dropped


def v1_admissible(repo: Path, base: Any, runs_dir: Path | str) -> tuple[bool, str | None]:
    """Whether a format-1 council record is admissible in a run whose base_commit is `base`, and
    why not.

    A format-1 record is admissible only in a run whose base_commit predates the commit that lands
    format version 2. Operationally: the base must be a readable commit of a repository that is not
    shallow, and the tree at the base must hold neither the plugin's attempt schema (when the
    plugin's source lies inside this repository) nor any council attempt record
    (`*.attempt.json` in a `council/` directory under `runs_dir`). An unknown base refuses."""
    if not isinstance(base, str) or not _COMMIT.fullmatch(base):
        return False, "the run records no usable base_commit, so it cannot be shown to predate format version 2"
    shallow = cr.git(repo, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != b"false":
        return False, "the repository is shallow, so the run's base_commit cannot be read"
    if cr.git(repo, "cat-file", "-e", f"{base}^{{commit}}").returncode != 0:
        return False, f"the run's base_commit {base} is not a readable commit"
    try:
        schema_rel = _ATTEMPT_SCHEMA.relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        schema_rel = None
    if schema_rel is not None and cr.git(repo, "cat-file", "-e", f"{base}:{schema_rel}").returncode == 0:
        return False, (f"the run's base_commit {base} already carries the council attempt schema, so the run "
                       "started after format version 2 landed")
    try:
        runs_rel = Path(runs_dir).resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return False, "the runs directory lies outside the repository"
    listing = cr.git(repo, "ls-tree", "-r", "-z", "--name-only", base, "--", runs_rel + "/")
    if listing.returncode != 0:
        return False, f"the tree at the run's base_commit {base} cannot be read"
    for name in (os.fsdecode(b) for b in listing.stdout.split(b"\x00") if b):
        parts = name.split("/")
        if name.endswith(".attempt.json") and len(parts) >= 2 and parts[-2] == "council":
            return False, (f"the tree at the run's base_commit {base} already holds a council attempt record, "
                           "so the run started after format version 2 landed")
    return True, None


def _recover_command(repo: Path, run_path: Path | str | None, prompt_n: int | None) -> str:
    """The recovery command a refusal names, with the run snapshot (shell-quoted) and the prompt:
    recovery without `--prompt` cannot recognise a record whose pending copy is already removed."""
    run_arg = shlex.quote(_rel(repo, run_path)) if run_path is not None else "<run>"
    prompt_arg = str(prompt_n) if prompt_n is not None else "<n>"
    return f"run-council.py --recover {run_arg} --prompt {prompt_arg}"


def _recover_uncommitted(recover: str) -> str:
    """Where the advance-time remedy for an uncommitted record differs by type. The council runner
    commits attempt and council records itself, so the conductor recovers them; the conductor
    commits refutation and owner-exception records."""
    return ("the council runner commits attempt and council records itself, so do not commit it by hand: "
            "run the process check and, once no live council runner holds the attempt, "
            f"{recover}, which commits the original record only when its bytes are proven")


def _names_committed_attempt(doc: Any, committed_attempts: set[str]) -> bool:
    """True when `doc` is a format-2 council record naming a committed attempt record in scope. Such
    a record, once committed, is checked through its attempt: a copy of it that no longer matches
    HEAD resolves nothing, so its attempt stays open and the gate stops at 4."""
    ref = doc.get("attempt") if isinstance(doc, dict) else None
    return (isinstance(doc, dict) and doc.get("record_type") == "council-record"
            and doc.get("format_version") == "2" and isinstance(ref, dict)
            and ref.get("path") in committed_attempts)


def _uncommitted_problem(repo: Path, in_scope: list[cr.Record],
                         committed_attempts: set[str] = frozenset(), recover: str | None = None) -> str | None:
    """The first in-scope record that is not committed, or None. A record is committed when it is
    tracked and HEAD, the index and the working tree hold the same bytes. The remedy the message
    names follows the record type: recovery for an attempt or council record, a commit for a
    refutation or owner-exception record.

    One exception: a format-2 council record that HEAD holds, and that names a committed attempt in
    scope, which is present in the working tree but whose working tree or index no longer matches HEAD (a hook altered it between the commit
    and the runner's verification). It resolves nothing, so its attempt is open and stops the gate
    at 4, which the conductor can advance blocked. A council record HEAD has never held still
    refuses."""
    for rec in in_scope:
        name = _rel(repo, rec.path)
        try:
            st = cr.path_state(repo, name)
            clean = st.clean
        except cr.PathRefused:
            st, clean = None, False
        if (not clean and st is not None and st.head is not None and st.worktree is not None
                and _names_committed_attempt(rec.doc, committed_attempts)):
            continue
        if not clean:
            runner_owned = (rec.record_type == "council-attempt"
                            or (rec.record_type == "council-record" and rec.doc.get("format_version") == "2"))
            remedy = (_recover_uncommitted(recover or _recover_command(repo, None, None)) if runner_owned
                      else "commit the record before the advance")
            return (f"{name}: is not committed (HEAD, the index and the working tree must hold the same "
                    f"bytes); {remedy}")
    return None


def _removed_record_problem(repo: Path, run: dict, council_dir: Path,
                           committed_attempts: set[str] = frozenset()) -> str | None:
    """The first record committed in HEAD under `council_dir`, bound to this run, that is missing
    from the working tree or differs from HEAD, or None. A record that was committed is never
    cleared by deleting or editing it: that would let an owner discard a held round.

    A format-2 council record in HEAD that names a committed attempt in scope, and that is still in
    the working tree but differs from HEAD, is checked through that attempt instead: it resolves
    nothing, so the gate stops at 4 rather than passing, and the stop can be advanced blocked. A
    deleted one still refuses."""
    try:
        council_rel = council_dir.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None
    listing = cr.git(repo, "ls-tree", "-z", "--name-only", "HEAD", "--", council_rel + "/")
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
        # A present but altered format-2 record naming a committed attempt is read through that
        # attempt (it resolves nothing, so the attempt stops at 4). A deleted one still refuses:
        # a committed record is never cleared by deleting it.
        if st.worktree is not None and _names_committed_attempt(doc, committed_attempts):
            continue
        if st.worktree != st.head:
            return (f"{name}: a record committed in HEAD and bound to this run is missing from the working "
                    "tree or differs from HEAD; a record is never cleared by deleting or editing it")
    return None


def _head_records_naming(repo: Path, run: dict, council_dir: Path) -> dict[str, list[str]]:
    """For each attempt path, the council records HEAD holds under `council_dir`, bound to this run,
    that name it."""
    out: dict[str, list[str]] = {}
    try:
        council_rel = council_dir.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return out
    listing = cr.git(repo, "ls-tree", "-z", "--name-only", "HEAD", "--", council_rel + "/")
    if listing.returncode != 0:
        return out
    for name in sorted(n for n in (os.fsdecode(b) for b in listing.stdout.split(b"\x00")) if n.endswith(".json")):
        blob = cr.git(repo, "cat-file", "blob", f"HEAD:{name}")
        if blob.returncode != 0:
            continue
        try:
            doc = json.loads(blob.stdout.decode("utf-8"))
        except ValueError:
            continue
        ref = doc.get("attempt") if isinstance(doc, dict) else None
        if (isinstance(doc, dict) and doc.get("record_type") == "council-record" and _bound(doc, run)
                and isinstance(ref, dict) and isinstance(ref.get("path"), str)):
            out.setdefault(ref["path"], []).append(name)
    return out


def _void_problems(repo: Path, owners: list[cr.Record], states: list[AttemptState], run: dict,
                   module_tag: str | None, prompt_n: int,
                   head_naming: dict[str, list[str]] | None = None, recover: str | None = None) -> str | None:
    """Why an in-scope void-attempt owner exception is refused, or None.

    A void must name an attempt record in scope by path and sha256. It is refused while a council
    record naming that attempt exists in HEAD or in the working tree (committed or not), or while a
    pending copy names it: the attempt's own result survives, so it is restored or recovered instead
    of voided."""
    by_rel = {st.rel: st for st in states}
    head_naming = head_naming or {}
    for o in owners:
        named = _void_names(o.doc, run, module_tag, prompt_n)
        if named is None:
            continue
        name = _rel(repo, o.path)
        st = by_rel.get(named.get("path"))
        if st is None or st.sha256 != named.get("sha256"):
            return (f"{name}: the void-attempt names no council attempt record in this gate's scope with that "
                    "path and sha256")
        in_head = [n for n in head_naming.get(st.rel, []) if n not in {_rel(repo, c.path) for c in st.naming}]
        if st.naming or st.pending or in_head:
            survivors = ([_rel(repo, c.path) for c in st.naming] + [f"{n} in HEAD" for n in in_head]
                         + [f"pending copy {p}" for p in st.pending])
            return (f"{name}: the void-attempt is refused while a council record or a pending copy naming the "
                    f"attempt {st.rel} survives ({', '.join(survivors)}); its own result can still resolve "
                    f"it, so run the process check and then {recover or _recover_command(repo, None, prompt_n)} "
                    "instead")
    return None


def _open_attempt_verdict(repo: Path, open_: list[AttemptState], artifacts: list[str],
                          run_path: Path | str | None = None, prompt_n: int | None = None) -> Verdict:
    """The verdict for open committed attempts in scope: `stop` at 4, decided by the first open
    attempt record, with every open attempt record required among the attached artifacts."""
    first = open_[0]
    attached = _attached(repo, artifacts)
    missing = [st.rel for st in open_ if st.rec.path.resolve() not in attached]
    if missing:
        return _refuse("an open council attempt stops this gate, and the stop needs its attempt record "
                       "attached; not among the attached artifacts: " + ", ".join(missing),
                       deciding=first.rel)
    reasons = []
    for st in open_:
        why = "; ".join(st.problems) if st.problems else "no council record names it"
        reasons.append(f"{st.rel}: the council attempt is open ({why}); only its original council record, "
                       "committed and verified, resolves it, and only an owner's void-attempt record voids it, "
                       "so no later round passes over it")
    recover = _recover_command(repo, run_path, prompt_n)
    reasons.append("before acting on this stop, run the process check and probe the lock "
                   f"({recover} --probe); once no live council runner holds it, run recovery ({recover}); "
                   "advance --outcome blocked with the attempt record attached only when recovery leaves "
                   "the attempt open")
    return Verdict("stop", [4], reasons, first.rel)


def _evaluate_council_history(gate: GateClass, run: dict, run_path: Path | str, repo: Path | None,
                          artifacts: list[str], records: list, registry_loader, start=None,
                          *, identity=None, rev: str = "HEAD") -> Verdict:
    """Judge a `council` or `module-close` gate (and a patch `verify` phase).

    Admits committed records only (`_uncommitted_problem`), refuses a run whose committed records
    were removed or changed (`_removed_record_problem`, `_snapshot_named_problem`), and checks each
    responding seat against the current router registry (`admissibility_problem`). A record that
    names `run-council.py` as its writer is a self-asserted claim; no check here verifies the author.

    Council attempts are read before any pass or route: an open committed attempt in scope stops the
    gate at 4 (`_open_attempt_verdict`). A format-2 council record that does not resolve the attempt
    it names is no evidence and takes no place (`_evidence_councils`). A format-1 council record is
    admitted only in a run whose base_commit predates format version 2 (`v1_admissible`). `start`
    carries that base_commit as the run's history pins it (`start_fields`); without it, the run
    snapshot's live `base_commit` is read."""
    if repo is None:
        return _refuse("the run is not inside a git repository, so no record can be checked")
    run_dir = Path(run_path).resolve().parent
    patch = gate.module_kind == "patch"
    scope_tag = gate.module_tag if not patch else None
    councils, owners, refutes = _scope(records, run, scope_tag, gate.n)
    attempts = [r for r in records if r.record_type == "council-attempt"
                and _in_attempt_scope(r.doc, run, scope_tag, gate.n)]
    committed_attempts = {_rel(repo, a.path) for a in attempts if _is_committed(repo, a.path)}
    recover = _recover_command(repo, run_path, gate.n)
    problem = (_uncommitted_problem(repo, attempts + councils + owners + refutes, committed_attempts, recover)
               or _removed_record_problem(repo, run, run_dir / "council", committed_attempts)
               or _snapshot_named_problem(repo, run, run_dir / "council"))
    if problem:
        return _refuse(problem)
    if gate.module_kind != "adr" and refutes:
        return _refuse("a refutation record is in scope, but only an adr module closes on one: "
                       + ", ".join(_rel(repo, r.path) for r in refutes))
    # The runner records the structural module kind, including implementation
    # modules in architectural books. A record must match this gate's kind.
    for r in councils:
        if r.doc.get("council_kind") != gate.module_kind:
            return _refuse(f"{_rel(repo, r.path)}: council_kind {r.doc.get('council_kind')!r} is not the "
                           f"kind of this gate ({gate.module_kind}); a council of another kind never "
                           "satisfies it")
        if r.doc["binding"]["prompt"] not in gate.module_prompts:
            return _refuse(f"{_rel(repo, r.path)}: is bound to prompt {r.doc['binding']['prompt']}, "
                           "outside this module's prompts")

    # Format 1 carries no attempt binding, so its admissibility turns on when the run started.
    base = start.base if start is not None else run.get("base_commit")
    if identity is not None:
        try:
            if identity != start_identity(repo, run, run_path):
                return _refuse("the retained run-start identity differs from committed history")
            base = identity["base_commit"]
        except cr.RecordError:
            return _refuse("the committed run-start identity is unavailable")
    v1 = [r for r in councils if r.doc.get("format_version") != "2"]
    if v1:
        ok, why = v1_admissible(repo, base, run_dir.parent)
        if not ok:
            return _refuse(f"{_rel(repo, v1[0].path)}: a format-1 council record is inadmissible in this run: "
                           f"{why}; a format-1 record was written without an attempt claim, so it counts only in "
                           "a run started before format version 2")

    # Council attempts, before any pass or route.
    try:
        states = attempt_states(run, run_dir, repo, scope_tag, gate.n, records=records)
    except cr.RecordError as exc:
        return _refuse(f"a council attempt or pending copy cannot be read: {exc}")
    attempt_rels = {st.rel for st in states}
    for r in councils:
        ref = r.doc.get("attempt")
        if r.doc.get("format_version") == "2" and isinstance(ref, dict) and ref.get("path") not in attempt_rels:
            return _refuse(f"{_rel(repo, r.path)}: names an attempt record {ref.get('path')!r} that is not a "
                           "council attempt record in this gate's scope; a council record counts only with the "
                           "attempt it ran under")
    problem = _void_problems(repo, owners, states, run, scope_tag, gate.n,
                             _head_records_naming(repo, run, run_dir / "council"), recover)
    if problem:
        return _refuse(problem)
    open_ = [st for st in states if st.open]
    if open_:
        return _open_attempt_verdict(repo, open_, artifacts, run_path, gate.n)
    councils, dropped = _evidence_councils(repo, councils, states)

    if not councils:
        return _refuse("no council record naming run-council.py as its writer (a self-asserted field) is in "
                       "scope for this book, run and module; a module whose council ran before records "
                       "existed closes only on a round convened at module close with run-council.py")


    try:
        order = committed_order(run, run_dir, repo, scope_tag, gate.n, rev=rev)
        contradicted = order.contradictions()
        descent = order.descent_problems()
    except OrderError as exc:
        return Verdict("stop", [4], [f"record order is unavailable: {exc}"])
    # A contradiction means a clock moved or history was rewritten, so the premise fails (stop 4). A
    # tie, below, gives no sign that either signal lies: it stops as an equal stamp always did (stop 1).
    if contradicted:
        a, b = contradicted[0]
        return Verdict("stop", [4], [f"{a} was committed before {b}, but its written_at is later; the stamps "
                                     "contradict the committed order, so a clock moved or history was "
                                     f"rewritten; {_PERMANENT}"])
    if descent:
        return Verdict("stop", [4], descent[:1])
    absent = [_rel(repo, r.path) for r in councils + refutes if _rel(repo, r.path) not in order.commit]
    if absent:
        return Verdict("stop", [4], [f"{absent[0]}: has no committed place in the record order"])
    ordered = _ordered_records(order, Path(repo).resolve(), councils + refutes)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if order.tied(_rel(repo, a.path), _rel(repo, b.path)):
                attached = _attached(repo, artifacts)
                missing = [_rel(repo, r.path) for r in (a, b) if r.path.resolve() not in attached]
                if missing:
                    return _refuse("record order is a tie, and a stop needs its tied records attached; "
                                   "not attached: " + ", ".join(missing))
                return Verdict("stop", [1], [f"records {_rel(repo, a.path)} and {_rel(repo, b.path)} share "
                                             "a commit or have no commit order between them; record order "
                                             "is a tie"])

    stops: set[int] = set()
    reasons: list[str] = [f"not evidence, and takes no place: {d}" for d in dropped]
    holds: list[str] = []
    verify_kind = gate.module_kind in ("verify", "implementation") or (patch and run.get("format_version") == "2")
    adr = gate.module_kind == "adr"
    conductor_recorders = {r.doc["recorder"] for r in refutes if r.doc["recorder_role"] == "conductor"}

    def authorized(kind: str, rnd: int) -> bool:
        # A void-attempt owner exception names an attempt, not a place, and authorizes nothing.
        return any(isinstance(o.doc.get("place"), dict) and o.doc["place"]["kind"] == kind
                   and o.doc["place"]["round"] == rnd for o in owners)

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
            code = deciding.doc["refusal_reason"]["code"]
            if code == PREFLIGHT_RETRIES_SPENT:
                stops.add(1)
                reasons.append(f"{deciding_name}: the preflight retry bound is spent ({code}); the third "
                               "preflight refusal at one convening prompt is an unsuccessful recovery sequence")
            else:
                stops.add(4)
                reasons.append(f"the council could not run ({deciding_name}: {code}); defer to the owner")
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
