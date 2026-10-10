"""Swift stack pack (ADR-0129) — the static Swift extractors for the arch spine.

This pack populates three concerns for a repository whose root carries a
Swift marker: a `Package.swift`, a `*.xcodeproj` holding `project.pbxproj`,
a standalone `*.xcworkspace` holding `contents.xcworkspacedata`, or a
qualifying Tuist or XcodeGen generator manifest (ADR-0129 clause 3,
ADR-0130 clause 13):

* `data-model` — struct, enum, class and actor declarations at every access
  level, their stored properties, and their declared relationships;
* `api-surface` — public, package and open declarations, every protocol with
  its requirements, the literal manifest's products, and `@main` declarations
  with their owning Xcode or package targets;
* `module-graph` — Swift package and Xcode project containers, their targets,
  the in-tree dependency graph, the dependencies it does not draw, `.swift`
  file membership, and every Swift import.

Every Swift source and manifest is read through the pinned `tree-sitter-swift`
grammar. The Xcode inputs — `project.pbxproj`, a standalone workspace's
`contents.xcworkspacedata`, and `.xcconfig` files — are read by the
standard-library readers in `swift_pbxproj`, `swift_xcode`,
`swift_xcode_products` and `swift_xcinputs` (ADR-0130 clause 1). An XcodeGen
`project.yml` is shape-checked by `swift_generators` through the pinned
PyYAML, and a Tuist manifest is recognised by presence (ADR-0130 clause 13).
Nothing is compiled, built, evaluated, resolved or expanded: no Swift
toolchain, no SwiftPM, no plugin, no macro. What a static read cannot see
renders as a residual line from ADR-0129 clause 7's closed vocabulary, which
ADR-0130 clause 14 extends. A top-level ERROR that swallows a type
declaration gets one grammar-only recovery (`_recover`), bounded by node
positions in the grammar's own tree.

Each concern file is bounded at `_MAX_CONCERN_BYTES` of content lines. Past
the bound, rows stop in file order and one `scan-cap` line marks the stop.
The "every" claims above hold for a concern whose bound has not tripped. A
concern whose bound trips renders a fixed summary variant that counts the
rows "rendered before the output bound" and claims no "every".

Known limitation: grammar parse time is not bounded. ADR-0129 clause 2 rules
that no content makes the derive exit 2; it sets no bound on parse time.
Repeated blocks of type declarations the pinned grammar
(`tree-sitter-swift==0.7.3`) fails to parse make its parse time grow
quadratically with the file's size. A parse timeout is planned, and it would
add a new outcome for a file whose parse times out.

An Apple app repository and a standalone Swift package both derive (ADR-0129
clause 1). `_project_facts` feeds the Xcode reader's targets, dependencies and
memberships into the renderers through `ProjectFacts`.

`tree_sitter` and `tree_sitter_swift` are imported inside `_swift_parser`,
never at module scope. The core imports every pack module to read its
registry, so a module-scope import would make every other pack's derive
depend on a wheel only this pack declares.
"""

from __future__ import annotations

import bisect
import multiprocessing
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple

from . import swift_generators, swift_prune, swift_xcinputs, swift_xcode

from ..core import (
    _retained_source_filter,
    _retained_source_walk,
    DetectResult,
    InputClass,
    Probe,
    Verdict,
    StubReason,
    _MAX_FILE_BYTES,
    _MAX_GRAPH_EDGES,
    _MAX_SCAN_BYTES,
    _MAX_SCAN_FILES,
    _canon,
    _cell,
    _node_ids,
    _safe_read_bytes,
    _sha256_hex,
)

#: The importable modules every Swift concern's `parser` declaration names.
#: `INPUT_CLASSES` reads it, so `core.resolve_declared_parsers` refuses before
#: extraction on a machine without them and `core.parser_pins` records them.
SWIFT_PARSER_MODULES = ("tree_sitter", "tree_sitter_swift")

#: Every Swift keyword that opens a type declaration (ADR-0129 clause 7).
_TYPE_KEYWORDS = frozenset({"class", "struct", "enum", "actor", "protocol"})

#: The tree-sitter-swift node types tree-sitter-swift.js exposes as a bare
#: identifier token immediately after a type keyword.
_NAME_TOKEN_TYPES = ("type_identifier", "simple_identifier")

#: Built once per process, keyed by a constant name (there is exactly one
#: Swift grammar), so a derive that touches many Swift files parses through
#: one `tree_sitter.Parser` instance rather than rebuilding it per file.
_PARSERS: dict[str, object] = {}


def _swift_parser():
    """The pinned tree-sitter Swift parser (`tree-sitter-swift==0.7.3`), built once.

    `crux.arch.core.resolve_declared_parsers` runs before any extraction and
    raises `ParserUnavailable` when a pack's declared grammar is not
    importable, and `verify_parser_load` loads this parser there; reaching this
    function during a real derive therefore means the import resolves. Called
    directly, as this module's own tests do, it raises whatever `ImportError`
    the environment produces — there is no fallback parser.
    """
    if "swift" not in _PARSERS:
        import tree_sitter  # noqa: PLC0415
        import tree_sitter_swift  # noqa: PLC0415

        language = tree_sitter.Language(tree_sitter_swift.language())
        _PARSERS["swift"] = tree_sitter.Parser(language)
    return _PARSERS["swift"]


def _parse(raw: bytes):
    """Parse Swift source BYTES into a tree-sitter tree.

    Bytes, not text: every tree-sitter offset this module reads is a byte
    offset, and decoding `raw` before parsing would misalign every slice on a
    file that holds a multi-byte character before the byte position in
    question. The caller supplies the bytes a safe read already produced;
    nothing in this module opens a file.
    """
    return _swift_parser().parse(raw)


@dataclass(frozen=True)
class DroppedDeclaration:
    """One top-level ERROR node that has swallowed a type declaration.

    `node` is the ERROR node: a direct child of the parse tree's `source_file`
    root. `keyword` is the bare token child naming the swallowed declaration's
    kind (`class`/`struct`/`enum`/`actor`/`protocol`); `name` is the bare
    identifier token immediately after it. Both are still ordinary child nodes
    of the ERROR node — tree-sitter's error recovery keeps the tokens it
    lexed, only the surrounding grammar production is what is missing.
    """

    node: object
    keyword: object
    name: object


def _dropped_declarations(tree) -> list[DroppedDeclaration]:
    """Every top-level ERROR that has swallowed a type declaration.

    A qualifying ERROR is a direct child of the root `source_file` node whose
    own direct children include a bare token child of a type keyword with no
    `*_declaration` node wrapping it (ADR-0129 clause 7's `declaration-dropped`
    shape). A `class_declaration`/`protocol_declaration`/etc. node uses that
    exact string as its `.type`, never the bare keyword token's type, so
    checking each child's `.type` against `_TYPE_KEYWORDS` already excludes
    the case where the declaration parsed normally.
    """
    found: list[DroppedDeclaration] = []
    for child in tree.root_node.children:
        if child.type != "ERROR":
            continue
        error_children = list(child.children)
        keyword_node = None
        name_node = None
        for index, grandchild in enumerate(error_children):
            if grandchild.type in _TYPE_KEYWORDS:
                keyword_node = grandchild
                for candidate in error_children[index + 1 :]:
                    if candidate.type in _NAME_TOKEN_TYPES:
                        name_node = candidate
                    break
                break
        if keyword_node is not None and name_node is not None:
            found.append(DroppedDeclaration(node=child, keyword=keyword_node, name=name_node))
    return found


@dataclass(frozen=True)
class RecoveredDeclaration:
    """Grammar-only recovery of a declaration a top-level ERROR dropped.

    `tree` is the tree-sitter tree from re-parsing `slice_bytes`; its
    `root_node`'s only non-extra child is the recovered type declaration.
    Every text read for the recovered subtree MUST slice `slice_bytes`, never
    the original file's bytes — offsets inside `tree` are relative to the
    slice, not the file. `row_offset` is the dropping ERROR node's start row
    in the ORIGINAL file (0-based); add it to a 0-based row read from `tree`,
    then add 1, to recover the file's 1-based line. The column shifts only on
    the slice's own row 0 (every other row's column is already file-relative,
    because the slice cuts on a byte boundary, never a row boundary).
    `error_span` and `slice_span` are 1-based, inclusive `(start_line,
    end_line)` file-line pairs, for a `declaration-recovered` residual.
    """

    tree: object
    slice_bytes: bytes
    row_offset: int
    keyword: str
    name: str
    error_span: tuple[int, int]
    slice_span: tuple[int, int]


@dataclass(frozen=True)
class DroppedDeclarationRecord:
    """A `declaration-dropped` residual: recovery did not meet its postcondition.

    Carries the ERROR span and the keyword/name tree-sitter still lexed, and
    no subtree — ADR-0129 clause 7's failure route never demotes a fact or
    guesses a boundary; the caller renders this record and moves on.
    """

    keyword: str
    name: str
    error_span: tuple[int, int]


def _end_display_line(point) -> int:
    """The 1-based file line a node's tree-sitter END point last touches.

    `point` is a `(row, column)` pair, 0-based, naming the position just PAST
    a node's last byte. When `column` is 0 the node's bytes stop at the START
    of `row`, so the last row they actually reach is `row - 1` (0-based) —
    `row` in 1-based terms, with no `+ 1`. When `column` is nonzero the node's
    bytes reach into `row`, whose 1-based number is `row + 1`.
    """
    row, column = point
    return row + 1 if column > 0 else row


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _recover(err: DroppedDeclaration, src: bytes):
    """Grammar-only recovery of the declaration `err` covers (ADR-0129 clause 7).

    Every boundary comes from a node position already present in the tree that
    produced `err` — never from searching `src` as text, which clause 7 names
    a `regex-over-source` read and forbids. The slice runs from the ERROR
    node's start byte to the end byte of the LAST direct ERROR child that: is
    a `*_declaration` node, sits after the `{` token child, and has
    `has_error` False and `is_missing` False (a complete member). That slice
    is re-parsed through the pinned grammar exactly once.

    Recovery accepts only when the re-parse yields exactly one top-level
    (non-extra) node, itself a `*_declaration` node whose keyword and name
    tokens match `err`'s keyword and name bytes, and whose only error or
    MISSING nodes sit at or after the last recovered member's end byte (a
    trailing MISSING close brace, never damage inside a member). On accept,
    returns a `RecoveredDeclaration`. On any failed check, returns a
    `DroppedDeclarationRecord` and performs no further parse.
    """
    node = err.node
    children = list(node.children)
    error_span = (node.start_point[0] + 1, _end_display_line(node.end_point))
    keyword_text = _decode(src[err.keyword.start_byte : err.keyword.end_byte])
    name_text = _decode(src[err.name.start_byte : err.name.end_byte])

    def dropped():
        return DroppedDeclarationRecord(keyword=keyword_text, name=name_text, error_span=error_span)

    brace_index = next((i for i, c in enumerate(children) if c.type == "{"), None)
    if brace_index is None:
        return dropped()

    candidates = [
        c
        for c in children[brace_index + 1 :]
        if c.type.endswith("_declaration") and not c.has_error and not c.is_missing
    ]
    if not candidates:
        return dropped()

    last_member = candidates[-1]
    slice_bytes = src[node.start_byte : last_member.end_byte]

    tree = _parse(slice_bytes)  # the one re-parse clause 7 permits
    root = tree.root_node
    top_level = [c for c in root.children if not c.is_extra]
    if len(top_level) != 1:
        return dropped()

    decl = top_level[0]
    if not decl.type.endswith("_declaration"):
        return dropped()

    decl_children = list(decl.children)
    decl_keyword = next((c for c in decl_children if c.type == err.keyword.type), None)
    decl_name = next((c for c in decl_children if c.type in _NAME_TOKEN_TYPES), None)
    if decl_keyword is None or decl_name is None:
        return dropped()
    if slice_bytes[decl_keyword.start_byte : decl_keyword.end_byte] != src[
        err.keyword.start_byte : err.keyword.end_byte
    ]:
        return dropped()
    if slice_bytes[decl_name.start_byte : decl_name.end_byte] != src[
        err.name.start_byte : err.name.end_byte
    ]:
        return dropped()

    body = decl.child_by_field_name("body")
    last_real_member_end = None
    if body is not None:
        real_members = [c for c in body.children if not c.is_missing and c.type not in ("{", "}")]
        if real_members:
            last_real_member_end = real_members[-1].end_byte

    # An explicit stack, not recursion: a complete member inside the slice can
    # nest deeper than the interpreter's recursion limit (ADR-0129 clause 2).
    bad_nodes: list[object] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.is_error:
            bad_nodes.append(n)
            continue
        if n.is_missing:
            bad_nodes.append(n)
        stack.extend(reversed(n.children))

    for bad in bad_nodes:
        if bad.is_error:
            return dropped()
        if last_real_member_end is None or bad.start_byte < last_real_member_end:
            return dropped()

    slice_span = (node.start_point[0] + 1, _end_display_line(last_member.end_point))

    return RecoveredDeclaration(
        tree=tree,
        slice_bytes=slice_bytes,
        row_offset=node.start_point[0],
        keyword=keyword_text,
        name=name_text,
        error_span=error_span,
        slice_span=slice_span,
    )


# ═════════════════ the pack interface and the walk (ADR-0129) ═════════════════
# The stack-marker detector, the declared input set, the per-concern probe
# registry, and the bounded repository walk. `DETECT_EXCLUDE` is a
# REQUIRED module attribute the core's marker scan reads before it ever calls
# `detect` on a candidate directory; matching runs on the REPO-RELATIVE path
# only, never the absolute path, so an absolute checkout path containing one
# of these names never suppresses detection.

#: Directory names and `*`-suffix patterns the core's marker scan and every
#: Swift walk never descend into. `swift_prune` defines the set, so the
#: Xcode readers prune by the same rule; this is the same object.
DETECT_EXCLUDE: frozenset[str] = swift_prune.DETECT_EXCLUDE

#: The api-surface stub line names an "interface row", not the core's
#: default "route row" (this pack has no route concept).
ENTITY_NOUNS: dict[str, str] = {"api-surface": "interface row"}


def _excluded_name(name: str) -> bool:
    """True when `name` (one path component, not a full path) matches a
    `DETECT_EXCLUDE` entry. The core's marker scan applies the same rule, so
    the walk and the scan cannot disagree about which directories are out."""
    return swift_prune.excluded_name(name)


def detect(root: Path) -> DetectResult:
    """The Swift stack-marker detector (ADR-0129 clause 3; ADR-0130 clause
    13). At the repository root, a marker is a `Package.swift`, a
    `*.xcodeproj` directory holding `project.pbxproj`, a standalone
    `*.xcworkspace` directory holding `contents.xcworkspacedata`, a
    qualifying Tuist manifest, or a qualifying XcodeGen `project.yml`. The
    three container forms are presence-only, and a symlinked candidate is
    never followed (`is_dir`/`is_file` with `follow_symlinks` defaulted True
    would still read through it, so every check here goes through
    `Path.is_symlink()` first).

    The Tuist form is presence-only too: `Project.swift` or
    `Workspace.swift` beside `Tuist.swift` or `Tuist/Config.swift`. The
    XcodeGen form is the one marker that reads content: `project.yml` is
    read through the safe-read contract and qualifies only when its shape
    check passes (ADR-0130 clause 13). A bare `project.yml` that fails the
    shape check is not a marker (ADR-0129 clause 3). Neither is a stray
    top-level `.swift` file, nor an `.xcodeproj` directory with no
    `project.pbxproj` inside it (a stripped or partial checkout).
    """
    held = _retained_source_filter(root)
    markers: list[str] = []
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        if entry.is_symlink() or held(entry):
            continue
        name = entry.name
        if name == "Package.swift" and entry.is_file():
            markers.append(name)
        elif name.endswith(".xcodeproj") and entry.is_dir():
            pbx = entry / "project.pbxproj"
            if pbx.is_file() and not pbx.is_symlink():
                markers.append(name)
        elif name.endswith(".xcworkspace") and entry.is_dir():
            contents = entry / "contents.xcworkspacedata"
            if contents.is_file() and not contents.is_symlink():
                markers.append(name)
    # The Tuist and XcodeGen forms (ADR-0130 clause 13): each check performs
    # its own containment/symlink/FIFO-safe read (`swift_generators.
    # check_tuist`/`check_xcodegen`, via `core._safe_read_bytes` for the
    # latter), so no additional guard is needed here. Root-level only, same
    # as every other marker above -- a nested generator manifest names a
    # missing-input residual inside `_project_facts`, never a stack marker.
    if root.is_dir():
        tuist = swift_generators.check_tuist(root, "")
        if tuist.qualifies and tuist.manifest:
            markers.append(tuist.manifest)
        xcodegen = swift_generators.check_xcodegen(root, "")
        if xcodegen.qualifies and xcodegen.manifest:
            markers.append(xcodegen.manifest)
    markers = sorted(set(markers))
    return DetectResult(matched=bool(markers), markers=tuple(markers))


def _swift_walk(root: Path):
    """One `os.walk(followlinks=False)` pass shared by this module's four
    walkers: `_walk_swift_files`, `detect_containers`, `_project_candidates`
    and `_generator_residuals`. It applies the
    ADR-0129 clause 8 prune rule that `swift_prune` holds: `_SKIP_DIRS`,
    dot-directories, and every `DETECT_EXCLUDE` name or suffix are pruned
    from descent (a symlinked directory is never followed either, since
    `followlinks=False`). `swift_xcode._scan_swift_files`, the folder-synced
    walk, is a separate walker that reads the same rule from `swift_prune`.
    Yields `(dirp, rel_dir, normal_dirnames, excluded_dirnames, filenames)`
    per directory, dirnames and filenames sorted by codepoint, so no two of
    the four walkers can disagree about which directories a Swift walk
    enters.
    `excluded_dirnames` is exposed, not descended, so a caller that needs to
    look inside an excluded name — `detect_containers` checking a
    `*.xcodeproj` bundle for `project.pbxproj` — still can, without walking
    further into it.
    """
    for dirpath, dirnames, filenames in _retained_source_walk(root, root):
        dirp = Path(dirpath)
        rel_dir = "" if dirp == Path(root) else _rel_str(root, dirp)
        normal: list[str] = []
        excluded: list[str] = []
        for d in sorted(dirnames):
            if swift_prune.skipped_name(d):
                continue
            if swift_prune.excluded_name(d):
                excluded.append(d)
                continue
            normal.append(d)
        dirnames[:] = normal
        yield dirp, rel_dir, normal, excluded, sorted(filenames)


