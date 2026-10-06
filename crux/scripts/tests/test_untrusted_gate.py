"""The binding gate in `untrusted.py`, driven in both directions.

`binding_problems` reports a module that CALLS `redact` or `parse_problem` by a
bare name it never binds — the `bionic_config` defect, where nine tests died on
`NameError` because the call sites were added and the import was not.

**Why every rule here has a positive control.** A scanner that returns `[]`
because it is broken, because it was pointed at the wrong directory, or because
its AST walk quietly stopped matching is indistinguishable from a scanner that
returns `[]` because the tree is clean. `test_the_real_tree_is_clean` alone
would go green in all four cases. So each rule is also driven from DOCTORED
source that must produce the specific problem string, and the clean-tree test
carries its own non-vacuity guard: it asserts the scan actually found modules
that call a helper, so the rule's matching machinery is known to have run.

The doctored source is built in a `tmp` directory or passed as a literal. The
real tree is never written to.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "_untrusted_under_test", SCRIPTS / "untrusted.py")
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)


# The defect exactly as it shipped: call sites added, import not.
_UNBOUND_SOURCE = '''
"""A reader that reached for the helper every sibling had."""


def refuse(value):
    return f"refusing {redact(value)}"
'''

_BOUND_SOURCE = '''
from untrusted import redact


def refuse(value):
    return f"refusing {redact(value)}"
'''

_QUALIFIED_SOURCE = '''
import importlib.util as _ilu

_u = _ilu.module_from_spec(_ilu.spec_from_file_location("untrusted", "x.py"))


def refuse(value):
    return f"refusing {_u.redact(value)}"
'''


class BindingRulePositiveControls(unittest.TestCase):
    """Each control constructs the defect and asserts the gate NAMES it."""

    def test_a_bare_call_with_no_import_is_reported(self):
        problems = U.binding_problems({"reader.py": _UNBOUND_SOURCE})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unbound:", problems[0])
        self.assertIn("reader.py", problems[0])
        self.assertIn("`redact`", problems[0])
        self.assertIn("NameError", problems[0])

    def test_the_reported_line_is_the_call_site(self):
        """The line has to be where the `NameError` lands, or the report sends
        the reader to the wrong place."""
        problems = U.binding_problems({"reader.py": _UNBOUND_SOURCE})
        call_line = next(i for i, ln in enumerate(
            _UNBOUND_SOURCE.splitlines(), start=1) if "redact(value)" in ln)
        self.assertIn(f"reader.py:{call_line}", problems[0])

    def test_parse_problem_is_covered_by_the_same_rule(self):
        src = 'def f(name, exc):\n    return parse_problem("x", name, exc)\n'
        problems = U.binding_problems({"reader.py": src})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("`parse_problem`", problems[0])

    def test_both_helpers_unbound_are_reported_separately(self):
        src = ('def f(v, name, exc):\n'
               '    a = redact(v)\n'
               '    b = parse_problem("x", name, exc)\n'
               '    return a, b\n')
        problems = U.binding_problems({"reader.py": src})
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("`redact`" in p for p in problems))
        self.assertTrue(any("`parse_problem`" in p for p in problems))

    def test_unparseable_source_is_reported_not_skipped(self):
        """A syntax error must not switch the gate off for that file."""
        problems = U.binding_problems({"reader.py": "def f(:\n"})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unparseable:", problems[0])
        self.assertIn("reader.py", problems[0])

    def test_an_unparseable_module_quotes_none_of_its_source(self):
        """The `unparseable:` report goes through `parse_problem`, so a hostile
        file's own bytes never reach the finding."""
        secret = "TOTALLY_SECRET_TOKEN_abcdef"
        problems = U.binding_problems({"reader.py": f"def f(:  # {secret}\n"})
        self.assertNotIn(secret, problems[0])


