"""Tests for crux/scripts/extractors/_py_locals.py (U12, ADR-0131 clause 3).

Requires griffelib==2.3.0 (declared in pyproject.toml/uv.lock; resolved by
`uv run`). Loaded by path — see the U15a test module's header for the
loading-by-path rationale, shared here.

Every `griffe.visit` call here constructs `griffe.Extensions(...)` directly
and never calls `griffe.load_extensions`, which would register a built-in
extension this unit does not own.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import griffe

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LOCALS_PATH = REPO_ROOT / "crux" / "scripts" / "extractors" / "_py_locals.py"


def _load_locals():
    spec = importlib.util.spec_from_file_location("crux_py_locals_t", LOCALS_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crux_py_locals_t"] = mod
    spec.loader.exec_module(mod)
    return mod


locals_mod = _load_locals()


def _load_page():
    # The start-line and qualified-name rules are the page renderer's; the
    # helper tests below exercise the live helpers rather than a copy.
    scripts = REPO_ROOT / "crux" / "scripts"
    for name, path in (
        ("extract_code_docs_dispatcher", scripts / "extract-code-docs.py"),
        ("crux_py_page_locals_t", scripts / "extractors" / "_py_page.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


page_mod = _load_page()


_COUNTER = [0]


def _visit(source: str):
    _COUNTER[0] += 1
    ext = griffe.Extensions(locals_mod.FunctionLocalDefs())
    return griffe.visit(f"m_{_COUNTER[0]}", Path(f"m_{_COUNTER[0]}.py"), source, extensions=ext)


def _resolve(mod, qualname: str):
    """Resolve a _qualname-style path (with `<locals>` segments) against
    Griffe's raw member tree, which carries no `<locals>` segments itself —
    those are purely a Crux-side display convention (see _py_page._qualname)."""
    obj = mod
    for part in qualname.split("."):
        if part == "<locals>":
            continue
        members = getattr(obj, "members", None)
        if members is None or part not in members:
            return None
        obj = members[part]
    return obj


class NestedOverloadTests(unittest.TestCase):
    def test_overload_variants_attach_to_implementation(self):
        src = (
            "from typing import overload\n\n\n"
            "def with_overload():\n"
            "    @overload\n"
            "    def ov(a: int) -> int: ...\n"
            "    @overload\n"
            "    def ov(a: str) -> str: ...\n"
            "    def ov(a):\n"
            "        return a\n"
            "    return ov\n"
        )
        mod = _visit(src)
        impl = _resolve(mod, "with_overload.<locals>.ov")
        self.assertIsNotNone(impl)
        self.assertEqual(impl.kind, griffe.Kind.FUNCTION)
        self.assertTrue(impl.overloads)
        self.assertEqual(len(impl.overloads), 2)


class DecoratedNestedDefTests(unittest.TestCase):
    def test_start_line_taken_from_decorator(self):
        import ast

        src = (
            "def outer():\n"
            "    @staticmethod\n"
            "    def deco():\n"
            "        return 1\n"
            "    return deco\n"
        )
        tree = ast.parse(src)
        outer_def = tree.body[0]
        deco_def = outer_def.body[0]
        self.assertEqual(page_mod._start_line(deco_def), 2)  # the @staticmethod line, not def's


class ClassInDefInClassTests(unittest.TestCase):
    def test_class_nested_in_method_nested_in_class(self):
        src = (
            "class Outer:\n"
            "    def method(self):\n"
            "        class Local:\n"
            "            attr = 1\n"
            "        return Local\n"
        )
        mod = _visit(src)
        local = _resolve(mod, "Outer.method.<locals>.Local")
        self.assertIsNotNone(local)
        self.assertEqual(local.kind, griffe.Kind.CLASS)


class AsyncNestedDefTests(unittest.TestCase):
    def test_async_def_nested_in_async_def(self):
        src = (
            "async def coro():\n"
            "    async def inner_coro():\n"
            "        return 1\n"
            "    return inner_coro\n"
        )
        mod = _visit(src)
        inner = _resolve(mod, "coro.<locals>.inner_coro")
        self.assertIsNotNone(inner)
        self.assertIn("async", inner.labels)


class ControlFlowNestedDefTests(unittest.TestCase):
    def test_defs_under_if_try_with_for_while(self):
        src = (
            "def outer(flag):\n"
            "    if flag:\n"
            "        def in_if():\n"
            "            return 1\n"
            "    try:\n"
            "        def in_try():\n"
            "            return 1\n"
            "    except Exception:\n"
            "        pass\n"
            "    with open(__file__) as fh:\n"
            "        def in_with():\n"
            "            return 1\n"
            "    for _ in range(1):\n"
            "        def in_for():\n"
            "            return 1\n"
            "    while flag:\n"
            "        def in_while():\n"
            "            return 1\n"
            "        flag = False\n"
            "    return in_if, in_try, in_with, in_for, in_while\n"
        )
        mod = _visit(src)
        for name in ("in_if", "in_try", "in_with", "in_for", "in_while"):
            with self.subTest(name=name):
                self.assertIsNotNone(_resolve(mod, f"outer.<locals>.{name}"))


class DeepNestingTests(unittest.TestCase):
    def test_eight_levels_deep(self):
        names = [f"level{i}" for i in range(8)]
        src_lines: list[str] = []
        indent = ""
        for i, name in enumerate(names):
            src_lines.append(f"{indent}def {name}():")
            indent += "    "
        src_lines.append(f"{indent}return 1")
        src = "\n".join(src_lines) + "\n"
        mod = _visit(src)
        qual = ".<locals>.".join(names)
        self.assertIsNotNone(_resolve(mod, qual))


class LastDefWinsTests(unittest.TestCase):
    def test_later_nested_def_of_same_name_replaces_earlier(self):
        src = (
            "def outer():\n"
            "    def inner():\n"
            "        return 1\n"
            "    def inner():\n"
            "        return 2\n"
            "    return inner\n"
        )
        mod = _visit(src)
        inner = _resolve(mod, "outer.<locals>.inner")
        self.assertIsNotNone(inner)
        self.assertEqual(inner.lineno, 4)


class LaterAssignmentDoesNotRemoveDefTests(unittest.TestCase):
    def test_assignment_after_nested_def_does_not_remove_it(self):
        src = (
            "def outer():\n"
            "    def inner():\n"
            "        return 1\n"
            "    inner = None\n"
            "    return inner\n"
        )
        mod = _visit(src)
        inner = _resolve(mod, "outer.<locals>.inner")
        self.assertIsNotNone(inner)
        self.assertEqual(inner.kind, griffe.Kind.FUNCTION)


class InitReentryTests(unittest.TestCase):
    def test_init_nested_def_placed_once_by_griffes_own_reentry(self):
        # ADR-0131 clause 3 / the prototype: Griffe re-enters a class
        # __init__'s body itself (to collect self.x attributes), so a
        # nested def there is placed by Griffe's own visitor. The
        # extension must skip __init__ rather than also visiting its
        # children, or the nested def would be visited (and registered)
        # twice.
        src = (
            "class Holder:\n"
            "    def __init__(self, x):\n"
            "        self.x = x\n\n"
            "        def _init_helper(y):\n"
            "            return y\n\n"
            "        self.y = _init_helper(x)\n"
        )
        mod = _visit(src)
        init = _resolve(mod, "Holder.__init__")
        self.assertIsNotNone(init)
        helper = init.members.get("_init_helper")
        self.assertIsNotNone(helper)
        # Not double-registered as a sibling of __init__ under the class.
        self.assertNotIn("_init_helper", mod.members["Holder"].members)


class InitOverloadTests(unittest.TestCase):
    """A nested `@overload` chain inside a class `__init__` (clause 3).

    Griffe re-enters `__init__`'s own body itself (InitReentryTests above);
    an `@overload`-decorated nested def there is filed through Griffe's own
    `handle_function`, which requires `Function.overloads` to be a dict for
    the duration of that re-entry, the same way this extension already gives
    every other function-local scope a dict via `func.overloads = collected`.
    """

    def test_overload_in_init_attaches_to_implementation(self):
        src = (
            "from typing import overload\n\n\n"
            "class Holder:\n"
            "    def __init__(self, x):\n"
            "        @overload\n"
            "        def parse(a: int) -> int: ...\n"
            "        @overload\n"
            "        def parse(a: str) -> str: ...\n"
            "        def parse(a):\n"
            "            return a\n"
            "        self.x = parse(x)\n"
        )
        mod = _visit(src)
        init = _resolve(mod, "Holder.__init__")
        self.assertIsNotNone(init)
        parse = init.members.get("parse")
        self.assertIsNotNone(parse)
        self.assertEqual(parse.kind, griffe.Kind.FUNCTION)
        self.assertTrue(parse.overloads)
        self.assertEqual(len(parse.overloads), 2)

    def test_a_class_level_init_overload_chain_survives(self):
        """Regression: the dict this extension gives `__init__` must not replace
        the chain Griffe already transferred to the class-level implementation."""
        src = (
            "from typing import overload\n\n\n"
            "class A:\n"
            "    @overload\n"
            "    def __init__(self, x: int) -> None: ...\n"
            "    @overload\n"
            "    def __init__(self, x: str) -> None: ...\n"
            "    def __init__(self, x):\n"
            "        self.x = x\n"
        )
        init = _resolve(_visit(src), "A.__init__")
        self.assertIsInstance(init.overloads, list)
        self.assertEqual(len(init.overloads), 2)

    def test_an_init_overload_chain_in_a_function_local_class_survives(self):
        src = (
            "from typing import overload\n\n\n"
            "def make():\n"
            "    class B:\n"
            "        @overload\n"
            "        def __init__(self, x: int) -> None: ...\n"
            "        @overload\n"
            "        def __init__(self, x: str) -> None: ...\n"
            "        def __init__(self, x):\n"
            "            self.x = x\n"
            "    return B\n"
        )
        init = _resolve(_visit(src), "make.B.__init__")
        self.assertIsNotNone(init)
        self.assertIsInstance(init.overloads, list)
        self.assertEqual(len(init.overloads), 2)

    def test_class_level_and_nested_init_overload_chains_both_survive(self):
        src = (
            "from typing import overload\n\n\n"
            "class C:\n"
            "    @overload\n"
            "    def __init__(self, x: int) -> None: ...\n"
            "    @overload\n"
            "    def __init__(self, x: str) -> None: ...\n"
            "    def __init__(self, x):\n"
            "        @overload\n"
            "        def parse(a: int) -> int: ...\n"
            "        @overload\n"
            "        def parse(a: str) -> str: ...\n"
            "        def parse(a):\n"
            "            return a\n"
            "        self.x = parse(x)\n"
        )
        init = _resolve(_visit(src), "C.__init__")
        self.assertEqual(len(init.overloads), 2)
        self.assertEqual(len(init.members["parse"].overloads), 2)

    def test_overload_in_init_with_no_implementation_raises_named_error(self):
        src = (
            "from typing import overload\n\n\n"
            "class Holder:\n"
            "    def __init__(self, x):\n"
            "        @overload\n"
            "        def parse(a: int) -> int: ...\n"
            "        @overload\n"
            "        def parse(a: str) -> str: ...\n"
            "        self.x = x\n"
        )
        with self.assertRaises(locals_mod.LocalPlacementError) as ctx:
            _visit(src)
        err = ctx.exception
        self.assertNotIsInstance(err, griffe.ExtensionError)
        self.assertIn("parse", err.qualified_path)


class PlacementFailureTests(unittest.TestCase):
    def test_overload_variants_with_no_implementation_raise_named_error(self):
        # Two @overload-decorated nested defs and no plain implementation of
        # the same name: nothing to attach the collected variants to, so the
        # extension refuses instead of leaving them dangling or raising a
        # bare griffe.ExtensionError.
        src = (
            "from typing import overload\n\n\n"
            "def outer():\n"
            "    @overload\n"
            "    def ov(a: int) -> int: ...\n"
            "    @overload\n"
            "    def ov(a: str) -> str: ...\n"
            "    return 1\n"
        )
        with self.assertRaises(locals_mod.LocalPlacementError) as ctx:
            _visit(src)
        err = ctx.exception
        self.assertNotIsInstance(err, griffe.ExtensionError)
        self.assertIn("ov", err.qualified_path)


class NoExecutionTests(unittest.TestCase):
    """Fixture: both a module-level statement AND a decorator expression on
    a function-local def write MARKER — either would fire from merely
    executing (importing) the module, with no further call needed. The
    absence assertion below shows visiting through griffe.visit writes
    nothing; the positive control shows executing the same source does."""

    _FIXTURE = (
        "MARKER = {marker!r}\n"
        "open(MARKER, 'w').close()\n\n\n"
        "def outer():\n"
        "    @(lambda f: (open(MARKER, 'w').close(), f)[1])\n"
        "    def deco():\n"
        "        return 1\n"
        "    return deco\n"
    )

    def test_visiting_executes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            src = self._FIXTURE.format(marker=marker.as_posix())
            _visit(src)
            self.assertFalse(marker.exists())

    def test_positive_control_executing_the_module_writes_the_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            module_path = Path(tmp) / "exec_target.py"
            module_path.write_text(self._FIXTURE.format(marker=marker.as_posix()))
            spec = importlib.util.spec_from_file_location("exec_target_t", module_path)
            assert spec is not None and spec.loader is not None
            exec_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(exec_module)  # executing the module alone writes the marker
            self.assertTrue(marker.exists())


class ExtensionVersionTests(unittest.TestCase):
    def test_extension_version_is_exposed(self):
        self.assertEqual(locals_mod.EXTENSION_VERSION, "1")


class StartLineHelperTests(unittest.TestCase):
    def test_start_line_undecorated_statement(self):
        import ast

        tree = ast.parse("def f():\n    pass\n")
        node = tree.body[0]
        self.assertEqual(page_mod._start_line(node), 1)

    def test_start_line_decorated_statement(self):
        import ast

        tree = ast.parse("@staticmethod\n@another\ndef f():\n    pass\n")
        node = tree.body[0]
        self.assertEqual(page_mod._start_line(node), 1)


class QualifiedNameHelperTests(unittest.TestCase):
    """_qualname(obj) in the page renderer maps a Griffe member to its Crux qualified name,
    inserting a <locals> segment after each function ancestor."""

    def test_simple_nested_def(self):
        src = "def outer():\n    def inner():\n        return 1\n    return inner\n"
        mod = _visit(src)
        inner = _resolve(mod, "outer.<locals>.inner")
        self.assertEqual(page_mod._qualname(inner), "outer.<locals>.inner")

    def test_class_in_function_in_class(self):
        src = (
            "class C:\n"
            "    def m(self):\n"
            "        def f():\n"
            "            class g:\n"
            "                pass\n"
            "            return g\n"
            "        return f\n"
        )
        mod = _visit(src)
        g = _resolve(mod, "C.m.<locals>.f.<locals>.g")
        self.assertIsNotNone(g)
        self.assertEqual(page_mod._qualname(g), "C.m.<locals>.f.<locals>.g")

    def test_no_locals_segment_after_a_class_ancestor(self):
        src = "class C:\n    def m(self):\n        return 1\n"
        mod = _visit(src)
        m = _resolve(mod, "C.m")
        self.assertEqual(page_mod._qualname(m), "C.m")


if __name__ == "__main__":
    unittest.main()