def detect_containers(root: Path) -> tuple[str, ...]:
    """Every Swift container marker the walk reaches, sorted, repo-
    relative: every `Package.swift` and every `*.xcodeproj` directory holding
    `project.pbxproj` (ADR-0129 clause 5's container definition). This
    function only names containers; `swift_xcode.read_projects` reads each
    `.xcodeproj` container's targets (ADR-0130). Built on the same `_swift_walk`
    `_walk_swift_files` uses, so a container never surfaces from inside a
    vendored or build directory.
    """
    found: list[str] = []
    for dirp, rel_dir, _normal, excluded, filenames in _swift_walk(root):
        for d in excluded:
            # `_swift_walk` lists an excluded directory without descending
            # into it; checking a file through a symlinked one would follow
            # it (clause 4), so the symlink check stays here.
            if d.endswith(".xcodeproj") and not (dirp / d).is_symlink():
                cand = dirp / d / "project.pbxproj"
                if cand.is_file() and not cand.is_symlink():
                    found.append((f"{rel_dir}/{d}" if rel_dir else d))
        if "Package.swift" in filenames:
            p = dirp / "Package.swift"
            if p.is_file() and not p.is_symlink():
                found.append((f"{rel_dir}/Package.swift" if rel_dir else "Package.swift"))
    return tuple(sorted(found))


def verify_parser_load() -> None:
    """Load the pinned grammar and build one parser.

    `resolve_declared_parsers` only checks `find_spec` (the module resolves on
    the import path); this additionally exercises `tree_sitter_swift.language()`
    and the `tree_sitter.Language`/`Parser` construction, so a wheel that
    resolves but is ABI-incompatible with the installed `tree_sitter` fails
    here, before any byte is written or compared, rather than at the first
    real parse deep inside a derive.
    """
    _swift_parser()


#: The Swift source-input glob set the `api-surface` and `module-graph`
#: `INPUT_CLASSES` entries share (ADR-0129 clause 4): every `.swift` file at
#: any depth, plus the manifest at the root and at any package depth (a local
#: `.package(path:)` dependency is itself a package with its own
#: `Package.swift`).
_SWIFT_SOURCE_GLOBS = ("*.swift", "**/*.swift", "Package.swift", "**/Package.swift")

#: The `.swift` globs alone. `data-model` reads and hashes `.swift` sources and
#: never `Package.swift`, so it declares these and no manifest glob.
_SWIFT_FILE_GLOBS = ("*.swift", "**/*.swift")

#: `project.pbxproj` carries a dedicated 16 MiB bound (ADR-0130 clause 2).
#: The clause states the number; it also calls it a quarter of the aggregate
#: per-concern byte cap, which is true while `_MAX_SCAN_BYTES` is 64 MiB. The
#: literal keeps a change to the aggregate cap from moving this bound, and
#: `ProjectFileBoundTests` pins it.
_MAX_PBXPROJ_BYTES = 16 * 1024 * 1024

#: The `project.pbxproj` glob every consuming concern shares (ADR-0130
#: clause 1): the file the reader reads inside any `*.xcodeproj` bundle the
#: Swift walk reaches, at any depth.
_PBXPROJ_GLOBS = ("*.xcodeproj/project.pbxproj", "**/*.xcodeproj/project.pbxproj")

#: The concern-specific project-input glob additions to `_SWIFT_SOURCE_GLOBS`
#: (ADR-0130 clause 1). `data-model` reads no project input -- it
#: renders no Xcode fact. `api-surface` reads `project.pbxproj` alone, for
#: the `@main` owning-target cell (clause 16). `module-graph` reads every
#: input the reader class names: `project.pbxproj`, a standalone workspace's
#: `contents.xcworkspacedata`, every `*.xcconfig`, and the XcodeGen
#: `project.yml` manifest (clause 13).
_PROJECT_INPUT_GLOBS: dict[str, tuple[str, ...]] = {
    "data-model": (),
    "api-surface": _PBXPROJ_GLOBS,
    "module-graph": _PBXPROJ_GLOBS + (
        "*.xcworkspace/contents.xcworkspacedata", "**/*.xcworkspace/contents.xcworkspacedata",
        "*.xcconfig", "**/*.xcconfig",
        "project.yml", "**/project.yml",
    ),
}

INPUT_CLASSES: dict[str, InputClass] = {
    "data-model": InputClass(
        expected=("Swift `struct`/`enum`/`class`/`actor` declarations with their stored properties "
                  "and conformances, read from `.swift` sources"),
        globs=_SWIFT_FILE_GLOBS,
        parser=SWIFT_PARSER_MODULES,
    ),
    "api-surface": InputClass(
        expected=("public/package/open Swift interfaces, protocol requirements, products and `@main` "
                  "declarations, read from `.swift` sources, `Package.swift` and `project.pbxproj`"),
        globs=_SWIFT_SOURCE_GLOBS + _PROJECT_INPUT_GLOBS["api-surface"],
        parser=SWIFT_PARSER_MODULES,
    ),
    "module-graph": InputClass(
        expected=("Swift and Xcode targets, target and product dependencies, file membership and "
                  "`import` statements, read from `.swift` sources, `Package.swift`, `project.pbxproj`, "
                  "`contents.xcworkspacedata`, `.xcconfig` and XcodeGen `project.yml` files"),
        globs=_SWIFT_SOURCE_GLOBS + _PROJECT_INPUT_GLOBS["module-graph"],
        #: `yaml` is added for the XcodeGen `project.yml` manifest this
        #: concern's probe now consumes (ADR-0130 clause 13); PyYAML is
        #: already pinned in every pack's PEP 723 block via the universal
        #: `decision-index` concern.
        parser=SWIFT_PARSER_MODULES + ("yaml",),
    ),
}


def probes() -> dict[str, list[Probe]]:
    """The Swift pack's per-concern probe registry: one `parser`-kind probe
    per concern, each always attempting extraction (the concern's own
    `no_entities`/`parse_failed` verdict logic self-degrades to the stub —
    there is no cheaper detector than the parse itself)."""
    return {
        "data-model": [Probe(_always_swift, extract_data_model, kind="parser")],
        "api-surface": [Probe(_always_swift, extract_api_surface, kind="parser")],
        "module-graph": [Probe(_always_swift, extract_module_graph, kind="parser")],
    }


def _always_swift(_root: Path) -> bool:
    return True


def _rel_str(root: Path, p: Path) -> str:
    """The repo-relative POSIX path of a path the walk yielded, computed
    lexically. `core._rel` resolves symlinks, which would name a refused
    symlink by its target rather than by the path the walk found.

    Every caller passes a `Path` the walk joined onto `root`, whose string
    starts with `root`'s string and a separator; the rest is sliced off in
    time linear in the path.
    `Path.relative_to` builds every parent of `p` as a `Path` to find `root`
    among them, which costs time quadratic in the path's depth, per file.
    Any other path falls back to `relative_to`, which gives the same answer
    or raises the same error."""
    p_str, prefix = str(p), str(root) + os.sep
    if p_str.startswith(prefix):
        return p_str[len(prefix):].replace(os.sep, "/")
    return Path(p).relative_to(Path(root)).as_posix()


def _walk_swift_files(root: Path):
    """Every `.swift` file under `root`, sorted by codepoint per directory,
    pruning core `_SKIP_DIRS`, dot-directories, and every `DETECT_EXCLUDE`
    entry (names and suffixes) — via the shared `_swift_walk`. Yields `Path`
    objects, not strings; the caller renders the repo-relative form via
    `_rel_str` where it needs one."""
    for dirp, _rel_dir, _normal, _excluded, filenames in _swift_walk(root):
        for f in filenames:
            if f.endswith(".swift"):
                yield dirp / f


class ProjectReads(NamedTuple):
    """The project-input reads and bundle census `_scan` hands to
    `_project_facts` (ADR-0130 clause 1). Reading is bounded and hashed
    here; `swift_xcode.read_projects` INTERPRETS these bytes into targets,
    memberships and dependency edges, so this type carries raw outcomes
    only.

    `files` maps a consumed project input's repo-relative path to its raw
    bytes (already hashed into the concern's `sources` map by `_scan`).

    `refusals` maps a project input's repo-relative path -- one the walk
    reached and this concern's globs admit -- to the `_safe_read_bytes`
    refusal reason: `not-regular`, `read-failed`, `oversize` or `escape`
    (ADR-0130 clause 14's `project-unreadable` kinds plus the shared
    `oversize` class). `_project_facts` folds each reason into its residual
    line: `swift_xcode.read_projects` for a `project.pbxproj`, and
    `_xcconfig_residuals` and `_workspace_facts_list` for the other inputs.

    `bundles` lists every `*.xcodeproj` the Swift walk reached, sorted:
    `(container_rel, pbxproj_present, pbxproj_regular)`. A bundle whose
    `project.pbxproj` is absent, or present but not a regular file, still
    appears here -- ADR-0130 clause 1's `missing-input` / `project-
    unreadable` distinction reads this tuple, never re-walks the tree.

    `residuals` carries this concern's project-input `scan-cap` lines (the
    aggregate cap was hit on a project candidate, not a Swift one) as
    `(class, path, ranges, detail)` -- kept apart from `_scan`'s general
    `residuals` return, because `_project_facts` renders them and no line
    may render twice.
    """
    files: dict = {}
    refusals: dict = {}
    bundles: tuple = ()
    residuals: tuple = ()


def _project_candidates(root: Path):
    """One `_swift_walk` pass collecting every project-input candidate this
    pack's reader might consume, plus the `*.xcodeproj` bundle census
    (ADR-0130 clause 1). Returns `(pbxproj_paths, workspace_paths,
    xcconfig_paths, project_yml_paths, bundles)`; `bundles` is sorted
    `(container_rel, pbxproj_present, pbxproj_regular)`.

    `present` is true whenever SOME filesystem entry sits at the
    `project.pbxproj` path (`os.path.lexists`, which does not follow the
    final symlink) -- a bundle with no entry there renders `missing-input`
    downstream. `regular` is true only for an entry that is a regular file
    and not itself a symlink. `pbxproj_paths` (and `workspace_paths`) offer
    every PRESENT candidate to `_scan`, regular or not: `_safe_read_bytes`'s
    own `O_NOFOLLOW` contained-read check is what determines the precise
    refusal kind (`not-regular` for a symlink, FIFO or device) -- this walk
    only decides whether a read is attempted, never why one was refused.
    """
    pbxproj: list[Path] = []
    workspace: list[Path] = []
    xcconfig: list[Path] = []
    project_yml: list[Path] = []
    bundles: list[tuple[str, bool, bool]] = []
    for dirp, rel_dir, _normal, excluded, filenames in _swift_walk(root):
        for d in excluded:
            if d.endswith(".xcodeproj") and not (dirp / d).is_symlink():
                bundle_rel = f"{rel_dir}/{d}" if rel_dir else d
                cand = dirp / d / "project.pbxproj"
                present = os.path.lexists(cand)
                regular = cand.is_file() and not cand.is_symlink()
                bundles.append((bundle_rel, present, regular))
                if present:
                    pbxproj.append(cand)
            elif d.endswith(".xcworkspace") and not (dirp / d).is_symlink():
                cand = dirp / d / "contents.xcworkspacedata"
                if os.path.lexists(cand):
                    workspace.append(cand)
        for f in filenames:
            if f.endswith(".xcconfig"):
                xcconfig.append(dirp / f)
            elif f == "project.yml":
                project_yml.append(dirp / f)
    return tuple(pbxproj), tuple(workspace), tuple(xcconfig), tuple(project_yml), tuple(sorted(bundles))


def _scan(root: Path, concern: str, with_manifests: bool = True):
    """The bounded read for one concern (ADR-0129 clauses 2 and 4; ADR-0130
    clauses 1-2 for the project-input half).

    Reads every `.swift` file the walk reaches, `Package.swift` included
    when `with_manifests` is set, PLUS every project input this concern's
    `INPUT_CLASSES` glob set names (`_PROJECT_INPUT_GLOBS[concern]`) --
    `project.pbxproj` for `api-surface`; that plus a standalone workspace's
    `contents.xcworkspacedata`, every `*.xcconfig` and `project.yml` for
    `module-graph`; nothing for `data-model`. Swift and project candidates
    are sorted into ONE list by repo-relative path and scanned under ONE
    budget: the core's aggregate caps (`_MAX_SCAN_FILES`, `_MAX_SCAN_BYTES`)
    bound the combined read count and byte total, so a project input counts
    against the same cap a Swift file does, and a cap breach on a project
    input renders `scan-cap` exactly as a Swift one does. `project.pbxproj`
    reads within the dedicated 16 MiB `_MAX_PBXPROJ_BYTES` bound (ADR-0130
    clause 2); every other project input keeps the core's 2 MB bound.

    Returns `(reads, project_reads, sources, residuals)`. `reads` holds
    `(path, bytes)` for every Swift/manifest file read. `project_reads` is
    a `ProjectReads` (see its
    docstring) -- empty in every field when this concern names no project
    glob. `sources` maps every read file's repo-relative path (Swift or
    project) to its SHA-256. `residuals` holds `(class, path, ranges,
    detail)` entries for the Swift/manifest half and the shared `scan-cap`
    class; a project input's `oversize`/`not-regular`/`read-failed`
    refusal is recorded on `project_reads.refusals` instead, because
    ADR-0130 clause 14 assigns it the `project-unreadable`/`oversize`
    classes `_project_facts` renders through `ProjectFacts.residuals`, not
    this function's generic vocabulary.

    The outcome splits at the read (ADR-0129 clause 2). A successful read is
    hashed. An over-bound Swift/manifest file renders `oversize` and is not
    hashed. Any other refusal of a Swift/manifest file renders a
    location-less `parse-error` and is not hashed. A project input follows
    the same split, but its refusal reason lands on `project_reads`
    (never on the generic `residuals`), so `_project_facts` is the one
    place that line renders.
    """
    swift_candidates = [p for p in _walk_swift_files(root)
                        if with_manifests or p.name != "Package.swift"]
    project_globs = _PROJECT_INPUT_GLOBS.get(concern, ())
    pbx, ws, cfg, yml, bundles = (
        _project_candidates(root) if project_globs else ((), (), (), (), ())
    )
    project_candidates: list[Path] = []
    if any(g.endswith("project.pbxproj") for g in project_globs):
        project_candidates.extend(pbx)
    if any(g.endswith("contents.xcworkspacedata") for g in project_globs):
        project_candidates.extend(ws)
    if any(g.endswith(".xcconfig") for g in project_globs):
        project_candidates.extend(cfg)
    if any(g.endswith("project.yml") for g in project_globs):
        project_candidates.extend(yml)
    project_paths = set(project_candidates)

    candidates = sorted(swift_candidates + project_candidates, key=lambda p: _rel_str(root, p))
    reads: list = []
    sources: dict[str, str] = {}
    residuals: list = []
    project_files: dict[str, bytes] = {}
    project_refusals: dict[str, str] = {}
    project_residuals: list = []
    total_bytes = 0
    total = len(candidates)
    for n, p in enumerate(candidates):
        rel = _rel_str(root, p)
        is_project = p in project_paths
        if n >= _MAX_SCAN_FILES or total_bytes >= _MAX_SCAN_BYTES:
            detail = (f"the {concern} scan stopped at the aggregate file or byte cap; "
                      f"{total - n} file(s) from here on are not read")
            if is_project:
                project_residuals.append(("scan-cap", rel, None, detail))
            else:
                residuals.append(("scan-cap", rel, None, detail))
            break
        oversize: list = []
        if is_project:
            refusal: list = []
            if p.name == "project.pbxproj":
                raw = _safe_read_bytes(root, p, oversize, max_bytes=_MAX_PBXPROJ_BYTES, refusal=refusal)
            else:
                raw = _safe_read_bytes(root, p, oversize, refusal=refusal)
        else:
            raw = _safe_read_bytes(root, p, oversize)
        if raw is not None:
            sources[rel] = _sha256_hex(raw)
            total_bytes += len(raw)
            if is_project:
                project_files[rel] = raw
            else:
                reads.append((p, raw))
        elif is_project:
            reason = refusal[0] if refusal else "read-failed"
            project_refusals[rel] = reason
        elif oversize:
            residuals.append(("oversize", rel, None,
                              "location none; the file exceeds the 2 MB per-file bound and is not read"))
        else:
            residuals.append(("parse-error", rel, None, "location none; effect step read failed"))
    project_reads = ProjectReads(files=project_files, refusals=project_refusals,
                                  bundles=bundles, residuals=tuple(project_residuals))
    return reads, project_reads, sources, residuals


# ═══════════════════ the residual vocabulary and escapers ════════════════════

#: The closed ADR-0129 clause 7 residual-class tuple, extended by ADR-0130
#: clause 14. `conditional-setting`, `missing-input`, `project-unreadable`,
#: `unsupported-project-form` and `xcconfig-include` are emitted by
#: `swift_xcode.read_projects`, `swift_xcinputs.scan_xcconfig`/
#: `read_workspace`, `swift_generators.check_tuist`/`check_xcodegen` and
#: this module's `_xcconfig_residuals`/`_workspace_facts_list` refusal
#: lines, all folded into `_project_facts`'s returned `ProjectFacts.
#: residuals`; the three concern extractors never emit one directly. They are named here so the closed set is stated once, in the
#: order the ADR states it, rather than assembled from whichever classes are
#: emitted at any one point in the cycle.
_RESIDUAL_CLASSES: tuple[str, ...] = (
    "parse-error",
    "declaration-recovered",
    "declaration-dropped",
    "conditional-declaration",
    "macro-not-expanded",
    "non-literal-manifest",
    "unresolved-reference",
    "path-escape",
    "oversize",
    "scan-cap",
    "conditional-setting",
    "missing-input",
    "project-unreadable",
    "unsupported-project-form",
    "xcconfig-include",
)


def _residual_line(klass: str, path: str, ranges, detail: str) -> str:
    """Render one `## Residuals` bullet in the residual grammar:
    ``- `<class>` `<path>` lines <a-b>[, <a-b>]* — <detail>``.

    `klass` MUST be a member of `_RESIDUAL_CLASSES` — this function refuses
    any other value. `ranges` is `None` (renders `lines —`, the file
    was not read/no location applies), a single `(a, b)` pair, or a list of
    `(a, b)` pairs (rendered ascending, merged only when identical — callers
    pass them pre-sorted/pre-merged; this function does not re-sort, so its
    OWN callers sort the bullets).
    """
    if klass not in _RESIDUAL_CLASSES:
        raise ValueError(f"residual class outside the closed ADR-0129 set: {klass!r}")
    # Each name inside `detail` was already passed through `_cell` where the
    # detail was composed; the templates' own backticks are deliberate.
    detail = str(detail).replace("\r", " ").replace("\n", " ")
    if ranges is None:
        lines_part = "lines —"
    elif isinstance(ranges, tuple):
        lines_part = f"lines {ranges[0]}-{ranges[1]}"
    else:
        lines_part = "lines " + ", ".join(f"{a}-{b}" for a, b in ranges)
    return f"- `{_cell(klass)}` `{_cell(path)}` {lines_part} — {detail}"


def _render_residuals(bullets: list[str]) -> list[str]:
    """`## Residuals` renders only when at least one bullet exists."""
    if not bullets:
        return []
    return ["## Residuals", ""] + bullets + [""]


# ═══════════════════════ the per-concern output bound ════════════════════════

