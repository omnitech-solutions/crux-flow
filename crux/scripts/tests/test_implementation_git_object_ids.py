"""Actual disposable Git starts and closed implementation result ID domains."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml
from _yaml_min import load_yaml

import _council_gate_support as sup
import council_history_v3 as history
import implementation_approval as approval
import implementation_decisions as decisions
from test_implementation_cycles import book_two


ROOT = sup.SCRIPTS.parent.parent


def result_record():
    return yaml.safe_load((ROOT / 'crux/templates/implementation-result-template.yaml').read_text())


def run_errors(doc):
    errors = []
    sup.vp.validate(doc, sup.vp.load_schema(sup.vp.RUN_SCHEMA), '#', '#', errors, 'run')
    return errors


class GitObjectIdTests(unittest.TestCase):
    def test_complete_result_records_accept_exact_sha1_and_sha256_domains(self):
        for source_size in (40, 64):
            for preimage_size in (40, 64):
                with self.subTest(source_size=source_size, preimage_size=preimage_size):
                    doc = result_record()
                    doc.update(source_revision='a' * source_size, preimage_revision='b' * preimage_size)
                    self.assertEqual([], decisions.schema_errors(doc))

    def test_both_result_fields_refuse_other_lengths_spelling_and_trailing_newline(self):
        invalid = ['a' * length for length in (39, 41, 63, 65)]
        invalid += ['g' * 40, 'g' * 64, 'A' * 40, 'A' * 64, 'a' * 40 + '\n', 'a' * 64 + '\n']
        for field in ('source_revision', 'preimage_revision'):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    doc = result_record()
                    doc[field] = value
                    self.assertTrue(decisions.schema_errors(doc))

    def test_run_base_domain_preserves_null_and_sha1_but_rejects_partial_sha256(self):
        template = load_yaml((sup.RECORDS.parent / 'run-valid.yaml').read_text())
        for value in (None, 'a' * 40, 'a' * 64):
            with self.subTest(valid=value):
                self.assertEqual([], run_errors({**template, 'base_commit': value}))
        for value in ('a' * 41, 'a' * 63, 'a' * 65, 'g' * 64, 'A' * 40,
                      'A' * 64, 'a' * 40 + '\n', 'a' * 64 + '\n'):
            with self.subTest(invalid=value):
                self.assertTrue(run_errors({**template, 'base_commit': value}))

    def test_actual_sha1_and_sha256_starts_bind_real_committed_start_identity(self):
        for object_format, size in (('sha1', 40), ('sha256', 64)):
            with self.subTest(object_format=object_format), tempfile.TemporaryDirectory() as temporary:
                repo = Path(temporary) / 'repo'
                repo.mkdir()
                sup.git(repo, 'init', '-q', '-b', 'main', '--object-format=' + object_format)
                self.assertEqual(object_format, sup.git(repo, 'rev-parse', '--show-object-format').strip())
                book = book_two()
                book.update(current_run=None, current_prompt=None)
                book_path = repo / 'docs/promptbooks/active/PB-0999-fixture.yaml'
                book_path.parent.mkdir(parents=True)
                book_path.write_text(yaml.safe_dump(book, sort_keys=False))
                original_book = book_path.read_bytes()
                sup.commit_all(repo, 'synthetic committed book')
                base = sup.git(repo, 'rev-parse', 'HEAD').strip()
                run_path = repo / 'docs/promptbooks/runs/PB-0999-fixture/run-RUN-001.yaml'
                started = subprocess.run([sys.executable, str(sup.SCRIPTS / 'start-run.py'),
                                          str(book_path), '--run-id', 'RUN-001', '--output', str(run_path)],
                                         cwd=ROOT, env=sup.scrubbed_env(), capture_output=True)
                self.assertEqual(0, started.returncode, started.stdout + started.stderr)
                self.assertFalse(json.loads(started.stdout)['book_pointer_updated'])
                run = yaml.safe_load(run_path.read_bytes())
                self.assertEqual(base, run['base_commit'])
                self.assertEqual(size, len(base))
                self.assertEqual([], run_errors(run))
                self.assertEqual(original_book, book_path.read_bytes())
                sup.commit_all(repo, 'synthetic committed run start')
                committed = sup.git(repo, 'rev-parse', 'HEAD').strip()
                identity = history.start_identity(repo, run, run_path)
                self.assertEqual(base, identity['base_commit'])
                self.assertEqual(committed, identity['commit'])
                self.assertEqual(size, len(identity['commit']))
                self.assertEqual(hashlib.sha256(run_path.read_bytes()).hexdigest(), identity['run']['sha256'])
                result = result_record()
                result.update(source_revision=committed, preimage_revision=base)
                self.assertEqual([], decisions.schema_errors(result))

    def test_issued_policy_images_remain_exact_and_generated_from_real_sources(self):
        expected = {'2': 'c44a85a6160a9fb5ddc6ac938ce7b15d462c9d40b2808d2db92699ad15b8f4d2',
                    '3': '62a4760de1e477fb13c78f9162c49c4da620b5ace304ca72ce6fc45496d16d23'}
        for version, digest in expected.items():
            with self.subTest(version=version):
                self.assertEqual(digest, hashlib.sha256(approval.production_contract_bytes(version)).hexdigest())


if __name__ == '__main__':
    unittest.main()
