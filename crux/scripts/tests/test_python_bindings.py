"""Tests for crux/scripts/extractors/_py_bindings.py (U13, ADR-0131 clause 12).

Stdlib only (unittest, ast, importlib). Loaded by path — see the U15a test
module's header for the loading-by-path rationale, shared here.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BINDINGS_PATH = REPO_ROOT / "crux" / "scripts" / "extractors" / "_py_bindings.py"


def _load_bindings():
    spec = importlib.util.spec_from_file_location("crux_py_bindings_t", BINDINGS_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crux_py_bindings_t"] = mod
    spec.loader.exec_module(mod)
    return mod


bindings = _load_bindings()


def _notes(source: str):
    tree = ast.parse(source)
    return bindings.find_binding_notes(tree)


def _by_name(notes, name):
    return [n for n in notes if n.qualified_name == name]


class DuplicateModuleScopeTests(unittest.TestCase):
    def test_unconditional_duplicate_functions(self):
        src = "def f():\n    pass\n\n\ndef f():\n    pass\n"
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        n = notes[0]
        self.assertEqual(n.note_kind, "duplicate")
        self.assertEqual(n.branch_context, "unconditional")
        self.assertEqual(n.shadowed_kind, "function")
        self.assertEqual(n.shadowed_line, 1)
        self.assertEqual(n.retained_kind, "function")
        self.assertEqual(n.retained_line, 5)

    def test_if_else_conditional_duplicate(self):
        src = (
            "flag = True\n"
            "if flag:\n"
            "    def branch(a):\n"
            "        return a\n"
            "else:\n"
            "    def branch(a, b=1):\n"
            "        return a + b\n"
        )
        notes = _by_name(_notes(src), "branch")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_kind, "duplicate")
        self.assertIn("if", notes[0].branch_context)
        self.assertIn("else", notes[0].branch_context)

    def test_try_except_conditional_duplicate(self):
        src = (
            "try:\n"
            "    def g():\n"
            "        return 1\n"
            "except Exception:\n"
            "    def g():\n"
            "        return 2\n"
        )
        notes = _by_name(_notes(src), "g")
        self.assertEqual(len(notes), 1)
        self.assertIn("try", notes[0].branch_context)
        self.assertIn("except", notes[0].branch_context)

    def test_s3_try_star_except_star_conditional_duplicate(self):
        src = (
            "try:\n"
            "    def g():\n"
            "        return 1\n"
            "except* Exception:\n"
            "    def g():\n"
            "        return 2\n"
        )
        notes = _by_name(_notes(src), "g")
        self.assertEqual(len(notes), 1)
        self.assertIn("try", notes[0].branch_context)
        self.assertIn("except", notes[0].branch_context)

    def test_class_duplicate(self):
        src = "class C:\n    x = 1\n\n\nclass C:\n    x = 2\n"
        notes = _by_name(_notes(src), "C")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].shadowed_kind, "class")
        self.assertEqual(notes[0].retained_kind, "class")

    def test_function_scope_duplicate(self):
        src = (
            "def outer():\n"
            "    def inner():\n"
            "        return 1\n"
            "    def inner():\n"
            "        return 2\n"
            "    return inner\n"
        )
        notes = _by_name(_notes(src), "outer.<locals>.inner")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_kind, "duplicate")

    def test_overload_chain_produces_no_note(self):
        src = (
            "from typing import overload\n\n\n"
            "@overload\n"
            "def ov(a: int) -> int: ...\n"
            "@overload\n"
            "def ov(a: str) -> str: ...\n"
            "def ov(a):\n"
            "    return a\n"
        )
        self.assertEqual(_by_name(_notes(src), "ov"), [])

    def test_accessor_chain_produces_no_note(self):
        src = (
            "class C:\n"
            "    @property\n"
            "    def x(self):\n"
            "        return self._x\n\n"
            "    @x.setter\n"
            "    def x(self, value):\n"
            "        self._x = value\n\n"
            "    @x.deleter\n"
            "    def x(self):\n"
            "        del self._x\n"
        )
        self.assertEqual(_by_name(_notes(src), "C.x"), [])


class BranchContextTests(unittest.TestCase):
    def test_elif_branch_is_distinguished_from_if(self):
        src = (
            "flag = 0\n"
            "if flag == 1:\n"
            "    def branch(a):\n"
            "        return a\n"
            "elif flag == 2:\n"
            "    def branch(a, b=1):\n"
            "        return a + b\n"
        )
        notes = _by_name(_notes(src), "branch")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].branch_context, "if/elif")

    def test_else_with_single_nested_if_is_not_conflated_with_elif(self):
        # The nested `if` here is INSIDE an explicit `else:` block, at a
        # deeper column than the outer `if` — not a real `elif`, even
        # though the AST shape (one `If` in `orelse`) looks the same as a
        # true elif chain.
        src = (
            "flag = 0\n"
            "if flag == 1:\n"
            "    def branch(a):\n"
            "        return a\n"
            "else:\n"
            "    if flag == 2:\n"
            "        def branch(a, b=1):\n"
            "            return a + b\n"
        )
        notes = _by_name(_notes(src), "branch")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].branch_context, "if/else")

    def test_finally_branch_context(self):
        src = (
            "def f():\n"
            "    return 1\n\n\n"
            "try:\n"
            "    pass\n"
            "finally:\n"
            "    f = 1\n"
        )
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        self.assertIn("finally", notes[0].branch_context)

    def test_match_case_duplicate(self):
        src = (
            "match 1:\n"
            "    case 1:\n"
            "        def h():\n"
            "            return 1\n"
            "    case 2:\n"
            "        def h():\n"
            "            return 2\n"
        )
        notes = _by_name(_notes(src), "h")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_kind, "duplicate")
        self.assertEqual(notes[0].branch_context, "case")

    def test_class_scope_conditional_duplicate(self):
        src = (
            "flag = True\n"
            "class C:\n"
            "    if flag:\n"
            "        def m(self):\n"
            "            return 1\n"
            "    else:\n"
            "        def m(self):\n"
            "            return 2\n"
        )
        notes = _by_name(_notes(src), "C.m")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_kind, "duplicate")
        self.assertIn("if", notes[0].branch_context)
        self.assertIn("else", notes[0].branch_context)


class ImportRebindingScopeTests(unittest.TestCase):
    def test_import_rebind_at_class_scope(self):
        src = (
            "class C:\n"
            "    def path(self):\n"
            "        return 1\n"
            "    from os import path\n"
        )
        notes = _by_name(_notes(src), "C.path")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_kind, "rebinding")
        self.assertEqual(notes[0].retained_kind, "import")

    def test_import_rebind_at_function_scope(self):
        src = (
            "def outer():\n"
            "    def path():\n"
            "        return 1\n"
            "    from os import path\n"
            "    return path\n"
        )
        notes = _by_name(_notes(src), "outer.<locals>.path")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_kind, "rebinding")
        self.assertEqual(notes[0].retained_kind, "import")


class CardinalityTests(unittest.TestCase):
    def test_three_shadowed_bindings_all_point_at_the_final_retained_one(self):
        src = (
            "def f():\n    return 1\n\n\n"
            "def f():\n    return 2\n\n\n"
            "def f():\n    return 3\n"
        )
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 2)
        for n in notes:
            self.assertEqual(n.note_kind, "duplicate")
            self.assertEqual(n.retained_line, 9)
        self.assertEqual({n.shadowed_line for n in notes}, {1, 5})


class GenericWalrusCoverageTests(unittest.TestCase):
    def test_with_context_expr_walrus_rebind(self):
        src = (
            "def f():\n    return 1\n\n\n"
            "with (f := open('x')):\n"
            "    pass\n"
        )
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_kind, "rebinding")
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_match_subject_walrus_rebind(self):
        src = (
            "def f():\n    return 1\n\n\n"
            "match (f := 1):\n"
            "    case _:\n"
            "        pass\n"
        )
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_case_guard_walrus_rebind(self):
        src = (
            "def f():\n    return 1\n\n\n"
            "match 1:\n"
            "    case x if (f := x):\n"
            "        pass\n"
        )
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_assert_walrus_rebind(self):
        src = "def f():\n    return 1\n\n\nassert (f := 1)\n"
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_raise_walrus_rebind(self):
        src = "def f():\n    return 1\n\n\nraise ValueError(f := 1)\n"
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_walrus_in_nested_comprehension_binds_enclosing_scope(self):
        # PEP 572: a walrus target inside a (possibly nested) comprehension
        # binds in the nearest enclosing non-comprehension scope, not the
        # comprehension's own scope.
        src = (
            "def f():\n    return 1\n\n\n"
            "xs = [[(f := y) for y in row] for row in [[1]]]\n"
        )
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_walrus_inside_lambda_body_is_not_scanned(self):
        # The lambda's own body is a separate scope; a walrus there binds
        # inside the lambda, not the enclosing statement's scope.
        src = "def f():\n    return 1\n\n\ng = lambda: (f := 1)\n"
        self.assertEqual(_by_name(_notes(src), "f"), [])


class RebindingTests(unittest.TestCase):
    def test_module_scope_assignment_rebind(self):
        src = "def f():\n    return 1\n\n\nf = 1\n"
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        n = notes[0]
        self.assertEqual(n.note_kind, "rebinding")
        self.assertEqual(n.shadowed_kind, "function")
        self.assertEqual(n.shadowed_line, 1)
        self.assertEqual(n.retained_kind, "assignment")
        self.assertEqual(n.retained_line, 5)

    def test_module_scope_import_rebind(self):
        src = "def path():\n    return 1\n\n\nfrom os import path\n"
        notes = _by_name(_notes(src), "path")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "import")

    def test_import_rebind_with_asname(self):
        src = "def h():\n    return 1\n\n\nimport json as h\n"
        notes = _by_name(_notes(src), "h")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "import")
        self.assertEqual(notes[0].retained_line, 5)

    def test_class_scope_rebind_to_property_attribute(self):
        src = (
            "class C:\n"
            "    def m(self):\n"
            "        return 1\n"
            "    m = property(m)\n"
        )
        notes = _by_name(_notes(src), "C.m")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_function_scope_def_survives_a_later_assignment(self):
        # ADR-0131 clause 3: a later assignment in the function does NOT
        # remove the nested def from documentation; the extension only
        # builds members for def/class. The gap note still records the
        # rebinding relationship.
        src = (
            "def outer():\n"
            "    def inner():\n"
            "        return 1\n"
            "    inner = None\n"
            "    return inner\n"
        )
        notes = _by_name(_notes(src), "outer.<locals>.inner")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_kind, "rebinding")
        self.assertEqual(notes[0].shadowed_kind, "function")
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_for_target_rebind(self):
        src = "def loop():\n    return 1\n\n\nfor loop in range(3):\n    pass\n"
        notes = _by_name(_notes(src), "loop")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "for target")

    def test_binding_preceding_def_is_out_of_scope(self):
        src = "x = 1\n\n\ndef x():\n    return 1\n"
        self.assertEqual(_by_name(_notes(src), "x"), [])

    def test_del_is_a_rebinding(self):
        src = "def f():\n    return 1\n\n\ndel f\n"
        notes = _by_name(_notes(src), "f")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "del")

    def test_global_and_nonlocal_are_not_bindings(self):
        src = (
            "def f():\n"
            "    return 1\n\n\n"
            "def user():\n"
            "    global f\n"
            "    f = 2\n"
        )
        # 'global f' inside user() is not itself a binding event; the
        # assignment on the next line, in user()'s own scope, is a
        # different name's scope (function-local to user, not module
        # scope) so it produces no note against module-scope f.
        self.assertEqual(_by_name(_notes(src), "f"), [])

    def test_with_as_rebind(self):
        src = (
            "def fh():\n"
            "    return 1\n\n\n"
            "with open('x') as fh:\n"
            "    pass\n"
        )
        notes = _by_name(_notes(src), "fh")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "with target")

    def test_except_as_rebind(self):
        src = (
            "def err():\n"
            "    return 1\n\n\n"
            "try:\n"
            "    pass\n"
            "except Exception as err:\n"
            "    pass\n"
        )
        notes = _by_name(_notes(src), "err")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "except target")

    def test_match_capture_rebind(self):
        src = (
            "def m():\n"
            "    return 1\n\n\n"
            "match 1:\n"
            "    case m:\n"
            "        pass\n"
        )
        notes = _by_name(_notes(src), "m")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "match capture")

    def test_tuple_unpacking_rebind(self):
        src = "def a():\n    return 1\n\n\na, b = 1, 2\n"
        notes = _by_name(_notes(src), "a")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_augmented_assignment_rebind(self):
        src = "n = 0\n\n\ndef n():\n    return 1\n\n\nn += 1\n"
        notes = _by_name(_notes(src), "n")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].shadowed_line, 4)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_annotated_assignment_with_value_rebind(self):
        src = "def n():\n    return 1\n\n\nn: int = 2\n"
        notes = _by_name(_notes(src), "n")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].retained_kind, "assignment")

    def test_annotation_only_is_not_a_binding(self):
        src = "def n():\n    return 1\n\n\nn: int\n"
        self.assertEqual(_by_name(_notes(src), "n"), [])

    def test_non_exported_import_still_produces_a_note(self):
        # The note's existence is independent of __all__ / export status,
        # which is a rendering-layer (clause 11) concern, not a bindings
        # concern.
        src = "__all__ = []\n\n\ndef path():\n    return 1\n\n\nfrom os import path\n"
        notes = _by_name(_notes(src), "path")
        self.assertEqual(len(notes), 1)


class OrderingTests(unittest.TestCase):
    def test_deterministic_ordering_by_line_then_name(self):
        src = (
            "def b():\n    pass\n\n\n"
            "def b():\n    pass\n\n\n"
            "def a():\n    pass\n\n\n"
            "def a():\n    pass\n"
        )
        notes = _notes(src)
        keys = [(n.retained_line, n.qualified_name) for n in notes]
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
