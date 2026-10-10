"""Exact recovery-subject eligibility; all councils/human inputs are synthetic."""
import copy
import json
import subprocess
import sys
import unittest
from unittest import mock
import yaml
import implementation_migration as migration
import test_implementation_authority as fixtures
import _council_gate_support as sup


class InitialSubject(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PublicationProof(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root

    def check(self, **changed):
        before = self.f.snapshot(); index = migration.cr.git(self.root, 'ls-files', '--stage', '-z').stdout
        kwargs = dict(slot=self.f.f.slot, batch_path=self.f.f.rel,
                      batch_sha256=migration._digest(self.f.f.path.read_bytes()))
        kwargs.update(changed)
        try: return migration._current_subject_eligibility(self.root, self.f.f.run_path, **kwargs)
        finally:
            self.assertEqual(self.f.snapshot(), before)
            self.assertEqual(migration.cr.git(self.root, 'ls-files', '--stage', '-z').stdout, index)

    def test_initial_subject_uses_complete_current_authority(self):
        self.check()
        self.f.close(); self.f.publish()
        ledger = self.root / 'docs/adrs/doctrine/reconciliations.yml'
        ledger.write_text(ledger.read_text() + '# synthetic later ledger drift\n')
        sup.commit_all(self.root, 'synthetic current drift must refuse ordinary eligibility')
        with self.assertRaisesRegex(migration.Refused, 'migration-signed-dependency-drift'):
            self.check()

    def test_nested_project_cannot_borrow_parent_git(self):
        nested = self.root / 'nested'; nested.mkdir()
        before = self.f.snapshot()
        with self.assertRaisesRegex(migration.Refused, 'migration-git-required'):
            migration._current_subject_eligibility(nested, 'absent-run.yaml',
                slot='implementation-1', batch_path='absent-batch.yaml', batch_sha256='0'*64)
        self.assertEqual(self.f.snapshot(), before)

    def test_initial_subject_revalidates_publication_roster(self):
        original = migration.ap.live_constraints; actor = []
        target = self.f.witness
        def added(*args):
            original(*args)
            target.write_text('{}\n')
            actor.append(self.f.snapshot())
        with mock.patch.object(migration.ap, 'live_constraints', side_effect=added):
            with self.assertRaises(migration.Refused):
                migration._current_subject_eligibility(self.root, self.f.f.run_path,
                    slot=self.f.f.slot, batch_path=self.f.f.rel,
                    batch_sha256=migration._digest(self.f.f.path.read_bytes()))
        self.assertEqual(self.f.snapshot(), actor[-1])


class RecoverySubject(unittest.TestCase):
    stable_dependencies = False
    def setUp(self):
        self.f = fixtures.RecoveryView(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        # Make the demoted handle raw-eligible so denial must retain historical facts.
        batch = migration.load_batch(self.root, self.f.f.path)
        entry = batch['entries'][0]; entry['affected_governs'][0]['retires'] = []
        entry['source_identity'] = migration.source_identity(entry)
        source = self.root / 'docs/adrs/ADR-0110-source.md'
        source.write_text('---\n' + yaml.safe_dump(dict(id='ADR-0110', status='Accepted',
            governs=entry['affected_governs'])) + '---\n' + entry['clause']['text'])
        self.f.f.path.write_text(yaml.safe_dump(batch, sort_keys=False))
        fixtures.AuthorityView.refresh_reviewed_batch(self.f)
        self.f.close(); self.parent = self.f.publish()
        self.parent_bytes = self.f.f.receipt.read_bytes()
        if self.stable_dependencies:
            ledger = self.root / 'docs/adrs/doctrine/reconciliations.yml'; raw = ledger.read_bytes()
            def stable(batch, path):
                ledger.write_bytes(raw); batch['signed_dependencies'][0]['sha256'] = migration._digest(raw)
                path.write_text(yaml.safe_dump(batch, sort_keys=False))
            self.f.prepare_successor = stable
        original = subprocess.run
        class Started(Exception): pass
        def stop(command, *args, **kwargs):
            if len(command) > 2 and str(command[1]) == str(sup.SCRIPTS / 'advance-run.py'):
                self.run_path = self.root / command[2]
                self.book_path = self.root / command[command.index('--book') + 1]
                self.receipt = command[command.index('--artifacts') + 1]
                raise Started()
            return original(command, *args, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=stop):
            with self.assertRaises(Started): self.f.successor()
        book = yaml.safe_load(self.book_path.read_bytes())
        self.path = self.root / book['implementation_slots'][0]['migration_batch']['path']
        self.batch = migration.load_batch(self.root, self.path)

    def check(self, **changed):
        before = self.f.snapshot(); index = migration.cr.git(self.root, 'ls-files', '--stage', '-z').stdout
        kwargs = dict(slot='implementation-1', batch_path=self.path.relative_to(self.root).as_posix(),
                      batch_sha256=migration._digest(self.path.read_bytes()))
        kwargs.update(changed)
        try: return migration._current_subject_eligibility(self.root, self.run_path, **kwargs)
        finally:
            self.assertEqual(self.f.snapshot(), before)
            self.assertEqual(migration.cr.git(self.root, 'ls-files', '--stage', '-z').stdout, index)

    def save(self, batch):
        batch = copy.deepcopy(batch)
        batch['batch_id'] = 'implementation-pilot-001-recovery-' + migration._canonical_digest(batch['recovery_from'])
        self.path.write_text(yaml.safe_dump(batch, sort_keys=False))
        sup.commit_all(self.root, 'synthetic separately committed selected recovery change')

    def test_started_recovery_has_exact_parent_history_without_current_ledger_waiver(self):
        with self.assertRaisesRegex(migration.Refused, 'migration-signed-dependency-drift'):
            migration.authority_view(self.root)
        self.assertEqual(yaml.safe_load(self.run_path.read_bytes())['current_prompt'], 1)
        self.check()
        self.assertEqual(self.f.f.receipt.read_bytes(), self.parent_bytes)
        # Eligibility is not activation; the existing authority view must still refuse.
        with self.assertRaisesRegex(migration.Refused, 'migration-signed-dependency-drift'):
            migration.authority_view(self.root)

    def test_exact_owner_digest_path_slot_and_source_link_refuse(self):
        for kwargs in (dict(slot='implementation-2'), dict(batch_sha256='0'*64),
                       dict(batch_path='../escaped.yaml'), dict(batch_path=self.f.f.rel)):
            with self.subTest(kwargs=kwargs), self.assertRaises(migration.Refused): self.check(**kwargs)
        for fault in ('parent', 'source', 'disposition'):
            batch = copy.deepcopy(self.batch)
            if fault == 'parent': batch['recovery_from']['witness']['sha256'] = '0'*64
            elif fault == 'source':
                entry = batch['entries'][0]; entry['clause']['text'] += ' Different reasoning.'
                entry['clause']['sha256'] = migration._digest(entry['clause']['text'].encode())
                entry['historical_destination']['clause_sha256'] = entry['clause']['sha256']
                entry['source_identity'] = migration.source_identity(entry)
                batch['recovery_from']['source_identities'] = sorted(e['source_identity'] for e in batch['entries'])
            else: batch['entries'][0]['disposition'] = 'architecture-retained'
            self.save(batch)
            with self.subTest(fault=fault), self.assertRaises(migration.Refused): self.check()
        self.save(self.batch)

    def test_current_missing_nonaccepted_and_demoted_refs_refuse(self):
        host = self.root / 'docs/adrs/ADR-0146-authorizer.md'; original = host.read_bytes()
        host.write_text(host.read_text().replace('status: Accepted', 'status: Proposed'))
        sup.commit_all(self.root, 'synthetic authorizer no longer Accepted')
        with self.assertRaises(migration.Refused): self.check()
        host.write_bytes(original); sup.commit_all(self.root, 'synthetic restore authorizer')
        for handle in ('ADR-0146/missing', 'ADR-0110/rotation'):
            batch = copy.deepcopy(self.batch); batch['entries'][0]['replacement_handles'] = [handle]
            self.save(batch)
            if handle.endswith('/rotation'):
                migration.ap._raw_live_constraints(self.root, [handle])
            with self.subTest(handle=handle), self.assertRaises(migration.Refused): self.check()
        self.save(self.batch)

    def test_head_or_raw_constraint_change_during_eligibility_refuses(self):
        original = migration.ap._raw_live_constraints
        for mode in ('head', 'raw'):
            host = self.root / 'docs/adrs/ADR-0146-authorizer.md'; raw = host.read_bytes()
            actor = []
            def changed(*args):
                original(*args)
                if mode == 'head': sup.git(self.root, 'commit', '--allow-empty', '-m', 'synthetic actor HEAD')
                else: host.write_bytes(raw + b'# synthetic actor raw metadata\n')
                actor.append(self.f.snapshot())
            with mock.patch.object(migration.ap, '_raw_live_constraints', side_effect=changed):
                with self.assertRaises(migration.Refused):
                    migration._current_subject_eligibility(self.root, self.run_path,
                        slot='implementation-1', batch_path=self.path.relative_to(self.root).as_posix(),
                        batch_sha256=migration._digest(self.path.read_bytes()))
            self.assertEqual(self.f.snapshot(), actor[-1]); host.write_bytes(raw)

    def test_late_adr_or_publication_roster_addition_refuses(self):
        original = migration.ap._raw_live_constraints
        for kind in ('adr', 'publication'):
            target = self.root / ('docs/adrs/ADR-0150-late.md' if kind == 'adr'
                else 'docs/adrs/migrations/late.application.json')
            actor = []
            def added(*args):
                original(*args)
                if kind == 'adr':
                    target.write_text('---\n' + yaml.safe_dump(dict(id='ADR-0150', status='Accepted',
                        governs=[dict(handle='ADR-0150/new', retires=['ADR-0146/migration-authority'])])) + '---\n')
                else: target.write_bytes(self.f.witness.read_bytes())
                actor.append(self.f.snapshot())
            with mock.patch.object(migration.ap, '_raw_live_constraints', side_effect=added):
                with self.subTest(kind=kind), self.assertRaises(migration.Refused):
                    migration._current_subject_eligibility(self.root, self.run_path,
                        slot='implementation-1', batch_path=self.path.relative_to(self.root).as_posix(),
                        batch_sha256=migration._digest(self.path.read_bytes()))
            self.assertEqual(self.f.snapshot(), actor[-1]); target.unlink()


class PublishedChain(unittest.TestCase):
    stable_dependencies = True
    setUp = RecoverySubject.setUp
    check = RecoverySubject.check

    def complete(self, run, book, receipt, path, *, yaml_locator=False, run_spelling=None):
        for n in range(1, 5):
            command = [sys.executable, str(sup.SCRIPTS/'advance-run.py'), str(run), '--book', str(book),
                       '--outcome', 'done', '--artifacts', receipt]
            if n == 4: command += ['--migration-batch', path.relative_to(self.root).as_posix()]
            result = subprocess.run(command, capture_output=True, env=sup.scrubbed_env())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            sup.commit_all(self.root, 'synthetic actual closed chain member')
        proof = migration.ap.validate_historical_migration_binding(self.root, run,
            slot='implementation-1', batch_path=path.relative_to(self.root).as_posix(),
            batch_sha256=migration._digest(path.read_bytes()))
        witness = path.with_name(migration.load_batch(self.root, path)['batch_id']+'.application.json')
        witness.write_bytes(migration._publication_locator_bytes(proof, run.relative_to(self.root).as_posix()))
        if run_spelling is not None:
            locator = json.loads(witness.read_bytes()); locator['run_path'] = run_spelling
            witness.write_text(json.dumps(locator))
        if yaml_locator: witness.write_text(yaml.safe_dump(json.loads(witness.read_bytes()), sort_keys=False))
        sup.commit_all(self.root, 'synthetic committed witness, not apply')
        return next(p for p in migration._discover_publications(self.root) if p['path']==witness.relative_to(self.root).as_posix()), proof

    def test_canonical_yaml_published_subject_remains_eligible(self):
        publication, proof = self.complete(self.run_path, self.book_path, self.receipt,
            self.path, yaml_locator=True)
        self.assertEqual(migration._publication_proof(self.root, publication).binding, proof.binding)
        self.check()

    def equivalent_run(self, spelling):
        publication, proof = self.complete(self.run_path, self.book_path, self.receipt,
            self.path, run_spelling=spelling)
        self.assertEqual(migration._publication_proof(self.root, publication).binding, proof.binding)
        self.assertEqual(migration._checked(self.root, spelling)[1], self.run_path.relative_to(self.root).as_posix())
        self.check()

    def test_contained_absolute_locator_run_is_exact_published_member(self):
        self.equivalent_run(str(self.run_path))

    def test_double_slash_locator_run_is_exact_published_member(self):
        self.equivalent_run(self.run_path.relative_to(self.root).as_posix().replace('/', '//'))

    def test_published_B_then_C_preserves_B_eligibility_but_new_nonterminal_parent_refuses(self):
        publication, proof = self.complete(self.run_path, self.book_path, self.receipt, self.path)
        self.check()  # Exact own terminal child.
        batch = copy.deepcopy(self.batch)
        batch['recovery_from'] = dict(witness={k:publication[k] for k in ('path','sha256')},
            batch=proof.binding['batch'], source_identities=sorted(e['source_identity'] for e in batch['entries']))
        batch['batch_id']='implementation-pilot-001-recovery-'+migration._canonical_digest(batch['recovery_from'])
        path=self.path.with_name(batch['batch_id']+'.yaml'); path.write_text(yaml.safe_dump(batch,sort_keys=False))
        book=yaml.safe_load(self.book_path.read_bytes()); book.update(id='PB-0997',current_run=None,current_prompt=None)
        book['implementation_slots'][0]['migration_batch']['path']=path.relative_to(self.root).as_posix()
        bp=self.book_path.with_name('PB-0997-fixture.yaml');bp.write_text(yaml.safe_dump(book,sort_keys=False))
        run=bp.parent.parent/'runs/PB-0997-fixture/run-RUN-001.yaml'
        sup.commit_all(self.root,'synthetic separately declared C')
        result=subprocess.run([sys.executable,str(sup.SCRIPTS/'start-run.py'),str(bp),'--run-id','RUN-001',
            '--output',str(run)],capture_output=True,env=sup.scrubbed_env())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        book.update(current_run='RUN-001',current_prompt=1);bp.write_text(yaml.safe_dump(book,sort_keys=False))
        cp=run.parent/'council/round.json';cp.parent.mkdir();retained=cp.parent/'subjects/batch.yaml'
        retained.parent.mkdir();retained.write_bytes(path.read_bytes())
        receipt=json.loads((self.root/self.receipt).read_bytes());receipt['book'].update(id='PB-0997',content_hash=sup.vp.compute_book_hash(book))
        receipt['subjects']=[dict(path=path.relative_to(self.root).as_posix(),sha256=migration._digest(path.read_bytes()),
            retained_copy=retained.relative_to(self.root).as_posix())]
        sup.select_question(self.root,receipt)
        cp.write_text(json.dumps(receipt));sup.commit_all(self.root,'synthetic C fake council after actual start')
        self.complete(run,bp,cp.relative_to(self.root).as_posix(),path)
        self.check()  # B is a proved nonterminal member, never a new candidate.
        result=subprocess.run([sys.executable,str(sup.SCRIPTS/'advance-run.py'),str(self.run_path),
            '--book',str(self.book_path),'--outcome','done','--artifacts',self.receipt],
            capture_output=True,env=sup.scrubbed_env())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        sup.commit_all(self.root,'synthetic actual B advance after C publication')
        self.check()
        candidate=yaml.safe_load(self.book_path.read_bytes())
        candidate.update(id='PB-0996',current_run=None,current_prompt=None)
        candidate_path=self.book_path.with_name('PB-0996-fixture.yaml')
        candidate_path.write_text(yaml.safe_dump(candidate,sort_keys=False))
        candidate_run=candidate_path.parent.parent/'runs/PB-0996-fixture/run-RUN-001.yaml'
        sup.commit_all(self.root,'synthetic new declared subject cannot borrow B publication')
        result=subprocess.run([sys.executable,str(sup.SCRIPTS/'start-run.py'),str(candidate_path),
            '--run-id','RUN-001','--output',str(candidate_run)],capture_output=True,env=sup.scrubbed_env())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        sup.commit_all(self.root,'synthetic exact new subject started after C')
        before=self.f.snapshot()
        with self.assertRaisesRegex(migration.Refused,'migration-recovery-link-refused'):
            migration._current_subject_eligibility(self.root,candidate_run,slot='implementation-1',
                batch_path=self.path.relative_to(self.root).as_posix(),batch_sha256=migration._digest(self.path.read_bytes()))
        self.assertEqual(self.f.snapshot(),before)

if __name__ == '__main__': unittest.main()
