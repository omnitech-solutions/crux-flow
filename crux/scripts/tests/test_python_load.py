"""Tests for crux/scripts/extractors/_py_load.py (U11, ADR-0131 clauses 1, 5, 7, 11).

Requires griffelib==2.3.0 (declared in pyproject.toml/uv.lock; resolved by
`uv run`). Loaded by path — see test_python_locals.py's header for the
loading-by-path rationale, shared here. Fixtures live under
crux/scripts/tests/fixtures/python_code_docs/, inside crux/, so nothing here
needs `_dev_surface.require_dev_surface`.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import griffe

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "crux" / "scripts"
LOAD_PATH = SCRIPTS / "extractors" / "_py_load.py"
DISPATCHER_PATH = SCRIPTS / "extract-code-docs.py"
FIXTURES = SCRIPTS / "tests" / "fixtures" / "python_code_docs"


def _load_dispatcher():
    cached = sys.modules.get("extract_code_docs_dispatcher")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("extract_code_docs_dispatcher", DISPATCHER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_code_docs_dispatcher"] = mod
    spec.loader.exec_module(mod)
    return mod


_load_dispatcher()  # registers ExtractionRefusal in sys.modules before _py_load loads


def _load_py_load():
    spec = importlib.util.spec_from_file_location("crux_py_load_t", LOAD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crux_py_load_t"] = mod
    spec.loader.exec_module(mod)
    return mod


load_mod = _load_py_load()
ExtractionRefusal = load_mod.ExtractionRefusal


def _units(base: Path, rel_paths: list[str]) -> list[tuple[str, bytes]]:
    return [(rel, (base / rel).read_bytes()) for rel in rel_paths]


class SizeBoundTests(unittest.TestCase):
    def test_oversized_source_refuses_before_decode(self):
        raw = b"\xff" * (load_mod.MAX_SOURCE_BYTES + 1)  # invalid UTF-8, on purpose
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection([("big.py", raw)])
        self.assertEqual(ctx.exception.path, "big.py")
        self.assertIn(str(load_mod.MAX_SOURCE_BYTES), ctx.exception.cause)

    def test_source_at_exactly_the_bound_is_not_refused_for_size(self):
        # Exactly at the bound must not trip the size refusal (only "larger
        # than 2 MiB" refuses); it may still fail for other reasons, so keep
        # this source trivially valid.
        raw = b"# " + b"x" * (load_mod.MAX_SOURCE_BYTES - 3) + b"\n"
        self.assertEqual(len(raw), load_mod.MAX_SOURCE_BYTES)
        sel = load_mod.load_selection([("ok.py", raw)])
        self.assertEqual(sel.sources[0].rel_path, "ok.py")


class GrammarGateTests(unittest.TestCase):
    SYNTAX_DIR = FIXTURES / "synthetic" / "src" / "syntax"

    def test_broken_py_refuses_naming_the_file(self):
        raw = (self.SYNTAX_DIR / "broken.py").read_bytes()
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection([("syntax/broken.py", raw)])
        self.assertEqual(ctx.exception.path, "syntax/broken.py")
        # `_check_grammar`'s cause names the line ("line N: msg"); a
        # regression to Griffe's bare SyntaxError message (no line number)
        # must be caught here rather than pass silently.
        self.assertIn("line", ctx.exception.cause)

    def test_py314_only_refuses_on_the_running_interpreter(self):
        """Valid only on 3.14 (PEP 758); ast.parse pins feature_version=(3,13)
        so it refuses on EVERY interpreter, 3.13 or 3.14 alike."""
        raw = (self.SYNTAX_DIR / "py314_only.py").read_bytes()
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection([("syntax/py314_only.py", raw)])
        self.assertEqual(ctx.exception.path, "syntax/py314_only.py")
        self.assertIn("line", ctx.exception.cause)
        self.assertIn("under the Python 3.13 grammar", ctx.exception.cause)


class RefusalRemedyTests(unittest.TestCase):
    def test_an_oversized_source_names_the_remedy(self):
        raw = b"#" * (load_mod.MAX_SOURCE_BYTES + 1)
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection([("big.py", raw)])
        self.assertEqual(ctx.exception.path, "big.py")
        self.assertIn("narrow the glob", ctx.exception.cause)



def _current_stack_depth() -> int:
    """The caller's current Python call-stack depth, by walking `f_back`."""
    depth = 0
    frame = sys._getframe()  # noqa: SLF001 — stdlib-sanctioned frame introspection
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


