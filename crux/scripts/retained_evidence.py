"""Classify the containing project's retained demonstration evidence holding.

Pass its already validated configured docs_root. Relative paths are docs-root
relative. No configuration or retained content is read by this primitive.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

# Recognition preserves historical loose-hyphen slugs. New authorship uses
# kebab-case; narrowing exclusion to new authorship would expose old evidence.
_BOOK_DIRECTORY = re.compile(
    r"(?:(?!(?:PB|ADR|RUN|BRIEF)-)[A-Z][A-Z0-9]{1,9}-)?PB-[0-9]{4}-[a-z0-9-]+",
    re.ASCII,
)
_TAIL = ("evidence", "implementation-cycles", "retained-repositories")


class RetainedEvidenceRefusal(ValueError):
    """A path cannot safely be classified before discovery."""


def _refuse() -> None:
    raise RetainedEvidenceRefusal("retained-evidence-layout-refused")


def _lexical(path: Path) -> Path:
    """Validate configured layout spelling, independently of source names."""
    path = Path(path)
    if ".." in path.parts or "\\" in str(path) or str(path).startswith("~") or re.match(r"^[A-Za-z]:", str(path)):
        _refuse()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in str(path)):
        _refuse()
    return path


def _candidate(path: Path) -> Path:
    """Keep POSIX filename bytes literal; reject traversal and invalid NUL."""
    path = Path(path)
    if ".." in path.parts or "\x00" in str(path):
        _refuse()
    return path


def _root(docs_root: Path, *, _source_io=None) -> tuple[Path, Path]:
    lexical = _lexical(docs_root).absolute()
    try:
        if _source_io is None:
            resolved = lexical.resolve()
            if resolved.exists() and not resolved.is_dir():
                _refuse()
        else:
            resolved = _source_io.resolve(lexical)
            if _source_io.kind(resolved) not in (None, "directory"):
                _refuse()
        return lexical, resolved
    except (OSError, RuntimeError, ValueError):
        _refuse()


def _holding(root: Path, path: Path) -> Path | None:
    if not path.is_relative_to(root):
        return None
    parts = path.relative_to(root).parts
    if len(parts) < 6 or parts[:2] != ("promptbooks", "runs") or parts[3:6] != _TAIL:
        return None
    if not _BOOK_DIRECTORY.fullmatch(parts[2]):
        _refuse()
    return root.joinpath(*parts[:6])


def spells_holding(path: Path) -> bool:
    """True when any component run of an absolute spelling is a holding.

    Spelling only, with no filesystem access: every ancestor is tried as a
    docs root against the canonical holding grammar, configured namespaces
    included. Traversal, NUL and a misnamed spelled book refuse.
    """
    path = _candidate(path)
    if not path.is_absolute():
        _refuse()
    return any(_holding(Path(*path.parts[:end]), path) is not None
               for end in range(1, len(path.parts)))


def holding_root_for_path(docs_root: Path, path: Path) -> Path | None:
    """Recognize a holding prefix in absolute spelling without filesystem access.

    The caller supplies its validated canonical docs root and anchored path.
    Pending parent components remain literal: traversal owns their resolution.
    A suffix cannot erase a holding that traversal must prune before descent.
    This classification grants no containment, layout validation or authority.
    """
    root=_lexical(Path(docs_root));path=Path(path)
    if not root.is_absolute() or not path.is_absolute() or "\x00" in str(path):
        _refuse()
    return _holding(root,path)


def _layout_directory(root: Path, path: Path, *, _source_io=None) -> bool:
    """Check only layout ancestors; never follow a holding link."""
    current = root
    for component in path.relative_to(root).parts:
        current /= component
        try:
            if _source_io is None:
                mode = current.lstat().st_mode
            else:
                metadata = _source_io.metadata(current)
                if metadata is None:
                    return False
                mode = metadata["st_mode"]
        except FileNotFoundError:
            return False
        except OSError:
            _refuse()
        if not stat.S_ISDIR(mode):
            _refuse()
    return True


def _resolve_until_holding(root: Path, path: Path, *,
                           _source_boundary: Path | None = None,
                           _source_io=None) -> bool | Path | None:
    """Resolve aliases one component at a time, stopping before held contents."""
    current = Path(path.anchor)
    pending = list(path.parts[1:])
    anchor = None
    if _source_io is not None:
        anchor = _source_io.resolve(_source_io.root)
        if not path.is_relative_to(anchor):
            _refuse()
        current, pending = anchor, list(path.relative_to(anchor).parts)
    links = 0
    while pending:
        component = pending.pop(0)
        if component == "..":
            current = current.parent
            if anchor is not None and not current.is_relative_to(anchor):
                _refuse()
            if _source_boundary is not None and not current.is_relative_to(_source_boundary):
                return None
            continue
        current /= component
        if _source_boundary is not None:
            if not (current.is_relative_to(_source_boundary) or _source_boundary.is_relative_to(current)):
                return None
            # Validate a spelled holding's ancestors before following a layout
            # link. Do not inspect any suffix once this walk enters a holding.
            spelled_holding = _holding(root, current.joinpath(*pending))
            if spelled_holding is not None:
                _layout_directory(root, spelled_holding, _source_io=_source_io)
                return None
        holding = _holding(root, current)
        if holding is not None:
            _layout_directory(root, holding, _source_io=_source_io)
            return True if _source_boundary is None else None
        try:
            metadata = None if _source_io is None else _source_io.metadata(current)
            if _source_io is not None and metadata is None:
                raise FileNotFoundError
            mode = current.lstat().st_mode if _source_io is None else metadata["st_mode"]
        except FileNotFoundError:
            if _source_boundary is not None:
                return None
            continue
        except OSError:
            if _source_boundary is not None:
                return None
            _refuse()
        if stat.S_ISLNK(mode):
            links += 1
            if links > 40:
                if _source_boundary is not None:
                    return None
                _refuse()
            try:
                target = Path(os.readlink(current) if _source_io is None else metadata["target"])
            except OSError:
                if _source_boundary is not None:
                    return None
                _refuse()
            target = target if target.is_absolute() else current.parent / target
            if anchor is None:
                pending = list(target.parts[1:]) + pending
                current = Path(target.anchor)
            else:
                if not target.is_relative_to(anchor):
                    _refuse()
                pending = list(target.relative_to(anchor).parts) + pending
                current = anchor
        elif _source_boundary is not None and pending and not stat.S_ISDIR(mode):
            return None
    if current.parent.is_relative_to(root):
        _layout_directory(root, current.parent, _source_io=_source_io)
    return False if _source_boundary is None else current


def resolve_source_path(repo_root: Path, docs_root: Path, path: Path) -> Path | None:
    """Resolve an existing source inside this repository, or prune it.

    Relative sources are repository-root relative. Parent components follow
    actual directory/symlink traversal; entering retained evidence prunes the
    entire candidate before its remaining components are inspected. Invalid
    declared holding layouts still raise. This discovery result grants no
    authority and does not replace the consumer's safe read.
    """
    lexical_repo, repo = _root(repo_root)
    _, docs = _root(docs_root)
    if not docs.is_relative_to(repo):
        _refuse()
    path = Path(path)
    if "\x00" in str(path):
        _refuse()
    path = path if path.is_absolute() else lexical_repo / path
    if path.is_relative_to(lexical_repo):
        path = repo / path.relative_to(lexical_repo)
    elif not path.is_relative_to(repo):
        return None
    return _resolve_until_holding(docs, path, _source_boundary=repo)


def is_retained_evidence_path(docs_root: Path, path: Path, *, _source_io=None) -> bool:
    """True for lexical or resolved holding membership; raise on invalid layout.

    docs_root is the containing project's validated configuration result.
    Ordinary source paths outside it remain outside this exclusion. This
    predicate does not replace the caller's repository containment check.
    """
    path = _candidate(path)
    lexical_root, root = _root(docs_root, _source_io=_source_io)
    path = path if path.is_absolute() else lexical_root / path
    if path.is_relative_to(lexical_root):
        path = root / path.relative_to(lexical_root)
    holding = _holding(root, path)
    if holding is not None:
        _layout_directory(root, holding, _source_io=_source_io)
        return True
    return _resolve_until_holding(root, path, _source_io=_source_io)


def retained_evidence_roots(docs_root: Path) -> tuple[Path, ...]:
    """Existing holding roots, sorted; inspect layout only, never held content."""
    _, root = _root(docs_root)
    runs = root / "promptbooks" / "runs"
    if not _layout_directory(root, runs):
        return ()
    try:
        roots = []
        for book in sorted(runs.iterdir()):
            mode = book.lstat().st_mode
            if stat.S_ISLNK(mode):
                _refuse()
            if not stat.S_ISDIR(mode):
                continue
            holding = book.joinpath(*_TAIL)
            if _layout_directory(root, holding):
                _holding(root, holding)
                roots.append(holding)
        return tuple(roots)
    except OSError:
        _refuse()
