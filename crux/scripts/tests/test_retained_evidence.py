"""Retained evidence is excluded before a containing project's source walk."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS)) if str(SCRIPTS) not in sys.path else None
import retained_evidence as retained


class PureHoldingPathTests(unittest.TestCase):
    def setUp(self):
        self.docs=Path("/synthetic-repository/knowledge/project")
        self.runs=self.docs/"promptbooks/runs"

    def test_pure_prefix_preserves_legacy_names_without_any_filesystem_operation(self):
        with patch.object(Path,"resolve",side_effect=AssertionError("resolve")), \
                patch.object(Path,"lstat",side_effect=AssertionError("lstat")), \
                patch.object(Path,"exists",side_effect=AssertionError("exists")), \
                patch("os.readlink",side_effect=AssertionError("readlink")):
            for name in ("PB-0001-choice","PB-0001--","OLD-PB-0001--original-"):
                with self.subTest(name=name):
                    holding=self.runs/name/"evidence/implementation-cycles/retained-repositories"
                    self.assertEqual(retained.holding_root_for_path(self.docs,holding/"source.py"),holding)
                    self.assertEqual(retained.holding_root_for_path(self.docs,holding),holding)

    def test_ordinary_and_nearby_paths_do_not_become_holdings(self):
        for path in (self.docs,self.runs/"PB-0001-choice/evidence/report.md",
                Path("/synthetic-repository/src/retained-repositories/source.py"),
                self.runs/"outer/PB-0001-choice/evidence/implementation-cycles/retained-repositories"):
            with self.subTest(path=path):self.assertIsNone(retained.holding_root_for_path(self.docs,path))

    def test_malformed_book_at_exact_tail_retains_refusal(self):
        for name in ("PB-0001-","PB-١٢٣٤-choice","ADR-PB-0001-choice","notes"):
            with self.subTest(name=name):
                path=self.runs/name/"evidence/implementation-cycles/retained-repositories/source.py"
                with self.assertRaises(retained.RetainedEvidenceRefusal):
                    retained.holding_root_for_path(self.docs,path)

    def test_unresolved_parent_suffix_never_erases_a_holding_prefix(self):
        holding=self.runs/"PB-0001-choice/evidence/implementation-cycles/retained-repositories"
        self.assertEqual(retained.holding_root_for_path(self.docs,holding/"jump/../../source.py"),holding)
        self.assertIsNone(retained.holding_root_for_path(self.docs,
            Path("/synthetic-repository/src/jump/../../source.py")))

    def test_relative_or_nul_arguments_refuse_without_filesystem_lookup(self):
        for docs,path in ((Path("docs"),self.docs),(self.docs,Path("source.py")),
                (self.docs,Path("/synthetic-repository/invalid\x00.py"))):
            with self.subTest(docs=docs,path=path), \
                    patch.object(Path,"resolve",side_effect=AssertionError("resolve")):
                with self.assertRaises(retained.RetainedEvidenceRefusal):
                    retained.holding_root_for_path(docs,path)


class RetainedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        self.docs = self.repo / "knowledge" / "project"
        self.docs.mkdir(parents=True)
        self.runs = self.docs / "promptbooks" / "runs"
        self.book = self.runs / "OLD-PB-0140-original-choice"
        self.holding = self.book / "evidence" / "implementation-cycles" / "retained-repositories"
        self.holding.mkdir(parents=True)

    def test_nested_tree_and_historical_prefix_are_anchored(self):
        self.assertTrue(retained.is_retained_evidence_path(self.docs, self.holding / "child" / "ADR-0146.md"))
        ordinary = self.repo / "src" / "retained-repositories" / "ADR-0146.md"
        self.assertFalse(retained.is_retained_evidence_path(self.docs, ordinary))

    def test_relative_paths_are_explicitly_docs_root_relative(self):
        relative = self.holding.relative_to(self.docs)
        self.assertTrue(retained.is_retained_evidence_path(self.docs, relative / "source.py"))
        self.assertFalse(retained.is_retained_evidence_path(self.docs, Path("src/source.py")))

    def test_docs_root_and_ordinary_directories_are_not_holdings(self):
        self.assertFalse(retained.is_retained_evidence_path(self.docs, self.docs))
        self.assertFalse(retained.is_retained_evidence_path(self.docs, self.book))

    def test_windows_drive_and_unc_configured_roots_refuse_before_stat(self):
        for spelling in ("C:/source.py", "C:\\source.py", "\\\\host\\share\\source.py"):
            with self.subTest(spelling=spelling), patch.object(Path, "resolve", side_effect=AssertionError("resolved hostile spelling")):
                with self.assertRaises(retained.RetainedEvidenceRefusal):
                    retained.is_retained_evidence_path(Path(spelling), self.repo / "ordinary.py")

    def test_ordinary_posix_candidate_names_preserve_literal_bytes(self):
        ordinary = self.repo / "src"
        ordinary.mkdir()
        for name in ("line\nbreak.rb", "back\\slash.rb", "control\x1f.rb", "del\x7f.rb",
                     "~literal.rb", "C:literal.rb"):
            with self.subTest(name=name):
                path = ordinary / name
                path.write_text("class Example; end\n")
                self.assertFalse(retained.is_retained_evidence_path(self.docs, path))
        for spelling in ("C:/source.py", "C:\\source.py", "\\\\host\\share\\source.py"):
            with self.subTest(spelling=spelling):
                self.assertFalse(retained.is_retained_evidence_path(self.docs, Path(spelling)))

    def test_odd_named_aliases_into_holding_are_excluded_before_held_probe(self):
        real_lstat = Path.lstat
        def bounded(path, *args, **kwargs):
            if self.holding in path.parents:
                raise AssertionError("held descendant probed")
            return real_lstat(path, *args, **kwargs)
        for name in ("alias\nsource.rb", "alias\\source.rb", "alias\x1fsource.rb"):
            with self.subTest(name=name):
                alias = self.repo / name
                alias.symlink_to(self.holding / "odd\nheld\\source.rb")
                with patch.object(Path, "lstat", bounded):
                    self.assertTrue(retained.is_retained_evidence_path(self.docs, alias))
                    self.assertTrue(retained.is_retained_evidence_path(self.docs,
                        self.holding / name))

    def test_odd_book_names_at_exact_holding_tail_still_refuse(self):
        for name in ("PB-0001-choice\n", "PB-0001-choice\\other", "PB-0001-choice\x1f"):
            with self.subTest(name=name):
                path = self.runs / name / "evidence" / "implementation-cycles" / "retained-repositories"
                with self.assertRaisesRegex(retained.RetainedEvidenceRefusal, "retained-evidence-layout-refused"):
                    retained.is_retained_evidence_path(self.docs, path / "odd\nsource.rb")
                alias = self.repo / "alias\nsource.rb"
                alias.symlink_to(path / "odd\\source.rb")
                try:
                    with self.assertRaisesRegex(retained.RetainedEvidenceRefusal, "retained-evidence-layout-refused"):
                        retained.is_retained_evidence_path(self.docs, alias)
                finally:
                    alias.unlink()

    def test_configured_root_control_bytes_remain_refused(self):
        for name in ("docs\nroot", "docs\\root", "docs\x1froot", "docs\x7froot"):
            with self.subTest(name=name):
                with self.assertRaises(retained.RetainedEvidenceRefusal):
                    retained.is_retained_evidence_path(self.repo / name, self.repo / "ordinary.rb")

    def test_nul_candidate_refuses_with_bounded_finding(self):
        with self.assertRaisesRegex(retained.RetainedEvidenceRefusal, "retained-evidence-layout-refused"):
            retained.is_retained_evidence_path(self.docs, self.repo / "invalid\x00.rb")

    def test_literal_layout_and_direct_book_child_only(self):
        controls = [self.book / "evidence" / "report.md", self.book / "run-RUN-001.yaml",
                    self.book / "council" / "receipt.json", self.book / "reviews" / "review.json",
                    self.book / "evidence" / "retained-repositories" / "source.py",
                    self.runs / "outer" / self.book.name / "evidence" / "implementation-cycles" / "retained-repositories",
                    self.docs / "other" / "promptbooks" / "runs" / self.book.name / "evidence" / "implementation-cycles" / "retained-repositories"]
        for path in controls:
            with self.subTest(path=path):
                self.assertFalse(retained.is_retained_evidence_path(self.docs, path))

    def test_owner_book_absence_does_not_unprune(self):
        book_file = self.docs / "promptbooks" / "active" / (self.book.name + ".yaml")
        book_file.parent.mkdir()
        book_file.write_text("id: OLD-PB-0140\n")
        self.assertTrue(retained.is_retained_evidence_path(self.docs, self.holding))
        book_file.unlink()
        self.assertTrue(retained.is_retained_evidence_path(self.docs, self.holding))

    def test_declared_holding_without_files_is_still_excluded(self):
        missing = self.runs / "PB-0001-other-choice" / "evidence" / "implementation-cycles" / "retained-repositories"
        self.assertTrue(retained.is_retained_evidence_path(self.docs, missing / "future.py"))

    def test_full_legacy_loose_names_and_historical_prefixes_remain_held(self):
        for name in ("PB-0001-new-choice", "PB-0001--old--choice-", "PB-0001--",
                     "OLD-PB-0001--original-", "ZZ12345678-PB-0001-choice",
                     "PB-0001-" + "a" * 100):
            with self.subTest(book=name):
                path = self.runs / name / "evidence" / "implementation-cycles" / "retained-repositories"
                path.mkdir(parents=True)
                self.assertTrue(retained.is_retained_evidence_path(self.docs, path / "source.py"))
                self.assertIn(path, retained.retained_evidence_roots(self.docs))

    def test_unrecognized_book_at_exact_layout_refuses_before_descent(self):
        for name in ("PB-0001-", "PB-PB-0001-choice", "ADR-PB-0001-choice", "RUN-PB-0001-choice",
                     "BRIEF-PB-0001-choice", "PB-١٢٣٤-choice", "PB-0001-Choice", "PB-0001-old_choice",
                     "A-PB-0001-choice", "ABCDEFGHIJK-PB-0001-choice", "junk-PB-0001-choice", "notes"):
            with self.subTest(book=name):
                path = self.runs / name / "evidence" / "implementation-cycles" / "retained-repositories"
                path.mkdir(parents=True)
                with self.assertRaisesRegex(retained.RetainedEvidenceRefusal, "retained-evidence-layout-refused"):
                    retained.is_retained_evidence_path(self.docs, path / "source.py")
                with self.assertRaisesRegex(retained.RetainedEvidenceRefusal, "retained-evidence-layout-refused"):
                    retained.retained_evidence_roots(self.docs)
                path.rmdir()
                self.assertFalse(retained.is_retained_evidence_path(self.docs, self.runs / name / "ordinary.txt"))

    def test_no_retained_child_symlink_is_followed(self):
        child = self.holding / "outside"
        child.symlink_to(self.repo.parent, target_is_directory=True)
        real_readlink = os.readlink
        def bounded(path, *args, **kwargs):
            self.assertFalse(Path(path) == child)
            return real_readlink(path, *args, **kwargs)
        with patch("os.readlink", bounded):
            self.assertTrue(retained.is_retained_evidence_path(self.docs, child / "private.py"))
            alias = self.repo / "held-alias"
            alias.symlink_to(self.holding, target_is_directory=True)
            self.assertTrue(retained.is_retained_evidence_path(self.docs, alias / "outside" / "private.py"))

    def test_roots_are_sorted_and_do_not_descend_into_holding(self):
        second = self.runs / "PB-0001-other-choice" / "evidence" / "implementation-cycles" / "retained-repositories"
        second.mkdir(parents=True)
        (self.holding / ".bionic.yml").write_text("not YAML: [\n")
        (self.holding / "nested").mkdir()
        real_iterdir = Path.iterdir
        def bounded(path):
            self.assertFalse(path == self.holding or self.holding in path.parents)
            return real_iterdir(path)
        with patch.object(Path, "iterdir", bounded), patch.object(Path, "read_text", side_effect=AssertionError("configuration read")):
            self.assertEqual(retained.retained_evidence_roots(self.docs), tuple(sorted((self.holding, second))))

    def test_absent_run_layout_returns_empty_roots(self):
        other = self.repo / "another-tree"
        other.mkdir()
        self.assertEqual(retained.retained_evidence_roots(other), ())
        self.assertEqual(retained.retained_evidence_roots(self.docs), (self.holding,))

    def test_alias_into_holding_is_excluded_and_ordinary_alias_is_not(self):
        target = self.holding / "source.py"
        target.write_text("raise RuntimeError('do not import')\n")
        alias = self.repo / "source-alias.py"
        alias.symlink_to(target)
        self.assertTrue(retained.is_retained_evidence_path(self.docs, alias))
        target.unlink()
        self.assertTrue(retained.is_retained_evidence_path(self.docs, alias))
        ordinary = self.repo / "actual.py"
        ordinary.write_text("x = 1\n")
        alias.unlink()
        alias.symlink_to(ordinary)
        self.assertFalse(retained.is_retained_evidence_path(self.docs, alias))

    def test_directory_alias_and_relative_symlink_target_are_excluded(self):
        alias = self.repo / "source-alias"
        alias.symlink_to(self.holding.relative_to(self.repo), target_is_directory=True)
        self.assertTrue(retained.is_retained_evidence_path(self.docs, alias / "nested" / "source.py"))

    def test_alias_parent_components_cannot_erase_a_holding_before_resolution(self):
        nested = self.holding / "nested" / "deeper"
        nested.mkdir(parents=True)
        jump = self.holding / "jump"
        jump.symlink_to(nested, target_is_directory=True)
        source = self.holding / "source.py"
        source.write_text("held evidence\n")
        alias = self.repo / "ordinary-alias.py"
        alias.symlink_to(self.holding / "jump" / ".." / ".." / "source.py")
        # The actual filesystem origin is held. Normalizing before resolving
        # jump instead selects the parent evidence directory's source.py.
        self.assertEqual(alias.resolve(), source)
        real_readlink = os.readlink
        def bounded(path, *args, **kwargs):
            self.assertNotEqual(Path(path), jump, "production must stop before reading held jump")
            return real_readlink(path, *args, **kwargs)
        with patch("os.readlink", bounded):
            self.assertTrue(retained.is_retained_evidence_path(self.docs, alias))
        # Parent components in an ordinary alias remain legitimate. A
        # conservative holding exclusion must not exclude ordinary source.
        ordinary = self.repo / "actual"
        (ordinary / "nested" / "deeper").mkdir(parents=True)
        ordinary_source = ordinary / "source.py"
        ordinary_source.write_text("x = 1\n")
        ordinary_jump = ordinary / "jump"
        ordinary_jump.symlink_to(ordinary / "nested" / "deeper", target_is_directory=True)
        alias.unlink()
        alias.symlink_to(ordinary / "jump" / ".." / ".." / "source.py")
        self.assertEqual(alias.resolve(), ordinary_source)
        self.assertFalse(retained.is_retained_evidence_path(self.docs, alias))

    def test_symlinked_repository_prefix_is_supported(self):
        alias = self.repo.parent / (self.repo.name + "-alias")
        alias.symlink_to(self.repo, target_is_directory=True)
        self.addCleanup(alias.unlink)
        alias_docs = alias / self.docs.relative_to(self.repo)
        alias_path = alias / self.holding.relative_to(self.repo)
        self.assertTrue(retained.is_retained_evidence_path(alias_docs, alias_path / "code.py"))
        self.assertEqual(retained.retained_evidence_roots(alias_docs), (self.holding,))

    def test_raw_traversal_refuses_before_any_stat(self):
        for path in (self.holding / ".." / "actual.py", Path("../actual.py")):
            with self.subTest(path=path), patch.object(Path, "resolve", side_effect=AssertionError("resolved traversal")):
                with self.assertRaises(retained.RetainedEvidenceRefusal):
                    retained.is_retained_evidence_path(self.docs, path)

    def test_symlinked_layout_refuses_without_following(self):
        outside = self.repo / "outside"
        outside.mkdir()
        for component in ("promptbooks", "runs", "evidence", "implementation-cycles", "retained-repositories"):
            with self.subTest(component=component):
                tree = self.repo / component
                base = tree / "promptbooks" / "runs" / "PB-0002-choice" / "evidence" / "implementation-cycles" / "retained-repositories"
                index = base.parts.index(component, len(tree.parts)) if component != "promptbooks" else len(tree.parts)
                link = Path(*base.parts[:index + 1])
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(retained.RetainedEvidenceRefusal):
                    retained.is_retained_evidence_path(tree, base / "source.py")
                with self.assertRaises(retained.RetainedEvidenceRefusal):
                    retained.retained_evidence_roots(tree)

    def test_non_directory_layout_refuses(self):
        self.holding.rmdir()
        self.holding.write_text("a file is not a holding directory")
        with self.assertRaises(retained.RetainedEvidenceRefusal):
            retained.is_retained_evidence_path(self.docs, self.holding / "code.py")
        with self.assertRaises(retained.RetainedEvidenceRefusal):
            retained.retained_evidence_roots(self.docs)

    def assert_refuses_in_both_modes(self, docs, path):
        from contextlib import closing
        from admission_source_io import SourceIO, SourceIORefusal
        with self.assertRaises(retained.RetainedEvidenceRefusal):
            retained.is_retained_evidence_path(docs, path)
        with closing(SourceIO(self.repo)) as source_io:
            with self.assertRaises((retained.RetainedEvidenceRefusal, SourceIORefusal)):
                retained.is_retained_evidence_path(docs, path, _source_io=source_io)

    def test_layout_symlink_to_a_file_refuses_directly_and_through_an_alias(self):
        manifest = self.docs / "manifest.yml"
        manifest.write_text("x")
        book = self.runs / "PB-0009-x"
        book.mkdir()
        (book / "evidence").symlink_to(manifest)
        alias = self.repo / "alias"
        alias.symlink_to(book, target_is_directory=True)
        for path in (book / "evidence" / "implementation-cycles",
                     alias / "evidence" / "implementation-cycles" / "retained-repositories" / "f"):
            with self.subTest(path=path):
                self.assert_refuses_in_both_modes(self.docs, path)

    def test_promptbooks_symlink_to_a_file_refuses(self):
        docs = self.repo / "other"
        docs.mkdir()
        (docs / "manifest.yml").write_text("x")
        (docs / "promptbooks").symlink_to(docs / "manifest.yml")
        alias = self.repo / "lnk"
        alias.symlink_to(docs / "promptbooks")
        for path in (docs / "promptbooks" / "runs" / "x", alias / "runs" / "x",
                     alias / "runs" / "PB-0009-x" / "evidence" / "implementation-cycles"
                     / "retained-repositories" / "f"):
            with self.subTest(path=path):
                self.assert_refuses_in_both_modes(docs, path)

    def test_alias_loop_refuses_instead_of_admitting(self):
        alias = self.repo / "loop"
        alias.symlink_to(alias)
        with self.assertRaises(retained.RetainedEvidenceRefusal):
            retained.is_retained_evidence_path(self.docs, alias)

    def test_explicit_isolated_tree_can_inspect_its_own_sources(self):
        child = self.holding / "isolated-child"
        child_docs = child / "bionic"
        child_docs.mkdir(parents=True)
        source = child / "source.py"
        source.write_text("x = 1\n")
        self.assertTrue(retained.is_retained_evidence_path(self.docs, source))
        self.assertFalse(retained.is_retained_evidence_path(child_docs, source))

    def test_top_down_pruning_skips_nested_records_and_configs(self):
        held = {".bionic.yml", "manifest.yml", "PB-9999-held.yaml", "ADR-9999-held.md", "OBS-9999-held.md", "source.py"}
        for name in held:
            (self.holding / name).write_text("held evidence")
        ordinary = self.book / "evidence" / "cycle-first.json"
        ordinary.write_text("real parent artifact binding")
        visited = []
        for directory, children, names in os.walk(self.docs):
            children[:] = [name for name in children if not retained.is_retained_evidence_path(self.docs, Path(directory) / name)]
            visited.extend(Path(directory) / name for name in names)
        self.assertIn(ordinary, visited)
        self.assertFalse(any(path.name in held for path in visited))


class SourceDiscoveryResolverTests(unittest.TestCase):
    def setUp(self):
        RetainedEvidenceTests.setUp(self)
        self.app = self.repo / "app"
        self.app.mkdir()
        self.lib = self.repo / "lib"
        self.lib.mkdir()

    def resolve(self, path):
        return retained.resolve_source_path(self.repo, self.docs, path)

    def test_ordinary_parent_path_and_repo_relative_path_resolve(self):
        source = self.lib / "source.rb"
        source.write_text("class Ordinary; end\n")
        self.assertEqual(source, self.resolve(self.app / ".." / "lib" / "source.rb"))
        self.assertEqual(self.lib, self.resolve(Path("app/../lib")))
        self.assertEqual(source, self.resolve(source))
        self.assertIsNone(self.resolve(Path("../outside")))

    def test_missing_or_nondirectory_parent_is_not_lexically_cancelled(self):
        file = self.repo / "not-directory"
        file.write_text("x")
        for prefix in (self.repo / "missing", file):
            with self.subTest(prefix=prefix):
                self.assertIsNone(self.resolve(prefix / ".." / "lib"))
        self.assertIsNone(self.resolve(self.lib / "missing.rb"))

    def test_symlink_is_resolved_before_subsequent_parent_components(self):
        nested = self.lib / "nested" / "deeper"
        nested.mkdir(parents=True)
        jump = self.app / "jump"
        jump.symlink_to(nested, target_is_directory=True)
        path = jump / ".." / ".."
        self.assertEqual(self.lib, self.resolve(path))
        self.assertNotEqual(self.app, self.resolve(path))

    def test_holding_entry_prunes_before_parent_suffix_or_held_link_probe(self):
        child = self.holding / "jump"
        child.symlink_to(self.lib, target_is_directory=True)
        alias = self.app / "holding"
        alias.symlink_to(self.holding, target_is_directory=True)
        original_lstat, original_readlink = Path.lstat, os.readlink
        def lstat(path, *args, **kwargs):
            if self.holding in path.parents:
                raise AssertionError("held suffix probed")
            return original_lstat(path, *args, **kwargs)
        def readlink(path, *args, **kwargs):
            if self.holding == Path(path) or self.holding in Path(path).parents:
                raise AssertionError("held link followed")
            return original_readlink(path, *args, **kwargs)
        with patch.object(Path, "lstat", lstat), patch("os.readlink", readlink):
            for path in (self.holding / ".." / ".." / ".." / ".." / ".." / ".." / "ordinary",
                         child / ".." / "source.rb", alias / "jump" / ".."):
                with self.subTest(path=path):
                    self.assertIsNone(self.resolve(path))

    def test_ordinary_alias_escape_and_loop_prune(self):
        escape = self.app / "outside"
        escape.symlink_to(self.repo.parent, target_is_directory=True)
        loop = self.app / "loop"
        loop.symlink_to(loop)
        self.assertIsNone(self.resolve(escape / "private"))
        self.assertIsNone(self.resolve(loop))
        self.assertEqual(self.lib, self.resolve(self.lib))

    def test_symlink_target_missing_parent_is_not_cancelled(self):
        alias = self.app / "missing-parent"
        alias.symlink_to(self.repo / "missing" / ".." / "lib", target_is_directory=True)
        self.assertIsNone(self.resolve(alias))

    def test_ordinary_escape_prunes_without_probing_outside_suffix(self):
        alias = self.app / "outside"
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external).resolve()
            alias.symlink_to(outside, target_is_directory=True)
            original = Path.lstat
            def lstat(path, *args, **kwargs):
                if Path(path).is_relative_to(outside):
                    raise AssertionError("outside target probed")
                return original(path, *args, **kwargs)
            with patch.object(Path, "lstat", lstat):
                self.assertIsNone(self.resolve(alias / "source.rb"))

    def test_declared_malformed_or_linked_holding_layout_refuses(self):
        malformed = self.runs / "notes" / "evidence" / "implementation-cycles" / "retained-repositories"
        malformed.mkdir(parents=True)
        with self.assertRaises(retained.RetainedEvidenceRefusal):
            self.resolve(malformed / "source.rb")
        alternate = self.repo / "alternate"
        (alternate / "promptbooks").mkdir(parents=True)
        (alternate / "promptbooks" / "runs").symlink_to(self.lib, target_is_directory=True)
        candidate = alternate / "promptbooks" / "runs" / "PB-0001-choice" / "evidence" / "implementation-cycles" / "retained-repositories"
        with self.assertRaises(retained.RetainedEvidenceRefusal):
            retained.resolve_source_path(self.repo, alternate, candidate / "source.rb")

    def test_docs_root_outside_explicit_repo_refuses(self):
        with self.assertRaises(retained.RetainedEvidenceRefusal):
            retained.resolve_source_path(self.lib, self.docs, self.lib)

    def test_symlinked_repo_spelling_and_ordinary_docs_sources_resolve(self):
        alias = self.repo.parent / (self.repo.name + "-source-alias")
        alias.symlink_to(self.repo, target_is_directory=True)
        self.addCleanup(alias.unlink)
        ordinary = self.docs / "ordinary-source.rb"
        ordinary.write_text("class Ordinary; end\n")
        self.assertEqual(ordinary, retained.resolve_source_path(alias,
            alias / self.docs.relative_to(self.repo), alias / ordinary.relative_to(self.repo)))

    def test_alias_into_malformed_layout_preserves_refusal_before_suffix(self):
        malformed = self.runs / "PB-0001-Choice" / "evidence" / "implementation-cycles" / "retained-repositories"
        malformed.mkdir(parents=True)
        alias = self.app / "malformed"
        alias.symlink_to(malformed / "held.rb")
        with self.assertRaises(retained.RetainedEvidenceRefusal):
            self.resolve(alias)

    def test_parent_candidate_is_never_passed_to_resolve_or_strict_predicate(self):
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if ".." in path.parts:
                raise AssertionError("parent candidate normalized before walking")
            return original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve), patch.object(retained, "is_retained_evidence_path",
                side_effect=AssertionError("strict predicate called for parent source")):
            self.assertEqual(self.lib, self.resolve(self.app / ".." / "lib"))


class HoldingSourceContractTests(unittest.TestCase):
    def test_template_declares_non_authoritative_holding_and_isolated_inspection(self):
        text = (ROOT / "crux/templates/AGENTS.md.tmpl").read_text()
        section = text.split("## 3. Directory layout", 1)[1].split("## 4.", 1)[0]
        self.assertIn("evidence/implementation-cycles/retained-repositories/", section)
        for term in ("not a concern", "no index section", "isolated", ".git", "historical", "legacy slugs with leading, repeated or trailing hyphens"):
            self.assertTrue(term in section, f"template §3 must declare {term}")

    def test_each_source_workflow_uses_shared_predicate_before_walks(self):
        for name in ("recover-decisions", "audit-docs", "cleanup-campsite", "archive-promptbook"):
            with self.subTest(skill=name):
                text = (ROOT / "crux/skills" / name / "SKILL.md").read_text()
                # The shared predicate reaches a skill through the shipped command, which
                # resolves the configured root itself; a Python function name is not a step
                # an agent can execute.
                self.assertTrue("authority-view.py\" retained-roots" in text, "missing shared predicate")
                self.assertTrue("--repo-root" in text, "missing canonical root")
                self.assertTrue("before" in text.lower(), "missing pre-discovery ordering")

    def test_delegated_audit_walk_preserves_the_exclusion(self):
        text = (ROOT / "crux/skills/audit-docs/SKILL.md").read_text()
        prompt = text.split("> Walk the entire", 1)[1].split("### 4.", 1)[0]
        self.assertTrue("authority-view.py\" retained-roots" in prompt,
                        "the independently handed walk prompt must retain pruning")
        self.assertTrue("before" in prompt.lower() and "--repo-root" in prompt)
        self.assertFalse("find docs -name '*.md' | wc -l" in text,
                         "raw census command contradicts holding pruning")


if __name__ == "__main__":
    unittest.main()
