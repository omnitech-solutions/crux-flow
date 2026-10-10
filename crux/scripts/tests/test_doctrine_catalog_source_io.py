"""Optional read transport preserves canonical doctrine and citation inputs."""
import contextlib
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import doctrine_projection as dp
import summaries_projection as sp
from admission_source_io import SourceIO, SourceIORefusal


spec = importlib.util.spec_from_file_location(
    "_catalog_source_io", Path(__file__).resolve().parents[1] / "generate-rules-catalog.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)

PATH_OPERATIONS = ("read_bytes", "read_text", "resolve", "glob", "rglob",
                   "is_file", "is_dir", "exists", "is_symlink", "stat", "lstat")


@contextlib.contextmanager
def no_path_reads():
    with contextlib.ExitStack() as stack:
        for name in PATH_OPERATIONS:
            stack.enter_context(patch.object(Path, name, side_effect=AssertionError("unguarded " + name)))
        yield


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.tree = self.root / "nested/knowledge"
        self.inv = self.tree / "invariants"
        self.doctrine = self.tree / "adrs/doctrine"
        self.plugin = self.root / "crux"
        for directory in (self.inv, self.doctrine, self.plugin / "skills/deep", self.plugin / "scripts"):
            directory.mkdir(parents=True)
        (self.root / ".bionic.yml").write_text("docs_dir: nested/knowledge\n")
        (self.inv / "constraint.md").write_bytes(
            b"---\r\nid: INV-0001\r\nratification: ratified\r\nrelated_adrs: [ADR-0001]\r\n---\r\n"
            b"## The invariant\r\nPreserve this constraint.\r\n## Why\r\nFixture.\r\n")
        (self.inv / "index.md").write_text("Index only.\n")
        self.ledger = self.doctrine / "reconciliations.yml"
        self.ledger.write_bytes(b'config_version: "1"\r\nreconciliations: []\r\n')
        (self.root / "source.py").write_text("pass\n")

    def source_io(self):
        io = SourceIO(self.root)
        self.addCleanup(io.close)
        return io


class DoctrineSourceIOTests(Fixture):
    def test_default_calls_preserve_existing_callee_signatures(self):
        cases = (
            (dp.invariants_dir, sp, "resolve_tree", self.tree),
            (dp.doctrine_dir, sp, "adrs_dir", self.tree / "adrs"),
            (dp.reconciliations_path, dp, "doctrine_dir", self.doctrine),
            (dp.read_invariants, dp, "invariants_dir", self.inv),
            (dp.read_reconciliations, dp, "reconciliations_path", self.ledger),
            (dp.reconciliations_sha256, dp, "reconciliations_path", self.ledger),
            (dp._doctrine_destinations, sp, "resolve_tree", self.tree),
        )
        for caller, owner, name, result in cases:
            with self.subTest(caller=caller.__name__):
                with patch.object(owner, name, return_value=result) as callee:
                    caller(self.root)
                callee.assert_called_once_with(self.root)

    def test_default_input_digest_calls_preserve_existing_callee_signatures(self):
        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(sp, name, return_value=name))
                     for name in ("adr_frontmatter_sha256", "observations_sha256", "survey_receipts_sha256")}
            ledger = stack.enter_context(patch.object(dp, "reconciliations_sha256", return_value="raw"))
            dp.input_digests(self.root, [], [], None, self.tree / "observations")
        mocks["adr_frontmatter_sha256"].assert_called_once_with(self.tree / "adrs")
        for name in ("observations_sha256", "survey_receipts_sha256"):
            mocks[name].assert_called_once_with(self.tree / "observations")
        ledger.assert_called_once_with(self.root)

    def test_tree_helpers_and_invariants_use_supplied_io(self):
        expected = dp.read_invariants(self.root)
        io = self.source_io()
        with no_path_reads():
            self.assertEqual(dp.invariants_dir(self.root, _source_io=io), self.inv)
            self.assertEqual(dp.doctrine_dir(self.root, _source_io=io), self.doctrine)
            self.assertEqual(dp.reconciliations_path(self.root, _source_io=io), self.ledger)
            self.assertEqual(dp.read_invariants(self.root, _source_io=io), expected)

    def test_reconciliation_parser_and_raw_digest_preserve_newline_semantics(self):
        expected = dp.read_reconciliations(self.root)
        digest = hashlib.sha256(self.ledger.read_bytes()).hexdigest()
        io = self.source_io()
        with no_path_reads():
            self.assertEqual(dp.read_reconciliations(self.root, _source_io=io), expected)
            self.assertEqual(dp.reconciliations_sha256(self.root, _source_io=io), digest)

    def test_malformed_reconciliations_preserve_canonical_error(self):
        self.ledger.write_text('config_version: "2"\nreconciliations: []\n')
        with self.assertRaises(dp.DoctrineValidationError) as default:
            dp.read_reconciliations(self.root)
        io = self.source_io()
        with no_path_reads(), self.assertRaises(dp.DoctrineValidationError) as guarded:
            dp.read_reconciliations(self.root, _source_io=io)
        self.assertEqual(default.exception.problems, guarded.exception.problems)

    def test_missing_inputs_preserve_empty_and_null(self):
        self.ledger.unlink()
        io = self.source_io()
        with no_path_reads():
            self.assertEqual(dp.read_reconciliations(self.root, _source_io=io), [])
            self.assertIsNone(dp.reconciliations_sha256(self.root, _source_io=io))

    def test_input_digests_forward_same_transport_to_each_owner(self):
        io = self.source_io()
        observed = self.tree / "observations"
        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(sp, name, return_value=name))
                     for name in ("adr_frontmatter_sha256", "observations_sha256", "survey_receipts_sha256")}
            stack.enter_context(patch.object(dp, "reconciliations_sha256", return_value="raw"))
            with no_path_reads():
                actual = dp.input_digests(self.root, [], [], None, observed, _source_io=io)
            mocks["adr_frontmatter_sha256"].assert_called_once_with(self.tree / "adrs", _source_io=io)
            for name in ("observations_sha256", "survey_receipts_sha256"):
                mocks[name].assert_called_once_with(observed, _source_io=io)
            self.assertEqual(actual["schema"], "4")
            self.assertEqual(actual["reconciliations_sha256"], "raw")

    def test_evidence_delegates_to_canonical_owner_with_same_io(self):
        io = self.source_io()
        with patch.object(dp.oe, "evidence_entry_resolves", return_value=True) as owner:
            self.assertTrue(dp.evidence_resolves("source.py:1-1", self.root, _source_io=io))
        owner.assert_called_once_with(self.root, "source.py:1-1", _source_io=io)

    def test_destinations_use_transport_and_refuse_symlink_or_temporary(self):
        io = self.source_io()
        with no_path_reads():
            dp._doctrine_destinations(self.root, _source_io=io)
        for name in ("index.md", "index.md.tmp"):
            path = self.doctrine / name
            path.symlink_to(self.root / "source.py")
            io = self.source_io()
            with no_path_reads(), self.assertRaises(OSError):
                dp._doctrine_destinations(self.root, _source_io=io)
            path.unlink()