#: The bytes one concern file's content lines may carry: every table row,
#: every Mermaid edge line and every residual bullet, each counted as its
#: UTF-8 bytes plus one for its newline. It is the core's 2 MiB per-file read
#: bound less 256 KiB, because `derive --dry-run` reads each spine file back
#: through that bound and a larger file could never compare clean. The 256
#: KiB covers the lines the bound does not count: the title, the summary
#: line, section headings, table header rows, the Mermaid fence and graph
#: declaration, the `## Residuals` heading and the one `scan-cap` line a
#: tripped bound renders. The largest file in the pinned corpus (NetNewsWire's
#: data model, 755,180 bytes) sits 2.43 times below it.
#:
#: Past the bound a concern renders its content lines in file order up to
#: the first line that does not fit. A table or graph section after the trip
#: renders nothing, not even a heading. `## Residuals` still renders, with
#: one `scan-cap` line as its last residual (ADR-0129 clause 7's scan-limit
#: class). The summary line counts only the rows that render.
_MAX_CONCERN_BYTES = _MAX_FILE_BYTES - 256 * 1024

#: The one line a tripped output bound renders, and its twin for a concern
#: whose declared inputs all failed to decode (`_output_cap_line`). The twin
#: carries the undecoded-input count, so `concern_decode_verdict` returns the
#: same verdict whether or not the bound dropped the `parse-error` lines it
#: reads. Neither counts the rows that did not render.
_OUTPUT_CAP_TEMPLATE = ("the {concern} rows pass the per-concern output bound of {limit} bytes; "
                        "later rows do not render")
_OUTPUT_CAP_UNDECODED_TEMPLATE = (_OUTPUT_CAP_TEMPLATE
                                  + "; {undecoded} declared input(s) present and none decoded")
_OUTPUT_CAP_UNDECODED_RE = re.compile(
    r"the (?:data-model|api-surface|module-graph) rows pass the per-concern output bound of "
    r"\d+ bytes; later rows do not render; (\d+) declared input\(s\) present and none decoded")


class _OutputBudget:
    """One concern file's output bound (`_MAX_CONCERN_BYTES`), created once
    per extractor call and never inside the cached per-file parse.

    `take` charges one content line before it is kept. The first line that
    does not fit trips the bound, and every later `take` refuses, so what
    renders is always a prefix in file order."""

    def __init__(self, concern: str):
        self.concern = concern
        self.limit = _MAX_CONCERN_BYTES
        self.spent = 0
        self.tripped = False

    def take(self, line: str) -> bool:
        if self.tripped:
            return False
        # `surrogatepass` keeps the meter total: a lone surrogate that
        # reached a line is counted, never raised on. The decoders refuse
        # surrogates; this is defence in depth, and over-counting is safe
        # for a bound.
        cost = (len(line) if line.isascii()
                else len(line.encode("utf-8", "surrogatepass"))) + 1
        if self.spent + cost > self.limit:
            self.tripped = True
            return False
        self.spent += cost
        return True


def _decode_counts(keys, sources: dict) -> tuple[int, bool]:
    """`(undecoded inputs, no input decoded)` over the full, deduplicated
    residual set, by the rule `concern_decode_verdict` applies to the
    rendered lines (ADR-0129 clause 7)."""
    undecoded = set()
    parse_failed = set()
    for path, klass, _ranges, detail in keys:
        flat = str(detail).replace("\r", " ").replace("\n", " ")
        if klass == "oversize" or (klass == "parse-error" and flat.endswith(_UNDECODED_DETAILS)):
            undecoded.add(_cell(path))
        if klass == "parse-error" and flat.endswith("effect step parse failed"):
            parse_failed.add(_cell(path))
    decoded = {_cell(p) for p in sources} - parse_failed
    return len(undecoded), bool(undecoded) and not decoded


def _output_cap_line(budget: "_OutputBudget", keys, sources: dict) -> str:
    """The tripped bound's one `scan-cap` line, path `.`, no line range."""
    undecoded, none_decoded = _decode_counts(keys, sources)
    if none_decoded:
        detail = _OUTPUT_CAP_UNDECODED_TEMPLATE.format(
            concern=budget.concern, limit=budget.limit, undecoded=undecoded)
    else:
        detail = _OUTPUT_CAP_TEMPLATE.format(concern=budget.concern, limit=budget.limit)
    return _residual_line("scan-cap", ".", None, detail)


def _swift_label(s) -> str:
    """The Swift pack's own Mermaid label escaper, NOT
    `core._mermaid`: this flattens CR itself (`core._mermaid`'s CR gap is a
    recorded core follow-on, not fixed here) and additionally neutralizes a
    backtick and a backslash, both of which a hostile Swift identifier can
    carry (Swift allows almost any Unicode scalar plus backtick-escaped
    keywords as an identifier)."""
    return (
        str(s)
        .replace("\\", "#92;")
        .replace('"', "#quot;")
        .replace("`", "#96;")
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _strip_location(loc: str) -> str:
    """Strip userinfo, query and fragment from a location string using str
    methods only — never a regex over untrusted content. Handles every
    syntax the render contract names: `scheme://user:pass@host/path`,
    `user@host:path` (scp-like), and a bare `host/path`.

    A `://` separates a scheme only when the text before it is an RFC 3986
    scheme (`_is_uri_scheme`); otherwise the whole string takes the
    scheme-less branch, so `deploy:SECRET@host:org/a.git?ref=https://x` cannot
    turn its credential into a "scheme".

    Userinfo is "anything before an `@` that precedes the host". A credential
    can itself hold `/`, `?` or `#`, and a query or fragment can itself hold
    `@`. So after the scheme, everything up to the LAST `@` is removed, and
    everything from the FIRST `?` or `#` is removed. When that `@` falls after
    that `?` or `#`, the two cuts overlap and nothing after the scheme
    renders: which side is the credential cannot be decided, so neither
    side renders. The cuts can lose part of a location; they do not render
    userinfo, a query or a fragment.
    """
    s = str(loc)
    head, rest = "", s
    sep = s.find("://")
    if sep != -1 and _is_uri_scheme(s[:sep]):
        head, rest = s[: sep + 3], s[sep + 3 :]
    ends = [i for i in (rest.find("?"), rest.find("#")) if i != -1]
    end = min(ends) if ends else len(rest)
    start = rest.rfind("@") + 1
    return head + (rest[start:end] if start <= end else "")


def _is_uri_scheme(prefix: str) -> bool:
    """True when `prefix` is an RFC 3986 scheme: `ALPHA *( ALPHA / DIGIT /
    "+" / "-" / "." )`, ASCII only."""
    return (prefix.isascii() and prefix[:1].isalpha()
            and all(c.isalnum() or c in "+-." for c in prefix))


# ═══════════ the declaration reader (ADR-0129 clauses 5 and 7) ═══════════════
# One explicit-stack walk per file (no Python recursion, so a deeply nested
# file cannot exhaust the interpreter stack). The walk descends into type,
# extension and protocol bodies and into ERROR nodes, so a declaration under an
# erroneous ancestor still renders. It never descends into a function, accessor
# or closure body: a declaration or freestanding macro there is local and
# renders nothing. Directives are siblings in the grammar's tree, and the walk
# visits nodes in document order, so one per-file directive stack gives every
# node its `#if` state.

#: Every declared model-type keyword the data-model renders (ADR-0129 clause 5).
_MODEL_KINDS = frozenset({"struct", "enum", "class", "actor"})

#: Access levels a `visibility_modifier` may name.
_ACCESS_LEVELS = ("private", "fileprivate", "internal", "package", "public", "open")

#: The access levels that make a declaration an interface (ADR-0129 clause 5).
_INTERFACE_ACCESS = frozenset({"public", "package", "open"})

#: The closed attached-macro table (ADR-0129 clause 7): attribute name → the
#: one concern its `macro-not-expanded` line renders in. The attachment site
#: never decides the concern.
_ATTACHED_MACROS = {"Observable": "data-model", "Test": "api-surface", "Suite": "api-surface"}

#: The declaration-scope freestanding macros the table names.
_FREESTANDING_MACROS = {"Preview": "api-surface"}

#: Nodes whose contents are local to a body; the walk never descends into them.
_BODY_TYPES = frozenset({"function_body", "computed_property", "lambda_literal", "statements",
                         "protocol_property_requirements"})

#: Container nodes whose direct children are members.
_MEMBER_CONTAINERS = frozenset({"class_body", "enum_class_body", "protocol_body"})

#: The deepest declaration nesting the walk renders. A row names its
#: type qualified by every enclosing type, extension or protocol, so rendered
#: text grows with the square of the nesting depth. A declaration nested
#: deeper than this renders nothing, and neither does anything inside it; the
#: first one renders one `scan-cap` line. The bound keeps a hostile file
#: from turning into an unbounded write or a `MemoryError` exit, which
#: ADR-0129 clause 2 forbids for any Swift content. The number matches the
#: 64-level nesting bound ADR-0130 clause 2 sets for project files. The
#: deepest real declaration in the pinned corpus nests 4 levels.
_MAX_DECLARATION_NESTING = 64

_NESTING_DETAIL = (f"declaration nesting passes {_MAX_DECLARATION_NESTING} levels; "
                   "declarations nested deeper do not render")

#: The deepest `#if` nesting the walk renders, the same 64 levels as
#: declaration nesting. Every occurrence records its whole `#if` stack, so
#: without a bound the work and memory grow with depth times declarations.
#: A declaration inside more than this many open `#if` blocks renders
#: nothing, and the first `#if` past the bound renders one `scan-cap` line.
#: The stack never grows past the bound.
_MAX_IF_NESTING = _MAX_DECLARATION_NESTING

_IF_NESTING_DETAIL = (f"`#if` nesting passes {_MAX_IF_NESTING} levels; "
                      "declarations nested deeper do not render")

#: The name text one file's rows may carry, in UTF-8 bytes: 8 times the
#: file's size in bytes plus 64 KiB. A long type name repeats in every member
#: row, so without a bound a 2 MB file can render gigabytes, against ADR-0129
#: clause 2. The largest ratio in the pinned corpus is 1.3. Past the bound
#: the walk stops, and one `scan-cap` line names the line where it stopped.
_NAME_BUDGET_FACTOR = 8
_NAME_BUDGET_FLOOR = 64 * 1024


def _utf8_len(text: str) -> int:
    """`text`'s length in UTF-8 bytes, the unit the per-file name bound
    compares against the file's size. An ASCII string's length is its byte
    count, so it is never encoded. `surrogatepass` keeps the count total: a
    lone surrogate is counted, never raised on."""
    return len(text) if text.isascii() else len(text.encode("utf-8", "surrogatepass"))

_BUDGET_DETAIL = (f"the declaration rows pass the per-file name bound of {_NAME_BUDGET_FACTOR} "
                  f"times the file's size plus {_NAME_BUDGET_FLOOR // 1024} KiB; "
                  "later declarations do not render")


#: The same per-file bound applied to the `parse-error` details: each repeats
#: the label of its nearest enclosing declaration, so a long name over many
#: stray errors otherwise renders gigabytes from a small file (ADR-0129
#: clause 2). The details count separately from the row names, so a file
#: that spent its name bound still reports its parse errors. Past the bound
#: the error lines stop, and one `scan-cap` line names the line of the first
#: error that did not render.
_ERROR_BUDGET_DETAIL = (f"the parse-error lines pass the per-file bound of {_NAME_BUDGET_FACTOR} "
                        f"times the file's size plus {_NAME_BUDGET_FLOOR // 1024} KiB; "
                        "later parse errors do not render")


class _NameBudgetSpent(Exception):
    """The walk passed the file's name bound at `line` (see `_NAME_BUDGET_FACTOR`)."""

    def __init__(self, line: int):
        super().__init__(line)
        self.line = line


#: Protocol-body children that are requirements.
_REQUIREMENT_TYPES = frozenset({"associatedtype_declaration", "protocol_property_declaration",
                                "protocol_function_declaration", "init_declaration",
                                "subscript_declaration"})


def _text(node, src: bytes) -> str:
    return _decode(src[node.start_byte : node.end_byte])


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def _access_word(modifier: str) -> str | None:
    """The access level a `visibility_modifier` sets, or None.

    `private(set)` and its siblings restrict only the setter, so they leave the
    declaration's access unchanged and return None.
    """
    text = _norm_ws(modifier)
    if "(" in text:
        return None
    return text if text in _ACCESS_LEVELS else None


@dataclass
class FileFacts:
    """Everything one Swift source file contributes, with file lines resolved.

    Every row carries `path`, and every location is a 1-based `(a, b)` pair:
    `a` is the declaration node's start row and `b` the row of its name token.
    A residual is a `(class, ranges, detail)` triple; its path is supplied at
    render time.
    """

    path: str
    types: list = field(default_factory=list)
    stored_properties: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    interfaces: list = field(default_factory=list)
    requirements: list = field(default_factory=list)
    mains: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    macros: dict = field(default_factory=dict)          # (concern, "@Name"|"#Name") -> [(a, b)]
    conditional_occurrences: list = field(default_factory=list)  # (concern, name, branch, block)
    block_spans: dict = field(default_factory=dict)     # block id -> [a, b]
    residuals: list = field(default_factory=list)       # (class, ranges | None, detail)
    #: The per-file bound on the name text the rows carry
    #: (`_NAME_BUDGET_FACTOR`), applied separately to the parse-error detail
    #: text (`_ERROR_BUDGET_DETAIL`). `_parse_file` always sets it; `charge`
    #: reads None as unbounded.
    name_budget: int | None = None
    #: The UTF-8 bytes of row name text charged so far (`_Walk.charge`).
    name_bytes: int = 0
    nesting_capped: bool = False
    if_nesting_capped: bool = False


class _Walk:
    """The per-file walk state: the directive stack, and the facts it fills."""

    def __init__(self, src: bytes, facts: FileFacts, row_offset: int = 0, block_base: int = 0):
        self.src = src
        self.facts = facts
        self.off = row_offset
        self.cond: list[list[int]] = []          # [block id, branch index]
        #: Open `#if` blocks past `_MAX_IF_NESTING`. They are counted, never
        #: pushed, and no declaration, import or macro inside them renders.
        #: A parse error inside them still renders its `parse-error` line.
        self.cond_overflow = 0
        self.next_block = block_base
        #: The `#if` state at each dropped declaration's ERROR the walk
        #: skipped, keyed by `_node_span_key`: the stack and the overflow
        #: count. Recovery and the detached walk start from it, so what they
        #: render under a top-level `#if` stays conditional (ADR-0129 clause 7).
        self.skipped_cond: dict = {}

    def cond_state(self) -> tuple:
        return ([list(b) for b in self.cond], self.cond_overflow)

    def set_cond_state(self, state) -> None:
        stack, overflow = state
        self.cond = [list(b) for b in stack]
        self.cond_overflow = overflow

    # ── positions ──
    def line(self, node) -> int:
        return node.start_point[0] + self.off + 1

    def end_line(self, node) -> int:
        return _end_display_line(node.end_point) + self.off

    def loc(self, decl, name_node) -> tuple[int, int]:
        return (self.line(decl), self.line(name_node) if name_node is not None else self.line(decl))

    # ── directives ──
    def directive(self, node) -> None:
        head = node.children[0].type if node.children else ""
        line = self.line(node)
        if head == "#if":
            if self.cond_overflow or len(self.cond) >= _MAX_IF_NESTING:
                self.cond_overflow += 1
                if not self.facts.if_nesting_capped:
                    self.facts.if_nesting_capped = True
                    self.facts.residuals.append(("scan-cap", [(line, line)], _IF_NESTING_DETAIL))
                return
            block = self.next_block
            self.next_block += 1
            self.cond.append([block, 0])
            self.facts.block_spans[block] = [line, line]
        elif head in ("#elseif", "#else") and self.cond_overflow:
            return
        elif head in ("#elseif", "#else") and self.cond:
            self.cond[-1][1] += 1
        elif head == "#endif" and self.cond_overflow:
            self.cond_overflow -= 1
        elif head == "#endif" and self.cond:
            block = self.cond.pop()[0]
            self.facts.block_spans[block][1] = line

    def conditional(self) -> bool:
        return bool(self.cond)

    def branch(self) -> tuple:
        return tuple(tuple(b) for b in self.cond)

    def note_conditional(self, concern: str, name: str) -> None:
        if self.cond:
            self.facts.conditional_occurrences.append(
                (concern, name, self.branch(), self.cond[-1][0]))

    def macro(self, concern: str, label: str, span: tuple[int, int]) -> None:
        self.facts.macros.setdefault((concern, label), []).append(span)

    # ── output bounds (see `_NAME_BUDGET_FACTOR`) ──
    def charge(self, line: int, *texts) -> None:
        """Count the name text a row is about to carry, in UTF-8 bytes,
        against the file's bound before the row is recorded. Past the bound,
        stop the walk."""
        budget = self.facts.name_budget
        if budget is None:
            return
        self.facts.name_bytes += sum(_utf8_len(t) for t in texts if t)
        if self.facts.name_bytes > budget:
            raise _NameBudgetSpent(line)

    def too_deep(self, node, depth: int) -> bool:
        """True when a declaration at `depth` enclosing declarations is past
        `_MAX_DECLARATION_NESTING`. The first one renders the `scan-cap` line."""
        if depth < _MAX_DECLARATION_NESTING:
            return False
        if not self.facts.nesting_capped:
            self.facts.nesting_capped = True
            line = self.line(node)
            self.facts.residuals.append(("scan-cap", [(line, line)], _NESTING_DETAIL))
        return True

    # ── the walk ──
    def run(self, root_node, skip: frozenset = frozenset()) -> None:
        # Each stack item: (node, owner, in_protocol, extension access,
        # nesting unconfirmed, enclosing declaration count)
        self._drain([(c, "", False, None, False, 0) for c in reversed(root_node.children)], skip)

    def run_detached(self, nodes) -> None:
        """Walk nodes whose enclosing declaration the grammar lost.

        They are the children of a dropped declaration's ERROR that recovery
        did not cover. Each renders unqualified with a `nesting unconfirmed`
        residual, never under an asserted parent (ADR-0129 clause 7).

        The grammar puts everything after the member that broke the parse into
        that member's `statements`, so one level of `statements` directly under
        the ERROR is unwrapped. Deeper bodies stay unwalked, as they always are.
        """
        items = []
        for c in nodes:
            items.extend(c.children if c.type == "statements" else [c])
        self._drain([(c, "", False, None, True, 0) for c in reversed(items)], frozenset())

    def _drain(self, stack: list, skip: frozenset) -> None:
        while stack:
            node, owner, in_protocol, ext_access, unconfirmed, depth = stack.pop()
            t = node.type
            if t == "directive":
                self.directive(node)
                continue
            if t == "ERROR":
                key = _node_span_key(node)
                if key in skip:
                    self.skipped_cond[key] = self.cond_state()
                    continue
                stack.extend((c, owner, in_protocol, ext_access, unconfirmed, depth)
                             for c in reversed(node.children))
                continue
            if t in _BODY_TYPES or self.cond_overflow:
                continue
            if t == "import_declaration" and not owner:
                self.import_row(node)
            elif t == "class_declaration":
                if self.too_deep(node, depth):
                    continue
                pushed = self.type_or_extension(node, owner, ext_access, unconfirmed, depth + 1)
                if pushed is not None:
                    stack.extend(reversed(pushed))
            elif t == "protocol_declaration":
                if self.too_deep(node, depth):
                    continue
                pushed = self.protocol(node, owner, ext_access, unconfirmed, depth + 1)
                if pushed is not None:
                    stack.extend(reversed(pushed))
            elif in_protocol and t in _REQUIREMENT_TYPES:
                self.requirement(node, owner)
            elif t == "property_declaration" and not in_protocol:
                self.property(node, owner, ext_access, unconfirmed)
            elif t in ("function_declaration", "init_declaration", "subscript_declaration"):
                self.callable(node, owner, ext_access, unconfirmed)
            elif t == "typealias_declaration" and not in_protocol:
                self.typealias(node, owner, ext_access, unconfirmed)
            elif t == "macro_invocation":
                self.freestanding_macro(node)

    def _body_items(self, body, owner, in_protocol, ext_access, depth):
        """The body's children as walk items. A body the grammar closed with
        a MISSING `}` marks every item's nesting unconfirmed, and the two
        declaration kinds then differ on purpose. A member (property,
        callable, typealias) keeps the grammar's owner, while a nested type
        renders unqualified with a `nesting unconfirmed` residual
        (`qualify`). The unqualified type is the conservative side ADR-0129
        clause 7 names ("never an asserted parent"). The member keeps its
        owner because the `ErrorLogDatabase` postcondition requires the
        recovered actor's stored property, initializer and method to render
        under the actor."""
        if body is None:
            return []
        children = list(body.children)
        unconfirmed = bool(children) and children[-1].type == "}" and children[-1].is_missing
        return [(c, owner, in_protocol, ext_access, unconfirmed, depth) for c in children]

    def modifiers(self, node, ext_access):
        """`(access, attribute nodes)` for a declaration."""
        access = None
        attributes = []
        mods = _find_child(node, "modifiers")
        if mods is not None:
            for c in mods.children:
                if c.type == "visibility_modifier":
                    word = _access_word(_text(c, self.src))
                    if word is not None:
                        access = word
                elif c.type == "attribute":
                    attributes.append(c)
        if access is None:
            access = ext_access or "internal"
        return access, attributes

    def attribute_names(self, attributes) -> list[str]:
        names = []
        for a in attributes:
            ut = _find_child(a, "user_type")
            if ut is not None:
                names.append("@" + _norm_ws(_text(ut, self.src)))
        return names

    def attached_macros(self, attributes, name_line: int) -> None:
        """Record each table-named attribute. An occurrence runs from the
        attribute's first line to the line of the declaration's name, so a
        multi-line attribute argument list stays inside the range."""
        for a in attributes:
            ut = _find_child(a, "user_type")
            if ut is None:
                continue
            name = _norm_ws(_text(ut, self.src))
            concern = _ATTACHED_MACROS.get(name)
            if concern is not None:
                self.macro(concern, f"@{name}", (self.line(a), max(self.line(a), name_line)))

    def import_row(self, node) -> None:
        ident = _find_child(node, "identifier")
        if ident is None:
            return
        first = _find_child(ident, "simple_identifier")
        module = _text(first if first is not None else ident, self.src)
        kinds = []
        mods = _find_child(node, "modifiers")
        if mods is not None:
            for c in mods.children:
                if c.type == "attribute":
                    ut = _find_child(c, "user_type")
                    if ut is not None:
                        kinds.append("@" + _norm_ws(_text(ut, self.src)))
                elif c.type == "visibility_modifier":
                    kinds.append(_text(c, self.src).strip())
        self.facts.imports.append({
            "path": self.facts.path, "module": module,
            "kind": " ".join(kinds) if kinds else "plain", "line": self.line(node),
        })

    def qualify(self, owner: str, name: str, unconfirmed: bool, decl, name_node) -> str:
        if unconfirmed:
            # The enclosing body ends in a MISSING `}`, so the parent this
            # declaration appears under is unconfirmed; it renders unqualified.
            self.facts.residuals.append(("parse-error", [self.loc(decl, name_node)],
                                         "location member; effect nesting unconfirmed"))
            return name
        return f"{owner}.{name}" if owner else name

    def type_or_extension(self, node, owner, ext_access, unconfirmed, depth):
        kind_node = node.child_by_field_name("declaration_kind")
        kind = kind_node.type if kind_node is not None else ""
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None:
            return None
        access, attributes = self.modifiers(node, ext_access)
        inheritance = _find_children(node, "inheritance_specifier")
        if kind == "extension":
            extended = _norm_ws(_text(name_node, self.src))
            for spec in inheritance:
                to = _norm_ws(_text(spec, self.src))
                self.charge(self.line(node), extended, to)
                self.facts.relationships.append({
                    "path": self.facts.path, "from": extended, "relation": "conforms (extension)",
                    "to": to, "loc": self.loc(node, name_node),
                    "first_class_entry": False,
                })
            explicit = self._explicit_access(node)
            return self._body_items(body, extended, False, explicit, depth)
        if kind not in _MODEL_KINDS:
            return None
        name = self.qualify(owner, _text(name_node, self.src), unconfirmed, node, name_node)
        loc = self.loc(node, name_node)
        cond = self.conditional()
        self.charge(loc[0], name)
        self.facts.types.append({"path": self.facts.path, "name": name, "kind": kind,
                                 "access": access, "loc": loc, "conditional": cond})
        self.note_conditional("data-model", name)
        attr_names = self.attribute_names(attributes)
        self.attached_macros(attributes, loc[1])
        if access in _INTERFACE_ACCESS:
            self.interface(name, kind, access, loc, attr_names, cond)
        if "@main" in attr_names:
            self.facts.mains.append({"path": self.facts.path, "type": name, "kind": kind, "loc": loc})
        for idx, spec in enumerate(inheritance):
            to = _norm_ws(_text(spec, self.src))
            self.charge(loc[0], name, to)
            self.facts.relationships.append({
                "path": self.facts.path, "from": name, "relation": "conforms",
                "to": to, "loc": loc,
                "first_class_entry": kind == "class" and idx == 0,
            })
        return self._body_items(body, name, False, None, depth)

    def _explicit_access(self, node):
        mods = _find_child(node, "modifiers")
        if mods is None:
            return None
        for c in mods.children:
            if c.type == "visibility_modifier":
                word = _access_word(_text(c, self.src))
                if word is not None:
                    return word
        return None

    def detached(self, owner: str, unconfirmed: bool, decl, name_node) -> None:
        """A member with no owner under a lost declaration names its nesting as
        unconfirmed. A member inside a type body keeps its owner: that body's
        own brace is what bounds it."""
        if unconfirmed and not owner:
            self.facts.residuals.append(("parse-error", [self.loc(decl, name_node)],
                                         "location member; effect nesting unconfirmed"))

    def interface(self, name, kind, access, loc, attr_names, cond) -> None:
        # Every interface row repeats its attribute text, so it counts too.
        self.charge(loc[0], name, *attr_names)
        self.facts.interfaces.append({
            "path": self.facts.path, "name": name, "kind": kind, "access": access,
            "loc": loc, "attributes": attr_names, "conditional": cond,
        })
        self.note_conditional("api-surface", name)

    def protocol(self, node, owner, ext_access, unconfirmed, depth):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        access, attributes = self.modifiers(node, ext_access)
        name = self.qualify(owner, _text(name_node, self.src), unconfirmed, node, name_node)
        loc = self.loc(node, name_node)
        self.attached_macros(attributes, loc[1])
        # Every protocol renders as an interface, whatever its access level.
        self.interface(name, "protocol", access, loc, self.attribute_names(attributes),
                       self.conditional())
        return self._body_items(node.child_by_field_name("body"), name, True, None, depth)

    def selector(self, base: str, node) -> str:
        labels = []
        for p in _find_children(node, "parameter"):
            ext = p.child_by_field_name("external_name")
            label = ext if ext is not None else p.child_by_field_name("name")
            labels.append(_text(label, self.src) if label is not None else "_")
        return base + "(" + "".join(f"{lbl}:" for lbl in labels) + ")"

    def callable_name(self, node):
        """`(key, kind, name node)` for a function, initialiser or subscript."""
        t = node.type
        if t in ("init_declaration",):
            name_node = node.child_by_field_name("name") or _find_child(node, "init")
            return self.selector("init", node), "init", name_node
        if t in ("subscript_declaration",):
            name_node = _find_child(node, "subscript")
            return self.selector("subscript", node), "subscript", name_node
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None, None, None
        return self.selector(_text(name_node, self.src), node), "func", name_node

    def callable(self, node, owner, ext_access, unconfirmed=False) -> None:
        key, kind, name_node = self.callable_name(node)
        if key is None:
            return
        access, attributes = self.modifiers(node, ext_access)
        loc = self.loc(node, name_node)
        self.attached_macros(attributes, loc[1])
        if access in _INTERFACE_ACCESS:
            self.detached(owner, unconfirmed, node, name_node)
            self.interface(f"{owner}.{key}" if owner else key, kind, access, loc,
                           self.attribute_names(attributes), self.conditional())

    def typealias(self, node, owner, ext_access, unconfirmed=False) -> None:
        name_node = node.child_by_field_name("name") or _find_child(node, "type_identifier")
        if name_node is None:
            return
        access, attributes = self.modifiers(node, ext_access)
        if access in _INTERFACE_ACCESS:
            self.detached(owner, unconfirmed, node, name_node)
            name = _text(name_node, self.src)
            self.interface(f"{owner}.{name}" if owner else name, "typealias", access,
                           self.loc(node, name_node), self.attribute_names(attributes),
                           self.conditional())

    def bindings(self, node):
        """`(name node, type annotation node | None, computed)` per binding."""
        out = []
        current = None
        for c in node.children:
            if c.type == "pattern":
                if current is not None:
                    out.append(current)
                idents = [n for n in _preorder(c) if n.type == "simple_identifier"]
                current = [idents[-1] if len(idents) == 1 else None, None, False]
            elif current is not None and c.type == "type_annotation":
                current[1] = c
            elif current is not None and c.type == "computed_property":
                current[2] = True
        if current is not None:
            out.append(current)
        return [tuple(b) for b in out if b[0] is not None]

    def property(self, node, owner, ext_access, unconfirmed=False) -> None:
        access, attributes = self.modifiers(node, ext_access)
        mutability = _find_child(node, "value_binding_pattern")
        word = _text(mutability, self.src).strip() if mutability is not None else "var"
        attr_names = self.attribute_names(attributes)
        bindings = self.bindings(node)
        if bindings:
            self.attached_macros(attributes, self.line(bindings[0][0]))
        for name_node, annotation, computed in bindings:
            name = _text(name_node, self.src)
            loc = self.loc(node, name_node)
            if owner and not computed:
                declared = None
                type_names: list[str] = []
                if annotation is not None:
                    raw = _text(annotation, self.src)
                    declared = _norm_ws(raw[1:] if raw.startswith(":") else raw)
                    type_names = [_text(n, self.src) for n in _preorder(annotation)
                                  if n.type == "type_identifier"]
                # Each named type becomes a `stores` row carrying the owner,
                # so each counts with the owner beside it.
                self.charge(loc[0], owner, name, declared,
                            *(owner + t for t in type_names))
                self.facts.stored_properties.append({
                    "path": self.facts.path, "owner": owner, "name": name, "type": declared,
                    "access": access, "loc": loc, "type_names": type_names,
                })
            if access in _INTERFACE_ACCESS:
                self.detached(owner, unconfirmed, node, name_node)
                self.interface(f"{owner}.{name}" if owner else name, word, access, loc,
                               attr_names, self.conditional())

    def requirement(self, node, protocol) -> None:
        t = node.type
        if t == "associatedtype_declaration":
            name_node = node.child_by_field_name("name") or _find_child(node, "type_identifier")
            if name_node is not None:
                self._req(protocol, _text(name_node, self.src), "associatedtype", node, name_node)
        elif t == "protocol_property_declaration":
            pattern = node.child_by_field_name("name") or _find_child(node, "pattern")
            idents = [n for n in _preorder(pattern) if n.type == "simple_identifier"] if pattern else []
            if idents:
                word = "var"
                vb = _find_child(pattern, "value_binding_pattern")
                if vb is not None:
                    word = _text(vb, self.src).strip()
                self._req(protocol, _text(idents[-1], self.src), word, node, idents[-1])
        elif t == "protocol_function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                self._req(protocol, self.selector(_text(name_node, self.src), node), "func",
                          node, name_node)
        else:
            key, kind, name_node = self.callable_name(node)
            if key is not None:
                self._req(protocol, key, kind, node, name_node)

    def _req(self, protocol, name, kind, node, name_node) -> None:
        self.charge(self.line(node), protocol, name)
        self.facts.requirements.append({"path": self.facts.path, "protocol": protocol, "name": name,
                                        "kind": kind, "loc": self.loc(node, name_node)})

    def freestanding_macro(self, node) -> None:
        name_node = _find_child(node, "simple_identifier")
        if name_node is None:
            return
        concern = _FREESTANDING_MACROS.get(_text(name_node, self.src))
        if concern is not None:
            self.macro(concern, "#" + _text(name_node, self.src),
                       (self.line(node), self.end_line(node)))


