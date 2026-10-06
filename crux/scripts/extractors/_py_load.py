"""Static Python source loader (ADR-0131 clauses 1, 5, 7, 11).

Turns selected Python sources into Griffe modules, statically. Requires
`griffelib==2.3.0` (see the dispatcher's PEP 723 block, which declares the
pin this file relies on). The dispatcher's `load_extractor_module` loads
each extractor by file path under a name outside the `extractors` package,
so an extractor module runs with no package context and a relative import
would fail; this file has no sibling `import` statements for `_py_locals` or
the dispatcher for that reason — both are loaded by file path below, the
same way `fallback.py` loads the dispatcher module.

## What this module does not do

It never calls `griffe.load_extensions`, `griffe.load`, or
`griffe.GriffeLoader`, and it never sets `allow_inspection=True` anywhere.
Every `griffe.visit` call here passes an explicit `griffe.Extensions`
carrying exactly one extension, `FunctionLocalDefs`. Griffe's dynamic
inspection path is an Inspector concept this module never reaches: `visit`
only ever compiles source text to an AST and walks it (ADR-0131 clause 1).

Aliases (`from .util import x`) are left unresolved here. This module's job
is to make the target reachable — the parent/child Griffe relationships a
resolver needs to walk from `pkg` to `pkg.util` to `x` — not to walk it.
Resolving within the selection, detecting cycles, and naming an
out-of-selection target as a gap is the composing extractor's job (ADR-0131
clause 11).

## Module naming (a documented scope decision ADR-0131 leaves to this loader)

A repo-relative path becomes a dotted module name in two steps:

1. Drop the file's `.py`/`.pyi` suffix. Drop a trailing `__init__` path
   component: an `__init__` file names its own DIRECTORY, never
   `<pkg>.__init__` — `pkg/__init__.py` names `pkg`.
2. Escape each remaining path component that is not a valid Python
   identifier (`_safe_segment`, below). `pkg/mod.py` therefore becomes
   `pkg.mod` when both are valid identifiers; `crux/scripts/crux-config.py`
   becomes `crux.scripts.0crux_002d_config` (escaped, see `_safe_segment`).

A directory is a "package directory" only when its own `__init__.py` (or
`__init__.pyi`, in the stub bucket) is itself among the selected units — no
namespace-package inference, no filesystem probing beyond the given units.
For a package directory, real Griffe parent/child links are built (the
outer-most package directory in a contiguous package-directory chain is
visited with no Griffe parent and a `module_name` equal to its own full
dotted path from the repo root; every directory and file nested inside that
chain is visited with `parent=<the containing package's Module>` and a
`module_name` equal to its own leaf segment, so Griffe's own `Object.path`
property computes the correct full path through the parent chain). A file
outside any package-directory chain is loaded standalone, with no Griffe
parent, under its own full dotted path. This is a documented scope decision
ADR-0131 itself leaves open: a directory nested inside a real package that itself lacks
an `__init__.py`/`__init__.pyi` (a PEP 420 namespace subpackage) gets no
parent link here — it still loads, under a deterministic, unique name, but a
relative import crossing that boundary is the composing extractor's gap to
name, not a resolution this loader attempts.

### Import roots (a bounded extension of this scheme for absolute imports by
### a package's installable name, rather than its repo-relative path)

A path-derived name (`src.pkg.m`) is what a relative import resolves to, but
not what an absolute import by a package's installable name (`pkg.m`) says.
Each unit inside a package-directory chain therefore also records, in
`LoadedSelection.import_roots` and `.root_names`:

- its import root: the parent directory of the chain's top-of-chain package
  directory (a package directory whose parent is not one), `""` for the
  repository root; and
- its root-stripped name: its dotted name relative to that root, with the
  same `_safe_segment` escaping (`src/pkg/m.py` -> root `src`, name `pkg.m`).

A unit outside any package chain records neither. Nothing here adds a root to
a Griffe search path or reads a file outside the selection: the names index
only the selected units. Displayed module names and page paths stay
path-derived.

The page renderer (`_py_page._target`) consults the root-stripped names only
when both conditions hold: the importing module's own import root is not the
repository root, and the target's first segment is a top-of-chain package
under that same root (the false-root guard). Otherwise the path-derived lookup
answers alone, exactly as before. The repository root is excluded because a
relative import there yields the same target text as an absolute one, so an
ambiguity check could not tell them apart. When the fallback applies, every
selected `.py` unit whose path-derived or root-stripped name equals a prefix
of the target is a candidate, deduplicated by unit. A prefix with more than
one candidate is ambiguous and the export stays the existing unresolved-export
gap; otherwise the longest matched prefix is walked by member name. There is
no precedence between roots and no standard-library name list (that list
varies by interpreter minor and would break cross-minor byte identity).

Known limitation: a namespace package under `src/` (for example
`src/myorg/lib/` with no `src/myorg/__init__.py`) derives the root
`src/myorg`, so `myorg.lib.*` stays a gap; and a module inside it that
re-exports from a same-named top-level module (the standard library's `json`
from inside `src/myorg/json/`) is reported without a gap when its own package
defines the name: the page carries no gap bullet, the manifest row's `gaps` is
empty and the run summary counts 0 gaps, although the true target (the
top-level module, outside the selection) is owed a gap. Both are pinned by regressions in `test_python_page.py`.

### `_safe_segment` cannot collide with a valid identifier

A path component that already satisfies `str.isidentifier()` is used
verbatim. Otherwise every character is escaped (ASCII alphanumerics and `_`
pass through; everything else becomes a 6-character `_HHHH_` token of its
lowercase hex code point) and the whole escaped string is prefixed with the
digit `0`. A valid identifier never starts with a digit, so an escaped
segment can never equal a verbatim (unescaped) segment — the two output sets
are disjoint by construction. That is the specific, testable guarantee this
module naming scheme's own delegated decision (see above) asks for.

## Stubs (ADR-0131 clause 11)

`.py` and `.pyi` units are loaded into two entirely separate module trees —
`LoadedSelection.collection` and `.stub_collection` — built independently by
this same code path (`_build_bucket`, called once per bucket). Griffe's own
`set_member` silently merges a same-named `.py` module and `.pyi` module
into one (its "implicit stub support"); routing every stub through its own
collection, keyed and parented independently of any `.py` module of the
same name, means that merge path is never reached — a stub module and its
`.py` counterpart never share one `Module` object, and a stub-only module
still gets built and gets a page.

## Refusals (ADR-0131 clauses 5, 7)

`ExtractionRefusal` (path, cause) is raised, never a bare Griffe exception
and never a traceback, for:

- a source over the 2 MiB bound (checked on the raw bytes, before decode);
- a UTF-8 decode failure;
- a grammar failure under the Python 3.13 grammar (`ast.parse` with
  `feature_version=(3, 13)`), naming the path, line and message;
- a `RecursionError` anywhere in parsing or visiting, naming the path and
  "nesting exhausts recursion";
- a `LocalPlacementError` from `FunctionLocalDefs`, naming the qualified
  path and its reason;
- any other exception raised while visiting, naming the path and the
  exception class only.
"""

