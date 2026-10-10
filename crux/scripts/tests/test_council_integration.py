"""Current execution and issued replay use separate, complete policy images."""
from pathlib import Path
import sys
import unittest
import json
import tempfile
import subprocess
import hashlib
import importlib.util
import copy
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_council_gate_attempts import _Base
import implementation_approval as approval
from test_implementation_cycles import WriterFixture
from test_implementation_cycles import book_two, migration_authorizer
import test_run_council as runner
import _council_gate_support as sup
import yaml
from unittest import mock
import council_gate as cg
import council_history_v3 as policy
import council_history_v4 as policy_four
import council_records as cr


class CurrentFormatDispatchTests(_Base):
    def test_committed_start_interpretation_is_independent_of_shared_yaml_changes(self):
        import _yaml_min
        run = self.env.load_run()
        expected = policy.start_identity(self.env.root, run, self.env.run_path)
        seal = approval.production_contract_bytes('3')
        with mock.patch.object(_yaml_min, 'load_yaml', return_value={'wrong': True}), \
             mock.patch.object(_yaml_min, '_normalize', return_value={'wrong': True}):
            self.assertEqual(policy.start_identity(self.env.root, run, self.env.run_path), expected)
            self.assertEqual(approval.production_contract_bytes('3'), seal)

    def test_start_identity_retains_first_committed_snapshot(self):
        run = self.env.load_run()
        first = policy.start_identity(self.env.root, run, self.env.run_path)
        self.assertEqual(first['base_commit'], run['base_commit'])
        self.assertEqual(first['run']['path'], self.env.run_path.relative_to(self.env.root).as_posix())
        blob = cr.git(self.env.root, 'show', first['commit'] + ':' + first['run']['path'])
        self.assertEqual(blob.returncode, 0)
        self.assertEqual(first['run']['sha256'], cr.sha256_bytes(blob.stdout))
        self.env.set_run(4)
        self.env.commit_records(self.env.run_path, msg='later snapshot')
        self.assertEqual(policy.start_identity(self.env.root, self.env.load_run(), self.env.run_path), first)
        changed = {**run, 'base_commit': cr.git(self.env.root, 'rev-parse', 'HEAD').stdout.decode().strip()}
        with self.assertRaises(cr.RecordError):
            policy.start_identity(self.env.root, changed, self.env.run_path)

    def test_format_two_run_cannot_bypass_open_attempt(self):
        attempt = self.attempt()
        run = self.env.load_run()
        run['format_version'] = '2'
        gate = cg.classify(self.env.book, 3)
        verdict = cg.evaluate_council_gate(gate, run, self.env.run_path, self.env.root,
                                         self.env.args_for(attempt))
        self.assertEqual(verdict.verdict, 'stop')
        self.assertEqual(verdict.stops, [4])
        self.assertEqual(verdict.deciding_record, attempt.relative_to(self.env.root).as_posix())
        self.assertEqual(cg.expected_round(run, self.env.run_dir, self.env.root, 'adr-1', 3), 1)


class ImplementationAttemptShapeTests(unittest.TestCase):
    def test_implementation_claim_is_closed_and_kind_matches_its_tag(self):
        fixture = Path(__file__).parent / 'fixtures/council_records/council-attempt.json'
        doc = json.loads(fixture.read_text())
        doc['binding']['module_tag'] = 'implementation-1'
        doc['council_kind'] = 'implementation'
        self.assertEqual(cr.schema_errors(doc, 'council-attempt'), [])
        doc['council_kind'] = 'verify'
        self.assertTrue(cr.schema_errors(doc, 'council-attempt'))
        doc['council_kind'] = 'implementation'
        doc['binding']['module_tag'] = 'adr-1'
        self.assertTrue(cr.schema_errors(doc, 'council-attempt'))
        doc['binding']['module_tag'] = 'implementation-1'
        doc['authority'] = True
        self.assertTrue(cr.schema_errors(doc, 'council-attempt'))

    def test_recovery_keeps_implementation_scope(self):
        import council_recovery
        book = {'prompts': [{'n': 2, 'module_tag': 'implementation-1'}]}
        self.assertEqual(council_recovery._scope({'current_prompt': 2}, book, None),
                         (2, 'implementation-1'))


