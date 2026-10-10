"""Ordinary dangling leaves reach canonical read diagnostics after holding pruning."""
from pathlib import Path
import tempfile
import unittest

from crux.arch import core
from crux.arch.packs import swift


class DanglingSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / '.bionic.yml').write_text('config_version: "1"\ndocs_dir: bionic\n')
        (self.root / 'bionic').mkdir()
        self.sources = self.root / 'Sources'
        self.sources.mkdir()
        (self.sources / 'Widget.swift').write_text('public struct Widget { public var id: Int }\n')

    def scan(self):
        reads, project_reads, sources, residuals = swift._scan(self.root, 'data-model', False)
        return sources, residuals

    def test_contained_dangling_leaf_is_listed_then_reports_canonical_read_failure(self):
        link = self.sources / 'Link.swift'
        link.symlink_to('Missing.swift')
        walked = {Path(directory) / name for directory, _, files in
                  core._retained_source_walk(self.root, self.root) for name in files}
        self.assertIn(link, walked)
        self.assertIn(link, set(swift._walk_swift_files(self.root)))
        sources, residuals = self.scan()
        self.assertNotIn('Sources/Link.swift', sources)
        self.assertIn(('parse-error', 'Sources/Link.swift', None,
                       'location none; effect step read failed'), residuals)
        self.assertIn('Sources/Widget.swift', sources)

    def test_dangling_holding_alias_and_retained_sources_stay_pruned(self):
        holding = self.root / 'bionic/promptbooks/runs/PB-0140-example/evidence/implementation-cycles/retained-repositories'
        holding.mkdir(parents=True)
        (holding / 'Held.swift').write_text('public struct Held {}\n')
        link = self.sources / 'HeldAlias.swift'
        link.symlink_to(holding / 'absent/Missing.swift')
        sources, residuals = self.scan()
        self.assertEqual({'Sources/Widget.swift'}, set(sources))
        self.assertEqual([], residuals)
        self.assertNotIn(link, set(swift._walk_swift_files(self.root)))

    def test_loop_escape_and_directory_alias_keep_existing_read_and_walk_behavior(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'Outside.swift'
            target.write_text('public struct Outside {}\n')
            (self.sources / 'Escape.swift').symlink_to(target)
            (self.sources / 'Loop.swift').symlink_to('Loop.swift')
            (self.root / 'DirectoryAlias').symlink_to(self.sources, target_is_directory=True)
            sources, residuals = self.scan()
            self.assertEqual({'Sources/Widget.swift'}, set(sources))
            failed = {path for kind, path, ranges, detail in residuals if kind == 'parse-error'}
            self.assertEqual({'Sources/Escape.swift'}, failed)
            self.assertNotIn(self.sources / 'Loop.swift', set(swift._walk_swift_files(self.root)))
            self.assertFalse(any(path.startswith('DirectoryAlias/') for path in sources))
            self.assertEqual('public struct Outside {}\n', target.read_text())


if __name__ == '__main__':
    unittest.main()
