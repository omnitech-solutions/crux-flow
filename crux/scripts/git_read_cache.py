"""A scoped memo for the repeated read-only git queries one migration proof makes.

One migration apply asks git the same two hundred questions about twenty thousand
times, one child process each. Inside `scope()`, a repeated read that this module
admits is answered from memory. Everything else runs git exactly as before.

What is cached:

* Only the closed set of argv shapes `_admitted` accepts. Each shape's output is a
  function of the object database, the index, HEAD, the shallow boundary and the
  repository configuration. None reads the working tree, attributes or ignore rules.
  Every revision such a read names is a full object id or HEAD, and every object spec
  is `<rev>`, `<rev>:<path>`, `<rev>^{commit}` or `:0:<path>`.
* Only under byte-identical repository state. The key holds the bytes of `.git/index`,
  `.git/HEAD`, the loose ref HEAD names, `.git/packed-refs`, `.git/config` and
  `.git/shallow`, read again on every call, with the git directory's real path, the
  argv and every `GIT_*` variable plus `PATH`. An object id names the same bytes for as
  long as the object exists, so a commit, a staged change or a moved HEAD is a new key.
* Only with replace refs and grafts turned off (`GIT_NO_REPLACE_OBJECTS=1` and a
  `GIT_GRAFT_FILE`), as `council_records` always passes. Otherwise `refs/replace/` and a
  grafts file would change answers the key does not cover.
* Only a successful read, plus the definite "not an ancestor" answer of
  `merge-base --is-ancestor`. An absent object is asked again every time.

A repository whose state cannot be read exactly is never cached: `<repo>/.git` must be
a real directory, and a linked worktree (`commondir`) or a reftable store is refused.

How it attaches. `council_records.git` runs `subprocess.run` through its module global
`subprocess`. The gate contract binds that function's source text, so the function is
never edited. While a scope is open, the global names a module proxy whose `run`
consults the memo of the scope that is active in the calling context, and passes every
other call to the real `subprocess.run`. The contract's source walk skips a module that
is not policy-owned, so it binds the same bytes either way. The last scope to close
restores whatever the global held before the first scope opened. The memo lives only for the outermost `with` block.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import stat
import subprocess
import threading
import types

import council_records

_MEMO: contextvars.ContextVar = contextvars.ContextVar("crux_git_read_memo", default=None)
_OID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
_REV_RE = re.compile(r"\A(?:%s|HEAD)\Z" % _OID)
_OBJECT_RE = re.compile(r"\A(?:(?:%s|HEAD)(?::.*|\^\{commit\})?|:0:.+)\Z" % _OID, re.S)
_BLOB_RE = re.compile(r"\A(?:%s|HEAD):.+\Z" % _OID, re.S)
_OID_RE = re.compile(r"\A%s\Z" % _OID)
_HEAD_OID_RE = re.compile(rb"\A%s\n?\Z" % _OID.encode())
_STATE_FILES = ("index", "HEAD", "packed-refs", "config", "shallow")
_FLAGS = {
    "ls-files": frozenset({"-s", "--stage", "-v", "-z"}),
    "ls-tree": frozenset({"-z", "-r", "--name-only"}),
    "log": frozenset({"--reverse", "--format=%H", "--format=", "--no-renames", "--diff-filter=d",
                      "--full-history", "--name-only", "-z"}),
    "rev-list": frozenset({"--reverse", "--topo-order", "--parents"}),
}
_REV_PARSE = frozenset({("HEAD",), ("--is-shallow-repository",), ("--absolute-git-dir",),
                        ("--show-toplevel",)})

_lock = threading.Lock()
_open_scopes = 0
_displaced = None


class _CachingSubprocess(types.ModuleType):
    """`subprocess` with a memoized `run` for admitted git reads."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, args, *pargs, **kwargs):
        memo = _MEMO.get()
        key = _key(args, pargs, kwargs) if memo is not None else None
        if key is not None:
            hit = memo.get(key)
            if hit is not None:
                return subprocess.CompletedProcess(list(hit[0]), hit[1], hit[2], hit[3])
        result = subprocess.run(args, *pargs, **kwargs)
        # Store only when the state did not move while git ran, so an answer is never
        # filed under a state it was not read under.
        if key is not None and (result.returncode == 0 or
                                (key[1][0] == "merge-base" and result.returncode == 1)) \
                and _repository_state(args[2]) == key[3]:
            memo[key] = (tuple(result.args), result.returncode, result.stdout, result.stderr)
        return result