class EncodingTests(unittest.TestCase):
    """Decode the way the Python 3.13 tokenizer would (ADR-0131 clause 5), via `tokenize.detect_encoding` — a UTF-8 BOM or a PEP 263 `coding:`
    declaration selects the encoding, and a BOM never shifts line numbers."""

    def test_a_utf8_bom_file_renders_with_lines_unshifted(self):
        raw = b"\xef\xbb\xbf\"\"\"Doc.\"\"\"\ndef f():\n    pass\n"
        sel = load_mod.load_selection([("m.py", raw)])
        source = sel.sources[0]
        self.assertFalse(source.text.startswith("﻿"))
        self.assertEqual(source.text, '"""Doc."""\ndef f():\n    pass\n')
        self.assertEqual(source.module.docstring.value, "Doc.")
        self.assertEqual(source.module.members["f"].lineno, 2)

    def test_a_latin1_declared_file_renders_the_hand_authored_bytes(self):
        raw = "# -*- coding: latin-1 -*-\n\"\"\"café\"\"\"\n".encode("latin-1")
        sel = load_mod.load_selection([("m.py", raw)])
        source = sel.sources[0]
        self.assertEqual(source.text, "# -*- coding: latin-1 -*-\n\"\"\"café\"\"\"\n")
        self.assertEqual(source.module.docstring.value, "café")

    def test_an_undecodable_file_still_refuses_named(self):
        raw = b"\xff\xfe not a real BOM and not valid under any detected encoding \x81"
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection([("bad.py", raw)])
        self.assertEqual(ctx.exception.path, "bad.py")
        self.assertTrue(ctx.exception.cause.startswith("cannot decode as UTF-8 ("), ctx.exception.cause)

    def test_a_declared_encoding_failure_names_that_encoding(self):
        raw = "# coding: ascii\nx = 'é'\n".encode("utf-8")
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection([("m.py", raw)])
        self.assertTrue(ctx.exception.cause.startswith("cannot decode as ascii ("), ctx.exception.cause)

    def test_an_unknown_declared_encoding_refuses_as_a_declaration(self):
        raw = b"# coding: nosuch\nx = 1\n"
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection([("m.py", raw)])
        self.assertTrue(ctx.exception.cause.startswith("encoding declaration refused ("),
                        ctx.exception.cause)


class RecursionBoundTests(unittest.TestCase):
    def test_deep_nesting_refuses_naming_nesting_exhausts_recursion(self):
        """Built at test time (never committed): 60 nested `def`s, well
        under CPython's own ~100-level indentation-depth SyntaxError guard,
        with sys.setrecursionlimit lowered, relative to the depth this test
        itself already runs at (never an absolute number -- a test runner
        or coverage wrapper can add frames of its own), so Griffe's own
        (Python-level, recursive) visitor walk overflows deterministically."""
        lines = []
        depth = 60
        for i in range(depth):
            lines.append("    " * i + f"def f{i}():")
        lines.append("    " * depth + "pass")
        src = "\n".join(lines) + "\n"

        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(_current_stack_depth() + 40)
        try:
            with self.assertRaises(ExtractionRefusal) as ctx:
                load_mod.load_selection([("deep.py", src.encode())])
        finally:
            sys.setrecursionlimit(old_limit)
        self.assertEqual(ctx.exception.path, "deep.py")
        self.assertEqual(ctx.exception.cause, "nesting exhausts recursion")


class LocalPlacementErrorTests(unittest.TestCase):
    def test_orphan_overload_variants_refuse_naming_qualified_path(self):
        src = (
            "from typing import overload\n\n\n"
            "def outer():\n"
            "    @overload\n"
            "    def ov(a: int) -> int: ...\n"
            "    @overload\n"
            "    def ov(a: str) -> str: ...\n"
            "    return None\n"  # no `def ov(a):` implementation
        )
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection([("bad.py", src.encode())])
        self.assertEqual(ctx.exception.path, "bad.outer.ov")
        self.assertIn("no matching implementation", ctx.exception.cause)


class OtherVisitExceptionTests(unittest.TestCase):
    def test_unnamed_exception_refuses_naming_path_and_exception_class(self):
        """Patches griffe.visit itself, since a real Griffe-internal crash
        beyond LocalPlacementError/RecursionError has no known reproduction
        here — the wrapping behavior is what this test proves, not a
        specific trigger."""
        real_visit = griffe.visit

        def _boom(*args, **kwargs):
            raise ValueError("synthetic failure")

        griffe.visit = _boom
        try:
            with self.assertRaises(ExtractionRefusal) as ctx:
                load_mod.load_selection([("ok.py", b'"""doc"""\n')])
        finally:
            griffe.visit = real_visit
        self.assertEqual(ctx.exception.path, "ok.py")
        # The same `Type: message` shape the dispatcher gives an extractor
        # exception; the dispatcher's one reporting function sanitizes it.
        self.assertEqual(ctx.exception.cause, "ValueError: synthetic failure")

    def test_never_a_traceback_string_in_the_cause(self):
        real_visit = griffe.visit

        def _boom(*args, **kwargs):
            raise ValueError("synthetic failure")

        griffe.visit = _boom
        try:
            with self.assertRaises(ExtractionRefusal) as ctx:
                load_mod.load_selection([("ok.py", b'"""doc"""\n')])
        finally:
            griffe.visit = real_visit
        self.assertNotIn("Traceback", ctx.exception.cause)
        self.assertNotIn("\n", ctx.exception.cause)


