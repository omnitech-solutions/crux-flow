"""Synthetic full-history controls, never real council or delivery-review evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup
import implementation_decisions as ids
import implementation_approval as approval
import council_gate as cg
import council_records as cr


class Contracts(unittest.TestCase):
    def test_closed_decision_schema_accepts_reasoning_and_rejects_authority(self):
        doc = decision()
        self.assertEqual(ids.schema_errors(doc), [])
        for key in ('governs', 'handle', 'status', 'supersedes', 'unknown_authority'):
            with self.subTest(key=key):
                self.assertTrue(ids.schema_errors({**doc, key: 'forbidden'}))

    def test_format_two_hash_binds_kind_and_slots_without_changing_legacy_hash(self):
        book = sup.make_book('verify')
        self.assertEqual(approval.book_hash(book), sup.vp.compute_book_hash(book))
        book.update(format_version='2', implementation_slots=[slot()])
        original = approval.book_hash(book)
        changed = copy.deepcopy(book)
        changed['implementation_slots'][0]['scope'].append('other.txt')
        self.assertNotEqual(original, approval.book_hash(changed))
        changed = copy.deepcopy(book)
        changed['cycle_kind'] = 'implementation'
        self.assertNotEqual(original, approval.book_hash(changed))

    def test_unsupported_live_format_cannot_advance_as_unclassified(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = sup.make_book('verify'); book['format_version'] = '2'
            env = sup.Env(Path(tmp) / 'repo', kind='verify', book=book)
            run = env.load_run(); run['format_version'] = '2'
            env.run_path.write_text(yaml.safe_dump(run))
            sup.commit_all(env.root, 'synthetic unsupported format')
            before = env.run_path.read_bytes()
            result = subprocess.run([sys.executable, str(sup.SCRIPTS / 'advance-run.py'),
                str(env.run_path), '--outcome', 'done', '--result', 'must refuse', '--book', str(env.book_path)],
                capture_output=True, env=sup.scrubbed_env())
            self.assertNotEqual(result.returncode, 0, result.stdout.decode())
            self.assertEqual(env.run_path.read_bytes(), before)


def slot():
    return {'slot': 'verify-1', 'slug': 'strategy', 'scope': ['widget.txt'],
            'constraint_refs': ['ADR-0001/fixture-rule']}


def decision(run='RUN-001'):
    return {'record_type': 'implementation-decision', 'format_version': '1',
            'book_id': 'PB-0999', 'run_id': run, 'slug': 'strategy', 'revision': 1,
            'slot': 'verify-1', 'display_title': 'Choose stategy', 'scope': ['widget.txt'],
            'constraint_refs': ['ADR-0001/fixture-rule'], 'reasoning': 'Preserve behavior.',
            'approach': 'Use a table.', 'alternatives': ['Use a tree.'],
            'assumptions': ['Inputs are bounded.'], 'intended_evidence': ['Equivalent output.'],
            'source_labels': [{'label': 'Widget', 'path': 'widget.txt'}]}


class Fixture:
    """Explicitly synthetic approvals. No council/model/reviewer was convened."""
    def __init__(self, root, fault=None, partial=False):
        self.env = sup.Env(root, kind='verify', commit=False)
        self.root = self.env.root
        self.env.subject.unlink()  # Replace the harness's non-ADR subject with a real constraint.
        (self.root / '.bionic.yml').write_text('config_version: "1"\ndocs_dir: docs\n')
        constraint = self.env.docs / 'adrs/ADR-0001-constraint.md'
        constraint.write_text('---\nid: ADR-0001\nstatus: Accepted\ngoverns:\n'
                              '  - handle: ADR-0001/fixture-rule\n    rule: Preserve output.\n---\n')
        (self.root / 'widget.txt').write_text('before\n')
        self.env.book.update(format_version='2', implementation_slots=[slot()])
        self.env.book['modules'] = {'adrs': 0, 'implementations': 0, 'verify': 1,
                                   'dev_loops': 1, 'review_cycles': 1}
        # New-format cycles have two final untagged prompts; no legacy prep slot.
        prep = self.env.book['prompts'].pop(0)
        self.env.book['prompts'].insert(len(self.env.book['prompts']) - 1, prep)
        for number, prompt in enumerate(self.env.book['prompts'], 1):
            prompt['n'] = number
        selected = decision()
        if partial:
            self.env.book['implementation_slots'][0]['scope'].append('other.txt')
            selected['scope'].append('other.txt')
        self.env.write_book_and_run(current=4)
        self.env.hash = approval.book_hash(self.env.book)
        self.run = self.env.load_run()
        self.run.update(format_version='2', book_content_hash=self.env.hash)
        self.save_run()
        self.path = self.env.run_dir / 'implementations/RUN-001/strategy/revision-001.yaml'
        self.path.parent.mkdir(parents=True)
        self.path.write_text(yaml.safe_dump(selected, sort_keys=False))
        self.rel = self.env.rel(self.path)
        self.original = self.path.read_bytes()
        sup.commit_all(self.root, 'synthetic draft and preimage')
        self.preimage = sup.git(self.root, 'rev-parse', 'HEAD').strip()
        (self.root / 'widget.txt').write_text('after\n')
        sup.commit_all(self.root, 'synthetic delivery')
        self.delivery = sup.git(self.root, 'rev-parse', 'HEAD').strip()
        self.retained = self.env.council / 'subjects/original.yaml'
        self.retained.parent.mkdir()
        self.retained.write_bytes(self.original)
        self.receipt = self.env.council / 'round.json'
        self.env.hash = self.run['book_content_hash']
        council = self.env.council_doc(module_tag='verify-1', prompt=2,
                    subjects=[{'path': self.rel, 'sha256': cr.sha256_bytes(self.original),
                               'retained_copy': self.env.rel(self.retained)}])
        history = []
        if fault in ('held', 'held-authorized', 'architectural', 'tie', 'earlier-subject', 'spent',
                     'authorized', 'wrong-exception', 'authorized-nonconverged'):
            count = 3 if fault in ('spent', 'authorized', 'wrong-exception', 'held-authorized',
                                   'authorized-nonconverged') else 1
            for number in range(1, count + 1):
                earlier = copy.deepcopy(council)
                earlier['round'] = number; earlier['written_at'] = sup.ts(number)
                if fault in ('spent', 'authorized', 'wrong-exception', 'authorized-nonconverged'):
                    earlier['seats'][0]['decision'] = 'REQUEST_CHANGES'
                    earlier['seats'][0]['findings'] = [{'id': 'openai_top:F1', 'dimension': 'Correctness',
                        'safety_adjacent': False, 'kind': 'blocking', 'text': 'synthetic blocker'}]
                if fault == 'held' or (fault == 'held-authorized' and number == 1):
                    earlier['seats'][0]['decision'] = 'DEFER_TO_HUMAN'
                if fault == 'architectural':
                    earlier['seats'][0]['decision'] = 'ARCHITECTURAL'
                path = self.env.council / f'earlier-{number}.json'
                path.write_text(json.dumps(earlier)); history.append(path)
            council['round'] = count + 1
            council['written_at'] = sup.ts(count + 1 if fault != 'tie' else 1)
        if fault in ('authorized', 'wrong-exception', 'held-authorized', 'authorized-nonconverged'):
            owner = self.env.council / 'owner.json'
            owner.write_text(json.dumps(self.env.owner_doc(kind='round-above-three',
                round=5 if fault == 'wrong-exception' else 4, module_tag='verify-1')))
            history.append(owner)
        if fault == 'earlier-subject':
            council['subjects'] = [{'path': 'widget.txt', 'sha256': self.env.sha('widget.txt'),
                                    'retained_copy': None}]
        if fault == 'numbering':
            council['round'] = 2
        if fault == 'authorized-nonconverged':
            council['seats'][0]['decision'] = 'REQUEST_CHANGES'
            council['seats'][0]['findings'] = [{'id': 'openai_top:F1', 'dimension': 'Correctness',
                'safety_adjacent': False, 'kind': 'blocking', 'text': 'synthetic unresolved blocker'}]
        self.receipt.write_text(json.dumps(council))
        self.context_path = self.env.council / 'close-context.json'
        self.registry_path = self.env.council / 'evaluated-registry.json'
        self.registry_path.write_bytes(sup.REGISTRY_PATH.read_bytes())
        self.contract_path = self.env.council / 'evaluated-contract.txt'
        self.contract_path.write_bytes(approval.gate_contract_bytes('2'))
        self.context = {'record_type': 'implementation-gate-context', 'format_version': '1',
                        # A freshly authored synthetic policy-2 fixture, never
                        # a conversion of issued or archived policy-1 evidence.
                        'contract': {'version': '2', **self.ref(self.contract_path)},
                        'registry': self.ref(self.registry_path),
                        'records': sorted([self.ref(p) for p in history + [self.receipt]], key=lambda x:x['path']),
                        'artifacts': [self.env.rel(p) for p in history + [self.receipt]]}
        if fault == 'unsupported-context':
            self.context['contract']['version'] = 'unrecognized'
        if fault == 'unattached':
            self.context['artifacts'] = []
        self.context_path.write_text(json.dumps(self.context))
        self.binding = {'slot': 'verify-1', 'book_id': self.run['book_id'], 'run_id': self.run['run_id'],
                        'book_content_hash': self.run['book_content_hash'], 'revision': self.ref(self.path),
                        'gate_prompt': 4, 'deciding_record': self.ref(self.receipt),
                        'retained_subject': self.ref(self.retained), 'context': self.ref(self.context_path)}
        self.run['implementation_bindings'] = [self.binding]
        self.run['prompts'][3].update(state='done', completed='2026-10-01T00:00:00Z',
                                     artifacts=self.context['artifacts'])
        self.save_run()
        sup.commit_all(self.root, 'synthetic successful close fixture, not runner evidence')
        self.report = self.env.run_dir / 'reviews/independent.json'
        self.report.parent.mkdir()
        report = sup.fixture('reviewer-report-paths.json')
        report.update(book={'id': self.run['book_id'], 'content_hash': self.run['book_content_hash']},
                      run_id=self.run['run_id'], subject={'form': 'paths',
                         'paths': [self.ref(self.path), self.ref(self.root / 'widget.txt')]})
        self.report.write_text(json.dumps(report))
        sup.commit_all(self.root, 'synthetic independent report fixture')
        self.result = {'record_type': 'implementation-result', 'format_version': '1',
                       'decision': self.ref(self.path), 'delivery_state': 'complete',
                       'source_revision': self.delivery, 'preimage_revision': self.preimage,
                       'scope': ['widget.txt'], 'sources': [{'path': 'widget.txt',
                         'preimage': ids.source_hash(self.root, self.preimage, 'widget.txt'),
                         'delivered': ids.source_hash(self.root, self.delivery, 'widget.txt')}],
                       'reviews': [self.ref(self.report)], 'annotations': []}
        if partial:
            self.result['delivery_state'] = 'partial'

    def ref(self, path):
        return {'path': self.env.rel(path), 'sha256': cr.sha256_file(path)}

    def save_run(self):
        self.env.run_path.write_text(yaml.safe_dump(self.run, sort_keys=False))

    def rebind_context(self):
        self.context_path.write_text(json.dumps(self.context))
        self.binding['context'] = self.ref(self.context_path)
        self.save_run()
        sup.commit_all(self.root, 'synthetic changed fixture')

    def proof(self):
        return approval.validate_implementation_binding(self.root, self.env.run_path, slot='verify-1',
                    revision_path=self.rel, revision_sha256=cr.sha256_file(self.path))


class ApprovalAndResults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(Path(self.tmp.name) / 'repo')

    def test_valid_committed_full_history_consumes_proof_and_writes_result(self):
        proof = self.f.proof()
        self.assertEqual(proof.slot, slot())
        path = ids.write_result(self.f.root, self.f.path, self.f.result)
        self.assertTrue(path.is_file())
        self.assertEqual(self.f.path.read_bytes(), self.f.original)
        self.assertEqual(self.f.env.load_run(), self.f.run)

    def test_raw_receipt_and_forged_done_without_binding_refuse_without_writes(self):
        self.f.run.pop('implementation_bindings')
        self.f.save_run()
        sup.commit_all(self.f.root, 'synthetic missing binding')
        with self.assertRaisesRegex(ids.Refused, 'approval-binding-missing'):
            ids.write_result(self.f.root, self.f.path, self.f.result)
        self.assertFalse(list(self.f.path.parent.glob('result-*.yaml')))

    def test_material_changed_reasoning_cannot_reuse_review(self):
        doc = decision(); doc['approach'] = 'Use a different tree.'
        self.f.path.write_text(yaml.safe_dump(doc))
        sup.commit_all(self.f.root, 'synthetic changed reasoning')
        with self.assertRaisesRegex(ids.Refused, 'revision-not-approved'):
            ids.write_result(self.f.root, self.f.path, {**self.f.result, 'decision': self.f.ref(self.f.path)})

    def test_out_of_scope_delivery_refuses(self):
        changed = copy.deepcopy(self.f.result); changed['scope'] = ['outside.txt']
        with self.assertRaisesRegex(ids.Refused, 'result-scope-refused'):
            ids.write_result(self.f.root, self.f.path, changed)

    def test_missing_context_and_uncommitted_proof_refuse(self):
        self.f.context_path.unlink()
        with self.assertRaisesRegex(ids.Refused, 'evidence-not-committed'):
            self.f.proof()

    def test_registry_rotation_after_close_uses_retained_registry(self):
        with mock.patch.object(cg, 'load_registry', return_value=(None, 'rotated current registry')):
            self.assertEqual(self.f.proof().slot, slot())

    def test_query_separates_unimplemented_intent_from_source(self):
        answer = ids.query(self.f.root, self.f.path)
        self.assertTrue(answer['reviewed_intent']['approved'])
        self.assertEqual(answer['historical_delivery'], [])
        self.assertEqual(answer['current_state']['state'], 'UNOBSERVED')

    def test_historical_proof_and_source_facts_survive_current_constraint_ineligibility(self):
        ids.write_result(self.f.root, self.f.path, self.f.result)
        sup.commit_all(self.f.root, 'synthetic recorded delivery')
        constraint = self.f.env.docs / 'adrs/ADR-0001-constraint.md'
        receipt = self.f.receipt.read_bytes(); original = self.f.path.read_bytes()
        constraint.write_text(constraint.read_text().replace('status: Accepted', 'status: Superseded'))
        sup.commit_all(self.f.root, 'synthetic current constraint transition')
        answer = ids.query(self.f.root, self.f.path)
        self.assertTrue(answer['reviewed_intent']['approved'])
        self.assertEqual(answer['current_state']['state'], 'delivered')
        self.assertFalse(answer['current_eligibility']['eligible'])
        self.assertEqual(answer['current_eligibility']['limit'], 'constraint-not-live-accepted')
        self.assertEqual(self.f.receipt.read_bytes(), receipt)
        self.assertEqual(self.f.path.read_bytes(), original)
        before = sorted((p.name, p.read_bytes()) for p in self.f.path.parent.iterdir() if p.is_file())
        with self.assertRaisesRegex(ids.Refused, 'constraint-not-live-accepted'):
            ids.write_result(self.f.root, self.f.path, self.f.result)
        self.assertEqual(before, sorted((p.name, p.read_bytes()) for p in self.f.path.parent.iterdir() if p.is_file()))

    def test_historical_consumer_requires_full_proof_and_accepts_no_context_bypass(self):
        arguments = dict(slot='verify-1', revision_path=self.f.rel, revision_sha256=cr.sha256_file(self.f.path))
        proof = approval.validate_historical_implementation_binding(self.f.root, self.f.env.run_path, **arguments)
        self.assertEqual(proof.slot, slot())
        with self.assertRaises(TypeError):
            approval.validate_historical_implementation_binding(self.f.root, self.f.env.run_path,
                                                                 **arguments, registry=sup.registry())
        with mock.patch.object(approval, 'live_constraints', side_effect=AssertionError('must not recurse')):
            self.assertEqual(approval.validate_historical_implementation_binding(
                self.f.root, self.f.env.run_path, **arguments).binding, self.f.binding)
        self.f.context_path.unlink()
        with self.assertRaisesRegex(ids.Refused, 'evidence-not-committed'):
            approval.validate_historical_implementation_binding(self.f.root, self.f.env.run_path, **arguments)

    def governing_pair(self, second_scope):
        """ADR-0001 governs widget.txt functionally; ADR-0002 governs `second_scope` algorithmically."""
        constraint = self.f.env.docs / 'adrs/ADR-0001-constraint.md'
        constraint.write_text('---\nid: ADR-0001\nstatus: Accepted\ngoverns:\n'
                              '  - handle: ADR-0001/fixture-rule\n    rule: Preserve output.\n'
                              '    scope: widget.txt\n---\n')
        (constraint.parent / 'ADR-0002-algorithm.md').write_text(
            '---\nid: ADR-0002\nstatus: Accepted\ngoverns:\n'
            '  - handle: ADR-0002/no-table-lookup\n    rule: Never implement the widget with a table.\n'
            f'    scope: {second_scope}\n---\n')
        sup.commit_all(self.f.root, 'synthetic overlapping governing constraints')

    def test_undeclared_overlapping_governing_constraint_is_not_eligible(self):
        self.governing_pair('widget.txt')
        before = sorted((p.name, p.read_bytes()) for p in self.f.path.parent.iterdir() if p.is_file())
        eligibility = ids.query(self.f.root, self.f.path)['current_eligibility']
        self.assertFalse(eligibility['eligible'])
        self.assertEqual(eligibility['limit'], 'undeclared-governing-constraint')
        self.assertEqual(eligibility['undeclared_refs'], ['ADR-0002/no-table-lookup'])
        self.assertEqual(eligibility['constraint_refs'], ['ADR-0001/fixture-rule'])
        self.assertEqual(before, sorted((p.name, p.read_bytes()) for p in self.f.path.parent.iterdir() if p.is_file()))

    def test_disjoint_or_declared_governing_constraint_stays_eligible(self):
        self.governing_pair('elsewhere/')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'],
                         {'eligible': True, 'limit': None, 'constraint_refs': ['ADR-0001/fixture-rule']})
        self.governing_pair('widget.txt')
        doc = decision(); doc['constraint_refs'] = ['ADR-0001/fixture-rule', 'ADR-0002/no-table-lookup']
        self.f.path.write_text(yaml.safe_dump(doc, sort_keys=False))
        sup.commit_all(self.f.root, 'synthetic revision declaring both governing constraints')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'],
                         {'eligible': True, 'limit': None, 'constraint_refs': doc['constraint_refs']})

    def revise_scope(self, scope):
        doc = decision(); doc['scope'] = scope
        self.f.path.write_text(yaml.safe_dump(doc, sort_keys=False))
        sup.commit_all(self.f.root, 'synthetic revision with an alternate scope spelling')

    def test_absolute_scope_entry_is_normalized_before_overlap(self):
        self.governing_pair('widget.txt')
        self.revise_scope([str(self.f.root / 'widget.txt')])
        eligibility = ids.query(self.f.root, self.f.path)['current_eligibility']
        self.assertFalse(eligibility['eligible'])
        self.assertEqual(eligibility['limit'], 'undeclared-governing-constraint')
        self.assertEqual(eligibility['undeclared_refs'], ['ADR-0002/no-table-lookup'])

    def symlinked_scope(self, links, scope='alias.txt'):
        """Commit each `name -> target` link and revise the decision scope to name only `scope`."""
        for name, target in links:
            try:
                (self.f.root / name).symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f'platform cannot create a symlink: {exc}')
        self.revise_scope([scope])

    def assert_path_refused(self):
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'],
                         {'eligible': False, 'limit': 'path-refused', 'constraint_refs': ['ADR-0001/fixture-rule']})

    def test_symlinked_scope_entry_is_path_refused(self):
        self.governing_pair('widget.txt')
        self.symlinked_scope([('alias.txt', 'widget.txt')])
        self.assert_path_refused()

    def test_symlinked_scope_entry_whose_target_leaves_the_repo_is_path_refused(self):
        self.governing_pair('widget.txt')
        outside = Path(self.tmp.name) / 'outside.txt'
        outside.write_text('outside\n')
        self.symlinked_scope([('alias.txt', outside)])
        self.assert_path_refused()

    def test_symlink_chain_in_scope_is_path_refused(self):
        self.governing_pair('mid.txt')
        self.symlinked_scope([('mid.txt', 'widget.txt'), ('alias.txt', 'mid.txt')])
        self.assert_path_refused()

    def test_symlink_loop_in_scope_is_path_refused(self):
        self.governing_pair('b.txt')
        self.symlinked_scope([('a.txt', 'b.txt'), ('b.txt', 'a.txt')], scope='a.txt')
        self.assert_path_refused()

    def test_dangling_symlink_in_scope_is_path_refused(self):
        self.governing_pair('elsewhere/')
        self.symlinked_scope([('alias.txt', 'missing.txt')])
        self.assert_path_refused()

    def test_regular_scope_entry_overlapping_nothing_stays_eligible(self):
        self.governing_pair('elsewhere/')
        (self.f.root / 'other.txt').write_text('other\n')
        self.revise_scope(['other.txt'])
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'],
                         {'eligible': True, 'limit': None, 'constraint_refs': ['ADR-0001/fixture-rule']})

    def test_unscoped_governing_rule_is_disclosed_unchecked_without_changing_eligibility(self):
        self.governing_pair('the widget algorithm')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'],
                         {'eligible': True, 'limit': None, 'constraint_refs': ['ADR-0001/fixture-rule'],
                          'unscoped_unchecked_refs': ['ADR-0002/no-table-lookup']})
        doc = decision(); doc['constraint_refs'] = ['ADR-0001/fixture-rule', 'ADR-0002/no-table-lookup']
        self.f.path.write_text(yaml.safe_dump(doc, sort_keys=False))
        sup.commit_all(self.f.root, 'synthetic revision declaring the unscoped rule')
        self.assertNotIn('unscoped_unchecked_refs', ids.query(self.f.root, self.f.path)['current_eligibility'])

    def test_retired_or_superseded_overlapping_rule_leaves_eligibility(self):
        expected = {'eligible': True, 'limit': None, 'constraint_refs': ['ADR-0001/fixture-rule']}
        self.governing_pair('widget.txt')
        (self.f.env.docs / 'adrs/ADR-0003-replacement.md').write_text(
            '---\nid: ADR-0003\nstatus: Accepted\ngoverns:\n'
            '  - handle: ADR-0003/replacement\n    rule: Replace the table rule.\n'
            '    scope: elsewhere/\n    retires: [ADR-0002/no-table-lookup]\n---\n')
        sup.commit_all(self.f.root, 'synthetic retirement of the overlapping rule')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'], expected)
        (self.f.env.docs / 'adrs/ADR-0003-replacement.md').unlink()
        superseded = self.f.env.docs / 'adrs/ADR-0002-algorithm.md'
        superseded.write_text(superseded.read_text().replace('status: Accepted', 'status: Superseded'))
        sup.commit_all(self.f.root, 'synthetic supersession of the overlapping rule')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'], expected)

    def replacement_over_widget(self):
        """ADR-0003/replacement is live, governs widget.txt, and retires ADR-0002/no-table-lookup."""
        self.governing_pair('widget.txt')
        (self.f.env.docs / 'adrs/ADR-0003-replacement.md').write_text(
            '---\nid: ADR-0003\nstatus: Accepted\ngoverns:\n'
            '  - handle: ADR-0003/replacement\n    rule: Replace the table rule.\n'
            '    scope: widget.txt\n    retires: [ADR-0002/no-table-lookup]\n---\n')
        sup.commit_all(self.f.root, 'synthetic live replacement rule over the scope')

    def test_live_overlapping_replacement_rule_is_an_undeclared_governing_constraint(self):
        # A governs entry that carries `retires` is itself a live rule; only its targets retire.
        self.replacement_over_widget()
        eligibility = ids.query(self.f.root, self.f.path)['current_eligibility']
        self.assertFalse(eligibility['eligible'])
        self.assertEqual(eligibility['limit'], 'undeclared-governing-constraint')
        self.assertEqual(eligibility['undeclared_refs'], ['ADR-0003/replacement'])

    def test_live_replacement_rule_is_declarable_and_its_target_is_not(self):
        self.replacement_over_widget()
        view = approval.live_constraints(self.f.root, ['ADR-0001/fixture-rule', 'ADR-0003/replacement'])
        self.assertIsInstance(view, dict)
        with self.assertRaisesRegex(approval.Refused, 'constraint-not-live-accepted'):
            approval.live_constraints(self.f.root, ['ADR-0002/no-table-lookup'])
        doc = decision(); doc['constraint_refs'] = ['ADR-0001/fixture-rule', 'ADR-0003/replacement']
        self.f.path.write_text(yaml.safe_dump(doc, sort_keys=False))
        sup.commit_all(self.f.root, 'synthetic revision declaring the live replacement rule')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'],
                         {'eligible': True, 'limit': None,
                          'constraint_refs': ['ADR-0001/fixture-rule', 'ADR-0003/replacement']})

    def test_overlap_read_fault_maps_to_existing_refusal_limits(self):
        self.governing_pair('widget.txt')
        for fault, limit in ((OSError('unreadable'), 'constraint-evidence-refused'),
                             (yaml.YAMLError('malformed'), 'constraint-evidence-refused'),
                             (approval.Refused('constraint-record-refused'), 'constraint-record-refused')):
            with self.subTest(limit=limit, fault=type(fault).__name__), \
                 mock.patch('doctrine_projection.scope_path_tokens', side_effect=fault):
                self.assertEqual(ids.query(self.f.root, self.f.path)['current_eligibility'],
                                 {'eligible': False, 'limit': limit, 'constraint_refs': ['ADR-0001/fixture-rule']})

    def test_query_reads_the_governing_view_once(self):
        import implementation_migration as migration
        self.governing_pair('widget.txt')
        with mock.patch.object(migration, 'authority_view', wraps=migration.authority_view) as view:
            ids.query(self.f.root, self.f.path)
        self.assertEqual(view.call_count, 1)

    def test_malformed_current_constraint_is_a_separate_bounded_limit(self):
        ids.write_result(self.f.root, self.f.path, self.f.result)
        sup.commit_all(self.f.root, 'synthetic delivery')
        constraint = self.f.env.docs / 'adrs/ADR-0001-constraint.md'
        constraint.write_text('---\nid: ADR-0001\nstatus: Accepted\ngoverns: [\n---\n')
        sup.commit_all(self.f.root, 'synthetic malformed current constraint')
        answer = ids.query(self.f.root, self.f.path)
        self.assertTrue(answer['reviewed_intent']['approved'])
        self.assertEqual(answer['current_state']['state'], 'delivered')
        self.assertFalse(answer['current_eligibility']['eligible'])
        self.assertEqual(answer['current_eligibility']['limit'], 'constraint-evidence-refused')

    def test_current_parallel_checkout_does_not_erase_historical_delivery(self):
        ids.write_result(self.f.root, self.f.path, self.f.result)
        sup.commit_all(self.f.root, 'synthetic delivery record')
        sup.git(self.f.root, 'checkout', '--quiet', '-b', 'parallel', self.f.preimage)
        # Carry the committed reasoning/proof/results onto the parallel branch,
        # without the delivery commit. Original source objects remain observable.
        sup.git(self.f.root, 'cherry-pick', self.f.delivery + '..main')
        answer = ids.query(self.f.root, self.f.path)
        self.assertTrue(answer['reviewed_intent']['approved'])
        self.assertEqual(answer['historical_delivery'][0]['observed'], 'unrelated-lineage')
        self.assertNotIn('limit', answer['historical_delivery'][0])

    def test_query_delivered_then_reverted_then_diverged_then_dirty(self):
        ids.write_result(self.f.root, self.f.path, self.f.result)
        sup.commit_all(self.f.root, 'synthetic result')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_state']['state'], 'delivered')
        (self.f.root / 'widget.txt').write_text('before\n')
        sup.commit_all(self.f.root, 'synthetic reversion')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_state']['state'], 'reverted')
        (self.f.root / 'widget.txt').write_text('other\n')
        sup.commit_all(self.f.root, 'synthetic divergence')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_state']['state'], 'diverged')
        (self.f.root / 'widget.txt').write_text('dirty\n')
        self.assertEqual(ids.query(self.f.root, self.f.path)['current_state']['state'], 'UNOBSERVED')

    def test_reviewed_revision_cannot_be_overwritten(self):
        with self.assertRaisesRegex(ids.Refused, 'reviewed-revision-immutable'):
            ids.write_decision(self.f.root, self.f.path, decision())

    def test_annotation_preserves_original_and_requires_review_scope(self):
        annotation = {'record_type': 'implementation-annotation', 'format_version': '1',
                      'decision': self.f.ref(self.f.path), 'operation': 'correct-spelling',
                      'target': 'display_title', 'original_sha256': cr.sha256_bytes(b'Choose stategy'),
                      'correction': 'Choose strategy'}
        path = self.f.path.with_name('annotation-001.yaml')
        ids.write_annotation(self.f.root, self.f.path, annotation, path)
        sup.commit_all(self.f.root, 'synthetic editorial annotation')
        self.assertEqual(self.f.path.read_bytes(), self.f.original)
        with self.assertRaisesRegex(ids.Refused, 'annotation-review-scope-missing'):
            ids.write_result(self.f.root, self.f.path, self.f.result)
        result = {**self.f.result, 'annotations': [self.f.ref(path)]}
        with self.assertRaisesRegex(ids.Refused, 'independent-review-scope-missing'):
            ids.write_result(self.f.root, self.f.path, result)
        report = json.loads(self.f.report.read_text())
        report['subject']['paths'].append(self.f.ref(path))
        self.f.report.write_text(json.dumps(report))
        sup.commit_all(self.f.root, 'synthetic annotation review')
        result['reviews'] = [self.f.ref(self.f.report)]
        ids.write_result(self.f.root, self.f.path, result)
        sup.commit_all(self.f.root, 'synthetic annotated result')
        with self.assertRaisesRegex(ids.Refused, 'annotation-correction-reused'):
            ids.write_annotation(self.f.root, self.f.path, annotation, path.with_name('annotation-002.yaml'))
        answer = ids.query(self.f.root, self.f.path)
        self.assertEqual(answer['reviewed_intent']['original']['display_title'], 'Choose stategy')
        self.assertEqual(answer['reviewed_intent']['annotations'][0]['annotation']['correction'], 'Choose strategy')

    def test_partial_result_does_not_claim_complete(self):
        f = Fixture(Path(self.tmp.name) / 'partial', partial=True)
        ids.write_result(f.root, f.path, f.result)
        sup.commit_all(f.root, 'synthetic partial result')
        answer = ids.query(f.root, f.path)
        self.assertEqual(answer['current_state']['delivery_state'], 'partial')
        with self.assertRaisesRegex(ids.Refused, 'complete-scope-missing'):
            ids.write_result(f.root, f.path, {**f.result, 'delivery_state': 'complete'})

    def test_unimplemented_result_records_no_delivered_scope(self):
        result = {**self.f.result, 'delivery_state': 'unimplemented', 'scope': [], 'sources': []}
        ids.write_result(self.f.root, self.f.path, result)
        sup.commit_all(self.f.root, 'synthetic unimplemented result')
        answer = ids.query(self.f.root, self.f.path)
        self.assertEqual(answer['historical_delivery'][0]['observed'], 'unimplemented')
        self.assertEqual(answer['current_state']['state'], 'UNOBSERVED')

    def test_parallel_revision_remains_historical(self):
        ids.write_result(self.f.root, self.f.path, self.f.result)
        sup.commit_all(self.f.root, 'synthetic result')
        answer = ids.query(self.f.root, self.f.path, self.f.preimage)
        self.assertEqual(answer['historical_delivery'][0]['observed'], 'unrelated-lineage')
        self.assertEqual(answer['current_state']['state'], 'UNOBSERVED')
        sup.git(self.f.root, 'checkout', '--quiet', '-b', 'parallel', self.f.preimage)
        (self.f.root / 'widget.txt').write_text('parallel choice\n')
        sup.commit_all(self.f.root, 'synthetic independent branch')
        sup.git(self.f.root, 'checkout', '--quiet', 'main')
        answer = ids.query(self.f.root, self.f.path, 'parallel')
        self.assertEqual(answer['historical_delivery'][0]['observed'], 'unrelated-lineage')
        self.assertEqual(answer['current_state']['observed_revision'], sup.git(self.f.root, 'rev-parse', 'parallel').strip())

    def test_book_archive_and_same_slug_in_another_run(self):
        archive = self.f.env.book_path.parent.parent / 'archive'
        archive.mkdir()
        self.f.env.book_path.rename(archive / self.f.env.book_path.name)
        sup.commit_all(self.f.root, 'synthetic book archive')
        self.assertEqual(self.f.proof().slot, slot())
        new_run = copy.deepcopy(self.f.run); new_run['run_id'] = 'RUN-002'; new_run.pop('implementation_bindings')
        new_run_path = self.f.env.run_dir / 'run-RUN-002.yaml'
        new_run_path.write_text(yaml.safe_dump(new_run))
        new_path = self.f.env.run_dir / 'implementations/RUN-002/strategy/revision-001.yaml'
        ids.write_decision(self.f.root, new_path, decision('RUN-002'))
        self.assertNotEqual(ids.validate_decision(self.f.root, new_path)['identity'],
                            ids.validate_decision(self.f.root, self.f.path)['identity'])

    def test_redirect_environment_does_not_change_proof(self):
        with mock.patch.dict(os.environ, {'GIT_DIR': '/no/such/repository', 'GIT_INDEX_FILE': '/missing',
                                         'GIT_WORK_TREE': '/missing', 'GIT_GRAFT_FILE': '/missing'}):
            self.assertEqual(self.f.proof().slot, slot())

    def test_cli_validate_result_and_query(self):
        evidence = self.f.root / 'candidate.yaml'
        evidence.write_text(yaml.safe_dump(self.f.result))
        script = sup.SCRIPTS / 'implementation-decisions.py'
        for arguments in (['validate', str(self.f.path)],
                          ['result', '--decision', str(self.f.path), '--evidence', str(evidence)]):
            response = subprocess.run([sys.executable, str(script), *arguments, '--repo-root', str(self.f.root)],
                                      capture_output=True, env=sup.scrubbed_env())
            self.assertEqual(response.returncode, 0, response.stdout.decode() + response.stderr.decode())
        sup.commit_all(self.f.root, 'synthetic CLI result')
        response = subprocess.run([sys.executable, str(script), 'query', '--decision', str(self.f.path),
                                   '--repo-root', str(self.f.root), '--revision', 'HEAD'], capture_output=True)
        self.assertEqual(json.loads(response.stdout)['current_state']['state'], 'delivered')

    def test_replace_refs_and_grafts_do_not_fabricate_source_or_ancestry(self):
        sup.git(self.f.root, 'replace', self.f.preimage, self.f.delivery)
        self.assertEqual(ids.source_hash(self.f.root, self.f.preimage, 'widget.txt'),
                         self.f.result['sources'][0]['preimage'])
        graft = self.f.root / '.git/info/grafts'
        graft.write_text(self.f.delivery + '\n')
        self.assertEqual(self.f.proof().slot, slot())
        self.assertTrue(ids.write_result(self.f.root, self.f.path, self.f.result).exists())

    def test_shallow_result_refuses_and_query_does_not_guess_or_fetch(self):
        clone = Path(self.tmp.name) / 'shallow'
        sup.git(self.f.root, 'clone', '--depth', '1', '--quiet', self.f.root.as_uri(), str(clone))
        path = clone / self.f.rel
        before = list(path.parent.iterdir())
        with self.assertRaisesRegex(ids.Refused, 'history-unavailable'):
            ids.write_result(clone, path, self.f.result)
        self.assertEqual(list(path.parent.iterdir()), before)
        answer = ids.query(clone, path)
        self.assertEqual(answer['current_state']['state'], 'UNOBSERVED')
        self.assertEqual(answer['current_state']['limit'], 'history-unavailable')
        self.assertEqual(sup.git(clone, 'rev-parse', '--is-shallow-repository').strip(), 'true')

    def test_missing_and_deleted_source_has_explicit_absence(self):
        self.assertEqual(ids.source_hash(self.f.root, self.f.delivery, 'absent.txt'), 'absent')
        (self.f.root / 'widget.txt').unlink()
        sup.commit_all(self.f.root, 'synthetic deletion')
        self.assertEqual(ids.source_hash(self.f.root, 'HEAD', 'widget.txt'), 'absent')
        with self.assertRaisesRegex(ids.Refused, 'delivered-source-dirty'):
            ids.write_result(self.f.root, self.f.path, self.f.result)

    def test_results_append_and_refuse_overwrite(self):
        first = ids.write_result(self.f.root, self.f.path, self.f.result)
        second = ids.write_result(self.f.root, self.f.path, self.f.result)
        self.assertNotEqual(first, second)
        original = first.read_bytes()
        with self.assertRaisesRegex(ids.Refused, 'immutable-artifact-exists'):
            ids.write_result(self.f.root, self.f.path, self.f.result, first)
        self.assertEqual(first.read_bytes(), original)

    def test_unreviewed_draft_changes_but_wrong_storage_refuses(self):
        path = self.f.path.with_name('revision-002.yaml')
        doc = {**decision(), 'revision': 2}
        ids.write_decision(self.f.root, path, doc)
        changed = {**doc, 'approach': 'A different draft.'}
        ids.write_decision(self.f.root, path, changed)
        self.assertEqual(ids.load(path)['approach'], 'A different draft.')
        with self.assertRaisesRegex(ids.Refused, 'decision-layout-refused'):
            ids.write_decision(self.f.root, self.f.root / 'other.yaml', doc)

    def test_annotation_semantic_target_and_original_digest_refuse(self):
        doc = {'record_type': 'implementation-annotation', 'format_version': '1',
               'decision': self.f.ref(self.f.path), 'operation': 'correct-spelling',
               'target': 'reasoning', 'original_sha256': cr.sha256_bytes(b'Choose stategy'), 'correction': 'changed'}
        with self.assertRaisesRegex(ids.Refused, 'annotation-schema-refused'):
            ids.write_annotation(self.f.root, self.f.path, doc, self.f.path.with_name('annotation-001.yaml'))
        doc.update(target='display_title', original_sha256='0' * 64)
        with self.assertRaisesRegex(ids.Refused, 'annotation-original-mismatch'):
            ids.write_annotation(self.f.root, self.f.path, doc, self.f.path.with_name('annotation-001.yaml'))

    def test_foreign_binding_and_changed_committed_binding_refuse(self):
        self.f.binding['run_id'] = 'RUN-002'; self.f.save_run()
        sup.commit_all(self.f.root, 'synthetic foreign binding')
        with self.assertRaisesRegex(ids.Refused, 'binding-identity-refused'):
            self.f.proof()
        self.f.binding['run_id'] = 'RUN-001'; self.f.save_run()
        sup.commit_all(self.f.root, 'synthetic edited binding')
        with self.assertRaisesRegex(ids.Refused, 'binding-history-changed'):
            self.f.proof()

    def test_declared_scope_and_constraints_cannot_be_changed_without_hash_change(self):
        book_path = self.f.env.book_path
        book = yaml.safe_load(book_path.read_bytes()); book['implementation_slots'][0]['scope'].append('outside.txt')
        book_path.write_text(yaml.safe_dump(book)); sup.commit_all(self.f.root, 'synthetic changed frozen slot')
        with self.assertRaisesRegex(ids.Refused, 'book-binding-refused'):
            self.f.proof()

    def test_proposed_and_retired_and_duplicate_constraints_refuse(self):
        constraint = self.f.env.docs / 'adrs/ADR-0001-constraint.md'
        original = constraint.read_text()
        constraint.write_text(original.replace('status: Accepted', 'status: Proposed'))
        sup.commit_all(self.f.root, 'synthetic proposed host')
        with self.assertRaisesRegex(ids.Refused, 'constraint-not-live-accepted'):
            self.f.proof()
        constraint.write_text(original)
        replacement = constraint.with_name('ADR-0002-replacement.md')
        replacement.write_text('---\nid: ADR-0002\nstatus: Accepted\ngoverns:\n'
                               '  - handle: ADR-0002/replacement\n    retires: [ADR-0001/fixture-rule]\n---\n')
        sup.commit_all(self.f.root, 'synthetic retirement')
        with self.assertRaisesRegex(ids.Refused, 'constraint-not-live-accepted'):
            self.f.proof()
        replacement.unlink()
        (constraint.parent / 'ADR-0001-duplicate.md').write_text(original)
        sup.commit_all(self.f.root, 'synthetic duplicate identity')
        with self.assertRaisesRegex(ids.Refused, 'constraint-identity-refused'):
            self.f.proof()

    def test_mandatory_retained_subject_and_inventory_tampering_refuse(self):
        original = self.f.retained.read_bytes()
        self.f.retained.unlink()
        with self.assertRaisesRegex(ids.Refused, 'evidence-not-committed'):
            self.f.proof()
        self.f.retained.write_bytes(original)
        receipt = json.loads(self.f.receipt.read_text()); receipt['round'] = 2
        self.f.receipt.write_text(json.dumps(receipt))
        sup.commit_all(self.f.root, 'synthetic changed evidence')
        with self.assertRaisesRegex(ids.Refused, 'history-inventory-mismatch'):
            self.f.proof()

    def test_caller_cannot_supply_historical_registry_override(self):
        with self.assertRaises(TypeError):
            approval.validate_implementation_binding(self.f.root, self.f.env.run_path, slot='verify-1',
                revision_path=self.f.rel, revision_sha256=cr.sha256_file(self.f.path), registry=sup.registry())

    def test_policy_refactor_outside_versioned_contract_preserves_history(self):
        with mock.patch.object(cg, 'classify', side_effect=RuntimeError('future live routing refactor')):
            self.assertEqual(self.f.proof().slot, slot())

    def test_missing_exact_contract_and_registry_bytes_refuse(self):
        self.f.contract_path.unlink()
        with self.assertRaisesRegex(ids.Refused, 'evidence-not-committed'):
            self.f.proof()


class HistoryControls(unittest.TestCase):
    def test_full_history_negative_controls_and_authorized_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            for fault, code in [('held', 'historical-gate-stop'), ('architectural', 'historical-gate-stop'),
                ('held-authorized', 'historical-gate-stop'), ('authorized-nonconverged', 'historical-gate-stop'),
                ('spent', 'historical-gate-stop'), ('wrong-exception', 'historical-gate-stop'),
                ('tie', 'historical-gate-stop'), ('numbering', 'historical-gate-stop'),
                ('earlier-subject', 'deciding-revision-missing'),
                ('unsupported-context', 'context-version-unsupported'), ('unattached', 'deciding-not-attached')]:
                with self.subTest(fault=fault):
                    f = Fixture(Path(tmp) / fault, fault=fault)
                    before = {p:f.root.joinpath(p).read_bytes() for p in
                              (f.env.rel(f.env.run_path), f.rel, f.env.rel(f.context_path))}
                    with self.assertRaisesRegex(ids.Refused, code):
                        ids.write_result(f.root, f.path, f.result)
                    self.assertEqual(before, {p:f.root.joinpath(p).read_bytes() for p in before})
                    self.assertFalse(list(f.path.parent.glob('result-*.yaml')))
            f = Fixture(Path(tmp) / 'authorized', fault='authorized')
            self.assertEqual(f.proof().slot, slot())
            self.assertTrue(ids.write_result(f.root, f.path, f.result).exists())


class ReviewBoundaryControls(unittest.TestCase):
    """Regression controls for the first internal-review batch, not product proof."""

    setUp = ApprovalAndResults.setUp

    def draft(self, revision=2):
        doc = decision()
        doc['revision'] = revision
        path = self.f.path.with_name(f'revision-{revision:03d}.yaml')
        ids.write_decision(self.f.root, path, doc)
        return path, doc

    def annotation(self, path):
        return {'record_type': 'implementation-annotation', 'format_version': '1',
                'decision': self.f.ref(path), 'operation': 'correct-spelling',
                'target': 'display_title', 'original_sha256': cr.sha256_bytes(b'Choose stategy'),
                'correction': 'Choose strategy'}

    def cli(self, *args):
        return subprocess.run([sys.executable, str(sup.SCRIPTS / 'implementation-decisions.py'),
                               *args, '--repo-root', str(self.f.root)],
                              capture_output=True, env=sup.scrubbed_env())

    def test_leaf_symlink_decision_api_and_cli_refuse_before_read(self):
        outside = Path(self.tmp.name) / 'outside.yaml'
        outside.write_bytes(self.f.original)
        self.f.path.unlink()
        self.f.path.symlink_to(outside)
        for operation in (ids.validate_decision, ids.query):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ids.Refused, 'record-symlink-refused'):
                    operation(self.f.root, self.f.path)
        for args in (('validate', str(self.f.path)), ('query', '--decision', str(self.f.path))):
            response = self.cli(*args)
            self.assertEqual(response.returncode, 1)
            self.assertEqual(json.loads(response.stdout), {'refused': 'record-symlink-refused'})
            self.assertEqual(response.stderr, b'')
        self.assertEqual(outside.read_bytes(), self.f.original)

    def test_leaf_symlink_owner_run_refuses_before_read(self):
        outside = Path(self.tmp.name) / 'outside-run.yaml'
        original = self.f.env.run_path.read_bytes()
        outside.write_bytes(original)
        self.f.env.run_path.unlink()
        self.f.env.run_path.symlink_to(outside)
        with self.assertRaisesRegex(ids.Refused, 'record-symlink-refused'):
            ids.validate_decision(self.f.root, self.f.path)
        response = self.cli('validate', str(self.f.path))
        self.assertEqual(response.returncode, 1)
        self.assertEqual(json.loads(response.stdout), {'refused': 'record-symlink-refused'})
        self.assertEqual(response.stderr, b'')
        self.assertEqual(outside.read_bytes(), original)

    def test_leaf_symlink_annotation_result_and_candidate_evidence_refuse(self):
        for kind, doc in (('annotation', self.annotation(self.f.path)), ('result', self.f.result)):
            with self.subTest(kind=kind):
                outside = Path(self.tmp.name) / f'outside-{kind}.yaml'
                outside.write_text(yaml.safe_dump(doc))
                path = self.f.path.with_name(f'{kind}-001.yaml')
                path.symlink_to(outside)
                with self.assertRaisesRegex(ids.Refused, 'record-symlink-refused'):
                    ids.query(self.f.root, self.f.path)
                self.assertEqual(yaml.safe_load(outside.read_bytes()), doc)
                path.unlink()
        candidate = self.f.root / 'candidate.yaml'
        candidate.symlink_to(outside)
        response = self.cli('result', '--decision', str(self.f.path), '--evidence', str(candidate))
        self.assertEqual(response.returncode, 1)
        self.assertEqual(json.loads(response.stdout), {'refused': 'record-symlink-refused'})
        self.assertFalse(list(self.f.path.parent.glob('result-*.yaml')))

    def test_preexisting_draft_temporary_survives_refused_write(self):
        path, doc = self.draft()
        temporary = path.with_name(path.name + '.draft-tmp')
        temporary.write_bytes(b'unowned bytes\n')
        original = path.read_bytes()
        with self.assertRaisesRegex(ids.Refused, 'immutable-artifact-exists'):
            ids.write_decision(self.f.root, path, {**doc, 'reasoning': 'Changed draft'})
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(temporary.exists())
        self.assertEqual(temporary.read_bytes(), b'unowned bytes\n')
        temporary.unlink()
        outside = Path(self.tmp.name) / 'unowned-temporary'
        outside.write_bytes(b'external unowned bytes\n')
        temporary.symlink_to(outside)
        with self.assertRaisesRegex(ids.Refused, 'immutable-artifact-exists'):
            ids.write_decision(self.f.root, path, doc)
        self.assertTrue(temporary.is_symlink())
        self.assertEqual(outside.read_bytes(), b'external unowned bytes\n')
        self.assertEqual(path.read_bytes(), original)

    def test_owned_temporary_is_cleaned_after_failed_or_successful_replace(self):
        path, doc = self.draft()
        original = path.read_bytes()
        temporary = path.with_name(path.name + '.draft-tmp')
        with mock.patch.object(ids.os, 'replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                ids.write_decision(self.f.root, path, {**doc, 'reasoning': 'Changed draft'})
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(temporary.exists())
        ids.write_decision(self.f.root, path, {**doc, 'reasoning': 'Changed draft'})
        self.assertEqual(yaml.safe_load(path.read_bytes())['reasoning'], 'Changed draft')
        self.assertFalse(temporary.exists())

    def test_new_revision_query_isolated_from_older_delivery_and_annotation(self):
        old_annotation = self.f.path.with_name('annotation-001.yaml')
        ids.write_annotation(self.f.root, self.f.path, self.annotation(self.f.path), old_annotation)
        report = json.loads(self.f.report.read_text())
        report['subject']['paths'].append(self.f.ref(old_annotation))
        self.f.report.write_text(json.dumps(report))
        sup.commit_all(self.f.root, 'synthetic annotated independent review')
        result = {**self.f.result, 'annotations': [self.f.ref(old_annotation)],
                  'reviews': [self.f.ref(self.f.report)]}
        old_result = ids.write_result(self.f.root, self.f.path, result)
        sup.commit_all(self.f.root, 'synthetic revision one result')
        retained = {p: p.read_bytes() for p in (self.f.path, old_result, old_annotation)}
        path, _ = self.draft()
        answer = ids.query(self.f.root, path)
        self.assertFalse(answer['reviewed_intent']['approved'])
        self.assertEqual(answer['reviewed_intent']['annotations'], [])
        self.assertEqual(answer['historical_delivery'], [])
        self.assertEqual(answer['current_state']['state'], 'UNOBSERVED')
        old = ids.query(self.f.root, self.f.path)
        self.assertTrue(old['reviewed_intent']['approved'])
        self.assertEqual(old['current_state']['state'], 'delivered')
        self.assertEqual(len(old['reviewed_intent']['annotations']), 1)
        # A synthetic unapproved result for revision two creates no approval or
        # source claim, and cannot replace revision one's historical delivery.
        proposed = {**self.f.result, 'decision': self.f.ref(path),
                    'delivery_state': 'unimplemented', 'scope': [], 'sources': []}
        new_result = path.with_name('result-002.yaml')
        new_result.write_text(yaml.safe_dump(proposed))
        sup.commit_all(self.f.root, 'synthetic unapproved revision two result control')
        newer = ids.query(self.f.root, path)
        self.assertFalse(newer['reviewed_intent']['approved'])
        self.assertEqual(newer['current_state']['state'], 'UNOBSERVED')
        self.assertEqual([r['path'] for r in newer['historical_delivery']], [self.f.env.rel(new_result)])
        older = ids.query(self.f.root, self.f.path)
        self.assertTrue(older['reviewed_intent']['approved'])
        self.assertEqual(older['current_state']['state'], 'delivered')
        self.assertEqual([r['path'] for r in older['historical_delivery']], [self.f.env.rel(old_result)])
        self.assertEqual(retained, {p: p.read_bytes() for p in retained})

    def test_annotation_inventory_and_correction_reuse_are_revision_scoped(self):
        other, _ = self.draft()
        unrelated = self.f.path.with_name('annotation-001.yaml')
        unrelated.write_text(yaml.safe_dump(self.annotation(other)))
        sup.commit_all(self.f.root, 'synthetic other revision annotation control')
        self.assertEqual(ids._annotation_inventory(self.f.root, self.f.path), [])
        current = self.f.path.with_name('annotation-002.yaml')
        ids.write_annotation(self.f.root, self.f.path, self.annotation(self.f.path), current)
        sup.commit_all(self.f.root, 'synthetic current revision annotation')
        self.assertEqual(ids._annotation_inventory(self.f.root, self.f.path), [self.f.ref(current)])
        self.assertEqual(len(ids.query(self.f.root, self.f.path)['reviewed_intent']['annotations']), 1)
        self.assertEqual(len(ids.query(self.f.root, other)['reviewed_intent']['annotations']), 1)
        with self.assertRaisesRegex(ids.Refused, 'annotation-correction-reused'):
            ids.write_annotation(self.f.root, self.f.path, self.annotation(self.f.path),
                                 current.with_name('annotation-003.yaml'))

    def test_same_revision_wrong_annotation_or_result_digest_is_not_filtered(self):
        for kind, doc in (('annotation', self.annotation(self.f.path)), ('result', copy.deepcopy(self.f.result))):
            with self.subTest(kind=kind):
                doc['decision']['sha256'] = 'a' * 64
                path = self.f.path.with_name(f'{kind}-001.yaml')
                path.write_text(yaml.safe_dump(doc))
                sup.commit_all(self.f.root, 'synthetic wrong revision digest')
                with self.assertRaisesRegex(ids.Refused, f'{kind}-decision-mismatch'):
                    ids.query(self.f.root, self.f.path)
                path.unlink()
                sup.commit_all(self.f.root, 'synthetic remove test control')

    def test_changed_semantic_constants_are_bound_before_evaluation(self):
        before = approval.gate_contract_bytes('2')
        for name, value in (('APPROVING', ()), ('SEAT_ROLES', ()), ('WITHHELD_PREFIX', 'other:')):
            with self.subTest(name=name), mock.patch.object(approval.supported_profile('2').kernel, name, value):
                self.assertNotEqual(cr.sha256_bytes(before), cr.sha256_bytes(approval.gate_contract_bytes('2')))
                with self.assertRaisesRegex(ids.Refused, 'context-contract-unsupported'):
                    self.f.proof()

    def test_called_helpers_classes_record_and_schema_policy_are_bound(self):
        before = approval.gate_contract_bytes('2')
        kernel = approval.supported_profile('2').kernel
        records = approval.supported_profile('2').records
        vp = kernel.schema_engine
        changes = ((kernel, '_removed_record_problem', kernel._uncommitted_problem),
                   (kernel, '_snapshot_named_problem', kernel._uncommitted_problem),
                   (kernel, 'Round', kernel.Refutation), (records, 'git', records.repo_root),
                   (vp, 'validate', vp._matches))
        for module, name, value in changes:
            with self.subTest(name=name), mock.patch.object(module, name, value):
                self.assertNotEqual(cr.sha256_bytes(before), cr.sha256_bytes(approval.gate_contract_bytes('2')))
        for name, value in (('RECORD_TYPES', ()), ('GIT_REDIRECT_VARS', ())):
            with self.subTest(name=name), mock.patch.object(records, name, value):
                self.assertNotEqual(cr.sha256_bytes(before), cr.sha256_bytes(approval.gate_contract_bytes('2')))
        with mock.patch.object(vp, 'SchemaLoadError', cr.RecordError):
            with self.assertRaisesRegex(ids.Refused, 'context-policy-unavailable'):
                approval.gate_contract_bytes('2')
        schema = kernel._SCHEMA_FILES['council-record']
        read = Path.read_bytes
        def altered(path):
            return read(path) + b'\n ' if path == schema else read(path)
        with mock.patch.object(Path, 'read_bytes', altered):
            self.assertNotEqual(cr.sha256_bytes(before), cr.sha256_bytes(approval.gate_contract_bytes('2')))
            with self.assertRaisesRegex(ids.Refused, 'context-contract-unsupported'):
                self.f.proof()

    def test_policy_manifest_includes_transitive_dependencies_and_excludes_live_routing(self):
        manifest = json.loads(approval.gate_contract_bytes('2'))
        policies = manifest['policy']
        for name in ('council-policy-2.APPROVING', 'council-policy-2.SEAT_ROLES', 'council-policy-2.Round',
                     'council-policy-2._removed_record_problem', 'council-policy-2._snapshot_named_problem',
                     'council-policy-2._subject_changes', 'council_records.git_isolated_env',
                     'council-policy-2._SCHEMA_FILES', 'schema-validator-2._JSON_TYPE_CHECKS'):
            self.assertIn(name, policies)
        self.assertNotIn('council-policy-2.classify', policies)
        self.assertNotIn('council-policy-2.load_registry', policies)

    def test_malformed_owner_mapping_cli_is_bounded_json(self):
        self.f.env.run_path.write_text('[]\n')
        for args in (('validate', str(self.f.path)), ('query', '--decision', str(self.f.path))):
            response = self.cli(*args)
            self.assertEqual(response.returncode, 1)
            self.assertEqual(json.loads(response.stdout), {'refused': 'owner-run-shape-refused'})
            self.assertEqual(response.stderr, b'')


ROOT = Path(__file__).resolve().parents[3]
vp = sup.vp

class SchemaContracts(unittest.TestCase):
    def errors(self, kind, doc):
        schema_root = Path(os.environ.get('CRUX_RECORD_SCHEMA_TEST_DIR', str(ROOT / 'crux/schemas')))
        schema = vp.load_schema(schema_root / f'implementation-{kind}.schema.json')
        errors = []
        vp.validate(doc, schema, '', '#', errors, kind)
        return errors

    def template(self, kind):
        text = (ROOT / f'crux/templates/implementation-{kind}-template.yaml').read_text()
        return yaml.safe_load(text.replace('ADR-NNNN/example-constraint', 'ADR-0001/example-constraint'))

    def test_templates_validate(self):
        for kind in ('decision', 'result'):
            with self.subTest(kind=kind):
                self.assertEqual(self.errors(kind, self.template(kind)), [])
        raw = yaml.safe_load((ROOT / 'crux/templates/implementation-decision-template.yaml').read_text())
        self.assertTrue(self.errors('decision', raw))

    def test_authority_and_lifecycle_fields_refuse(self):
        for kind in ('decision', 'result'):
            for field in ('governs', 'handle', 'status', 'supersedes', 'approval', 'current_state'):
                with self.subTest(kind=kind, field=field):
                    doc = self.template(kind)
                    doc[field] = 'forbidden'
                    self.assertTrue(self.errors(kind, doc))

    def annotation(self):
        return dict(record_type='implementation-annotation', format_version='1',
                    decision={'path': 'records/revision-001.yaml', 'sha256': 'a' * 64},
                    operation='correct-spelling', target='display_title',
                    original_sha256='b' * 64, correction='Choose strategy')

    def test_annotation_targets_and_operations_are_closed(self):
        doc = self.annotation()
        self.assertEqual(self.errors('annotation', doc), [])
        for target in ('reasoning', 'scope', 'source_labels.0.path', 'approach', 'display_title\n', 'source_labels.-1.label'):
            with self.subTest(target=target):
                self.assertTrue(self.errors('annotation', {**doc, 'target': target}))
        for operation in ('rewrite', 'replace-approach', 'correct-spelling\n'):
            with self.subTest(operation=operation):
                self.assertTrue(self.errors('annotation', {**doc, 'operation': operation}))
        doc['decision']['governs'] = []
        self.assertTrue(self.errors('annotation', doc))

    def test_source_hash_or_explicit_absence_only(self):
        doc = self.template('result')
        for value in ('absent', 'a' * 64):
            doc['sources'][0]['preimage'] = value
            self.assertEqual(self.errors('result', doc), [])
        for value in (None, '', 'unknown', 'a' * 63, 'a' * 64 + '\n'):
            with self.subTest(value=value):
                doc['sources'][0]['preimage'] = value
                self.assertTrue(self.errors('result', doc))

    def test_required_reasoning_and_identity_dimensions(self):
        doc = self.template('decision')
        for field in ('book_id', 'run_id', 'slug', 'revision', 'slot', 'scope', 'constraint_refs', 'reasoning', 'alternatives', 'approach', 'assumptions', 'intended_evidence', 'source_labels'):
            with self.subTest(field=field):
                changed = copy.deepcopy(doc)
                del changed[field]
                self.assertTrue(self.errors('decision', changed))
        for field, value in (('revision', 0), ('revision', True), ('run_id', 'RUN-001\n'), ('book_id', 'PB-0001\n')):
            with self.subTest(field=field, value=value):
                self.assertTrue(self.errors('decision', {**doc, field: value}))

    def test_module_and_patch_slots(self):
        doc = self.template('decision')
        for slot in ('implementation-1', 'verify-2', 'patch'):
            self.assertEqual(self.errors('decision', {**doc, 'slot': slot}), [])
        for slot in ('adr-1', 'implementation-0', 'patch-verify', 'verify-1\n'):
            self.assertTrue(self.errors('decision', {**doc, 'slot': slot}))

    def test_results_require_source_and_review_dimensions(self):
        doc = self.template('result')
        for field in ('decision', 'delivery_state', 'source_revision', 'preimage_revision', 'scope', 'sources', 'reviews', 'annotations'):
            changed = copy.deepcopy(doc)
            del changed[field]
            self.assertTrue(self.errors('result', changed))
        for state in ('complete', 'partial'):
            self.assertEqual(self.errors('result', {**doc, 'delivery_state': state}), [])
        for field, value in (('delivery_state', 'approved'), ('source_revision', 'HEAD'), ('source_revision', 'a' * 40 + '\n'), ('reviews', [])):
            self.assertTrue(self.errors('result', {**doc, field: value}))
        for field in ('decision',):
            changed = copy.deepcopy(doc)
            changed[field]['approval'] = True
            self.assertTrue(self.errors('result', changed))

    def test_unimplemented_requires_empty_scope_and_sources(self):
        doc = self.template('result')
        empty = {**doc, 'delivery_state': 'unimplemented', 'scope': [], 'sources': []}
        self.assertEqual(self.errors('result', empty), [])
        for field in ('scope', 'sources'):
            with self.subTest(field=field):
                changed = {**empty, field: doc[field]}
                self.assertTrue(self.errors('result', changed))
        for state in ('complete', 'partial'):
            for field in ('scope', 'sources'):
                with self.subTest(state=state, field=field):
                    self.assertTrue(self.errors('result', {**doc, 'delivery_state': state, field: []}))

    def test_qualified_architectural_constraint_identity(self):
        doc = self.template('decision')
        for handle in ('ADR-0001/example-constraint', 'DEMO-ADR-0001/example-constraint'):
            self.assertEqual(self.errors('decision', {**doc, 'constraint_refs': [handle]}), [])
        for handle in ('DEMO-OBS-0001/example-constraint', 'DEMO-PB-0001/example-constraint', 'ADR-ADR-0001/example-constraint', 'DEMO-ADR-0001/example-constraint\n'):
            self.assertTrue(self.errors('decision', {**doc, 'constraint_refs': [handle]}))



if __name__ == '__main__':
    unittest.main()
