"""Shared raw reads and pure layout buffers; no real approval or migration."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import yaml

import admission_source_io as source_io
import implementation_migration as migration
import _council_gate_support as sup


class RawReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.path = self.root / 'source.md'

    def test_raw_newlines_are_never_presentation_normalized(self):
        raw = 'é\r\n## Reason\rbody\n'.encode(); self.path.write_bytes(raw)
        text, rel = migration._read(self.root, self.path)
        self.assertEqual(text.encode(), raw); self.assertEqual(rel, 'source.md')
        self.assertEqual(migration._digest(text.encode()), migration._digest(raw))

    def test_exact_and_over_authored_and_generated_limits(self):
        for limit in (migration.MAX_SOURCE_BYTES, 4 * 1024 * 1024):
            self.path.write_bytes(b'x' * limit)
            self.assertEqual(len(migration._bounded_read(self.root, self.path, limit)[0]), limit)
            self.path.write_bytes(b'x' * (limit + 1))
            with self.assertRaisesRegex(migration.Refused, 'migration-source-oversize'):
                migration._bounded_read(self.root, self.path, limit)

    def test_revalidation_failure_refuses_and_closes_transport(self):
        self.path.write_text('canonical')
        instances = []; original = source_io.SourceIO.__init__
        def initialized(instance, *args, **kwargs):
            original(instance, *args, **kwargs); instances.append(instance)
        with mock.patch.object(source_io.SourceIO, '__init__', initialized), \
             mock.patch.object(source_io.SourceIO, 'revalidate', side_effect=source_io.SourceIORefusal):
            with self.assertRaisesRegex(migration.Refused, 'migration-source-unreadable'):
                migration._read(self.root, self.path)
        self.assertTrue(instances); self.assertTrue(all(io._fd is None for io in instances))

    def test_missing_invalid_encoding_and_symlinks_refuse(self):
        with self.assertRaises(migration.Refused): migration._read(self.root, self.path)
        self.path.write_bytes(b'\xff')
        with self.assertRaises(migration.Refused): migration._read(self.root, self.path)
        self.path.unlink(); self.path.symlink_to(self.root / 'other')
        with self.assertRaisesRegex(migration.Refused, 'migration-symlink-refused'):
            migration._read(self.root, self.path)


class LayoutBuffers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); (self.root / 'bionic').mkdir()

    def test_current_and_historical_buffers_need_no_scratch_files(self):
        forms = ('---\ndocs_dir: bionic\n', '%YAML 1.1\n---\ndocs_dir: bionic\n',
                 '{docs_dir: bionic}', '{}')
        for text in forms:
            with self.subTest(text=text), mock.patch.object(migration.tempfile, 'TemporaryDirectory',
                    side_effect=AssertionError('layout must parse captured buffers')):
                self.assertEqual(migration._discovery_directory(self.root, {'.bionic.yml': text}),
                                 'bionic/adrs/migrations')

    def test_no_git_layout_is_bound_to_actual_root_without_scratch(self):
        (self.root / '.bionic.yml').write_text('docs_dir: bionic\n')
        with mock.patch.object(migration.tempfile, 'TemporaryDirectory',
                side_effect=AssertionError('layout must parse captured buffers')):
            self.assertEqual(migration._no_git_layout(self.root), [self.root / 'bionic'])

    def test_canonical_selection_keeps_lazy_config_precedence(self):
        self.assertEqual(migration._discovery_directory(self.root,
            {'.bionic.yml': 'docs_dir: bionic\n', '.crux': 'malformed: [\n'}),
            'bionic/adrs/migrations')


class GuardedConfiguration(unittest.TestCase):
    def setUp(self):
        from test_implementation_migration import Foundation
        self.f = Foundation(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_actual_clause_reader_requires_guarded_configuration_capture(self):
        before = self.f.snapshot()
        with mock.patch.object(migration.bionic_config, '_collect_layout_inputs',
                side_effect=source_io.SourceIORefusal):
            with self.assertRaisesRegex(migration.Refused, 'migration-layout-refused'):
                migration.resolve_clause(self.f.root, self.f.batch['entries'][0])
        self.assertEqual(self.f.snapshot(), before)

    def test_actual_clause_reader_preserves_canonical_selection_and_strict_versions(self):
        (self.f.root / '.crux').write_text('malformed: [\n')
        ref = migration.resolve_clause(self.f.root, self.f.batch['entries'][0])
        self.assertEqual(ref['path'], 'knowledge/adrs/ADR-0110-source.md')
        for text in ('config_version: null\ndocs_dir: knowledge\n',
                     'config_version: "1"\ndocs_dir: ../outside\n'):
            (self.f.root / '.bionic.yml').write_text(text)
            with self.assertRaises(migration.bionic_config.BionicConfigError):
                migration.resolve_clause(self.f.root, self.f.batch['entries'][0])

    def test_internal_materialization_root_uses_canonical_containment(self):
        given = Path(self.f.temp.name) / 'repo'
        self.assertEqual(migration._configuration(given).docs_root, self.f.adrs.parent)


class GuardedAdditions(unittest.TestCase):
    def setUp(self):
        from test_implementation_migration_signed_additions import PublishedAdditions
        self.f = PublishedAdditions(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.observation()

    def test_actual_addition_view_binds_and_closes_canonical_admission_transport(self):
        import observation_admission as admission
        opened = []; bound = []; used = []
        initialize = admission._AdmissionIO.__init__; bind = admission._AdmissionIO.bind_tree
        source_problems = admission._source_problems
        def initialized(instance, *args, **kwargs):
            initialize(instance, *args, **kwargs); opened.append(instance)
        def bound_tree(instance, tree):
            bind(instance, tree); bound.append(tree)
        def checked(context, *args, **kwargs):
            self.assertIsInstance(context['source_io'], admission._AdmissionIO)
            self.assertEqual(context['source_io']._selection['tree'], self.f.root / 'docs')
            used.append(context['source_io'])
            return source_problems(context, *args, **kwargs)
        with mock.patch.object(admission._AdmissionIO, '__init__', initialized), \
             mock.patch.object(admission._AdmissionIO, 'bind_tree', bound_tree), \
             mock.patch.object(admission, '_source_problems', checked):
            self.assertEqual(self.f.view(), self.f.original)
        self.assertEqual(bound, [self.f.root / 'docs'] * len(opened))
        self.assertEqual(len(used), 1); self.assertIn(used[0], opened)
        self.assertTrue(opened); self.assertTrue(all(io._fd is None for io in opened))

    def test_canonical_addition_selection_revalidation_failure_refuses_without_writes(self):
        import observation_admission as admission
        with mock.patch.object(admission._AdmissionIO, 'bind_tree',
                side_effect=source_io.SourceIORefusal):
            with self.assertRaises(migration.Refused): self.f.view()


def writer_driver(name):
    import importlib
    return importlib.import_module('implementation_migration_apply')._driver(name)


class CitationKinds(unittest.TestCase):
    def setUp(self):
        import test_implementation_authority as fixtures
        self.fixtures = fixtures
        self.f = fixtures.PublicationProof(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root; self.path = self.root / 'CHANGELOG.md'

    def complete_governs(self):
        """Give the synthetic records the governs fields the production builders require."""
        (self.root / 'docs/adrs/ADR-0109-predecessor.md').write_text('---\n' + yaml.safe_dump(dict(
            id='ADR-0109', status='Accepted', governs=[dict(handle='ADR-0109/old-rotation',
                domain='decision-review', rule='The predecessor.', scope='Assessment', provenance='authored')])) + '---\n')
        for source in (self.root / 'docs/adrs').glob('ADR-*.md'):
            text = source.read_text(); fm = migration._yaml(migration.adr_frontmatter.frontmatter_block(text))
            for rule in fm.get('governs', []):
                rule.setdefault('provenance', 'authored'); rule.setdefault('domain', 'testing')
                rule.setdefault('scope', 'reader')
            source.write_text('---\n' + yaml.safe_dump(fm) + '---\n' + text.split('---', 2)[2].lstrip('\n'))

    def prepare(self, text):
        self.path.write_text(text)
        batch = migration.load_batch(self.root, self.f.f.path)
        batch['citation_dependencies'] = [{'path': 'CHANGELOG.md',
            'sha256': migration._digest(self.path.read_bytes())}]
        self.f.f.path.write_text(yaml.safe_dump(batch, sort_keys=False))
        self.fixtures.AuthorityView.refresh_reviewed_batch(self.f)
        return batch

    def test_citation_scope_and_body_share_canonical_admission_transport(self):
        import observation_admission as admission
        batch = self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        module = migration._reference_reader(); original = module.resolve_scope
        seen = []; reads = []
        read = admission._AdmissionIO.read_bytes
        def scoped(root, extra, *, _source_io=None):
            self.assertIsInstance(_source_io, admission._AdmissionIO)
            self.assertEqual(_source_io._selection['tree'], self.root / 'docs')
            seen.append(_source_io)
            return original(root, extra, _source_io=_source_io)
        def read_bytes(instance, path, **kwargs):
            if Path(path) == self.path: reads.append(instance)
            return read(instance, path, **kwargs)
        with mock.patch.object(module, 'resolve_scope', scoped), \
             mock.patch.object(admission._AdmissionIO, 'read_bytes', read_bytes):
            deps, _ = migration._citation_inspection(self.root, batch)
        self.assertEqual(deps, batch['citation_dependencies'])
        self.assertEqual(len(seen), 1); self.assertEqual(reads, seen)
        self.assertIsNone(seen[0]._fd)

    def test_citation_selection_revalidation_failure_refuses_before_scope(self):
        import observation_admission as admission
        batch = self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        before = self.f.snapshot()
        with mock.patch.object(admission._AdmissionIO, 'bind_tree',
                side_effect=source_io.SourceIORefusal):
            with self.assertRaises(migration.Refused):
                migration._citation_inspection(self.root, batch)
        self.assertEqual(self.f.snapshot(), before)

    def test_dated_reference_binds_exact_bytes_only_before_publication(self):
        batch = self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\n'
            'Earlier choices.[^old]\n\n[^old]: rule:rotation (ADR-0110/rotation).\n')
        self.f.close()
        candidate = migration._validated_inputs(self.root, self.f.proof)
        self.assertIn(batch['citation_dependencies'][0], candidate['dependency_fingerprints'])
        self.f.publish(); before = self.f.snapshot()
        view = migration.authority_view(self.root)
        self.assertEqual(view['state'], 'published')
        # A published reader binds no whole-file digest of a historical citation file.
        self.assertNotIn('CHANGELOG.md', [ref['path'] for ref in view['dependency_fingerprints']])
        self.assertEqual(view['historical_handles'], candidate['historical_handles'])
        self.assertEqual(self.f.snapshot(), before)

    def test_current_reference_refuses_even_with_complete_dependency(self):
        self.prepare('# Changelog\n\n## [Unreleased]\n\nFollow rule:rotation.\n')
        self.f.close(); self.f.publish(); before = self.f.snapshot()
        with self.assertRaisesRegex(migration.Refused, 'migration-citation-repair-required'):
            migration.authority_view(self.root)
        self.assertEqual(self.f.snapshot(), before)

    def test_dated_reference_never_waives_missing_dependency_before_publication(self):
        batch = self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        batch['citation_dependencies'] = []
        self.f.f.path.write_text(yaml.safe_dump(batch, sort_keys=False))
        self.fixtures.AuthorityView.refresh_reviewed_batch(self.f)
        self.f.close(); before = self.f.snapshot()
        with self.assertRaisesRegex(migration.Refused, 'migration-citation-dependency-drift'):
            migration._validated_inputs(self.root, self.f.proof)
        self.assertEqual(self.f.snapshot(), before)
        # After publication a new historical citation file is accepted; only bound paths stay required.
        self.f.publish()
        self.assertEqual(migration.authority_view(self.root)['state'], 'published')

    def test_published_view_survives_committed_historical_changelog_growth(self):
        text = '# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n'
        self.complete_governs()
        self.prepare(text)
        self.f.close(); self.f.publish()
        published = migration.authority_view(self.root)
        self.assertEqual(published['state'], 'published')
        summaries = writer_driver('summarize-adrs').build(self.root)
        doctrine = writer_driver('compile-doctrine').build(self.root)
        self.path.write_text('# Changelog\n\n## [3.11.0] — 2026-09-01\n\nA later release entry.\n'
                             + text[len('# Changelog\n'):] + '\nUnrelated dated explanation.\n')
        sup.commit_all(self.root, 'synthetic historical changelog growth after publication')
        before = self.f.snapshot()
        view = migration.authority_view(self.root)
        self.assertEqual(view, published)
        self.assertEqual(view['historical_handles'], published['historical_handles'])
        self.assertEqual(writer_driver('summarize-adrs').build(self.root), summaries)
        self.assertEqual(writer_driver('compile-doctrine').build(self.root), doctrine)
        self.assertEqual(self.f.snapshot(), before)

    def test_published_new_governing_citation_of_demoted_slug_still_refuses(self):
        text = '# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n'
        self.prepare(text)
        self.f.close(); self.f.publish()
        self.assertEqual(migration.authority_view(self.root)['state'], 'published')
        for committed in (False, True):
            with self.subTest(committed=committed):
                self.path.write_text('# Changelog\n\n## [Unreleased]\n\nFollow rule:rotation.\n'
                                     + text[len('# Changelog\n'):])
                if committed: sup.commit_all(self.root, 'synthetic governing citation after publication')
                before = self.f.snapshot()
                with self.assertRaisesRegex(migration.Refused, 'migration-citation-repair-required'):
                    migration.authority_view(self.root)
                self.assertEqual(self.f.snapshot(), before)

    def test_published_governing_citation_added_during_read_refuses(self):
        text = '# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n'
        self.prepare(text)
        self.f.close(); self.f.publish()
        original = migration._unchanged_inputs
        def raced(repo, fingerprints):
            original(repo, fingerprints)
            self.path.write_text('# Changelog\n\n## [Unreleased]\n\nFollow rule:rotation.\n'
                                 + text[len('# Changelog\n'):])
        with mock.patch.object(migration, '_unchanged_inputs', raced), \
             self.assertRaisesRegex(migration.Refused, 'migration-citation-repair-required'):
            migration.authority_view(self.root)

    def test_published_deleted_cited_file_refuses(self):
        self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        self.f.close(); self.f.publish()
        self.assertEqual(migration.authority_view(self.root)['state'], 'published')
        self.path.unlink()
        sup.commit_all(self.root, 'synthetic cited file deletion after publication')
        before = self.f.snapshot()
        with self.assertRaisesRegex(migration.Refused, 'migration-citation-roster-drift'):
            migration.authority_view(self.root)
        self.assertEqual(self.f.snapshot(), before)

    BRIEF = ('# Draft brief\n\nEarlier reasoning held the old rotation.[^old]\n\n'
             '[^old]: rule:rotation\n')

    def test_published_view_accepts_a_new_brief_citing_a_demoted_slug(self):
        text = '# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n'
        self.complete_governs()
        self.prepare(text)
        self.f.close(); self.f.publish()
        published = migration.authority_view(self.root)
        summaries = writer_driver('summarize-adrs').build(self.root)
        doctrine = writer_driver('compile-doctrine').build(self.root)
        brief = self.root / 'docs/briefs/implementation-decisions.md'
        brief.parent.mkdir(parents=True, exist_ok=True); brief.write_text(self.BRIEF)
        sup.commit_all(self.root, 'synthetic brief filed after publication')
        before = self.f.snapshot()
        self.assertEqual(migration.authority_view(self.root), published)
        self.assertEqual(writer_driver('summarize-adrs').build(self.root), summaries)
        self.assertEqual(writer_driver('compile-doctrine').build(self.root), doctrine)
        self.assertEqual(self.f.snapshot(), before)

    def run_lint(self, *paths):
        import contextlib, io, json
        lint = migration._reference_reader()
        out = io.StringIO()
        argv = ['--repo-root', str(self.root)] + [a for p in paths for a in ('--path', str(p))]
        with contextlib.redirect_stdout(out):
            code = lint.main(argv)
        return code, json.loads(out.getvalue())

    def test_lint_gives_a_brief_the_template_advice_for_a_demoted_slug(self):
        lint = migration._reference_reader()
        successor = 'rule:rotation-preserves-assessment-outcomes'
        self.complete_governs()
        self.prepare('# Changelog\n\n## [3.10.0] \u2014 2026-08-01\n\nrule:rotation\n')
        self.f.close(); self.f.publish()
        brief = self.root / 'docs/briefs/implementation-decisions.md'
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(self.BRIEF + '\nThe handle form ADR-0110/rotation also appears.\n')
        sup.commit_all(self.root, 'synthetic brief filed after publication')
        for paths in ((), (brief,)):
            code, report = self.run_lint(*paths)
            findings = report['rule_findings'] + report['unresolved']
            self.assertEqual(code, 1, paths)
            self.assertTrue(report['rule_findings'] and report['unresolved'], 'both legs report')
            for finding in findings:
                self.assertEqual(finding['reason'], 'historical', finding)
                self.assertNotIn('replace with', finding['message'])
                self.assertIn(lint.BRIEF_HISTORICAL_ADVICE, finding['message'])
                self.assertIn(successor, finding['message'])
        notes = self.root / 'notes/live.md'
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text(self.BRIEF + '\nThe handle form ADR-0110/rotation also appears.\n')
        code, report = self.run_lint(notes)
        self.assertEqual(code, 1)
        self.assertTrue(report['rule_findings'] and report['unresolved'], 'both legs report')
        for finding in report['rule_findings'] + report['unresolved']:
            self.assertIn('replace with ' + successor, finding['message'])

    def test_published_new_shipped_citation_of_demoted_slug_still_refuses(self):
        self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        self.f.close(); self.f.publish()
        self.assertEqual(migration.authority_view(self.root)['state'], 'published')
        skill = self.root / 'crux/skills/demo/SKILL.md'
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text('# Demo\n\nFollow the rotation.[^r]\n\n[^r]: rule:rotation\n')
        sup.commit_all(self.root, 'synthetic shipped citation after publication')
        with self.assertRaisesRegex(migration.Refused, 'migration-citation-repair-required'):
            migration.authority_view(self.root)

    def test_preapply_new_brief_citation_still_drifts(self):
        self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        self.f.close()
        brief = self.root / 'docs/briefs/implementation-decisions.md'
        brief.parent.mkdir(parents=True, exist_ok=True); brief.write_text(self.BRIEF)
        sup.commit_all(self.root, 'synthetic brief filed before publication')
        with self.assertRaisesRegex(migration.Refused, 'migration-citation-dependency-drift'):
            migration._validated_inputs(self.root, self.f.proof)

    def test_published_shallow_history_still_refuses(self):
        self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        self.f.close(); self.f.publish()
        self.assertEqual(migration.authority_view(self.root)['state'], 'published')
        head = migration.cr.git(self.root, 'rev-parse', 'HEAD').stdout
        (self.root / '.git/shallow').write_bytes(head)
        with self.assertRaisesRegex(migration.Refused, 'history-unavailable'):
            migration.authority_view(self.root)

    def test_preapply_citation_digest_drift_still_refuses(self):
        self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        self.f.close()
        self.path.write_text(self.path.read_text() + '\nUnrelated dated explanation.\n')
        sup.commit_all(self.root, 'synthetic changelog growth before publication')
        before = self.f.snapshot()
        with self.assertRaisesRegex(migration.Refused, 'migration-citation-dependency-drift'):
            migration._validated_inputs(self.root, self.f.proof)
        self.assertEqual(self.f.snapshot(), before)

    def test_typed_partition_does_not_exempt_other_files_or_ambiguous_headings(self):
        for heading in ('## [Unreleased]', '## [3.10.0] — invalid',
                        '## [3.10.0] — 2026-08-01\n\n## [3.10.0] — 2026-08-01'):
            batch = self.prepare('# Changelog\n\n' + heading + '\n\nrule:rotation\n')
            deps, kinds = migration._citation_inspection(self.root, batch)
            self.assertEqual(deps, batch['citation_dependencies']); self.assertTrue(kinds['governing'])
            self.assertEqual(kinds['historical'], [])
        batch = self.prepare('# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n')
        (self.root / 'README.md').write_bytes(self.path.read_bytes())
        _, kinds = migration._citation_inspection(self.root, batch)
        self.assertEqual({row['path'] for row in kinds['governing']}, {'README.md'})
        self.assertTrue(kinds['historical']); self.assertTrue(all(
            row['authority'] == 'none' and row['path'] == 'CHANGELOG.md' for row in kinds['historical']))


if __name__ == '__main__': unittest.main()
