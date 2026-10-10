"""Shared helpers for the council-gate records and the git-backed clean-path check.

Used by the gate check in `advance-run.py` (through `council_gate.py`) and by the
council runner, which refuses an untracked or gitignored council subject with
`path_state` / `is_clean` and writes records `load_record` accepts.

What lives here:

* Record loading. A JSON file under `<run_dir>/council/` is a record only when its
  `record_type` is one of the four recognized values. A JSON file with any other
  `record_type` (an old non-record file) is skipped. A recognized record that fails
  its schema, or a file that is not parseable JSON, raises `InvalidRecord`: a
  corrupt record never reads as absent.
* Discovery. `discover_records` lists a run's records and refuses a symlinked
  record file or a symlinked `council/` directory. It follows no symlink.
* Hashing, repository root, and clean-path state. A path is clean when HEAD, the
  index and the working tree hold the same bytes for it. An untracked file and a
  symlink are never clean.
* Symlink refusal on an opened path. `reached_through_symlink` is the one check
  advance-run.py and run-council.py make on a run snapshot and an explicit book: a
  symlink at the leaf, or one held in a directory inside a git repository, refuses.
* Book resolution. `resolve_book` finds the book a run snapshot was started from
  (the explicit path, then `<docs>/promptbooks/active/<dir>.yaml`, then
  `<docs>/promptbooks/archive/<dir>.yaml`) and verifies the run's
  `book_content_hash` against it.

Git runs with the ambient global and system configuration neutralized, with every
repository-redirecting variable (`GIT_REDIRECT_VARS`: GIT_DIR, GIT_WORK_TREE,
GIT_INDEX_FILE and the rest) dropped, with replace refs turned off, and with literal
pathspecs. A result never depends on the developer's `~/.gitconfig`, on an exported
GIT_DIR or on a `git replace` ref, and a path is never read as a glob.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_SCHEMAS = _HERE.parent / "schemas"

RECORD_TYPES = ("council-record", "refutation-record", "owner-exception", "reviewer-report")
#: The three gate-council seat roles, one per provider. The gate check and the runner share it.
SEAT_ROLES = ("openai_top", "anthropic_top", "google_top")
_SCHEMA_FILES = {kind: _SCHEMAS / f"{kind}.schema.json" for kind in RECORD_TYPES}


class RecordError(Exception):
    """Base class: every error names the path it concerns."""

    def __init__(self, path: Any, message: str):
        self.path = str(path)
        self.message = message
        super().__init__(f"{self.path}: {message}")


class InvalidRecord(RecordError):
    """A recognized record that fails its schema, or an unparseable JSON file."""


class SymlinkRefused(RecordError):
    """A record file or a council directory that is a symlink."""


class PathRefused(RecordError):
    """A path that escapes the repository, traverses a symlink, or is not a path."""


class BookResolutionError(RecordError):
    """A book that cannot be found, loaded, or bound to the run by content hash."""


# ───────────────────────────── hashing ─────────────────────────────


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ───────────────────────────── validator access ─────────────────────────────

_VP: Any = None


def _validator() -> Any:
    """`validate-promptbook.py`, loaded once by path. It owns the schema engine,
    the book loader and the book content hash, so this module never reimplements
    any of them."""
    global _VP
    if _VP is None:
        spec = importlib.util.spec_from_file_location("_vp_for_council_records", _HERE / "validate-promptbook.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _VP = mod
    return _VP


def schema_errors(doc: Any, record_type: str) -> list[str]:
    """The schema errors of `doc` against the schema for `record_type`, as short
    strings. An empty list means the document is valid."""
    vp = _validator()
    schema = vp.load_schema(_SCHEMA_FILES[record_type])
    errors: list[dict] = []
    vp.validate(doc, schema, "#", "#", errors, record_type)
    return [f"{e['instance_path']}: {e['error']}" for e in errors]


# ───────────────────────────── records ─────────────────────────────


@dataclass(frozen=True)
class Record:
    path: Path
    doc: dict
    record_type: str


def load_record(path: Path | str) -> Record | None:
    """Load through the current public record schemas."""
    return _load_record(path, schema_errors)


def _load_record(path: Path | str, check_schema) -> Record | None:
    """Load one JSON file as a record.

    Returns None when the file is valid JSON whose `record_type` is not one of the
    four recognized values. Raises `InvalidRecord` when the file is not parseable
    JSON or when a recognized record fails its schema."""
    p = Path(path)
    try:
        doc = json.loads(p.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidRecord(p, f"not parseable JSON ({type(exc).__name__})") from exc
    if not isinstance(doc, dict):
        return None
    kind = doc.get("record_type")
    if kind not in RECORD_TYPES:
        return None
    errors = check_schema(doc, kind)
    if errors:
        # The validator's error text echoes the offending value, and the gate prints this message.
        # Keep the instance paths only, as run-council.py does for a record it is about to write.
        # An unknown property's instance path is its own name, so a path that matches the secret
        # scan is withheld too.
        import secret_scan  # standard library only; imported here so loading stays light
        paths = sorted({e.split(": ", 1)[0] for e in errors})
        shown = ["[withheld: matched the secret scan]" if secret_scan.scan_text(x) else x for x in paths]
        raise InvalidRecord(p, f"fails the {kind} schema at: " + ", ".join(shown[:3]))
    return Record(p, doc, kind)


def discover_records(run_dir: Path | str) -> list[Record]:
    """Discover current records with the public schema loader."""
    return _discover_records(run_dir, load_record)


def _discover_records(run_dir: Path | str, load) -> list[Record]:
    """Every record directly under `<run_dir>/council/`, in file-name order.

    A missing directory holds no records. A symlinked directory, or a symlink
    named `*.json` inside it, raises `SymlinkRefused`. Non-record JSON is skipped."""
    d = Path(run_dir) / "council"
    if d.is_symlink():
        raise SymlinkRefused(d, "the council directory is a symlink")
    if not d.exists():
        return []
    if not d.is_dir():
        raise PathRefused(d, "the council path is not a directory")
    out: list[Record] = []
    for child in sorted(d.iterdir(), key=lambda c: c.name):
        if not child.name.endswith(".json"):
            continue
        if child.is_symlink():
            raise SymlinkRefused(child, "a record file is a symlink")
        if not child.is_file():
            continue
        rec = load(child)
        if rec is not None:
            out.append(rec)
    return out


# ───────────────────────────── git ─────────────────────────────


#: Variables that redirect or bound git's repository discovery, or point it at another
#: repository's directory, index or objects. Every git call here drops each one, so an ambient
#: value (a dotfiles alias's GIT_DIR, a hook's GIT_INDEX_FILE) never makes git judge another
#: repository's HEAD and index. The repository probe below shares this one list.
#: GIT_REPLACE_REF_BASE names where git looks for replace refs; `git_isolated_env` also turns
#: replace refs off, so dropping it is a second guard.
GIT_REDIRECT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                     "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
                     "GIT_REPLACE_REF_BASE")


#: The value of GIT_GRAFT_FILE in every cycle git read: a child of the null device, which can never
#: exist. git reads a missing grafts file as none, silently. `os.devnull` itself would open, and git
#: then prints a grafts-deprecation hint on every call.
NO_GRAFT_FILE = os.path.join(os.devnull, "crux-no-grafts")


def git_isolated_env() -> dict[str, str]:
    """The ambient environment with every `GIT_REDIRECT_VARS` entry dropped, replace refs
    turned off (GIT_NO_REPLACE_OBJECTS=1) and grafts turned off (GIT_GRAFT_FILE=NO_GRAFT_FILE).

    A replace ref (`git replace <object> <replacement>`, stored under `refs/replace/`) makes
    every read of `<object>` return `<replacement>`. Honoured, it lets a dirty working-tree blob
    read as the committed one, or HEAD read as a commit the branch never made. A legacy grafts
    file (`.git/info/grafts`, or an ambient GIT_GRAFT_FILE) rewrites a commit's parents the same
    way, so it can hide a committed version from history. A shallow boundary (`.git/shallow`) has
    no off switch; `council_gate` reads a shallow repository's history as unreadable. The
    base-commit pin and the blast-radius gate build their git environment here too."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_REDIRECT_VARS}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = NO_GRAFT_FILE
    return env


