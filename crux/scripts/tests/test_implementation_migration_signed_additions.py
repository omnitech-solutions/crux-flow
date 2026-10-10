"""Published inventory freezes activation inputs, then admits unrelated current facts."""
import unittest
from unittest import mock
from pathlib import Path
import hashlib
import tempfile
import yaml
import implementation_migration as migration
import observation_admission as admission
import _council_gate_support as sup
import test_implementation_authority as fixtures


class PublishedAdditions(unittest.TestCase):
    def setUp(self, *, baseline=False, affected_scope=False):
        fixture = fixtures.PublicationProof()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture = fixture; self.root = fixture.root; self.f = fixture.f
        self.close = fixture.close; self.publish = fixture.publish; self.snapshot = fixture.snapshot
        self.folder = self.root / 'docs/observations'; self.folder.mkdir(exist_ok=True)
        (self.root / 'factual.py').write_text('x = 1\n')
        (self.root / 'docs/manifest.yml').write_text('schema_version: 5\nconcerns_enabled: [adrs, observations]\n')
        adrs = self.root / 'docs/adrs'
        # Match PublishedConsumers' canonical raw admission fixture before the close.
        (adrs / 'ADR-0109-predecessor.md').write_text('---\n' + yaml.safe_dump(dict(
            id='ADR-0109', status='Accepted', governs=[dict(handle='ADR-0109/old-rotation',
                domain='decision-review', rule='The predecessor.', scope='Assessment', provenance='authored')])) + '---\n')
        for source in adrs.glob('ADR-*.md'):
            text = source.read_text(); block = migration.adr_frontmatter.frontmatter_block(text)
            fm = migration._yaml(block)
            for rule in fm.get('governs', []):
                rule.setdefault('provenance', 'authored'); rule.setdefault('domain', 'testing')
                rule.setdefault('scope', 'reader')
            source.write_text('---\n' + yaml.safe_dump(fm) + '---\n' + text.split('---', 2)[2].lstrip('\n'))
        if affected_scope:
            source = adrs / 'ADR-0110-source.md'
            text = source.read_text(); fm = migration._yaml(migration.adr_frontmatter.frontmatter_block(text))
            fm['governs'][0]['scope'] = 'factual.py'
            source.write_text('---\n' + yaml.safe_dump(fm) + '---\n' + text.split('---', 2)[2].lstrip('\n'))
            batch = yaml.safe_load(self.f.path.read_bytes())
            batch['entries'][0]['affected_governs'][0]['scope'] = 'factual.py'
            batch['entries'][0]['source_identity'] = migration.source_identity(batch['entries'][0])
            self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False))
        if baseline:
            self.observation()
            batch = yaml.safe_load(self.f.path.read_bytes())
            batch['signed_dependencies'] = migration._signed_inventory(self.root, self.root / 'docs')[0]
            self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False))
            fixtures.AuthorityView.refresh_reviewed_batch(fixture)
        elif affected_scope:
            fixtures.AuthorityView.refresh_reviewed_batch(fixture)
        else:
            sup.commit_all(self.root, 'synthetic canonical source metadata before close')
        self.close(); self.publication = self.publish()
        self.original = migration.authority_view(self.root)

    def observation(self, **changes):
        doc = dict(id='OBS-0001', status='ratified', provenance='recovered',
            anchor_id='a' * 16, evidence=['factual.py:1-1'], governs=[dict(
                handle='OBS-0001/distinct-fact', rule='The source assigns one.',
                domain='testing', scope='factual.py', provenance='recovered')])
        doc.update(changes)
        path = self.folder / (doc['id'] + '-fact.md')
        path.write_text('---\n' + yaml.safe_dump(doc) + '---\n# Fact\n')
        return path

    def view(self):
        before = self.snapshot()
        try: return migration.authority_view(self.root)
        finally: self.assertEqual(self.snapshot(), before)

    def test_uncommitted_and_committed_unrelated_observation_preserve_historical_facts(self):
        path = self.observation()
        first = self.view()
        self.assertEqual(first, self.original)
        self.assertNotIn(path.relative_to(self.root).as_posix(),
            [item['path'] for item in first['dependency_fingerprints']])
        sup.commit_all(self.root, 'synthetic unrelated observation after publication')
        self.assertEqual(self.view(), first)

    def test_nonratified_activation_member_may_later_be_unrelated(self):
        # A fresh lineage binds the same actual close, with an observed row at activation.
        migration.cr.git(self.root, 'reset', '--hard', self.publication['publication_commit'] + '^')
        self.observation(status='observed')
        self.publish()
        self.observation()
        self.assertEqual(self.view()['historical_handles'], self.original['historical_handles'])

    def test_affected_historical_reasoning_malformed_and_collision_additions_refuse(self):
        source = self.root / 'docs/adrs/ADR-0110-source.md'
        line = len(source.read_text().splitlines())
        faults = [dict(evidence=[f'docs/adrs/ADR-0110-source.md:{line}-{line}']),
                  dict(evidence=['missing.py:1-1']), dict(evidence=['bad']),
                  dict(governs=[dict(handle='OBS-0001/rotation', rule='New.',
                       domain='testing', scope='factual.py', provenance='recovered')]),
                  dict(governs=[dict(handle='OBS-0001/distinct-fact', rule='New.',
                       domain='testing', scope='factual.py', provenance='decided')])]
        for fault in faults:
            with self.subTest(fault=fault):
                self.observation(**fault)
                with self.assertRaises(migration.Refused): self.view()
        path = self.observation()
        duplicate = self.folder / 'OBS-0001-duplicate.md'; duplicate.write_bytes(path.read_bytes())
        with self.assertRaises(migration.Refused): self.view()

    def test_activation_omission_and_prepublication_current_addition_are_not_waived(self):
        self.observation()
        with self.assertRaisesRegex(migration.Refused, 'signed-dependency-drift'):
            migration._validated_inputs(self.root, self.fixture.proof)
        migration.cr.git(self.root, 'reset', '--hard', self.publication['publication_commit'] + '^')
        self.observation(); self.publish()
        with self.assertRaisesRegex(migration.Refused, 'signed-dependency-drift'): self.view()

    def test_frozen_members_and_full_ledger_cannot_change_disappear_move_or_be_replaced(self):
        self.setUp(baseline=True)
        path = self.folder / 'OBS-0001-fact.md'
        ledger = self.root / 'docs/adrs/doctrine/reconciliations.yml'
        for target in (path, ledger):
            original = target.read_bytes()
            for mode in ('changed', 'deleted', 'renamed', 'replacement'):
                moved = target.with_name('moved-' + target.name)
                with self.subTest(path=target.name, mode=mode):
                    if mode == 'changed': target.write_bytes(original + b'changed\n')
                    elif mode == 'deleted': target.unlink()
                    elif mode == 'renamed': target.rename(moved)
                    else: target.write_text('---\nid: OBS-9999\nstatus: ratified\n---\n')
                    with self.assertRaises(migration.Refused): self.view()
                    moved.unlink(missing_ok=True); target.write_bytes(original)
        self.assertEqual(self.view(), self.original)

    def test_new_affected_observation_still_requires_disposition_but_distinct_source_passes(self):
        self.setUp(affected_scope=True)
        self.observation()
        with self.assertRaisesRegex(migration.Refused, 'human-disposition-pending'): self.view()
        (self.root / 'distinct.py').write_text('y = 2\n')
        self.observation(evidence=['distinct.py:1-1'])
        self.assertEqual(self.view(), self.original)

    def test_new_invariant_and_new_signed_ledger_are_not_unrelated_observation_additions(self):
        invariant = self.root / 'docs/invariants/member.md'; invariant.parent.mkdir(exist_ok=True)
        invariant.write_text('---\nid: INV-0001\nratification: ratified\nrelated_adrs: []\n---\n')
        with self.assertRaises(migration.Refused): self.view()
        invariant.unlink()
        ledger = self.root / 'docs/adrs/summaries/backfill-reviews.yml'; ledger.parent.mkdir(exist_ok=True)
        ledger.write_text('config_version: "1"\nbatches: []\nreceipts: []\n')
        with self.assertRaises(migration.Refused): self.view()

    def test_new_member_byte_or_roster_change_during_validation_refuses(self):
        path = self.observation(); original = migration._unchanged_inputs
        for mode in ('bytes', 'roster'):
            self.observation()
            extra = self.folder / 'OBS-0002-extra.md'
            def actor(*args):
                original(*args)
                if mode == 'bytes': path.write_text(path.read_text() + 'changed\n')
                else:
                    extra.write_text(path.read_text().replace('OBS-0001', 'OBS-0002'))
            with self.subTest(mode=mode), mock.patch.object(migration, '_unchanged_inputs', side_effect=actor):
                with self.assertRaises(migration.Refused): migration.authority_view(self.root)
            extra.unlink(missing_ok=True)

    def test_existing_admission_operation_revalidates_current_addition_bytes_and_membership(self):
        self.observation()
        context = admission._load_context(self.root)
        self.observation(evidence=['factual.py:1-1'], anchor_id='b' * 16)
        before = self.snapshot()
        with self.assertRaises(ValueError): admission._revalidate(context)
        self.assertEqual(self.snapshot(), before)

    def newline_observation(self, newline, **changes):
        path = self.observation(**changes)
        path.write_bytes(path.read_bytes().replace(b'\n', newline))
        return path

    def test_member_newline_presentations_preserve_raw_inventory_and_unrelated_facts(self):
        for newline in (b'\n', b'\r\n', b'\r'):
            with self.subTest(newline=newline):
                path = self.newline_observation(newline)
                refs, members = migration._member_inventory(self.root, self.root / 'docs')
                self.assertEqual(refs, [dict(path=path.relative_to(self.root).as_posix(),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest())])
                self.assertEqual(members[0]['id'], 'OBS-0001')
                self.assertEqual(self.view(), self.original)

    def test_member_newlines_cannot_hide_historical_or_affected_reasoning(self):
        source = self.root / 'docs/adrs/ADR-0110-source.md'
        line = len(source.read_text().splitlines())
        for newline in (b'\r\n', b'\r'):
            with self.subTest(newline=newline):
                self.newline_observation(newline,
                    evidence=[f'docs/adrs/ADR-0110-source.md:{line}-{line}'])
                with self.assertRaises(migration.Refused): self.view()
        self.setUp(affected_scope=True)
        for newline in (b'\r\n', b'\r'):
            with self.subTest(affected=newline):
                self.newline_observation(newline)
                with self.assertRaisesRegex(migration.Refused, 'human-disposition-pending'): self.view()

    def test_member_newlines_cannot_hide_activation_inventory_omissions(self):
        for newline in (b'\r\n', b'\r'):
            migration.cr.git(self.root, 'reset', '--hard', self.publication['publication_commit'] + '^')
            self.folder.mkdir(exist_ok=True)
            self.newline_observation(newline); self.publish()
            with self.subTest(newline=newline), self.assertRaisesRegex(
                    migration.Refused, 'signed-dependency-drift'):
                self.view()