class BindingRuleNegativeControls(unittest.TestCase):
    """The other direction: shapes that are correct must NOT be reported, or
    the gate becomes noise and gets switched off."""

    def test_an_imported_helper_is_not_reported(self):
        self.assertEqual(U.binding_problems({"reader.py": _BOUND_SOURCE}), [])

    def test_a_qualified_call_is_not_reported(self):
        """`_u.redact(...)` resolves through the object, so it cannot raise
        `NameError` for the helper's own name."""
        self.assertEqual(
            U.binding_problems({"reader.py": _QUALIFIED_SOURCE}), [])

    def test_a_module_level_assignment_binds(self):
        src = ('redact = _untrusted_module().redact\n'
               'def f(v):\n    return redact(v)\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_local_assignment_binds(self):
        src = ('def f(v, mod):\n'
               '    redact = mod.redact\n'
               '    return redact(v)\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_parameter_named_for_a_helper_binds(self):
        src = 'def f(v, redact):\n    return redact(v)\n'
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_untrusted_itself_binds_by_def(self):
        """`untrusted.py` calls `redact` inside `parse_problem` and binds both
        by `def`. The gate must not report the module it lives in."""
        source = (SCRIPTS / "untrusted.py").read_text(encoding="utf-8")
        self.assertEqual(U.binding_problems({"untrusted.py": source}), [])

    def test_a_module_that_never_calls_a_helper_is_not_reported(self):
        self.assertEqual(
            U.binding_problems({"reader.py": "def f(v):\n    return v\n"}), [])


# ── the six scope-blind shapes ────────────────────────────────────────────
#
# Each fixture below CALLS its own function at module level, so executing the
# file raises `NameError` — the very failure `binding_problems` exists to
# predict. `_raises_name_error_at_runtime` proves that; the gate assertion
# then proves the gate SEES it. Without the runtime half, a gate assertion
# would only show the two halves agree, not that either is right.

_SCOPE_SHAPES: dict[str, str] = {
    # A class body's names are NOT in the lexical chain of its methods.
    "class_scope.py": '''
class Reader:
    redact = staticmethod(str)

    def refuse(self, value):
        return f"refusing {redact(value)}"


Reader().refuse("x")
''',
    # A comprehension has its own scope; its target does not leak out.
    "comprehension_scope.py": '''
def refuse(values):
    kept = [redact for redact in values]
    return redact(kept)


refuse(["a"])
''',
    # `del` unbinds. Every binding here precedes it.
    "deleted_binding.py": '''
redact = str

del redact


def refuse(value):
    return f"refusing {redact(value)}"


refuse("x")
''',
    # `if TYPE_CHECKING:` never executes at runtime.
    "type_checking_import.py": '''
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untrusted import redact


def refuse(value):
    return f"refusing {redact(value)}"


refuse("x")
''',
    # A function-local import binds in that function only.
    "function_local_import.py": '''
def setup():
    from untrusted import redact
    return redact


def refuse(value):
    return f"refusing {redact(value)}"


refuse("x")
''',
    # `bionic_config.py:57-68` with its `except` rebind DELETED: the handler
    # that runs when the import fails binds nothing.
    "unbound_try_branch.py": '''
try:
    from untrusted import redact
except ImportError:
    pass


def refuse(value):
    return f"refusing {redact(value)}"


refuse("x")
''',
}


# Runs a fixture with `crux/scripts/` scrubbed from `sys.path`. The scrub is
# not optional: this checkout installs itself editable, and
# `.venv/.../_editable_impl_crux.pth` puts `crux/scripts/` on the path of every
# process the venv starts. Without the scrub the `try`/`except` fixture's
# `from untrusted import redact` SUCCEEDS, the fixture runs clean, and the
# runtime half of the control silently proves nothing. `argv[1]` is the
# fixture; `argv[2]` is the directory to scrub.
_RUNTIME_DRIVER = r'''
import runpy, sys
from pathlib import Path

fixture = Path(sys.argv[1]).resolve()
scripts = Path(sys.argv[2]).resolve()

kept = []
for entry in sys.path:
    try:
        resolved = Path(entry if entry else ".").resolve()
    except OSError:
        kept.append(entry)
        continue
    if resolved == scripts:
        continue
    kept.append(entry)
sys.path[:] = kept

# POSITIVE CONTROL for the scrub itself, same shape as `test_bypath_load.py`.
try:
    import untrusted  # noqa: F401
except ImportError:
    pass
else:
    print("SCRUB-FAILED: `import untrusted` resolved by name", file=sys.stderr)
    raise SystemExit(3)

runpy.run_path(str(fixture), run_name="__main__")
'''


def _raises_name_error_at_runtime(source: str) -> str:
    """Execute `source` as a file in a subprocess and return its stderr."""
    import os
    import subprocess
    import sys
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "fixture.py"
        script.write_text(source, encoding="utf-8")
        driver = Path(td) / "driver.py"
        driver.write_text(_RUNTIME_DRIVER, encoding="utf-8")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, str(driver), str(script), str(SCRIPTS)],
            capture_output=True, text=True, cwd=td, env=env, timeout=60)
    if "SCRUB-FAILED" in proc.stderr:
        raise AssertionError(proc.stderr)
    return proc.stderr