def _git_env(literal_pathspecs: bool = True) -> dict[str, str]:
    env = git_isolated_env()
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    if literal_pathspecs:
        env["GIT_LITERAL_PATHSPECS"] = "1"
    else:
        env.pop("GIT_LITERAL_PATHSPECS", None)
    return env


def git(repo: Path | str, *args: str, literal_pathspecs: bool = True) -> subprocess.CompletedProcess:
    """Run git in `repo`; bytes in the result, never raising on a nonzero exit.

    Pathspecs are literal unless `literal_pathspecs` is false, which `check-ignore`
    needs: it refuses the literal-pathspec setting outright."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          env=_git_env(literal_pathspecs))


def repo_root(path: Path | str) -> Path | None:
    """The resolved git work-tree root containing `path`, or None outside a repository."""
    p = Path(path).resolve()
    start = p if p.is_dir() else p.parent
    if not start.exists():
        return None
    r = git(start, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        return None
    return Path(os.fsdecode(r.stdout.rstrip(b"\n"))).resolve()


def resolve_in_repo(repo: Path | str, path: Path | str) -> tuple[Path, str]:
    """Resolve `path` (relative to `repo`, or absolute) to ``(absolute, repo-relative posix)``.

    Raises `PathRefused` when the path is empty, escapes the repository, names the
    repository root, or traverses a symlinked directory. The last component may be
    a symlink: `path_state` reports it, and a caller decides."""
    repo_r = Path(repo).resolve()
    text = str(path)
    if not text or "\x00" in text:
        raise PathRefused(path, "not a usable path")
    cand = Path(text)
    cand = Path(os.path.normpath(cand if cand.is_absolute() else repo_r / cand))
    real = cand.parent.resolve() / cand.name
    try:
        rel = real.relative_to(repo_r)
    except ValueError:
        raise PathRefused(path, "resolves outside the repository") from None
    if str(rel) in ("", "."):
        raise PathRefused(path, "names the repository root")
    # A lexical path that differs from its resolved form traverses a symlink.
    try:
        lex_rel = cand.relative_to(repo_r)
    except ValueError:
        lex_rel = None
    if lex_rel is not None and lex_rel != rel:
        raise PathRefused(path, "traverses a symlinked directory")
    return real, rel.as_posix()


def below_repo(path: Path | str, repo: Path | str) -> tuple[str, ...] | None:
    """The components of the absolute, unresolved `path` below `repo`, counted from the
    shortest prefix whose real path is the repository root, or None when no prefix is. A
    symlink above the root does not count."""
    parts = Path(path).parts
    root = os.path.realpath(repo)
    for i in range(1, len(parts) + 1):
        if os.path.realpath(Path(*parts[:i])) == root:
            return parts[i:]
    return None


def symlink_below(repo: Path | str, rest: tuple[str, ...]) -> bool:
    """True when a component of `rest`, taken from `repo` down, is a symlink. Stops at the
    first component that does not exist."""
    cur = Path(repo)
    for part in rest:
        cur = cur / part
        try:
            if stat.S_ISLNK(os.lstat(cur).st_mode):
                return True
        except OSError:
            return False
    return False


#: The probe below drops every redirecting variable (the shared list, through `_git_env`), so an
#: ambient value cannot make a directory inside a repository read as outside every one.
_DISCOVERY_VARS = GIT_REDIRECT_VARS


def _inside_a_repository(directory: Path) -> bool:
    """True when `directory`, taken physically, lies in a git work tree or a git directory.

    Fails closed: when git cannot run, or fails for any reason other than "not a git
    repository", the directory counts as inside one."""
    env = _git_env()
    for var in _DISCOVERY_VARS:
        env.pop(var, None)
    env.update({"GIT_DISCOVERY_ACROSS_FILESYSTEM": "1", "LC_ALL": "C", "LANGUAGE": "C"})
    try:
        r = subprocess.run(["git", "-C", str(directory), "rev-parse", "--git-dir"],
                           capture_output=True, env=env)
    except OSError:
        return True
    if r.returncode == 0:
        return True
    return b"not a git repository" not in r.stderr