def _preorder(node):
    """Every node under `node`, pre-order, with an explicit stack."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _find_child(node, type_name: str):
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def _find_children(node, type_name: str):
    return [c for c in node.children if c.type == type_name]


def _node_span_key(node) -> tuple:
    """A structural identity for a node. The binding builds a fresh wrapper per
    access, so `id()` differs between two reads of one node; this does not."""
    return (node.start_byte, node.end_byte, node.type)


# ═══════════════════════ per-file error classification ════════════════════════

#: How many of a node's nearest ancestors the error walk carries with it: the
#: benign `MISSING "!"` shape below is read four levels up, and no deeper.
_BENIGN_ANCESTORS = 4


def _is_benign_missing_bang(node, ancestors: tuple) -> bool:
    """ADR-0129 clause 7's benign shape: a MISSING `!` the grammar inserts
    directly after an empty-argument attribute on a stored property.

    In the pinned grammar the shape is `property_declaration > modifiers >
    attribute(@, user_type, "(", bang(MISSING "!"), ")")`: the parentheses hold
    only the inserted node, so the attribute has no argument at all.

    `ancestors` holds the node's nearest ancestors, parent first, as the error
    walk (`_stray_errors`) visited them. This never calls `Node.parent`: the
    binding answers that call by searching down from the root, so it costs
    time linear in the node's depth, and past about 70,000 levels the native
    search overflows the C stack (ADR-0129 clause 2).
    """
    if not node.is_missing or node.type != "!":
        return False
    bang, attribute, modifiers, declaration = (tuple(ancestors) + (None,) * 4)[:4]
    if bang is None or bang.type != "bang" or bang.named_child_count != 0:
        return False
    if attribute is None or attribute.type != "attribute":
        return False
    if [c.type for c in attribute.children] != ["@", "user_type", "(", "bang", ")"]:
        return False
    return (modifiers is not None and modifiers.type == "modifiers"
            and declaration is not None and declaration.type == "property_declaration")


def _declaration_label(node, src: bytes) -> str | None:
    """`<kind> <Name>` for a declaration node, or None when it names nothing."""
    t = node.type
    if t == "class_declaration":
        kind_node = node.child_by_field_name("declaration_kind")
        name_node = node.child_by_field_name("name")
        kind = kind_node.type if kind_node is not None else "type"
    elif t == "protocol_declaration":
        kind, name_node = "protocol", node.child_by_field_name("name")
    elif t in ("function_declaration", "protocol_function_declaration"):
        kind, name_node = "func", node.child_by_field_name("name")
    elif t == "init_declaration":
        return "init"
    elif t == "subscript_declaration":
        return "subscript"
    elif t == "property_declaration":
        idents = [n for n in _preorder(node.child_by_field_name("name") or node)
                  if n.type == "simple_identifier"]
        kind, name_node = "var", idents[0] if idents else None
    else:
        return None
    if name_node is None:
        return None
    return f"{kind} {_cell(_norm_ws(_text(name_node, src)))}"


class _StrayError(NamedTuple):
    """One top-most ERROR or MISSING node, with the ancestry the error walk
    carried down to it (`_stray_errors`).

    `location` is the error's location read from its nearest qualifying
    ancestor, or None when no ancestor qualifies. `declarations` is the chain
    of enclosing `*_declaration` nodes, nearest first, as nested pairs
    `(node, rest)` ending in None. A pair is built once per declaration and
    shared by every node below it, so carrying the chain costs constant work
    per visited node however deep the tree is.
    """

    node: object
    location: str | None
    declarations: tuple | None


def _location_rule(node_type: str) -> str | None:
    """The location an error reads from an ancestor of `node_type`, or None
    when that ancestor does not decide it (`_classify_error`)."""
    if node_type in _BODY_TYPES:
        return "body"
    if node_type in _MEMBER_CONTAINERS:
        return "member"
    if node_type.endswith("_declaration"):
        return "declaration header"
    return None


def _classify_error(err: _StrayError, src: bytes, labels: dict | None = None) -> tuple[str, str]:
    """`(location, effect)` for one error node, read from its ancestry.

    `location` is `body` inside a function, accessor or closure body, `member`
    directly inside a type body, `declaration header` inside a declaration but
    outside its body, and `top level` otherwise. The nearest ancestor that
    decides it wins. `effect` names the nearest enclosing declaration that
    carries a label, or states there is none. Neither claims facts are intact
    (ADR-0129 clause 7).

    The ancestry comes from the error walk, never from `Node.parent` (see
    `_is_benign_missing_bang`). `labels` memoizes each resolved link of the
    declaration chain for the file, so many errors under one deep chain
    resolve each link once.
    """
    memo = labels if labels is not None else {}
    visited = []
    link = err.declarations
    effect = None
    while link is not None:
        if id(link) in memo:
            effect = memo[id(link)]
            break
        visited.append(link)
        decl, rest = link
        label = _declaration_label(decl, src)
        if label is not None:
            effect = f"enclosing {label}"
            break
        link = rest
    for seen in visited:
        memo[id(seen)] = effect
    return err.location or "top level", effect or "no enclosing declaration"


def _error_span(node) -> tuple[int, int]:
    """The 1-based lines an error node covers.

    A zero-width node at column 0 (a MISSING token the grammar inserts at the
    start of a line, typically at end of file) belongs to the line before it,
    where the missing token would have been written.
    """
    row, col = node.start_point
    if node.start_byte == node.end_byte and col == 0 and row > 0:
        return (row, row)
    start = row + 1
    return (start, max(start, _end_display_line(node.end_point)))


def _stray_errors(root_node, skip: frozenset) -> list[_StrayError]:
    """Every top-most ERROR or MISSING node outside the dropped declarations,
    the benign shape excluded, in document order.

    One explicit-stack walk carries each node's ancestry down with it: its
    nearest ancestors for the benign-shape check, its location, and its chain
    of enclosing declarations. Nothing here calls `Node.parent`, so the walk
    stays linear in the size of the tree at any depth (ADR-0129 clause 2).
    """
    found: list[_StrayError] = []
    stack = [(root_node, (), None, None)]
    while stack:
        n, ancestors, location, declarations = stack.pop()
        if _node_span_key(n) in skip:
            continue
        if n.is_error or n.is_missing:
            if not _is_benign_missing_bang(n, ancestors):
                found.append(_StrayError(n, location, declarations))
            continue
        t = n.type
        child_ancestors = ((n,) + ancestors)[:_BENIGN_ANCESTORS]
        child_location = _location_rule(t) or location
        child_declarations = (n, declarations) if t.endswith("_declaration") else declarations
        stack.extend((c, child_ancestors, child_location, child_declarations)
                     for c in reversed(n.children))
    return found


def _parse_file(rel: str, raw: bytes) -> FileFacts:
    """One Swift source file's facts, behind the per-file boundary.

    Every step catches `Exception`, which covers `RecursionError` and
    `MemoryError`. A failed step renders one `parse-error` naming the step and
    the derive continues. The caller has already hashed the bytes.
    """
    facts = FileFacts(path=rel, name_budget=_NAME_BUDGET_FACTOR * len(raw) + _NAME_BUDGET_FLOOR)
    try:
        tree = _parse(raw)
    except Exception:  # noqa: BLE001 — the per-file boundary is deliberately broad.
        facts.residuals.append(("parse-error", None, "location none; effect step parse failed"))
        return facts
    step = "walk"
    try:
        dropped = _dropped_declarations(tree)
        skip = frozenset(_node_span_key(d.node) for d in dropped)
        walk = _Walk(raw, facts)
        try:
            walk.run(tree.root_node, skip)
            step = "recovery"
            _recover_dropped(dropped, raw, facts, walk)
        except _NameBudgetSpent as spent:
            facts.residuals.append(("scan-cap", [(spent.line, spent.line)], _BUDGET_DETAIL))
        step = "walk"
        labels: dict = {}
        error_bytes = 0
        for err in _stray_errors(tree.root_node, skip):
            location, effect = _classify_error(err, raw, labels)
            span = _error_span(err.node)
            # Charged in UTF-8 bytes before the detail is built
            # (`_ERROR_BUDGET_DETAIL`).
            error_bytes += len("location ; effect ") + _utf8_len(location) + _utf8_len(effect)
            if error_bytes > facts.name_budget:
                facts.residuals.append(("scan-cap", [(span[0], span[0])], _ERROR_BUDGET_DETAIL))
                break
            facts.residuals.append(("parse-error", [span], f"location {location}; effect {effect}"))
    except Exception:  # noqa: BLE001 — the per-file boundary is deliberately broad.
        facts.residuals.append(("parse-error", None, f"location none; effect step {step} failed"))
    return facts


def _recover_dropped(dropped: list, raw: bytes, facts: FileFacts, walk: "_Walk") -> None:
    """Run clause 7's one bounded recovery for each dropped declaration, then
    walk the rest of its ERROR node (`_parse_file`)."""
    for d in dropped:
        state = walk.skipped_cond.get(_node_span_key(d.node), ([], 0))
        if state[1]:
            # Dropped inside `#if` blocks past `_MAX_IF_NESTING`: nothing
            # there renders, and the `scan-cap` line already says so.
            continue
        result = _recover(d, raw)
        if isinstance(result, RecoveredDeclaration):
            sub = _Walk(result.slice_bytes, facts, result.row_offset, walk.next_block)
            sub.set_cond_state(state)
            sub.run(result.tree.root_node)
            walk.next_block = sub.next_block
            facts.residuals.append((
                "declaration-recovered", [result.error_span],
                f"location top level; {result.keyword} {_cell(result.name)} dropped by a top-level "
                f"error, recovered from lines {result.slice_span[0]}-{result.slice_span[1]}"))
            tail_start = d.node.start_byte + len(result.slice_bytes)
        else:
            facts.residuals.append((
                "declaration-dropped", [result.error_span],
                f"location top level; {result.keyword} {_cell(result.name)} dropped by a top-level "
                "error and not recovered"))
            tail_start = d.node.start_byte
        # The rest of the ERROR is still walked: the reader walks whole
        # subtrees (ADR-0129 clause 7).
        walk.set_cond_state(state)
        walk.run_detached([c for c in d.node.children if c.start_byte >= tail_start])


# ═══════════════════ the literal `Package.swift` reader ═══════════════════════
# Reads `let package = Package(...)` (or `var`) through the pinned grammar and
# never evaluates it. Every value comes from a string literal node, and every
# construct outside the closed subset renders one `non-literal-manifest` line.

#: The closed construct kinds a `non-literal-manifest` line names.
_NON_LITERAL_KINDS = ("#if block", "mutation", "variable reference",
                      "interpolated string", "unsupported call", "condition argument")

_PRODUCT_CALLS = {"library": "library", "executable": "executable", "plugin": "plugin"}
_TARGET_CALLS = {"target": "library", "executableTarget": "executable",
                 "testTarget": "test", "plugin": "plugin"}
_PLUGIN_CAPABILITIES = {"command": "command plugin", "buildTool": "build-tool plugin"}
_DEPENDENCY_REQUIREMENTS = ("from", "exact", "branch", "revision")
_TARGET_DEPENDENCY_CALLS = frozenset({"target", "byName", "product"})

#: SwiftPM's default source directory per target kind — the manifest's own
#: semantics, used only for a target that names no `path:`.
_DEFAULT_TARGET_DIRS = {"library": "Sources", "executable": "Sources", "test": "Tests",
                        "command plugin": "Plugins", "build-tool plugin": "Plugins",
                        "plugin": "Plugins"}

_STRING_ESCAPES = {"\\n": "\n", "\\r": "\r", "\\t": "\t", "\\0": "\0", "\\\\": "\\",
                   '\\"': '"', "\\'": "'"}


@dataclass
class ManifestFacts:
    """One `Package.swift`'s literal facts. `path` names the container by its marker path."""

    path: str
    directory: str
    products: list = field(default_factory=list)       # {name, kind, targets, loc, conditional}
    dependencies: list = field(default_factory=list)   # {identity, kind, written, location, requirement, loc}
    targets: list = field(default_factory=list)        # {name, kind, path, loc, conditional}
    target_deps: list = field(default_factory=list)    # {from, name, package, form, loc, conditional}
    residuals: list = field(default_factory=list)      # (class, ranges | None, detail)
    #: Raw `.package(path:)` references, resolved AFTER cache retrieval
    #: (ADR-0129 clause 4's containment check): {written, loc, conditional}. Parsing which calls exist is a
    #: pure function of the manifest's bytes; whether the named path stays
    #: contained under the checkout reads live filesystem state (a symlink
    #: can be retargeted between two derives in one process), so that check
    #: never runs inside the cached build and never mutates a cached entry.
    path_deps: list = field(default_factory=list)      # {written, loc, conditional}
    #: The identities of the `path_deps` this derive refused (lexical escape,
    #: pruned directory, or real-path escape), one per refusal. Filled only
    #: on the copy `_resolve_manifest_path_deps` returns and never stored in
    #: a cached entry, for the same reason as `path_deps`' resolution. A
    #: product qualified by one of them renders `unresolved-reference` and
    #: draws no edge (ADR-0129 clause 5).
    refused_path_identities: tuple = ()


