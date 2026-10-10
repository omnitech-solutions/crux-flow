#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0,<7"]
# ///
"""Recovery from a council persistence failure: commit or recognise the original council record,
never ask for a new verdict.

    uv run crux/scripts/run-council.py --recover <run-RUN-NNN.yaml> [--prompt N] [--book B] \\
        [--probe] [--owner-commit-pending]

`run-council.py` dispatches `--recover` here before it reads any key, calling `main(argv)` with the
arguments after `--recover`. This module also runs as a program with the same arguments.

This module imports none of `crux.council`, `crux.core.llm_caller` and `run-council.py`. The
documented route, `run-council.py --recover`, loads the first two through run-council.py's own
imports and never calls them. On either route recovery calls no `crux_env` key accessor, reads no
key and makes no request: it opens no network connection and asks the gateway for no verdict.
Before it starts any git child it deletes
from its own environment every name `council_commit.git_child_env()` drops (the gateway key and
each name the crux env file holds), so no git child inherits one, including those the shared read
helpers start. Its own git children run under `council_commit.git_child_env()`, and every commit
goes through `council_commit.commit_owned`.

The pending copy under the repository's git directory is the source of truth: it holds the bytes
the council runner computed. For each attempt in the prompt's scope (the module tag, or the prompt
in a patch book), and each pending copy in that scope, recovery decides from HEAD, the working tree
and the pending copies:

* `recognised` - a council record in HEAD names the attempt, its seal holds, and it equals the
  pending copy. The runner died after its commit. Recovery reprints the summary, removes the
  pending copy, and exits with the record's code. It makes no commit. One case has no pending
  copy: the runner died after removing it and before printing. When nothing else in scope needs
  recovery, `--prompt N` was passed, prompt N's state in the run snapshot is neither `done` nor
  `blocked`, and the latest attempt in scope (highest round, then highest ordinal) is resolved by
  a record bound to prompt N, recovery recognises that record the same way. It does not when
  another council record in scope bound to prompt N comes after that record in committed order
  (an uncommitted record, a tie, or history that cannot order the records counts as later; a
  preflight could-not-run record names no attempt), or when prompt N's last claim line in
  `council-preflight.jsonl` names a later round or was written after that record (a released
  attempt leaves only its claim line).
* `committed` - a pending copy whose bytes are not in HEAD, with no working-tree record or one
  equal to it. Recovery re-scans the bytes for key shapes, writes them when the working tree has
  no record, commits them (with each retained copy whose bytes match its subject hash), verifies
  the commit, removes the pending copy, and exits with the record's code.
* `mismatch` - a working-tree record that differs from the pending copy, conflicting pending
  copies, a seal that fails, or HEAD differing from the pending copy. Nothing changes; exit 1.
  With `--owner-commit-pending`, the owner's remedy once a hook is fixed, recovery commits the
  pending copy's bytes over the altered record and verifies them. The flag acts on a mismatch only.
  After a `recognised` or `committed` act, recovery re-reads the attempt's state through
  `council_gate.attempt_states`. When the attempt is still not resolved (two committed records
  name it, for example), the item becomes a `mismatch`: the pending copy is kept and the attempt
  stays open, although a recovery commit may already have landed.
* `unproven` - a working-tree record with no pending copy and not in HEAD, or no council record at
  all. Nothing changes; exit 1.
* `index-locked` / `live-runner` - git's index lock is held, or a live runner holds its attempt's
  lock. Checked before anything is written; nothing changes; exit 1. A lock probe that fails for
  any reason but a missing file is an environment fault (exit 2), never a free lock.
* `released` - an attempt record that was never committed, whose lock is free, is deleted and
  unstaged. Its runner made no request; exit 0.
* `nothing-open` - nothing to do; exit 0.
* `commit-refused` - a recovery commit `council_commit` refused (its `code` is printed); exit 1.
  When HEAD did not move, recovery takes back what it changed. It unstages an owned path the
  index newly stages only when the staged bytes are the pending copy's, HEAD lacks the path and
  no index lock is held. It removes the record file it wrote only when the index no longer
  stages it. It then compares the index, the index lock, the owned working-tree files and the
  pending copy with their state before the commit, and the report names each difference:
  `"index_moved": true` with `"staged"` (the paths whose index entries changed) and
  `"index_lock_left": true`, `"worktree_moved"`, `"pending_moved": true`. A timeout leaves
  `index.lock` and the staged record, for example; a hook that rewrote the record and failed
  leaves nothing once recovery unstages it. When the refused call names outside paths it found
  changed, the report carries them as `"outside_moved"` (with the literal `refs/stash` when the
  stash moved), and the owned paths it staged and could not take back as `"owned_staged"`; stderr
  shows each such path on its own, a control character as `?` and a key-shaped name withheld. A
  timeout, or a refusal that names moved outside paths, names the owner's remedy in order: look for work a hook set aside (`git stash list`; a
  pre-commit framework keeps a backup patch under its cache directory) and restore it, then
  remove a stale `index.lock`, then run recovery. When the commit landed and was refused, the report
  carries `"commit_landed": true`: HEAD holds bytes not proven to be the pending copy, the pending
  copy is kept, and the attempt stays open. The owner's remedy is worded by the code. For a
  `mismatch` (a hook rewrote the record), fix the hook, then run recovery with
  `--owner-commit-pending`; otherwise run recovery again, which recognises the commit when HEAD
  holds the pending copy's bytes.

Each item is acted on in turn, and each prints its own report. A refusal or an environment fault
stops the loop, and its stderr line names every act this invocation already made.

`--probe` writes nothing. It prints `{"recovery": "probe", "lock": "live-runner"|"unknown"|"free",
...}` with the open attempts, and exits 1 while a live runner holds a lock or a lock cannot be
probed, otherwise 0.

Stdout: the report `{"recovery": <token>, "attempt": <path or null>, "record": <path or null>}`
first, then, for a recognised or committed record, the runner's summary. Both are scanned for
key shapes before printing. Exit 2, with a fixed stderr line, is an environment fault: no
`fcntl`, an unreadable git repository, a malformed crux env file, a lock probe that fails, or a
run that cannot be bound.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: recovery refuses, as the runner does
    fcntl = None  # type: ignore[assignment]

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    import yaml  # noqa: E402
except ImportError:
    sys.stderr.write("council_recovery.py requires PyYAML - run via `uv run` (PEP 723 supplies it).\n")
    raise SystemExit(2)

import council_commit  # noqa: E402  - attribute-style calls only, so a test can replace one
import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402
import secret_scan as ss  # noqa: E402

_MODULE_TAG = re.compile(r"(adr|implementation|verify)-\d+")
_PROG = "council-recovery"
#: The council runner's diagnostics log in the run directory: one JSON object per line.
_DIAGNOSTICS_LOG = "council-preflight.jsonl"
_WITHHELD = "[withheld: matched the secret scan]"

#: The tokens a report carries in `recovery`.
TOKENS = ("recognised", "committed", "released", "nothing-open", "mismatch", "unproven", "live-runner",
          "index-locked", "commit-refused", "probe")
#: Tokens that hold the attempt open for the owner; when any item carries one, nothing is written.
_BLOCKING = ("mismatch", "unproven")


class Fatal(Exception):
    """An environment fault or a run that cannot be bound: exit 2. Fixed text and paths only."""


# ───────────────────────────── output ─────────────────────────────


def _err(message: str) -> None:
    labels = ss.scan_text(message)
    if labels:
        message = f"{_PROG}: a message was withheld (matched {', '.join(labels)})"
    print(message, file=sys.stderr, flush=True)


def _clean(value: Any) -> Any:
    if isinstance(value, str) and ss.scan_text(value):
        return _WITHHELD
    return value


def _print(doc: dict) -> None:
    text = json.dumps(doc, indent=2)
    if ss.scan_text(text):
        text = json.dumps({k: (v if k == "recovery" else _WITHHELD) for k, v in doc.items()}, indent=2)
    print(text, flush=True)


def _report(token: str, attempt: str | None, record: str | None, **extra: Any) -> None:
    doc = {"recovery": token, "attempt": _clean(attempt), "record": _clean(record)}
    doc.update({k: _clean(v) for k, v in extra.items()})
    _print(doc)


def _print_summary(doc: dict, record_rel: str, attempt_rel: str | None) -> None:
    summary = cr.record_summary(doc, record_rel, attempt_rel)
    text = json.dumps(summary, indent=2)
    if ss.scan_text(text):
        text = json.dumps({"record": _clean(record_rel), "scan_refused_fields": ["serialized-summary"]}, indent=2)
    print(text, flush=True)


# ───────────────────────────── binding (as run-council.py binds) ─────────────────────────────


def _abs(arg: str) -> Path:
    return Path(os.path.abspath(arg))


def _raw(arg: str) -> Path:
    return Path(os.path.join(os.getcwd(), arg))


def _bind(args: argparse.Namespace) -> tuple[Path, Path, dict, dict]:
    """Repo root, run directory, run snapshot and book, bound exactly as the council runner binds
    them. Raises `Fatal` when none can be bound."""
    run_abs = _abs(args.run)
    if not run_abs.is_file():
        raise Fatal("the run snapshot does not exist")
    repo = cr.repo_root(run_abs.parent)
    if repo is None:
        raise Fatal("the run snapshot is not inside a git repository")
    rest = cr.below_repo(run_abs, repo)
    if not rest:
        raise Fatal("the run snapshot is not inside the repository")
    if cr.symlink_below(repo, rest) or cr.reached_through_symlink(run_abs) or cr.reached_through_symlink(_raw(args.run)):
        raise Fatal("a component of the run snapshot path is a symlink; name the snapshot by its physical path")
    if args.book and (cr.reached_through_symlink(_abs(args.book)) or cr.reached_through_symlink(_raw(args.book))):
        raise Fatal("the book is reached through a symlink; name the book by its physical path")
    try:
        resolved = cr.resolve_book(run_abs, _abs(args.book) if args.book else None)
    except cr.RecordError as exc:
        raise Fatal(f"the book cannot be bound to the run: {exc.message}") from None
    try:
        run = yaml.safe_load(run_abs.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise Fatal("the run snapshot cannot be read") from None
    if not isinstance(run, dict) or not isinstance(run.get("run_id"), str):
        raise Fatal("the run snapshot carries no run_id")
    if run.get("book_id") != resolved.book.get("id"):
        raise Fatal("the run snapshot names a different book id than the resolved book")
    return repo, run_abs.parent, run, resolved.book


def _scope(run: dict, book: dict, prompt_arg: int | None) -> tuple[int, str | None]:
    prompt_n = prompt_arg if prompt_arg is not None else run.get("current_prompt")
    if type(prompt_n) is not int or prompt_n < 1:
        raise Fatal("no prompt number: pass --prompt, or use a run with a current_prompt")
    prompts = book.get("prompts")
    entry = next((p for p in prompts if isinstance(p, dict) and p.get("n") == prompt_n), None) \
        if isinstance(prompts, list) else None
    if entry is None:
        raise Fatal(f"prompt {prompt_n} is not in the book")
    tag = entry.get("module_tag")
    return prompt_n, (tag if isinstance(tag, str) and _MODULE_TAG.fullmatch(tag) else None)


# ───────────────────────────── git reads ─────────────────────────────


@dataclass
class Git:
    repo: Path
    env: dict
    timeout: float = field(default_factory=lambda: council_commit.GIT_TIMEOUT_SECONDS)

    def run(self, *args: str):
        return council_commit._run(self.repo, list(args), self.env, self.timeout)

    def head_blob(self, rel: str) -> bytes | None:
        """HEAD's bytes for `rel`, or None when HEAD holds no regular file there."""
        r = self.run("ls-tree", "-z", "HEAD", "--", rel)
        if r.returncode != 0:
            if self.run("rev-parse", "--verify", "-q", "HEAD^{commit}").returncode != 0:
                return None  # an unborn branch holds nothing
            raise Fatal("git cannot read HEAD")
        entries = [e for e in r.stdout.split(b"\x00") if e]
        if len(entries) != 1:
            return None
        meta, name = entries[0].split(b"\t", 1)
        mode, kind, sha = meta.split(b" ")
        if os.fsdecode(name) != rel or kind != b"blob" or not mode.startswith(b"100"):
            return None
        blob = self.run("cat-file", "blob", os.fsdecode(sha))
        if blob.returncode != 0:
            raise Fatal("git cannot read a blob in HEAD")
        return blob.stdout

    def head_names(self, dir_rel: str) -> list[str]:
        r = self.run("ls-tree", "-z", "--name-only", "HEAD", "--", dir_rel + "/")
        if r.returncode != 0:
            return []
        return [os.fsdecode(n) for n in r.stdout.split(b"\x00") if n]

    def in_index(self, rel: str) -> bool:
        r = self.run("ls-files", "-z", "--", rel)
        if r.returncode != 0:
            raise Fatal("git cannot read the index")
        return bool(r.stdout.strip(b"\x00"))


