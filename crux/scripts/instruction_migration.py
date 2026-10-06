#!/usr/bin/env python3
"""instruction_migration.py — discovery and migration for the canonical AGENTS.md.

Implements the discovery and migration contract for the canonical repository
instruction file, `bionic/AGENTS.md` §18 in the source checkout (§18 of the
tree's own AGENTS.md downstream). `migrate-instructions.py` is the CLI entry
point; `audit-docs` reports in plain mode and applies under `--migrate`.

Two sets, and the difference between them is the whole safety argument:

  MUTATION set    the entries and index paths of each TOUCHED scope of ONE
                  checkout: a directory whose listing and index, read together,
                  hold a managed instruction file. A vendored dependency cache and
                  a linked worktree hold no file tracked here, so they fall
                  outside it by construction.

  SUPPRESSION set wider, and it includes untracked files. A file this tool must
                  not touch can still silence the canonical one: under the host's
                  default mode a CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md
                  between the root and the working directory suppresses AGENTS.md
                  entirely. Reporting one and failing on one are different acts.

**No projected rule carries the suppression clause.** The rule table names the
mutation boundary only, so an implementer reading the projection alone would
build a tracked-only scan and ship no suppressor reporting at all. The decision
body is the authority here, and this docstring is the reminder.

Where a touched scope holds a CLAUDE.md-family entry (the winner) and an
AGENTS.md-family entry (the loser), the result is one `AGENTS.md` holding the
winner's bytes with each line that imports the loser replaced by the loser's
bytes. No other merge happens and no instruction byte is decoded. A loser whose
bytes differ from the result is SET ASIDE: renamed, without replacing anything,
to a reported name in its own directory. No step unlinks a winner or a loser,
and no step replaces an existing entry; `os.replace` and `os.rename` replace on
POSIX, which is how an untracked AGENTS.md was once lost, so every such step
goes through a no-replace primitive from the C library.

A scope that cannot migrate safely is refused alone and changes nothing. A
scope whose result would carry untracked bytes stages nothing. Neither blocks
another scope.

Exit codes (entry point): 0 clean, 1 findings/refusal with JSON on stdout,
2 capability error with a message on stderr.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# The three names discovery matches, case-insensitively.
CANONICAL = "AGENTS.md"
_NAMES = {"agents.md", "claude.md", "claude.local.md"}

# The two instruction families, keyed by the lowercased name.
_FAMILY = {"claude.md": "claude", "agents.md": "agents"}

RECEIPT_NAME = ".instruction-migration-receipt.json"

# Path prefixes excluded from mutation regardless of the denylist.
_TEMPLATE_MARKERS = ("crux/templates/", "templates/")

# The set-aside sequence and the temporary entry. Neither ends in `.md`, and
# neither lowercases to a family name, so no harness loads one by default.
SET_ASIDE_MARK = ".crux-set-aside-"
_SET_ASIDE = re.compile(
    r"^(?P<orig>.+)\.crux-set-aside-(?P<hash>[0-9a-f]{12})(?:-(?P<n>[0-9]+))?$")
_TEMPORARY = re.compile(r"^\.crux-migrate-[0-9a-f]{12}\.tmp$")

# The next step each report names. Every one, followed literally, loses no byte
# and publishes nothing.
NEXT_UNLIST = "remove the denylist entry or move the file yourself, then migrate"
NEXT_LINK = "replace it with a regular file or remove the link"
NEXT_REGULAR = "make it a readable regular file or move it out of the directory"
NEXT_IGNORED = ("move your private file to a name that does not exist yet, that git "
                "ignores, and that no harness loads by default")
NEXT_SPELLINGS = ("move one spelling to a name that does not exist yet and no harness "
                  "loads by default, saving any index-only version first")
NEXT_FLAGS = ("save each index-only stage, `git show :<stage>:<path>`, under its own "
              "new name that does not exist yet, then finish that merge or clear that "
              "flag, then migrate")
NEXT_VOLUME = "migrate from a clone on a volume that offers them"
NEXT_IMPORT = "replace that line with the instructions you meant it to load"
NEXT_LINK_DIR = "replace the link with a real directory or remove the link, then migrate"


def next_index_only(stage: int, path: str) -> str:
    """The save step for an index-only blob. Every path a next step names is
    shell-quoted, so a pasted command never runs a substitution in the name."""
    return (f"before changing the index, save `git show {shlex.quote(f':{stage}:{path}')}` "
            "under a new name that does not exist yet")


def next_missing(path: str) -> str:
    """The step for an index path the directory no longer lists."""
    q = shlex.quote(path)
    return (f"save `git show {shlex.quote(f':0:{path}')}` under a new name that does not "
            f"exist yet, then restore the file with `git checkout -- {q}` or remove it "
            f"from the index with `git rm --cached -- {q}`, then migrate")


PUBLISH_NOTE = ("committing AGENTS.md publishes any untracked or ignored bytes it "
                "holds")


class CapabilityError(Exception):
    """An environment problem — exit 2, never a findings exit."""


class ScopeStop(Exception):
    """One scope stops before its next step; the others continue (exit 1)."""


class RunStopped(Exception):
    """An environment failure stops the whole run after the current step (exit 2)."""

    def __init__(self, message: str, completed: list[str]):
        super().__init__(message)
        self.completed = completed


def load_denylist(root: Path) -> list[str]:
    """The one declaration surface for a fixture or a generated runtime copy.

    An absent key, and an absent configuration, are both an empty denylist rather
    than an error, so a project audited before initialization still reports.

    Shared by every caller on purpose: a review found the compatibility check
    reading suppressors without it while the migration CLI read them with it, so
    one surface called a withheld path "suppresses until migrated" and the other
    did not. Two callers, one helper, one answer.
    """
    root = Path(root)
    for name in (".bionic.yml", ".crux"):
        cfg = root / name
        if not cfg.exists():
            continue
        try:
            text = cfg.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CapabilityError(f"cannot read {cfg} as UTF-8: {exc}") from exc
        except OSError:
            return []
        out: list[str] = []
        in_block = False
        for line in text.splitlines():
            if line.strip().startswith("instruction_migration_denylist:"):
                in_block = True
                continue
            if in_block:
                stripped = line.strip()
                if stripped.startswith("- "):
                    out.append(stripped[2:].strip().strip('"').strip("'"))
                    continue
                if stripped and not line.startswith((" ", "\t")):
                    break
        return out
    return []


def is_instruction_name(name: str) -> bool:
    return name.lower() in _NAMES


def family_of(name: str) -> str | None:
    """`claude` or `agents` for a family member's name, else None."""
    return _FAMILY.get(name.lower())


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def show(name: str) -> str:
    """A name for the report. A non-UTF-8 name is reported escaped."""
    try:
        name.encode("utf-8")
        return name
    except UnicodeEncodeError:
        raw = os.fsencode(name)
        return raw.decode("utf-8", "backslashreplace")


# ------------------------------------------------------------------- discovery


@dataclass
class Suppressor:
    path: Path
    reason: str
    on_chain: bool
    remedy: str = ""



@dataclass
class Discovery:
    root: Path
    managed: list[Path]
    excluded: dict[str, str]
    suppressors: list[Suppressor]
    denylist: list[str] = field(default_factory=list)

    def managed_paths(self) -> list[Path]:
        return sorted(self.managed, key=lambda p: p.as_posix())

    def disposition(self, rel: str) -> str | None:
        return self.excluded.get(rel)

    def by_scope(self) -> dict[Path, list[Path]]:
        scopes: dict[Path, list[Path]] = {}
        for rel in self.managed:
            scopes.setdefault(PurePosixPath(rel).parent, []).append(rel)
        return {Path(str(k)): v for k, v in scopes.items()}