class _NonLiteral(Exception):
    """A value outside the literal subset; carries the construct kind."""

    def __init__(self, kind: str, node):
        super().__init__(kind)
        self.kind = kind
        self.node = node


def _string_literal(node, src: bytes) -> str:
    """The decoded value of a plain string literal, or `_NonLiteral`."""
    if node is None:
        raise _NonLiteral("unsupported call", node)
    if node.type != "line_string_literal":
        kind = "variable reference" if node.type in ("simple_identifier", "navigation_expression") \
            else "unsupported call"
        raise _NonLiteral(kind, node)
    out = []
    for c in node.children:
        if c.type == '"':
            continue
        if c.type == "line_str_text":
            # Bytes that are not UTF-8 (a surrogate spelled as ED A0 80, for
            # one) name no string. They are refused as `\u{D800}` is, never
            # replaced with U+FFFD, which would fabricate a value.
            try:
                out.append(src[c.start_byte:c.end_byte].decode("utf-8"))
            except UnicodeDecodeError:
                raise _NonLiteral("unsupported call", c) from None
        elif c.type == "str_escaped_char":
            esc = _text(c, src)
            if esc in _STRING_ESCAPES:
                out.append(_STRING_ESCAPES[esc])
            elif esc.startswith("\\u{") and esc.endswith("}"):
                try:
                    scalar = int(esc[3:-1], 16)
                    out.append(chr(scalar))
                except ValueError:
                    raise _NonLiteral("unsupported call", c) from None
                # A surrogate code point is not a Swift Unicode scalar, so it
                # is refused exactly as an out-of-range scalar is.
                if 0xD800 <= scalar <= 0xDFFF:
                    raise _NonLiteral("unsupported call", c)
            else:
                raise _NonLiteral("unsupported call", c)
        else:
            raise _NonLiteral("interpolated string", node)
    return "".join(out)


#: The conditional-compilation tokens a `directive` node opens with.
_DIRECTIVE_TOKENS = frozenset({"#if", "#elseif", "#else", "#endif"})


def _directive_only(node) -> bool:
    """True when `node` holds nothing but conditional-compilation directives.

    Inside a list the grammar parses a directive as a `directive` node, and a
    directive it cannot place, such as a second `#if` in a row, as an ERROR
    around one.
    """
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "directive" or n.type in _DIRECTIVE_TOKENS:
            continue
        if n.type != "ERROR":
            return False
        stack.extend(n.children)
    return True


def _leading_directive_tokens(el) -> list:
    """The directive tokens the grammar attached at the head of a list element.

    Inside a list literal, the pinned grammar parses a directive as the target of
    a navigation, so `#if os(Linux)` followed by `.target(...)` is one element.
    The search stops at `call_suffix`, so a directive inside the element's own
    arguments belongs to that nested list, not to this one.
    """
    out = []
    stack = [el]
    while stack:
        n = stack.pop()
        if n.type == "call_suffix":
            continue
        if n.type in _DIRECTIVE_TOKENS:
            out.append(n)
            continue
        stack.extend(reversed(n.children))
    return out


def _callee(call):
    """The bare name of `Name(...)` or `.name(...)`, or None.

    `.name(...)` also covers the list-element form where the grammar puts the
    preceding directives ahead of the member (`_leading_directive_tokens`).
    """
    if call is None or call.type != "call_expression":
        return None
    first = call.children[0] if call.children else None
    if first is None:
        return None
    if first.type == "simple_identifier":
        return first
    if first.type == "prefix_expression":
        ident = [c for c in first.children if c.type == "simple_identifier"]
        if len(ident) == 1 and first.children[0].type == ".":
            return ident[0]
    if first.type == "navigation_expression" and first.children:
        *heads, suffix = first.children
        if heads and suffix.type == "navigation_suffix" and all(_directive_only(h) for h in heads):
            ident = [c for c in suffix.children if c.type == "simple_identifier"]
            if len(ident) == 1 and suffix.children[0].type == ".":
                return ident[0]
    return None


def _arguments(call):
    """`[(label | None, value node, argument node)]` of a call."""
    suffix = _find_child(call, "call_suffix")
    args = _find_child(suffix, "value_arguments") if suffix is not None else None
    out = []
    if args is None:
        return out
    for a in _find_children(args, "value_argument"):
        label_node = _find_child(a, "value_argument_label")
        value = a.child_by_field_name("value")
        if value is None:
            rest = [c for c in a.children if c.type not in ("value_argument_label", ":")]
            value = rest[0] if rest else None
        out.append((None if label_node is None else label_node, value, a))
    return out


class _ManifestReader:
    def __init__(self, root: Path, manifest: Path, src: bytes):
        self.root = root
        self.src = src
        rel = _rel_str(root, manifest)
        directory = os.path.dirname(rel)
        self.facts = ManifestFacts(path=rel, directory=directory or ".")
        self.base_dir = manifest.parent

    def line(self, node) -> int:
        return node.start_point[0] + 1

    def span(self, node) -> tuple[int, int]:
        return (self.line(node), max(self.line(node), _end_display_line(node.end_point)))

    def residual(self, kind: str, node, span=None) -> None:
        span = span or self.span(node)
        self.facts.residuals.append((
            "non-literal-manifest", [span],
            f"construct {kind} is outside the literal Package.swift subset and is not evaluated"))

    def label(self, label_node) -> str | None:
        return None if label_node is None else _text(label_node, self.src).strip()

    def head_line(self, el) -> int:
        """The line an element's own call starts on, past any directive the
        grammar attached ahead of it."""
        callee = _callee(el)
        return self.line(callee if callee is not None else el)

    def elements(self, container, conditional: bool):
        """`(element, conditional)` for each element of a list literal.

        Tracks the `#if` blocks inside the list. An element inside a block is
        conditional, and an element after its `#endif` is not. Each block
        renders one `#if block` residual from `#if` to `#endif`. A block still
        open at the list's end runs to the list's last line.
        """
        depth = 0
        start = None
        for el in container.children:
            if el.type in ("[", "]", ",", "comment", "multiline_comment"):
                continue
            for tok in _leading_directive_tokens(el):
                if tok.type == "#if":
                    if depth == 0:
                        start = self.line(tok)
                    depth += 1
                elif tok.type == "#endif" and depth > 0:
                    depth -= 1
                    if depth == 0:
                        self.residual("#if block", None, (start, self.line(tok)))
            if _directive_only(el):
                continue
            yield el, conditional or depth > 0
        if depth > 0:
            self.residual("#if block", None, (start, _end_display_line(container.end_point)))

    def read(self, tree) -> ManifestFacts:
        package_call = None
        block_start = None
        depth = 0
        pending: list = []
        for child in tree.root_node.children:
            t = child.type
            if t in ("comment", "multiline_comment", "import_declaration"):
                continue
            if t == "directive":
                head = child.children[0].type if child.children else ""
                if head == "#if":
                    if depth == 0:
                        block_start, pending = child, []
                    depth += 1
                elif head == "#endif" and depth > 0:
                    depth -= 1
                    if depth == 0:
                        # Every branch is read and none is evaluated: one
                        # residual covers the block, and each literal call in a
                        # recognised mutation inside it renders as conditional.
                        self.residual("#if block", block_start,
                                      (self.line(block_start), self.line(child)))
                        for stmt in pending:
                            self.mutation(stmt, conditional=True, report=False)
                        block_start = None
                continue
            if depth > 0:
                pending.append(child)
                continue
            if t == "property_declaration" and package_call is None:
                call = self.package_call(child)
                if call is not None:
                    package_call = call
                    continue
            if t in ("property_declaration", "function_declaration", "class_declaration",
                     "protocol_declaration"):
                # A helper declaration renders nothing by itself; a read that
                # reaches it through a name renders `variable reference`.
                continue
            if t == "assignment":
                self.residual("mutation", child)
            elif not self.mutation(child, conditional=True, report=True):
                self.residual("unsupported call", child)
        if package_call is None:
            self.facts.residuals.append((
                "non-literal-manifest", None,
                "construct unsupported call: no literal `let package = Package(...)` declaration"))
            return self.facts
        self.package(package_call)
        return self.facts

    def package_call(self, decl):
        pattern = decl.child_by_field_name("name") or _find_child(decl, "pattern")
        if pattern is None or _text(pattern, self.src).strip() != "package":
            return None
        value = decl.child_by_field_name("value")
        callee = _callee(value)
        if callee is None or _text(callee, self.src) != "Package":
            return None
        return value

    def package(self, call) -> None:
        for label_node, value, arg in _arguments(call):
            label = self.label(label_node)
            if label == "products":
                self.array(value, self.product, conditional=False)
            elif label == "dependencies":
                self.array(value, self.dependency, conditional=False)
            elif label == "targets":
                self.array(value, self.target, conditional=False)
            elif label == "name":
                try:
                    _string_literal(value, self.src)
                except _NonLiteral as e:
                    self.residual(e.kind, arg)
            # `platforms` and every other argument render nothing; none names
            # a target, product or dependency.

    def array(self, value, reader, conditional: bool) -> None:
        if value is None or value.type != "array_literal":
            kind = "variable reference" if value is not None and value.type == "simple_identifier" \
                else "unsupported call"
            self.residual(kind, value if value is not None else None)
            return
        for el, cond in self.elements(value, conditional):
            try:
                reader(el, cond)
            except _NonLiteral as e:
                self.residual(e.kind, el)

    def mutation(self, stmt, conditional: bool, report: bool) -> bool:
        """`package.<field>.append(...)`: read the literal calls inside as
        conditional rows. Returns False when `stmt` is no such mutation."""
        call = stmt if stmt.type == "call_expression" else None
        if call is None:
            return False
        nav = call.children[0] if call.children else None
        if nav is None or nav.type != "navigation_expression":
            return False
        parts = [_text(n, self.src) for n in _preorder(nav) if n.type == "simple_identifier"]
        if len(parts) != 3 or parts[0] != "package" or parts[2] != "append":
            return False
        reader = {"targets": self.target, "products": self.product,
                  "dependencies": self.dependency}.get(parts[1])
        if reader is None:
            return False
        if report:
            self.residual("mutation", stmt)
        for label_node, value, _arg in _arguments(call):
            if value is not None and value.type == "array_literal":
                self.array(value, reader, conditional=True)
            elif value is not None:
                try:
                    reader(value, True)
                except _NonLiteral as e:
                    self.residual(e.kind, value)
        return True

    def call_parts(self, el, allowed):
        callee = _callee(el)
        if callee is None:
            kind = "variable reference" if el.type == "simple_identifier" else "unsupported call"
            raise _NonLiteral(kind, el)
        name = _text(callee, self.src)
        if name not in allowed:
            raise _NonLiteral("unsupported call", el)
        return name, _arguments(el)

    def name_arg(self, args):
        for label_node, value, arg in args:
            if self.label(label_node) == "name":
                return _string_literal(value, self.src), value
        raise _NonLiteral("unsupported call", None)

    def product(self, el, conditional: bool) -> None:
        method, args = self.call_parts(el, _PRODUCT_CALLS)
        name, name_node = self.name_arg(args)
        targets = []
        for label_node, value, _arg in args:
            if self.label(label_node) == "targets" and value is not None and value.type == "array_literal":
                for t in value.children:
                    if t.type == "line_string_literal":
                        targets.append(_string_literal(t, self.src))
        self.facts.products.append({
            "name": name, "kind": _PRODUCT_CALLS[method], "targets": targets,
            "loc": (self.head_line(el), self.line(name_node)), "conditional": conditional,
        })

    def dependency(self, el, conditional: bool) -> None:
        method, args = self.call_parts(el, {"package"})
        labels = {self.label(ln): (v, a) for ln, v, a in args if ln is not None}
        loc = (self.head_line(el), self.head_line(el))
        if "path" in labels:
            written = _string_literal(labels["path"][0], self.src)
            loc = (self.head_line(el), self.line(labels["path"][0]))
            # The real-path containment check reads live filesystem state,
            # so only the raw reference is recorded here; a post-cache step
            # resolves it fresh on every derive (ADR-0129 clause 4).
            self.facts.path_deps.append({"written": written, "loc": loc, "conditional": conditional})
            return
        if "url" not in labels:
            raise _NonLiteral("unsupported call", el)
        url = _strip_location(_string_literal(labels["url"][0], self.src))
        loc = (self.head_line(el), self.line(labels["url"][0]))
        requirement = None
        for key in _DEPENDENCY_REQUIREMENTS:
            if key in labels:
                requirement = f"{key}: {_string_literal(labels[key][0], self.src)}"
        for label_node, value, arg in args:
            if label_node is None and value is not None:
                requirement = self.range_requirement(value, arg)
        identity = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        identity = identity[:-4] if identity.endswith(".git") else identity
        self.facts.dependencies.append({
            "identity": identity, "kind": "remote package", "written": url, "location": url,
            "requirement": requirement, "loc": loc, "conditional": conditional,
        })

    def range_requirement(self, value, arg):
        if value.type == "range_expression":
            strings = [c for c in value.children if c.type == "line_string_literal"]
            ops = [_text(c, self.src) for c in value.children
                   if c.type not in ("line_string_literal",) and not c.is_named]
            if len(strings) == 2 and len(ops) == 1:
                return (f"{_string_literal(strings[0], self.src)}{ops[0]}"
                        f"{_string_literal(strings[1], self.src)}")
        self.residual("unsupported call", arg)
        return None

    def target(self, el, conditional: bool) -> None:
        method, args = self.call_parts(el, _TARGET_CALLS)
        name, name_node = self.name_arg(args)
        kind = _TARGET_CALLS[method]
        path = None
        deps = None
        for label_node, value, arg in args:
            label = self.label(label_node)
            if label == "path":
                path = _string_literal(value, self.src)
            elif label == "dependencies":
                deps = value
            elif label == "capability" and method == "plugin":
                cap = _callee(value)
                kind = _PLUGIN_CAPABILITIES.get(_text(cap, self.src), "plugin") if cap is not None \
                    else "plugin"
        self.facts.targets.append({
            "name": name, "kind": kind, "path": path, "loc": (self.head_line(el), self.line(name_node)),
            "conditional": conditional,
        })
        if deps is None:
            return
        if deps.type != "array_literal":
            raise _NonLiteral("variable reference" if deps.type == "simple_identifier"
                              else "unsupported call", deps)
        for d, cond in self.elements(deps, conditional):
            try:
                self.target_dependency(name, d, cond)
            except _NonLiteral as e:
                self.residual(e.kind, d)

    def target_dependency(self, owner: str, d, conditional: bool) -> None:
        loc = (self.head_line(d), self.head_line(d))
        if d.type == "line_string_literal":
            self.facts.target_deps.append({"from": owner, "name": _string_literal(d, self.src),
                                           "package": None, "form": "byName", "loc": loc,
                                           "conditional": conditional})
            return
        method, args = self.call_parts(d, _TARGET_DEPENDENCY_CALLS)
        labels = {self.label(ln): v for ln, v, _a in args if ln is not None}
        dep_conditional = conditional
        if "condition" in labels:
            self.residual("condition argument", d)
            dep_conditional = True
        name = _string_literal(labels.get("name"), self.src)
        package = _string_literal(labels["package"], self.src) if "package" in labels else None
        self.facts.target_deps.append({"from": owner, "name": name, "package": package,
                                       "form": method, "loc": loc, "conditional": dep_conditional})


