"""Prepared writer fixtures retain history without sharing mutable test state."""
from pathlib import Path
import tempfile
import unittest

import _council_gate_support as support
import test_implementation_cycles as fixtures


class FixtureCopyTests(unittest.TestCase):
    def test_copies_preserve_history_and_isolate_files_paths_and_python_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            original = fixtures.WriterFixture(root / "original", migration=True)
            first = original.copy_to(root / "first")
            second = original.copy_to(root / "second")
            commit = support.git(original.root, "rev-parse", "HEAD")
            content = original.path.read_bytes()
            for fixture in (first, second):
                self.assertEqual(support.git(fixture.root, "rev-parse", "HEAD"), commit)
                self.assertEqual(fixture.path.read_bytes(), content)
                self.assertEqual(fixture.env.root, fixture.root)
                for value in vars(fixture).values():
                    if isinstance(value, Path):
                        self.assertTrue(value.is_relative_to(fixture.root), value)
            first.path.write_text("synthetic mutation\n")
            first.book["title"] = "Changed only in the first copy"
            first.env.hash = "changed"
            support.commit_all(first.root, "synthetic private copy mutation")
            self.assertNotEqual(support.git(first.root, "rev-parse", "HEAD"), commit)
            for fixture in (original, second):
                self.assertEqual(fixture.path.read_bytes(), content)
                self.assertEqual(support.git(fixture.root, "rev-parse", "HEAD"), commit)
                self.assertNotEqual(fixture.book["title"], first.book["title"])
                self.assertNotEqual(fixture.env.hash, first.env.hash)