def reached_through_symlink(path: Path | str) -> bool:
    """True when opening `path` would follow a symlink at its leaf, or a symlink that sits
    at or below a git repository root.

    The walk takes `path` exactly as it will be opened: a relative path from the working
    directory, with no lexical normalization, so `link/..` is walked through `link`. Each
    prefix is checked with `lstat`, so every symlink the open would follow is seen. A
    symlink other than the leaf counts when the directory that physically holds it lies
    inside a git repository; a symlink held outside every repository (`/var` ->
    `/private/var` on macOS) sits above any root and is followed. The repository is
    found from the holding directory, never from the path's physical target, so an
    in-repository link to a directory outside every repository, or to another
    repository's root, counts. When git cannot judge a holding directory, its symlink
    counts. The walk stops, returning False, at the first prefix `lstat` cannot read: one
    that does not exist, and also one it is denied or otherwise fails on (any `OSError`).

    A false refusal is possible, and is the fail-closed side: a checkout reached through
    a symlink held inside an enclosing repository (a home directory under version
    control) is refused."""
    raw = Path(path)
    parts = (raw if raw.is_absolute() else Path(os.getcwd()) / raw).parts
    cur = Path(parts[0])
    last = len(parts) - 1
    for i, part in enumerate(parts[1:], start=1):
        holder = cur
        cur = cur / part
        try:
            is_link = stat.S_ISLNK(os.lstat(cur).st_mode)
        except OSError:
            return False
        if is_link and (i == last or _inside_a_repository(holder)):
            return True
    return False