def _worktree(path: Path) -> bytes | None:
    """The working-tree bytes of a regular file, or None when absent. A symlink is refused."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise Fatal(f"{path.name} is not a regular file")
    return path.read_bytes()


# ───────────────────────────── the attempt lock ─────────────────────────────


def _lock_state(path: Path) -> str:
    """`live` when another open file description holds the attempt record's flock (a live
    runner), `free` when none does, `unknown` when the lock cannot be probed. Opens read-only and
    never creates the file. Only a missing file reads free: no runner can hold a lock on it. Any
    other failure to open or lock reads `unknown`, which recovery never treats as free."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return "free"
    except OSError:
        return "unknown"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "live"
        except OSError:
            return "unknown"
        fcntl.flock(fd, fcntl.LOCK_UN)
        return "free"
    finally:
        os.close(fd)


# ───────────────────────────── decisions ─────────────────────────────


@dataclass
class Item:
    """One decision. `token` is the verdict; `owned` and `write` describe a commit; `pending_name`
    is the pending copy removed once the record is committed or recognised."""

    token: str
    attempt: str | None
    record: str | None
    reason: str = ""
    doc: dict | None = None
    data: bytes | None = None
    pending_name: str | None = None
    retained: dict = field(default_factory=dict)
    claim: Path | None = None
    owner_ok: bool = False
    round: Any = None
    ordinal: Any = None
    prompt: Any = None
    landed: bool = False
    residue: dict = field(default_factory=dict)
    moved: tuple = ()
    call_staged: tuple = ()