def git_tracked_files(root: Path) -> list[str]:
    # Bytes, decoded as the filesystem decodes a name, so a path that is not
    # UTF-8 reaches the report escaped instead of raising in the decoder.
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, check=True).stdout
    except FileNotFoundError as exc:
        raise CapabilityError("git is not available on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise CapabilityError(f"git ls-files failed in {root}: "
                              f"{exc.stderr.decode('utf-8', 'replace')}") from exc
    return [os.fsdecode(p) for p in out.split(b"\0") if p]


def _contained(root: Path, rel: str) -> bool:
    """Does `rel` resolve to a location inside `root`?

    Checked over the FULLY RESOLVED path, not the leaf. A leaf-only check passes a
    tracked path whose intermediate directory was replaced by a symlink after
    checkout, and the commit would then write and unlink outside the repository.
    """
    try:
        target = (root / rel).resolve()
        base = root.resolve()
    except (OSError, RuntimeError):
        return False
    return target == base or base in target.parents


def _classify(root: Path, rel: str) -> str | None:
    """Return an exclusion reason, or None when the file is managed."""
    p = PurePosixPath(rel)
    name = p.name.lower()

    if (root / rel).is_symlink():
        return "excluded:symlink"
    if not _contained(root, rel):
        return "excluded:escapes-root"
    if name == "claude.local.md":
        return "excluded:local-override"
    if ".claude" in p.parts and name == "claude.md":
        return "excluded:dot-claude"
    if any(rel.startswith(m) or f"/{m}" in f"/{rel}" for m in _TEMPLATE_MARKERS):
        return "excluded:template"
    return None


def _scan_suppressors(root: Path, working_dir: Path,
                      deny: set[str] | None = None) -> list[Suppressor]:
    """Clause 5. The whole checkout, not one ancestor chain.

    The chain that matters runs from the root to whatever working directory a
    developer later runs from, which an audit cannot know. A private
    `<docs_dir>/CLAUDE.local.md` silences the tree for anyone working inside it,
    and an ancestor-only scan would miss it.
    """
    found: list[Suppressor] = []
    # Resolve BOTH ends before comparing. On macOS a temp root is /var/... while
    # its resolved parents are /private/var/..., so comparing an unresolved root
    # against resolved parents made every chain empty and every suppressor
    # off-chain — the exact shape that would silently disarm the verdict.
    rroot = root.resolve()
    try:
        wd = working_dir.resolve()
    except OSError:
        wd = rroot
    chain_set = {rroot}
    if wd == rroot or rroot in wd.parents:
        cur = wd
        while True:
            chain_set.add(cur)
            if cur == rroot or rroot not in cur.parents:
                break
            cur = cur.parent

    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # Skip a nested or linked checkout BELOW the root. The root carries a
        # .git entry too, and a scan that skipped it would scan nothing.
        if here != root and (here / ".git").exists():
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d != ".git"]

        for fn in filenames:
            low = fn.lower()
            if low not in {"claude.md", "claude.local.md"}:
                continue
            full = here / fn
            rel = full.relative_to(root).as_posix()
            if low == "claude.md" and ".claude" in PurePosixPath(rel).parts:
                reason = "dot-claude instruction file"
                remedy = ("move its content into the AGENTS.md of the same scope "
                          "and delete this file; no host is known to load "
                          "`.claude/AGENTS.md`, so it is never renamed for you")
            elif low == "claude.local.md":
                reason = "private local override"
                remedy = ("yours to delete or rename; crux never touches a private "
                          "override. Until then it silences the canonical file for "
                          "you alone, on any host using the default mode")
            elif deny and rel in deny:
                # A denylisted path is not managed and will never be migrated, so
                # "suppresses until migrated" would name an event that never comes.
                reason = "withheld by the instruction-migration denylist"
                remedy = ("none while the denylist names it; remove that entry "
                          "first if this file should migrate")
            else:
                reason = "legacy managed file, suppresses until migrated"
                remedy = "run `audit-docs --migrate` to convert it to AGENTS.md"
            # A `<dir>/.claude/CLAUDE.md` is the project-scope file of `<dir>`, so
            # its chain membership follows the directory that holds `.claude`. Only
            # this exact shape moves; any deeper path keeps its own directory.
            scope_dir = (here.parent if here.name == ".claude" and here != root
                         and low == "claude.md" else here)
            found.append(Suppressor(Path(rel), reason,
                                    scope_dir.resolve() in chain_set, remedy))
    return sorted(found, key=lambda s: s.path.as_posix())


def discover(root: Path, tracked_files: list[str] | None = None,
             denylist: list[str] | None = None,
             working_dir: Path | None = None) -> Discovery:
    root = Path(root)
    working_dir = Path(working_dir) if working_dir else root
    # An absent key, and an absent configuration, are both an empty denylist
    # rather than an error, so a project audited before init still reports.
    deny = set(denylist or ())
    tracked = tracked_files if tracked_files is not None else git_tracked_files(root)

    managed: list[Path] = []
    excluded: dict[str, str] = {}

    for rel in tracked:
        if not is_instruction_name(PurePosixPath(rel).name):
            continue
        if rel in deny:
            excluded[rel] = "excluded:denylist"
            continue
        # A tracked path that is already gone. This is the RESUME case, not an
        # error: after a partial run `git ls-files` still lists the old path
        # until the deletion is staged, so a rerun must step over it rather than
        # fail reading it. Checked before _classify, which stats the file.
        full = root / rel
        if not full.exists() and not full.is_symlink():
            excluded[rel] = "excluded:absent"
            continue
        reason = _classify(root, rel)
        if reason:
            excluded[rel] = reason
            continue
        managed.append(Path(rel))

    suppressors = _scan_suppressors(root, working_dir, deny)
    tracked_set = set(tracked)
    for s in suppressors:
        rel = s.path.as_posix()
        if rel not in tracked_set and rel not in excluded:
            excluded[rel] = "excluded:untracked"

    return Discovery(root, managed, excluded, suppressors, sorted(deny))


# ------------------------------------------------------------ the filesystem seam


def _load_noreplace():
    """The C library's no-replace rename, or None where this platform lacks one.

    Python has no binding for it. macOS offers `renamex_np` with RENAME_EXCL and
    Linux offers `renameat2` with RENAME_NOREPLACE. There is no link-then-unlink
    fallback: where neither exists, a scope that needs a rename is refused.
    """
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
    except (ImportError, OSError):
        return None
    if sys.platform == "darwin":
        fn = getattr(libc, "renamex_np", None)
        if fn is None:
            return None
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int

        def call(src: bytes, dst: bytes) -> int:
            return fn(src, dst, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        fn = getattr(libc, "renameat2", None)
        if fn is None:
            return None
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                       ctypes.c_uint]
        fn.restype = ctypes.c_int

        def call(src: bytes, dst: bytes) -> int:
            return fn(-100, src, -100, dst, 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        return None

    def rename_noreplace(src: Path, dst: Path) -> None:
        ctypes.set_errno(0)
        if call(os.fsencode(src), os.fsencode(dst)) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), str(src), None, str(dst))

    return rename_noreplace


_NOREPLACE = _load_noreplace()


class NotRegular(OSError):
    """The entry is not a regular file."""


