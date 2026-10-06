"""Duplicate-binding and rebinding detection for Python source (ADR-0131 clause 12).

Stdlib `ast` only. Read-only: this module never imports or executes the
source it inspects, and never mutates the tree it is given. Loaded by path,
like every module under `extractors/`: no sibling imports, so this file is
importable on its own with importlib.

## What a duplicate binding is

Two or more `def`/`class` statements binding one name in one scope (module,
class or function). The final one in source order is the page's retained
member (the one Griffe, or the function-local extension, actually builds);
each earlier one is "shadowed" and gets its own gap note pointing at the
retained member. An `@overload` chain (`typing.overload` / `overload` /
`t.overload`) and an accessor chain (`@<name>.setter` / `.getter` /
`.deleter` sharing the def's own name) are not duplicates — they are
excluded from consideration entirely, per clause 12.

## What a rebinding is

A LATER binding operation, in the same scope, that binds the name of a
`def`/`class` and is not itself a `def`/`class` statement. The forms this
module recognizes, each mapped to the kind vocabulary below:

- **assignment** — `Assign` (simple, tuple/list-unpacking, and
  multi-target), `AugAssign`, an `AnnAssign` that carries a value (an
  annotation with no value is not a binding), and a walrus (`NamedExpr`)
  target. A walrus is scanned in every expression child of every
  non-`def`/`class` statement itself: an `Assign`/`AnnAssign`/`AugAssign`'s
  value, an `If`/`While`'s test, a `Return`/`Expr`'s value, a
  `With`/`AsyncWith` item's `context_expr`, a `Match`'s subject, a `case`'s
  guard, and an `Assert`'s or `Raise`'s operands. The scan passes
  transparently through a comprehension — nested comprehensions included —
  because a walrus target inside one binds in the nearest enclosing
  non-comprehension scope, per PEP 572. It never passes into a `lambda`'s
  own body or a nested statement's own body (a `def`, `class`, or `case`),
  each a separate scope walked separately. A walrus in a `def`/`class`
  statement's own expressions (a decorator, a default value, a base or a
  keyword) is not scanned and produces no note.
- **import** — `Import` and `ImportFrom`, with or without `asname`. A
  wildcard `from m import *` binds no single name this pass can name, and
  is skipped (clause 11 covers wildcard imports separately).
- **for target** — the loop variable of `For`/`AsyncFor`.
- **with target** — the `as` target of a `With`/`AsyncWith` item.
- **except target** — the `as` name of an `except` clause.
- **del** — a `Delete` target, per the Python language reference's binding
  list ("Naming and binding" section 6.2).
- **match capture** — a capture name in a `match`/`case` pattern (`MatchAs`,
  `MatchStar`, or a mapping pattern's `**rest`).

`global`/`nonlocal` declarations are explicitly not bindings, and produce no
event: they only change which scope a later binding in the same statement
list targets, and this pass does not resolve globals across scopes.

A binding operation that textually precedes the `def`/`class` it would
otherwise rebind is out of scope for a note: it is dead by the time the
`def`/`class` executes, and the `def`/`class` is what a reader sees.

Two further behaviors follow from the grouping `_scope_notes` does: only
the FIRST rebinding after the retained `def`/`class` gets a note (a later
rebinding rebinds the rebinding, not the `def`/`class`); and `def f`, then
`f = 1`, then `def f` again in one scope yields only the duplicate note
between the two `def`s, because the interceding assignment sits before the
final retained `def` in source order, which puts it out of scope for a
rebinding note under the rule above.

## Branch context grammar

Each note carries a `branch_context` string describing where its shadowed
and retained bindings sit, deterministically:

- `"unconditional"` — neither binding sits inside an `if`/`elif`/`else`,
  `try`/`except`/`else`/`finally`, or `match`/`case` block relative to its
  enclosing scope.
- Otherwise, the distinct branch-construct role labels touched by the two
  bindings, joined with `"/"`, in this canonical order:
  `if`, `elif`, `else`, `try`, `except`, `finally`, `case`. A `with`,
  `for` or `while` block is not a branch construct for this purpose (its
  body always runs once control reaches it), so it contributes no role
  label; a binding inside one still counts as unconditional unless an
  enclosing `if`/`try`/`match` also wraps it.

`try`'s own `else` clause and `if`'s `else` clause share the label
`"else"` — the two constructs cannot both apply to one binding, so this is
not ambiguous in a rendered note.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
DEFCLASS = FUNCS + (ast.ClassDef,)

_OVERLOAD_DECORATOR_NAMES = {"overload", "typing.overload", "t.overload", "typing_extensions.overload"}
_ACCESSOR_SUFFIXES = {"setter", "getter", "deleter"}

_BRANCH_ROLE_ORDER = ("if", "elif", "else", "try", "except", "finally", "case")

_STMT_SCOPE_BOUNDARY = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
)


@dataclass(frozen=True)
class BindingNote:
    """One duplicate-binding or rebinding gap note (ADR-0131 clause 12)."""

    qualified_name: str
    shadowed_kind: str
    shadowed_line: int
    retained_kind: str
    retained_line: int
    branch_context: str
    note_kind: str  # "duplicate" | "rebinding"


@dataclass
class _Event:
    name: str
    kind: str
    line: int
    branch_path: tuple[str, ...]
    node: ast.AST | None = None
    is_defclass: bool = False


def _dotted_decorator_name(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Call):
        expr = expr.func
    parts: list[str] = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
        return ".".join(reversed(parts))
    return None


def _is_overload_decorated(node: ast.AST) -> bool:
    for dec in getattr(node, "decorator_list", []):
        name = _dotted_decorator_name(dec)
        if name in _OVERLOAD_DECORATOR_NAMES:
            return True
    return False


def _is_accessor_decorated(node: ast.AST) -> bool:
    """True when `node` is `@<own-name>.setter`/`.getter`/`.deleter`."""
    own_name = getattr(node, "name", None)
    for dec in getattr(node, "decorator_list", []):
        if isinstance(dec, ast.Attribute) and dec.attr in _ACCESSOR_SUFFIXES:
            if isinstance(dec.value, ast.Name) and dec.value.id == own_name:
                return True
    return False


def _iter_walrus_transparent(node: ast.AST):
    """Descend through everything but a nested `def`/`class`/`lambda`/`case`.

    A comprehension is deliberately transparent (never stopped at): per
    PEP 572, a walrus target inside one binds in the nearest enclosing
    non-comprehension scope, not the comprehension's own, so a `NamedExpr`
    nested arbitrarily deep in comprehensions must still surface here.
    """
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.stmt, *_STMT_SCOPE_BOUNDARY)):
            continue  # a nested statement, walked separately by the caller
        yield from _iter_walrus_transparent(child)


def _direct_walrus_events(stmt: ast.stmt, branch_path: tuple[str, ...]) -> list[_Event]:
    """Every walrus binding in every expression child of `stmt` itself.

    Scans each of `stmt`'s own direct children (its test, value, items,
    guard, and so on), transparently through nested expressions and
    comprehensions, but never into a nested `def`/`class` (a separate
    statement, walked by the caller) or a `lambda`'s own body (a separate
    scope).
    """
    events: list[_Event] = []
    for child in ast.iter_child_nodes(stmt):
        if isinstance(child, (ast.stmt, ast.Lambda)):
            continue  # a nested statement or a lambda's own scope
        for node in _iter_walrus_transparent(child):
            if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                events.append(_Event(node.target.id, "assignment", node.lineno, branch_path))
    return events


def _target_names(target: ast.expr) -> list[ast.Name]:
    """Name nodes a single assignment/for/with target binds (recursively)."""
    if isinstance(target, ast.Name):
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[ast.Name] = []
        for elt in target.elts:
            out.extend(_target_names(elt))
        return out
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []  # Attribute / Subscript targets do not bind a name


def _match_capture_names(pattern: ast.pattern) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if isinstance(pattern, ast.MatchAs):
        if pattern.name:
            out.append((pattern.name, pattern.lineno))
        if pattern.pattern is not None:
            out.extend(_match_capture_names(pattern.pattern))
    elif isinstance(pattern, ast.MatchStar):
        if pattern.name:
            out.append((pattern.name, pattern.lineno))
    elif isinstance(pattern, ast.MatchOr):
        for p in pattern.patterns:
            out.extend(_match_capture_names(p))
    elif isinstance(pattern, ast.MatchSequence):
        for p in pattern.patterns:
            out.extend(_match_capture_names(p))
    elif isinstance(pattern, ast.MatchMapping):
        for p in pattern.patterns:
            out.extend(_match_capture_names(p))
        if pattern.rest:
            out.append((pattern.rest, pattern.lineno))
    elif isinstance(pattern, ast.MatchClass):
        for p in list(pattern.patterns) + list(pattern.kwd_patterns):
            out.extend(_match_capture_names(p))
    return out


def _stmt_events(stmt: ast.stmt, branch_path: tuple[str, ...]) -> list[_Event]:
    """Binding events directly produced by `stmt` (not its nested body)."""
    if isinstance(stmt, ast.FunctionDef):
        return [_Event(stmt.name, "function", stmt.lineno, branch_path, stmt, True)]
    if isinstance(stmt, ast.AsyncFunctionDef):
        return [_Event(stmt.name, "async function", stmt.lineno, branch_path, stmt, True)]
    if isinstance(stmt, ast.ClassDef):
        return [_Event(stmt.name, "class", stmt.lineno, branch_path, stmt, True)]

    events: list[_Event] = []
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            for name_node in _target_names(target):
                events.append(_Event(name_node.id, "assignment", name_node.lineno, branch_path))
    elif isinstance(stmt, ast.AugAssign):
        for name_node in _target_names(stmt.target):
            events.append(_Event(name_node.id, "assignment", stmt.lineno, branch_path))
    elif isinstance(stmt, ast.AnnAssign):
        if stmt.value is not None:
            for name_node in _target_names(stmt.target):
                events.append(_Event(name_node.id, "assignment", stmt.lineno, branch_path))
    elif isinstance(stmt, ast.Import):
        for alias in stmt.names:
            bound = alias.asname or alias.name.split(".")[0]
            events.append(_Event(bound, "import", stmt.lineno, branch_path))
    elif isinstance(stmt, ast.ImportFrom):
        for alias in stmt.names:
            if alias.name == "*":
                continue
            bound = alias.asname or alias.name
            events.append(_Event(bound, "import", stmt.lineno, branch_path))
    elif isinstance(stmt, (ast.For, ast.AsyncFor)):
        for name_node in _target_names(stmt.target):
            events.append(_Event(name_node.id, "for target", stmt.lineno, branch_path))
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        for item in stmt.items:
            if item.optional_vars is not None:
                for name_node in _target_names(item.optional_vars):
                    events.append(_Event(name_node.id, "with target", stmt.lineno, branch_path))
    elif isinstance(stmt, ast.Delete):
        for target in stmt.targets:
            for name_node in _target_names(target):
                events.append(_Event(name_node.id, "del", stmt.lineno, branch_path))
    elif isinstance(stmt, ast.Match):
        for case in stmt.cases:
            for name, line in _match_capture_names(case.pattern):
                events.append(_Event(name, "match capture", line, branch_path + ("case",)))
    # global/nonlocal, pass, and every other statement bind no name of its
    # own here — but every statement's expression children, walrus targets
    # included, are scanned below regardless of which branch above ran.

    events.extend(_direct_walrus_events(stmt, branch_path))
    return events


def _walk_if(stmt: ast.If, branch_path: tuple[str, ...], first: bool, out: list[_Event]) -> None:
    role = "if" if first else "elif"
    out.extend(_stmt_events(stmt, branch_path))
    _walk_stmts(stmt.body, branch_path + (role,), out)
    is_real_elif = (
        len(stmt.orelse) == 1
        and isinstance(stmt.orelse[0], ast.If)
        and stmt.orelse[0].col_offset == stmt.col_offset
    )
    if is_real_elif:
        _walk_if(stmt.orelse[0], branch_path, False, out)
    elif stmt.orelse:
        # Either a real `else:` body, or an `else:` whose body happens to
        # be a single nested `if` at a deeper column — not a real `elif`,
        # so it is walked as `else` and, for the nested-if case, its own
        # fresh `if` chain starts one level under that "else".
        _walk_stmts(stmt.orelse, branch_path + ("else",), out)


def _walk_stmts(stmts: list[ast.stmt], branch_path: tuple[str, ...], out: list[_Event]) -> None:
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            _walk_if(stmt, branch_path, True, out)
            continue
        out.extend(_stmt_events(stmt, branch_path))
        if isinstance(stmt, DEFCLASS):
            continue  # nested scope; the caller recurses into it separately
        if isinstance(stmt, (ast.Try, ast.TryStar)):
            _walk_stmts(stmt.body, branch_path + ("try",), out)
            for handler in stmt.handlers:
                handler_path = branch_path + ("except",)
                if handler.name:
                    out.append(_Event(handler.name, "except target", handler.lineno, handler_path))
                _walk_stmts(handler.body, handler_path, out)
            if stmt.orelse:
                _walk_stmts(stmt.orelse, branch_path + ("else",), out)
            if stmt.finalbody:
                _walk_stmts(stmt.finalbody, branch_path + ("finally",), out)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            _walk_stmts(stmt.body, branch_path, out)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            _walk_stmts(stmt.body, branch_path, out)
            _walk_stmts(stmt.orelse, branch_path, out)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                _walk_stmts(case.body, branch_path + ("case",), out)


def _branch_context(a: tuple[str, ...], b: tuple[str, ...]) -> str:
    roles = set(a) | set(b)
    if not roles:
        return "unconditional"
    ordered = [role for role in _BRANCH_ROLE_ORDER if role in roles]
    return "/".join(ordered)


def _scope_notes(events: list[_Event], qual_prefix: str) -> list[BindingNote]:
    notes: list[BindingNote] = []
    by_name: dict[str, list[_Event]] = {}
    for ev in events:
        by_name.setdefault(ev.name, []).append(ev)

    for name, evs in by_name.items():
        qualified = qual_prefix + name
        defclass_evs = [e for e in evs if e.is_defclass]
        chain_exempt = [
            e for e in defclass_evs if _is_overload_decorated(e.node) or _is_accessor_decorated(e.node)
        ]
        real_defclass = [e for e in defclass_evs if e not in chain_exempt]

        if len(real_defclass) >= 2:
            retained = real_defclass[-1]
            for shadowed in real_defclass[:-1]:
                notes.append(
                    BindingNote(
                        qualified_name=qualified,
                        shadowed_kind=shadowed.kind,
                        shadowed_line=shadowed.line,
                        retained_kind=retained.kind,
                        retained_line=retained.line,
                        branch_context=_branch_context(shadowed.branch_path, retained.branch_path),
                        note_kind="duplicate",
                    )
                )
        elif len(real_defclass) == 1:
            retained = real_defclass[0]
        else:
            retained = None

        if retained is not None:
            retained_index = evs.index(retained)
            for ev in evs[retained_index + 1 :]:
                if ev.is_defclass:
                    continue  # a later def/class is handled by its own group's shadowing, not here
                notes.append(
                    BindingNote(
                        qualified_name=qualified,
                        shadowed_kind=retained.kind,
                        shadowed_line=retained.line,
                        retained_kind=ev.kind,
                        retained_line=ev.line,
                        branch_context=_branch_context(retained.branch_path, ev.branch_path),
                        note_kind="rebinding",
                    )
                )
                break  # only the first rebinding after the retained def/class

    return notes


def _walk_scope(body: list[ast.stmt], qual_prefix: str, notes: list[BindingNote]) -> None:
    events: list[_Event] = []
    _walk_stmts(body, (), events)
    notes.extend(_scope_notes(events, qual_prefix))

    # Recurse into each def/class's own body as a nested scope. Only the
    # final (retained) def/class of a duplicate-bound name is structurally
    # reachable, so recurse into the *last* defclass event per name.
    last_defclass_by_name: dict[str, _Event] = {}
    for ev in events:
        if ev.is_defclass:
            last_defclass_by_name[ev.name] = ev
    for name, ev in last_defclass_by_name.items():
        if isinstance(ev.node, ast.ClassDef):
            _walk_scope(ev.node.body, qual_prefix + name + ".", notes)
        else:
            _walk_scope(ev.node.body, qual_prefix + name + ".<locals>.", notes)


def find_binding_notes(tree: ast.Module) -> list[BindingNote]:
    """Read-only pass over `tree` producing duplicate/rebinding gap notes.

    Deterministically ordered by `(retained_line, qualified_name)`.
    """
    notes: list[BindingNote] = []
    _walk_scope(tree.body, "", notes)
    notes.sort(key=lambda n: (n.retained_line, n.qualified_name))
    return notes