from __future__ import annotations

import importlib.util as _ilu
import io
import sys as _sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

import griffe

_HERE = Path(__file__).resolve().parent


def _load_sibling_by_path(module_name: str, filename: str):
    """Load `filename` (a sibling under extractors/) by file path.

    Never a plain `import`: this file has no sibling imports (see module
    docstring), so `_py_locals` stays independently loadable by importlib,
    exactly as `_py_locals.py`'s own header requires of its consumers.
    """
    cached = _sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = _ilu.spec_from_file_location(module_name, _HERE / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {_HERE / filename}")
    mod = _ilu.module_from_spec(spec)
    _sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_locals_mod = _load_sibling_by_path("crux_py_locals", "_py_locals.py")
FunctionLocalDefs = _locals_mod.FunctionLocalDefs
LocalPlacementError = _locals_mod.LocalPlacementError
EXTENSION_VERSION = _locals_mod.EXTENSION_VERSION

# Obtain ExtractionRefusal from the dispatcher module registered in
# sys.modules by `load_extractor_module`; fall back to loading
# ../extract-code-docs.py by path, exactly as fallback.py does, for a test
# or REPL context that loaded this file directly.
_dispatch = _sys.modules.get("extract_code_docs_dispatcher")
if _dispatch is None:  # pragma: no cover — exercised by direct-load callers
    _spec = _ilu.spec_from_file_location(
        "extract_code_docs_dispatcher",
        _HERE.parent / "extract-code-docs.py",
    )
    if _spec is not None and _spec.loader is not None:
        _dispatch = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_dispatch)
        _sys.modules["extract_code_docs_dispatcher"] = _dispatch