class ScopeVisibilityPositiveControls(unittest.TestCase):
    """Six shapes that pass the gate today and raise `NameError` when run.

    They are written to disk and scanned through `scan_scripts` so the gate is
    driven over real files, exactly as it runs against `crux/scripts/`.
    """

    def _problems_for(self, filename: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / filename).write_text(_SCOPE_SHAPES[filename],
                                         encoding="utf-8")
            return U.binding_problems(U.scan_scripts(root))

    def _assert_shape_is_reported(self, filename: str):
        stderr = _raises_name_error_at_runtime(_SCOPE_SHAPES[filename])
        self.assertIn("NameError", stderr,
                      f"{filename} does not actually break at runtime, so the "
                      f"gate assertion below would prove nothing:\n{stderr}")
        problems = self._problems_for(filename)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unbound:", problems[0])
        self.assertIn(filename, problems[0])
        self.assertIn("`redact`", problems[0])

    def test_a_class_body_binding_does_not_reach_a_method(self):
        self._assert_shape_is_reported("class_scope.py")

    def test_a_comprehension_target_does_not_leak_out(self):
        self._assert_shape_is_reported("comprehension_scope.py")

    def test_a_deleted_binding_does_not_satisfy_a_later_call(self):
        self._assert_shape_is_reported("deleted_binding.py")

    def test_a_type_checking_import_does_not_bind_at_runtime(self):
        self._assert_shape_is_reported("type_checking_import.py")

    def test_a_function_local_import_does_not_reach_a_sibling_function(self):
        self._assert_shape_is_reported("function_local_import.py")

    def test_a_try_branch_that_does_not_rebind_is_reported(self):
        self._assert_shape_is_reported("unbound_try_branch.py")


class TryExceptImportFallbackNegativeControl(unittest.TestCase):
    """The other direction of the same shape, on the real file.

    `bionic_config.py:57-68` binds `redact` and `parse_problem` on BOTH the
    `try` path and the `except ImportError` path. The shape above is that block
    with its rebind deleted. If the gate cannot tell them apart it is either
    blind to the defect or noisy on the fix, and both directions are asserted
    here so neither can be traded for the other.
    """

    def test_the_real_bionic_config_is_clean(self):
        source = (SCRIPTS / "bionic_config.py").read_text(encoding="utf-8")
        self.assertIn("except ImportError:", source,
                      "bionic_config.py no longer carries the fallback shape "
                      "this control is written against")
        self.assertEqual(U.binding_problems({"bionic_config.py": source}), [])

    def test_the_two_shapes_differ_only_in_the_rebind(self):
        """Guard against the control drifting away from the real file: the
        fixture must remain the same idiom, minus the binding handler."""
        fixture = _SCOPE_SHAPES["unbound_try_branch.py"]
        self.assertIn("except ImportError:\n    pass", fixture)
        self.assertNotIn("redact = ", fixture)