class ExtensionSetTests(unittest.TestCase):
    def test_exactly_one_extension_function_local_defs(self):
        real_extensions = griffe.Extensions
        seen: list[tuple] = []

        class _Spy(real_extensions):
            def __init__(self, *exts):
                seen.append(exts)
                super().__init__(*exts)

        griffe.Extensions = _Spy
        try:
            load_mod.load_selection([("ok.py", b'"""doc"""\n')])
        finally:
            griffe.Extensions = real_extensions
        self.assertEqual(len(seen), 1)
        (exts,) = seen
        self.assertEqual(len(exts), 1)
        self.assertIsInstance(exts[0], load_mod.FunctionLocalDefs)

    def test_load_extensions_is_never_called(self):
        real_load_extensions = griffe.load_extensions

        def _raise(*args, **kwargs):
            raise AssertionError("griffe.load_extensions must never be called")

        griffe.load_extensions = _raise
        try:
            load_mod.load_selection([("ok.py", b'"""doc"""\n')])
        finally:
            griffe.load_extensions = real_load_extensions


class StubSeparationTests(unittest.TestCase):
    BASE = FIXTURES / "synthetic" / "src"

    def test_py_and_pyi_produce_two_sources_never_merged(self):
        units = _units(self.BASE, ["stubbed.py", "stubbed.pyi"])
        sel = load_mod.load_selection(units)
        self.assertEqual({s.rel_path for s in sel.sources}, {"stubbed.py", "stubbed.pyi"})
        by_rel = {s.rel_path: s for s in sel.sources}
        self.assertFalse(by_rel["stubbed.py"].is_stub)
        self.assertTrue(by_rel["stubbed.pyi"].is_stub)
        # The .py module carries no stub-only member (VERSION lives only in
        # the .pyi); the .pyi carries no runtime-only member (_CACHE lives
        # only in the .py) — proof the two were never merged into one.
        py_module = by_rel["stubbed.py"].module
        pyi_module = by_rel["stubbed.pyi"].module
        self.assertIn("_CACHE", py_module.members)
        self.assertNotIn("_CACHE", pyi_module.members)
        self.assertIn("VERSION", pyi_module.members)
        self.assertNotIn("VERSION", py_module.members)
        # Never the same Module object, and never each other's filepath.
        self.assertIsNot(py_module, pyi_module)
        self.assertNotEqual(py_module.filepath, pyi_module.filepath)

    def test_stub_only_module_loads(self):
        units = _units(self.BASE, ["stub_only.pyi"])
        sel = load_mod.load_selection(units)
        self.assertEqual(len(sel.sources), 1)
        self.assertTrue(sel.sources[0].is_stub)
        self.assertIn("ping", sel.sources[0].module.members)

    def test_stub_and_py_land_in_separate_collections(self):
        units = _units(self.BASE, ["stubbed.py", "stubbed.pyi"])
        sel = load_mod.load_selection(units)
        self.assertIn("stubbed", sel.collection.members)
        self.assertIn("stubbed", sel.stub_collection.members)
        self.assertIsNot(sel.collection.members["stubbed"], sel.stub_collection.members["stubbed"])


class DeterminismTests(unittest.TestCase):
    BASE = FIXTURES / "synthetic" / "src"
    REL_PATHS = ["members.py", "overloads.py", "pep695.py", "pkg/__init__.py", "pkg/_impl.py", "pkg/star.py"]

    @staticmethod
    def _member_paths(selection) -> list[tuple[str, str, bool]]:
        """(module path, member name, is_alias) triples, own members only.

        Never resolves an alias target: an alias in this fixture set can
        point outside the selection (`members.py` imports `abc.ABC`), and
        resolving it would try a real Griffe lookup this loader deliberately
        never performs (module docstring: "Aliases ... stay unresolved").
        """
        triples: list[tuple[str, str, bool]] = []
        for source in selection.sources:
            for name, member in sorted(source.module.members.items()):
                triples.append((source.module.path, name, member.is_alias))
        return triples

    def test_reloading_the_same_units_is_byte_identical(self):
        units = _units(self.BASE, self.REL_PATHS)
        first = load_mod.load_selection(units)
        second = load_mod.load_selection(units)
        self.assertEqual(self._member_paths(first), self._member_paths(second))
        self.assertEqual(
            [s.rel_path for s in first.sources],
            [s.rel_path for s in second.sources],
        )

    def test_loading_order_is_independent_of_input_order(self):
        units = _units(self.BASE, self.REL_PATHS)
        forward = load_mod.load_selection(units)
        backward = load_mod.load_selection(list(reversed(units)))
        self.assertEqual(self._member_paths(forward), self._member_paths(backward))
        self.assertEqual(
            [s.rel_path for s in forward.sources],
            [s.rel_path for s in backward.sources],
        )