class CurrentRetainedLocatorTests(unittest.TestCase):
    def test_declared_run_relative_and_legacy_repository_relative_names_share_one_locator(self):
        def retained(repo, run_path, given):
            return approval._policy_call(approval.supported_profile(approval.CONTRACT_VERSION), "retained_path",
                                         repo, run_path, given)
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run = repo / 'docs/promptbooks/runs/PB-0999-fixture/run-RUN-001.yaml'
            rel = 'docs/promptbooks/runs/PB-0999-fixture/council/subjects/revision.yaml'
            self.assertEqual(retained(repo, run, 'council/subjects/revision.yaml'), rel)
            self.assertEqual(retained(repo, run, rel), rel)
            for given in ('revision.yaml', 'other/council/subjects/revision.yaml',
                          'docs/promptbooks/runs/another/council/subjects/revision.yaml',
                          'council/subjects/../revision.yaml', str(repo / rel)):
                with self.subTest(given=given), self.assertRaises(approval.Refused):
                    retained(repo, run, given)
            self.assertEqual(list(repo.rglob('*')), [])


class SealedCurrentAdmissionTests(unittest.TestCase):
    def test_explicit_start_parser_root_cannot_be_foreign_or_missing(self):
        loader = getattr(policy, 'start_snapshot_yaml', policy.schema_engine)
        for replacement in (None, lambda text: {}):
            with self.subTest(replacement=replacement), mock.patch.object(loader, 'load_yaml', replacement):
                with self.assertRaises(approval.Refused): approval.production_contract_bytes('3')

    def test_missing_start_parser_module_refuses_as_policy_unavailable(self):
        with mock.patch.object(policy, 'start_snapshot_yaml', None):
            with self.assertRaisesRegex(approval.Refused, '^context-policy-unavailable$'):
                approval.gate_contract_bytes('3')

    def test_actual_preparation_reaches_semantic_helpers_and_closed_negative_controls(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = WriterFixture(Path(folder) / 'repo')
            seen = set()
            previous = sys.getprofile()
            def trace(frame, event, arg):
                if event == 'call' and frame.f_globals.get('__name__') == policy_four.__name__:
                    seen.add(frame.f_code.co_name)
                if event == 'call' and frame.f_globals.get('__name__') == policy_four.start_snapshot_yaml.__name__:
                    seen.add('owned-yaml.' + frame.f_code.co_name)
            try:
                sys.setprofile(trace)
                binding, files = approval.prepare_implementation_close(fixture.root, fixture.run_path,
                    cg.classify(fixture.book, fixture.close), fixture.rel, fixture.artifacts.split(','))
            finally:
                sys.setprofile(previous)
            self.assertTrue({'retained_path', 'validate_context', 'context_records', 'deciding_subject'} <= seen)
            self.assertIn('owned-yaml.load_yaml', seen)
            context = json.loads(files[binding['context']['path']])
            for field, value in (('format_version', '1'), ('start_identity', None),
                    ('records', [{'path': 'bad', 'sha256': 'wrong'}]), ('artifacts', ['../bad']), ('authority', True)):
                changed = copy.deepcopy(context); changed[field] = value
                with self.subTest(field=field), self.assertRaises(cr.RecordError): policy_four.validate_context(changed)
            changed = copy.deepcopy(context); changed['contract']['version'] = '2'
            with self.assertRaises(cr.RecordError): policy_four.validate_context(changed)
            deciding = json.loads(fixture.receipt.read_bytes())
            expected = policy_four.deciding_subject(fixture.root, fixture.run_path, deciding, binding['revision'])
            self.assertEqual(expected, binding['retained_subject'])
            for fault in ('digest', 'missing', 'duplicate'):
                changed = copy.deepcopy(deciding)
                if fault == 'digest': changed['subjects'][0]['sha256'] = '0' * 64
                if fault == 'missing': changed['subjects'] = []
                if fault == 'duplicate': changed['subjects'] *= 2
                with self.subTest(fault=fault), self.assertRaises(cr.RecordError):
                    policy_four.deciding_subject(fixture.root, fixture.run_path, changed, binding['revision'])

    def test_each_reached_semantic_helper_is_inside_candidate_contract(self):
        for name in ('retained_path', 'validate_context', 'context_records', 'deciding_subject'):
            with self.subTest(name=name):
                self.assertTrue(hasattr(policy, name))
                with mock.patch.object(policy, name, lambda *args: None):
                    with self.assertRaises(approval.Refused):
                        approval.production_contract_bytes('3')

    def test_isolated_source_mutation_of_each_helper_changes_the_actual_image(self):
        source = Path(policy.__file__).read_text()
        expected = approval.production_contract_bytes('3')
        with tempfile.TemporaryDirectory() as folder:
            def load(text):
                path = Path(folder) / 'owned_policy.py'
                path.write_text(text)
                spec = importlib.util.spec_from_file_location('owned_policy_variant', path)
                module = importlib.util.module_from_spec(spec)
                with mock.patch.dict(sys.modules, {spec.name: module}): spec.loader.exec_module(module)
                module._ATTEMPT_SCHEMA = policy._ATTEMPT_SCHEMA
                module.schema_engine = policy.schema_engine
                return module
            for name in ('retained_path', 'validate_context', 'context_records', 'deciding_subject'):
                with self.subTest(name=name):
                    baseline = load(source)
                    descriptor = approval.PolicyProfile('3', baseline,
                        approval.supported_profile('3').contract_sha256, cr)
                    with mock.patch.dict(approval.SUPPORTED_PROFILES, {'3': descriptor}), \
                         mock.patch.dict(sys.modules, {'owned_policy_variant': baseline}):
                        self.assertEqual(approval.gate_contract_bytes('3'), expected)
                    line = next(line for line in source.splitlines(keepends=True) if line.startswith('def ' + name + '('))
                    mutated = load(source.replace(line, line + '    _proof_require(False, "isolated-semantic-mutation")\n', 1))
                    descriptor = approval.PolicyProfile('3', mutated,
                        approval.supported_profile('3').contract_sha256, cr)
                    with mock.patch.dict(approval.SUPPORTED_PROFILES, {'3': descriptor}), \
                         mock.patch.dict(sys.modules, {'owned_policy_variant': mutated}):
                        self.assertNotEqual(approval.gate_contract_bytes('3'), expected)
                        with self.assertRaises(approval.Refused): approval.production_contract_bytes('3')
                        args = {'retained_path': (Path(folder), Path(folder) / 'run.yaml', 'bad'),
                            'validate_context': ({},), 'context_records': ([], {}, None),
                            'deciding_subject': (Path(folder), Path(folder) / 'run.yaml', {}, {})}[name]
                        with self.assertRaisesRegex(cr.RecordError, 'isolated-semantic-mutation'):
                            getattr(mutated, name)(*args)


class CandidateCloseCompositionTests(unittest.TestCase):
    def test_actual_close_issues_current_context_and_preserves_retained_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = WriterFixture(Path(folder) / 'repo')
            result = fixture.advance('--implementation-revision', fixture.rel)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            import yaml
            run = yaml.safe_load(fixture.run_path.read_bytes())
            binding = run['implementation_bindings'][0]
            context = json.loads((fixture.root / binding['context']['path']).read_bytes())
            self.assertEqual(context['format_version'], '3')
            self.assertEqual(context['contract']['version'], '4')
            self.assertEqual(context['start_identity'], policy_four.start_identity(fixture.root, run, fixture.run_path))
            from _council_gate_support import commit_all
            commit_all(fixture.root, 'synthetic committed current close')
            proof = approval.validate_historical_implementation_binding(fixture.root, fixture.run_path,
                slot=fixture.slot, revision_path=fixture.rel,
                revision_sha256=binding['revision']['sha256'])
            self.assertEqual(proof.binding, binding)


class ActualRunnerCloseTests(runner._Base):
    """Production writers with synthetic HTTP seats; never a real council approval."""

    def _install(self, kind, migration=False, combined=False):
        old_root = self.env.root
        self.env.root = sup.init_repo(old_root.parent / 'actual-repo')
        for name in ('docs', 'run_dir', 'council', 'book_path', 'run_path', 'subject'):
            setattr(self.env, name, self.env.root / getattr(self.env, name).relative_to(old_root))
        self.subject = self.env.subject
        self.question = self.env.root / self.question.relative_to(old_root)
        for path in (self.env.council, self.env.book_path.parent, self.subject.parent):
            path.mkdir(parents=True, exist_ok=True)
        self.env.book = book_two(kind, combined=combined)
        declaration = self.env.book['implementation_slots'][0]
        if migration:
            declaration.update(scope=['docs/adrs/migrations'], migration_batch={
                'role': 'migration-batch', 'path': 'docs/adrs/migrations/implementation-pilot-001.yaml'})
        self.env.book.update(current_run=None, current_prompt=None)
        self.env.book_path.write_text(yaml.safe_dump(self.env.book, sort_keys=False))
        (self.env.root / '.bionic.yml').write_text('config_version: "1"\ndocs_dir: docs\n')
        self.subject.write_text('---\nid: ADR-0001\nstatus: Accepted\ngoverns:\n'
            '- handle: ADR-0001/fixture-rule\n  rule: Preserve synthetic output.\n---\n')
        if migration:
            (self.subject.parent / 'ADR-0146-authorizer.md').write_text('---\n' +
                yaml.safe_dump(migration_authorizer()) + '---\n')
        (self.env.root / 'widget.txt').write_text('before\n')
        sup.commit_all(self.env.root, 'synthetic declared book and constraints')
        started = subprocess.run([sys.executable, str(sup.SCRIPTS / 'start-run.py'),
            str(self.env.book_path), '--run-id', sup.RUN_ID, '--output', str(self.env.run_path)],
            capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.env.hash = self.env.load_run()['book_content_hash']
        self.env.book.update(current_run=sup.RUN_ID, current_prompt=1)
        self.env.book_path.write_text(yaml.safe_dump(self.env.book, sort_keys=False))
        sup.commit_all(self.env.root, 'actual start snapshot')
        return declaration

    def _advance(self, artifacts=(), selector=()):
        result = subprocess.run([sys.executable, str(sup.SCRIPTS / 'advance-run.py'),
            str(self.env.run_path), '--outcome', 'done', '--book', str(self.env.book_path),
            '--artifacts', ','.join(self.env.rel(p) for p in artifacts), *selector],
            capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # These are prompt snapshots, not parent-manual council evidence commits.
        changed = sup.git(self.env.root, 'diff', '--name-only').splitlines()
        self.assertTrue(set(changed) <= {self.env.rel(self.env.run_path), self.env.rel(self.env.book_path)})
        sup.commit_all(self.env.root, 'actual advance snapshot')

    def _produce(self, subject, prompt, selector=()):
        before = self.env.run_path.read_bytes()
        code, out, err, gateway = self.run_main(self.argv('--retain-subjects', *selector,
            prompt=prompt, subjects=[subject]))
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(len(gateway.requests), 3)
        self.assertEqual(self.env.run_path.read_bytes(), before)
        record = self.newest_record()[0]
        doc = json.loads(record.read_bytes())
        self.assertEqual(doc['format_version'], '2')
        self.assert_committed(record)
        attempt = self.env.root / doc['attempt']['path']
        self.assert_committed(attempt)
        self.assertEqual(doc['attempt']['sha256'], hashlib.sha256(attempt.read_bytes()).hexdigest())
        retained = self.env.run_dir / doc['subjects'][0]['retained_copy']
        self.assertEqual(retained.read_bytes(), subject.read_bytes())
        return record

    def _chain(self, kind='implementation', migration=False, combined=False, omit_attempt=False):
        declaration = self._install('adr' if combined else kind, migration, combined)
        artifacts = []
        if combined:
            self._advance()
            self.question.write_text('Assess Completeness, Correctness, Consistency, Clarity and Security.\n')
            sup.commit_all(self.env.root, 'synthetic architectural question')
            architecture = self._produce(self.subject, 2)
            artifacts = [architecture]
            for _ in range(3): self._advance(artifacts)
        author = 5 if combined else 1
        if migration:
            from test_implementation_migration import document
            selected = document(); selected['approval_slot'] = declaration['slot']
            decision = self.env.root / declaration['migration_batch']['path']
            flag, container, subject_key = '--migration-batch', 'migration_bindings', 'batch'
        else:
            from test_implementation_decisions import decision as decision_doc
            selected = decision_doc(); selected['slot'] = declaration['slot']
            decision = self.env.run_dir / 'implementations/RUN-001/strategy/revision-001.yaml'
            flag, container, subject_key = '--implementation-revision', 'implementation_bindings', 'revision'
        decision.parent.mkdir(parents=True, exist_ok=True)
        decision.write_text(yaml.safe_dump(selected, sort_keys=False))
        witnessed = subprocess.run([sys.executable, str(sup.SCRIPTS / 'run-work-witness.py'),
            'record', str(self.env.run_path), '--prompt', str(author), '--path', str(decision)],
            capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(witnessed.returncode, 0, witnessed.stdout + witnessed.stderr)
        sup.commit_all(self.env.root, 'synthetic subject authored with write-time witness')
        if kind != 'patch': self._advance(artifacts)
        prompt = 6 if combined else 1 if kind == 'patch' else 2
        council_kind = 'implementation' if combined else kind
        dimensions = runner.rc_mod._IMPLEMENTATION_DIMENSIONS[council_kind]
        rel = self.env.rel(decision)
        self.question.write_text(f'Assess {rel} SHA256 {hashlib.sha256(decision.read_bytes()).hexdigest()}. '
            f'Assess {", ".join(dimensions)} and architectural conflict.\n')
        sup.commit_all(self.env.root, 'synthetic exact formal question')
        record = self._produce(decision, prompt, (flag, rel))
        self.assertEqual(json.loads(record.read_bytes())['council_kind'], council_kind)
        artifacts.append(record)
        if kind != 'patch':
            self._advance(artifacts)
            self._advance(artifacts)
        self._advance(artifacts, (flag, rel))
        run = self.env.load_run()
        binding = run[container][0]
        context = json.loads((self.env.root / binding['context']['path']).read_bytes())
        self.assertEqual((context['format_version'], context['contract']['version']), ('3', '4'))
        self.assertTrue(any(item['path'].endswith('.attempt.json') for item in context['records']))
        consumer = approval.validate_historical_migration_binding if migration else approval.validate_historical_implementation_binding
        kwargs = {'batch_path': rel, 'batch_sha256': binding['batch']['sha256']} if migration else {
            'revision_path': rel, 'revision_sha256': binding['revision']['sha256']}
        seen = set(); previous = sys.getprofile()
        def trace(frame, event, arg):
            if event == 'call' and frame.f_globals.get('__name__') == policy_four.__name__: seen.add(frame.f_code.co_name)
            if event == 'call' and frame.f_globals.get('__name__') == policy_four.start_snapshot_yaml.__name__:
                seen.add('owned-yaml.' + frame.f_code.co_name)
        try:
            sys.setprofile(trace)
            proof = consumer(self.env.root, self.env.run_path, slot=declaration['slot'], **kwargs)
        finally:
            sys.setprofile(previous)
        self.assertTrue({'retained_path', 'validate_context', 'context_records', 'deciding_subject'} <= seen)
        self.assertIn('owned-yaml.load_yaml', seen)
        self.assertEqual(proof.binding, binding)
        self.assertEqual(binding[subject_key]['path'], rel)
        retained = self.env.root / binding['retained_subject']['path']
        original = retained.read_bytes()
        retained.write_bytes(original + b'changed copy\n')
        before = {p.relative_to(self.env.root).as_posix(): p.read_bytes()
                  for p in self.env.root.rglob('*') if p.is_file() and '.git' not in p.parts}
        with self.assertRaises(approval.Refused):
            consumer(self.env.root, self.env.run_path, slot=declaration['slot'], **kwargs)
        self.assertEqual(before, {p.relative_to(self.env.root).as_posix(): p.read_bytes()
            for p in self.env.root.rglob('*') if p.is_file() and '.git' not in p.parts})
        retained.write_bytes(original)
        self.assertEqual(sup.git(self.env.root, 'status', '--porcelain').strip(), '')
        if omit_attempt:
            context['records'] = [ref for ref in context['records'] if not ref['path'].endswith('.attempt.json')]
            context_path = self.env.root / binding['context']['path']
            context_path.write_text(json.dumps(context, sort_keys=True))
            binding['context']['sha256'] = hashlib.sha256(context_path.read_bytes()).hexdigest()
            run[container][0] = binding
            self.env.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
            # Replace only this disposable fixture's closing commit so the altered
            # context is first published with DONE and its recomputed binding.
            sup.git(self.env.root, 'add', self.env.rel(context_path), self.env.rel(self.env.run_path))
            sup.git(self.env.root, 'commit', '--amend', '--no-edit')
            before = {p.relative_to(self.env.root).as_posix(): p.read_bytes()
                      for p in self.env.root.rglob('*') if p.is_file() and '.git' not in p.parts}
            with self.assertRaisesRegex(approval.Refused, '^history-inventory-mismatch$'):
                consumer(self.env.root, self.env.run_path, slot=declaration['slot'], **kwargs)
            self.assertEqual(before, {p.relative_to(self.env.root).as_posix(): p.read_bytes()
                for p in self.env.root.rglob('*') if p.is_file() and '.git' not in p.parts})

    def test_committed_recomputed_context_cannot_omit_attempt(self): self._chain(omit_attempt=True)

    def test_actual_implementation_runner_close_and_retained_consumer(self): self._chain()
    def test_actual_migration_runner_close_and_retained_consumer(self): self._chain(migration=True)
    def test_existing_formal_verify_runner_close_and_retained_consumer(self): self._chain('verify')
    def test_existing_formal_patch_runner_close_and_retained_consumer(self): self._chain('patch')
    def test_combined_structural_implementation_runner_close_and_retained_consumer(self): self._chain(combined=True)


if __name__ == '__main__':
    unittest.main()