ExtractionRefusal = _dispatch.ExtractionRefusal  # type: ignore[attr-defined]


#: ADR-0131 clause 7: a selected source larger than this refuses before decoding.
MAX_SOURCE_BYTES = 2 * 1024 * 1024

#: ADR-0131 clause 5: the one grammar version this extractor parses under, on
#: every interpreter. Every other spelling (the provenance string, the page
#: renderer's own `ast.parse` calls) derives from this tuple rather than
#: repeating the digits, so the four spellings cannot drift apart.
GRAMMAR_VERSION = (3, 13)
GRAMMAR_VERSION_STR = ".".join(str(part) for part in GRAMMAR_VERSION)
_GRAMMAR_VERSION = GRAMMAR_VERSION


@dataclass(frozen=True)
class LoadedSource:
    """One selected source, loaded into a Griffe module. See module docstring."""

    rel_path: str
    text: str
    module: "griffe.Module"
    is_stub: bool


@dataclass(frozen=True)
class LoadedSelection:
    """The whole selection, loaded. See module docstring."""

    sources: tuple[LoadedSource, ...]
    collection: "griffe.ModulesCollection"
    stub_collection: "griffe.ModulesCollection"
    module_names: Mapping[str, str]
    #: rel_path -> the import root of the unit's package chain (see the
    #: module docstring's "Import roots" section). Only units inside a
    #: package-directory chain carry an entry.
    import_roots: Mapping[str, str] = field(default_factory=dict)
    #: rel_path -> the unit's dotted name relative to its import root.
    root_names: Mapping[str, str] = field(default_factory=dict)


def _safe_segment(name: str) -> str:
    """Escape `name` into a Python-identifier-shaped module-name segment.

    See the module docstring's "`_safe_segment` cannot collide with a valid
    identifier" section for the guarantee this makes and why it holds.
    """
    if name.isidentifier():
        return name
    escaped = []
    for ch in name:
        if ch.isascii() and (ch.isalnum() or ch == "_"):
            escaped.append(ch)
        else:
            escaped.append(f"_{ord(ch):04x}_")
    return "0" + "".join(escaped)


def _check_size(rel_path: str, raw: bytes) -> None:
    if len(raw) > MAX_SOURCE_BYTES:
        raise ExtractionRefusal(
            rel_path,
            f"source is {len(raw)} bytes, over the {MAX_SOURCE_BYTES}-byte bound; "
            "narrow the glob to exclude it",
        )