@dataclass(frozen=True)
class PathState:
    """What HEAD, the index and the working tree hold for one repo-relative path.

    The hashes are sha256 of the bytes, or None when that place holds no regular
    file for the path. `symlink` is true when the working tree entry or the index
    entry is a symlink; a symlink's target is never read."""

    path: str
    tracked: bool
    ignored: bool
    symlink: bool
    head: str | None
    index: str | None
    worktree: str | None

    @property
    def clean(self) -> bool:
        return (self.tracked and not self.symlink and self.head is not None
                and self.head == self.index == self.worktree)


def _blob_sha256(repo: Path, spec: str) -> str | None:
    r = git(repo, "cat-file", "blob", spec)
    return sha256_bytes(r.stdout) if r.returncode == 0 else None


def path_state(repo: Path | str, rel: str) -> PathState:
    """The `PathState` of repo-relative `rel`. Raises `PathRefused` on an escape."""
    repo_r = Path(repo).resolve()
    absolute, rel = resolve_in_repo(repo_r, rel)
    listing = git(repo_r, "ls-files", "-s", "-z", "--", rel)
    entries = [e for e in listing.stdout.split(b"\x00") if e] if listing.returncode == 0 else []
    tracked = bool(entries)
    index_symlink = any(e.split(b" ", 1)[0] == b"120000" for e in entries)
    stage0 = [e for e in entries if e.split(b"\t", 1)[0].split(b" ")[2:3] == [b"0"]]
    index_sha = _blob_sha256(repo_r, f":0:{rel}") if stage0 else None
    head_sha = _blob_sha256(repo_r, f"HEAD:{rel}")
    symlink = absolute.is_symlink() or index_symlink
    worktree = None
    if absolute.is_file() and not absolute.is_symlink():
        worktree = sha256_file(absolute)
    ignored = False
    if not tracked:
        ignored = git(repo_r, "check-ignore", "-q", "--", rel, literal_pathspecs=False).returncode == 0
    return PathState(rel, tracked, ignored, symlink, head_sha, index_sha, worktree)


