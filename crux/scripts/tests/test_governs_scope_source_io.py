"""The private citation scope port preserves canonical scope through one I/O."""
import contextlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from admission_source_io import SourceIO, SourceIORefusal
from observation_admission import _AdmissionIO
import summaries_projection as sp


spec = importlib.util.spec_from_file_location("guarded_governs_scope",
    Path(__file__).resolve().parents[1] / "lint-governs-references.py")
linter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linter)


class RecordingIO(SourceIO):
    def __init__(self, root):
        super().__init__(root)
        self.calls = []

    def glob(self, path, pattern):
        self.calls.append(("glob", path, pattern))
        return super().glob(path, pattern)

    def metadata(self, path):
        self.calls.append(("metadata", path, None))
        return super().metadata(path)

    def read_text(self, path, **kwargs):
        self.calls.append(("read_text", path, None))
        return super().read_text(path, **kwargs)


class GovernsScopeSourceIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.tree = self.root / "knowledge"
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: knowledge\n')
        for rel in ("crux/skills/a/SKILL.md", "crux/scripts/source.py",
                    "crux/scripts/tests/excluded.py", "crux/catalog/rules.json",
                    "knowledge/plans/current.md", "knowledge/research/current.md",
                    "knowledge/research/raw/excluded.md", "knowledge/adrs/excluded.md",
                    "knowledge/promptbooks/runs/excluded.yaml", "knowledge/manifest.yml",
                    "knowledge/node_modules/excluded.md", "knowledge/plans/ignored.txt"):
            path = self.root / rel; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("rule:synthetic-token\n")

    def test_guarded_scope_matches_default_without_raw_path_or_walk_fallback(self):
        expected = linter.resolve_scope(self.root, [])
        with contextlib.closing(RecordingIO(self.root)) as source_io:
            with contextlib.ExitStack() as stack:
                for name in ("resolve", "exists", "is_file", "is_dir", "is_symlink",
                             "lstat", "glob", "iterdir", "read_text"):
                    stack.enter_context(patch.object(Path, name, side_effect=AssertionError("raw " + name)))
                stack.enter_context(patch.object(os, "walk", side_effect=AssertionError("raw walk")))
                actual = linter.resolve_scope(self.root, [], _source_io=source_io)
            self.assertEqual(actual, expected)
            self.assertIn(("read_text", self.root / ".bionic.yml", None), source_io.calls)
            self.assertTrue(all(pattern == "*" for method, _, pattern in source_io.calls if method == "glob"))
            touched = {path for method, path, _ in source_io.calls if method in ("glob", "metadata")}
            for rel in ("knowledge/adrs", "knowledge/research/raw", "knowledge/promptbooks/runs",
                        "knowledge/node_modules", "crux/scripts/tests"):
                self.assertFalse(any(path == self.root / rel or (self.root / rel) in path.parents
                                     for path in touched), rel)
            source_io.revalidate()

    def test_guarded_config_and_directory_membership_remain_in_same_snapshot(self):
        for change in ("config", "roster"):
            with self.subTest(change=change), contextlib.closing(SourceIO(self.root)) as source_io:
                linter.resolve_scope(self.root, [], _source_io=source_io)
                if change == "config":
                    changed = self.root / ".bionic.yml"; original = changed.read_bytes()
                    changed.write_bytes(original + b"# changed layout input\n")
                else:
                    changed = self.root / "crux/skills/a/new.md"; original = None
                    changed.write_text("rule:new-token\n")
                with self.assertRaises(SourceIORefusal): source_io.revalidate()
                if original is None: changed.unlink()
                else: changed.write_bytes(original)

    def test_guarded_explicit_scope_preserves_symlink_nonregular_and_bound_refusals(self):
        source = self.root / "crux/skills/a/SKILL.md"
        link = source.with_name("link.md"); link.symlink_to(source.name)
        fifo = source.with_name("fifo.md"); os.mkfifo(fifo)
        missing = source.with_name("missing.md")
        oversized = source.with_name("oversized.md"); oversized.write_bytes(b"x" * 16)
        selected = [str(path) for path in (source, link, fifo, missing, oversized)]
        with patch.object(linter, "MAX_FILE_BYTES", 8):
            expected = linter.resolve_scope(self.root, selected)
            with contextlib.closing(SourceIO(self.root)) as source_io:
                self.assertEqual(linter.resolve_scope(self.root, selected, _source_io=source_io), expected)
                source_io.revalidate()
        with patch.object(linter, "MAX_SCANNED_FILES", 1):
            expected = linter.resolve_scope(self.root, [])
            with contextlib.closing(SourceIO(self.root)) as source_io:
                self.assertEqual(linter.resolve_scope(self.root, [], _source_io=source_io), expected)

    def test_canonical_admission_callback_prunes_holding_and_alias_before_descent(self):
        runs = self.tree / "promptbooks/runs"
        holding = runs / "PB-0001-fixture/evidence/implementation-cycles/retained-repositories"
        held = holding / "repo/source.md"; held.parent.mkdir(parents=True)
        held.write_text("rule:held-token\n")
        ordinary = runs / "PB-0001-fixture/ordinary.md"; ordinary.write_text("rule:ordinary-token\n")
        alias = runs / "alias"; alias.symlink_to(holding, target_is_directory=True)
        with contextlib.closing(_AdmissionIO(self.root)) as source_io:
            source_io.bind_tree(source_io.resolve(sp.resolve_tree(self.root, _source_io=source_io)))
            original_glob = source_io.glob
            enumerated = []
            def glob(path, pattern):
                self.assertFalse(path == holding or holding in path.parents)
                enumerated.append(path)
                return original_glob(path, pattern)
            with patch.object(source_io, "glob", side_effect=glob):
                files, _, refused = linter.resolve_scope(self.root, [str(runs)], _source_io=source_io)
            self.assertEqual(refused, [])
            self.assertIn(ordinary, files); self.assertNotIn(held, files)
            self.assertNotIn(alias, enumerated)
            files, _, refused = linter.resolve_scope(self.root, [str(alias / "repo/source.md")], _source_io=source_io)
            self.assertEqual((files, refused), ([], []))
            source_io.revalidate()
