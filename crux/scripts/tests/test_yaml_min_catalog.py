"""Tests for the strict catalog read path in _yaml_min.py.

`load_catalog_yaml` is a SECOND, stricter entry point beside `load_yaml`, not a
change to it. The catalog files are hand-authored sources of truth whose pinned
YAML subset forbids five constructs that `safe_load` accepts happily, so the
subset is a convention until a loader refuses them.

Two properties are pinned here and are easy to lose:

  1. The two parse paths agree on REJECTION as well as on acceptance. A refusal
     that only fires under PyYAML would let the fallback path accept a document
     the strict path refuses.
  2. `load_yaml` is UNCHANGED. It parses every promptbook and run snapshot in
     the tree, whose literal block scalars carry arbitrary Markdown; narrowing
     it would break them, and a line-oriented refusal scan cannot tell a `*`
     opening a scalar line from an alias.

Stdlib only. Run: uv run python3 -m unittest crux.scripts.tests.test_yaml_min_catalog
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import _yaml_min as Y  # noqa: E402

CATALOG_DIR = SCRIPTS_DIR.parent / "catalog"
MODELS_YML = CATALOG_DIR / "models.yml"
BUNDLES_YML = CATALOG_DIR / "bundles.yml"


class _NoPyYAML:
    """Import hook making `import yaml` raise, so the fallback path runs.

    Mirrors the stubbing pattern in test_validate_promptbook.py: the loader
    branches on a live `import yaml` inside the function, so a module-level
    capability flag would not respond to the stub.
    """

    def find_spec(self, name, path=None, target=None):
        if name == "yaml":
            raise ImportError("stubbed: PyYAML unavailable")
        return None


class _WithoutPyYAML:
    def __enter__(self):
        self._hook = _NoPyYAML()
        self._saved = sys.modules.pop("yaml", None)
        sys.meta_path.insert(0, self._hook)
        return self

    def __exit__(self, *exc):
        sys.meta_path.remove(self._hook)
        if self._saved is not None:
            sys.modules["yaml"] = self._saved
        return False


class ShippedCatalogParityTests(unittest.TestCase):
    """Parity is proven PER FILE, never inherited: the two catalog files
    exercise different corners of the pinned subset."""

    def _assert_parity(self, path: Path):
        text = path.read_text(encoding="utf-8")
        via_pyyaml = Y.load_catalog_yaml(text)
        with _WithoutPyYAML():
            via_minimal = Y.load_catalog_yaml(text)
        self.assertEqual(via_pyyaml, via_minimal, f"{path.name}: the two parse paths disagree")
        return via_pyyaml

    def test_models_yml_parses_the_same_on_both_paths(self):
        parsed = self._assert_parity(MODELS_YML)
        self.assertEqual(parsed["schema_version"], "4")

    def test_bundles_yml_parses_the_same_on_both_paths(self):
        parsed = self._assert_parity(BUNDLES_YML)
        self.assertTrue(parsed)
        for value in parsed.values():
            self.assertIsInstance(value["default_provision"], bool)

    def test_verified_dates_stay_strings_on_both_paths(self):
        # Quoted on purpose: an unquoted date resolves to datetime.date under
        # safe_load, and rule V4 parses the field as text.
        parsed = self._assert_parity(MODELS_YML)
        for level in parsed["levels"].values():
            self.assertIsInstance(level["codex"]["verified"], str)


class RefusalTests(unittest.TestCase):
    """Each refused construct, with the two paths agreeing on the rejection."""

    # Refused by the CONTRACT — the five constructs, duplicate keys, and the
    # root-shape rule. Both paths reach the same verdict class, because the
    # construct scan is textual and the root rule runs after either parse.
    CONTRACT_CASES = {
        "anchor": "a: &anchor 1\nb: 2\n",
        "alias": "a: &anchor 1\nb: *anchor\n",
        "merge_key": "base:\n  x: 1\nchild:\n  <<: base\n",
        "explicit_tag": 'a: !!str 1\n',
        "tag_directive": "%TAG ! tag:example.com,2000:\n---\na: 1\n",
        "yaml_directive": "%YAML 1.1\n---\na: 1\n",
        "second_document": "a: 1\n---\nb: 2\n",
        "duplicate_key": "a: 1\na: 2\n",
        "nested_duplicate_key": "top:\n  a: 1\n  a: 2\n",
        "sequence_root": "- one\n- two\n",
        "empty_file": "",
        "whitespace_only": "   \n\n",
        "comment_only": "# nothing but a comment\n",
    }

    # Refused by the PARSER. Without PyYAML the loader cannot tell "invalid
    # document" from "exceeds the minimal subset", so it raises the capability
    # error rather than a document verdict — the inherited PB-0026 contract,
    # stated here so the asymmetry is a decision and not a surprise.
    PARSE_FAILURE_CASES = {
        "scalar_root": "just a scalar\n",
    }

    def test_every_case_is_refused_on_the_pyyaml_path(self):
        for label, text in sorted({**self.CONTRACT_CASES, **self.PARSE_FAILURE_CASES}.items()):
            with self.subTest(case=label):
                with self.assertRaises(Y.CatalogYamlError):
                    Y.load_catalog_yaml(text)

    def test_contract_cases_are_refused_identically_on_the_minimal_path(self):
        # The load-bearing half: a refusal that only fires under PyYAML would
        # let the fallback accept a document the strict path refuses.
        with _WithoutPyYAML():
            for label, text in sorted(self.CONTRACT_CASES.items()):
                with self.subTest(case=label):
                    with self.assertRaises(Y.CatalogYamlError):
                        Y.load_catalog_yaml(text)

    def test_parse_failures_surface_as_a_capability_error_on_the_minimal_path(self):
        with _WithoutPyYAML():
            for label, text in sorted(self.PARSE_FAILURE_CASES.items()):
                with self.subTest(case=label):
                    with self.assertRaises(Y.YamlCapabilityError):
                        Y.load_catalog_yaml(text)


class NoFalsePositiveTests(unittest.TestCase):
    """A conforming document is accepted, including the awkward-looking cases."""

    ACCEPTED = {
        "quoted_star": 'a: "*not an alias*"\n',
        "quoted_ampersand": 'a: "R&D"\n',
        "quoted_bang": 'a: "!important"\n',
        "inner_indicators": "a: x*y&z\n",
        "hash_inside_value": 'a: "sharp # not a comment"\n',
        "colon_inside_quoted_value": 'a: "provider: model"\n',
        "sequence_of_scalars": "a:\n  - one\n  - two\n",
        "nested_depth_three": "a:\n  b:\n    c: 1\n",
        "boolean_and_number": "a: false\nb: 3\n",
    }

    def test_accepted_on_both_paths_with_the_same_value(self):
        for label, text in sorted(self.ACCEPTED.items()):
            with self.subTest(case=label):
                via_pyyaml = Y.load_catalog_yaml(text)
                with _WithoutPyYAML():
                    via_minimal = Y.load_catalog_yaml(text)
                self.assertEqual(via_pyyaml, via_minimal)


class LoadYamlIsUnchangedTests(unittest.TestCase):
    """The promptbook path keeps its old behavior.

    Every book and run snapshot in the tree goes through `load_yaml`. If the
    refusals had landed there instead of in a separate entry point, a run
    snapshot whose `result:` block scalar happens to open a line with `*` would
    stop parsing — so this test is the guard on that design decision.
    """

    def test_load_yaml_accepts_a_document_the_catalog_path_refuses(self):
        text = "prompts:\n  - n: 1\n    result: |\n      *emphasis* at line start\n"
        self.assertIsInstance(Y.load_yaml(text), dict)
        with self.assertRaises(Y.CatalogYamlError):
            Y.load_catalog_yaml(text)

    def test_load_yaml_still_accepts_a_top_level_sequence(self):
        self.assertEqual(Y.load_yaml("- a\n- b\n"), ["a", "b"])

    def test_load_yaml_still_accepts_a_duplicate_key(self):
        # Not an endorsement — a statement that this test would catch a
        # narrowing of load_yaml, which is a change the promptbook corpus has
        # not been checked against.
        self.assertEqual(Y.load_yaml("a: 1\na: 2\n"), {"a": 2})


class WriterTests(unittest.TestCase):
    """The writer refuses to commit output it cannot read back."""

    DATA = {
        "one": {
            "name": "n",
            "description": 'has a colon: and a "quote" and an em-dash —',
            "audiences": ["a", "b"],
            "default_provision": False,
            "skills": ["s1", "s2"],
        }
    }

    def test_emitted_text_round_trips_on_both_paths(self):
        text = Y.dump_catalog_yaml(self.DATA)
        self.assertEqual(Y.load_catalog_yaml(text), self.DATA)
        with _WithoutPyYAML():
            self.assertEqual(Y.load_catalog_yaml(text), self.DATA)

    def test_emitted_text_stays_inside_the_pinned_subset(self):
        text = Y.dump_catalog_yaml(self.DATA)
        self.assertNotIn("!!", text, "an explicit tag is outside the subset")
        self.assertNotIn("'", text, "a single-quoted scalar is outside the subset")
        for line in text.splitlines():
            self.assertLess(len(line), 10_000, "a wrapped scalar would break fallback parity")

    def test_write_moves_into_place_and_preserves_a_header(self):
        import tempfile

        header = "# a hand-authored header\n# second line\n"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bundles.yml"
            Y.write_catalog_yaml(path, self.DATA, header=header)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(Y.leading_comment_block(text), header)
            self.assertEqual(Y.load_catalog_yaml(text), self.DATA)
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()), ["bundles.yml"])

    def test_boolean_survives_the_round_trip_as_a_boolean(self):
        text = Y.dump_catalog_yaml(self.DATA)
        for parsed in (Y.load_catalog_yaml(text), Y._normalize(Y._parse_minimal_yaml(text, strict=True))):
            self.assertIsInstance(parsed["one"]["default_provision"], bool)


class FlowCollectionRefusalTests(unittest.TestCase):
    """A flow collection hides every construct the line scan refuses.

    The first version of `_refuse_catalog_constructs` inspected only the FIRST
    character of a value token, so `a: {b: &x 1, c: *x}` walked past it: the
    token begins with `{`, not with an indicator. Both parse paths then did
    something, and they did DIFFERENT things — PyYAML expanded the alias, and
    the minimal parser kept the literal string `{b: &x 1, c: *x}`. So the hole
    was simultaneously an alias-expansion bypass (a 412-byte input reached
    10.5 GB RSS) and a silent two-path disagreement.

    Flow collections were never in the pinned subset, so the repair is to
    refuse `{` and `[` outright at the start of any catalog key or value token.
    That closes the bypass and restores agreement in the same edit.
    """

    FLOW_CASES = {
        "flow_map_hiding_an_anchor_and_alias": 'a: {b: &x 1, c: *x}\n',
        "flow_seq_hiding_an_anchor_and_alias": "a: [&x 1, *x]\n",
        "flow_map_hiding_a_merge_key": "base:\n  x: 1\na: {<<: base, c: 2}\n",
        "flow_map_hiding_a_tag": 'a: {b: !!str 5}\n',
        "flow_seq_hiding_a_tag": 'a: [!!str 5]\n',
        "plain_flow_map": "a: {b: 1}\n",
        "plain_flow_seq": "a: [1, 2]\n",
        "flow_at_the_root_of_a_sequence_item": "a:\n  - {b: 1}\n",
        "flow_key": "{a: 1}: v\n",
    }

    def test_every_flow_case_is_refused_on_the_pyyaml_path(self):
        for label, text in sorted(self.FLOW_CASES.items()):
            with self.subTest(case=label):
                with self.assertRaises(Y.CatalogYamlError):
                    Y.load_catalog_yaml(text)

    def test_every_flow_case_is_refused_identically_on_the_minimal_path(self):
        # The load-bearing half. Before the fix the two paths SILENTLY
        # DISAGREED on these inputs rather than both accepting them.
        with _WithoutPyYAML():
            for label, text in sorted(self.FLOW_CASES.items()):
                with self.subTest(case=label):
                    with self.assertRaises(Y.CatalogYamlError):
                        Y.load_catalog_yaml(text)

    def test_a_brace_inside_or_quoted_is_still_accepted(self):
        # The refusal is positional, like the indicator rule beside it: only a
        # token that BEGINS with the character is a flow collection.
        for label, text in sorted(
            {
                "quoted_brace": 'a: "{not flow}"\n',
                "quoted_bracket": 'a: "[not flow]"\n',
                "brace_inside_a_plain_scalar": "a: x{y}z\n",
                "bracket_inside_a_plain_scalar": "a: x[y]z\n",
            }.items()
        ):
            with self.subTest(case=label):
                via_pyyaml = Y.load_catalog_yaml(text)
                with _WithoutPyYAML():
                    self.assertEqual(Y.load_catalog_yaml(text), via_pyyaml)

    def test_the_empty_flow_forms_are_accepted_on_both_paths(self):
        # The carve-out, and why it is not a hole: `{}` and `[]` have no
        # contents, so no anchor, alias, tag or merge key can hide in one.
        # Block YAML has no other spelling for an empty collection, and the
        # emitter renders a bundle with no skills as `skills: []`, so refusing
        # them would hand the writer a legal value it cannot read back.
        for label, text in sorted(
            {
                "empty_flow_seq": "a: []\n",
                "empty_flow_map": "a: {}\n",
                "empty_flow_seq_spaced": "a: [ ]\n",
                "empty_flow_map_spaced": "a: { }\n",
            }.items()
        ):
            with self.subTest(case=label):
                via_pyyaml = Y.load_catalog_yaml(text)
                with _WithoutPyYAML():
                    self.assertEqual(Y.load_catalog_yaml(text), via_pyyaml)

    def test_an_empty_skills_list_survives_the_writer(self):
        import tempfile

        data = {"b": {"name": "n", "skills": [], "default_provision": False}}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bundles.yml"
            Y.write_catalog_yaml(path, data)
            self.assertEqual(Y.load_catalog_yaml(path.read_text(encoding="utf-8")), data)

    def test_load_yaml_is_not_narrowed_by_the_flow_refusal(self):
        # `load_yaml` parses every promptbook and run snapshot in the tree and
        # must keep accepting a flow collection.
        self.assertEqual(Y.load_yaml("a: {b: 1}\n"), {"a": {"b": 1}})


class CatalogSizeCapTests(unittest.TestCase):
    """A catalog file is hand-authored and small; 64 KiB is the same ceiling
    `bionic_config.py` puts on the other hand-authored YAML in this repo."""

    def test_a_document_over_the_cap_is_refused_on_both_paths(self):
        text = "a: 1\n" + ("# padding to exceed the cap\n" * 3000)
        self.assertGreater(len(text.encode("utf-8")), Y.CATALOG_MAX_BYTES)
        with self.assertRaises(Y.CatalogYamlError):
            Y.load_catalog_yaml(text)
        with _WithoutPyYAML():
            with self.assertRaises(Y.CatalogYamlError):
                Y.load_catalog_yaml(text)

    def test_the_shipped_catalog_files_are_far_under_the_cap(self):
        for path in (MODELS_YML, BUNDLES_YML):
            with self.subTest(path=path.name):
                self.assertLess(len(path.read_bytes()), Y.CATALOG_MAX_BYTES // 4)


class WriterSymlinkRefusalTests(unittest.TestCase):
    """[SECURITY:S5] The writer must not write THROUGH a symlink.

    `write_catalog_yaml` writes to a predictable `<path>.tmp` and then
    `os.replace`s it into place. `Path.write_bytes` FOLLOWS a link, so an
    attacker who can pre-create `bundles.yml.tmp` as a symlink gets the
    catalog's contents written into the link target — and, because
    `os.replace` then moves the tmp PATH, the victim keeps the write and the
    catalog path is left as whatever it was. Proven before the fix: the victim
    file was clobbered and `bundles.yml` remained a symlink.
    """

    DATA = {"one": {"name": "n", "default_provision": False}}

    def test_a_pre_created_tmp_symlink_is_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            victim = tmp / "victim.txt"
            victim.write_text("VICTIM\n", encoding="utf-8")
            path = tmp / "bundles.yml"
            (tmp / "bundles.yml.tmp").symlink_to(victim)
            with self.assertRaises(Y.CatalogYamlError):
                Y.write_catalog_yaml(path, self.DATA)
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            self.assertFalse(path.exists())

    def test_a_target_that_is_already_a_symlink_is_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            victim = tmp / "victim.txt"
            victim.write_text("VICTIM\n", encoding="utf-8")
            path = tmp / "bundles.yml"
            path.symlink_to(victim)
            with self.assertRaises(Y.CatalogYamlError):
                Y.write_catalog_yaml(path, self.DATA)
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            self.assertTrue(path.is_symlink())

    def test_a_plain_pre_existing_target_is_still_overwritten(self):
        # The refusal is about links, not about existence: the writer's normal
        # job is to replace the file it wrote last time.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bundles.yml"
            path.write_text("# stale\n", encoding="utf-8")
            Y.write_catalog_yaml(path, self.DATA)
            self.assertEqual(Y.load_catalog_yaml(path.read_text(encoding="utf-8")), self.DATA)


class WriterRefusalBranchTests(unittest.TestCase):
    """The dual-path re-read must be a live guard, not decoration.

    Before this test, deleting the re-read left both suites green — the branch
    had zero coverage, so the one requirement ADR-0073 clause 2 states as
    load-bearing was unpinned. Each case forces a non-round-tripping emission
    and asserts the writer refuses AND leaves the directory untouched.
    """

    DATA = {"one": {"name": "n"}}

    def _assert_refused_and_nothing_left(self, td: Path, path: Path):
        with self.assertRaises(Y.CatalogYamlError):
            Y.write_catalog_yaml(path, self.DATA)
        self.assertFalse(path.exists(), "no file may be created on a refused write")
        self.assertEqual(list(td.iterdir()), [], "no .tmp may be left behind")

    def test_an_emitter_that_drops_a_value_is_refused(self):
        import tempfile

        original = Y.dump_catalog_yaml
        try:
            Y.dump_catalog_yaml = lambda data: 'one:\n  name: "DIFFERENT"\n'
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                self._assert_refused_and_nothing_left(tmp, tmp / "bundles.yml")
        finally:
            Y.dump_catalog_yaml = original

    def test_an_emitter_whose_output_only_the_minimal_parser_misreads_is_refused(self):
        # Isolates the SECOND re-read. `0x1f` is a hex int to PyYAML and the
        # string "0x1f" to the minimal parser, so an emission of `name: 0x1f`
        # for the intended value 31 satisfies the first re-read and fails the
        # second. Deleting the fallback check leaves this the only red test.
        import tempfile

        original = Y.dump_catalog_yaml
        try:
            Y.dump_catalog_yaml = lambda data: "one:\n  name: 0x1f\n"
            self.assertEqual(Y.load_catalog_yaml("one:\n  name: 0x1f\n"), {"one": {"name": 31}})
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                path = tmp / "bundles.yml"
                with self.assertRaises(Y.CatalogYamlError):
                    Y.write_catalog_yaml(path, {"one": {"name": 31}})
                self.assertFalse(path.exists())
                self.assertEqual(list(tmp.iterdir()), [])
        finally:
            Y.dump_catalog_yaml = original

    def test_an_emitter_whose_output_only_pyyaml_misreads_is_refused(self):
        # Isolates the FIRST re-read — the exact mirror of the test above, and
        # the one case that test cannot cover. Same emission, `name: 0x1f`,
        # but the INTENDED value is the STRING "0x1f": PyYAML resolves the
        # plain scalar to the int 31 and diverges, while the minimal parser
        # keeps it a string and agrees. So the fallback re-read stays silent
        # and only the PyYAML re-read can refuse. Delete the PyYAML check and
        # this is the test that turns red; the sibling above stays green,
        # which is why one test could not pin both branches.
        import tempfile

        emission = "one:\n  name: 0x1f\n"
        intended = {"one": {"name": "0x1f"}}
        # State the asymmetry as a measurement, not as a comment: the first
        # path diverges from `intended` and the second matches it exactly.
        self.assertEqual(Y.load_catalog_yaml(emission), {"one": {"name": 31}})
        self.assertEqual(Y._normalize(Y._parse_minimal_yaml(emission, strict=True)), intended)

        original = Y.dump_catalog_yaml
        try:
            Y.dump_catalog_yaml = lambda data: emission
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                path = tmp / "bundles.yml"
                with self.assertRaises(Y.CatalogYamlError) as ctx:
                    Y.write_catalog_yaml(path, intended)
                self.assertIn("PyYAML path", str(ctx.exception))
                self.assertFalse(path.exists(), "no file may be created on a refused write")
                self.assertEqual(list(tmp.iterdir()), [], "no .tmp may be left behind")
        finally:
            Y.dump_catalog_yaml = original

    def test_an_emission_the_minimal_parser_cannot_read_at_all_is_refused(self):
        # A line-wrapped scalar: legal to PyYAML, outside the fallback subset.
        # The writer must still speak one exception type rather than leaking
        # the fallback parser's raw ValueError.
        import tempfile

        original = Y.dump_catalog_yaml
        try:
            Y.dump_catalog_yaml = lambda data: "one:\n  name: a\n    b\n"
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                self._assert_refused_and_nothing_left(tmp, tmp / "bundles.yml")
        finally:
            Y.dump_catalog_yaml = original

    def test_an_emitter_producing_an_unparseable_document_is_refused(self):
        import tempfile

        original = Y.dump_catalog_yaml
        try:
            Y.dump_catalog_yaml = lambda data: "one: &a 1\ntwo: *a\n"
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                self._assert_refused_and_nothing_left(tmp, tmp / "bundles.yml")
        finally:
            Y.dump_catalog_yaml = original


if __name__ == "__main__":
    unittest.main()
