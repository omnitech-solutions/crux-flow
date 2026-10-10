"""Absent automatic docs layout is ordinary, never evidence of lost migration history."""
from pathlib import Path
import tempfile
import shutil
import unittest
from unittest import mock
import implementation_migration as migration


class AbsentDefault(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'source.py').write_text('x = 1\n')

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def view(self):
        before = self.inventory()
        try: return migration.authority_view(self.root)
        finally: self.assertEqual(self.inventory(), before)

    def test_code_only_default_is_original_without_creating_docs_or_files(self):
        self.assertEqual(self.view()['state'], 'original')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['source.py'])
        with self.assertRaises(migration.Refused): migration.authority_view(self.root, revision='HEAD')

    def test_explicit_missing_layout_file_and_symlink_stay_refused(self):
        (self.root / '.bionic.yml').write_text('config_version: "1"\ndocs_dir: bionic\n')
        with self.assertRaises(migration.Refused): self.view()
        (self.root / '.bionic.yml').unlink()
        tree = self.root / 'bionic'; tree.write_text('not a directory')
        with self.assertRaises(migration.Refused): self.view()
        tree.unlink(); tree.symlink_to(self.root / 'missing', target_is_directory=True)
        with self.assertRaises(migration.Refused): self.view()

    def test_surviving_conventional_publication_declaration_and_projection_refuse(self):
        cases = [('adrs/migrations/implementation-pilot-001.application.json', '{}'),
                 ('promptbooks/active/PB-0001-plan.yaml', 'migration_batch: missing.yaml\n'),
                 ('adrs/summaries/_meta.json', '{"input_domain":["implementation-migration"]}'),
                 ('adrs/summaries/resolver.json', '{"historical_handles":{"old":{}}}'),
                 ('manifest.yml', 'migration_inputs: missing.yaml\n')]
        for tree in ('bionic', 'docs'):
            for rel, text in cases:
                with self.subTest(tree=tree, path=rel):
                    path = self.root / tree / rel; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                    with self.assertRaises(migration.Refused): self.view()
                    shutil.rmtree(self.root / tree)

    def test_absence_is_rechecked_and_midoperation_appearance_refuses(self):
        self.assertEqual(self.view()['state'], 'original')
        original = migration._no_git_layout
        def actor(*args):
            result = original(*args)
            path = self.root / 'bionic/adrs/migrations/implementation-pilot-001.application.json'
            path.parent.mkdir(parents=True); path.write_text('{}')
            return result
        with mock.patch.object(migration, '_no_git_layout', side_effect=actor):
            with self.assertRaises(migration.Refused): migration.authority_view(self.root)
        with self.assertRaises(migration.Refused): self.view()


if __name__ == '__main__': unittest.main()
