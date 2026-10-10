"""The runner-commit contract: one way for the council runner, its recovery mode and the run-work
witness writer to commit the paths they own.

A commit made here holds exactly the owned paths. It leaves every staged and unstaged change
outside them as it was, honours the user's git configuration (identity, signing, hooks path) with
hooks enabled, gives no git child a key, and is verified byte-exact before success is claimed.

What lives here:

* `git_child_env()`: the environment of every git child. The ambient environment minus the
  repository-redirecting variables, minus `OPENROUTER_API_KEY`, and minus every name the crux env
  file holds (the names are read through `crux_env.load_all()`; `crux_env` parses and caches the
  values, and nothing here uses or prints them). Pathspecs are literal, replace refs and grafts are off. Global and system configuration
  are NOT nulled, unlike the read helper in `council_records`. A malformed env file raises
  `EnvUnreadable`, whose message never quotes the file.
* `sequence_in_progress(repo)` and `index_locked(repo)`: the two refusals checked before a commit.
  A merge, cherry-pick, revert, rebase, am or sequencer run counts as a sequence; a bisect does
  not. Each marker path comes from `git rev-parse --git-path`, so a linked worktree is read right.
* `write_pending()` / `remove_pending()`: the pending copy under the repository's git directory,
  created exclusively, never through a symlink, and flushed with its directory.
* `scan_output(text, exact)`: captured git and hook output, withheld by label when it matches a
  key shape or an exact value. Raw output that matched is never returned.
* `commit_owned(repo, owned, message, ...)`: the commit itself, then `verify_commit(...)`. Raises
  `CommitRefused` with one of the codes in `REFUSAL_CODES`. Its `timeout` bounds the whole call:
  one deadline is taken at entry, and each git child gets only the time left before it.
* `path_state(repo, rel)`: HEAD's bytes and the index's bytes at one path, so a caller can refuse
  a path whose index holds a staged version that is neither HEAD's nor the bytes it would commit.
  An intent-to-add entry (`git add -N`) stages no bytes and reads as no index entry.
* `git_internal_path(rel)`: whether a repository-relative path names the git directory.
* `read_witness(run_dir)` and `witness_sha(run_dir, rel)`: the run-work witness reader, shared by
  the witness writer and the council runner's preflight.

"Outside state" means what git can see. A commit is checked against the index entries outside
the owned paths and against the modified, deleted and untracked-not-ignored paths there, each with
its working-tree digest. Two changes are invisible to that check: an edit to an ignored file, and a
same-size rewrite of a clean tracked file whose mtime is restored under `core.trustctime=false`.
Both need a hostile hook, which is outside this tool's threat model (a local tool run on the user's
own code).

A timeout stops the git process group in two steps. SIGINT goes first, so a hook framework's
`finally` or `trap` restores the work it set aside and git removes its own `index.lock`. Git gets
`_GRACE_SECONDS` to exit. A group still alive after that is killed with SIGKILL, and the output
pipes are drained under their own short bound. When git exits within the grace, a hook member that
ignored SIGINT and holds no output pipe is still in the group: it gets the rest of the grace, then
SIGKILL, before the outside state is compared. A timeout can still leave residue:

* `index.lock`, when git ignored SIGINT and the kill took it;
* an owned path this call added, still staged (`CommitRefused.staged`), because the call does not
  unstage after a timeout;
* the user's work, when a hook set it aside and did not restore it. It then sits in the hook's own
  backup: a pre-commit framework keeps a patch under its cache directory, and a hook that used
  `git stash` leaves an entry in `git stash list`.

HEAD does not move unless the commit landed before the stop. When the timeout came after the call's
snapshot, the call compares the outside index, the outside working tree and `refs/stash` with that
snapshot, under its own `_COMPARE_SECONDS` bound. A nonzero `git add` or `git commit` makes the same
comparison, because a hook can set the user's work aside and fail before it restores it (a `set -e`
hook whose lint step fails). The refusal names every moved path in `detail` and in
`CommitRefused.moved`, and names a moved stash as `refs/stash`. When the comparison cannot finish,
the detail says the outside state was not compared and `moved` holds `MOVED_UNKNOWN`. The user's
remedy, in this order: look for the set-aside work (`git stash list`, or the hook framework's backup
patch) and restore it; only then remove a stale `index.lock`; then run recovery. A timeout with
nothing moved still leaves the attempt open, and recovery handles it.

Callers reach these functions attribute-style (`council_commit.commit_owned(...)`), so a test
shim can replace one of them to simulate a crash at that point.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import council_records as cr  # noqa: E402
import crux_env  # noqa: E402
import secret_scan as ss  # noqa: E402

#: The gateway key's environment name. Dropped from every git child even when the env file does
#: not name it.
GATEWAY_KEY_NAME = "OPENROUTER_API_KEY"

#: Every code `CommitRefused` carries.
REFUSAL_CODES = ("sequence-in-progress", "index-locked", "hook-or-commit-failed", "timeout", "mismatch")

#: Markers git keeps while a sequence is in progress, resolved with `git rev-parse --git-path`.
#: `rebase-apply` covers `git am` too (`rebase-apply/applying`). BISECT_* is not a sequence: a
#: bisect leaves HEAD detached but holds no partial commit state.
SEQUENCE_MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply",
                    "sequencer")

#: The bound, in seconds, on one runner-commit call: every git child the council runner, its
#: recovery mode and the run-work witness writer start through this module shares it.
GIT_TIMEOUT_SECONDS = 120.0

#: Seconds git gets, after SIGINT, to exit and let its hooks restore what they set aside, before the
#: process group is killed. A module constant so a test can shorten it.
_GRACE_SECONDS = 10.0

#: The bound, in seconds, on each read made after a timeout (the outside-state comparison, the
#: landed-HEAD read, the staged-path read): each takes its own fresh deadline, because the call's
#: deadline has run out by then.
_COMPARE_SECONDS = 20.0

# Worst-case wall time of one timed-out call, with these defaults: the call's GIT_TIMEOUT_SECONDS
# (120), then the grace (10) and the bounded drain (2), then three post-timeout reads, each up to
# _COMPARE_SECONDS plus its own grace and drain (3 x 32): about 230 seconds. Python-side digesting
# of the outside working tree adds to that. "Its 120-second bound" in prose is when the stop
# starts, not when the call returns.

#: What `CommitRefused.moved` holds when a timed-out call could not compare the outside state.
MOVED_UNKNOWN = "<outside state not compared>"

#: The run-work witness file name, in the run directory (never under `council/`).
WITNESS_FILE = "run-work-witness.json"

#: Captured output longer than this is cut, from the front, after it has been scanned whole.
_DETAIL_LIMIT = 4000
#: The bound on the stop's own text when a moved-work clause follows it, so the clause always fits.
_HEAD_LIMIT = _DETAIL_LIMIT // 2


class CommitRefused(Exception):
    """A runner commit that did not happen, or did not verify. `code` is one of `REFUSAL_CODES`;
    `detail` is scanned text and never carries a matched value. `moved` names the outside state a
    timed-out or failed call found changed against its snapshot: repository-relative paths, plus the
    literal `refs/stash` when the stash moved. `staged` names the owned paths the call added to the
    index and could not take back. Both are empty when there is nothing to name. `base` is `detail`
    without its outside-state clause (what moved, and the remedy order), for a caller that names
    the moved paths and the remedy itself; it equals `detail` when nothing moved."""

    def __init__(self, code: str, detail: str = "", *, moved: Iterable[str] = (), staged: Iterable[str] = (),
                 base: str | None = None):
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown refusal code {code!r}")
        self.code = code
        self.detail = detail
        self.moved = tuple(moved)
        self.staged = tuple(staged)
        self.base = detail if base is None else base
        super().__init__(f"{code}: {detail}" if detail else code)


class EnvUnreadable(Exception):
    """The crux env file cannot be read or parsed. Callers turn it into exit 2. The message names
    the error type only, never a line of the file."""


class WitnessInvalid(Exception):
    """A run-work witness file that is a symlink, is not parseable JSON, or fails its schema."""


class _Deadline:
    """One time bound shared by every git child of a call. `left()` is the time remaining, and
    raises `CommitRefused("timeout")` once none remains."""

    def __init__(self, seconds: float):
        self.seconds = float(seconds)
        self.end = time.monotonic() + self.seconds

    def left(self) -> float:
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise CommitRefused("timeout", f"the call exceeded its {self.seconds:g} s bound")
        return remaining


def _seconds(timeout: "float | _Deadline") -> float:
    return timeout.left() if isinstance(timeout, _Deadline) else float(timeout)


# ───────────────────────────── the git child environment ─────────────────────────────


def _env_file_names() -> set[str]:
    try:
        names = set(crux_env.load_all())  # only the names are used; crux_env caches the parsed values
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        # The parser's message quotes the offending line, so it is never relayed or chained.
        raise EnvUnreadable(f"the crux env file cannot be read ({type(exc).__name__})") from None
    return names


def git_child_env() -> dict[str, str]:
    """The environment for every git child: the ambient environment minus
    `council_records.GIT_REDIRECT_VARS`, minus `OPENROUTER_API_KEY`, minus every name the crux env
    file holds; with GIT_LITERAL_PATHSPECS=1, GIT_NO_REPLACE_OBJECTS=1 and GIT_GRAFT_FILE set to
    `council_records.NO_GRAFT_FILE`. Global and system configuration stay as the user set them.
    Raises `EnvUnreadable` on a malformed env file."""
    drop = set(cr.GIT_REDIRECT_VARS) | {GATEWAY_KEY_NAME} | _env_file_names()
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["GIT_LITERAL_PATHSPECS"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = cr.NO_GRAFT_FILE
    return env


#: Seconds a killed git child's output pipes are drained before they are closed.
_DRAIN_SECONDS = 2.0


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """Send `sig` to the process group `proc` leads (git and any hook it started)."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        if sig == signal.SIGKILL:
            proc.kill()