def _runs_clean_at_runtime(source: str) -> str:
    """Execute `source` WITH `crux/scripts/` on `sys.path` and return stderr.

    The mirror of `_raises_name_error_at_runtime`. A negative control claims
    the code works; this is what makes that a fact rather than an opinion.
    """
    import os
    import subprocess
    import sys
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "fixture.py"
        script.write_text(source, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SCRIPTS)
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, cwd=td,
                              env=env, timeout=60)
    return proc.stderr


# The two shapes the flat `ast.walk` reported though both run fine. The old
# `_bound_names` produced `{"*", "refuse", "v"}` for the first — a literal
# `"*"` in the bound set, which satisfies nothing — and simply never looked at
# `globals()` for the second.
_STAR_IMPORT_SOURCE = '''
from untrusted import *


def refuse(v):
    return redact(v)


print(refuse("x"))
'''

_GLOBALS_MUTATION_SOURCE = '''
import untrusted as _u

globals().update(
    {k: getattr(_u, k) for k in dir(_u) if not k.startswith("__")})


def refuse(v):
    return redact(v)


print(refuse("x"))
'''


class DynamicNamespaceNegativeControls(unittest.TestCase):
    """A gate that reports working code gets switched off. Both shapes below
    run clean, so neither may be reported."""

    def test_a_star_import_is_not_reported(self):
        self.assertEqual("", _runs_clean_at_runtime(_STAR_IMPORT_SOURCE))
        self.assertEqual(
            U.binding_problems({"reader.py": _STAR_IMPORT_SOURCE}), [])

    def test_a_globals_mutation_is_not_reported(self):
        self.assertEqual("", _runs_clean_at_runtime(_GLOBALS_MUTATION_SOURCE))
        self.assertEqual(
            U.binding_problems({"reader.py": _GLOBALS_MUTATION_SOURCE}), [])

    def test_the_real_crux_config_shim_is_clean(self):
        """`crux_config.py` is the file the second shape is drawn from: it
        populates itself with `globals().update(...)`."""
        source = (SCRIPTS / "crux_config.py").read_text(encoding="utf-8")
        self.assertIn("globals().update(", source,
                      "crux_config.py no longer carries the shape this "
                      "control is written against")
        self.assertEqual(U.binding_problems({"crux_config.py": source}), [])

    def test_a_traceable_globals_subscript_does_not_open_the_scope(self):
        """`globals()["redact"] = ...` names the binding, so it is read as a
        binding rather than as an unknowable namespace. The second helper stays
        reportable, which is what proves the scope was not simply opened."""
        src = ('def _load():\n'
               '    return None\n'
               'globals()["redact"] = _load()\n'
               'def f(v, name, exc):\n'
               '    return redact(v), parse_problem("x", name, exc)\n')
        problems = U.binding_problems({"reader.py": src})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("`parse_problem`", problems[0])


