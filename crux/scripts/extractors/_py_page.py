"""Python page rendering for the code-doc dispatcher (ADR-0131 clauses 3, 4, 8-12).

Griffe decides WHAT a page documents: the members `_py_load.load_selection`
built with `griffe.visit` and the Crux `FunctionLocalDefs` extension, their
kinds and labels, their docstrings, a property's accessors, an `@overload`
chain, the static `__all__` evaluation (`module.exports`) and each alias's
target path. Crux decides HOW it reads: selection, visibility by name and
scope (clause 4), placement, Markdown layout and escaping.

Two facts come from the syntax tree rather than from Griffe objects, because
the decision says so:

- a member's start line is its first decorator's line, computed from the
  syntax node and never taken from Griffe's line number (clause 3); and
- a signature renders each annotation, default, base, keyword, decorator and
  return annotation as `ast.unparse` of its expression (the fixtures' R7), so
  the signature text is built from the node Griffe built the member from.

The node for a Griffe member is found by `(name, end line)`, which Griffe
copies from the node. This module never imports or executes the target: it
reads `source.text` with `ast.parse` and Griffe's already-built objects as
data. It never touches `Alias.target`, `Alias.final_target`, `Alias.lineno`
or any other attribute that makes Griffe resolve an alias, because
resolution may try to load a module; exported aliases are followed here by
walking the selection's own collections by path (clause 11).

Loaded by file path, like every module under `extractors/`.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import griffe

_HERE = Path(__file__).resolve().parent


def _load_sibling(mod_name: str, filename: str):
    existing = sys.modules.get(mod_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(mod_name, _HERE / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_render = _load_sibling("crux_py_render", "_py_render.py")
_bindings = _load_sibling("crux_py_bindings", "_py_bindings.py")
_load = _load_sibling("crux_py_load", "_py_load.py")

_GRAMMAR_VERSION = _load.GRAMMAR_VERSION

_EN_DASH = "–"
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEFCLASS = _FUNCS + (ast.ClassDef,)

#: ADR-0131 clause 8: a member heading nests one level per Python nesting
#: level, clamped at H6 (Markdown has no heading deeper than six `#`s).
_MAX_HEADING_LEVEL = 6


@dataclass(frozen=True)
class PageResult:
    """One rendered page: its path under the output root, its escaped title,
    its full body, and the metadata gap entries recorded on its row."""

    doc_path: str
    title: str
    body: str
    gaps: list[dict]


# ─────────────────────────── syntax-node lookup ────────────────────────────


class _Nodes:
    """Index of the source's syntax nodes a renderer needs, by name and line."""

    def __init__(self, tree: ast.Module) -> None:
        # Keyed by (name, end line) with every node sharing the key kept, in
        # walk order (outermost first): a nested def of the same name can end
        # on its enclosing def's last line, and must not replace it.
        self.defs: dict[tuple[str, int], list[ast.AST]] = {}
        self.assigns: dict[int, list[ast.stmt]] = {}
        # Every import statement on a line: `import a as a; import b as b`
        # puts two statements on one line.
        self.imports: dict[int, list[ast.stmt]] = {}
        self.type_aliases: dict[tuple[str, int], ast.AST] = {}
        self.top_level = {id(stmt) for stmt in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, _DEFCLASS):
                self.defs.setdefault((node.name, node.end_lineno), []).append(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                self.assigns.setdefault(node.lineno, []).append(node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                self.imports.setdefault(node.lineno, []).append(node)
            elif isinstance(node, ast.TypeAlias):
                self.type_aliases[(node.name.id, node.end_lineno)] = node

    def defnode(self, obj) -> ast.AST | None:
        if obj is None or obj.is_alias:
            return None  # an alias's line attributes would resolve it
        candidates = self.defs.get((obj.name, obj.endlineno), [])
        for node in candidates:
            if obj.lineno in (node.lineno, _start_line(node)):
                return node
        return candidates[0] if candidates else None

    def import_stmt(self, line: int, name: str) -> ast.stmt | None:
        """The import statement on `line` that binds `name`, or None."""
        for stmt in self.imports.get(line, []):
            for alias in stmt.names:
                if (alias.asname or alias.name.split(".")[0]) == name:
                    return stmt
        return None


def _start_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", None)
    if decorators:
        return min(d.lineno for d in decorators)
    return node.lineno


# ─────────────────────────────── signatures ────────────────────────────────


def _annotation(node: ast.expr) -> str:
    """R7: `ast.unparse`, except a string literal renders the expression it holds."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            inner = ast.parse(node.value.strip(), mode="eval").body
        except SyntaxError:
            return ast.unparse(node)
        return ast.unparse(inner)
    return ast.unparse(node)


def _param(arg: ast.arg, default: ast.expr | None, prefix: str = "") -> str:
    text = prefix + arg.arg
    if arg.annotation is not None:
        text += ": " + _annotation(arg.annotation)
        if default is not None:
            text += " = " + ast.unparse(default)
    elif default is not None:
        text += "=" + ast.unparse(default)
    return text


def _parameters(args: ast.arguments) -> str:
    parts: list[str] = []
    positional = args.posonlyargs + args.args
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for index, arg in enumerate(positional):
        parts.append(_param(arg, defaults[index]))
        if args.posonlyargs and index == len(args.posonlyargs) - 1:
            parts.append("/")
    if args.vararg is not None:
        parts.append(_param(args.vararg, None, "*"))
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_param(arg, default))
    if args.kwarg is not None:
        parts.append(_param(args.kwarg, None, "**"))
    return ", ".join(parts)


def _type_params(node: ast.AST) -> str:
    params = getattr(node, "type_params", None)
    if not params:
        return ""
    return "[" + ", ".join(ast.unparse(p) for p in params) + "]"


def _decorators(node: ast.AST) -> list[str]:
    return ["@" + ast.unparse(d) for d in getattr(node, "decorator_list", [])]


def _def_lines(node: ast.AST) -> list[str]:
    lines = _decorators(node)
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases]
        bases += [f"{k.arg}={ast.unparse(k.value)}" if k.arg else "**" + ast.unparse(k.value)
                  for k in node.keywords]
        head = f"class {node.name}{_type_params(node)}"
        lines.append(head + (f"({', '.join(bases)})" if bases else ""))
    else:
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        head = f"{prefix} {node.name}{_type_params(node)}({_parameters(node.args)})"
        if node.returns is not None:
            head += " -> " + _annotation(node.returns)
        lines.append(head)
    return lines


# ──────────────────────────── naming and kinds ─────────────────────────────


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _ancestors(obj):
    """Ancestors of `obj` up to (not including) its module, nearest first."""
    out = []
    current = obj.parent
    while current is not None and current.kind is not griffe.Kind.MODULE:
        out.append(current)
        current = current.parent
    return out


def _qualname(obj) -> str:
    """Clause 3: a `<locals>` segment follows every function ancestor."""
    chain = list(reversed(_ancestors(obj))) + [obj]
    segments: list[str] = []
    for index, member in enumerate(chain):
        segments.append(member.name)
        if index < len(chain) - 1 and member.kind is griffe.Kind.FUNCTION:
            segments.append("<locals>")
    return ".".join(segments)


def _function_local(obj) -> bool:
    return any(a.kind is griffe.Kind.FUNCTION for a in _ancestors(obj))


def _kind(obj, node: ast.AST | None) -> str:
    parent = obj.parent
    if obj.is_alias:
        return "alias"
    if obj.kind is griffe.Kind.TYPE_ALIAS:
        return "type alias"
    if obj.kind is griffe.Kind.CLASS:
        return "nested class" if _function_local(obj) else "class"
    if obj.kind is griffe.Kind.ATTRIBUTE:
        if "property" in obj.labels:
            return "property"
        if "instance-attribute" in obj.labels and "class-attribute" not in obj.labels \
                and node is not None and _assigns_to_attribute(node):
            return "instance attribute"
        return "attribute"
    is_async = isinstance(node, ast.AsyncFunctionDef) or "async" in obj.labels
    if parent is not None and parent.kind is griffe.Kind.CLASS:
        if "classmethod" in obj.labels:
            return "class method"
        if "staticmethod" in obj.labels:
            return "static method"
        return "async method" if is_async else "method"
    if parent is not None and parent.kind is griffe.Kind.FUNCTION:
        return "nested async function" if is_async else "nested function"
    return "async function" if is_async else "function"


def _assigns_to_attribute(stmt: ast.AST) -> bool:
    """True for `self.x = ...`: an assignment whose target is an attribute."""
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [getattr(stmt, "target", None)]
    return any(isinstance(t, ast.Attribute) for t in targets)


def _span(start: int, end: int) -> str:
    return f"line {start}" if start == end else f"lines {start}{_EN_DASH}{end}"


# ───────────────────────────── exports / aliases ───────────────────────────


def _redundant_alias(alias, nodes: _Nodes) -> bool:
    """`import x as x` or `from m import y as y` (clause 11)."""
    stmt = nodes.import_stmt(alias.alias_lineno, alias.name)
    if stmt is None:
        return False
    for name in stmt.names:
        bound = name.asname or name.name.split(".")[0]
        if bound == alias.name:
            return name.asname is not None and name.asname == name.name.split(".")[-1] \
                and (isinstance(stmt, ast.ImportFrom) or "." not in name.name)
    return False


def _statement_text(text_lines: list[str], stmt: ast.stmt) -> str:
    """The statement's own source text: from its first to its last token, so a
    trailing comment on its last line is not part of it."""
    # `col_offset` and `end_col_offset` count UTF-8 bytes, not characters,
    # so the slice is taken on the encoded line.
    lines = text_lines[stmt.lineno - 1:stmt.end_lineno]
    lines[-1] = lines[-1].encode("utf-8")[:stmt.end_col_offset].decode("utf-8")
    lines[0] = lines[0].encode("utf-8")[stmt.col_offset:].decode("utf-8")
    return "\n".join(lines)


def _lookup(selection, path: str):
    """The object at dotted `path` inside the selection, or None; never loads."""
    parts = path.split(".")
    collection = selection.collection
    for depth in range(len(parts), 0, -1):
        head = ".".join(parts[:depth])
        # The loader registers each top-of-chain module under one flat key
        # that may itself contain dots (`src.pkg`), so the head is looked up
        # as a key; `get_member` would split it and miss.
        module = collection.members.get(head)
        if module is None or module.is_alias:
            continue
        if depth == len(parts):
            return module
        return _walk_members(module, parts[depth:], module_only_until_last=True)
    return None


class _ImportIndex:
    """The selection's `.py` units by import name (see `_py_load`'s "Import
    roots" section): each unit under its path-derived name and under its
    name relative to its import root. Built from the selected units only."""

    def __init__(self, selection) -> None:
        self.by_name: dict[str, dict[int, object]] = {}
        self.root_of: dict[int, str] = {}
        self.tops: dict[str, set[str]] = {}
        roots = getattr(selection, "import_roots", {})
        root_names = getattr(selection, "root_names", {})
        for source in selection.sources:
            if source.is_stub:
                continue
            module = source.module
            names = [module.path]
            if source.rel_path in roots:
                root = roots[source.rel_path]
                stripped = root_names[source.rel_path]
                names.append(stripped)
                self.root_of[id(module)] = root
                if "." not in stripped:
                    self.tops.setdefault(root, set()).add(stripped)
            for name in names:
                self.by_name.setdefault(name, {})[id(module)] = module


# Every alias lookup on every page needs the import index, so it is built
# once per selection rather than once per lookup. Keyed by id(selection)
# because LoadedSelection is unhashable. Each entry holds the selection
# itself, so its id cannot be reused while cached, and the identity check in
# `_index` rejects any other object. One entry at a time: a run renders one
# selection, and a new selection evicts the old one.
_INDEX_CACHE: dict[int, tuple[object, _ImportIndex]] = {}


def _index(selection) -> _ImportIndex:
    cached = _INDEX_CACHE.get(id(selection))
    if cached is not None and cached[0] is selection:
        return cached[1]
    index = _ImportIndex(selection)
    _INDEX_CACHE.clear()
    _INDEX_CACHE[id(selection)] = (selection, index)
    return index


def _module_of(obj):
    current = obj.parent
    while current is not None and current.kind is not griffe.Kind.MODULE:
        current = current.parent
    return current


def _walk_members(obj, parts: list[str], *, module_only_until_last: bool):
    """Walk `.members[part]` for each of `parts`, in order; None on a miss.

    Never reads `.members` on an Alias: that proxies to the resolved
    target and would make Griffe try to resolve it. When
    `module_only_until_last`, every part except the last must land on a
    genuine Griffe MODULE (a real selected unit's own module object) — the
    walk is standing in for a dotted import chain, where only the final
    segment may be a non-module member (an attribute, class or function).
    """
    last_index = len(parts) - 1
    for index, part in enumerate(parts):
        if obj.is_alias:
            return None
        # An attribute or other leaf object has no `members`; the walk
        # then misses rather than raising.
        members = getattr(obj, "members", None)
        if members is None or part not in members:
            return None
        obj = members[part]
        if module_only_until_last and index != last_index and obj.kind is not griffe.Kind.MODULE:
            return None
    return obj


def _target(selection, alias):
    """The object `alias` names inside the selection, or None; never loads.

    The path-derived lookup (`_lookup`) answers unless the importing module
    sits under an import root other than the repository root and the
    target's first segment is a top-of-chain package under that same root.
    Then every unit whose path-derived or root-stripped name matches a
    prefix of the target is a candidate; a prefix with more than one
    candidate unit is ambiguous (None). A candidate at a depth beyond the
    first counts only when it is the same object reached by walking
    `.members` from the unit matched at the previous depth: import roots
    define which roots exist, and matches combine only by descent. A
    same-named unit that is not that descendant makes the export a gap
    (None). The check can only turn a resolution into a gap, and it reads no
    iteration order: each depth has at most one candidate, or it is already
    ambiguous.
    """
    path = alias.target_path
    index = _index(selection)
    importer = _module_of(alias)
    root = index.root_of.get(id(importer)) if importer is not None else None
    parts = path.split(".")
    if not root or parts[0] not in index.tops.get(root, ()):
        return _lookup(selection, path)
    best: tuple[int, object] | None = None
    for depth in range(1, len(parts) + 1):
        candidates = index.by_name.get(".".join(parts[:depth]), {})
        if len(candidates) > 1:
            return None
        if not candidates:
            continue
        candidate = next(iter(candidates.values()))
        if best is None:
            best = (depth, candidate)
            continue
        prev_depth, prev_obj = best
        walked = _walk_members(prev_obj, parts[prev_depth:depth], module_only_until_last=False)
        if walked is not candidate:
            # The longer match does not descend from the previous depth's
            # unit in the same root: the export is a gap, never a resolution
            # through an unrelated same-named unit.
            return None
        best = (depth, candidate)
    if best is None:
        return None
    depth, obj = best
    if depth == len(parts):
        return obj
    return _walk_members(obj, parts[depth:], module_only_until_last=True)


#: A hop bound independent of the identity-based cycle guard below, so a
#: chain that keeps producing new objects without ever repeating one (a
#: regression in `_target`, not a real cycle) refuses deterministically
#: rather than hanging. No real selection needs anywhere near this many
#: hops: it is one hop per alias in the whole selection, at most.
_MAX_ALIAS_HOPS = 10_000


def _resolves(selection, alias) -> bool:
    """Follow an alias chain inside the selection; False on a miss or a cycle.

    A cycle is detected by alias identity rather than by the dotted target
    text, because one module has more than one spelling (`src.pkg.m`,
    `pkg.m`). Identity keying is chosen for correctness; no current test
    distinguishes it from text keying. `_MAX_ALIAS_HOPS` bounds the walk
    independently, so a chain that never repeats an identity still refuses
    deterministically rather than hanging.
    """
    seen: set[int] = set()
    current = alias
    hops = 0
    while current is not None and current.is_alias:
        if id(current) in seen or hops >= _MAX_ALIAS_HOPS:
            return False
        seen.add(id(current))
        hops += 1
        current = _target(selection, current)
    return current is not None


# ──────────────────────────────── the page ─────────────────────────────────


_REBIND_PROSE = {"for target": "for-loop target"}


def _coarse_context(context: str) -> str:
    roles = set(context.split("/"))
    if context == "unconditional":
        return "unconditional"
    if roles & {"try", "except", "finally"}:
        return "try"
    if "case" in roles:
        return "match"
    return "if"


class _Page:
    def __init__(self, source, selection, include_private: bool) -> None:
        self.source = source
        self.selection = selection
        self.include_private = include_private
        self.text = source.text.replace("\r\n", "\n")
        self.text_lines = self.text.split("\n")
        self.tree = ast.parse(source.text, filename=source.rel_path, feature_version=_GRAMMAR_VERSION)
        self.nodes = _Nodes(self.tree)
        self.notes = _bindings.find_binding_notes(self.tree)
        self.module = source.module
        self.gaps: list[dict] = []
        self.out: list[str] = []
        self.excluded = 0

    # ── helpers ──
    def _add_block(self, text: str) -> None:
        if self.out:
            self.out.append("")
        self.out.append(text)

    def _exports(self) -> list[str] | None:
        """The static `__all__` names Griffe 2.3.0 evaluated, or None.

        None when the module has no `__all__`, and when Griffe could not
        evaluate it statically: an entry Griffe left as an expression, or an
        empty evaluation of a value that is not an empty literal (Griffe 2.3.0
        evaluates `__all__ = compute()` to an empty list).
        """
        exports = self.module.exports
        if exports is None or not self._all_is_static(exports):
            return None
        return list(exports)

    def _all_is_static(self, exports) -> bool:
        if any(not isinstance(e, str) for e in exports):
            return False
        if exports:
            return True
        for stmts in self.nodes.assigns.values():
            for stmt in stmts:
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                    value = stmt.value
                    if not (isinstance(value, (ast.List, ast.Tuple)) and not value.elts):
                        return False
        return True

    def _all_line(self) -> int | None:
        for line, stmts in sorted(self.nodes.assigns.items()):
            for stmt in stmts:
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                    return line
        return None

    # ── sections ──
    def _script_metadata(self) -> None:
        lines = self.text_lines
        start = None
        for index, line in enumerate(lines):
            if line.rstrip() == "# /// script":
                start = index
                break
        if start is None:
            return
        for end in range(start + 1, len(lines)):
            if lines[end].rstrip() == "# ///":
                # PEP 723: the TOML is the block's lines with the leading
                # "# " (or a bare "#") removed, between the delimiter lines.
                block = "\n".join(
                    ln[2:] if ln.startswith("# ") else ln[1:] for ln in lines[start + 1:end])
                self._add_block("## Script metadata")
                self._add_block(_render.fence(block, "toml"))
                return

    def _export_section(self) -> None:
        exports = self._exports()
        all_line = self._all_line()
        bullets: list[str] = []
        for stmt in sorted((s for line in self.nodes.imports.values() for s in line),
                           key=lambda s: (s.lineno, s.col_offset)):
            if isinstance(stmt, ast.ImportFrom) and any(a.name == "*" for a in stmt.names) \
                    and id(stmt) in self.nodes.top_level:
                text = _statement_text(self.text_lines, stmt)
                bullets.append(
                    f"- Gap: {_render.code_span(text)} (line {stmt.lineno}) is a wildcard import; "
                    "its names are not expanded.")
                self._gap("wildcard_import", text, stmt.lineno)
        if exports is None and self.module.exports is not None:
            bullets.append(
                f"- Gap: {_render.code_span('__all__')} (line {all_line}) is not statically "
                "resolvable; no export list is rendered.")
            self._gap("unresolvable_all", "__all__", all_line)
        if exports:
            for name in exports:
                member = self.module.members.get(name)
                ok = member is not None and (not member.is_alias or _resolves(self.selection, member))
                if not ok:
                    line = member.alias_lineno if member is not None and member.is_alias else all_line
                    bullets.append(
                        f"- Gap: {_render.code_span(name)} (line {line}) is exported but does not "
                        "resolve inside the selection.")
                    self._gap("unresolved_export", name, line)
        # Clause 11: a redundant-alias import is an export too, and one whose
        # target does not resolve inside the selection is the same gap.
        listed = set(exports or [])
        for member in sorted(self.module.members.values(),
                             key=lambda m: (m.alias_lineno if m.is_alias else 0, m.name)):
            if member.is_alias and member.name not in listed \
                    and _redundant_alias(member, self.nodes) and not _resolves(self.selection, member):
                bullets.append(
                    f"- Gap: {_render.code_span(member.name)} (line {member.alias_lineno}) is "
                    "exported but does not resolve inside the selection.")
                self._gap("unresolved_export", member.name, member.alias_lineno)
        if exports is None and not bullets:
            return
        self._add_block("## Exports")
        if exports:
            self._add_block(", ".join(_render.code_span(n) for n in exports))
        if bullets:
            self._add_block("\n".join(bullets))

    def _gap(self, kind: str, name: str, line: int | None, **extra) -> None:
        entry = {
            "kind": kind, "qualified_name": name, "line": line,
            "shadowed_kind": None, "shadowed_line": None,
            "retained_kind": None, "retained_line": None, "branch_context": None,
        }
        entry.update(extra)
        self.gaps.append(entry)

    # ── members ──
    def _visible(self, obj) -> bool:
        if obj.is_alias:
            exports = self._exports() or []
            if obj.name in exports:
                return _resolves(self.selection, obj)  # an unresolved export is a gap only
            return _redundant_alias(obj, self.nodes) and _resolves(self.selection, obj)
        if obj.kind is griffe.Kind.MODULE:
            return False  # a submodule has its own page
        if obj.name == "__all__" and obj.parent is self.module:
            return False
        return True

    def _private_excluded(self, obj) -> bool:
        return (not self.include_private) and (_is_private(obj.name) or _function_local(obj))

    def _start(self, obj) -> int:
        if obj.is_alias:
            return obj.alias_lineno
        node = self.nodes.defnode(obj)
        if node is not None:
            if obj.kind is griffe.Kind.FUNCTION and obj.overloads:
                first = self.nodes.defnode(obj.overloads[0])
                if first is not None:
                    return _start_line(first)
            return _start_line(node)
        return obj.lineno

    def _end(self, obj) -> int:
        if obj.is_alias:
            return obj.alias_endlineno
        end = obj.endlineno
        for accessor in (getattr(obj, "setter", None), getattr(obj, "deleter", None)):
            if accessor is not None:
                end = max(end, accessor.endlineno)
        return end

    def _children(self, obj) -> list:
        members = [m for m in obj.members.values() if m.is_alias or self._visible(m)]
        return sorted(members, key=lambda m: (self._start(m), m.name))

    def _signature(self, obj) -> str:
        if obj.is_alias:
            stmt = self.nodes.import_stmt(obj.alias_lineno, obj.name)
            if stmt is not None:
                return _statement_text(self.text_lines, stmt)
            return obj.name
        node = self.nodes.defnode(obj)
        if node is not None:
            lines: list[str] = []
            if obj.kind is griffe.Kind.FUNCTION and obj.overloads:
                for variant in obj.overloads:
                    vnode = self.nodes.defnode(variant)
                    if vnode is not None:
                        lines += _def_lines(vnode)
            lines += _def_lines(node)
            for accessor in (getattr(obj, "setter", None), getattr(obj, "deleter", None)):
                anode = self.nodes.defnode(accessor) if accessor is not None else None
                if anode is not None:
                    lines += _def_lines(anode)
            return "\n".join(lines)
        ta = self.nodes.type_aliases.get((obj.name, obj.endlineno))
        if ta is not None:
            return f"type {obj.name}{_type_params(ta)} = {ast.unparse(ta.value)}"
        return self._attribute_signature(obj)

    def _line_assigns(self, obj) -> list[ast.stmt]:
        """The assignment statements on `obj`'s line, binding ones first.

        Griffe creates an attribute only from a binding (`=` or an annotated
        assignment), never from `+=`; an augmented assignment sharing the
        line (`x += 1; x = 2`) must not stand in for the binding.
        """
        stmts = self.nodes.assigns.get(obj.lineno, [])
        return sorted(stmts, key=lambda stmt: isinstance(stmt, ast.AugAssign))

    def _assign_node(self, obj) -> ast.stmt | None:
        if obj.is_alias or obj.kind is not griffe.Kind.ATTRIBUTE:
            return None
        for stmt in self._line_assigns(obj):
            return stmt
        return None

    def _attribute_signature(self, obj) -> str:
        for stmt in self._line_assigns(obj):
            if isinstance(stmt, ast.AnnAssign):
                text = f"{ast.unparse(stmt.target)}: {_annotation(stmt.annotation)}"
                if stmt.value is not None:
                    text += " = " + ast.unparse(stmt.value)
                return text
            if isinstance(stmt, ast.Assign):
                return " = ".join(ast.unparse(t) for t in stmt.targets) + " = " + ast.unparse(stmt.value)
            if isinstance(stmt, ast.AugAssign):
                return ast.unparse(stmt)
        text = obj.name
        if obj.annotation is not None:
            text += f": {obj.annotation}"
        if obj.value is not None:
            text += f" = {obj.value}"
        return text

    def _docstring(self, obj) -> str:
        docstring = None
        if obj.is_alias:
            return _render.render_docstring(None)
        candidates = [obj]
        if obj.kind is griffe.Kind.FUNCTION and obj.overloads:
            candidates += list(obj.overloads)
        for candidate in candidates:
            node = self.nodes.defnode(candidate)
            raw = ast.get_docstring(node, clean=False) if node is not None else None
            if raw is None and candidate.docstring is not None and (
                    candidate.kind is not griffe.Kind.ATTRIBUTE
                    or (candidate.docstring.lineno or 0) > candidate.endlineno):
                # An attribute's docstring is the string statement after it.
                # Griffe 2.3.0 carries a replaced def's docstring onto the
                # attribute that rebinds it; that text is not the attribute's.
                raw = candidate.docstring.value
            if raw is not None:
                docstring = raw
                break
        return _render.render_docstring(docstring)

    def _notes_for(self, obj, qualname: str) -> list[str]:
        lines: list[str] = []
        kind = _kind(obj, self.nodes.defnode(obj))
        for note in self.notes:
            if note.qualified_name != qualname:
                continue
            context = _coarse_context(note.branch_context)
            if note.note_kind == "duplicate":
                lines.append(
                    f"_Gap: {_render.code_span(qualname)} at {context} rebinds "
                    f"`{self._note_kind(note.shadowed_kind, obj)}` (line {note.shadowed_line}); "
                    f"`{kind}` (line {note.retained_line}) is documented._")
                self._gap("duplicate_binding", qualname, note.retained_line,
                          shadowed_kind=self._note_kind(note.shadowed_kind, obj),
                          shadowed_line=note.shadowed_line, retained_kind=kind,
                          retained_line=note.retained_line, branch_context=context)
            else:
                original = self._note_kind(note.shadowed_kind, obj)
                prose = _REBIND_PROSE.get(note.retained_kind, note.retained_kind)
                if not obj.is_alias and obj.kind in (griffe.Kind.FUNCTION, griffe.Kind.CLASS):
                    tail = "and stays documented above; the rebinding is not reconstructed._"
                else:
                    tail = "and is not reconstructed; the rebinding is documented above._"
                lines.append(
                    f"_Gap: {_render.code_span(qualname)} at {context} ({original}, line "
                    f"{note.shadowed_line}) is rebound by {prose} at line {note.retained_line} {tail}")
                self._gap("rebinding", qualname, note.retained_line, shadowed_kind=original,
                          shadowed_line=note.shadowed_line,
                          retained_kind=note.retained_kind.replace(" ", "_"),
                          retained_line=note.retained_line, branch_context=context)
        return lines

    def _note_kind(self, binding_kind: str, obj) -> str:
        nested = _function_local(obj) or (obj.parent is not None
                                          and obj.parent.kind is griffe.Kind.FUNCTION)
        in_class = obj.parent is not None and obj.parent.kind is griffe.Kind.CLASS
        if binding_kind == "class":
            return "nested class" if nested else "class"
        is_async = binding_kind.startswith("async")
        if in_class:
            return "async method" if is_async else "method"
        if nested:
            return "nested async function" if is_async else "nested function"
        return binding_kind

    def _orphan(self, obj) -> None:
        """A rebinding whose own member renders nothing (a non-exported import)."""
        qualname = _qualname(obj)
        for note in self.notes:
            if note.qualified_name != qualname or note.note_kind != "rebinding":
                continue
            original = self._note_kind(note.shadowed_kind, obj)
            prose = _REBIND_PROSE.get(note.retained_kind, note.retained_kind)
            context = _coarse_context(note.branch_context)
            self._add_block(
                f"- Gap: {_render.code_span(qualname)} at {context} ({original}, line "
                f"{note.shadowed_line}) is rebound by {prose} at line {note.retained_line}, which renders no member "
                f"(not exported); the original {original} is not reconstructed.")
            self._gap("rebinding", qualname, note.retained_line, shadowed_kind=original,
                      shadowed_line=note.shadowed_line,
                      retained_kind=note.retained_kind.replace(" ", "_"),
                      retained_line=note.retained_line,
                      branch_context=_coarse_context(note.branch_context))

    def _member(self, obj, depth: int) -> None:
        if obj.is_alias and not self._visible(obj):
            self._orphan(obj)
            return
        if self._private_excluded(obj):
            self.excluded += 1
            return
        qualname = _qualname(obj)
        level = min(depth, _MAX_HEADING_LEVEL)
        node = None if obj.is_alias else (self.nodes.defnode(obj) or self._assign_node(obj))
        visibility = "private" if _is_private(obj.name) else "public"
        self._add_block("#" * level + " " + _render.code_span(qualname))
        self._add_block(_render.fence(self._signature(obj), "python"))
        meta = f"_{_kind(obj, node)} · {visibility} · {_span(self._start(obj), self._end(obj))}_"
        notes = self._notes_for(obj, qualname)
        self._add_block("\n\n".join([meta] + notes))
        self._add_block(self._docstring(obj))
        if not obj.is_alias and obj.kind in (griffe.Kind.CLASS, griffe.Kind.FUNCTION):
            for child in self._children(obj):
                self._member(child, depth + 1)

    # ── assembly ──
    def render(self) -> PageResult:
        rel = self.source.rel_path
        title = _render.visible_escapes(_render.md_escape_inline(rel))
        self.out.append("# " + title)
        doc = self.module.docstring.value if self.module.docstring is not None else None
        tree_doc = ast.get_docstring(self.tree, clean=False)
        if tree_doc is not None:
            doc = tree_doc
        if doc is not None:
            self._add_block(_render.render_docstring(doc))
        self._script_metadata()
        count_at = len(self.out)
        self._export_section()
        for child in self._children(self.module):
            self._member(child, 2)
        if self.excluded:
            line = (f"_Private declarations not rendered: {self.excluded} "
                    "(include_private is false)._")
            insert = ["", line]
            self.out[count_at:count_at] = insert
        body = "\n".join(self.out).rstrip("\n") + "\n"
        return PageResult(doc_path=f"python/{rel}.md", title=title, body=body, gaps=self.gaps)


def render_page(source, selection, *, include_private: bool) -> PageResult:
    """Render one loaded source as its page (see the module docstring)."""
    return _Page(source, selection, include_private).render()
