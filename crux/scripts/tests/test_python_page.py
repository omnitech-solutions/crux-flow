"""Focused tests for the Python page renderer (crux/scripts/extractors/_py_page.py).

The byte-level contract is held by the golden tests (test_python_goldens.py),
which render every committed fixture set through the real dispatcher. These
tests pin three properties the goldens cannot show on their own:

- rendering never reads an Alias attribute that makes Griffe resolve it,
  because resolution may try to load a module (ADR-0131 clauses 1 and 11);
- an `__all__` Griffe leaves partly as an expression, or evaluates to an
  empty list from a call, is an unresolvable-`__all__` gap, never a list;
- two renders of one selection are byte-identical.

Stdlib only plus the project's griffelib; inputs are inline sources.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

import griffe

SCRIPTS = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "python_code_docs"


def _load(name: str, path: Path):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("extract_code_docs_dispatcher", SCRIPTS / "extract-code-docs.py")
load_mod = _load("crux_test_py_load", SCRIPTS / "extractors" / "_py_load.py")
page_mod = _load("crux_test_py_page", SCRIPTS / "extractors" / "_py_page.py")


def _render_all(units: list[tuple[str, str]], include_private: bool = True) -> dict[str, object]:
    selection = load_mod.load_selection([(p, t.encode("utf-8")) for p, t in units])
    return {s.rel_path: page_mod.render_page(s, selection, include_private=include_private)
            for s in selection.sources}


class AliasesAreNeverResolvedByGriffeTests(unittest.TestCase):
    """Reading `Alias.target`, `final_target` or a line attribute resolves the alias."""

    def test_rendering_the_exports_set_reads_no_resolving_alias_attribute(self):
        src = FIXTURES / "exports" / "src"
        units = [(str(p.relative_to(src)), p.read_text(encoding="utf-8"))
                 for p in sorted(src.rglob("*.py"))]

        def refuse(self, *a, **k):
            raise AssertionError("the renderer read an alias attribute that resolves it")

        patches = [mock.patch.object(griffe.Alias, name, property(refuse))
                   for name in ("target", "final_target", "lineno", "endlineno", "docstring")]
        for p in patches:
            p.start()
        try:
            pages = _render_all(units)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertIn("pkg/cycle_a.py", pages)

    def test_positive_control_the_patch_does_fire_on_a_resolving_read(self):
        selection = load_mod.load_selection([("m.py", b"from os import path\n")])
        alias = selection.sources[0].module.members["path"]
        with mock.patch.object(griffe.Alias, "target",
                               property(lambda self: (_ for _ in ()).throw(AssertionError("read")))):
            with self.assertRaises(AssertionError):
                alias.target  # noqa: B018 - the read is the assertion


class UnresolvableAllTests(unittest.TestCase):
    def _exports_section(self, source: str) -> str:
        body = _render_all([("m.py", source)])["m.py"].body
        return body.split("## Exports", 1)[1] if "## Exports" in body else ""

    def test_a_call_is_an_unresolvable_all_gap(self):
        section = self._exports_section('def f():\n    return []\n\n__all__ = f()\n')
        self.assertIn("is not statically resolvable", section)

    def test_an_expression_entry_is_an_unresolvable_all_gap_not_a_partial_list(self):
        source = 'from other import names\n__all__ = ["a"] + names\n\ndef a():\n    pass\n'
        page = _render_all([("m.py", source)])["m.py"]
        self.assertIn("is not statically resolvable", page.body)
        self.assertNotIn("`a`, ", page.body)
        self.assertEqual([g["kind"] for g in page.gaps], ["unresolvable_all"])

    def test_an_empty_literal_is_an_empty_static_all(self):
        page = _render_all([("m.py", "__all__ = []\n\ndef a():\n    pass\n")])["m.py"]
        self.assertNotIn("not statically resolvable", page.body)


class ClauseElevenAndTwelveDecisionTests(unittest.TestCase):
    """Dev-lead decisions DL-1 and DL-2 on the fixture review (2026-09-25)."""

    def test_an_unresolved_redundant_alias_is_an_export_gap_not_a_member(self):
        page = _render_all([("m.py", "import nowhere as nowhere\n")])["m.py"]
        self.assertIn("## Exports", page.body)
        self.assertIn("`nowhere` (line 1) is exported but does not resolve", page.body)
        self.assertNotIn("## `nowhere`", page.body)
        self.assertEqual([g["kind"] for g in page.gaps], ["unresolved_export"])

    def test_positive_control_a_resolved_redundant_alias_is_a_member(self):
        pages = _render_all([("m.py", "import other as other\n"),
                             ("other.py", '"""Other."""\n')])
        self.assertIn("## `other`", pages["m.py"].body)
        self.assertNotIn("does not resolve", pages["m.py"].body)

    def test_a_rebinding_note_names_its_branch_context(self):
        source = "def f():\n    pass\n\nf = 1\n"
        page = _render_all([("m.py", source)])["m.py"]
        self.assertIn("_Gap: `f` at unconditional (function, line 1) is rebound by assignment",
                      page.body)


class FixRoundOneTests(unittest.TestCase):
    """PB-0121 Prompt 8 fix round 1: findings M1, M2, S1 and S4."""

    def test_m1_an_export_in_a_package_below_the_repo_root_resolves(self):
        pages = _render_all([
            ("src/pkg/__init__.py", '__all__ = ["Thing"]\nfrom ._impl import f as f\nfrom ._impl import Thing\n'),
            ("src/pkg/_impl.py", 'def f():\n    pass\n\n\nclass Thing:\n    pass\n'),
        ])
        page = pages["src/pkg/__init__.py"]
        self.assertNotIn("does not resolve", page.body)
        self.assertEqual(page.gaps, [])
        self.assertIn("## `f`", page.body)
        self.assertIn("## `Thing`", page.body)

    def test_m1_positive_control_a_missing_target_below_the_root_is_still_a_gap(self):
        pages = _render_all([
            ("src/pkg/__init__.py", "from ._impl import gone as gone\n"),
            ("src/pkg/_impl.py", "def f():\n    pass\n"),
        ])
        page = pages["src/pkg/__init__.py"]
        self.assertIn("`gone` (line 1) is exported but does not resolve", page.body)

    def test_m2_a_same_named_nested_def_does_not_replace_its_enclosing_def(self):
        page = _render_all([("m.py", "def outer(a, b):\n    def outer():\n        pass\n")])["m.py"]
        self.assertIn("def outer(a, b)", page.body)
        self.assertIn("lines 1\u20133", page.body)

    def test_s1_a_non_ascii_import_line_renders_only_the_statement(self):
        source = "x = '\u00e9\u00e9\u00e9'; import other as other  # comment\n"
        page = _render_all([("m.py", source), ("other.py", '"""Other."""\n')])["m.py"]
        self.assertIn("```python\nimport other as other\n```", page.body)
        self.assertNotIn("# comment", page.body)

    def test_s4_two_redundant_imports_on_one_line_both_render(self):
        pages = _render_all([("m.py", "import a as a; import b as b\n"),
                             ("a.py", '"""A."""\n'), ("b.py", '"""B."""\n')])
        body = pages["m.py"].body
        self.assertIn("## `a`", body)
        self.assertIn("## `b`", body)
        self.assertIn("import b as b", body)


_GAP = "is exported but does not resolve inside the selection."


class SrcLayoutAbsoluteImportTests(unittest.TestCase):
    """PB-0121 Prompt 8 fix round 2: a package below the repository root
    re-exporting through absolute imports by its installable name."""

    def _page(self, units, rel):
        return _render_all(units)[rel]

    def test_absolute_re_export_by_installable_name_resolves(self):
        page = self._page([
            ("src/pkg/__init__.py", '__all__ = ["Thing"]\n'
                                    "from pkg.sub.m import value as value\n"
                                    "from pkg.sub.m import Thing\n"),
            ("src/pkg/sub/__init__.py", ""),
            ("src/pkg/sub/m.py", "value = 1\n\n\nclass Thing:\n    pass\n"),
        ], "src/pkg/__init__.py")
        self.assertNotIn(_GAP, page.body)
        self.assertEqual(page.gaps, [])
        self.assertIn("## `value`", page.body)
        self.assertIn("## `Thing`", page.body)
        self.assertIn("from pkg.sub.m import value as value", page.body)

    def test_the_same_name_under_two_import_roots_is_a_gap(self):
        page = self._page([
            ("src/pkg/__init__.py", "from pkg.m import value as value\n"),
            ("src/pkg/m.py", "value = 1\n"),
            ("vendor/pkg/__init__.py", ""),
            ("vendor/pkg/m.py", "value = 2\n"),
        ], "src/pkg/__init__.py")
        self.assertIn(f"`value` (line 1) {_GAP}", page.body)

    def test_a_repo_root_package_and_a_src_package_of_one_name_is_a_gap(self):
        page = self._page([
            ("src/pkg/__init__.py", "from pkg.m import value as value\n"),
            ("src/pkg/m.py", "value = 1\n"),
            ("pkg/__init__.py", ""),
            ("pkg/m.py", "value = 2\n"),
        ], "src/pkg/__init__.py")
        self.assertIn(f"`value` (line 1) {_GAP}", page.body)

    def test_a_package_under_another_root_does_not_answer_for_this_one(self):
        # src/myorg/ has no __init__.py, so src/myorg/json/ derives the false
        # root src/myorg. An importer under the root src must not see it.
        page = self._page([
            ("src/app/__init__.py", "from json.decoder import JSONDecoder as JSONDecoder\n"),
            ("src/myorg/json/__init__.py", ""),
            ("src/myorg/json/decoder.py", "class JSONDecoder:\n    pass\n"),
        ], "src/app/__init__.py")
        self.assertIn(f"`JSONDecoder` (line 1) {_GAP}", page.body)

    def test_positive_control_a_package_under_the_importers_own_root_resolves(self):
        page = self._page([
            ("src/app/__init__.py", "from json.decoder import JSONDecoder as JSONDecoder\n"),
            ("src/json/__init__.py", ""),
            ("src/json/decoder.py", "class JSONDecoder:\n    pass\n"),
        ], "src/app/__init__.py")
        self.assertNotIn(_GAP, page.body)
        self.assertIn("## `JSONDecoder`", page.body)

    def test_a_multi_hop_absolute_chain_resolves(self):
        page = self._page([
            ("src/pkg/__init__.py", "from pkg.relay import value as value\n"),
            ("src/pkg/relay.py", "from pkg.core.values import value as value\n"),
            ("src/pkg/core/__init__.py", ""),
            ("src/pkg/core/values.py", "from .impl import value as value\n"),
            ("src/pkg/core/impl.py", "value = 1\n"),
        ], "src/pkg/__init__.py")
        self.assertNotIn(_GAP, page.body)
        self.assertIn("## `value`", page.body)

    def test_a_cycle_through_two_spellings_of_one_module_is_a_gap(self):
        pages = _render_all([
            ("src/pkg/__init__.py", ""),
            ("src/pkg/a.py", "from pkg.b import x as x\n"),
            ("src/pkg/b.py", "from .a import x as x\n"),
        ])
        for rel in ("src/pkg/a.py", "src/pkg/b.py"):
            self.assertIn(f"`x` (line 1) {_GAP}", pages[rel].body)

    def test_known_limitation_a_namespace_package_under_src_is_a_gap(self):
        # src/myorg/ is a namespace package (no __init__.py): its import root
        # derives as src/myorg, so the installable name myorg.lib is not seen.
        page = self._page([
            ("src/myorg/lib/__init__.py", "from myorg.lib.core import value as value\n"),
            ("src/myorg/lib/core.py", "value = 1\n"),
        ], "src/myorg/lib/__init__.py")
        self.assertIn(f"`value` (line 1) {_GAP}", page.body)

    def test_known_limitation_a_namespace_nested_package_answers_for_its_own_name(self):
        # The false root src/myorg makes `json` a top-level package there, so a
        # module inside it re-exporting the standard library's json is reported
        # as a resolved member with no gap, although its true target (the
        # top-level json module, outside the selection) is owed a gap. Pinned
        # so a change to this behaviour is deliberate.
        page = self._page([
            ("src/myorg/json/__init__.py", "from json.decoder import JSONDecoder as JSONDecoder\n"),
            ("src/myorg/json/decoder.py", "class JSONDecoder:\n    pass\n"),
        ], "src/myorg/json/__init__.py")
        self.assertNotIn(_GAP, page.body)
        self.assertEqual(page.gaps, [])
        self.assertIn("## `JSONDecoder`", page.body)

    def test_a_relative_import_at_the_repo_root_is_unchanged_by_another_root(self):
        page = self._page([
            ("pkg/__init__.py", "from .m import value as value\n"),
            ("pkg/m.py", "value = 1\n"),
            ("src/pkg/__init__.py", ""),
            ("src/pkg/m.py", "value = 2\n"),
        ], "pkg/__init__.py")
        self.assertNotIn(_GAP, page.body)
        self.assertIn("## `value`", page.body)

    def test_loader_records_each_units_import_root(self):
        selection = load_mod.load_selection([
            ("src/pkg/__init__.py", b""), ("src/pkg/sub/m.py", b""),
            ("src/pkg/sub/__init__.py", b""), ("tool.py", b""), ("top/__init__.py", b""),
        ])
        self.assertEqual(selection.import_roots["src/pkg/sub/m.py"], "src")
        self.assertEqual(selection.root_names["src/pkg/sub/m.py"], "pkg.sub.m")
        self.assertEqual(selection.root_names["src/pkg/__init__.py"], "pkg")
        self.assertEqual(selection.import_roots["top/__init__.py"], "")
        self.assertNotIn("tool.py", selection.import_roots)
        self.assertEqual(selection.module_names["src/pkg/sub/m.py"], "src.pkg.sub.m")


class MaDescentGuardTests(unittest.TestCase):
    """A longer matched prefix in `_target`'s depth loop counts only when it
    descends from the previous depth's match in the same root; otherwise the
    export is a gap rather than a false resolution (ADR-0131 clause 11)."""

    def _page(self, units, rel):
        return _render_all(units)[rel]

    def test_an_unrelated_standalone_module_does_not_extend_the_match(self):
        # src/pkg/__init__.py imports "pkg.m.value" but has no src/pkg/m.py.
        # A second, unrelated tree has a plain top-level pkg/m.py (no
        # pkg/__init__.py there), whose path-derived name is exactly "pkg.m"
        # — a coincidental match at depth 2 that is not a descendant of
        # src/pkg's own module. Without the descent check this used to
        # silently resolve to the unrelated module's "value".
        page = self._page([
            ("src/pkg/__init__.py", "from pkg.m import value as value\n"),
            ("pkg/m.py", "value = 1\n"),
        ], "src/pkg/__init__.py")
        self.assertIn(f"`value` (line 1) {_GAP}", page.body)
        # The metadata row carries the same gap the page states.
        self.assertIn("value", [g.get("qualified_name") or g.get("name") for g in page.gaps])

    def test_a_same_named_member_inside_an_unrelated_module_does_not_answer(self):
        # A plain top-level module "pkg.py" (no real "pkg.m" submodule
        # anywhere) happens to define a class literally named "m" with an
        # attribute "value". Without the module-kind guard on intermediate
        # walked segments, the trailing member walk after depth 1's match
        # would treat that class as if it were the submodule "pkg.m".
        page = self._page([
            ("app.py", "from pkg.m import value as value\n"),
            ("pkg.py", "class m:\n    value = 1\n"),
        ], "app.py")
        self.assertIn(f"`value` (line 1) {_GAP}", page.body)

    def test_positive_control_a_real_descendant_chain_still_resolves(self):
        page = self._page([
            ("src/pkg/__init__.py", "from pkg.m import value as value\n"),
            ("src/pkg/m.py", "value = 1\n"),
        ], "src/pkg/__init__.py")
        self.assertNotIn(_GAP, page.body)
        self.assertIn("## `value`", page.body)


class AliasWalkNeverResolvesTests(unittest.TestCase):
    """`.members` is never read on a possible Alias mid-walk in
    `_lookup`/`_target`: that would trigger Griffe's own alias resolution and
    could raise AliasResolutionError for the whole run (ADR-0131 clause 11)."""

    def test_a_deeper_lookup_through_an_unresolved_alias_is_a_gap_not_a_crash(self):
        pages = _render_all([
            ("pkg/__init__.py", "from .util.x import y as y\n"),
            ("pkg/util.py", "from os.path import join as x\n"),
        ])
        page = pages["pkg/__init__.py"]
        self.assertIn(f"`y` (line 1) {_GAP}", page.body)


class HopBoundTests(unittest.TestCase):
    def test_a_chain_longer_than_the_hop_bound_is_a_gap_not_a_hang(self):
        """With the bound lowered to 2, a three-alias chain that really does
        resolve stops at the bound and reports unresolved; the same chain
        resolves under the real bound (positive control)."""
        units = [
            ("a.py", "from b import v as v\n"),
            ("b.py", "from c import v as v\n"),
            ("c.py", "from d import v as v\n"),
            ("d.py", "v = 1\n"),
        ]
        self.assertNotIn(_GAP, _render_all(units)["a.py"].body)
        saved = page_mod._MAX_ALIAS_HOPS
        page_mod._MAX_ALIAS_HOPS = 2
        try:
            self.assertIn(f"`v` (line 1) {_GAP}", _render_all(units)["a.py"].body)
        finally:
            page_mod._MAX_ALIAS_HOPS = saved


class SignatureEdgeCaseTests(unittest.TestCase):
    """Hand-authored expectations for signature shapes the goldens do not
    cover (fixtures README R7)."""

    def test_positional_only_marker_renders(self):
        body = _render_all([("m.py", "def f(a, b=1, /, c=2, *, d):\n    pass\n")])["m.py"].body
        self.assertIn("```python\ndef f(a, b=1, /, c=2, *, d)\n```", body)

    def test_an_unparseable_string_annotation_renders_as_its_literal(self):
        body = _render_all([("m.py", "def f(x: 'not valid )(') -> 'List[':\n    pass\n")])["m.py"].body
        self.assertIn("```python\ndef f(x: 'not valid )(') -> 'List['\n```", body)

    def test_an_attribute_renders_its_binding_statement_not_an_augassign_on_its_line(self):
        body = _render_all([("m.py", "x = 0\nx += 1; x = 2\n")])["m.py"].body
        self.assertIn("## `x`\n\n```python\nx = 2\n```\n\n_attribute · public · line 2_", body)

    def test_a_nested_leaf_collision_names_the_duplicate_binding(self):
        result = _render_all([("m.py", "def outer():\n    def inner():\n        pass\n"
                                        "    def inner():\n        pass\n")])["m.py"]
        self.assertEqual(result.body.count("### `outer.<locals>.inner`"), 1)
        self.assertIn("_nested function · public · lines 4–5_", result.body)
        self.assertEqual(
            [(g["kind"], g["qualified_name"], g["line"], g["shadowed_line"]) for g in result.gaps],
            [("duplicate_binding", "outer.<locals>.inner", 4, 2)],
        )


class DeterminismTests(unittest.TestCase):
    def test_two_renders_of_the_real_set_are_byte_identical(self):
        src = FIXTURES / "real" / "src"
        units = [(str(p.relative_to(src)), p.read_text(encoding="utf-8"))
                 for p in sorted(src.rglob("*.py"))]
        first = {k: (v.body, v.gaps) for k, v in _render_all(units).items()}
        second = {k: (v.body, v.gaps) for k, v in _render_all(units).items()}
        self.assertEqual(first, second)
        self.assertGreater(len(first), 0)


if __name__ == "__main__":
    unittest.main()