def is_clean(repo: Path | str, rel: str) -> bool:
    """True when HEAD, the index and the working tree hold the same content for `rel`."""
    return path_state(repo, rel).clean


# ───────────────────────────── book resolution ─────────────────────────────


@dataclass(frozen=True)
class ResolvedBook:
    path: Path
    book: dict
    explicit: bool


def _load_book(path: Path) -> dict:
    vp = _validator()
    try:
        text = path.read_bytes().decode("utf-8")
        book = vp.load_yaml(text)
    except Exception as exc:  # unreadable or unparseable: one typed error, path named
        raise BookResolutionError(path, f"the book cannot be loaded ({type(exc).__name__})") from exc
    if not isinstance(book, dict):
        raise BookResolutionError(path, "the book is not a mapping")
    return book


def resolve_book(run_path: Path | str, book_arg: Path | str | None = None) -> ResolvedBook:
    """The book a run snapshot started from, verified by `book_content_hash`.

    Order: the explicit `book_arg`; the layout
    `<docs>/promptbooks/runs/<dir>/run-RUN-NNN.yaml` -> `<docs>/promptbooks/active/<dir>.yaml`;
    then `<docs>/promptbooks/archive/<dir>.yaml`. Raises `BookResolutionError` when no
    candidate exists, when the book cannot be loaded, or when its content hash differs
    from the run's `book_content_hash`."""
    vp = _validator()
    run_p = Path(run_path)
    try:
        run = vp.load_yaml(run_p.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise BookResolutionError(run_p, f"the run snapshot cannot be loaded ({type(exc).__name__})") from exc
    if not isinstance(run, dict) or not isinstance(run.get("book_content_hash"), str):
        raise BookResolutionError(run_p, "the run snapshot carries no book_content_hash")

    if book_arg is not None:
        candidates = [Path(book_arg)]
        explicit = True
    else:
        explicit = False
        real = run_p.resolve()
        runs_dir = real.parent.parent
        if runs_dir.name != "runs" or runs_dir.parent.name != "promptbooks":
            raise BookResolutionError(run_p, "the run snapshot is not at "
                                      "<docs>/promptbooks/runs/<dir>/ and no --book was given")
        books = runs_dir.parent
        candidates = [books / "active" / f"{real.parent.name}.yaml",
                      books / "archive" / f"{real.parent.name}.yaml"]
    repo = None if explicit else repo_root(run_p)
    for cand in candidates:
        if cand.is_symlink():
            raise BookResolutionError(cand, "the book is a symlink; a book is never read through one")
        if cand.is_file():
            if not explicit:
                # `books` is already resolved, so a layout candidate that resolves anywhere else
                # sits behind a symlinked directory; one outside the repository is never the
                # run's book.
                real_cand = cand.resolve()
                if real_cand != cand or (repo is not None and not real_cand.is_relative_to(repo)):
                    raise BookResolutionError(cand, "the book resolves through a symlink or outside "
                                              "the repository")
            book = _load_book(cand)
            if vp.compute_book_hash(book) != run["book_content_hash"]:
                raise BookResolutionError(cand, "the book's content hash differs from the run's "
                                          "book_content_hash")
            return ResolvedBook(cand, book, explicit)
    raise BookResolutionError(run_p, "no book found: tried " + ", ".join(str(c) for c in candidates))


__all__ = [
    "RECORD_TYPES", "SEAT_ROLES", "GIT_REDIRECT_VARS", "git_isolated_env", "RecordError", "InvalidRecord", "SymlinkRefused", "PathRefused",
    "BookResolutionError", "Record", "ResolvedBook", "PathState", "sha256_bytes", "sha256_file",
    "schema_errors", "load_record", "discover_records", "git", "repo_root", "resolve_in_repo",
    "path_state", "is_clean", "resolve_book", "below_repo", "symlink_below", "reached_through_symlink",
]
