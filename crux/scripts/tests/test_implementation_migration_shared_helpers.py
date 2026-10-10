"""Shared historical proof adapters; every publication/human input here is synthetic."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest import mock
import yaml
import implementation_migration as migration
import _council_gate_support as sup
import test_implementation_authority as fixtures


class RecoveryLink(unittest.TestCase):
    def setUp(self):
        f = fixtures.RecoveryView(); f.setUp(); self.addCleanup(f.doCleanups)
        self.f = f; self.root = f.root
        f.close(); self.parent = f.publish(); self.parent_proof = f.proof
        self.parent_batch = migration.load_batch(self.root, f.f.rel)
        ledger = self.root / 'docs/adrs/doctrine/reconciliations.yml'
        original = ledger.read_bytes()
        # Keep dependencies unchanged: stale-ledger preclose eligibility is a separate U17 unit.
        def unchanged(batch, path):
            ledger.write_bytes(original)
            batch['signed_dependencies'][0]['sha256'] = migration._digest(original)
            path.write_text(yaml.safe_dump(batch, sort_keys=False))
        f.prepare_successor = unchanged
        self.proof = f.successor()
        self.batch = migration.load_batch(self.root, self.proof.binding['batch']['path'])

    def validate(self, *, batch=None, proof=None, parent=None):
        before = self.f.snapshot()
        try:
            migration._validate_recovery_link(self.root,
                parent_publication=parent or self.parent, parent_proof=self.parent_proof,
                parent_batch=self.parent_batch, batch=batch or self.batch, proof=proof or self.proof)
        finally: self.assertEqual(self.f.snapshot(), before)

    def test_two_actual_closes_share_successor_checks_and_original_chain(self):
        self.validate()
        chain, proof = migration._publication_chain(self.root, migration._discover_publications(self.root))
        self.assertEqual(len(chain), 2)
        self.assertEqual(proof.binding, self.proof.binding)

    def test_wrong_parent_source_disposition_destination_and_close_order_refuse(self):
        faults = [('witness', 'path'), ('batch', 'sha256'), ('source_identities', None)]
        for key, field in faults:
            batch = copy.deepcopy(self.batch)
            if field: batch['recovery_from'][key][field] = 'wrong'
            else: batch['recovery_from'][key] = []
            with self.subTest(key=key), self.assertRaises(migration.Refused): self.validate(batch=batch)
        for key, value in (('disposition', 'enduring-architecture'),
                           ('historical_destination', {'source_adr': 'ADR-0110', 'clause_sha256': '0'*64})):
            batch = copy.deepcopy(self.batch); batch['entries'][0][key] = value
            with self.subTest(key=key), self.assertRaises(migration.Refused): self.validate(batch=batch)
        oldest = migration.cr.git(self.root, 'rev-list', '--max-parents=0', 'HEAD').stdout.decode().strip()
        with self.assertRaises(migration.Refused): self.validate(proof=replace(self.proof, proof_commit=oldest))


class PendingDiscovery(unittest.TestCase):
    def setUp(self):
        f = fixtures.PublicationProof(); f.setUp(); self.addCleanup(f.doCleanups)
        self.f = f; self.root = f.root
        f.close()
        self.raw = (json.dumps(f.locator(), sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()
        self.pending = dict(path=f.witness.relative_to(self.root).as_posix(),
            sha256=migration._digest(self.raw), close_identity={k: f.proof.binding[k] for k in (
                'batch', 'book_id', 'run_id', 'book_content_hash', 'slot', 'gate_prompt')})

    def discover(self, pending=None):
        before = self.f.snapshot()
        index = migration.cr.git(self.root, 'ls-files', '--stage', '-z').stdout
        try: return migration._discover_apply_publications(self.root, pending=pending or self.pending)
        finally:
            self.assertEqual(self.f.snapshot(), before)
            self.assertEqual(migration.cr.git(self.root, 'ls-files', '--stage', '-z').stdout, index)

    def test_absent_exact_pending_is_not_publication_or_authority(self):
        self.assertEqual(migration._publication_locator_bytes(self.f.proof,
            self.f.f.run_path.relative_to(self.root).as_posix()), self.raw)
        self.assertEqual(self.discover(), ([], None))
        self.assertEqual(migration._discover_publications(self.root), [])
        self.assertEqual(migration.authority_view(self.root)['state'], 'original')

    def test_untracked_staged_and_committed_exact_witness_keep_strict_public_discovery(self):
        self.f.witness.write_bytes(self.raw)
        for staged in (False, True):
            if staged: sup.git(self.root, 'add', self.pending['path'])
            with self.subTest(staged=staged):
                self.assertEqual(self.discover(), ([], None))
                with self.assertRaises(migration.Refused): migration._discover_publications(self.root)
                with self.assertRaises(migration.Refused): migration.authority_view(self.root)
        sup.commit_all(self.root, 'synthetic committed locator, not apply producer')
        rows, selected = self.discover()
        self.assertEqual(rows, [selected])
        self.assertEqual(selected['sha256'], self.pending['sha256'])
        self.assertEqual(migration._publication_proof(self.root, selected).binding, self.f.proof.binding)

    def test_claims_flags_wrong_path_digest_role_slot_and_close_refuse(self):
        for key, value in (('path', '../escaped.application.json'), ('path', 'docs/other.application.json'),
                           ('sha256', '0'*64), ('proof', {}), ('assume_active', True)):
            pending = copy.deepcopy(self.pending); pending[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(migration.Refused):
                self.discover(pending)
        for key, value in (('slot', 'implementation-2'), ('gate_prompt', True),
                           ('book_id', 'PB-9999'), ('run_id', '../RUN-001'),
                           ('book_content_hash', 'sha256:'+'0'*64)):
            pending = copy.deepcopy(self.pending); pending['close_identity'][key] = value
            with self.subTest(key=key), self.assertRaises(migration.Refused): self.discover(pending)

    def test_actual_committed_noncanonical_bytes_replay_but_uncommitted_copy_refuses(self):
        raw = (json.dumps(self.f.locator(), sort_keys=True) + '\n').encode()
        self.assertNotEqual(raw, self.raw)
        self.f.witness.write_bytes(raw)
        pending = copy.deepcopy(self.pending); pending['sha256'] = migration._digest(raw)
        with self.assertRaises(migration.Refused): self.discover(pending)
        sup.commit_all(self.root, 'synthetic legacy locator layout remains immutable')
        rows, selected = self.discover(pending)
        self.assertEqual(rows, [selected]); self.assertEqual(selected['content'], raw)
        self.assertEqual(selected['sha256'], pending['sha256'])
        with self.assertRaises(migration.Refused): self.discover()  # No silent SHA substitution.

    def test_deleted_renamed_and_edited_historical_paths_cannot_become_pending(self):
        self.f.witness.write_bytes(self.raw); sup.commit_all(self.root, 'synthetic first publication')
        first = migration.cr.git(self.root, 'rev-parse', 'HEAD').stdout.decode().strip()
        for mode in ('deleted', 'renamed', 'edited'):
            sup.git(self.root, 'reset', '--hard', first)
            pending = copy.deepcopy(self.pending)
            if mode == 'deleted': self.f.witness.unlink()
            elif mode == 'renamed': self.f.witness.rename(self.f.witness.with_name('moved.application.json'))
            else:
                self.f.witness.write_bytes(self.raw + b'\n')
                pending['sha256'] = migration._digest(self.f.witness.read_bytes())
            sup.commit_all(self.root, 'synthetic damaged descendant')
            with self.subTest(mode=mode), self.assertRaises(migration.Refused): self.discover(pending)

    def test_parallel_never_published_lineage_differs_from_deleted_descendant(self):
        before = migration.cr.git(self.root, 'rev-parse', 'HEAD').stdout.decode().strip()
        self.f.witness.write_bytes(self.raw); sup.commit_all(self.root, 'synthetic published branch')
        published = migration.cr.git(self.root, 'rev-parse', 'HEAD').stdout.decode().strip()
        sup.git(self.root, 'checkout', '--detach', before)
        self.assertEqual(self.discover(), ([], None))
        self.assertEqual(migration.authority_view(self.root)['state'], 'original')
        sup.git(self.root, 'checkout', '--detach', published)
        self.f.witness.unlink(); sup.commit_all(self.root, 'synthetic descendant publication loss')
        with self.assertRaises(migration.Refused): self.discover()

    def test_second_pending_index_mismatch_symlink_and_dirty_config_refuse(self):
        extra = self.f.witness.with_name('other.application.json'); extra.write_bytes(self.raw)
        with self.assertRaises(migration.Refused): self.discover()
        extra.unlink()
        self.f.witness.write_bytes(self.raw + b'\n'); sup.git(self.root, 'add', self.pending['path'])
        self.f.witness.write_bytes(self.raw)
        with self.assertRaises(migration.Refused): self.discover()
        sup.git(self.root, 'reset', 'HEAD', '--', self.pending['path']); self.f.witness.unlink()
        self.f.witness.symlink_to(self.f.f.path)
        with self.assertRaises(migration.Refused): self.discover()
        self.f.witness.unlink()
        config = self.root / '.bionic.yml'; config.write_bytes(config.read_bytes() + b'# dirty\n')
        with self.assertRaises(migration.Refused): self.discover()

    def test_read_races_refuse_without_any_adapter_publication(self):
        original = migration._publication_inventory
        for mode in ('target', 'roster', 'config', 'head', 'index'):
            self.f.witness.unlink(missing_ok=True)
            before = self.f.snapshot(); actor_state = []; actor_index = []
            def actor(*args, **kwargs):
                result = original(*args, **kwargs)
                if mode == 'target': self.f.witness.write_bytes(self.raw)
                elif mode == 'roster': self.f.witness.with_name('other.application.json').write_bytes(self.raw)
                elif mode == 'config':
                    config = self.root / '.bionic.yml'; config.write_bytes(config.read_bytes() + b'# actor\n')
                elif mode == 'head':
                    (self.root / 'actor.txt').write_text('independent actor\n')
                    sup.commit_all(self.root, 'synthetic independent actor changes HEAD')
                else:
                    extra = self.f.witness.with_name('other.application.json')
                    extra.write_bytes(self.raw)
                    sup.git(self.root, 'add', extra.relative_to(self.root).as_posix())
                    extra.unlink()
                actor_state.append(self.f.snapshot())
                actor_index.append(migration.cr.git(self.root, 'ls-files', '--stage', '-z').stdout)
                return result
            with self.subTest(mode=mode), mock.patch.object(migration, '_publication_inventory', side_effect=actor):
                with self.assertRaises(migration.Refused):
                    migration._discover_apply_publications(self.root, pending=self.pending)
            self.assertTrue(actor_state); self.assertEqual(self.f.snapshot(), actor_state[-1])
            self.assertEqual(migration.cr.git(self.root, 'ls-files', '--stage', '-z').stdout, actor_index[-1])
            for rel in set(self.f.snapshot()) - set(before): (self.root / rel).unlink()
            for rel, raw in before.items(): (self.root / rel).write_bytes(raw)

    def test_missing_historical_context_and_shallow_history_refuse(self):
        path = self.root / self.f.proof.binding['context']['path']; original = path.read_bytes(); path.unlink()
        with self.assertRaises(migration.Refused): self.discover()
        path.write_bytes(original)
        shallow = self.root / '.git/shallow'; shallow.write_bytes(migration.cr.git(self.root, 'rev-parse', 'HEAD').stdout)
        with self.assertRaises(migration.Refused): self.discover()
        shallow.unlink()

    def test_preselection_refuses_a_small_cyclic_run_without_recursion(self):
        run = self.f.f.run_path
        run.write_text(run.read_text() + '\nordinary_extra: &loop [*loop]\n')
        with self.assertRaises(migration.Refused): self.discover()

    def test_symlink_book_is_refused_before_outside_run_enumeration(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory)
            (outside / self.f.f.run_path.name).write_text('outside sentinel filename only\n')
            alias = self.f.f.run_path.parent.parent / 'PB-9999-outside'
            alias.symlink_to(outside, target_is_directory=True)
            scanned = []; calls = []; original = Path.glob
            def observe(path, pattern, *args, **kwargs):
                calls.append((path, pattern))
                for found in original(path, pattern, *args, **kwargs):
                    scanned.append(found.resolve().parent)
                    yield found
            with mock.patch.object(Path, 'glob', observe):
                with self.assertRaises(migration.Refused): self.discover()
            self.assertNotIn(outside, scanned)
            self.assertNotIn((self.f.f.run_path.parent.parent,
                              '*/' + self.f.f.run_path.name), calls)

    def test_other_index_only_pending_publication_refuses(self):
        extra = self.f.witness.with_name('other.application.json')
        extra.write_bytes(self.raw)
        sup.git(self.root, 'add', extra.relative_to(self.root).as_posix())
        extra.unlink()
        with self.assertRaises(migration.Refused): self.discover()

    def test_run_roster_transport_revalidates_and_closes_on_success_and_swap(self):
        from admission_source_io import SourceIO
        original_init = SourceIO.__init__; original_glob = SourceIO.glob
        original_revalidate = SourceIO.revalidate
        opened = []; validated = []
        def initialize(io, root, **kwargs):
            original_init(io, root, **kwargs); opened.append(io)
        def revalidate(io):
            self.assertIsNotNone(io._fd)
            original_revalidate(io); validated.append(io)
        with mock.patch.object(SourceIO, '__init__', initialize), \
             mock.patch.object(SourceIO, 'revalidate', revalidate):
            self.assertEqual(self.discover(), ([], None))
        self.assertGreaterEqual(len(validated), 2)
        self.assertTrue(all(io._fd is None for io in opened))
        opened.clear()
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory); runs = self.f.f.run_path.parent.parent
            alias = runs / 'PB-9999-racing'; alias.mkdir()
            touched = []
            def swap(io, path, pattern):
                result = original_glob(io, path, pattern)
                if path == runs and not touched:
                    alias.rmdir(); alias.symlink_to(outside, target_is_directory=True)
                    touched.append(True)
                self.assertNotEqual(path, alias)
                return result
            with mock.patch.object(SourceIO, '__init__', initialize), \
                 mock.patch.object(SourceIO, 'glob', swap):
                with self.assertRaises(migration.Refused): self.discover()
            self.assertTrue(touched)
            self.assertTrue(all(io._fd is None for io in opened))


if __name__ == '__main__': unittest.main()