class RealFS:
    """Every filesystem operation migration makes, in one replaceable object.

    Tests substitute a subclass that folds letter case on each operation, so a
    case-sensitive volume proves what a case-folding one does.
    """

    def listdir(self, directory: Path) -> list[str]:
        return os.listdir(directory)

    def lstat(self, path: Path) -> os.stat_result:
        return os.lstat(path)

    def readlink(self, path: Path) -> str:
        return os.readlink(path)

    def read(self, path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise NotRegular(errno.EINVAL, "not a regular file", str(path))
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(fd)

    def create_excl(self, path: Path, data: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o644)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)

    def noreplace_available(self) -> bool:
        return _NOREPLACE is not None

    def rename_noreplace(self, src: Path, dst: Path) -> None:
        if _NOREPLACE is None:
            raise OSError(errno.ENOTSUP, "no no-replace rename on this platform", str(src))
        _NOREPLACE(src, dst)

    def rename(self, src: Path, dst: Path) -> None:
        """Plain rename. Used only for a case-only respell of one identity."""
        os.rename(src, dst)

    def unlink(self, path: Path) -> None:
        os.unlink(path)

    def fsync_dir(self, directory: Path) -> None:
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)


# ------------------------------------------------------------------------ git


class GitError(Exception):
    pass


class Git:
    def __init__(self, root: Path):
        self.root = Path(root)

    def run(self, *args: str, stdin: bytes | None = None, ok=(0,)) -> bytes:
        try:
            proc = subprocess.run(["git", "-C", str(self.root), *args], input=stdin,
                                  capture_output=True)
        except FileNotFoundError as exc:
            raise CapabilityError("git is not available on PATH") from exc
        if proc.returncode not in ok:
            raise GitError(f"git {' '.join(args)} failed: "
                           f"{proc.stderr.decode('utf-8', 'replace').strip()}")
        return proc.stdout


@dataclass(frozen=True)
class IndexEntry:
    path: str
    mode: str
    blob: str
    stage: int
    tag: str

    @property
    def flag(self) -> str | None:
        """The index state that refuses a scope, or None."""
        if self.stage != 0 or self.tag == "M":
            return "unmerged"
        if self.tag in ("S", "s"):
            return "skip-worktree"
        if self.tag.islower():
            return "assume-unchanged"
        return None


def read_index(git: Git) -> list[IndexEntry]:
    raw = git.run("ls-files", "-z", "-s", "-v")
    out = []
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        head, _, path = rec.partition(b"\t")
        tag, mode, blob, stage = head.decode("ascii").split(" ")
        out.append(IndexEntry(os.fsdecode(path), mode, blob, int(stage), tag))
    return out


def _git_blob(data: bytes, algo: str) -> str:
    h = hashlib.new(algo)
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _rel(scope: str, name: str) -> str:
    return name if scope == "." else f"{scope}/{name}"


# ------------------------------------------------------------------ import lines

_FENCE_OPEN = re.compile(rb"^ {0,3}(`{3,}|~{3,})")


def _split_ending(line: bytes) -> tuple[bytes, bytes]:
    if line.endswith(b"\r\n"):
        return line[:-2], b"\r\n"
    if line.endswith((b"\n", b"\r")):
        return line[:-1], line[-1:]
    return line, b""


def _import_target(body: bytes) -> bytes | None:
    if not body.startswith(b"@"):
        return None
    target = body[1:]
    if target.startswith(b"./"):
        target = target[2:]
    return target or None


def inline_imports(winner: bytes, loser: bytes | None, loser_name: str | None,
                   folds: bool) -> tuple[bytes, list[dict], list[dict], bool]:
    """The winner's bytes with each import line of the loser replaced by the loser.

    Returns the result, the replaced lines, the other lines importing a family
    name, and whether the result carries any winner byte beyond those replaced.
    Nothing is decoded; a line inside a fenced code block is not an import line.
    """
    out: list[bytes] = []
    replaced: list[dict] = []
    others: list[dict] = []
    carries_winner = False
    fence: bytes | None = None
    want = os.fsencode(loser_name) if loser_name is not None else None
    for number, line in enumerate(winner.splitlines(keepends=True), 1):
        body, ending = _split_ending(line)
        if fence is not None:
            m = _FENCE_OPEN.match(body)
            if (m and m.group(1)[:1] == fence[:1] and len(m.group(1)) >= len(fence)
                    and not body[m.end():].strip()):
                fence = None
        else:
            m = _FENCE_OPEN.match(body)
            if m:
                fence = m.group(1)
            else:
                target = _import_target(body)
                if target is not None and loser is not None and want is not None and (
                        target == want or (folds and target.lower() == want.lower())):
                    piece = loser
                    if ending and not loser.endswith((b"\n", b"\r")):
                        piece += ending
                    out.append(piece)
                    replaced.append({"line": number,
                                     "text": body.decode("utf-8", "backslashreplace")})
                    continue
                if target is not None and target.lower() in (b"agents.md", b"claude.md"):
                    others.append({"line": number,
                                   "text": body.decode("utf-8", "backslashreplace"),
                                   "next_step": NEXT_IMPORT})
        out.append(line)
        carries_winner = True
    return b"".join(out), replaced, others, carries_winner


def set_aside_name(name: str, data: bytes, taken: set[str]) -> str:
    """The first unused name of the §18 set-aside sequence.

    `taken` holds the lowercased names of the directory's entries, so a case-fold
    alias of a candidate counts as used.
    """
    base = f"{name}{SET_ASIDE_MARK}{_sha(data)[:12]}"
    candidate, n = base, 2
    while candidate.lower() in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


# -------------------------------------------------------------------- planning


@dataclass
class Member:
    """One family entry in a scope's listing."""
    name: str
    family: str
    kind: str                         # regular | symlink | directory | other
    ident: tuple[int, int] | None
    data: bytes | None
    index: IndexEntry | None          # the matching stage-0 index entry
    ignored: bool = False
    excluded: str | None = None

    @property
    def usable(self) -> bool:
        return self.kind == "regular" and self.data is not None

    def git_state(self, algo: str) -> str:
        if self.index is None:
            return "ignored" if self.ignored else "untracked"
        if self.data is not None and _git_blob(self.data, algo) == self.index.blob:
            return "tracked"
        return "tracked-dirty"


@dataclass
class Step:
    kind: str       # set-aside | create | rename-in | respell | set-aside-winner | index
    scope: str
    source: str | None = None
    dest: str | None = None
    ident: tuple[int, int] | None = None
    data: bytes | None = None
    remove: list[str] = field(default_factory=list)
    add: str | None = None

    def to_json(self) -> dict:
        row = {"kind": self.kind, "scope": self.scope}
        if self.kind == "index":
            row.update(remove=sorted(self.remove), add=self.add)
        else:
            row.update(source=show(self.source) if self.source else None,
                       dest=show(self.dest) if self.dest else None)
        return row


@dataclass
class ScopePlan:
    scope: str
    folds: bool = False
    winner: Member | None = None
    loser: Member | None = None
    loser_input: str | None = None
    result: bytes | None = None
    steps: list[Step] = field(default_factory=list)
    refusals: list[dict] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    set_asides: list[dict] = field(default_factory=list)
    temporaries: list[str] = field(default_factory=list)
    imports: list[dict] = field(default_factory=list)
    other_imports: list[dict] = field(default_factory=list)
    index_blobs: list[dict] = field(default_factory=list)
    index_only: list[dict] = field(default_factory=list)
    no_longer_loaded: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)
    missing: list[dict] = field(default_factory=list)
    resume: dict = field(default_factory=dict)
    winner_left: bool = False
    stopped: str | None = None
    done: bool = False

    @property
    def stages(self) -> bool:
        return not self.blockers and not self.refusals

    def to_json(self) -> dict:
        return {
            "scope": self.scope,
            "winner": show(self.winner.name) if self.winner else None,
            "loser": show(self.loser.name) if self.loser else None,
            "loser_input": show(self.loser_input) if self.loser_input else None,
            "result_sha256": _sha(self.result) if self.result is not None else None,
            "steps": [s.to_json() for s in self.steps],
            "refusals": self.refusals,
            "stages": self.stages,
            "stages_nothing_because": self.blockers,
            "publish_note": PUBLISH_NOTE if self.blockers and not self.refusals else None,
            "imports_inlined": self.imports,
            "other_imports": self.other_imports,
            "index_blobs": self.index_blobs,
            "index_only_blobs": self.index_only,
            "missing_index_paths": self.missing,
            "no_longer_loaded": self.no_longer_loaded,
            "temporaries": self.temporaries,
            "winner_left_in_place": self.winner_left,
            "stopped": self.stopped,
        }