def _group_alive(pgid: int) -> bool:
    """Whether the process group `pgid` still has a member. A group that cannot be signalled for a
    reason other than having no member counts as alive."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_group_survivors(pgid: int, grace_end: float) -> None:
    """Wait until `grace_end` for the group `pgid` to empty, then SIGKILL what is left of it and
    wait up to `_DRAIN_SECONDS` for the kill to take effect. A group with no member returns at
    once."""
    while _group_alive(pgid) and time.monotonic() < grace_end:
        time.sleep(0.05)
    if not _group_alive(pgid):
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    end = time.monotonic() + _DRAIN_SECONDS
    while _group_alive(pgid) and time.monotonic() < end:
        time.sleep(0.02)


def _run(repo: Path, args: list[str], env: Mapping[str, str], timeout: "float | _Deadline",
         stdin: bytes | None = None) -> subprocess.CompletedProcess:
    """Run git in its own process group, bounded by `timeout` seconds, or by the time left before
    a shared `_Deadline`. On timeout the whole group (git and any hook it started) gets SIGINT,
    then SIGKILL once `_GRACE_SECONDS` have passed, and `CommitRefused("timeout")` is raised. When
    git exits within the grace, a group member still alive at the grace's end is killed too."""
    seconds = _seconds(timeout)
    proc = subprocess.Popen(["git", "-C", str(repo), *args], stdin=subprocess.PIPE if stdin is not None
                            else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=dict(env), start_new_session=True)
    try:
        out, err = proc.communicate(stdin, timeout=seconds)
    except subprocess.TimeoutExpired:
        # SIGINT first: a hook's `finally` or `trap` restores the work it set aside, and git
        # removes its own index lock. Only a group still alive after the grace is killed.
        grace_end = time.monotonic() + _GRACE_SECONDS
        _signal_group(proc, signal.SIGINT)
        try:
            proc.communicate(timeout=_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGKILL)
            # A hook descendant that left the group can still hold the output pipes, so the
            # drain is bounded too: past it the pipes are closed and only the killed git child is
            # reaped.
            try:
                proc.communicate(timeout=_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                for pipe in (proc.stdout, proc.stderr):
                    try:
                        pipe.close()
                    except OSError:
                        pass
                proc.wait()
        else:
            # Git exited, but a hook member that ignores SIGINT and holds no output pipe can still
            # be in the group. It gets the rest of the grace, then SIGKILL, so it cannot change a
            # file after the comparison that follows has reported.
            _kill_group_survivors(proc.pid, grace_end)
        raise CommitRefused("timeout", f"git {args[0]} exceeded {seconds:g} s and was stopped") from None
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _git_paths(repo: Path, names: Iterable[str], env: Mapping[str, str], timeout: float) -> list[Path] | None:
    names = list(names)
    args = ["rev-parse"]
    for n in names:
        args += ["--git-path", n]
    r = _run(repo, args, env, timeout)
    if r.returncode != 0:
        return None
    lines = os.fsdecode(r.stdout).splitlines()
    if len(lines) != len(names):
        return None
    return [Path(line) if Path(line).is_absolute() else repo / line for line in lines]


def sequence_in_progress(repo: Path | str, *, env: Mapping[str, str] | None = None,
                         timeout: float = GIT_TIMEOUT_SECONDS) -> str | None:
    """The first in-progress sequence marker git holds for `repo`, or None. When git cannot name
    the marker paths the answer is `"unreadable"`, which refuses as a sequence would."""
    repo = Path(repo)
    paths = _git_paths(repo, SEQUENCE_MARKERS, env if env is not None else git_child_env(), timeout)
    if paths is None:
        return "unreadable"
    for name, path in zip(SEQUENCE_MARKERS, paths):
        if os.path.lexists(path):
            return name
    return None


def index_locked(repo: Path | str, *, env: Mapping[str, str] | None = None, timeout: float = GIT_TIMEOUT_SECONDS) -> bool:
    """True when the repository's index lock file exists (or cannot be located)."""
    repo = Path(repo)
    paths = _git_paths(repo, ["index.lock"], env if env is not None else git_child_env(), timeout)
    return paths is None or os.path.lexists(paths[0])


# ───────────────────────────── the pending copy ─────────────────────────────


def _component(value: str, what: str) -> str:
    return cr.pending_component(value, what)


def _open_pending_dir(repo: Path | str, book_id: str, run_id: str, create: bool) -> int | None:
    """`council_records.open_pending_dir`: the component-wise O_NOFOLLOW walk to the pending
    directory, shared with the gate check's reader."""
    return cr.open_pending_dir(repo, book_id, run_id, create)


def write_pending(repo: Path | str, book_id: str, run_id: str, name: str, data: bytes) -> Path:
    """Write `data` to `<git dir>/crux/council-pending/<book_id>/<run_id>/<name>` and return the
    path. The file is created with O_CREAT|O_EXCL|O_NOFOLLOW (an existing file raises
    FileExistsError), and the file and its directory are flushed. A symlinked directory component
    raises OSError. A failed write or flush unlinks the partial file before the error is
    re-raised. Callers write the pending copy before the working-tree record."""
    _component(name, "record name")
    fd = _open_pending_dir(repo, book_id, run_id, create=True)
    try:
        ffd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(ffd, view):]
                os.fsync(ffd)
            finally:
                os.close(ffd)
        except BaseException:
            # A partial copy would read as the runner's bytes to recovery; take it back.
            try:
                os.unlink(name, dir_fd=fd)
            except OSError:
                pass
            raise
        os.fsync(fd)
    finally:
        os.close(fd)
    return cr.pending_dir(repo, book_id, run_id) / name