@dataclass
class Ctx:
    repo: Path
    run_dir: Path
    run: dict
    book_id: str
    run_id: str
    prompt: int
    module_tag: str | None
    git: Git
    owner: bool

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo).as_posix() if path.is_absolute() else path.as_posix()

    @property
    def council_dir(self) -> Path:
        return self.run_dir / "council"


def _parse(data: bytes) -> dict | None:
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _retained(ctx: Ctx, doc: dict) -> dict[str, bytes]:
    """Each retained copy the record names whose working-tree bytes equal its subject's sha256 and
    differ from HEAD: repository path -> bytes. A copy outside the run directory is skipped."""
    out: dict[str, bytes] = {}
    run_dir = ctx.run_dir.resolve()
    for s in doc.get("subjects") or []:
        if not isinstance(s, dict) or not isinstance(s.get("retained_copy"), str) or not s.get("sha256"):
            continue
        try:
            path, rel = cr.resolve_in_repo(ctx.repo, run_dir / s["retained_copy"])
        except cr.RecordError:
            continue
        if not path.resolve().is_relative_to(run_dir) or cr.symlink_below(ctx.repo, tuple(rel.split("/"))):
            continue
        try:
            data = _worktree(path)
        except Fatal:
            continue
        if data is None or cr.sha256_bytes(data) != s["sha256"]:
            continue
        if ctx.git.head_blob(rel) != data:
            out[rel] = data
    return out


def _decide_pending(ctx: Ctx, name: str, data: bytes, doc: dict, att: cg.AttemptState | None) -> Item:
    """The decision for one pending copy (the only pending copy naming its attempt, or a
    preflight could-not-run record's)."""
    path = ctx.council_dir / name
    rel = ctx.rel(path)
    att_rel = att.rel if att else None
    base = dict(attempt=att_rel, record=rel, doc=doc, data=data, pending_name=name, round=doc.get("round"),
                ordinal=att.rec.doc.get("ordinal") if att else None,
                prompt=(doc.get("binding") or {}).get("prompt"))
    if not cr.seal_holds(data):
        return Item("mismatch", reason="the pending copy's seal does not hold", **base)
    errors = cr.schema_errors(doc, "council-record")
    if errors:
        return Item("mismatch", reason="the pending copy fails the council-record schema", **base)
    if att is not None:
        problem = cg.resolution_problem(att.rec.doc, att.rel, att.sha256, cr.Record(path, doc, "council-record"),
                                        data, data)
        if problem:
            return Item("mismatch", reason=f"the pending copy does not resolve the attempt: {problem}", **base)
    elif doc.get("attempt") is not None:
        return Item("unproven", reason="the pending copy names an attempt that is not in the working tree", **base)
    head = ctx.git.head_blob(rel)
    wt = _worktree(path)
    if head is not None and head == data:
        if wt != data:
            return Item("mismatch", reason="the working-tree record differs from HEAD and the pending copy", **base)
        if att is not None and not att.committed:
            return Item("mismatch", reason="the attempt record itself is not committed", **base)
        return Item("recognised", **base)
    # Past this point the pending copy is sealed, schema-valid and resolves its attempt, so the
    # owner's remedy may commit it over whatever HEAD or the working tree holds instead.
    if head is not None:
        return Item("mismatch", reason="HEAD's record differs from the pending copy", owner_ok=True, **base)
    if wt is not None and wt != data:
        return Item("mismatch", reason="the working-tree record differs from the pending copy", owner_ok=True,
                    **base)
    return Item("committed", **base)