@dataclass
class Plan:
    root: Path
    scopes: list[ScopePlan]
    discovery: "Discovery | None" = None
    fs: RealFS | None = None
    git: Git | None = None
    algo: str = "sha1"
    resume: dict = field(default_factory=dict)

    @property
    def actions(self) -> list[Step]:
        return [s for sp in self.scopes if not sp.refusals for s in sp.steps]

    @property
    def refusals(self) -> list[dict]:
        return [r for sp in self.scopes for r in sp.refusals]

    def findings(self) -> bool:
        """Does anything here owe the user an exit 1?"""
        return any(sp.refusals or sp.stopped or sp.temporaries or sp.missing
                   or sp.index_only or (sp.blockers and (sp.steps or sp.winner_left))
                   for sp in self.scopes)


class _Ctx:
    def __init__(self, root: Path, fs: RealFS, git: Git, deny: set[str],
                 resume: dict | None = None):
        self.root = root
        self.fs = fs
        self.git = git
        self.deny = deny
        # Each scope's receipt row from an earlier run that acted on it: the
        # sources' sha256 and index paths, written before that run's first step.
        self.resume = resume or {}
        try:
            self.index = read_index(git)
            fmt = git.run("rev-parse", "--show-object-format", ok=(0, 128, 129)).strip()
            self.algo = "sha256" if fmt == b"sha256" else "sha1"
            conf = git.run("config", "--bool", "core.ignorecase", ok=(0, 1)).strip()
            self.ignorecase = conf == b"true"
            self.has_head = bool(git.run("rev-parse", "-q", "--verify", "HEAD",
                                         ok=(0, 1)).strip())
        except GitError as exc:
            raise CapabilityError(str(exc)) from exc

    def head_blobs(self, scope: str) -> dict[str, str]:
        if not self.has_head:
            return {}
        args = ["ls-tree", "-z", "HEAD"] + ([] if scope == "." else ["--", f"{scope}/"])
        try:
            raw = self.git.run(*args)
        except GitError as exc:
            raise CapabilityError(str(exc)) from exc
        out = {}
        for rec in raw.split(b"\0"):
            if not rec:
                continue
            head, _, path = rec.partition(b"\t")
            parts = head.split(b" ")
            if len(parts) == 3 and parts[1] == b"blob":
                out[os.fsdecode(path)] = parts[2].decode("ascii")
        return out

    def holds_blob(self, data: bytes, blob: str, entry: str, as_path: str) -> bool:
        """Do an entry's bytes make this index blob, raw or through git's filters?

        An eol or clean filter stores bytes other than the working tree's, so a
        raw hash alone misses a winner a stopped run already moved.

        This does not contradict clause 7's raw-byte rule. That rule decides
        which index blobs no working-tree entry holds, and so what staging could
        drop; it still hashes raw bytes. This check only recognises which entry a
        stopped run renamed the winner into. That recognition sets the winner's
        index path, which the index step then removes, so it bears on staging;
        the raw-byte index-only check still runs before any index step.
        """
        if _git_blob(data, self.algo) == blob:
            return True
        try:
            out = self.git.run("hash-object", f"--path={as_path}", "--", entry)
        except GitError:
            return False
        return out.decode("ascii", "replace").strip() == blob

    def ignored(self, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        try:
            raw = self.git.run("check-ignore", "-z", "--no-index", "--stdin",
                               stdin=b"".join(os.fsencode(p) + b"\0" for p in paths),
                               ok=(0, 1))
        except GitError as exc:
            raise CapabilityError(str(exc)) from exc
        return {os.fsdecode(p) for p in raw.split(b"\0") if p}


def _excluded_by_path(rel: str, deny: set[str]) -> str | None:
    p = PurePosixPath(rel)
    if rel in deny:
        return "excluded:denylist"
    if ".claude" in p.parts and p.name.lower() == "claude.md":
        return "excluded:dot-claude"
    if any(rel.startswith(m) or f"/{m}" in f"/{rel}" for m in _TEMPLATE_MARKERS):
        return "excluded:template"
    return None


def _folds(fs: RealFS, directory: Path, names: list[str]) -> bool:
    """Does this directory's volume fold case? Identity confirms a probed alias."""
    listed = set(names)
    for name in names:
        alias = name.swapcase()
        if alias == name or alias in listed:
            continue
        try:
            a, b = fs.lstat(directory / name), fs.lstat(directory / alias)
        except OSError:
            return False
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    return False


def touched_scopes(ctx: _Ctx) -> list[str]:
    scopes = set()
    for e in ctx.index:
        p = PurePosixPath(e.path)
        if family_of(p.name) is None or _excluded_by_path(e.path, ctx.deny):
            continue
        scope = p.parent.as_posix()
        if _contained(ctx.root, scope):
            scopes.add(scope)
    return sorted(scopes)


def _member(ctx: _Ctx, directory: Path, scope: str, name: str) -> Member:
    fs = ctx.fs
    path = directory / name
    st = fs.lstat(path)
    mode = st.st_mode
    kind = ("regular" if stat.S_ISREG(mode) else "symlink" if stat.S_ISLNK(mode)
            else "directory" if stat.S_ISDIR(mode) else "other")
    data = None
    if kind == "regular":
        try:
            data = fs.read(path)
        except OSError:
            data = None
    return Member(name, family_of(name) or "", kind, (st.st_dev, st.st_ino), data, None)


def plan_scope(ctx: _Ctx, scope: str) -> ScopePlan | None:
    """Plan one touched scope, or return None when it holds no managed member."""
    fs, root = ctx.fs, ctx.root
    directory = root if scope == "." else root / scope
    sp = ScopePlan(scope)
    # A scope reached through a link: git refuses every path beyond it, and a
    # step there would write where the link points. Refused alone.
    cur = root
    for part in ([] if scope == "." else PurePosixPath(scope).parts):
        cur = cur / part
        try:
            linked = stat.S_ISLNK(fs.lstat(cur).st_mode)
        except OSError:
            break
        if linked:
            sp.refusals.append({
                "scope": scope, "entry": show(cur.relative_to(root).as_posix()),
                "reason": "the scope directory is reached through a symlink",
                "next_step": NEXT_LINK_DIR})
            return sp
    try:
        names = fs.listdir(directory)
    except FileNotFoundError:
        names = []
    except OSError as exc:
        raise CapabilityError(f"cannot list {directory}: {exc}") from exc

    fam_names = sorted(n for n in names if family_of(n))
    folds = ctx.ignorecase or _folds(fs, directory, fam_names)
    sp.folds = folds

    # Index paths of this scope, grouped by path (an unmerged path has stages).
    by_path: dict[str, list[IndexEntry]] = {}
    for e in ctx.index:
        p = PurePosixPath(e.path)
        if p.parent.as_posix() == scope and family_of(p.name):
            by_path.setdefault(e.path, []).append(e)

    members = {n: _member(ctx, directory, scope, n) for n in fam_names}
    unmatched: list[str] = []
    for path, entries in sorted(by_path.items()):
        base = PurePosixPath(path).name
        target = base if base in members else None
        if target is None and folds:
            target = next((n for n in fam_names if n.lower() == base.lower()
                           and members[n].index is None), None)
        if target is None:
            unmatched.append(path)
            continue
        stage0 = next((e for e in entries if e.stage == 0), entries[0])
        members[target].index = stage0

    # A stopped run's receipt row for this scope, if one exists.
    resume = ctx.resume.get(scope)
    resume = resume if isinstance(resume, dict) else {}
    rw = resume.get("winner") if isinstance(resume.get("winner"), dict) else {}
    ri = resume.get("input") if isinstance(resume.get("input"), dict) else {}

    # A run stopped after a respell leaves the old spelling in the index and
    # `AGENTS.md` in the listing. On a case-sensitive volume nothing above
    # matches them, so the index path is that entry when the entry holds its
    # blob or the bytes the receipt row recorded for that path.
    if CANONICAL in members and members[CANONICAL].index is None \
            and members[CANONICAL].data is not None:
        entry = members[CANONICAL]
        for path in list(unmatched):
            stage0 = next((e for e in by_path[path] if e.stage == 0), None)
            recorded = {r.get("sha256") for r in (rw, ri) if r.get("index_path") == path}
            if (stage0 is not None and family_of(PurePosixPath(path).name) == "agents"
                    and (_git_blob(entry.data, ctx.algo) == stage0.blob
                         or _sha(entry.data) in recorded)):
                entry.index = stage0
                unmatched.remove(path)
                break

    for m in members.values():
        rel = _rel(scope, m.name)
        m.excluded = (_excluded_by_path(rel, ctx.deny)
                      or (_excluded_by_path(m.index.path, ctx.deny) if m.index else None)
                      or ("excluded:symlink" if m.kind == "symlink" else None))
        if m.excluded:
            sp.excluded[rel] = m.excluded

    managed = [m for m in members.values() if m.index is not None and not m.excluded]
    managed_absent = [p for p in unmatched
                      if not _excluded_by_path(p, ctx.deny)]
    if not managed and not managed_absent:
        return None

    # Set-asides and temporaries this scope already holds.
    taken = {n.lower() for n in names}
    found = []
    for n in sorted(names):
        m = _SET_ASIDE.match(n)
        if m and family_of(m.group("orig")):
            try:
                data = fs.read(directory / n)
            except OSError:
                data = None
            found.append((n, m.group("orig"), data))
        elif _TEMPORARY.match(n):
            sp.temporaries.append(_rel(scope, n))

    # Ignore state, asked of git once for the scope.
    candidates = [_rel(scope, m.name) for m in members.values() if m.index is None]
    candidates += [_rel(scope, n) for n, _, _ in found]
    candidates.append(_rel(scope, CANONICAL))
    planned_names = {}
    for m in members.values():
        if m.usable:
            planned_names[m.name] = set_aside_name(m.name, m.data, taken)
            candidates.append(_rel(scope, planned_names[m.name]))
    ignored = ctx.ignored(sorted(set(candidates)))
    for m in members.values():
        m.ignored = m.index is None and _rel(scope, m.name) in ignored

    for n, orig, data in found:
        sp.set_asides.append({
            "scope": scope, "entry": show(orig), "set_aside": show(n),
            "sha256": _sha(data) if data is not None else None,
            "git_state": "ignored" if _rel(scope, n) in ignored else "untracked",
            "ignored_by_git": _rel(scope, n) in ignored, "status": "found"})

    # Index blobs, recorded for the receipt.
    for path, entries in sorted(by_path.items()):
        for e in entries:
            sp.index_blobs.append({"path": show(path), "stage": e.stage, "blob": e.blob})

    def refuse(entry: str, reason: str, next_step: str, **extra):
        row = {"scope": scope, "entry": show(entry), "reason": reason,
               "next_step": next_step}
        row.update(extra)
        sp.refusals.append(row)

    # Index paths a stopped run moved: the receipt row names the path and a
    # listed entry holds its recorded bytes, or a set-aside holds its blob.
    listed_sha = {_sha(m.data) for m in members.values() if m.data is not None}
    listed_sha |= {_sha(d) for _, _, d in found if d is not None}
    found_blobs = {_git_blob(d, ctx.algo) for _, _, d in found if d is not None}
    resumed_paths = {r.get("index_path") for r in (rw, ri)
                     if r.get("index_path") in unmatched and r.get("sha256") in listed_sha}
    accounted = {p for p in unmatched if p in resumed_paths
                 or any(e.blob in found_blobs for e in by_path[p])}

    # Clause 6: two spellings of one family, across the listing and the index.
    for fam in ("claude", "agents"):
        spellings = [_rel(scope, n) for n in fam_names if family_of(n) == fam]
        spellings += [p for p in unmatched if family_of(PurePosixPath(p).name) == fam
                      and p not in accounted]
        if len(spellings) > 1:
            refuse(spellings[0], "one family holds two spellings: "
                   + ", ".join(show(s) for s in spellings), NEXT_SPELLINGS,
                   paths=[show(s) for s in spellings])
    for path, entries in sorted(by_path.items()):
        for e in entries:
            if e.flag:
                refuse(path, f"its index entry is {e.flag}", NEXT_FLAGS)
                break
    for m in members.values():
        if m.kind == "regular" and (m.ident is None or m.ident[1] == 0):
            refuse(_rel(scope, m.name), "the volume cannot establish identity",
                   NEXT_VOLUME)
    if sp.refusals:
        return sp

    claude = next((m for m in members.values() if m.family == "claude"), None)
    loser = next((m for m in members.values() if m.family == "agents"), None)
    winner = claude if claude is not None and not claude.excluded else None
    sp.winner, sp.loser = winner, loser
    head = ctx.head_blobs(scope)
    held = {_git_blob(m.data, ctx.algo) for m in members.values() if m.data is not None}
    held |= {_git_blob(d, ctx.algo) for _, _, d in found if d is not None}
    index_blobs = {e.blob for entries in by_path.values() for e in entries}

    # The winner input: the entry, or a prior run's winner found by its blob.
    w_data: bytes | None = None
    w_tracked = False
    w_label = None
    resumed_result = False
    resumed_path: str | None = None
    if winner is not None:
        if not winner.usable:
            refuse(_rel(scope, winner.name), "the winner is not a readable regular file",
                   NEXT_REGULAR)
            return sp
        w_data, w_tracked, w_label = winner.data, winner.index is not None, winner.name
    else:
        gone = [p for p in unmatched if family_of(PurePosixPath(p).name) == "claude"]
        if gone:
            path = rw.get("index_path") if rw.get("index_path") in gone else gone[0]
            blob = next(e.blob for e in by_path[path] if e.stage == 0)
            recorded = rw.get("sha256") if rw.get("index_path") == path else None

            # A dirty winner's bytes make no index blob, so the receipt row's
            # recorded sha256 is what finds where a stopped run moved it.
            def was_winner(data: bytes, entry: str) -> bool:
                return (_sha(data) == recorded
                        or ctx.holds_blob(data, blob, _rel(scope, entry), path))

            prior = [(n, d) for n, orig, d in found
                     if family_of(orig) == "claude" and d is not None and was_winner(d, n)]
            if len(prior) == 1:
                w_data, w_tracked, w_label = prior[0][1], True, prior[0][0]
                resumed_path = path
            elif (loser is not None and loser.name == CANONICAL and loser.usable
                  and was_winner(loser.data, loser.name)):
                w_data, w_tracked, w_label = loser.data, True, loser.name
                resumed_result = True
                resumed_path = path

    # The loser, and the rows of clause 4 that refuse the scope.
    if loser is not None and w_data is not None:
        rel = _rel(scope, loser.name)
        if loser.excluded == "excluded:symlink":
            try:
                target = fs.readlink(directory / loser.name)
            except OSError:
                target = None
            refuse(rel, "the loser is a symlink", NEXT_LINK,
                   target=show(target) if target else None)
        elif loser.excluded:
            what = ("named by the denylist" if loser.excluded == "excluded:denylist"
                    else "a template")
            refuse(rel, f"the loser is {what}", NEXT_UNLIST)
        elif not loser.usable:
            refuse(rel, "the loser is not a regular file, or is unreadable", NEXT_REGULAR)
        if sp.refusals:
            return sp

    agents_found = [(n, orig, d) for n, orig, d in found
                    if family_of(orig) == "agents" and d is not None]
    finished = False
    result: bytes | None = None
    input_name = input_orig = None
    input_data: bytes | None = None
    input_tracked = False
    loser_is_input = False
    if w_data is not None:
        if resumed_result:
            finished, result = True, loser.data
            _, _, sp.other_imports, carries_w = inline_imports(w_data, None, None, folds)
        else:
            if loser is not None and loser.name == CANONICAL:
                # A rerun: an AGENTS.md computed from the winner and a set-aside
                # of this scope is finished, not a loser.
                for n, orig, d in agents_found:
                    r, rep, oth, cw = inline_imports(w_data, d, orig, folds)
                    if r == loser.data:
                        finished, result, carries_w = True, r, cw
                        input_name, input_orig, input_data = n, orig, d
                        sp.imports, sp.other_imports = rep, oth
                        break
            if not finished:
                if loser is not None:
                    input_name, input_orig, input_data = loser.name, loser.name, loser.data
                    loser_is_input = True
                elif len(agents_found) == 1:
                    input_name, input_orig, input_data = agents_found[0]
                result, sp.imports, sp.other_imports, carries_w = inline_imports(
                    w_data, input_data, input_orig, folds)
        if len(agents_found) > 1 and not loser_is_input and not finished:
            # More than one set-aside could be the loser: inline none.
            sp.no_longer_loaded += [show(_rel(scope, n)) for n, _, _ in agents_found]
        elif input_name is not None and not loser_is_input and not sp.imports:
            sp.no_longer_loaded.append(show(_rel(scope, input_name)))
        if loser_is_input:
            input_tracked = loser.index is not None
        else:
            # A dirty tracked loser's set-aside makes no index blob, so the receipt
            # row a stopped run wrote says whether that input was tracked.
            input_tracked = input_data is not None and (
                _git_blob(input_data, ctx.algo) in index_blobs
                or (ri.get("tracked") is True and ri.get("sha256") == _sha(input_data)))
        if carries_w and not w_tracked:
            state = "ignored" if winner is not None and winner.ignored else "untracked"
            sp.blockers.append(f"the result carries bytes read from the {state} entry "
                               f"{show(_rel(scope, w_label))}")
        if sp.imports and not input_tracked:
            if loser_is_input:
                state = "ignored" if loser.ignored else "untracked"
                sp.blockers.append(f"the result carries bytes read from the {state} "
                                   f"entry {show(_rel(scope, input_name))}")
            else:
                # A set-aside is untracked, but its source may have been a tracked
                # entry with dirty bytes; without a receipt row that is unknown.
                sp.blockers.append(
                    f"the result carries bytes read from the set-aside "
                    f"{show(_rel(scope, input_name))}, whose bytes no index blob holds "
                    "and no receipt record marks as tracked")
    elif loser is not None and not loser.excluded:
        # One family: a lone variant takes the exact spelling.
        if not loser.usable:
            if loser.name != CANONICAL:
                refuse(_rel(scope, loser.name),
                       "the entry is not a regular file, or is unreadable", NEXT_REGULAR)
            return sp
        result, input_name, loser_is_input = loser.data, loser.name, True
        input_data, input_tracked = loser.data, loser.index is not None
        finished = loser.name == CANONICAL
        if not finished and loser.index is None:
            state = "ignored" if loser.ignored else "untracked"
            sp.blockers.append(f"the result carries bytes read from the {state} entry "
                               f"{show(_rel(scope, loser.name))}")
    else:
        _missing(sp, scope, unmatched, set(), ctx.deny)
        return sp
    sp.loser_input = input_name
    sp.result = result

    # What a rerun needs if this run stops: each source's sha256 and index path.
    record: dict = {}
    if w_data is not None and w_tracked:
        record["winner"] = {
            "sha256": _sha(w_data),
            "index_path": winner.index.path if winner is not None else resumed_path}
    elif rw:
        record["winner"] = rw
    if input_data is not None:
        if loser_is_input and loser.index is not None:
            in_path = loser.index.path
        else:
            in_path = ri.get("index_path") if ri.get("sha256") == _sha(input_data) else None
        record["input"] = {"sha256": _sha(input_data), "index_path": in_path,
                           "tracked": bool(input_tracked)}
    elif ri:
        record["input"] = ri
    sp.resume = record

    # Staging: nothing where untracked bytes or an index-only blob would move.
    # A finished one-family scope plans an index step only to remove an old
    # path, and that removal is checked like every other index change.
    exact = _rel(scope, CANONICAL)
    moved = {m.index.path for m in members.values()
             if m.index is not None and not m.excluded}
    removable = sorted(p for p, entries in by_path.items()
                       if p != exact and not _excluded_by_path(p, ctx.deny)
                       and (p in moved or p == resumed_path or p in resumed_paths
                            or any(e.blob in held for e in entries)))
    if not finished or w_data is not None or removable:
        if exact not in by_path and exact in ignored:
            sp.blockers.append(
                f"the exact path {show(exact)} is untracked and git ignores it")
        for path, entries in sorted(by_path.items()):
            for e in entries:
                if e.blob not in held and head.get(path) != e.blob:
                    sp.index_only.append({
                        "path": show(path), "stage": e.stage, "blob": e.blob,
                        "next_step": next_index_only(e.stage, show(path))})
        if sp.index_only:
            sp.blockers.append("an instruction-family index entry holds a blob that "
                               "neither HEAD nor any working-tree entry holds")
    stages = not sp.blockers

    # The working-tree steps, in the order clause 8 fixes.
    loaded_before_steps = list(sp.no_longer_loaded)
    steps: list[Step] = []
    renamed_in = False
    if not finished:
        if loser_is_input and loser.data != result:
            dest = set_aside_name(loser.name, loser.data, taken)
            taken.add(dest.lower())
            if loser.ignored and _rel(scope, dest) not in ignored:
                refuse(_rel(scope, loser.name),
                       "the loser is ignored and git would not ignore its set-aside name "
                       + show(dest), NEXT_IGNORED)
                return sp
            steps.append(Step("set-aside", scope, loser.name, dest, loser.ident,
                              loser.data))
            sp.set_asides.append({
                "scope": scope, "entry": show(loser.name), "set_aside": show(dest),
                "sha256": _sha(loser.data), "git_state": loser.git_state(ctx.algo),
                "ignored_by_git": _rel(scope, dest) in ignored, "status": "planned",
                "inlined": bool(sp.imports)})
            if not sp.imports:
                sp.no_longer_loaded.append(show(_rel(scope, dest)))
        if loser_is_input and loser.data == result:
            if loser.name != CANONICAL:
                steps.append(Step("respell", scope, loser.name, CANONICAL, loser.ident,
                                  loser.data))
        elif winner is not None and w_tracked and stages and winner.data == result:
            steps.append(Step("rename-in", scope, winner.name, CANONICAL, winner.ident,
                              winner.data))
            renamed_in = True
        else:
            steps.append(Step("create", scope, None, CANONICAL, None, result))
    if winner is not None and w_tracked and stages and not renamed_in:
        dest = set_aside_name(winner.name, winner.data, taken)
        taken.add(dest.lower())
        steps.append(Step("set-aside-winner", scope, winner.name, dest, winner.ident,
                          winner.data))
        sp.set_asides.append({
            "scope": scope, "entry": show(winner.name), "set_aside": show(dest),
            "sha256": _sha(winner.data), "git_state": winner.git_state(ctx.algo),
            "ignored_by_git": _rel(scope, dest) in ignored, "status": "planned",
            "inlined": False})
    sp.winner_left = winner is not None and not (w_tracked and stages)

    # The index: the result at the exact path, no other family path this
    # migration moved. A path the user deleted, whose blob only HEAD holds, and
    # an excluded path are left as they are, and a deleted one is reported.
    remove: list[str] = []
    if stages:
        remove = removable
        current = by_path.get(exact)
        stale = current is None or current[0].blob != _git_blob(result, ctx.algo)
        if steps or remove or (w_data is not None and stale):
            steps.append(Step("index", scope, remove=remove, add=exact))

    if steps and not all(s.kind == "index" for s in steps) and not fs.noreplace_available():
        needs = [s for s in steps if s.kind in ("set-aside", "create", "rename-in",
                                                "set-aside-winner")]
        if needs:
            refuse(_rel(scope, needs[0].source or CANONICAL),
                   "the volume offers no atomic no-replace create and rename",
                   NEXT_VOLUME)
            # A refused scope changes nothing, so nothing is planned to move.
            sp.set_asides = [s for s in sp.set_asides if s["status"] == "found"]
            sp.no_longer_loaded = loaded_before_steps
            sp.winner_left = False
            return sp
    # A path the plan does not remove is missing whether or not the scope
    # stages; the exact path is not missing when the index step adds it.
    _missing(sp, scope, unmatched, set(remove) | ({exact} if stages else set()),
             ctx.deny)
    sp.steps = steps
    return sp


def _missing(sp: ScopePlan, scope: str, unmatched: list[str], removed: set[str],
             deny: set[str]) -> None:
    """Report each family index path the directory no longer lists and this plan
    does not remove. The index then differs from what a finished run stages, so
    the scope is a finding, never clean."""
    for path in unmatched:
        if path in removed or _excluded_by_path(path, deny):
            continue
        sp.missing.append({
            "scope": scope, "path": show(path),
            "reason": "the index holds this path and the directory does not list it",
            "next_step": next_missing(show(path))})


def build_plan(d: "Discovery", fs: RealFS | None = None, git: Git | None = None) -> Plan:
    """Plan every touched scope. A refused scope is planned as refused, alone."""
    root = Path(d.root)
    fs = fs or RealFS()
    git = git or Git(root)
    resume = _prior_resume(root)
    ctx = _Ctx(root, fs, git, set(d.denylist), resume)
    scopes = []
    for scope in touched_scopes(ctx):
        sp = plan_scope(ctx, scope)
        if sp is not None:
            scopes.append(sp)
    return Plan(root, scopes, d, fs, git, ctx.algo, resume)


def _prior_resume(root: Path) -> dict:
    """The receipt's per-scope source records, or {} when there is none to read."""
    path = receipt_path(root)
    try:
        if path.is_symlink():
            return {}
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    resume = data.get("resume") if isinstance(data, dict) else None
    if not isinstance(resume, dict):
        return {}
    return {k: v for k, v in resume.items() if isinstance(k, str) and isinstance(v, dict)}


# -------------------------------------------------------------------- applying


@dataclass
class Receipt:
    root: Path
    scopes: list[dict] = field(default_factory=list)
    dispositions: dict[str, str] = field(default_factory=dict)
    resume: dict = field(default_factory=dict)
    atomic: bool = False
    staging_note: str = (
        "Each scope migrates by steps: set a loser aside, write AGENTS.md through a "
        "temporary entry and a no-replace rename, then move the winner. A stopped run "
        "leaves no partial AGENTS.md, and a rerun reaches the state an uninterrupted "
        "run reaches. This claims no multi-file filesystem atomicity and no "
        "durability across power loss."
    )

    def has_unresolved(self) -> bool:
        return any(s.get("refusals") or s.get("stopped") for s in self.scopes)

    def is_noop(self) -> bool:
        return not any(s.get("steps") for s in self.scopes)

    def to_json(self, pending: list[dict] | None = None) -> dict:
        return {"format": 2, "scopes": self.scopes + (pending or []),
                "dispositions": self.dispositions, "resume": self.resume,
                "atomic": self.atomic, "staging_note": self.staging_note}


def receipt_path(root: Path) -> Path:
    return Path(root) / RECEIPT_NAME


def read_receipt(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_own_file(target: Path, data: bytes) -> None:
    """Write one of this tool's own files through a fresh temporary entry.

    The temporary name is new on every call and created O_EXCL, so no existing
    entry is ever unlinked or written through.
    """
    if target.exists() and not target.is_symlink():
        try:
            if target.read_bytes() == data:
                return
        except OSError:
            pass
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                 | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _Applier:
    def __init__(self, plan: Plan, hook=None):
        self.plan = plan
        self.fs = plan.fs or RealFS()
        self.git = plan.git or Git(plan.root)
        self.hook = hook

    def _dir(self, scope: str) -> Path:
        return self.plan.root if scope == "." else self.plan.root / scope

    def _verify(self, path: Path, ident, data: bytes, what: str) -> None:
        try:
            st = self.fs.lstat(path)
            now = self.fs.read(path)
        except FileNotFoundError:
            raise ScopeStop(f"{what} {show(path.name)} is gone since planning")
        except NotRegular:
            raise ScopeStop(f"{what} {show(path.name)} is no longer a regular file")
        if (ident is not None and (st.st_dev, st.st_ino) != ident) or now != data:
            raise ScopeStop(f"{what} {show(path.name)} changed since planning")

    def _one_result(self, directory: Path, result: bytes) -> None:
        agents = [n for n in self.fs.listdir(directory) if family_of(n) == "agents"]
        if agents != [CANONICAL]:
            raise ScopeStop("the scope does not hold exactly one AGENTS.md-family "
                            f"entry spelled AGENTS.md: {sorted(show(a) for a in agents)}")
        self._verify(directory / CANONICAL, None, result, "the result")

    def _exists_error(self, directory: Path, dest: str) -> ScopeStop:
        # A volume may fold more than lowercasing does (`agentſ.md` onto
        # `AGENTS.md`), so the entry met is found by full case folding.
        alias = [n for n in self.fs.listdir(directory)
                 if n.casefold() == dest.casefold()]
        where = alias[0] if alias else dest
        kind = "a case-fold alias of " if where != dest else ""
        return ScopeStop(f"a no-replace step met an existing entry {show(where)}, "
                         f"{kind}{show(dest)}")

    def _noreplace(self, directory: Path, src: str, dest: str) -> None:
        try:
            self.fs.rename_noreplace(directory / src, directory / dest)
        except FileExistsError:
            raise self._exists_error(directory, dest)
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
                raise self._exists_error(directory, dest)
            if exc.errno in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS,
                             getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
                raise ScopeStop("the volume offers no atomic no-replace rename; "
                                + NEXT_VOLUME)
            raise

    def _sources(self, sp: ScopePlan, locations: dict[str, str]) -> None:
        """Before each step: every source still holds what the plan observed."""
        directory = self._dir(sp.scope)
        for m in (sp.winner, sp.loser):
            if m is None or m.name not in locations or not m.usable:
                continue
            self._verify(directory / locations[m.name], m.ident, m.data, "source")

    def run_scope(self, sp: ScopePlan) -> list[dict]:
        directory = self._dir(sp.scope)
        locations = {m.name: m.name for m in (sp.winner, sp.loser) if m is not None}
        changes: list[dict] = []
        for step in sp.steps:
            self._sources(sp, locations)
            if step.kind in ("set-aside", "set-aside-winner"):
                if step.kind == "set-aside-winner":
                    self._one_result(directory, sp.result)
                    self.fs.fsync_dir(directory)
                self._noreplace(directory, step.source, step.dest)
                self._verify(directory / step.dest, step.ident, step.data, "set-aside")
                for row in sp.set_asides:
                    if row["status"] == "planned" and row["set_aside"] == show(step.dest):
                        row["status"] = "created"
                if step.source in self.fs.listdir(directory):
                    raise ScopeStop(f"{show(step.source)} is still listed after its "
                                    "set-aside")
                locations[step.source] = step.dest
                self.fs.fsync_dir(directory)
            elif step.kind == "create":
                tmp = directory / f".crux-migrate-{secrets.token_hex(6)}.tmp"
                self.fs.create_excl(tmp, step.data)
                try:
                    self._noreplace(directory, tmp.name, CANONICAL)
                except BaseException:
                    self.fs.unlink(tmp)
                    raise
                self.fs.fsync_dir(directory)
                self._one_result(directory, sp.result)
            elif step.kind in ("rename-in", "respell"):
                src = directory / step.source
                dst = directory / CANONICAL
                same = False
                if sp.folds and step.source.lower() == CANONICAL.lower():
                    try:
                        a, b = self.fs.lstat(src), self.fs.lstat(dst)
                        same = (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
                    except OSError:
                        same = False
                if same:
                    self.fs.rename(src, dst)
                else:
                    self._noreplace(directory, step.source, CANONICAL)
                locations[step.source] = CANONICAL
                self.fs.fsync_dir(directory)
                self._one_result(directory, sp.result)
            elif step.kind == "index":
                changes.append(self._index(sp, step, directory))
            if self.hook:
                self.hook(sp.scope, step.kind)
        return changes

    def _index(self, sp: ScopePlan, step: Step, directory: Path) -> dict:
        self._one_result(directory, sp.result)
        algo = self.plan.algo
        zero = "0" * (64 if algo == "sha256" else 40)
        try:
            blob = self.git.run("hash-object", "-w", "--", step.add).decode().strip()
            mode = "100755" if self.fs.lstat(directory / CANONICAL).st_mode & 0o111 \
                else "100644"
            lines = b"".join(os.fsencode(f"0 {zero}\t{p}") + b"\0" for p in step.remove)
            lines += os.fsencode(f"{mode} {blob}\t{step.add}") + b"\0"
            self.git.run("update-index", "-z", "--index-info", stdin=lines)
        except GitError as exc:
            raise ScopeStop(f"git refused the index change for {sp.scope}: {exc}; "
                            "no byte was lost")
        return {"removed": sorted(show(p) for p in step.remove), "added": show(step.add),
                "blob": blob}


def apply_plan(plan: Plan, root: Path | None = None, hook=None) -> Receipt:
    """Run each scope's steps in order. A refused or stopped scope blocks no other.

    An environment failure stops the run after the current step. The receipt
    still records every scope reached, so a completed scope's set-asides and
    index changes are never left unrecorded.
    """
    root = Path(root or plan.root)
    receipt = Receipt(root, resume=dict(plan.resume))
    applier = _Applier(plan, hook)

    def write(pending: list[dict] | None = None) -> None:
        _write_own_file(receipt_path(root), json.dumps(
            receipt.to_json(pending), indent=2, sort_keys=True).encode())

    if plan.discovery:
        for rel, reason in plan.discovery.excluded.items():
            receipt.dispositions[rel] = reason
    completed: list[str] = []
    failure: RunStopped | None = None
    for sp in plan.scopes:
        row = sp.to_json()
        if not sp.refusals and sp.steps:
            # Before the first step: the sources' sha256 and index paths, so a
            # rerun after a stop at any step finds what this run moved.
            if sp.resume:
                receipt.resume[sp.scope] = sp.resume
            write([dict(row, stopped="stopped before this scope finished")])
            try:
                row["index_changes"] = applier.run_scope(sp)
                sp.done = True
            except KeyboardInterrupt:
                sp.stopped = row["stopped"] = "interrupted"
                row["set_asides"] = sp.set_asides
                receipt.scopes.append(row)
                write()
                raise
            except ScopeStop as exc:
                sp.stopped = row["stopped"] = str(exc)
            except OSError as exc:
                sp.stopped = row["stopped"] = f"environment failure: {exc}"
                failure = RunStopped(
                    f"environment failure in scope {sp.scope}: {exc}; scopes completed: "
                    f"{', '.join(completed) or 'none'}", list(completed))
                failure.__cause__ = exc
        row["set_asides"] = sp.set_asides
        receipt.scopes.append(row)
        for sa in sp.set_asides:
            if sa["status"] == "created":
                receipt.dispositions[_rel(sp.scope, sa["entry"])] = (
                    f"set aside as {sa['set_aside']}")
        if sp.done:
            completed.append(sp.scope)
            for s in sp.steps:
                if s.kind in ("rename-in", "respell"):
                    receipt.dispositions[_rel(sp.scope, s.source)] = "renamed to AGENTS.md"
                elif s.kind == "create":
                    receipt.dispositions[_rel(sp.scope, CANONICAL)] = "created"
        for r in sp.refusals:
            receipt.dispositions[r["entry"]] = "refused"
        if failure is not None:
            break
    # A run that changed nothing leaves an earlier run's receipt as it is, so a
    # second run on a completed tree rewrites nothing, the receipt included.
    acted = any(sp.done or sp.stopped for sp in plan.scopes)
    refused = any(sp.refusals for sp in plan.scopes)
    if acted or (refused and not receipt_path(root).exists()):
        write()
    if failure is not None:
        raise failure
    return receipt


# ------------------------------------------------------------------------- log


def append_log(log_path: Path, receipt: Receipt, date: str) -> None:
    """One `schema` op per migration — never one entry per file."""
    log_path = Path(log_path)
    migrated = [s for s in receipt.scopes if s.get("steps") and not s.get("stopped")
                and not s.get("refusals")]
    unresolved = [s for s in receipt.scopes if s.get("stopped") or s.get("refusals")]
    heading = (f"## [{date}] schema | migrate instruction files to {CANONICAL} "
               f"({len(migrated)} scopes)")
    body = [
        f"Migrated {len(migrated)} instruction scope(s) to `{CANONICAL}`.",
        f"Refused or stopped scopes: {len(unresolved)}.",
        f"Receipt: `{RECEIPT_NAME}`.",
    ]
    entry = heading + "\n\n" + "\n".join(body) + "\n\n"

    text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    marker = "_Append-only. Newest first._\n\n"
    if marker in text:
        text = text.replace(marker, marker + entry, 1)
    else:
        text = entry + text
    _write_own_file(log_path, text.encode("utf-8"))
