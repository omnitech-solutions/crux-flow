"""Function-local declarations Griffe extension (ADR-0131 clauses 3 and 8).

Requires `griffelib==2.3.0` (see the dispatcher's PEP 723 block, which
declares the pin this file relies on). Loaded by path, like every module
under `extractors/`: no sibling imports, so this file stays independently
loadable by importlib.

`FunctionLocalDefs` re-enters Griffe's own `Visitor` on the `def`/`class`
statements Griffe's own AST walk does not descend into: those nested inside
a function's own scope. It contributes no parser and no rendering; it only
tells Griffe's visitor where to look next.

Derived from the measured prototype (ADR-0131's footnoted measurement: 297 of
297 function-local declarations found on the Crux corpus, no target
execution). This file:

- replaces the prototype's bare `griffe.ExtensionError` on unattached
  overloads with the named `LocalPlacementError`, so a node this extension
  cannot place never surfaces as a bare Griffe error;
- exposes `EXTENSION_VERSION` for the provenance block (clause 13). The
  page renderer owns the qualified-name and start-line rules (clauses 3 and
  8); this module only finds the declarations.
"""

from __future__ import annotations

import ast
from collections import defaultdict

import griffe

EXTENSION_VERSION = "1"

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


class LocalPlacementError(Exception):
    """A function-local `def`/`class` node this extension could not place.

    Never a bare `griffe.ExtensionError` (or any other Griffe exception):
    the composing extractor's refusal path (ADR-0131 clause 7) names this
    class specifically so a placement failure is distinguishable from a
    Griffe-internal error.
    """

    def __init__(self, qualified_path: str, line: int, reason: str) -> None:
        self.qualified_path = qualified_path
        self.line = line
        self.reason = reason
        super().__init__(f"{qualified_path} (line {line}): {reason}")


def _scoped_defs(fn_node: ast.AST):
    """Yield `def`/`class` nodes whose nearest enclosing scope is `fn_node`.

    Walks control-flow blocks (`if`/`try`/`with`/`for`/`while`/`match`) but
    never descends into another `def`, `class` or `lambda`: those scopes are
    reached when Griffe's own `Visitor` visits the yielded node, re-firing
    this extension's hook for them in turn.
    """
    stack = list(reversed(fn_node.body))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node
        elif isinstance(node, ast.Lambda):
            continue
        else:
            stack.extend(reversed(list(ast.iter_child_nodes(node))))


class FunctionLocalDefs(griffe.Extension):
    """Register function-local `def`/`class` statements as members of their `Function`."""

    def on_function_instance(self, *, node, func, agent, **kwargs) -> None:
        if not isinstance(node, _FUNCS):  # Inspector path: no AST, nothing to do
            return
        # Griffe re-enters a class __init__'s own body (to collect self.x
        # attributes): self.current is set to the Function and
        # generic_visit walks the body again. A nested def in __init__ is
        # therefore placed by Griffe's own visitor, and visiting it here too
        # would register it twice.
        if func.name == "__init__" and func.parent is not None and func.parent.kind is griffe.Kind.CLASS:
            # Griffe's own re-entry files an @overload variant nested in
            # __init__ under `self.current.overloads[name]` — the same
            # dict-valued filing this extension already gives every other
            # function-local scope. Give `__init__` a dict too, for the
            # duration of Griffe's own re-entry, so that filing never hits
            # Function.overloads's plain `None` default. on_class_members
            # attaches the collected variants once the class body is done,
            # because no hook fires right after Griffe's own re-entry ends.
            # Griffe has already transferred a class-level `@overload` chain
            # on `__init__` itself to `func.overloads` before this hook fires;
            # keep it aside so on_class_members restores it.
            func.extra["crux_locals"]["griffe_overloads"] = func.overloads
            func.overloads = defaultdict(list)
            return

        saved_current, saved_overloads = agent.current, func.overloads
        # Griffe's own visitor files an @overload variant under
        # current.overloads[name] (a dict on Module/Class, so it
        # auto-transfers to the implementation there; Function.overloads is
        # a plain list-or-None with no such transfer). Give the Function a
        # dict for the duration of this body visit so the same filing logic
        # works, then attach the collected variants ourselves afterward.
        collected: dict[str, list] = defaultdict(list)
        func.overloads = collected
        agent.current = func
        try:
            for child in _scoped_defs(node):
                agent.visit(child)
        finally:
            agent.current = saved_current
            func.overloads = saved_overloads

        for name, variants in collected.items():
            impl = func.members.get(name)
            if impl is not None and not impl.is_alias and impl.kind is griffe.Kind.FUNCTION:
                impl.overloads = variants
            else:
                raise LocalPlacementError(
                    qualified_path=f"{func.path}.{name}",
                    line=variants[0].lineno if variants else func.lineno,
                    reason="overload variants with no matching implementation",
                )

    def on_class_members(self, *, cls, **kwargs) -> None:
        """Attach `__init__`-nested overload variants once its class is done.

        Fires after Griffe's own `generic_visit` of the whole class body,
        which is also when Griffe's own re-entry of `__init__` (triggered by
        `on_function_instance` above) has finished. `__init__.overloads` is
        a dict only when this extension put one there; every other function
        keeps Griffe's own `list | None` value and is left alone.
        """
        init = cls.members.get("__init__")
        if init is None or init.is_alias or init.kind is not griffe.Kind.FUNCTION:
            return
        overloads = init.overloads
        if not isinstance(overloads, dict):
            return
        init.overloads = init.extra.get("crux_locals", {}).pop("griffe_overloads", None)
        for name, variants in overloads.items():
            if not variants:
                continue
            impl = init.members.get(name)
            if impl is not None and not impl.is_alias and impl.kind is griffe.Kind.FUNCTION:
                impl.overloads = variants
            else:
                raise LocalPlacementError(
                    qualified_path=f"{init.path}.{name}",
                    line=variants[0].lineno,
                    reason="overload variants with no matching implementation",
                )