def _read_manifest(root: Path, manifest: Path, raw: bytes) -> ManifestFacts:
    """One manifest's literal facts, behind the per-file boundary."""
    reader = _ManifestReader(root, manifest, raw)
    try:
        tree = _parse(raw)
    except Exception:  # noqa: BLE001 — the per-file boundary is deliberately broad.
        reader.facts.residuals.append(("parse-error", None, "location none; effect step parse failed"))
        return reader.facts
    try:
        reader.read(tree)
        labels: dict = {}
        for err in _stray_errors(tree.root_node, frozenset()):
            location, effect = _classify_error(err, raw, labels)
            reader.facts.residuals.append(("parse-error", [_error_span(err.node)],
                                           f"location {location}; effect {effect}"))
    except Exception:  # noqa: BLE001 — the per-file boundary is deliberately broad.
        reader.facts.residuals.append(("parse-error", None, "location none; effect step manifest failed"))
    return reader.facts


def _path_dep_identity(written: str) -> str:
    """The package identity a `.package(path:)` declares: the final component
    of its normalised written path. One helper names both the dependency row
    and a refused reference, so the two can never disagree."""
    return os.path.basename(os.path.normpath(written))


def _contain_path_dep(root: Path, directory: str, written: str) -> tuple[str | None, str | None]:
    """Classify one `.package(path:)`, cheapest check first, as
    `(location, refusal)`: exactly one is `None`.

    `location` is the repo-relative directory the reference names lexically
    (`.` for the checkout root). `refusal` is the residual class the
    reference renders instead, `path-escape` or `unresolved-reference`.
    ADR-0129 clause 4 is the rule: a `.package(path:)` that stays inside the
    checkout names a local package container, and one that escapes renders
    `path-escape`. ADR-0130 clause 3 governs project, workspace and xcconfig
    paths only; `Package.swift` is none of those, so a contained symlinked
    component is not refused here. No build-setting reference (`$(VAR)`,
    `${VAR}` or `$VAR`) is recognised: in a `.package(path:)` it is literal
    text.

    The steps run in this order, and each refusal returns before the next:
    1. normalise lexically against the declaring manifest's repo-relative
       directory, with string operations only. An absolute path, or one a
       `..` walks past the root, is `path-escape`.
    2. test the normalised segments against the Swift walk's prune rule
       (ADR-0129 clause 8). A pruned directory is `unresolved-reference`,
       and no filesystem call has touched the reference yet.
    3. check real-path containment once, through `swift_xcinputs.contained`,
       which gives the core's containment verdict (`_contained`) in time
       linear in the path. Nothing is read. A symlinked component whose
       target leaves the checkout is `path-escape`, and so is a path that
       cannot be resolved.

    Step 3 reads live filesystem state (`os.path.realpath` follows a symlink
    to its CURRENT target), so this runs fresh on every call and is never
    memoized alongside the manifest's own parsed facts: a symlink can be
    retargeted between two derives in the same process, and the second
    derive must see the second target, not the first derive's verdict."""
    base = "" if directory == "." else directory
    rel = swift_xcinputs._normalize_relative(base, written)
    if rel is None:
        return None, "path-escape"
    segments = rel.split("/") if rel else []
    if swift_prune.pruned_path(segments):
        return None, "unresolved-reference"
    try:
        contained = swift_xcinputs.contained(root, root.joinpath(*segments))
    except (OSError, RuntimeError, ValueError):
        contained = False
    if not contained:
        return None, "path-escape"
    return (rel or "."), None


_PATH_ESCAPE_DETAIL = "a `.package(path:)` reference leaves the checkout; nothing is read through it"


def _resolve_manifest_path_deps(root: Path, mf: ManifestFacts) -> ManifestFacts:
    """Resolve `mf.path_deps` (raw, cache-pure) against the live filesystem,
    returning a manifest whose `dependencies`, `residuals` and
    `refused_path_identities` carry the result.

    Never mutates the cached `mf` the caller passed in: `dependencies` and
    `residuals` are copied first and `refused_path_identities` is built
    fresh, so a later derive that retrieves the SAME cached entry (same
    `(rel, sha256)`, read again by `_cached`) still starts from the cache's
    pure facts rather than a stale resolution this call appended on a
    previous derive."""
    if not mf.path_deps:
        return mf
    dependencies = list(mf.dependencies)
    residuals = list(mf.residuals)
    refused: list = []
    for pd in mf.path_deps:
        written, loc = pd["written"], pd["loc"]
        identity = _path_dep_identity(written)
        location, refusal = _contain_path_dep(root, mf.directory, written)
        if refusal == "path-escape":
            residuals.append(("path-escape", [loc], _PATH_ESCAPE_DETAIL))
            refused.append(identity)
            continue
        if refusal is not None:
            # ADR-0129 clause 8: a directory every Swift walk skips is named by
            # no row, so the reference renders one residual and no dependency.
            residuals.append(("unresolved-reference", [loc], swift_prune.PATH_DEPENDENCY_DETAIL))
            refused.append(identity)
            continue
        dependencies.append({
            "identity": identity, "kind": "path dependency",
            "written": written, "location": location, "requirement": None, "loc": loc,
            "conditional": pd["conditional"],
        })
    return replace(mf, dependencies=dependencies, residuals=residuals,
                   refused_path_identities=tuple(refused))


# ═══════════ the project-facts seam the Xcode reader fills ════════════════════
# ADR-0129 clause 10's consumer interface: `_project_facts` fills it from the
# Xcode project reader (`swift_xcode.read_projects`) and the workspace,
# xcconfig and generator readers, and every renderer consumes it.

class ProjectFacts(NamedTuple):
    """What the Xcode inputs contribute to the spine: targets, target and
    product dependencies, project-level dependency rows, `.swift` membership
    and project-file residuals (ADR-0129 clause 10; ADR-0130 clauses 5-14)."""

    #: {name, container, kind, product_type, path, loc, conditional}. `kind`
    #: is one of {app, extension, test, library, executable, unmapped}
    #: (ADR-0130 clause 5). `product_type` is the final dot-segment of
    #: `productType`, or `—` when the target names none. `path` is the
    #: declaring `project.pbxproj`'s repo-relative path (used with `loc`,
    #: the target object's span, to render "declared at").
    targets: tuple = ()
    #: {from, to, container} -- a resolved target-to-target edge inside one
    #: container (ADR-0130 clause 6). Rendered as a graph edge only, never a
    #: dependency row.
    target_deps: tuple = ()
    #: {from, container, kind, product, location, requirement, path, loc,
    #: edge_to}. `edge_to` is `(container, target)` when this entry is a
    #: RESOLVED in-tree product edge (rendered as a graph edge and nothing
    #: else); `edge_to` is `None` when it is a DEPENDENCY ROW instead,
    #: `kind` naming which ("remote product" or "plugin product"),
    #: `location` the stripped location (or `None`), `requirement` the
    #: requirement string (or `None`), and `path`/`loc` the declaring
    #: target's `packageProductDependencies` list-item line (ADR-0130
    #: clause 7).
    product_deps: tuple = ()
    #: {location, requirement, path, loc} -- a project-level
    #: `XCRemoteSwiftPackageReference` (ADR-0130 clause 7). Always renders
    #: as one dependency row with `from` the literal `project`.
    dependencies: tuple = ()
    #: {file, target, container, route, root, loc, conditional}. `route` is
    #: one of `folder-synced root <root path>`, `classic Sources build
    #: phase`, `added by an exception set`, `excluded by an exception set`
    #: (ADR-0130 clauses 8-9). `root` is the synced root's repo-relative
    #: path when `route` names one, else `None`. `path`/`loc` (with
    #: `container`) render "declared at" as the pbxproj line of the route
    #: entry.
    memberships: tuple = ()
    #: (class, path, ranges | None, detail) -- project-level residuals
    #: (ADR-0130 clause 14). Rendered in module-graph; the read-level
    #: classes among them (`oversize`, `project-unreadable`, `scan-cap`)
    #: also render in every OTHER concern that consumed a project input.
    residuals: tuple = ()


def _input_refusal_kind(reason: str) -> str:
    """The `project-unreadable` kind for a `_safe_read_bytes` refusal of a
    walk-reached project input (ADR-0130 clause 14). The walk descends no
    directory symlink, so an input escapes only through a symlink at its own
    path: `escape` is therefore `not-regular`, the kind a symlink pointing
    inside the checkout already gets. Anything else is `read-failed`."""
    return "not-regular" if reason in ("not-regular", "escape") else "read-failed"


def _xcconfig_residuals(reads: "ProjectReads") -> tuple:
    """Every `.xcconfig` residual (ADR-0130 clause 12), read through
    `swift_xcinputs.scan_xcconfig` over `reads.files`, plus a
    `project-unreadable`/`oversize` line for each `.xcconfig` `_scan`
    already refused (`reads.refusals`) -- rendered the same way a refused
    `project.pbxproj` is in `swift_xcode.read_projects`."""
    residuals: list = []
    for rel, raw in sorted(reads.files.items()):
        if not rel.endswith(".xcconfig"):
            continue
        facts = swift_xcinputs.scan_xcconfig(rel, raw)
        residuals.extend((r.klass, r.path, [r.lines] if r.lines else None, r.detail) for r in facts.residuals)
    for rel, reason in sorted(reads.refusals.items()):
        if not rel.endswith(".xcconfig"):
            continue
        if reason == "oversize":
            residuals.append(("oversize", rel, None,
                              "xcconfig exceeds the 2 MB bound and is not read"))
        else:
            residuals.append(("project-unreadable", rel, None, _input_refusal_kind(reason)))
    return tuple(residuals)


def _workspace_facts_list(root: Path, reads: "ProjectReads",
                          budget: "swift_xcinputs.CollectionBudget | None" = None) -> tuple:
    """Every standalone workspace's `swift_xcinputs.WorkspaceFacts` this
    concern's scan reached (ADR-0130 clause 11): read through
    `swift_xcinputs.read_workspace` over `reads.files`, plus one carrying a
    `project-unreadable`/`oversize` residual for each workspace `_scan`
    already refused. A workspace adds no container itself -- this is the
    `workspace_facts` `swift_xcode.read_projects` consumes as the unlinked-
    product resolver's candidate-container source, never a container list
    of its own.

    `budget` is the concern's `CollectionBudget`: each workspace's walk
    charges its work bound (`swift_xcinputs.MAX_WORK_UNITS`), and a walk the
    bound refuses keeps what it read and stops. `read_workspace` never lets
    that refusal reach the per-file boundary below."""
    suffix = "/contents.xcworkspacedata"
    facts: list = []
    for rel, raw in sorted(reads.files.items()):
        if not rel.endswith("contents.xcworkspacedata"):
            continue
        ws_dir = rel[: -len(suffix)] if rel.endswith(suffix) else ""
        try:
            facts.append(swift_xcinputs.read_workspace(root, ws_dir, raw, budget))
        except Exception as exc:
            # The per-file boundary (ADR-0130 clause 2): no workspace content
            # exits 2. An OS error is `read-failed`; anything else the grammar
            # did not anticipate is `malformed`. The exception text never
            # renders.
            kind = "read-failed" if isinstance(exc, OSError) else "malformed"
            facts.append(swift_xcinputs.WorkspaceFacts(
                path=rel, refs=(),
                residuals=(swift_xcinputs.Residual("project-unreadable", rel, None, kind),)))
    for rel, reason in sorted(reads.refusals.items()):
        if not rel.endswith("contents.xcworkspacedata"):
            continue
        klass = "oversize" if reason == "oversize" else "project-unreadable"
        kind = ("the workspace exceeds the 2 MB bound and is not read" if reason == "oversize"
                else _input_refusal_kind(reason))
        facts.append(swift_xcinputs.WorkspaceFacts(
            path=rel, refs=(), residuals=(swift_xcinputs.Residual(klass, rel, None, kind),)))
    return tuple(facts)


def _generator_residuals(root: Path, reads: "ProjectReads") -> tuple:
    """Every Tuist/XcodeGen `missing-input` residual this checkout carries
    (ADR-0130 clause 13): the checkout root PLUS every
    directory a `project.yml`, `Project.swift` or `Workspace.swift` candidate
    sits in, reached through the same `_swift_walk` every other project-input
    collector in this module uses. `detect()` already ran the same two
    checks at the root alone to decide whether this repository is a Swift
    stack candidate at all; this call is the cheap, presence/shape check
    repeated per candidate directory, never a re-derive of anything `_scan`
    already read. Clause 13 scopes a manifest's `missing-input` residual to
    "its directory" -- a root-only check silently drops a NESTED qualifying
    manifest (`ios/project.yml` with no companion `App.xcodeproj` beside it)
    entirely, rendering zero `missing-input` lines for a checkout that
    should carry one.

    The XcodeGen check interprets only the `project.yml` bytes this
    concern's `_scan` read (`reads.files`), so each byte it reads is counted
    against the caps and hashed into `sources` (ADR-0129 clause 2). A
    manifest the scan capped or refused is never read here and renders
    nothing. The Tuist check reads no content.
    """
    directories: set = {""}
    for _dirp, rel_dir, _normal, _excluded, filenames in _swift_walk(root):
        if any(f in ("project.yml", "Project.swift", "Workspace.swift") for f in filenames):
            directories.add(rel_dir)
    residuals: list = []
    seen: set = set()
    for directory_rel in sorted(directories):
        manifest_rel = f"{directory_rel}/project.yml" if directory_rel else "project.yml"
        raw = reads.files.get(manifest_rel)
        results = [swift_generators.check_tuist(root, directory_rel)]
        if raw is not None:
            results.append(swift_generators.check_xcodegen(root, directory_rel, raw))
        for result in results:
            for r in result.residuals:
                key = (r.klass, r.path, r.detail)
                if key in seen:
                    continue
                seen.add(key)
                residuals.append((r.klass, r.path, [r.lines] if r.lines else None, r.detail))
    return tuple(residuals)


