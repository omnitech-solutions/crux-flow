"""Captured configuration inputs use canonical policy without filesystem I/O."""
from __future__ import annotations

import builtins
import contextlib
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bionic_config as config

MANIFEST = 'schema_version: "5"\nconcerns_enabled: [adrs]\n'
CONFIG = 'config_version: "1"\ndocs_dir: knowledge/project\n'
MARKER = 'source: docs\nstep: 3\ninventory:\n  - manifest.yml\n'


class ConfigBufferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_config_parser_matches_file_wrapper_values_and_exact_errors(self):
        texts = [CONFIG,
                 '%YAML 1.2\n---\nconfig_version: "1"\ndocs_dir: docs\n...\n',
                 '--- {config_version: "1", docs_dir: docs}\n',
                 'config_version: "1"\nartifact_prefix: DEMO\narch_stack: ruby\n'
                 'arch_extractors: {code: src/parser.py:extract}\narch_decision_index_mode: curated\n',
                 'config_version: "1"\nunknown: ignored\n',
                 'config_version: "1"\ndocs_dir: ../outside\n',
                 '', 'config_version: 1\n', 'config_version: "9"\n',
                 '[list]\n', 'config_version: "1"\nartifact_prefix: PB\n',
                 'config_version: "1"\na: &anchor value\n',
                 'config_version: "1"\n# &anchor ignored\n',
                 'config_version: "1"\narch_stack: 2\n',
                 'config_version: "1"\narch_extractors: [bad]\n',
                 'config_version: "1"\narch_decision_index_mode: unknown\n',
                 'broken: [\x1b[31msecret\n', 'x' * 65537]
        for text in texts:
            with self.subTest(text=text[:60]):
                path = self.write('.bionic.yml', text)
                try:
                    expected = config._read_and_validate_config(path, '.bionic.yml')
                except config.BionicConfigError as exc:
                    with self.assertRaises(config.BionicConfigError) as actual:
                        config.parse_config_text(text, source='.bionic.yml', path=path)
                    self.assertEqual(str(exc), str(actual.exception))
                else:
                    self.assertEqual(expected, config.parse_config_text(text, source='.bionic.yml', path=path))

    def test_manifest_discriminator_matches_wrapper_shallow_semantics(self):
        for text in (MANIFEST, '', 'schema_version: "5"\n',
                     'concerns_enabled: []\n', '# schema_version: 5\n# concerns_enabled: []\n',
                     'schema_version: [broken\nconcerns_enabled: not-a-list\n'):
            with self.subTest(text=text):
                path = self.write('docs/manifest.yml', text)
                self.assertEqual(config._is_crux_manifest(path), config.is_crux_manifest_text(text))

    def test_marker_parser_matches_wrapper_values_and_errors(self):
        for text in (MARKER, 'source: docs\nstep: 1\ninventory:\n',
                     'source: docs\n', MARKER.replace('step: 3', 'step: 7'),
                     MARKER.replace('source: docs', 'source: ../outside'),
                     MARKER + 'extra\n'):
            with self.subTest(text=text):
                path = self.write('bionic/.migrating', text)
                try:
                    expected = config._parse_migration_marker(path)
                except config.BionicConfigError as exc:
                    with self.assertRaises(config.BionicConfigError) as actual:
                        config.parse_migration_marker_text(text, marker=path)
                    self.assertEqual(str(exc), str(actual.exception))
                else:
                    self.assertEqual(expected, config.parse_migration_marker_text(text, marker=path))

    def assert_layout_matches(self, inputs):
        for relative, text in inputs.items():
            self.write(relative, text)
        actual = config.load_config(self.root)
        values, source = config.select_layout_inputs(inputs)
        self.assertEqual((actual.config_version, actual.docs_dir, actual.artifact_prefix,
                          actual.arch_stack, actual.arch_extractors, actual.arch_decision_index_mode), values)
        self.assertEqual(actual.source, source)

    def test_selected_layout_preserves_all_precedence_and_discovery_lanes(self):
        cases = [({}, 'bionic'),
                 ({'docs/manifest.yml': MANIFEST}, 'docs'),
                 ({'bionic/manifest.yml': MANIFEST}, 'bionic'),
                 ({'.crux': CONFIG}, 'knowledge/project'),
                 ({'.bionic.yml': CONFIG, '.crux': 'bad config'}, 'knowledge/project'),
                 ({'.bionic.yml': 'config_version: "1"\nartifact_prefix: DEMO\n',
                   '.crux': CONFIG, 'docs/manifest.yml': MANIFEST}, 'docs'),
                 ({'docs/manifest.yml': MANIFEST, 'bionic/manifest.yml': MANIFEST,
                   'bionic/.migrating': MARKER}, 'docs'),
                 ({'.bionic.yml': CONFIG, 'bionic/.migrating': 'invalid ignored'}, 'knowledge/project')]
        for inputs, expected in cases:
            with self.subTest(inputs=inputs), tempfile.TemporaryDirectory() as temp:
                previous = self.root
                self.root = Path(temp).resolve()
                try:
                    self.assert_layout_matches(inputs)
                    self.assertEqual(expected, config.select_layout_inputs(inputs)[0][1])
                finally:
                    self.root = previous

    def test_discovery_ambiguity_marker_qualification_and_selected_text_refuse(self):
        cases = [{'docs/manifest.yml': MANIFEST, 'bionic/manifest.yml': MANIFEST},
                 {'bionic/.migrating': 'source: docs\n'},
                 {'docs/.migrating': MARKER, 'bionic/.migrating': MARKER},
                 {'.bionic.yml': 'docs_dir: docs\n'},
                 {'.bionic.yml': 'config_version: "1"\ndocs_dir: ../outside\n'}]
        for inputs in cases:
            with self.subTest(inputs=inputs):
                with self.assertRaises(config.BionicConfigError):
                    config.select_layout_inputs(inputs)
        # A valid marker naming neither qualified conventional tree does not
        # resolve the both-valid ambiguity.
        with self.assertRaises(config.BionicConfigError):
            config.discover_docs_dir_from_inputs({'docs/manifest.yml': MANIFEST,
                'bionic/manifest.yml': MANIFEST, 'bionic/.migrating': MARKER.replace('source: docs', 'source: other')})

    def test_all_pure_apis_work_under_filesystem_traps_without_mutating_inputs(self):
        inputs = {'.bionic.yml': 'config_version: "1"\n',
                  'docs/manifest.yml': MANIFEST, 'bionic/manifest.yml': MANIFEST,
                  'bionic/.migrating': MARKER}
        original = copy.deepcopy(inputs)
        with contextlib.ExitStack() as stack:
            for name in ('read_text', 'read_bytes', 'write_text', 'write_bytes', 'exists',
                         'is_file', 'is_dir', 'is_symlink', 'resolve', 'cwd', 'stat', 'lstat',
                         'mkdir', 'iterdir', 'glob'):
                stack.enter_context(patch.object(Path, name, side_effect=AssertionError('pure API used filesystem ' + name)))
            stack.enter_context(patch.object(builtins, 'open', side_effect=AssertionError('pure open')))
            stack.enter_context(patch.object(os, 'open', side_effect=AssertionError('pure os.open')))
            self.assertEqual('1', config.parse_config_text(CONFIG, source='.bionic.yml')[0])
            self.assertTrue(config.is_crux_manifest_text(MANIFEST))
            self.assertEqual('docs', config.parse_migration_marker_text(MARKER, marker='bionic/.migrating'))
            self.assertEqual('docs', config.discover_docs_dir_from_inputs(inputs))
            self.assertEqual('docs', config.select_layout_inputs(inputs)[0][1])
            self.assertEqual(config.select_layout_inputs(inputs), config.select_layout_inputs(copy.deepcopy(inputs)))
        self.assertEqual(original, inputs)

    def test_filesystem_wrapper_never_reads_or_probes_ignored_lower_tiers(self):
        selected = self.write('.bionic.yml', CONFIG)
        self.write('.crux', 'invalid ignored config')
        self.write('bionic/.migrating', 'invalid ignored marker')
        original_read, original_exists = Path.read_text, Path.exists
        def read(path, *args, **kwargs):
            self.assertEqual(selected, path, 'explicit layout must not read ignored inputs')
            return original_read(path, *args, **kwargs)
        def exists(path, *args, **kwargs):
            if path.name in ('.crux', '.migrating'):
                raise AssertionError('ignored lower tier probed')
            return original_exists(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read), patch.object(Path, 'exists', exists):
            self.assertEqual('knowledge/project', config.load_config(self.root).docs_dir)

    def test_authored_version_strictness_and_redaction_are_preserved(self):
        for text in ('docs_dir: docs\n', '%YAML 1.2\n---\ndocs_dir: docs\n...\n',
                     'config_version: "\\x1b[31msecret"\n', 'broken: [\x1b[31msecret\n'):
            with self.subTest(text=text), self.assertRaises(config.BionicConfigError) as error:
                config.parse_config_text(text, source='.bionic.yml')
            self.assertNotIn('\x1b', str(error.exception))

    def test_config_extracts_keyless_or_invalid_docs_but_selection_validates(self):
        for docs in ('../outside', '.github', '/absolute', 'docs/../tree'):
            body = 'config_version: "1"\ndocs_dir: ' + docs + '\n'
            self.assertEqual(docs, config.parse_config_text(body, source='.bionic.yml')[1])
            with self.assertRaises(config.BionicConfigError):
                config.select_layout_inputs({'.bionic.yml': body})
        body = 'config_version: "1"\n'
        self.assertIsNone(config.parse_config_text(body, source='.bionic.yml')[1])
        self.assertEqual('docs', config.select_layout_inputs({'.bionic.yml': body,
                         'docs/manifest.yml': MANIFEST})[0][1])


if __name__ == '__main__':
    unittest.main()