class ModuleNamingTests(unittest.TestCase):
    BASE = FIXTURES / "synthetic" / "src"

    def test_package_and_child_names_match_the_adr_example(self):
        units = _units(self.BASE, ["pkg/__init__.py", "pkg/_impl.py", "pkg/star.py"])
        sel = load_mod.load_selection(units)
        self.assertEqual(sel.module_names["pkg/__init__.py"], "pkg")
        self.assertEqual(sel.module_names["pkg/_impl.py"], "pkg._impl")
        self.assertEqual(sel.module_names["pkg/star.py"], "pkg.star")
        pkg_module = sel.collection.members["pkg"]
        self.assertIn("_impl", pkg_module.members)
        self.assertIs(pkg_module.members["_impl"], pkg_module.members["_impl"])
        self.assertIs(pkg_module.members["_impl"], sel.sources[
            [s.rel_path for s in sel.sources].index("pkg/_impl.py")
        ].module)

    def test_a_flat_script_uses_its_own_stem(self):
        sel = load_mod.load_selection(_units(self.BASE, ["members.py"]))
        self.assertEqual(sel.module_names["members.py"], "members")
        self.assertIn("members", sel.collection.members)

    def test_non_identifier_stem_still_loads_under_an_escaped_name(self):
        raw = b'"""doc"""\ndef f(): pass\n'
        sel = load_mod.load_selection([("crux/scripts/crux-config.py", raw)])
        dotted = sel.module_names["crux/scripts/crux-config.py"]
        self.assertTrue(dotted)
        # The escaped last segment (the invalid stem "crux-config") must
        # start with a digit: valid identifiers never do, which is exactly
        # what makes it impossible for the escaped name to collide with a
        # valid-identifier path (module docstring's `_safe_segment` note).
        last_segment = dotted.rsplit(".", 1)[-1]
        self.assertTrue(last_segment[0].isdigit())
        self.assertFalse(last_segment.isidentifier())
        # The valid segments ("crux", "scripts") pass through unescaped.
        self.assertTrue(dotted.startswith("crux.scripts."))
        self.assertNotIn("crux-config", dotted)

    def test_escaped_segment_never_equals_a_valid_identifier(self):
        # `_safe_segment`'s core guarantee (module docstring): an escaped
        # segment always starts with a digit, so it can never equal any
        # legal (unescaped) identifier segment.
        escaped = load_mod._safe_segment("crux-config")  # noqa: SLF001 — unit-level test of the escape scheme itself
        self.assertTrue(escaped[0].isdigit())
        self.assertFalse(escaped.isidentifier())
        # A real identifier is returned verbatim, never escaped.
        self.assertEqual(load_mod._safe_segment("crux_config"), "crux_config")


class PackageModuleLeafCollisionTests(unittest.TestCase):
    """R-E6: `pkg/mod.py` and `pkg/mod/__init__.py`, both selected, bind the
    same leaf name `mod`. Python's own import system prefers the package;
    this loader refuses the pair (fail closed) rather than let one silently
    replace the other in the Griffe tree."""

    def test_file_and_package_of_the_same_name_refuses(self):
        units = [
            ("pkg/mod.py", b"def f():\n    pass\n"),
            ("pkg/mod/__init__.py", b"x = 1\n"),
        ]
        with self.assertRaises(ExtractionRefusal) as ctx:
            load_mod.load_selection(units)
        self.assertEqual(ctx.exception.path, "pkg/mod.py")
        self.assertIn("pkg/mod/__init__.py", ctx.exception.cause)


class SelectionSortingTests(unittest.TestCase):
    def test_sources_are_sorted_by_rel_path_regardless_of_input_order(self):
        base = FIXTURES / "synthetic" / "src"
        units = _units(base, ["overloads.py", "members.py", "pep695.py"])
        sel = load_mod.load_selection(units)
        self.assertEqual(
            [s.rel_path for s in sel.sources],
            sorted(s.rel_path for s in sel.sources),
        )


if __name__ == "__main__":
    unittest.main()