def _project_facts(root: Path, manifests: list, reads: "ProjectReads",
                   collection: "swift_xcinputs.CollectionBudget | None" = None) -> ProjectFacts:
    """Every Xcode input's contribution for one concern, as `ProjectFacts`:
    `swift_xcode.read_projects` over each `project.pbxproj`, plus every
    standalone workspace's classified facts (`swift_xcinputs.
    read_workspace`), every `.xcconfig`'s classified facts (`swift_xcinputs.
    scan_xcconfig`), and the Tuist/XcodeGen `missing-input` check
    (`swift_generators`).

    `reads` is a `ProjectReads`: the project-input bytes and
    refusal reasons `_scan` already read and bounded for THIS concern
    (`analysis.project_reads`, ADR-0130 clauses 1-2), plus the
    `*.xcodeproj` bundle census. It is never the Swift-source `reads` list
    -- that stays `analysis.reads`, consumed by `_analyse` alone.
    `swift_xcode.read_projects` reads `reads.files`/`reads.bundles` to build
    targets, target dependencies and memberships, and `reads.refusals` to
    render `project-unreadable`/`oversize` lines without re-deriving them.
    `reads.residuals` (this concern's own `scan-cap` lines on a project
    candidate) is folded in here rather than by the reader, which never sees
    a concern that named no project glob at all.

    `workspace_facts` is the tuple `_workspace_facts_list` builds: one
    `swift_xcinputs.WorkspaceFacts` per standalone workspace this concern's
    scan reached. `swift_xcode.read_projects` passes it to the
    unlinked-product resolver (`swift_xcode_products.py`), which reads each
    entry's `.refs` as unlinked-product candidates (ADR-0130 clause 7). This
    function folds each entry's own `.residuals` into this concern's
    rendered output, since a workspace adds no container of its own
    (ADR-0130 clause 11) and its residuals would otherwise never render
    anywhere.

    `read_projects` returns a plain dict (never a `swift.ProjectFacts`) so
    that `swift_xcode.py` need not import this module -- the dependency
    stays one-way, `swift.py` importing `swift_xcode`, never the reverse.

    `collection` is the calling extractor's `swift_xcinputs.CollectionBudget`.
    The reader charges it and renders the membership bound's `scan-cap` line
    itself. When `collection` is passed, the reader leaves the detail
    bound's line to the caller: `extract_module_graph` charges the same
    budget after this returns and renders that line, and `extract_api_surface`
    renders no project residual detail and so no detail-bound line. `None`
    gives the reader a budget of its own, and it then renders both lines.

    The same `collection` is the work budget of the workspace reader and the
    project reader (`swift_xcinputs.MAX_WORK_UNITS`): the workspaces are
    read first, and the project reader renders the work bound's one
    `scan-cap` line for both.
    With `None` the workspace reader charges nothing."""
    workspace_facts = _workspace_facts_list(root, reads, collection)
    result = swift_xcode.read_projects(root, reads, manifests, workspace_facts, collection)
    workspace_residuals = tuple(
        (r.klass, r.path, [r.lines] if r.lines else None, r.detail)
        for wf in workspace_facts for r in wf.residuals)
    residuals = (
        tuple(result["residuals"])
        + tuple(reads.residuals)
        + _xcconfig_residuals(reads)
        + workspace_residuals
        + _generator_residuals(root, reads)
    )
    return ProjectFacts(
        targets=result["targets"],
        target_deps=result["target_deps"],
        product_deps=result["product_deps"],
        dependencies=result["dependencies"],
        memberships=result["memberships"],
        residuals=residuals,
    )


# ═══════════════════ the shared per-derive analysis ══════════════════════════

#: Parsed per-file facts for the repository most recently derived, keyed by
#: `(repo-relative path, SHA-256 of the bytes)`. The three extractors read the
#: same files, so each file is parsed once per derive. A different root clears
#: the cache, so its size is bounded by one repository.
_CACHE: dict = {"root": None, "files": {}, "manifests": {}}


def _cached(kind: str, root: Path, rel: str, digest: str, build):
    key_root = str(root)
    if _CACHE["root"] != key_root:
        _CACHE.update(root=key_root, files={}, manifests={})
    table = _CACHE[kind]
    key = (rel, digest)
    if key not in table:
        table[key] = build()
    return table[key]


@dataclass
class _Analysis:
    sources: dict
    residuals: list            # (class, path, ranges | None, detail)
    files: list                # FileFacts
    manifests: list            # ManifestFacts
    reads: list                # (Path, bytes) -- the Swift/manifest reads `_analyse` parsed
    project_reads: "ProjectReads" = None  # the project-input reads `_project_facts` consumes


_PARSE_SECONDS = 5.0


def _parse_worker(connection) -> None:
    """Owned short-lived worker; no source path is opened by the child."""
    try:
        while True:
            item = connection.recv()
            if item is None:
                break
            kind, args = item
            try:
                result = _parse_file(*args) if kind == "file" else _read_manifest(*args)
                connection.send(("ok", result))
            except BaseException:
                connection.send(("error", None))
    except EOFError:
        pass
    finally:
        connection.close()


class _BoundedParser:
    """One process per analysis pass, with a fresh process after a timeout."""

    def __init__(self):
        self.context = multiprocessing.get_context("fork" if hasattr(os, "fork") else "spawn")
        self.parent = None
        self.process = None

    def _start(self) -> None:
        if self.process is not None and self.process.is_alive():
            return
        self.close()
        parent, child = self.context.Pipe()
        process = self.context.Process(target=_parse_worker, args=(child,))
        process.start()
        child.close()
        self.parent, self.process = parent, process

    def call(self, kind: str, args: tuple, *, timeout: float = _PARSE_SECONDS):
        if kind not in ("file", "manifest") or timeout <= 0:
            raise ValueError("invalid bounded Swift parse request")
        self._start()
        assert self.parent is not None
        detail = f"Swift parse unobserved after {timeout:g}s owned deadline"
        try:
            self.parent.send((kind, args))
            if self.parent.poll(timeout):
                status, value = self.parent.recv()
                if status == "ok":
                    return value
                detail = "Swift parse unobserved because the owned worker failed"
        except (EOFError, BrokenPipeError, OSError):
            detail = "Swift parse unobserved because the owned worker exited"
        self.close()
        if kind == "file":
            facts = FileFacts(path=args[0])
        else:
            facts = _ManifestReader(*args).facts
        facts.residuals.append(("parse-error", None,
                                f"location none; {detail}; effect step parse failed"))
        return facts

    def close(self) -> None:
        if self.parent is not None:
            try:
                if self.process is not None and self.process.is_alive():
                    self.parent.send(None)
                    self.process.join(timeout=0.2)
            except (EOFError, BrokenPipeError, OSError):
                pass
            self.parent.close()
            self.parent = None
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()  # only this worker's PID
                self.process.join(timeout=1)
            if self.process.is_alive():
                self.process.kill()  # only this worker's PID; bounded cleanup
                self.process.join(timeout=1)
            self.process.close()
            self.process = None


def _analyse(root: Path, concern: str, with_manifests: bool) -> _Analysis:
    root = Path(root)
    reads, project_reads, sources, residuals = _scan(root, concern, with_manifests)
    files: list = []
    manifests: list = []
    worker = _BoundedParser()
    try:
        for path, raw in reads:
            rel = _rel_str(root, path)
            digest = sources[rel]
            if path.name == "Package.swift":
                mf = _cached("manifests", root, rel, digest,
                             lambda: worker.call("manifest", (root, path, raw)))
                mf = _resolve_manifest_path_deps(root, mf)
                manifests.append(mf)
                residuals.extend((c, rel, r, d) for c, r, d in mf.residuals)
            else:
                ff = _cached("files", root, rel, digest,
                             lambda: worker.call("file", (rel, raw)))
                files.append(ff)
                residuals.extend((c, rel, r, d) for c, r, d in ff.residuals)
    finally:
        worker.close()
    return _Analysis(sources=sources, residuals=residuals, files=files, manifests=manifests,
                      reads=reads, project_reads=project_reads)


def _manifest_owner_index(manifests: list) -> dict:
    """Each manifest target's directory -> its best owner candidate
    `(-len(directory), target, container)`: an explicit `path:`, else
    SwiftPM's default directory for the target kind. Built once per
    extractor call, so each file's lookup costs its own depth, not the
    number of targets."""
    index: dict = {}
    for mf in manifests:
        base = "" if mf.directory == "." else mf.directory
        for t in mf.targets:
            rel_dir = t["path"] if t["path"] else f"{_DEFAULT_TARGET_DIRS.get(t['kind'], 'Sources')}/{t['name']}"
            full = os.path.normpath(os.path.join(base, rel_dir)).replace(os.sep, "/")
            candidate = (-len(full), t["name"], mf.path)
            held = index.get(full)
            if held is None or candidate < held:
                index[full] = candidate
    return index


def _owning_target(file_rel: str, owner_index: dict) -> tuple[str, str] | None:
    """`(target, container)` for the manifest target whose directory holds
    the file (`_manifest_owner_index`). The deepest matching directory wins;
    ties sort by name. A directory holds the file when it equals the file's
    path or is one of its ancestor directories."""
    best = None
    probes = [file_rel] + [file_rel[:i] for i, ch in enumerate(file_rel) if ch == "/"]
    for probe in probes:
        candidate = owner_index.get(probe)
        if candidate is not None and (best is None or candidate < best):
            best = candidate
    return None if best is None else (best[1], best[2])


def _membership_index(project: "ProjectFacts") -> dict:
    """File -> every `(target, container)` whose Xcode membership route
    claims it, `excluded by an exception set` left out: an excluded file is
    not a member (ADR-0130 clauses 9 and 16). Built once per extractor call,
    so no file scans every membership."""
    index: dict = {}
    for m in project.memberships:
        if m["route"] != "excluded by an exception set":
            index.setdefault(m["file"], set()).add((m["target"], m["container"]))
    return index


def _owning_targets(file_rel: str, owner_index: dict, member_index: dict) -> list[tuple[str, str]]:
    """Every `(target, container)` that owns `file_rel`, sorted: the manifest
    owner (ADR-0129) plus every Xcode route other than `excluded by an
    exception set`. Shared by the `@main` owning-target cell and the import
    owning-target cell."""
    owners: set[tuple[str, str]] = set(member_index.get(file_rel, ()))
    manifest_owner = _owning_target(file_rel, owner_index)
    if manifest_owner:
        owners.add(manifest_owner)
    return sorted(owners)


def _owners_cell(owners: list[tuple[str, str]]) -> str:
    """Every owner `Name (container)`, sorted, joined with `, `, inside one
    code span; `—` when there is none (ADR-0130 clause 16)."""
    return _code(", ".join(f"{n} ({c})" for n, c in owners)) if owners else "—"


#: The `oversize` / `project-unreadable` / `scan-cap` classes a concern that
#: merely consumed a project input still renders, even though the FULL
#: project-level residual list renders in module-graph only. The read-level
#: lines follow the read: each concern that consumes a project input reads
#: it, hashes it and counts it against its own caps (ADR-0130 clauses 2 and
#: 16), so each reports that read's outcome. The `scan-cap` class also
#: carries the Xcode reader's membership-bound line, a collection bound
#: rather than a read outcome, which every concern that runs the reader
#: renders.
_PROJECT_READ_LEVEL_CLASSES = frozenset({"oversize", "project-unreadable", "scan-cap"})


# ═══════════════════════ the three concern renderers ══════════════════════════

def _loc_cell(path: str, loc: tuple[int, int]) -> str:
    return f"`{_cell(f'{path}:{loc[0]}-{loc[1]}')}`"


def _code(value) -> str:
    return f"`{_cell(value)}`"


def _table_section(title: str, header_cells: tuple, rows, budget: "_OutputBudget") -> tuple[list, int]:
    """`(lines, rendered row count)` for one table section.

    The heading renders, and an empty section renders `_None._` rather than
    a header-only table, so no zero-row section shows a counted header.
    `rows` is an iterable read one row at a time: each row is charged to
    `budget` before it is kept, and reading stops at the first refused row,
    so a generator builds no row past it. A section the bound has already tripped renders nothing, not even
    its heading, and so does one whose first row does not fit."""
    if budget.tripped:
        return [], 0
    body = []
    empty = True
    for row in rows:
        empty = False
        if not budget.take(row):
            break
        body.append(row)
    if empty:
        return [f"## {title}", "", "_None._", ""], 0
    if not body:
        return [], 0
    out = [f"## {title}", "", "| " + " | ".join(header_cells) + " |", "|" + "---|" * len(header_cells)]
    return out + body + [""], len(body)


def _merge_ranges(ranges) -> list[tuple[int, int]]:
    return sorted(set(tuple(r) for r in ranges))


def _conditional_declarations(files: list, concern: str) -> list:
    """One `conditional-declaration` line per file and name declared in more
    than one `#if` branch, ranged over the enclosing `#if` blocks."""
    out = []
    for ff in files:
        branches: dict = {}
        blocks: dict = {}
        for c, name, branch, block in ff.conditional_occurrences:
            if c != concern:
                continue
            branches.setdefault(name, set()).add(branch)
            blocks.setdefault(name, set()).add(block)
        for name in sorted(branches):
            if len(branches[name]) < 2:
                continue
            spans = [tuple(ff.block_spans[b]) for b in blocks[name] if b in ff.block_spans]
            out.append(("conditional-declaration", ff.path, _merge_ranges(spans),
                        f"{_cell(name)} is declared in {len(branches[name])} `#if` branches; "
                        "every branch renders and none is evaluated"))
    return out


def _macro_residuals(files: list, concern: str) -> list:
    out = []
    for ff in files:
        for (c, label), spans in sorted(ff.macros.items()):
            if c != concern:
                continue
            kind = "attached" if label.startswith("@") else "freestanding"
            out.append(("macro-not-expanded", ff.path, _merge_ranges(spans),
                        f"{kind} macro {_cell(label)} is not expanded; code it generates does not render"))
    return out


def _render_residual_block(residuals: list, budget: "_OutputBudget", sources: dict) -> list:
    """Residual bullets, sorted by (path, class, first range), under `## Residuals`.

    The bullets are deduplicated and sorted first, then built and charged to
    `budget` one at a time, so the ones that render are a prefix of that
    order. When the bound has tripped here or in an earlier section, one
    `scan-cap` line renders last (`_output_cap_line`); `sources` and the full
    bullet set decide which of its two templates."""
    bullets = []
    seen = set()
    for klass, path, ranges, detail in residuals:
        ranges = None if ranges is None else _merge_ranges(ranges)
        key = (path, klass, tuple(ranges or ()), detail)
        if key in seen:
            continue
        seen.add(key)
        bullets.append(key)
    bullets.sort(key=lambda k: (k[0], k[1], k[2][0] if k[2] else (0, 0), k[3]))
    lines = []
    for path, klass, ranges, detail in bullets:
        if budget.tripped:
            break
        line = _residual_line(klass, path, list(ranges) if ranges else None, detail)
        if not budget.take(line):
            break
        lines.append(line)
    if budget.tripped:
        lines.append(_output_cap_line(budget, bullets, sources))
    return _render_residuals(lines)


def _mermaid_block(edges: list, budget: "_OutputBudget") -> tuple[list, int]:
    """`(lines, rendered edge count)` for the `## Graph` section. Each edge
    line is charged to `budget`; a trip inside the fence still closes it
    (ADR-0129 clause 7). Node ids come from every held edge, so a rendered
    prefix names each node exactly as the full graph would."""
    if budget.tripped:
        return [], 0
    if not edges:
        return ["## Graph", "", "_No in-tree dependency edge._", ""], 0
    ids = _node_ids(sorted({a for a, _ in edges} | {b for _, b in edges}))

    def label(const: str) -> str:
        container, name = const.split("::", 1)
        return _swift_label(f"{name} ({container})")

    body = []
    for a, b in edges:
        line = f'  {ids[a]}["{label(a)}"] --> {ids[b]}["{label(b)}"]'
        if not budget.take(line):
            break
        body.append(line)
    if not body:
        return [], 0
    return ["## Graph", "", "```mermaid", "graph LR"] + body + ["```", ""], len(body)


def extract_data_model(root: Path, docs_dir: str):
    """data-model: model types, their stored properties and relationships.

    Every row is built and charged one at a time under the concern's output
    bound (`_MAX_CONCERN_BYTES`); the summary counts the type rows that
    render."""
    analysis = _analyse(root, "data-model", with_manifests=False)
    files = analysis.files
    budget = _OutputBudget("data-model")
    types = [t for ff in files for t in ff.types]
    model_names = {t["name"].rsplit(".", 1)[-1] for t in types} | {t["name"] for t in types}
    class_names = {t["name"].rsplit(".", 1)[-1] for t in types if t["kind"] == "class"}

    type_rows = (
        f"| {_code(t['name'])} | {_cell(t['kind'])} | {_cell(t['access'])} | "
        f"{_loc_cell(t['path'], t['loc'])} | {'yes' if t['conditional'] else '—'} |"
        for t in sorted(types, key=lambda r: (r["path"], r["loc"], r["name"]))
    )
    props = [p for ff in files for p in ff.stored_properties]
    prop_rows = (
        f"| {_code(p['owner'])} | {_code(p['name'])} | {_code(p['type']) if p['type'] else '—'} | "
        f"{_cell(p['access'])} | {_loc_cell(p['path'], p['loc'])} |"
        for p in sorted(props, key=lambda r: (r["path"], r["loc"], r["owner"], r["name"]))
    )
    relations = set()
    for ff in files:
        for r in ff.relationships:
            relation = r["relation"]
            if r["first_class_entry"] and r["to"] in class_names:
                relation = "inherits"
            relations.add((r["path"], r["loc"], r["from"], relation, r["to"]))
        for p in ff.stored_properties:
            for name in p["type_names"]:
                if name in model_names:
                    relations.add((p["path"], p["loc"], p["owner"], "stores", name))
    rel_rows = (
        f"| {_code(frm)} | {_cell(relation)} | {_code(to)} | {_loc_cell(path, loc)} |"
        for path, loc, frm, relation, to in sorted(relations)
    )
    residuals = (analysis.residuals + _conditional_declarations(files, "data-model")
                 + _macro_residuals(files, "data-model"))
    body, n_types = _table_section("Types", ("type", "kind", "access", "declared at", "conditional"),
                                   type_rows, budget)
    body += _table_section("Stored properties",
                           ("owner", "property", "declared type", "access", "declared at"),
                           prop_rows, budget)[0]
    body += _table_section("Relationships", ("from type", "relation", "to type", "declared at"),
                           rel_rows, budget)[0]
    body += _render_residual_block(residuals, budget, analysis.sources)
    if budget.tripped:
        summary = (f"_{n_types} Swift model types (struct, enum, class, actor) with the stored "
                   "properties and declared relationships rendered before the output bound, "
                   "read through the pinned Swift grammar; nothing was compiled or executed._")
    else:
        summary = (f"_{n_types} Swift model types (struct, enum, class, actor) with their stored "
                   "properties and declared relationships, read through the pinned Swift grammar; "
                   "nothing was compiled or executed._")
    out = ["# Data model", "", summary, ""]
    return _canon(out + body), analysis.sources


