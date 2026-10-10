"""The scoped git read memo answers repeats from memory and never a changed state."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _council_gate_support as sup
import council_records as cr
import git_read_cache
import implementation_approval as ap


class _Counted:
    """Counts the git children the real `subprocess.run` starts."""

    def __init__(self):
        self.calls = []
        self._real = subprocess.run

    def __call__(self, args, *pargs, **kwargs):
        if isinstance(args, list) and args[:1] == ["git"]:
            self.calls.append(tuple(args[3:]))
        return self._real(args, *pargs, **kwargs)


class GitReadCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = sup.init_repo(Path(temp.name) / "repo").resolve()
        (self.root / "a.txt").write_text("one\n")
        sup.commit_all(self.root, "first")
        self.counted = _Counted()
        patcher = mock.patch.object(subprocess, "run", self.counted)
        patcher.start(); self.addCleanup(patcher.stop)

    def reads(self, *args):
        return [c for c in self.counted.calls if c == args]

    def test_a_repeated_admitted_read_starts_one_child_inside_the_scope(self):
        with git_read_cache.scope():
            first = cr.git(self.root, "cat-file", "blob", "HEAD:a.txt")
            second = cr.git(self.root, "cat-file", "blob", "HEAD:a.txt")
        self.assertEqual((first.returncode, first.stdout), (0, b"one\n"))
        self.assertEqual((second.returncode, second.stdout, second.stderr),
                         (first.returncode, first.stdout, first.stderr))
        self.assertEqual(len(self.reads("cat-file", "blob", "HEAD:a.txt")), 1)

    def test_outside_a_scope_every_read_starts_a_child(self):
        cr.git(self.root, "cat-file", "blob", "HEAD:a.txt")
        cr.git(self.root, "cat-file", "blob", "HEAD:a.txt")
        self.assertEqual(len(self.reads("cat-file", "blob", "HEAD:a.txt")), 2)
        self.assertIs(cr.subprocess, subprocess)

    def test_a_commit_inside_the_scope_is_read_fresh(self):
        with git_read_cache.scope():
            before = cr.git(self.root, "cat-file", "blob", "HEAD:a.txt").stdout
            head = cr.git(self.root, "rev-parse", "HEAD").stdout
            (self.root / "a.txt").write_text("two\n")
            sup.commit_all(self.root, "second")
            after = cr.git(self.root, "cat-file", "blob", "HEAD:a.txt").stdout
            moved = cr.git(self.root, "rev-parse", "HEAD").stdout
        self.assertEqual((before, after), (b"one\n", b"two\n"))
        self.assertNotEqual(head, moved)

    def test_a_staged_change_inside_the_scope_is_read_fresh(self):
        with git_read_cache.scope():
            before = cr.git(self.root, "ls-files", "-s", "-z", "--", "a.txt").stdout
            staged_before = cr.git(self.root, "cat-file", "blob", ":0:a.txt").stdout
            (self.root / "a.txt").write_text("staged\n")
            sup.git(self.root, "add", "a.txt")
            after = cr.git(self.root, "ls-files", "-s", "-z", "--", "a.txt").stdout
            staged_after = cr.git(self.root, "cat-file", "blob", ":0:a.txt").stdout
        self.assertNotEqual(before, after)
        self.assertEqual((staged_before, staged_after), (b"one\n", b"staged\n"))

    def test_a_branch_move_that_keeps_the_index_is_read_fresh(self):
        (self.root / "a.txt").write_text("two\n")
        sup.commit_all(self.root, "second")
        with git_read_cache.scope():
            before = cr.git(self.root, "rev-parse", "HEAD").stdout
            sup.git(self.root, "reset", "--soft", "HEAD~1")
            after = cr.git(self.root, "rev-parse", "HEAD").stdout
        self.assertNotEqual(before, after)

    def test_the_scope_restores_a_patched_module(self):
        fake = mock.Mock()
        with mock.patch.object(cr, "subprocess", fake):
            with git_read_cache.scope():
                pass
            self.assertIs(cr.subprocess, fake)

    def test_a_working_tree_read_is_never_cached(self):
        with git_read_cache.scope():
            for _ in range(2):
                cr.git(self.root, "check-ignore", "-q", "--", "a.txt", literal_pathspecs=False)
        self.assertEqual(len(self.reads("check-ignore", "-q", "--", "a.txt")), 2)

    def test_a_failed_read_is_asked_again(self):
        with git_read_cache.scope():
            for _ in range(2):
                self.assertNotEqual(cr.git(self.root, "cat-file", "blob", "HEAD:absent").returncode, 0)
        self.assertEqual(len(self.reads("cat-file", "blob", "HEAD:absent")), 2)

    def test_a_revision_named_by_ref_is_never_cached(self):
        sup.git(self.root, "branch", "side")
        with git_read_cache.scope():
            for _ in range(2):
                cr.git(self.root, "ls-tree", "-z", "side", "--", "a.txt")
        self.assertEqual(len(self.reads("ls-tree", "-z", "side", "--", "a.txt")), 2)

    def test_the_scope_restores_the_real_module_and_nests(self):
        with git_read_cache.scope():
            self.assertIsNot(cr.subprocess, subprocess)
            with git_read_cache.scope():
                cr.git(self.root, "rev-parse", "HEAD")
            cr.git(self.root, "rev-parse", "HEAD")
            self.assertIsNot(cr.subprocess, subprocess)
        self.assertIs(cr.subprocess, subprocess)
        self.assertEqual(len(self.reads("rev-parse", "HEAD")), 1)

    def test_the_gate_contract_binds_the_same_bytes_inside_the_scope(self):
        outside = ap.production_contract_bytes()
        with git_read_cache.scope():
            inside = ap.production_contract_bytes()
        self.assertEqual(inside, outside)


if __name__ == "__main__":
    unittest.main()