def _decide(ctx: Ctx, states: list[cg.AttemptState], pending: dict[str, bytes]) -> list[Item]:
    items: list[Item] = []
    docs: dict[str, dict | None] = {name: _parse(data) for name, data in pending.items()}
    in_scope = {name: d for name, d in docs.items()
                if d is not None and cg._in_attempt_scope(d, ctx.run, ctx.module_tag, ctx.prompt)}
    for name, d in docs.items():
        if d is None:
            items.append(Item("mismatch", None, ctx.rel(ctx.council_dir / name),
                              reason="a pending copy for this run is not parseable JSON"))
    by_attempt: dict[str, list[str]] = {}
    for name, d in in_scope.items():
        ref = d.get("attempt")
        if isinstance(ref, dict) and isinstance(ref.get("path"), str):
            by_attempt.setdefault(ref["path"], []).append(name)
    seen = {s.rel for s in states}

    for st in states:
        names = by_attempt.get(st.rel, [])
        head_attempt = ctx.git.head_blob(st.rel)
        if head_attempt is None:
            if names or st.naming:
                items.append(Item("unproven", st.rel, None,
                                  reason="an uncommitted attempt is named by a council record"))
            else:
                items.append(Item("released", st.rel, None, claim=st.rec.path))
            continue
        if len(names) > 1:
            items.append(Item("mismatch", st.rel, None, reason="more than one pending copy names the attempt"))
            continue
        if names:
            items.append(_decide_pending(ctx, names[0], pending[names[0]], in_scope[names[0]], st))
            continue
        if not st.open:
            continue  # resolved or voided, with nothing pending: done
        if not st.naming:
            items.append(Item("unproven", st.rel, None, reason="no council record names the attempt"))
            continue
        uncommitted = [c for c in st.naming if ctx.git.head_blob(ctx.rel(c.path)) is None]
        if uncommitted:
            items.append(Item("unproven", st.rel, ctx.rel(uncommitted[0].path),
                              reason="a working-tree record has no pending copy and is not in HEAD"))
        else:
            items.append(Item("mismatch", st.rel, ctx.rel(st.naming[0].path),
                              reason="the committed record does not resolve the attempt: "
                                     + "; ".join(st.problems)))

    # A committed attempt in scope whose working-tree file is gone: discovery cannot see it.
    council_rel = ctx.rel(ctx.council_dir)
    for rel in ctx.git.head_names(council_rel):
        if not rel.endswith(".attempt.json") or rel in seen or os.path.lexists(ctx.repo / rel):
            continue
        d = _parse(ctx.git.head_blob(rel) or b"")
        if d is not None and d.get("record_type") == "council-attempt" and \
                cg._in_attempt_scope(d, ctx.run, ctx.module_tag, ctx.prompt):
            items.append(Item("unproven", rel, None, reason="a committed attempt record is missing from the working tree"))

    # Pending copies in scope that name no attempt (the preflight could-not-run records) or an unseen one.
    for name, d in in_scope.items():
        ref = d.get("attempt")
        if ref is None:
            items.append(_decide_pending(ctx, name, pending[name], d, None))
        elif not (isinstance(ref, dict) and ref.get("path") in seen):
            items.append(Item("unproven", ref.get("path") if isinstance(ref, dict) else None,
                              ctx.rel(ctx.council_dir / name),
                              reason="a pending copy names an attempt that is not in the working tree"))
    return items


def _latest_resolved(ctx: Ctx, states: list[cg.AttemptState], explicit_prompt: bool,
                     records: list[cr.Record]) -> Item | None:
    """The `recognised` item for a runner that died after removing its pending copy and before
    printing, or None. Called only when `_decide` found nothing to act on.

    Every condition guards against reprinting an earlier result as the current one: `--prompt`
    was passed; that prompt's state in the run snapshot is neither `done` nor `blocked` (an
    advanced prompt already attached its record); the latest attempt in scope, by round and then
    ordinal, is resolved, so no later voided or open attempt follows it; its resolving record is
    bound to that prompt; no other council record in scope bound to that prompt comes after it in
    committed order, an uncommitted record counting as later (`_later_record`); and the prompt's last claim line in the diagnostics log names
    no later round or later attempt (`_later_claim`)."""
    if not explicit_prompt:
        return None
    prompts = ctx.run.get("prompts")
    entry = next((p for p in prompts if isinstance(p, dict) and p.get("n") == ctx.prompt), None) \
        if isinstance(prompts, list) else None
    if entry is None or entry.get("state") in ("done", "blocked"):
        return None
    keyed = [s for s in states if type(s.rec.doc.get("round")) is int and type(s.rec.doc.get("ordinal")) is int]
    if not keyed or len(keyed) != len(states):
        return None
    latest = max(keyed, key=lambda s: (s.rec.doc["round"], s.rec.doc["ordinal"]))
    ties = [s for s in keyed if (s.rec.doc["round"], s.rec.doc["ordinal"]) ==
            (latest.rec.doc["round"], latest.rec.doc["ordinal"])]
    if len(ties) != 1 or latest.state != "resolved" or latest.resolved_by is None:
        return None
    doc = latest.resolved_by.doc
    if (doc.get("binding") or {}).get("prompt") != ctx.prompt:
        return None
    if _later_record(ctx, latest.resolved_by, records) or _later_claim(ctx, doc, latest.rec.doc["round"]):
        return None
    return Item("recognised", latest.rel, ctx.rel(latest.resolved_by.path), doc=doc, round=doc.get("round"),
                ordinal=latest.rec.doc.get("ordinal"), prompt=ctx.prompt)


def _later_record(ctx: Ctx, resolving: cr.Record, records: list[cr.Record]) -> bool:
    """True when a council record in scope, bound to the prompt, sorts after `resolving` in the
    gate's committed record order, or ties with it. A preflight could-not-run record carries no
    attempt, so the attempt order cannot see it. Records in HEAD whose working-tree file is gone
    count too, and so do working-tree records not yet committed: a record with no committed place
    is later than every committed one. History that cannot order the records counts as later."""
    if not isinstance(resolving.doc.get("written_at"), str):
        return True
    own = ctx.rel(resolving.path)
    docs: dict[str, dict] = {ctx.rel(r.path): r.doc for r in records if r.record_type == "council-record"}
    for rel in ctx.git.head_names(ctx.rel(ctx.council_dir)):
        if rel not in docs and rel.endswith(".json") and not rel.endswith(".attempt.json"):
            d = _parse(ctx.git.head_blob(rel) or b"")
            if d is not None and d.get("record_type") == "council-record":
                docs[rel] = d
    try:
        order = cg.committed_order(ctx.run, ctx.run_dir, ctx.repo, ctx.module_tag, ctx.prompt)
    except cr.RecordError:
        return True
    for rel, d in docs.items():
        if rel == own or not cg._in_attempt_scope(d, ctx.run, ctx.module_tag, ctx.prompt):
            continue
        if (d.get("binding") or {}).get("prompt") != ctx.prompt:
            continue
        if rel not in order.commit or own not in order.commit or not order.before(rel, own):
            return True
    return False