def _decode(rel_path: str, raw: bytes) -> str:
    """Decode `raw` the way the Python 3.13 tokenizer would: a UTF-8 BOM or a
    PEP 263 `coding:` declaration on line 1 or 2 selects the encoding, exactly
    as `tokenize.detect_encoding` reads it for CPython's own compiler. A BOM
    is stripped by the encoding itself (`utf-8-sig`), never by slicing text,
    so a source's line numbers are unchanged. A genuine decode failure, or an
    encoding name `detect_encoding` cannot resolve, still refuses named."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except SyntaxError as exc:
        # detect_encoding raises SyntaxError both for a declaration it cannot
        # honour and for undeclared bytes that are not UTF-8; the cause names
        # which one.
        message = str(exc)
        if message.startswith(("unknown encoding", "encoding problem")):
            cause = f"encoding declaration refused ({exc})"
        else:
            cause = f"cannot decode as UTF-8 ({exc})"
        raise ExtractionRefusal(rel_path, cause) from exc
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise ExtractionRefusal(rel_path, f"cannot decode as {encoding} ({exc})") from exc


def _check_grammar(rel_path: str, text: str) -> None:
    """ADR-0131 clause 7: the Python 3.13 grammar, on every interpreter."""
    import ast

    try:
        ast.parse(text, filename=rel_path, feature_version=_GRAMMAR_VERSION)
    except RecursionError as exc:
        raise ExtractionRefusal(rel_path, "nesting exhausts recursion") from exc
    except SyntaxError as exc:
        raise ExtractionRefusal(
            rel_path, f"line {exc.lineno}: {exc.msg} (under the Python {GRAMMAR_VERSION_STR} grammar)"
        ) from exc


def _make_extensions() -> "griffe.Extensions":
    """The one, exact extension set ADR-0131 clause 1 allows.

    Never `griffe.load_extensions()` — that would resolve a built-in,
    entry-point or configured extension this extractor does not own.
    """
    return griffe.Extensions(FunctionLocalDefs())


def _visit_or_refuse(
    rel_path: str,
    module_name: str,
    text: str,
    parent: "griffe.Module | None",
) -> "griffe.Module":
    try:
        return griffe.visit(
            module_name,
            Path(rel_path),
            text,
            extensions=_make_extensions(),
            parent=parent,
        )
    except RecursionError as exc:
        raise ExtractionRefusal(rel_path, "nesting exhausts recursion") from exc
    except LocalPlacementError as exc:
        raise ExtractionRefusal(exc.qualified_path, exc.reason) from exc
    except ExtractionRefusal:
        raise
    except Exception as exc:  # noqa: BLE001 — named refusal, never a traceback (clause 7)
        raise ExtractionRefusal(rel_path, f"{type(exc).__name__}: {exc}") from exc


def _dir_key(rel_path: str) -> str:
    parent = PurePosixPath(rel_path).parent
    return "" if str(parent) == "." else str(parent)


def _full_dotted(dir_key: str, leaf: str | None) -> str:
    parts = list(PurePosixPath(dir_key).parts) if dir_key else []
    if leaf is not None:
        parts.append(leaf)
    return ".".join(_safe_segment(p) for p in parts)


@dataclass
class _BucketResult:
    sources: list[LoadedSource] = field(default_factory=list)
    collection: "griffe.ModulesCollection" = field(default_factory=griffe.ModulesCollection)
    module_names: dict[str, str] = field(default_factory=dict)
    import_roots: dict[str, str] = field(default_factory=dict)
    root_names: dict[str, str] = field(default_factory=dict)


def _module_parts(rel: str) -> list[str]:
    """The path components naming `rel`'s module: `.py`/`.pyi` dropped, and a
    trailing `__init__` dropped so the file names its directory."""
    path = PurePosixPath(rel)
    parts = list(path.parent.parts) if str(path.parent) != "." else []
    if path.stem != "__init__":
        parts.append(path.stem)
    return parts


def _build_bucket(entries: list[tuple[str, str]], *, is_stub: bool) -> _BucketResult:
    """Build one independent Griffe module tree for one bucket (.py or .pyi).

    See the module docstring's "Module naming" and "Stubs" sections.
    """
    result = _BucketResult()
    text_by_rel = dict(entries)

    # dir_key ("" for repo root, else the repo-relative directory) -> the
    # rel_path of the __init__ file that makes it a package directory.
    dir_init: dict[str, str] = {}
    for rel, _text in entries:
        stem = PurePosixPath(rel).stem
        if stem == "__init__":
            dir_init[_dir_key(rel)] = rel

    def is_package_dir(dir_key: str) -> bool:
        return dir_key in dir_init

    def parent_dir_of(dir_key: str) -> str | None:
        if dir_key == "":
            return None
        parent = str(PurePosixPath(dir_key).parent)
        return "" if parent == "." else parent

    # Package directories, shallowest first, so a nested package's own
    # parent Module always already exists when we build the nested one.
    package_modules: dict[str, "griffe.Module"] = {}
    ordered_package_dirs = sorted(dir_init, key=lambda d: (d.count("/") if d else 0, d))

    # Two selected units can bind the same leaf name under the same parent
    # — a package directory and a module of the same name, e.g. `pkg/mod.py`
    # and `pkg/mod/__init__.py` both selected. Python's own import system
    # would silently prefer one of the two; this loader refuses instead
    # (fail closed) rather than let the later of the two silently replace
    # the earlier in the Griffe tree. `top_claims` covers a name registered
    # at the top of a chain (`register_top`); `nested_claims` covers a name
    # registered as a package's own child (`set_child`) — each keyed by the
    # rel_path of the unit that claimed the name first.
    top_claims: dict[str, str] = {}
    nested_claims: dict[int, dict[str, str]] = {}

    def register_top(dotted_name: str, module: "griffe.Module", rel: str) -> None:
        earlier = top_claims.get(dotted_name)
        if earlier is not None:
            raise ExtractionRefusal(
                rel, f"module name '{dotted_name}' collides with {earlier}"
            )
        top_claims[dotted_name] = rel
        # A top-of-chain module's dotted name may itself contain dots
        # (escaped directory components), so it is registered as one flat
        # key directly rather than through set_member's dotted-path
        # traversal (which requires each intermediate segment to already
        # exist as its own collection member).
        result.collection.members[dotted_name] = module
        module._modules_collection = result.collection  # noqa: SLF001 — mirrors set_member's own single-part-key behavior

    def set_child(parent_mod: "griffe.Module", leaf: str, module: "griffe.Module", rel: str) -> None:
        claims = nested_claims.setdefault(id(parent_mod), {})
        earlier = claims.get(leaf)
        if earlier is not None:
            raise ExtractionRefusal(
                rel, f"leaf name '{leaf}' collides with {earlier}"
            )
        claims[leaf] = rel
        parent_mod.set_member(leaf, module)

    for dir_key in ordered_package_dirs:
        rel = dir_init[dir_key]
        text = text_by_rel[rel]
        parent_dir = parent_dir_of(dir_key)
        if parent_dir is not None and is_package_dir(parent_dir):
            parent_mod = package_modules[parent_dir]
            leaf = _safe_segment(PurePosixPath(dir_key).name)
            mod = _visit_or_refuse(rel, leaf, text, parent_mod)
            set_child(parent_mod, leaf, mod, rel)
        else:
            dotted_name = _full_dotted(dir_key, None)
            mod = _visit_or_refuse(rel, dotted_name, text, None)
            register_top(dotted_name, mod, rel)
        package_modules[dir_key] = mod
        result.module_names[rel] = mod.path
        result.sources.append(LoadedSource(rel_path=rel, text=text, module=mod, is_stub=is_stub))

    for rel, text in entries:
        if PurePosixPath(rel).stem == "__init__":
            continue  # already built above, as its directory's package module
        dir_key = _dir_key(rel)
        leaf = _safe_segment(PurePosixPath(rel).stem)
        if is_package_dir(dir_key):
            parent_mod = package_modules[dir_key]
            mod = _visit_or_refuse(rel, leaf, text, parent_mod)
            set_child(parent_mod, leaf, mod, rel)
        else:
            dotted_name = _full_dotted(dir_key, leaf)
            mod = _visit_or_refuse(rel, dotted_name, text, None)
            register_top(dotted_name, mod, rel)
        result.module_names[rel] = mod.path
        result.sources.append(LoadedSource(rel_path=rel, text=text, module=mod, is_stub=is_stub))

    # Import roots (see the module docstring's "Import roots" section): the
    # parent directory of the top-of-chain package directory holding a unit.
    for rel, _text in entries:
        top = _dir_key(rel)
        if not is_package_dir(top):
            continue  # not inside a package chain: no import root
        while True:
            parent = parent_dir_of(top)
            if parent is None or not is_package_dir(parent):
                break
            top = parent
        root = parent_dir_of(top) or ""
        root_len = len(PurePosixPath(root).parts) if root else 0
        result.import_roots[rel] = root
        result.root_names[rel] = ".".join(
            _safe_segment(p) for p in _module_parts(rel)[root_len:])

    result.sources.sort(key=lambda s: s.rel_path)
    return result


def load_selection(units: Sequence[tuple[str, bytes]]) -> LoadedSelection:
    """Load `units` (repo-relative path, raw bytes) into a `LoadedSelection`.

    See the module docstring. Never imports or executes any of `units`;
    every failure refuses through `ExtractionRefusal`, never a traceback.
    """
    py_entries: list[tuple[str, str]] = []
    pyi_entries: list[tuple[str, str]] = []
    for rel_path, raw in units:
        _check_size(rel_path, raw)
        text = _decode(rel_path, raw)
        _check_grammar(rel_path, text)
        if rel_path.endswith(".pyi"):
            pyi_entries.append((rel_path, text))
        else:
            py_entries.append((rel_path, text))

    py_result = _build_bucket(py_entries, is_stub=False)
    pyi_result = _build_bucket(pyi_entries, is_stub=True)

    sources = tuple(sorted((*py_result.sources, *pyi_result.sources), key=lambda s: s.rel_path))
    module_names: dict[str, str] = {**py_result.module_names, **pyi_result.module_names}

    return LoadedSelection(
        sources=sources,
        collection=py_result.collection,
        stub_collection=pyi_result.collection,
        module_names=module_names,
        import_roots={**py_result.import_roots, **pyi_result.import_roots},
        root_names={**py_result.root_names, **pyi_result.root_names},
    )