class MemberYamlBoundary(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / '.bionic.yml').write_text('config_version: "1"\ndocs_dir: docs\n')
        self.tree = self.root / 'docs'; folder = self.tree / 'observations'; folder.mkdir(parents=True)
        self.path = folder / 'OBS-0001-fact.md'

    def inventory(self, extra):
        self.path.write_text('---\nid: OBS-0001\nstatus: ratified\n'
            'evidence: [factual.py:1-1]\n' + extra + '\n---\n')
        before = self.path.read_bytes()
        try: return migration._member_inventory(self.root, self.tree)
        finally: self.assertEqual(self.path.read_bytes(), before)

    def test_ordinary_alias_dag_keeps_member_and_raw_digest(self):
        extra = 'ordinary_extra: &a [one]\n'; prior = 'a'
        for name in 'bcdefghi':
            extra += f'{name}: &{name} [*{prior}, *{prior}]\n'; prior = name
        with mock.patch.object(migration, '_NO_GIT_WORK_LIMIT', 100):
            refs, members = self.inventory(extra)
        self.assertEqual(members[0]['id'], 'OBS-0001')
        self.assertEqual(refs[0]['sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_cycle_duplicate_and_malformed_merge_nodes_refuse(self):
        for extra in ('ordinary_extra: &loop [*loop]', 'ordinary_extra: one\nordinary_extra: two',
                      'ordinary_extra: {<<: [one]}'):
            with self.subTest(extra=extra), self.assertRaises(migration.Refused): self.inventory(extra)

    def test_supported_merge_order_and_explicit_override_match_canonical_parser(self):
        text = 'one: &a {value: first}\ntwo: &b {value: second}\nmerged: {<<: [*a, *b], extra: present}\n'
        for suffix in ('', 'override: {<<: *a, value: explicit}\n',
                       'nested: &c {<<: *a, third: 3}\nlast: {<<: [*c, *b]}\n',
                       '=: retainedvalue\n'):
            self.assertEqual(migration._no_git_document(text + suffix, _member=True),
                             yaml.safe_load(text + suffix))
        self.inventory('ordinary_extra: &base {one: 1}\nmerged: {<<: *base}')

    def test_merge_expansion_has_its_own_before_extension_work_bound(self):
        extra = 'a: &a {one: 1, two: 2, three: 3}\n'
        prior = 'a'
        for name in ('b', 'c', 'd', 'e'):
            extra += f'{name}: &{name} {{<<: [*{prior}, *{prior}]}}\n'; prior = name
        with mock.patch.object(migration, '_NO_GIT_WORK_LIMIT', 80):
            with self.assertRaises(migration.Refused): self.inventory(extra)

    def test_node_work_and_depth_are_bounded_before_construction(self):
        with mock.patch.object(migration, '_NO_GIT_WORK_LIMIT', 8):
            with self.assertRaises(migration.Refused):
                self.inventory('ordinary_extra: &a [one]\nsecond: [*a, *a, *a]')
        with mock.patch.object(migration, '_NO_GIT_DEPTH_LIMIT', 4):
            with self.assertRaises(migration.Refused): self.inventory('ordinary_extra: [[[[one]]]]')


if __name__ == '__main__': unittest.main()