class CatalogSourceIOTests(Fixture):
    def test_directory_walk_does_not_reenter_for_nested_roster(self):
        (self.plugin / "skills/deep/a.md").write_text("rule:nested\n")
        expected = catalog.cited_slug_paths(self.plugin)
        io = self.source_io()
        original = catalog._guarded_citing_paths
        active = False
        def enumeration_boundary(*args):
            nonlocal active
            self.assertFalse(active, "directory descent reentered the Python walk")
            active = True
            try:
                yield from original(*args)
            finally:
                active = False
        with patch.object(catalog, "_guarded_citing_paths", side_effect=enumeration_boundary):
            with no_path_reads():
                self.assertEqual(catalog.cited_slug_paths(self.plugin, _source_io=io), expected)

    def test_excluded_subtrees_are_pruned_before_metadata_or_kind(self):
        directory = self.plugin / "scripts/tests"
        directory.mkdir()
        (directory / "ignored.md").write_text("rule:excluded\n")
        io = self.source_io()
        original_kind, original_metadata = io.kind, io.metadata
        def kind(path):
            self.assertNotIn("tests", path.relative_to(self.plugin).parts)
            return original_kind(path)
        def metadata(path):
            self.assertNotIn("tests", path.relative_to(self.plugin).parts)
            return original_metadata(path)
        with patch.object(io, "kind", side_effect=kind), patch.object(io, "metadata", side_effect=metadata):
            with no_path_reads():
                self.assertEqual(catalog.cited_slug_paths(self.plugin, _source_io=io), ({}, []))

    def test_directory_is_guarded_before_enumeration(self):
        io = self.source_io()
        guarded = set()
        original_kind, original_glob = io.kind, io.glob
        def kind(path):
            result = original_kind(path)
            if result == "directory":
                guarded.add(path)
            return result
        def glob(path, pattern):
            self.assertIn(path, guarded)
            return original_glob(path, pattern)
        with patch.object(io, "kind", side_effect=kind), patch.object(io, "glob", side_effect=glob):
            with no_path_reads():
                self.assertEqual(catalog.cited_slug_paths(self.plugin, _source_io=io), ({}, []))

    def test_plugin_resolution_refusal_cannot_become_advisory(self):
        (self.plugin / "skills/a.md").write_text("rule:one\n")
        io = self.source_io()
        resolve = io.resolve
        def guarded_resolve(path):
            if path == self.plugin:
                raise SourceIORefusal("fixture-refused")
            return resolve(path)
        with patch.object(io, "resolve", side_effect=guarded_resolve):
            with no_path_reads(), self.assertRaises(SourceIORefusal):
                catalog.cited_slug_paths(self.plugin, _source_io=io)

    def test_contained_aliases_and_directory_aliases_preserve_default_scan(self):
        (self.plugin / "scripts/target.py").write_text("rule:inside\n")
        (self.plugin / "skills/alias.md").symlink_to(self.plugin / "scripts/target.py")
        (self.plugin / "skills/directory").symlink_to(self.plugin / "scripts", target_is_directory=True)
        (self.plugin / "skills/outside.md").symlink_to(self.root / "source.py")
        expected = catalog.cited_slug_paths(self.plugin)
        io = self.source_io()
        with no_path_reads():
            self.assertEqual(catalog.cited_slug_paths(self.plugin, _source_io=io), expected)
        self.assertTrue(expected[1][0]["advisory"])

    def test_scan_preserves_exclusions_token_grammar_and_line_locations(self):
        (self.plugin / "skills/deep/skill.md").write_bytes(
            b"rule:alpha rule:<placeholder> rule:CAPS\r\nrule:beta-2 rule:alpha\r\n")
        (self.plugin / "scripts/module.py").write_text("rule:alpha\n")
        for name in ("tests", "catalog", "__pycache__"):
            directory = self.plugin / "scripts" / name
            directory.mkdir()
            (directory / "ignored.md").write_text("rule:excluded\n")
        (self.plugin / "skills/deep/asset.bin").write_bytes(b"rule:binary")
        expected = catalog.cited_slug_paths(self.plugin)
        io = self.source_io()
        with no_path_reads():
            actual = catalog.cited_slug_paths(self.plugin, _source_io=io)
        self.assertEqual(actual, expected)
        self.assertEqual(set(actual[0]), {"alpha", "beta-2"})

    def test_scan_preserves_size_and_file_count_refusals(self):
        (self.plugin / "skills/a.md").write_text("rule:one\n")
        (self.plugin / "skills/b.md").write_text("rule:two\n")
        for bounds in ({"MAX_SCANNED_FILES": 1}, {"MAX_FILE_BYTES": 1}):
            with patch.multiple(catalog, **bounds):
                expected = catalog.cited_slug_paths(self.plugin)
                io = self.source_io()
                with no_path_reads():
                    self.assertEqual(catalog.cited_slug_paths(self.plugin, _source_io=io), expected)

    def test_transport_refusal_propagates_before_any_unguarded_read(self):
        (self.plugin / "skills/a.md").write_text("rule:one\n")
        io = self.source_io()
        with patch.object(io, "glob", side_effect=SourceIORefusal("fixture-refused")):
            with no_path_reads(), self.assertRaises(SourceIORefusal):
                catalog.cited_slug_paths(self.plugin, _source_io=io)