def extract_api_surface(root: Path, docs_dir: str):
    """api-surface: interfaces, protocol requirements, products, `@main`.

    Also consumes `ProjectFacts` (ADR-0130 clause 16): the `@main`
    owning-target cell folds in every Xcode membership route. The returned
    `sources` map holds every file this concern's scan read, each
    `project.pbxproj` included. Every row, the owning-target cell included,
    is built and charged one at a time under the concern's output bound
    (`_MAX_CONCERN_BYTES`); the summary counts the rows that render."""
    root = Path(root)
    analysis = _analyse(root, "api-surface", with_manifests=True)
    files, manifests = analysis.files, analysis.manifests
    # The project reader's residual details do not render here, so this
    # concern renders no detail-bound line; the membership bound's line is
    # read-level and does (`_PROJECT_READ_LEVEL_CLASSES`).
    project = _project_facts(root, manifests, analysis.project_reads, swift_xcinputs.CollectionBudget())
    budget = _OutputBudget("api-surface")
    interfaces = [i for ff in files for i in ff.interfaces]
    iface_rows = (
        f"| {_code(i['name'])} | {_cell(i['kind'])} | {_cell(i['access'])} | "
        f"{_loc_cell(i['path'], i['loc'])} | "
        f"{_cell(', '.join(i['attributes'])) if i['attributes'] else '—'} | "
        f"{'yes' if i['conditional'] else '—'} |"
        for i in sorted(interfaces, key=lambda r: (r["path"], r["loc"], r["name"]))
    )
    reqs = [r for ff in files for r in ff.requirements]
    req_rows = (
        f"| {_code(r['name'])} | {_code(r['protocol'])} | {_cell(r['kind'])} | "
        f"{_loc_cell(r['path'], r['loc'])} |"
        for r in sorted(reqs, key=lambda r: (r["path"], r["loc"], r["name"]))
    )
    target_kinds = {(mf.path, t["name"]): t["kind"] for mf in manifests for t in mf.targets}

    def product_rows():
        for mf in sorted(manifests, key=lambda m: m.path):
            for p in sorted(mf.products, key=lambda r: (r["loc"], r["name"])):
                kind = p["kind"]
                if kind == "plugin":
                    named = [target_kinds.get((mf.path, t)) for t in p["targets"]]
                    kind = next((k for k in named if k and k.endswith("plugin")), "plugin")
                yield (f"| {_code(p['name'])} | {_cell(kind)} | {_code(mf.path)} | "
                       f"{_loc_cell(mf.path, p['loc'])} |")

    owner_index = _manifest_owner_index(manifests)
    member_index = _membership_index(project)
    mains = [m for ff in files for m in ff.mains]
    main_rows = (
        f"| {_code(m['type'])} | {_cell(m['kind'])} | "
        f"{_owners_cell(_owning_targets(m['path'], owner_index, member_index))} | "
        f"{_loc_cell(m['path'], m['loc'])} |"
        for m in sorted(mains, key=lambda r: (r["path"], r["loc"], r["type"]))
    )
    project_read_level = [r for r in project.residuals if r[0] in _PROJECT_READ_LEVEL_CLASSES]
    residuals = (analysis.residuals + _conditional_declarations(files, "api-surface")
                 + _macro_residuals(files, "api-surface") + project_read_level)
    body, n_ifaces = _table_section(
        "Interfaces", ("interface", "kind", "access", "declared at", "attributes", "conditional"),
        iface_rows, budget)
    lines, n_reqs = _table_section("Protocol requirements", ("requirement", "protocol", "kind", "declared at"),
                                   req_rows, budget)
    body += lines
    lines, n_products = _table_section("Products", ("product", "product kind", "container", "declared at"),
                                       product_rows(), budget)
    body += lines
    lines, n_mains = _table_section("@main declarations", ("@main type", "kind", "owning target", "declared at"),
                                    main_rows, budget)
    body += lines
    body += _render_residual_block(residuals, budget, analysis.sources)
    if budget.tripped:
        summary = (f"_{n_ifaces} interfaces (public, package or open declarations, and protocols "
                   f"at any access level), {n_reqs} protocol requirements, {n_products} package "
                   f"products and {n_mains} `@main` declarations rendered before the output "
                   "bound, read through the pinned Swift grammar; nothing was compiled or "
                   "executed._")
    else:
        summary = (f"_{n_ifaces} interfaces (public, package or open declarations, and every "
                   f"protocol), {n_reqs} protocol requirements, {n_products} package "
                   f"products and {n_mains} `@main` declarations, read through the pinned Swift "
                   "grammar; nothing was compiled or executed._")
    out = ["# API surface", "", summary, ""]
    return _canon(out + body), dict(analysis.sources)


class _BoundedEdges:
    """The `_MAX_GRAPH_EDGES` smallest distinct edges added, held sorted, and
    whether any other distinct edge was added (`over`).

    `sorted(held)` equals the first `_MAX_GRAPH_EDGES` of the full edge set
    sorted, for every input, and `over` is True exactly when the full set is
    larger: once the set is full, any edge not already held is a distinct
    one past the bound. Memory is bounded by the edge bound, never by the
    edges added."""

    def __init__(self):
        self.limit = _MAX_GRAPH_EDGES
        self.held: list = []
        self.members: set = set()
        self.over = False

    def past(self, edge) -> bool:
        """True when the set is full and `edge` sorts after every held edge:
        it is not held and cannot render, and neither can any larger edge."""
        return len(self.held) >= self.limit and (not self.held or edge > self.held[-1])

    def add(self, edge) -> None:
        if edge in self.members:
            return
        if len(self.held) < self.limit:
            bisect.insort(self.held, edge)
            self.members.add(edge)
            return
        self.over = True
        if not self.held or edge > self.held[-1]:
            return
        self.members.discard(self.held.pop())
        bisect.insort(self.held, edge)
        self.members.add(edge)


def _has_edge(owners: list, targets: list) -> bool:
    """True when some owner differs from some target. Both lists hold
    distinct `(string, pair)` entries, so two of either always yield one."""
    if not owners or not targets:
        return False
    return len(owners) > 1 or len(targets) > 1 or owners[0][1] != targets[0][1]


def _add_import_edges(edges: "_BoundedEdges", owners: list, targets: list) -> None:
    """Add the edge from every owning target to every imported in-tree target
    other than itself, in sorted order, and stop at the first edge past a
    full set: every later one sorts after it (`_BoundedEdges.past`). The work
    is bounded by the edge bound plus the owners, not by their product.
    `owners` and `targets` are `(node string, (name, container))` lists."""
    owners = sorted(owners)
    targets = sorted(targets)
    first = targets[0][0]
    for i, (a, owner) in enumerate(owners):
        if edges.past((a, first)):
            if _has_edge(owners[i:], targets):
                edges.over = True
            return
        for j, (b, target) in enumerate(targets):
            if edges.past((a, b)):
                if _has_edge(owners[i:i + 1], targets[j:]):
                    edges.over = True
                break
            if owner != target:
                edges.add((a, b))


_EDGE_CAP_TEMPLATE = "the module graph holds more than {limit} edges; {limit} render"


def extract_module_graph(root: Path, docs_dir: str):
    """module-graph: containers, targets, the dependency graph and imports.

    Consumes `ProjectFacts` in full (ADR-0129 clause 10): Xcode
    targets (with product type once at least one exists), target and
    product dependencies, membership, and every project-level residual.

    Bounded three ways. The edge set keeps only the `_MAX_GRAPH_EDGES`
    smallest edges (`_BoundedEdges`). The residual details composed from
    names count against one `swift_xcinputs.CollectionBudget` shared with
    the project reader. Every row, edge line and residual bullet is charged
    one at a time under the concern's output bound (`_MAX_CONCERN_BYTES`),
    and the summary counts what renders. Every table row except the
    container rows, and every edge line and residual bullet, is also built
    one at a time; the container rows are one sorted list built first."""
    root = Path(root)
    analysis = _analyse(root, "module-graph", with_manifests=True)
    files, manifests = analysis.files, analysis.manifests
    collection = swift_xcinputs.CollectionBudget()
    project = _project_facts(root, manifests, analysis.project_reads, collection)
    budget = _OutputBudget("module-graph")
    residuals = list(analysis.residuals) + list(project.residuals)
    xcode_containers = [c for c in detect_containers(root) if not c.endswith("Package.swift")]

    container_rows = sorted(
        [f"| {_code(mf.path)} | swift package |" for mf in manifests]
        + [f"| {_code(c)} | xcode project |" for c in xcode_containers])

    xcode_present = bool(project.targets)
    targets_by_name: dict = {}
    for mf in manifests:
        for t in mf.targets:
            targets_by_name.setdefault(t["name"], set()).add(mf.path)
    for t in project.targets:
        targets_by_name.setdefault(t["name"], set()).add(t["container"])

    def target_rows():
        product_type_cell = " — |" if xcode_present else ""
        for mf in sorted(manifests, key=lambda m: m.path):
            for t in sorted(mf.targets, key=lambda r: (r["loc"], r["name"])):
                yield (f"| {_code(t['name'])} | {_code(mf.path)} | {_cell(t['kind'])} |{product_type_cell} "
                       f"{'yes' if t['conditional'] else '—'} | {_loc_cell(mf.path, t['loc'])} |")
        for t in sorted(project.targets, key=lambda r: (r["loc"], r["name"])):
            yield (f"| {_code(t['name'])} | {_code(t['container'])} | {_cell(t['kind'])} | "
                   f"{_cell(t.get('product_type') or '—')} | "
                   f"{'yes' if t['conditional'] else '—'} | {_loc_cell(t['path'], t['loc'])} |")

    products_by_name: dict = {}
    for mf in manifests:
        for p in mf.products:
            products_by_name.setdefault(p["name"], set()).add(mf.path)
    local_packages = {}   # (manifest path, identity) -> container of that package
    local_containers = {}  # manifest path -> the other containers its path dependencies name
    remote_packages = {}  # (manifest path, identity) -> dependency row
    manifests_by_dir = {mf.directory: mf.path for mf in manifests}
    refused = {(mf.path, identity) for mf in manifests for identity in mf.refused_path_identities}
    for mf in manifests:
        for d in mf.dependencies:
            if d["kind"] == "path dependency":
                container = manifests_by_dir.get(d["location"])
                local_packages[(mf.path, d["identity"])] = container
                if container is not None and container != mf.path:
                    local_containers.setdefault(mf.path, set()).add(container)
            else:
                remote_packages[(mf.path, d["identity"])] = d

    edges = _BoundedEdges()
    dep_rows = []
    for mf in manifests:
        for d in mf.dependencies:
            # The Dependencies `from` cell names the declaring package by its
            # directory, while a Graph edge names a container by its
            # `Package.swift` path. The asymmetry is deliberate: 50
            # must-render package-dependency facts in the frozen NetNewsWire
            # expectation were recorded in directory form, and the matcher
            # compares `from` exactly. Changing it is an ADR-0130
            # clarification plus a logged expectation amendment.
            dep_rows.append((mf.directory, d["written"], d["kind"], d["location"],
                             d["requirement"], mf.path, d["loc"]))
        own_targets = {t["name"] for t in mf.targets}
        for d in mf.target_deps:
            src_node = f"{mf.path}::{d['from']}"
            name, package = d["name"], d["package"]
            if package is not None and (mf.path, package) in refused:
                # ADR-0129 clause 5: a product qualified by a refused
                # `.package(path:)` resolves to no container. The detail names
                # no package identity (the refused reference renders only its
                # own line range).
                if collection.detail(mf.path, d["loc"], name, d["from"]):
                    residuals.append((
                        "unresolved-reference", mf.path, [d["loc"]],
                        f"product {_cell(name)} of target {_cell(d['from'])} names a package whose "
                        "`.package(path:)` reference was refused; no edge is drawn"))
                continue
            if package is not None and (mf.path, package) in remote_packages:
                remote = remote_packages[(mf.path, package)]
                dep_rows.append((d["from"], name, "remote product", remote["location"], None,
                                 mf.path, d["loc"]))
                continue
            if package is None and d["form"] != "product" and name in own_targets:
                edges.add((src_node, f"{mf.path}::{name}"))
                continue
            if package is not None:
                # A qualified product resolves only through the package its
                # manifest declares; one naming no declared package resolves
                # to none.
                owner = local_packages.get((mf.path, package))
                owners = {owner} if owner and owner in products_by_name.get(name, set()) else set()
            else:
                # An unqualified product's candidates are this manifest's own
                # resolved local packages that declare it. ADR-0129 clause 5
                # allows no other target-to-product edge, and a checkout-wide
                # product-name search is a guess (ADR-0130's rejected
                # alternative, "every literal product in the checkout").
                owners = local_containers.get(mf.path, set()) & products_by_name.get(name, set())
            if len(owners) == 1:
                edges.add((src_node, f"{next(iter(owners))}::{name}"))
            elif collection.detail(mf.path, d["loc"], name, d["from"]):
                residuals.append((
                    "unresolved-reference", mf.path, [d["loc"]],
                    f"product {_cell(name)} of target {_cell(d['from'])} resolves to {len(owners)} containers; "
                    "no edge is drawn"))
    detail_cap = collection.detail_line()
    if detail_cap is not None:
        klass, path, span, detail = detail_cap
        residuals.append((klass, path, swift_xcode.residual_ranges(span), detail))
    for td in project.target_deps:
        to_container = td.get("to_container", td["container"])
        edges.add((f"{td['container']}::{td['from']}", f"{to_container}::{td['to']}"))

    proj_dep_rows = []
    for pd in project.product_deps:
        if pd.get("edge_to") is not None:
            to_container, to_name = pd["edge_to"]
            edges.add((f"{pd['container']}::{pd['from']}", f"{to_container}::{to_name}"))
        else:
            proj_dep_rows.append((pd["from"], pd["product"], pd["kind"], pd["location"],
                                  pd["requirement"], pd["path"], pd["loc"]))
    for d in project.dependencies:
        proj_dep_rows.append(("project", d["location"], "remote package", d["location"],
                              d["requirement"], d["path"], d["loc"]))

    owner_index = _manifest_owner_index(manifests)
    member_index = _membership_index(project)
    import_rows = []
    for ff in files:
        owners = _owning_targets(ff.path, owner_index, member_index)
        imported: dict = {}
        for imp in ff.imports:
            holders = targets_by_name.get(imp["module"], set())
            target = (imp["module"], next(iter(holders))) if len(holders) == 1 else None
            import_rows.append((ff.path, imp["line"], imp["module"], imp["kind"], owners, target))
            if target:
                imported[target] = None
        # An import is a module dependency, never a call: it adds the edge
        # from EVERY owning target to the imported in-tree target.
        if owners and imported:
            _add_import_edges(edges,
                              [(f"{o[1]}::{o[0]}", o) for o in owners],
                              [(f"{t[1]}::{t[0]}", t) for t in imported])

    if edges.over:
        residuals.append(("scan-cap", ".", None, _EDGE_CAP_TEMPLATE.format(limit=edges.limit)))
    edge_list = edges.held

    def dep_lines():
        for frm, dep, kind, location, requirement, path, loc in sorted(
                dep_rows + proj_dep_rows, key=lambda r: (r[5], r[6], r[0], r[1])):
            yield (f"| {_code(frm)} | {_code(dep)} | {_cell(kind)} | {_code(location) if location else '—'} | "
                   f"{_cell(requirement) if requirement else '—'} | {_loc_cell(path, loc)} |")

    def pair(value):
        return _code(f"{value[0]} ({value[1]})") if value else None

    def import_lines():
        for path, line, module, kind, owners, target in sorted(
                import_rows, key=lambda r: (r[0], r[1], r[2], r[3])):
            yield (f"| {_loc_cell(path, (line, line))} | {_code(module)} | {_cell(kind)} | "
                   f"{_owners_cell(owners)} | {pair(target) or 'sdk-or-unresolved'} |")

    def membership_lines():
        for m in sorted(project.memberships, key=lambda m: (m["file"], m["target"], m["route"])):
            yield (f"| {_code(m['file'])} | {_code(m['target'])} | {_cell(m['route'])} | "
                   f"{'yes' if m['conditional'] else '—'} | "
                   f"{_loc_cell(f"{m['container']}/project.pbxproj", m['loc'])} |")

    body, n_containers = _table_section("Containers", ("container", "container kind"), container_rows, budget)
    if xcode_present:
        lines, n_targets = _table_section(
            "Targets", ("target", "container", "target kind", "product type", "conditional", "declared at"),
            target_rows(), budget)
    else:
        lines, n_targets = _table_section(
            "Targets", ("target", "container", "target kind", "conditional", "declared at"),
            target_rows(), budget)
    body += lines
    lines, n_edges = _mermaid_block(edge_list, budget)
    body += lines
    body += _table_section("Dependencies",
                           ("from", "dependency", "dependency kind", "location", "requirement",
                            "declared at"), dep_lines(), budget)[0]
    body += _table_section("Imports", ("file", "module", "import kind", "owning target", "resolves to"),
                           import_lines(), budget)[0]
    if project.memberships:
        body += _table_section("Membership", ("file", "target", "route", "conditional", "declared at"),
                               membership_lines(), budget)[0]
    body += _render_residual_block(residuals, budget, analysis.sources)
    if budget.tripped:
        summary = (f"_{n_containers} containers, {n_targets} targets, {n_edges} in-tree "
                   "dependency edges and the Swift imports rendered before the output bound, "
                   "read from the literal manifest subset and the pinned Swift grammar; no "
                   "manifest was evaluated and no dependency resolved._")
    else:
        summary = (f"_{n_containers} containers, {n_targets} targets, {n_edges} in-tree "
                   "dependency edges and every Swift import, read from the literal manifest "
                   "subset and the pinned Swift grammar; no manifest was evaluated and no "
                   "dependency resolved._")
    out = ["# Module graph", "", summary, ""]
    return _canon(out + body), dict(analysis.sources)


# ═══════════════════════════ the decode verdict ═══════════════════════════════

#: The residual details that mean a declared input yielded no parse tree.
_UNDECODED_DETAILS = ("effect step read failed", "effect step parse failed")


def _residual_entries(content: str):
    """`(class, path, detail)` for every bullet under `## Residuals`."""
    inside = False
    for line in content.split("\n"):
        if line.startswith("## "):
            inside = line.strip() == "## Residuals"
            continue
        if not inside or not line.startswith("- `"):
            continue
        parts = line.split("`")
        if len(parts) < 5:
            continue
        klass, path = parts[1], parts[3]
        rest = line.split("` lines ", 1)[1] if "` lines " in line else ""
        if rest.startswith("— — "):
            detail = rest[len("— — "):]
        else:
            detail = rest.split(" — ", 1)[1] if " — " in rest else ""
        yield klass, path, detail


def concern_decode_verdict(concern: str, content: str, sources: dict) -> "Verdict | None":
    """`parse_failed` exactly when the concern names at least one declared
    Swift input that was not decoded and no input was decoded (ADR-0129
    clause 7). A pure function of the rendered content and the source map.

    A concern whose output bound tripped may not render the `parse-error`
    lines this reads. Its bound line then says itself when no input was
    decoded, with the count (`_OUTPUT_CAP_UNDECODED_TEMPLATE`), so the
    verdict is the one the full residual set gives."""
    entries = list(_residual_entries(content))
    for klass, path, detail in entries:
        capped = klass == "scan-cap" and path == "." and _OUTPUT_CAP_UNDECODED_RE.fullmatch(detail)
        if capped:
            return Verdict.stubbed(
                StubReason.PARSE_FAILED,
                expected="at least one Swift source or `Package.swift` that yields a parse tree",
                found=f"{int(capped.group(1))} declared input(s) present and none decoded",
            )
    undecoded = set()
    parse_failed = set()
    for klass, path, detail in entries:
        if klass == "oversize" or (klass == "parse-error" and detail.endswith(_UNDECODED_DETAILS)):
            undecoded.add(path)
        if klass == "parse-error" and detail.endswith("effect step parse failed"):
            parse_failed.add(path)
    # A residual names its path through `_cell`; `sources` keys the raw path.
    decoded = {_cell(p) for p in sources} - parse_failed
    if undecoded and not decoded:
        return Verdict.stubbed(
            StubReason.PARSE_FAILED,
            expected="at least one Swift source or `Package.swift` that yields a parse tree",
            found=f"{len(undecoded)} declared input(s) present and none decoded",
        )
    return None
