"""Issued policy bytes are an immutable oracle, independent of current execution."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
import subprocess
import tempfile
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import implementation_approval as approval

ORACLE = Path(__file__).parent / 'fixtures' / 'issued-policy-two'


class IssuedPolicyTwoTests(unittest.TestCase):
    def test_worker_cannot_reload_owner_credentials_or_accept_unbounded_result(self):
        binding = {'revision': {'path': 'fixture', 'sha256': 'a' * 64}}
        seen = []
        def child(command, **kwargs):
            home = Path(kwargs['env']['CRUX_HOME'])
            self.assertTrue(home.is_dir())
            self.assertEqual(list(home.iterdir()), [])
            self.assertEqual(command[1], '-I')
            self.assertEqual(command[2], '-B')
            self.assertEqual(Path(command[3]).resolve(), SCRIPTS / 'council_historical_worker.py')
            seen.append(home)
            return SimpleNamespace(returncode=0, stdout=b'x' * 2_000_001, stderr=b'')
        with mock.patch.object(approval.subprocess, 'run', side_effect=child):
            with self.assertRaisesRegex(approval.Refused, 'historical-worker-output-refused'):
                approval._historical_worker(Path('/fixture'), Path('/fixture/run.yaml'), binding, migration=False)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists())

    def test_original_committed_closes_replay_in_filtered_worker(self):
        tokens = json.loads((ORACLE / 'replay-tokens.json').read_text())
        for token in tokens:
            if token.get('replay') != 'approved':
                continue
            with self.subTest(scenario=token['scenario']), tempfile.TemporaryDirectory() as folder:
                repo = Path(folder) / 'repo'
                clone = subprocess.run(['git', 'clone', str(ORACLE / (token['scenario'] + '.bundle')),
                                        str(repo)], capture_output=True, env=approval._historical_worker_env())
                self.assertEqual(clone.returncode, 0, clone.stderr)
                run = repo / 'docs/promptbooks/runs/PB-0999-fixture/run-RUN-001.yaml'
                with mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': 'fixture-credential'}):
                    proof = approval.validate_historical_implementation_binding(repo, run,
                        slot=token['slot'], revision_path=token['revision_path'],
                        revision_sha256=token['revision_sha256'])
                self.assertEqual(proof.binding, token['binding'])
                self.assertTrue(proof.proof_commit)
                self.assertEqual(approval.cr.git(repo, 'status', '--porcelain').stdout, b'')

    def test_worker_environment_excludes_credentials_and_git_runtime(self):
        with mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': 'fixture-credential',
                'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.fsmonitor',
                'GIT_CONFIG_VALUE_0': 'fixture-command', 'GIT_TEMPLATE_DIR': '/fixture',
                'PYTHONPATH': '/fixture'}, clear=False):
            with mock.patch.object(approval.cr, '_secret_names', return_value={'OPENROUTER_API_KEY'}):
                env = approval._historical_worker_env()
        self.assertNotIn('OPENROUTER_API_KEY', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertNotIn('GIT_TEMPLATE_DIR', env)
        self.assertNotIn('GIT_CONFIG_COUNT', env)
        self.assertNotIn('GIT_CONFIG_KEY_0', env)
        self.assertNotIn('GIT_CONFIG_VALUE_0', env)
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], '/dev/null')
        self.assertEqual(env['GIT_CONFIG_SYSTEM'], '/dev/null')

    def test_complete_issued_image_and_all_members_are_exact(self):
        expected = (ORACLE / 'contract.json').read_bytes()
        self.assertEqual(len(expected), 51308)
        self.assertEqual(hashlib.sha256(expected).hexdigest(),
                         'c44a85a6160a9fb5ddc6ac938ce7b15d462c9d40b2808d2db92699ad15b8f4d2')
        actual = approval.gate_contract_bytes('2')
        self.assertEqual(actual, expected)
        members = {key: hashlib.sha256(json.dumps(value, sort_keys=True,
                   separators=(',', ':')).encode()).hexdigest()
                   for key, value in json.loads(actual)['policy'].items()}
        self.assertEqual(len(members), 62)
        self.assertEqual(members, json.loads((ORACLE / 'members.json').read_text()))

    def test_four_frozen_schema_blobs_are_exact(self):
        hashes = json.loads((ORACLE / 'schema-hashes.json').read_text())
        self.assertEqual(len(hashes), 4)
        for relative, expected in hashes.items():
            with self.subTest(schema=relative):
                frozen = SCRIPTS.parent / 'schemas' / 'council-policy-v2' / relative
                self.assertEqual(hashlib.sha256(frozen.read_bytes()).hexdigest(),
                                 expected)


if __name__ == '__main__':
    unittest.main()
