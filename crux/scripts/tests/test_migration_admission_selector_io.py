"""Explicit migration admission selectors resolve through anchored transport."""
from pathlib import Path
from unittest import mock
import unittest

import admission_source_io as source_io
import implementation_migration as migration
import test_implementation_migration_alternate_tree as fixtures


class SelectorIO(unittest.TestCase):
    setUp = fixtures.AlternateTree.setUp
    snapshot = fixtures.AlternateTree.snapshot
    read = fixtures.AlternateTree.read
    def test_outside_selector_refuses_without_raw_target_resolution(self):
        tree = self.root / "alternate"
        (tree / "manifest.yml").unlink(); tree.rmdir()
        outside = self.root.parent / "outside"; outside.mkdir()
        tree.symlink_to(outside, target_is_directory=True)
        before = self.snapshot(); original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path in (tree, outside):
                raise AssertionError("candidate resolved outside anchored transport")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "resolve", resolve):
            with self.assertRaises(migration.Refused):
                migration.admission_authority_view(self.root, docs_dir="alternate")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_equivalent_and_distinct_selectors_own_revalidate_and_close_transport(self):
        original = source_io.SourceIO.__init__
        for selector in ("bionic", "alternate"):
            instances = []
            def init(io, *args, **kwargs):
                original(io, *args, **kwargs); instances.append(io)
            with mock.patch.object(source_io.SourceIO, "__init__", init):
                self.assertEqual(self.read(selector), migration._original_view())
            self.assertTrue(instances)
            bootstrap = instances[0]
            self.assertTrue(any(key[0] == "resolve" and key[1] == bootstrap.root / selector
                                for key in bootstrap._observed))
            self.assertIsNone(bootstrap._fd)

    def test_raw_source_reader_never_resolves_candidate_parent_by_path(self):
        source = self.root / "alternate/source.txt"; source.write_bytes(b"fact\r\n")
        canonical = source.resolve(); parent = canonical.parent
        original = Path.resolve; before = self.snapshot()
        def resolve(path, *args, **kwargs):
            if path == parent:
                raise AssertionError("raw candidate parent resolved before anchored read")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "resolve", resolve):
            text, rel = migration._read(self.root.resolve(), canonical)
        self.assertEqual(text.encode(), b"fact\r\n")
        self.assertEqual(rel, "alternate/source.txt")
        self.assertEqual(self.snapshot(), before)

    def test_finite_no_git_layout_never_resolves_tree_by_path(self):
        root = self.root.resolve(); tree = root / "bionic"
        original = Path.resolve; before = self.snapshot()
        def resolve(path, *args, **kwargs):
            if path == tree:
                raise AssertionError("layout candidate resolved outside anchored transport")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "resolve", resolve):
            self.assertEqual(migration._no_git_layout(root), [tree])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__": unittest.main()
