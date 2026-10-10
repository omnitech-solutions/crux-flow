"""Canonical summaries required reads share one operation source transport."""
import contextlib
import hashlib
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml
import summaries_projection as sp
from admission_source_io import SourceIO, SourceIORefusal
import test_summaries_source_io as fixture
import test_survey_sheet as surveys

driver = importlib.import_module('summarize-adrs')


class RequiredReadTests(unittest.TestCase):
    def setUp(self):
        fixture.SummariesSourceIOTests.setUp(self)
        self.runs = self.tree / 'promptbooks/runs'
        self.snapshot = self.runs / 'PB-0001-test/run-RUN-001.yaml'
        self.snapshot.parent.mkdir(parents=True)
        self.snapshot.write_text('book_id: PB-0001\nrun_id: RUN-001\nprompts:\n- artifacts: [ADR-0001, DEMO-ADR-0002]\n')
        self.manifest = {'concerns_enabled': ['adrs', 'observations']}

    def guard(self):
        guard = SourceIO(self.root)
        self.addCleanup(guard.close)
        return guard

    def exercise(self, guard=None):
        kw = {} if guard is None else {'_source_io': guard}
        return dict(observations=sp.observations_sha256(self.obs, **kw),
                    receipts=sp.survey_receipts_sha256(self.obs, **kw),
                    receipt_paths=sp.survey_receipt_paths(self.obs, **kw),
                    observation_problem=sp.observations_source_problem(self.root, self.manifest, **kw),
                    observation_source=sp.observations_source(self.root, self.manifest, **kw),
                    adr_hash=sp.adr_frontmatter_sha256(self.adrs, **kw),
                    review_hash=sp.backfill_reviews_sha256(self.adrs, **kw),
                    ids=sp.collect_adr_ids(self.adrs, **kw),
                    bindings=sp.read_run_bindings(self.runs, repo_root=self.root, **kw),
                    implementation_map=sp.build_implementation_map(self.adrs, self.runs, repo_root=self.root, **kw),
                    destinations=driver._summary_destinations(self.root, **kw))

    def test_guarded_defaults_equivalent_without_unsafe_path_reads_or_probes(self):
        expected = self.exercise()
        guard = self.guard()
        with contextlib.ExitStack() as stack:
            for name in ('read_text', 'read_bytes', 'glob', 'iterdir', 'resolve',
                         'is_file', 'is_dir', 'exists', 'is_symlink'):
                stack.enter_context(patch.object(Path, name, side_effect=AssertionError('unsafe ' + name)))
            actual = self.exercise(guard)
        self.assertEqual(expected, actual)
        guard.revalidate()

    def test_raw_review_and_receipt_hashes_keep_original_bytes(self):
        review = sp.reviews_path(self.adrs)
        review.write_bytes(b'config_version: "1"\r\nbatches: []\r\nreceipts: []\r\n')
        receipt = self.obs / '_surveys/SVY-0001/receipt.yml'
        receipt.parent.mkdir(parents=True)
        receipt.write_bytes(b'raw: fixture\r\n')
        guard = self.guard()
        self.assertEqual(hashlib.sha256(review.read_bytes()).hexdigest(),
                         sp.backfill_reviews_sha256(self.adrs, _source_io=guard))
        digest = hashlib.sha256(b'SVY-0001\0' + receipt.read_bytes() + b'\0').hexdigest()
        self.assertEqual(digest, sp.survey_receipts_sha256(self.obs, _source_io=guard))
        self.assertEqual(sp.survey_receipts_sha256(self.obs), digest)

    def test_canonical_survey_state_is_forwarded_and_unfinished_batch_refuses(self):
        receipt = self.obs / '_surveys/SVY-0001/receipt.yml'
        receipt.parent.mkdir(parents=True)
        receipt.write_text(yaml.safe_dump(surveys._receipt(), sort_keys=False))
        expected = sp.survey_receipts_problem(self.root, self.manifest)
        self.assertIn('S1', expected)
        guard = self.guard()
        with patch.object(Path, 'read_text', side_effect=AssertionError('unsafe read_text')), \
             patch.object(Path, 'is_file', side_effect=AssertionError('unsafe is_file')):
            self.assertEqual(expected, sp.survey_receipts_problem(self.root, self.manifest, _source_io=guard))

    def test_contained_record_and_run_directory_aliases_preserve_results(self):
        expected = self.exercise()
        source = self.adrs / 'ADR-0001-rule.md'
        target = self.adrs / 'stored-source.txt'
        source.rename(target)
        source.symlink_to(target.name)
        directory = self.snapshot.parent
        stored = directory.with_name('stored-runs')
        directory.rename(stored)
        directory.symlink_to(stored.name, target_is_directory=True)
        # Both spellings are ordinary directory entries and retain attribution.
        self.assertEqual(self.exercise(), self.exercise(self.guard()))
        self.assertEqual(expected['bindings'], self.exercise()['bindings'])

    def test_same_guard_revalidates_source_bytes_membership_and_absence(self):
        cases = ((self.snapshot, b'changed run'),
                 (self.adrs / 'ADR-0003-added.md', fixture.record('ADR-0003').encode()),
                 (self.obs / '_surveys', None))
        for path, content in cases:
            with self.subTest(path=path):
                guard = self.guard()
                self.exercise(guard)
                before = path.read_bytes() if path.is_file() else None
                if content is None:
                    path.mkdir()
                else:
                    path.write_bytes(content)
                try:
                    with self.assertRaises(SourceIORefusal):
                        guard.revalidate()
                finally:
                    if content is None:
                        path.rmdir()
                    elif before is None:
                        path.unlink()
                    else:
                        path.write_bytes(before)

    def test_destination_final_alias_and_temporary_alias_refuse(self):
        target = self.adrs / 'summaries/stored-output.txt'
        target.write_text('preserved bytes')
        for name in ('resolver.json', 'resolver.json.tmp'):
            path = target.parent / name
            path.symlink_to(target.name)
            try:
                with self.assertRaisesRegex(OSError, 'destination is symlinked'):
                    driver._summary_destinations(self.root, _source_io=self.guard())
                self.assertEqual('preserved bytes', target.read_text())
            finally:
                path.unlink()


if __name__ == '__main__':
    unittest.main()