def _later_claim(ctx: Ctx, doc: dict, round_: int) -> bool:
    """True when the last `claim` line for the prompt in `council-preflight.jsonl` names a later
    round than the resolving attempt's, or was written after the resolving record (a later
    attempt at the same round). A released attempt leaves no record, and its claim line is its
    only trace. A log that is a symlink or not a regular file is an environment fault."""
    path = ctx.run_dir / _DIAGNOSTICS_LOG
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    except OSError:
        raise Fatal(f"the diagnostics log {_DIAGNOSTICS_LOG} cannot be read (a symlink, or unreadable)") from None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise Fatal(f"the diagnostics log {_DIAGNOSTICS_LOG} is not a regular file")
        raw = fh.read()
    last = None
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        # Every run of a book shares the log: a claim line another run wrote is not this run's.
        if isinstance(entry, dict) and entry.get("event") == "claim" and entry.get("prompt") == ctx.prompt \
                and entry.get("run_id", ctx.run_id) == ctx.run_id:
            last = entry
    if last is None:
        return False
    claim_round, at, stamp = last.get("round"), last.get("at"), doc.get("written_at")
    if type(claim_round) is not int or not isinstance(at, str) or not isinstance(stamp, str):
        return True  # a claim line recovery cannot order is not proof that none came later
    return claim_round > round_ or at > stamp


# ───────────────────────────── acts ─────────────────────────────


def _message(ctx: Ctx, item: Item) -> str:
    msg = f"crux council recovery: {ctx.book_id} {ctx.run_id} prompt {item.prompt} round {item.round}"
    if item.ordinal is not None:
        msg += f" attempt {item.ordinal}"
    return msg


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.recovery-tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    _write_new(tmp, data)
    os.replace(tmp, path)


def _index(ctx: Ctx) -> dict[str, bytes]:
    """The index's `ls-files -s` entries, path -> entry. Reading needs no index lock."""
    r = ctx.git.run("ls-files", "-s", "-z")
    if r.returncode != 0:
        raise Fatal("git cannot read the index")
    out: dict[str, bytes] = {}
    for e in (e for e in r.stdout.split(b"\x00") if e):
        rel = os.fsdecode(e.split(b"\t", 1)[1])
        out[rel] = out.get(rel, b"") + e + b"\n"
    return out


def _staged_blob(ctx: Ctx, entry: bytes) -> bytes | None:
    """The bytes of one stage-0 regular-file index entry, or None for any other entry."""
    lines = [line for line in entry.splitlines() if line]
    if len(lines) != 1:
        return None
    mode, sha, stage = lines[0].split(b"\t", 1)[0].split(b" ")
    if stage != b"0" or not mode.startswith(b"100"):
        return None
    r = ctx.git.run("cat-file", "blob", os.fsdecode(sha))
    return r.stdout if r.returncode == 0 else None


def _restore_after_refusal(ctx: Ctx, item: Item, owned: dict[str, bytes], before: bytes | None,
                           index0: dict[str, bytes]) -> None:
    """Put back what recovery itself changed after a refusal where nothing landed, then record in
    `item.residue` only what still differs from before the commit.

    `commit_owned` unstages a path it added, but git refuses that unstage when a hook changed the
    working-tree file. An owned path the index now stages, that it did not stage before, is
    unstaged here only when its staged bytes are the bytes recovery committed, HEAD lacks the
    path, and no index lock is held: the pending copy still holds those bytes, so nothing is lost.
    The working-tree record recovery wrote is removed only when the index no longer stages it;
    removing it under a staged entry would leave a staged new file with no working copy."""
    path = ctx.repo / item.record
    lock = council_commit.index_locked(ctx.repo, env=ctx.git.env)
    index1 = _index(ctx)
    for rel, data in owned.items():
        if rel in index0 or rel not in index1 or lock or ctx.git.head_blob(rel) is not None:
            continue
        if _staged_blob(ctx, index1[rel]) == data:
            ctx.git.run("rm", "--cached", "-f", "-q", "--", rel)
    index1 = _index(ctx)
    if before is None:
        if item.record not in index1:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    elif before != item.data:
        _replace(path, before)

    residue: dict[str, Any] = {}
    moved = sorted(p for p in set(index0) | set(index1) if index0.get(p) != index1.get(p))
    if moved:
        residue["index_moved"] = True
        residue["staged"] = moved
    if council_commit.index_locked(ctx.repo, env=ctx.git.env):
        residue["index_moved"] = True
        residue["index_lock_left"] = True
    wt_before = {rel: (before if rel == item.record else data) for rel, data in owned.items()}
    wt_moved = sorted(rel for rel, data in wt_before.items() if _worktree_or_none(ctx.repo / rel) != data)
    if wt_moved:
        residue["worktree_moved"] = wt_moved
    pending = _pending_bytes(ctx.repo, ctx.book_id, ctx.run_id, item.pending_name) if item.pending_name else None
    if pending is None or pending != item.data:
        residue["pending_moved"] = True
    item.residue = residue