def remove_pending(repo: Path | str, book_id: str, run_id: str, name: str) -> bool:
    """Remove a pending copy; True when one was removed. Never follows a symlinked component."""
    _component(name, "record name")
    fd = _open_pending_dir(repo, book_id, run_id, create=False)
    if fd is None:
        return False
    try:
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            return False
        os.fsync(fd)
        return True
    finally:
        os.close(fd)


# ───────────────────────────── output scanning ─────────────────────────────


def scan_output(text: str | bytes, exact: Iterable[str] = ()) -> str:
    """`text` unchanged when it holds no key shape and no `exact` value; otherwise a fixed
    placeholder naming the matched labels. A matched value is never returned."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", errors="replace")
    if isinstance(exact, str):
        exact = (exact,)
    labels = ss.scan_text(text, exact=list(exact))
    if labels:
        return f"[output withheld: matched {', '.join(labels)}]"
    return text


def _printable(text: str) -> str:
    """`text` with every control character except newline and tab replaced by "?": C0, DEL, C1
    (U+009B introduces a terminal control sequence) and the line and paragraph separators, the set
    the council runner's own stderr check uses. A hook's terminal escapes are never relayed."""
    return "".join("?" if c not in "\n\t" and (unicodedata.category(c) == "Cc" or c in "\u2028\u2029")
                   else c for c in text)


def _detail(r: subprocess.CompletedProcess, exact: Iterable[str]) -> str:
    text = _printable(scan_output(r.stdout + r.stderr, exact)).strip()
    return text if len(text) <= _DETAIL_LIMIT else "..." + text[-_DETAIL_LIMIT:]


# ───────────────────────────── commit and verification ─────────────────────────────


@dataclass(frozen=True)
class Snapshot:
    """The state a runner commit must not disturb: HEAD before the commit (None on an unborn
    branch), the `ls-files -s` entries outside the owned set, and, outside it, every modified,
    deleted or untracked-not-ignored path with its working-tree digest (None when absent), and the
    object `refs/stash` named (None when there is no stash)."""

    head: str | None
    index_outside: tuple[bytes, ...]
    worktree_outside: tuple[tuple[str, str | None], ...]
    env: Mapping[str, str]
    timeout: "float | _Deadline"
    stash: str | None = None


def git_internal_path(rel: str) -> bool:
    """True when the repository-relative `rel` has a component that names a git directory
    (`.git`, compared case-insensitively, as git itself refuses it on a case-insensitive volume)."""
    return any(part.casefold() == ".git" for part in rel.replace("\\", "/").split("/"))


def _validate_owned(repo: Path, owned: Mapping[str, bytes], sealed: Iterable[str]) -> list[str]:
    if not owned:
        raise ValueError("commit_owned needs at least one owned path")
    rels = []
    for rel, data in owned.items():
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError(f"the bytes for {rel!r} are not bytes")
        try:
            _abs, norm = cr.resolve_in_repo(repo, rel)
        except cr.PathRefused as exc:
            raise ValueError(f"owned path refused: {exc.message}") from None
        if norm != rel or git_internal_path(rel):
            raise ValueError(f"owned path {rel!r} is not a normalized repository-relative path")
        rels.append(rel)
    for rel in sealed:
        if rel not in owned:
            raise ValueError(f"sealed path {rel!r} is not owned")
    return sorted(rels)


def _worktree_digest(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        return "symlink:" + cr.sha256_bytes(os.fsencode(os.readlink(path)))
    if stat.S_ISREG(st.st_mode):
        return cr.sha256_file(path)
    return "other:" + oct(stat.S_IFMT(st.st_mode))


def _worktree_bytes(path: Path) -> bytes | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return path.read_bytes()


def _head(repo: Path, env: Mapping[str, str], timeout: float) -> str | None:
    r = _run(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"], env, timeout)
    return os.fsdecode(r.stdout).strip() if r.returncode == 0 else None


def _stash(repo: Path, env: Mapping[str, str], timeout: "float | _Deadline") -> str | None:
    """The object `refs/stash` names, or None when there is no stash."""
    r = _run(repo, ["rev-parse", "--verify", "-q", "refs/stash"], env, timeout)
    return os.fsdecode(r.stdout).strip() if r.returncode == 0 else None


def _index_entries(repo: Path, env: Mapping[str, str], timeout: float) -> list[bytes]:
    r = _run(repo, ["ls-files", "-s", "-z"], env, timeout)
    if r.returncode != 0:
        raise CommitRefused("mismatch", "the index cannot be listed")
    return [e for e in r.stdout.split(b"\x00") if e]


def _entry_path(entry: bytes) -> str:
    return os.fsdecode(entry.split(b"\t", 1)[1])


def _outside_worktree(repo: Path, owned: set[str], env: Mapping[str, str],
                      timeout: float) -> tuple[tuple[str, str | None], ...]:
    r = _run(repo, ["ls-files", "-z", "-m", "-d", "-o", "--exclude-standard"], env, timeout)
    if r.returncode != 0:
        raise CommitRefused("mismatch", "the working tree cannot be listed")
    paths = {os.fsdecode(p) for p in r.stdout.split(b"\x00") if p} - owned
    return tuple(sorted((p, _worktree_digest(repo / p)) for p in paths))


def snapshot(repo: Path | str, owned: Iterable[str], env: Mapping[str, str] | None = None,
             timeout: "float | _Deadline" = GIT_TIMEOUT_SECONDS) -> Snapshot:
    """The `Snapshot` of `repo` around the owned paths, taken before a runner commit."""
    repo = Path(repo).resolve()
    env = dict(env) if env is not None else git_child_env()
    owned = set(owned)
    index = tuple(e for e in _index_entries(repo, env, timeout) if _entry_path(e) not in owned)
    return Snapshot(_head(repo, env, timeout), index, _outside_worktree(repo, owned, env, timeout),
                    env, timeout, _stash(repo, env, timeout))


def _blob(repo: Path, spec: str, env: Mapping[str, str], timeout: float) -> bytes | None:
    r = _run(repo, ["cat-file", "blob", spec], env, timeout)
    return r.stdout if r.returncode == 0 else None


@dataclass(frozen=True)
class PathState:
    """One path's bytes in HEAD and in the index. `head` is None when HEAD does not hold the path
    (or the branch is unborn); `index` is None when the index holds no stage-0 entry for it, or
    holds only an intent-to-add entry (`git add -N`), which stages no bytes; `unmerged` is True
    when it holds a higher-stage entry."""

    head: bytes | None
    index: bytes | None
    unmerged: bool

    def holds_other_version(self, data: bytes) -> bool:
        """True when the index holds a staged state of the path that is neither HEAD's nor
        `data`: a staged edit, a staged deletion, or an unmerged entry. `git commit --only` would
        replace that state, so committing over it loses it."""
        if self.unmerged:
            return True
        if self.index is None:
            return self.head is not None
        return self.index != self.head and self.index != bytes(data)


def _stage(entry: bytes) -> bytes:
    return entry.split(b"\t", 1)[0].split(b" ")[-1]


#: The intent-to-add bit of an index entry's flags (`CE_INTENT_TO_ADD`, 1 << 29), as
#: `git ls-files --debug` prints the flags in hexadecimal.
_INTENT_TO_ADD = 1 << 29


def _intent_to_add(repo: Path, rel: str, env: Mapping[str, str], timeout: "float | _Deadline") -> bool:
    """True when `rel`'s index entry was made by `git add -N`: it records that the path will be
    added and stages no bytes. Its blob is the empty blob, like a staged empty file's, so only the
    entry's flags tell the two apart. Flags that cannot be read answer False, which keeps the
    entry a staged version and the commit refused."""
    r = _run(repo, ["ls-files", "--debug", "--", rel], env, timeout)
    if r.returncode != 0:
        return False
    flags = [line.split(b"flags:", 1)[1].strip() for line in r.stdout.splitlines() if b"flags:" in line]
    if len(flags) != 1:
        return False
    try:
        return bool(int(flags[0], 16) & _INTENT_TO_ADD)
    except ValueError:
        return False


def _path_state(repo: Path, rel: str, head: str | None, entries: list[bytes], env: Mapping[str, str],
                timeout: "float | _Deadline") -> PathState:
    mine = [e for e in entries if _entry_path(e) == rel]
    stage0 = [e for e in mine if _stage(e) == b"0"]
    index = _blob(repo, os.fsdecode(stage0[0].split(b" ")[1]), env, timeout) if stage0 else None
    head_bytes = _blob(repo, f"{head}:{rel}", env, timeout) if head else None
    if index == b"" and head_bytes is None and len(mine) == 1 and _intent_to_add(repo, rel, env, timeout):
        index = None  # `git add -N`: no staged bytes, so nothing is lost by committing over it
    return PathState(head_bytes, index, len(stage0) != len(mine))


def path_state(repo: Path | str, rel: str, *, env: Mapping[str, str] | None = None,
               timeout: float = GIT_TIMEOUT_SECONDS) -> PathState:
    """The `PathState` of repository-relative `rel`. Raises `EnvUnreadable` on a malformed env
    file, and `CommitRefused("mismatch")` when the index cannot be listed."""
    repo = Path(repo).resolve()
    env = dict(env) if env is not None else git_child_env()
    deadline = _Deadline(timeout)
    return _path_state(repo, rel, _head(repo, env, deadline), _index_entries(repo, env, deadline), env,
                       deadline)


def verify_commit(repo: Path | str, before: Snapshot, owned: Mapping[str, bytes], *,
                  sealed: Iterable[str] = ()) -> str:
    """Prove the commit just made is the runner's commit and return the new HEAD.

    HEAD's only parent is `before.head` (no parent on an unborn branch); the commit changes exactly
    the owned paths; each committed blob, stage-0 index entry and working-tree file equals the
    owned bytes; each `sealed` path's committed bytes pass `council_records.seal_holds`; and the
    index entries and working-tree state outside the owned set equal `before`. Any failure raises
    `CommitRefused("mismatch")`. A git exit code alone is never taken as proof."""
    repo = Path(repo).resolve()
    env, timeout = before.env, before.timeout
    owned_set = set(owned)
    new = _head(repo, env, timeout)
    if new is None or new == before.head:
        raise CommitRefused("mismatch", "HEAD did not move to a new commit")
    r = _run(repo, ["rev-list", "--parents", "-n", "1", new], env, timeout)
    parents = os.fsdecode(r.stdout).split()[1:] if r.returncode == 0 else None
    if parents != ([before.head] if before.head else []):
        raise CommitRefused("mismatch", "the new commit's parent is not the previous HEAD")
    diff_args = ["diff-tree", "-r", "-z", "--no-commit-id", "--name-only", "--no-renames"]
    diff_args += [before.head, new] if before.head else ["--root", new]
    r = _run(repo, diff_args, env, timeout)
    changed = {os.fsdecode(p) for p in r.stdout.split(b"\x00") if p} if r.returncode == 0 else None
    if changed != owned_set:
        raise CommitRefused("mismatch", "the commit does not change exactly the owned paths")
    index_now = _index_entries(repo, env, timeout)
    for rel in sorted(owned_set):
        data = bytes(owned[rel])
        if _blob(repo, f"{new}:{rel}", env, timeout) != data:
            raise CommitRefused("mismatch", f"{rel}: the committed bytes differ from the computed bytes")
        entries = [e for e in index_now if _entry_path(e) == rel]
        fields = entries[0].split(b"\t", 1)[0].split(b" ") if len(entries) == 1 else []
        if len(fields) != 3 or fields[2] != b"0" or not fields[0].startswith(b"100"):
            raise CommitRefused("mismatch", f"{rel}: the index does not hold one regular stage-0 entry")
        if _blob(repo, os.fsdecode(fields[1]), env, timeout) != data:
            raise CommitRefused("mismatch", f"{rel}: the index bytes differ from the computed bytes")
        if _worktree_bytes(repo / rel) != data:
            raise CommitRefused("mismatch", f"{rel}: the working-tree bytes differ from the computed bytes")
    for rel in sealed:
        if not cr.seal_holds(bytes(owned[rel])):
            raise CommitRefused("mismatch", f"{rel}: the committed bytes fail their seal")
    if tuple(e for e in index_now if _entry_path(e) not in owned_set) != before.index_outside:
        raise CommitRefused("mismatch", "index entries outside the owned paths moved")
    if _outside_worktree(repo, owned_set, env, timeout) != before.worktree_outside:
        raise CommitRefused("mismatch", "working-tree changes outside the owned paths moved")
    return new


def commit_owned(repo: Path | str, owned: Mapping[str, bytes], message: str, *, timeout: float = GIT_TIMEOUT_SECONDS,
                 exact: Iterable[str] = (), sealed: Iterable[str] = ()) -> str:
    """Commit exactly the `owned` paths (repository-relative path -> the bytes the caller computed
    and has already written to the working tree), then verify the commit. Returns the new HEAD.

    Order: refuse an in-progress sequence, then a held index lock; refuse working-tree bytes that
    differ from `owned` (or a `sealed` path whose bytes fail their seal) before any commit;
    snapshot; `git add -- <path>` for each owned path not in the index; `git commit -q -m
    <message> --only -- <paths>` with hooks enabled, under `git_child_env()` and the time bound;
    then `verify_commit`. Never `--no-verify`, `-a` or `-A`. An owned path whose index holds a
    staged state that is neither HEAD's nor the owned bytes (`PathState.holds_other_version`)
    refuses with `mismatch` before any commit, because `--only` would replace that state.
    `timeout` bounds the whole call: each git child gets the time left before one deadline. A
    call that runs out sends SIGINT to the git process group, waits `_GRACE_SECONDS` for git to
    exit (a hook's `finally` or `trap` restores what it set aside, and git removes its own
    `index.lock`), then sends SIGKILL, and raises `timeout`. A timeout can still leave an
    `index.lock` (git ignored SIGINT), an owned path staged, and a hook's stash or backup patch
    holding the user's work when the hook did not restore it. A timeout after the snapshot
    compares the outside index, working tree and `refs/stash` with it, and the refusal's detail
    and `moved` name every moved path (`refs/stash` for the stash, `MOVED_UNKNOWN` when the
    comparison could not finish); `staged` names the owned paths the call added and left staged.
    The remedy order is: restore the set-aside work, then remove a stale `index.lock`, then run
    recovery. A nonzero `git add` or `git commit` raises `hook-or-commit-failed`, tries to unstage
    each path this call added, while time remains, and names in `staged` the added paths the index
    still holds; it compares the outside state with the snapshot as a timeout does, and names what
    moved in `moved` and the detail. The unstage is `git rm --cached` without `-f`, so it
    fails, and the path stays staged, when a hook changed the path's working-tree file: the staged
    bytes then differ from both that file and HEAD. A caller that wrote the file reads the index
    before it removes the file. An intent-to-add entry counts as no staged version. Git's captured
    output reaches the refusal only through `scan_output`. `message` is caller-fixed text; a message
    that matches a key shape or an `exact` value raises ValueError before any git child runs.
    Raises `EnvUnreadable` when the crux env file is malformed."""
    repo = Path(repo).resolve()
    exact = (exact,) if isinstance(exact, str) else tuple(exact)
    sealed = (sealed,) if isinstance(sealed, str) else tuple(sealed)
    rels = _validate_owned(repo, owned, sealed)
    if ss.scan_text(message, exact=list(exact)):
        raise ValueError("the commit message matches a secret shape")
    env = git_child_env()
    deadline = _Deadline(timeout)

    seq = sequence_in_progress(repo, env=env, timeout=deadline)
    if seq:
        raise CommitRefused("sequence-in-progress", seq)
    if index_locked(repo, env=env, timeout=deadline):
        raise CommitRefused("index-locked", "the index lock file exists")
    for rel in rels:
        if _worktree_bytes(repo / rel) != bytes(owned[rel]):
            raise CommitRefused("mismatch", f"{rel}: the working-tree bytes differ from the computed bytes")
    for rel in sealed:
        if not cr.seal_holds(bytes(owned[rel])):
            raise CommitRefused("mismatch", f"{rel}: the bytes fail their seal")

    entries = _index_entries(repo, env, deadline)
    head = _head(repo, env, deadline)
    for rel in rels:
        if _path_state(repo, rel, head, entries, env, deadline).holds_other_version(bytes(owned[rel])):
            raise CommitRefused("mismatch", f"{rel}: the index holds a staged version that is neither "
                                            "HEAD's nor the computed bytes")

    before = snapshot(repo, rels, env, deadline)
    in_index = {_entry_path(e) for e in entries}
    added: list[str] = []
    try:
        for rel in rels:
            if rel in in_index:
                continue
            r = _run(repo, ["add", "--", rel], env, deadline)
            if r.returncode != 0:
                _failed(repo, r, before, rels, added, env, deadline, exact)
            added.append(rel)
        r = _run(repo, ["commit", "-q", "-m", message, "--only", "--", *rels], env, deadline)
        if r.returncode != 0:
            _failed(repo, r, before, rels, added, env, deadline, exact)
        return verify_commit(repo, before, owned, sealed=sealed)
    except CommitRefused as exc:
        if exc.code != "timeout":
            raise
        moved = _moved_since(repo, before, set(rels), env)
        stop = exc.detail
        landed = _landed_head(repo, before, env)
        if landed:
            # The commit landed before the stop (a stalled post-commit hook, or a deadline that ran
            # out before verification), so it was never verified; recovery recognises it.
            stop = f"the commit landed as {landed} before the stop, unverified; {stop}"
        detail = _outside_detail(stop, moved, exact, timed_out=True)
        # A path whose `git add` finished just before the stop is not yet in `added`, so every
        # owned path that was not in the index is asked about; `diff --cached` names only staged ones.
        raise CommitRefused("timeout", detail, moved=moved, base=_bounded(_printable(scan_output(stop, exact))),
                            staged=_still_staged(repo, [r for r in rels if r not in in_index], env)) from None


def _failed(repo: Path, r: subprocess.CompletedProcess, before: Snapshot, rels: list[str], added: list[str],
            env: Mapping[str, str], deadline: _Deadline, exact: Iterable[str]) -> None:
    """Raise `hook-or-commit-failed` for a nonzero `git add` or `git commit`: unstage what this call
    added, then compare the outside state with the snapshot, because a hook that set the user's
    work aside and then failed (a `set -e` hook whose lint step failed before its restore) leaves
    that work moved. The refusal names every moved path in `moved` and in its detail."""
    _unstage(repo, added, env, deadline)
    moved = _moved_since(repo, before, set(rels), env)
    base = _detail(r, exact)
    raise CommitRefused("hook-or-commit-failed", _outside_detail(base, moved, exact, timed_out=False),
                        moved=moved, base=base, staged=_still_staged(repo, added, env))


def _landed_head(repo: Path, before: Snapshot, env: Mapping[str, str]) -> str | None:
    """HEAD after a timed-out call when it moved off `before.head`, under the comparison's own
    bound; None when it did not move or cannot be read."""
    try:
        now = _head(repo, env, _Deadline(_COMPARE_SECONDS))
    except CommitRefused:
        return None
    return now if now is not None and now != before.head else None


def _still_staged(repo: Path, added: list[str], env: Mapping[str, str]) -> tuple[str, ...]:
    """The paths in `added` (never tracked, added by this call) that the index still holds, under
    the comparison's own bound. When git cannot say, every added path is named: a refusal names
    what it may have left behind."""
    if not added:
        return ()
    try:
        r = _run(repo, ["diff", "--cached", "--name-only", "-z", "--", *added], env,
                 _Deadline(_COMPARE_SECONDS))
    except CommitRefused:
        return tuple(added)
    if r.returncode != 0:
        return tuple(added)
    return tuple(sorted(os.fsdecode(p) for p in r.stdout.split(b"\x00") if p))


def _moved_since(repo: Path, before: Snapshot, owned: set[str], env: Mapping[str, str]) -> tuple[str, ...]:
    """What a timed-out or failed call moved outside the owned paths: the repository-relative paths whose
    index entry or working-tree state differs from `before`, sorted, then `refs/stash` when the
    stash moved. Runs under its own fresh `_COMPARE_SECONDS` bound. When the comparison cannot
    complete the answer is `(MOVED_UNKNOWN,)`."""
    try:
        deadline = _Deadline(_COMPARE_SECONDS)
        index_now = tuple(e for e in _index_entries(repo, env, deadline) if _entry_path(e) not in owned)
        worktree_now = _outside_worktree(repo, owned, env, deadline)
        stash_now = _stash(repo, env, deadline)
    except (CommitRefused, OSError):
        return (MOVED_UNKNOWN,)
    moved = {_entry_path(e) for e in set(index_now) ^ set(before.index_outside)}
    moved |= {path for path, _digest in set(worktree_now) ^ set(before.worktree_outside)}
    names = sorted(moved)
    if stash_now != before.stash:
        names.append("refs/stash")
    return tuple(names)


def _path_label(path: str, exact: Iterable[str]) -> str:
    """One moved path for a detail: every control character shown as "?", and the whole name
    withheld when it matches the secret scan, so one bad name never withholds the rest."""
    text = "".join("?" if unicodedata.category(c) == "Cc" or c in "\u2028\u2029" else c for c in path)
    return "[withheld: matched the secret scan]" if ss.scan_text(text, exact=list(exact)) else text


def _bounded(text: str) -> str:
    return text if len(text) <= _DETAIL_LIMIT else text[:_DETAIL_LIMIT] + "..."


def _outside_detail(base: str, moved: tuple[str, ...], exact: Iterable[str], *, timed_out: bool) -> str:
    """The detail of a timed-out or failed call that came after the snapshot: the stop, what moved
    (every path, each labelled on its own, cut at `_DETAIL_LIMIT` with the total kept up front),
    and the order of the user's remedy. A failed call that moved nothing adds no clause: only a
    timeout claims that the outside state matched.

    The stop and the clause are bounded apart, the stop first: a hook that prints more than the
    bound keeps the end of its output to `_HEAD_LIMIT`, so it can never push the remedy and the
    moved paths out of the detail."""
    exact = tuple(exact)
    if moved == (MOVED_UNKNOWN,):
        tail = "the outside state was not compared"
    elif not moved:
        tail = "the outside index, working tree and stash match the snapshot" if timed_out else ""
    else:
        tail = (f"outside state moved ({len(moved)} in all): "
                "restore the set-aside work first (`git stash list`, or the hook framework's backup "
                "patch), then remove a stale index.lock, then run recovery; moved: "
                + ", ".join(_path_label(p, exact) for p in moved))
    head = _printable(scan_output(base, exact))
    if not tail:
        return _bounded(head)
    if len(head) > _HEAD_LIMIT:
        head = "..." + head[-_HEAD_LIMIT:]
    return f"{head}; {_bounded(tail)}"


def _unstage(repo: Path, added: list[str], env: Mapping[str, str], timeout: "float | _Deadline") -> None:
    """Take back the index entries this call added, so a failed commit leaves the index as it
    found it. Best effort: a failure here leaves the path staged, never anything else. Git refuses
    the unstage when a hook changed the path's working-tree file, because the staged bytes then
    differ from both the file and HEAD."""
    if added:
        try:
            _run(repo, ["rm", "--cached", "-q", "--ignore-unmatch", "--", *added], env, timeout)
        except CommitRefused:
            pass


# ───────────────────────────── the run-work witness reader ─────────────────────────────

_VP: Any = None


def _validator() -> Any:
    global _VP
    if _VP is None:
        spec = importlib.util.spec_from_file_location("_vp_for_council_commit", _HERE / "validate-promptbook.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _VP = mod
    return _VP


def witness_errors(doc: Any) -> list[str]:
    """Schema errors of `doc` against `crux/schemas/run-work-witness.schema.json`."""
    vp = _validator()
    schema = vp.load_schema(_HERE.parent / "schemas" / "run-work-witness.schema.json")
    errors: list[dict] = []
    vp.validate(doc, schema, "#", "#", errors, "run-work-witness")
    return [f"{e['instance_path']}: {e['error']}" for e in errors]


def read_witness(run_dir: Path | str) -> dict | None:
    """The parsed run-work witness of a run directory, or None when there is none. Raises
    `WitnessInvalid` for a symlinked, unparseable or schema-invalid file."""
    path = Path(run_dir) / WITNESS_FILE
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise WitnessInvalid(f"{path}: the witness file is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise WitnessInvalid(f"{path}: the witness file is not a regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        raw = fh.read()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise WitnessInvalid(f"{path}: the witness file is not JSON") from None
    errors = witness_errors(doc)
    if errors:
        raise WitnessInvalid(f"{path}: the witness file fails its schema: {errors[0]}")
    return doc


def witness_sha(run_dir: Path | str, rel: str) -> str | None:
    """The sha256 of the latest witness entry for repository-relative `rel`, or None."""
    doc = read_witness(run_dir)
    if doc is None:
        return None
    for entry in reversed(doc["entries"]):
        if entry["path"] == rel:
            return entry["sha256"]
    return None


__all__ = [
    "CommitRefused", "EnvUnreadable", "WitnessInvalid", "REFUSAL_CODES", "SEQUENCE_MARKERS", "MOVED_UNKNOWN",
    "GATEWAY_KEY_NAME", "GIT_TIMEOUT_SECONDS", "WITNESS_FILE", "Snapshot", "git_child_env", "sequence_in_progress",
    "index_locked", "write_pending", "remove_pending", "scan_output", "snapshot", "verify_commit",
    "commit_owned", "witness_errors", "read_witness", "witness_sha", "PathState", "path_state",
    "git_internal_path",
]