_PROXY = _CachingSubprocess("subprocess")


@contextlib.contextmanager
def scope():
    """Answer repeated admitted git reads from memory inside the block.

    A nested block shares the outer block's memo."""
    global _open_scopes, _displaced
    if _MEMO.get() is not None:
        yield
        return
    with _lock:
        if _open_scopes == 0:
            _displaced = council_records.subprocess
            council_records.subprocess = _PROXY
        _open_scopes += 1
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)
        with _lock:
            _open_scopes -= 1
            if _open_scopes == 0:
                council_records.subprocess = _displaced
                _displaced = None


def _key(args, pargs, kwargs):
    """The memo key of one `subprocess.run` call, or None when it is not cached."""
    if pargs or set(kwargs) != {"capture_output", "env"} or kwargs["capture_output"] is not True:
        return None
    env = kwargs["env"]
    if not isinstance(env, dict) or not isinstance(args, list) or len(args) < 4:
        return None
    # The admitted shapes ignore replace refs and grafts only because git is told to.
    if env.get("GIT_NO_REPLACE_OBJECTS") != "1" or "GIT_GRAFT_FILE" not in env:
        return None
    if args[0] != "git" or args[1] != "-C" or not all(isinstance(a, str) for a in args):
        return None
    repo, rest = args[2], tuple(args[3:])
    if not _admitted(rest):
        return None
    state = _repository_state(repo)
    if state is None:
        return None
    environment = tuple(sorted((k, v) for k, v in env.items() if k.startswith("GIT_") or k == "PATH"))
    return (os.path.abspath(repo), rest, environment, state)


def _admitted(args):
    """True when `args` is one of the cached read shapes."""
    if not args:
        return False
    command, rest = args[0], list(args[1:])
    if command == "cat-file":
        return len(rest) == 2 and rest[0] in ("blob", "-e") and bool(_OBJECT_RE.match(rest[1]))
    if command == "show":
        return len(rest) == 1 and bool(_BLOB_RE.match(rest[0]))
    if command == "rev-parse":
        return tuple(rest) in _REV_PARSE
    if command == "merge-base":
        return len(rest) == 3 and rest[0] == "--is-ancestor" and all(_OID_RE.match(r) for r in rest[1:])
    if command in _FLAGS:
        head = rest[:rest.index("--")] if "--" in rest else rest
        revisions = [r for r in head if r not in _FLAGS[command]]
        if not all(_REV_RE.match(r) for r in revisions):
            return False
        if command == "ls-files":
            return not revisions
        if command in ("ls-tree", "rev-list"):
            return len(revisions) == 1
        return len(revisions) <= 1  # log reads HEAD when it names no revision
    return False


def _state_bytes(path):
    """A regular file's bytes, or None when the path is absent. Raises for anything else."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise OSError("not a regular file: " + path)
    with open(path, "rb") as handle:
        return handle.read()


def _repository_state(repo):
    """The bytes the admitted reads depend on, or None when they cannot be read exactly."""
    try:
        git_dir = os.path.join(os.path.abspath(repo), ".git")
        if not stat.S_ISDIR(os.lstat(git_dir).st_mode):
            return None
        # Without these git does not take the directory as a repository and searches upward.
        if not all(os.path.isdir(os.path.join(git_dir, d)) for d in ("objects", "refs")):
            return None
        if os.path.lexists(os.path.join(git_dir, "commondir")) or \
                os.path.lexists(os.path.join(git_dir, "reftable")):
            return None
        files = tuple(_state_bytes(os.path.join(git_dir, name)) for name in _STATE_FILES)
        head = files[1]
        ref = None
        if head is None:
            return None
        if head.startswith(b"ref: "):
            name = head[5:].rstrip(b"\n").decode("utf-8", "strict")
            parts = name.split("/")
            if parts[0] != "refs" or any(p in ("", ".", "..") for p in parts) or "\\" in name:
                return None
            ref = _state_bytes(os.path.join(git_dir, *parts))
        elif not _HEAD_OID_RE.match(head):
            return None
        return (os.path.realpath(git_dir), files, ref)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