def _pending_bytes(repo: Path, book_id: str, run_id: str, name: str) -> bytes | None:
    """The bytes of one pending copy, or None when it cannot be read without following a link.

    The walk is `council_records.open_pending_dir`'s: every component from the git directory with
    O_NOFOLLOW, then the copy itself with O_NOFOLLOW. A symlinked component, a copy that is not a
    regular file, a missing copy and an unreadable one all read as None."""
    try:
        dfd = cr.open_pending_dir(repo, book_id, run_id, create=False)
    except (OSError, ValueError):
        return None
    if dfd is None:
        return None
    try:
        fd = os.open(cr.pending_component(name, "record name"), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
    except (OSError, ValueError):
        return None
    finally:
        os.close(dfd)
    try:
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return None
            return fh.read()
    except OSError:
        return None


def _worktree_or_none(path: Path) -> bytes | None:
    """`_worktree`, reading a path that is not a regular file as None rather than refusing."""
    try:
        return _worktree(path)
    except (Fatal, OSError):
        return None


def _commit(ctx: Ctx, item: Item) -> tuple[int, Item]:
    """Write (when needed), commit and verify the pending bytes. Returns the exit code and the
    item, whose token is `committed`, `commit-refused` or `unproven`. The pending copy is never
    removed here: `_act` removes it once the attempt's state is re-read as resolved.

    When `commit_owned` refuses, HEAD before and after tells the two cases apart. HEAD moved: a
    commit landed that verification refused, so `item.landed` is set and the working tree is left
    as the commit left it. HEAD unchanged: nothing landed. Recovery puts back what it changed
    (`_restore_after_refusal`), then compares the index, the index lock, the owned working-tree
    files and the pending copy with their state before the commit, and records each difference in
    `item.residue`, so the report claims only what holds."""
    labels = ss.scan_text(item.data.decode("utf-8", errors="replace"))
    if labels:
        return 1, Item("unproven", item.attempt, item.record,
                       reason=f"the pending copy matched the secret scan ({', '.join(labels)}); not committed")
    path = ctx.repo / item.record
    before = _worktree(path)
    if before is None:
        _write_new(path, item.data)
    elif before != item.data:
        _replace(path, item.data)  # only on --owner-commit-pending: the altered record is overwritten
    owned = {item.record: item.data, **_retained(ctx, item.doc)}
    head0 = ctx.git.run("rev-parse", "--verify", "-q", "HEAD").stdout
    index0 = _index(ctx)
    try:
        council_commit.commit_owned(ctx.repo, owned, _message(ctx, item), sealed=(item.record,))
    except council_commit.CommitRefused as exc:
        item.token, item.reason = "commit-refused", f"{exc.code}: {exc.base}"  # moved paths and remedy print once, below
        item.moved, item.call_staged = tuple(exc.moved), tuple(exc.staged)
        if ctx.git.run("rev-parse", "--verify", "-q", "HEAD").stdout != head0:
            item.landed = True
            return 1, item
        _restore_after_refusal(ctx, item, owned, before, index0)
        return 1, item
    item.token = "committed"
    return cr.record_exit_code(item.doc), item


def _unresolved(ctx: Ctx, item: Item) -> str | None:
    """Why the item's attempt is not resolved by the item's record, re-read from git and the tree
    through `council_gate.attempt_states`, or None when it is. A record that names no attempt (a
    preflight could-not-run record) has nothing to resolve."""
    if item.attempt is None:
        return None
    states = cg.attempt_states(ctx.run, ctx.run_dir, ctx.repo, ctx.module_tag, ctx.prompt)
    found = [s for s in states if s.rel == item.attempt]
    if len(found) != 1:
        return "the attempt record is no longer in scope"
    st = found[0]
    if st.state != "resolved":
        why = "; ".join(st.problems) or "no committed council record resolves it"
        return f"the attempt is {st.state}, not resolved: {why}"
    if st.resolved_by is None or ctx.rel(st.resolved_by.path) != item.record:
        return "the attempt is not resolved, because another record resolves it"
    return None


def _act(ctx: Ctx, item: Item) -> tuple[int, Item]:
    """Carry out one `released`, `recognised` or `committed` item. Returns the exit code and the
    item with its final token. A pending copy is removed only once the attempt is re-read as
    resolved by this item's record; otherwise the item becomes a `mismatch` and the copy is kept."""
    if item.token == "released":
        return _release(ctx, item), item
    if item.token == "committed":
        rc, item = _commit(ctx, item)
        if item.token != "committed":
            return rc, item
    else:
        rc = cr.record_exit_code(item.doc)
    problem = _unresolved(ctx, item)
    if problem:
        if item.token == "committed":
            item.landed = True
            problem = "recovery committed the pending copy's bytes, but " + problem
        item.token, item.reason = "mismatch", problem
        return 1, item
    if item.pending_name is not None:
        council_commit.remove_pending(ctx.repo, ctx.book_id, ctx.run_id, item.pending_name)
    return rc, item


def _release(ctx: Ctx, item: Item) -> int:
    rel = item.attempt
    if ctx.git.in_index(rel):
        # `-f`: a hook that rewrote the claim file and failed the claim commit leaves staged bytes
        # that differ from both the file and HEAD, and git refuses a plain unstage. The path is the
        # runner's own never-committed claim, and its file is deleted next.
        r = ctx.git.run("rm", "--cached", "-f", "-q", "--ignore-unmatch", "--", rel)
        if r.returncode != 0:
            raise council_commit.CommitRefused("hook-or-commit-failed",
                                               council_commit.scan_output(r.stdout + r.stderr).strip())
    try:
        os.unlink(item.claim)
    except FileNotFoundError:
        pass
    return 0


# ───────────────────────────── main ─────────────────────────────


class _Parser(argparse.ArgumentParser):
    """Argparse quotes the offending value in its message, and a value can be a key. The message is
    scanned before it prints, as the council runner's parser does."""

    def error(self, message: str):
        _err(f"{self.prog}: error: {message}")
        raise SystemExit(2)


_EPILOG = """\
exit codes:
  0  the work is done or there is nothing to do: recognised, committed, released, nothing-open,
     or a probe that found the lock free
  1  the attempt stays open for the owner: mismatch, unproven, live-runner, index-locked,
     commit-refused, or a probe that found a live runner or a lock it could not probe;
     a recognised or committed record whose gate stops exits with its own code, 1: a
     could-not-run record, or a ran record whose full write the secret scan refused
  2  an environment fault or a run that cannot be bound; stderr names it

result tokens (the `recovery` value): recognised, committed, released, nothing-open, mismatch,
unproven, live-runner, index-locked, commit-refused, probe.

Pass --prompt <n>, the prompt the council was convened at. Without it, recovery cannot recognise
a resolved attempt whose pending copy is gone, and reports nothing-open.
"""


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="run-council.py --recover", description=__doc__.split("\n\n")[0], epilog=_EPILOG,
                usage="run-council.py --recover <run-RUN-NNN.yaml> --prompt <n> [--book B] [--probe] "
                      "[--owner-commit-pending]",
                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", help="the run snapshot, run-RUN-NNN.yaml")
    p.add_argument("--prompt", type=int, default=None, metavar="<n>",
                   help="the convening prompt (default: current_prompt)")
    p.add_argument("--book", default=None, help="the book, when the run is not at the standard layout")
    p.add_argument("--probe", action="store_true", help="report the lock state and the open attempts; write nothing")
    p.add_argument("--owner-commit-pending", action="store_true",
                   help="the owner's remedy after fixing a hook: commit the pending copy over an altered record")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        return _main(args)
    except Fatal as exc:
        # Reached only before the first act: `_main` catches a fault raised while it acts.
        _err(f"{_PROG}: {exc}")
        return 2
    except council_commit.EnvUnreadable:
        _err(f"{_PROG}: the crux env file cannot be read, so no git child can be started without a key; "
             "nothing was changed")
        return 2
    except council_commit.CommitRefused as exc:
        # Reached only before the first act: `_main` catches a refusal raised while it acts.
        _err(f"{_PROG}: git refused ({exc.code}) before recovery acted on any item; nothing was changed")
        return 1
    except cr.RecordError as exc:
        _err(f"{_PROG}: a record or pending copy cannot be read: {exc.message}")
        return 2
    except Exception as exc:  # noqa: BLE001 - name only the type
        _err(f"{_PROG}: stopped on an unexpected {type(exc).__name__}")
        return 2


def _scrub_own_environment(child_env: dict) -> None:
    """Delete from this process's environment every name `git_child_env` drops: the gateway key,
    each name the crux env file holds, and the repository-redirecting variables.

    This is the second guard. The first is in each helper: `council_commit.git_child_env` and the
    shared read helpers in `council_records` drop these names from every git child they start.
    This deletion runs before the first read, so a helper that one day starts a child from the
    ambient environment (or an fsmonitor program the repository configures) still finds no key in
    it. Recovery needs none of these names, and no value is read: each is deleted unseen."""
    for name in [n for n in os.environ if n not in child_env]:
        del os.environ[name]


def _main(args: argparse.Namespace) -> int:
    if fcntl is None:
        raise Fatal("fcntl is unavailable on this platform, so a live runner cannot be told from a dead one")
    env = council_commit.git_child_env()  # EnvUnreadable -> exit 2
    _scrub_own_environment(env)
    repo, run_dir, run, book = _bind(args)
    prompt_n, module_tag = _scope(run, book, args.prompt)
    git = Git(repo, env)
    if git.run("rev-parse", "--absolute-git-dir").returncode != 0 or cr.absolute_git_dir(repo) is None:
        raise Fatal("git cannot read the repository")
    ctx = Ctx(repo, run_dir.resolve(), run, str(book["id"]), str(run["run_id"]), prompt_n, module_tag, git,
              args.owner_commit_pending)
    council_dir = ctx.council_dir
    if council_dir.is_symlink() or (council_dir.exists() and not council_dir.is_dir()):
        raise Fatal("the council directory is a symlink or not a directory")

    records = cr.discover_records(ctx.run_dir)
    pending = cg.read_pending(repo, run)
    states = cg.attempt_states(run, ctx.run_dir, repo, module_tag, prompt_n, records, pending)

    locks = {s.rel: _lock_state(s.rec.path) for s in states}
    live = [s for s in states if locks[s.rel] == "live"]
    unknown = [s for s in states if locks[s.rel] == "unknown"]
    if args.probe:
        open_rels = [s.rel for s in states if s.open]
        held = live or unknown
        _report("probe", held[0].rel if held else None, None,
                lock="live-runner" if live else "unknown" if unknown else "free", open=open_rels)
        return 1 if held else 0

    # (e) before any write: the index lock, then a live runner, then a lock that cannot be probed.
    if council_commit.index_locked(repo, env=env):
        _report("index-locked", None, None)
        _err(f"{_PROG}: git's index lock is held; recovery changes nothing while it is")
        return 1
    if live:
        _report("live-runner", live[0].rel, None)
        _err(f"{_PROG}: a live council runner holds {live[0].rel}; wait for it, then run recovery again")
        return 1
    if unknown:
        raise Fatal(f"the lock on {unknown[0].rel} cannot be probed, so a live runner cannot be ruled out; "
                    "recovery changed nothing")

    items = _decide(ctx, states, pending)
    if not items:
        latest = _latest_resolved(ctx, states, args.prompt is not None, records)
        if latest is None:
            _report("nothing-open", None, None)
            return 0
        items = [latest]

    blocking = [i for i in items if i.token in _BLOCKING]
    if blocking and ctx.owner:
        # The owner's remedy acts on a mismatch whose single pending copy resolves the attempt.
        fixable = [i for i in blocking if i.token == "mismatch" and i.owner_ok]
        if len(fixable) == len(blocking):
            for i in fixable:
                i.token = "committed"
            blocking = []
    if blocking:
        first = blocking[0]
        _report(first.token, first.attempt, first.record)
        _err(f"{_PROG}: {first.token}: {first.reason}; the attempt stays open for the owner and nothing was changed")
        return 1

    code = 0
    acted: list[str] = []
    for item in items:
        target = item.attempt or item.record
        try:
            rc, done = _act(ctx, item)
        except council_commit.CommitRefused as exc:
            _report("commit-refused", item.attempt, item.record, code=exc.code, **_refusal_paths(exc.moved, exc.staged))
            _err(f"{_PROG}: commit-refused: git refused ({exc.code}) while recovery acted on {target}"
                 f"{_paths_text(exc.moved, exc.staged)}; {_acted_text(acted)}"
                 + (f" {_TIMEOUT_REMEDY}" if _needs_remedy(exc.code, exc.moved) else ""))
            return max(code, 1)
        except Fatal as exc:
            _err(f"{_PROG}: {exc}, while recovery acted on {target}; {_acted_text(acted)}")
            return 2
        except cr.RecordError as exc:
            _err(f"{_PROG}: a record or pending copy cannot be read: {exc.message}, while recovery acted on "
                 f"{target}; {_acted_text(acted)}")
            return 2
        except council_commit.EnvUnreadable:
            _err(f"{_PROG}: the crux env file cannot be read, so no git child can be started without a key, "
                 f"while recovery acted on {target}; {_acted_text(acted)}")
            return 2
        except Exception as exc:  # noqa: BLE001 - name only the type, and the acts already made
            _err(f"{_PROG}: stopped on an unexpected {type(exc).__name__} while recovery acted on {target}; "
                 f"{_acted_text(acted)}")
            return 2
        if done.token in ("released", "recognised", "committed"):
            _report(done.token, done.attempt, done.record if done.token != "released" else None)
            if done.token != "released":
                _print_summary(done.doc, done.record, done.attempt)
            acted.append(f"released {done.attempt}" if done.token == "released" else
                         f"{done.token} {done.record}" + (f" for {done.attempt}" if done.attempt else ""))
            code = max(code, rc)
            continue
        _report_failure(done, acted)
        return max(code, rc)
    return code


def _acted_text(acted: list[str]) -> str:
    if not acted:
        return "no earlier item was acted on in this invocation"
    return "earlier in this invocation recovery " + "; ".join(acted)


def _report_failure(item: Item, acted: list[str]) -> None:
    """Print the report and the stderr line for an item that did not complete."""
    extra: dict[str, Any] = {}
    code = item.reason.split(":", 1)[0] if item.token == "commit-refused" else None
    if code is not None:
        extra["code"] = code
    if item.landed:
        extra["commit_landed"] = True
    extra.update(item.residue)
    extra.update(_refusal_paths(item.moved, item.call_staged))
    _report(item.token, item.attempt, item.record, **extra)
    held = "the attempt stays open for the owner" if item.attempt else "the record stays uncommitted for the owner"
    if item.token == "commit-refused" and item.landed:
        what = (f"a commit landed that recovery cannot prove equals the pending copy ({item.reason}). "
                f"HEAD moved; the pending copy is kept and {held}. {_landed_remedy(code)}"
                f"{_paths_text(item.moved, item.call_staged)}"
                + (f". {_TIMEOUT_REMEDY}" if code != "timeout" and _needs_remedy(code, item.moved) else ""))
    elif item.token == "commit-refused":
        what = (f"{item.reason}. This item's commit did not land: {_residue_text(item.residue)}, and {held}"
                f"{_paths_text(item.moved, item.call_staged)}"
                + (f". {_TIMEOUT_REMEDY}" if _needs_remedy(code, item.moved) else ""))
    else:
        what = f"{item.reason}. The pending copy is kept and {held}"
    _err(f"{_PROG}: {item.token}: {what}; {_acted_text(acted)}")


#: The owner's remedy for a timed-out call, or a refused call that names moved outside work, in the
#: order the owner takes it.
_TIMEOUT_REMEDY = ("First look for work a hook set aside (`git stash list`; a pre-commit framework keeps a "
                   "backup patch under its cache directory) and restore it. Then, once no git process is "
                   "running, remove a stale index.lock. Then run recovery again.")


def _needs_remedy(code: str | None, moved: tuple) -> bool:
    """Whether a refusal gets the owner's remedy order: a timeout, or any refusal whose call names
    outside work it moved (a hook that set the work aside and failed before its restore)."""
    return code == "timeout" or bool(moved)


def _path_label(path: str) -> str:
    """One path for stderr: each control character shown as `?`, and the name withheld when it
    matches the secret scan, so one bad name never withholds the line or the remedy order."""
    text = "".join("?" if unicodedata.category(c) == "Cc" or c in "\u2028\u2029" else c for c in str(path))
    return _WITHHELD if ss.scan_text(text) else text


def _refusal_paths(moved: tuple, staged: tuple) -> dict:
    """The report keys for the paths a refused call names, present only when non-empty."""
    out: dict[str, Any] = {}
    if moved:
        out["outside_moved"] = list(moved)
    if staged:
        out["owned_staged"] = list(staged)
    return out


def _paths_text(moved: tuple, staged: tuple) -> str:
    """The stderr clause naming the paths a refused call names, or an empty string."""
    parts = []
    paths = tuple(p for p in moved if p != council_commit.MOVED_UNKNOWN)
    if council_commit.MOVED_UNKNOWN in moved:
        # A marker, never a path: the comparison after the stop did not run.
        parts.append("The call could not compare the outside state after it stopped, so work outside "
                     "the commit may have moved: check `git status` and `git stash list`")
    if paths:
        parts.append(("the call" if parts else "The call") + " found these outside paths changed: "
                     + ", ".join(_path_label(p) for p in paths))
    if staged:
        parts.append(("it left" if parts else "The call left") + " these owned paths staged: "
                     + ", ".join(_path_label(p) for p in staged))
    return (". " + "; ".join(parts)) if parts else ""


def _landed_remedy(code: str | None) -> str:
    """The owner's next step after a refused commit that landed, worded by the refusal's code."""
    if code == "mismatch":
        return ("The owner's remedy: fix the hook that changed the record, then run recovery with "
                "--owner-commit-pending")
    if code == "timeout":
        return ("The call ran out of time after the commit landed. " + _TIMEOUT_REMEDY + " Recovery then "
                "recognises the commit when HEAD holds the pending copy's bytes, and reports a mismatch "
                "otherwise")
    return ("Run recovery again: it recognises the commit when HEAD holds the pending copy's bytes, and "
            "reports a mismatch otherwise")


def _residue_text(residue: dict) -> str:
    """What a refused commit that did not land left, stated only as far as recovery observed it."""
    parts = ["HEAD did not move"]
    staged = residue.get("staged") or []
    if staged:
        parts.append("the index changed at " + ", ".join(staged))
    elif residue.get("index_lock_left"):
        parts.append("the index entries are unchanged")
    else:
        parts.append("the index is as it was")
    if residue.get("index_lock_left"):
        parts.append("git left its index.lock, so the owner removes it once no git process is running")
    moved = residue.get("worktree_moved") or []
    if moved:
        kept = [p for p in moved if p in staged]
        text = "the working tree differs from before at " + ", ".join(moved)
        if kept:
            text += " (recovery keeps a file the index stages: " + ", ".join(kept) + ")"
        parts.append(text)
    else:
        parts.append("the working-tree files recovery owns are as they were")
    parts.append("the pending copy differs from the bytes recovery read" if residue.get("pending_moved")
                 else "the pending copy is kept")
    return "; ".join(parts)


if __name__ == "__main__":
    sys.exit(main())