class ScopeVisibilityNegativeControls(unittest.TestCase):
    """The shapes adjacent to each positive control that DO resolve."""

    def test_a_class_body_call_sees_the_class_body_binding(self):
        """The class scope is visible to code written directly in it — only
        code NESTED inside it cannot see it."""
        src = ('class Reader:\n'
               '    redact = str\n'
               '    banner = redact("x")\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_comprehension_sees_the_enclosing_scope(self):
        src = ('from untrusted import redact\n'
               'def f(vs):\n'
               '    return [redact(v) for v in vs]\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_rebind_after_a_del_satisfies_the_call(self):
        src = ('redact = str\n'
               'del redact\n'
               'from untrusted import redact\n'
               'def f(v):\n'
               '    return redact(v)\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_nested_function_sees_its_enclosing_function(self):
        src = ('def outer(mod):\n'
               '    redact = mod.redact\n'
               '    def inner(v):\n'
               '        return redact(v)\n'
               '    return inner\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_global_declaration_reaches_the_module_binding(self):
        src = ('redact = str\n'
               'def f(v):\n'
               '    global redact\n'
               '    return redact(v)\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_handler_that_reraises_does_not_have_to_rebind(self):
        """Nothing after the `try` runs on a re-raising path, so that handler
        is excluded from the intersection."""
        src = ('try:\n'
               '    from untrusted import redact\n'
               'except ImportError:\n'
               '    raise\n'
               'def f(v):\n'
               '    return redact(v)\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_handler_that_exits_does_not_have_to_rebind(self):
        """A handler ending in `sys.exit(2)` terminates the process, so no
        code after the `try` runs on that path either. This repo's CLIs end
        their import fallbacks that way, and reporting them would be
        reporting working code."""
        src = ('import sys\n'
               'try:\n'
               '    from untrusted import redact\n'
               'except ImportError:\n'
               '    print("no helper", file=sys.stderr)\n'
               '    sys.exit(2)\n'
               'def f(v):\n'
               '    return redact(v)\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])

    def test_a_handler_that_merely_prints_must_still_rebind(self):
        """The discriminator for the test above: the same handler WITHOUT the
        exit falls through, leaves `redact` unbound, and is reported. Without
        this pair, `_falls_through` could return False for everything and both
        directions would look right."""
        src = ('import sys\n'
               'try:\n'
               '    from untrusted import redact\n'
               'except ImportError:\n'
               '    print("no helper", file=sys.stderr)\n'
               'def f(v):\n'
               '    return redact(v)\n')
        problems = U.binding_problems({"reader.py": src})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("`redact`", problems[0])

    def test_a_binding_written_below_the_call_still_satisfies_it(self):
        """The analysis is lexical, not ordered — a module-level import at the
        foot of the file binds for a function defined above it."""
        src = ('def f(v):\n'
               '    return redact(v)\n'
               'from untrusted import redact\n')
        self.assertEqual(U.binding_problems({"reader.py": src}), [])


class ScanScriptsTests(unittest.TestCase):
    def test_test_modules_are_excluded_and_others_are_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests").mkdir()
            (root / "sub" / "tests").mkdir(parents=True)
            (root / "keep.py").write_text("x = 1\n")
            (root / "sub" / "also_keep.py").write_text("x = 2\n")
            (root / "tests" / "test_a.py").write_text("x = 3\n")
            (root / "sub" / "tests" / "test_b.py").write_text("x = 4\n")
            found = set(U.scan_scripts(root))
        # Positive half: the non-test modules ARE found, so an empty result
        # below could not be mistaken for a working exclusion.
        self.assertEqual(found, {"keep.py", "sub/also_keep.py"})

    def test_a_doctored_tree_is_scanned_and_reported_end_to_end(self):
        """`scan_scripts` -> `binding_problems` over a real directory, so the
        two halves are known to compose. The clean-tree assertion below is the
        same call over the same code, differing only in its input."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "reader.py").write_text(_UNBOUND_SOURCE, encoding="utf-8")
            problems = U.binding_problems(U.scan_scripts(root))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unbound: reader.py", problems[0])


class RealTreeTests(unittest.TestCase):
    def test_the_real_tree_is_clean(self):
        sources = U.scan_scripts(SCRIPTS)

        # NON-VACUITY GUARD. `[]` means "clean" only if the rule actually ran
        # over modules it could have reported. Both halves are asserted: the
        # scan found modules, and some of those modules really do call a
        # helper by a bare name, which is the only input the rule inspects.
        self.assertGreater(len(sources), 20,
                           "scan_scripts found almost nothing — it is probably "
                           "pointed at the wrong directory")
        import ast
        callers = [rel for rel, src in sources.items()
                   if U._bare_helper_calls(ast.parse(src))]
        self.assertGreater(
            len(callers), 5,
            "no module in the scan calls a helper by a bare name, so a clean "
            "verdict says nothing about the rule")

        self.assertEqual(U.binding_problems(sources), [])


if __name__ == "__main__":
    unittest.main()
