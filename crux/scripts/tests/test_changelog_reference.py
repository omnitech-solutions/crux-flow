"""Dated release references preserve history without exempting current uses."""
import hashlib
import builtins
import contextlib
import importlib
import os
import re
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:  # package-relative when run as a module, flat when run by discovery
    from ._dev_surface import IS_STAGED_ARTIFACT
except ImportError:  # pragma: no cover
    __import__('sys').path.insert(0, str(Path(__file__).resolve().parent))
    from _dev_surface import IS_STAGED_ARTIFACT

READER = importlib.import_module('lint-governs-references')
ROOT = Path(__file__).resolve().parents[3]
TOKEN = 'rule:journal-index-row-is-derived-on-every-write'


def occurrences(text):
    """Use the actual caller's canonical token patterns, retaining locations."""
    return sorted([dict(token=m.group(), start=m.start(), end=m.end())
                   for pattern in (READER.RULE_RE, READER.HANDLE_RE)
                   for m in pattern.finditer(text)], key=lambda row: row['start'])


def classify(text):
    return importlib.import_module('changelog_reference').classify_changelog_references(text, occurrences(text))


class ChangelogReferenceTests(unittest.TestCase):
    def test_label_scan_preserves_nonoverlapping_label_grammar(self):
        module = importlib.import_module('changelog_reference')
        fixtures = ('plain', '[^]', '[^name]', '[^[^inner]', '[^][^next]',
                    '[^unclosed[^again', '[^a]tail[^b]', '[^ A  Label ]',
                    '[^line\nbreak]', '[^é]')
        for text in fixtures:
            for start in (0, 2, len(text)):
                with self.subTest(text=text, start=start):
                    expected = [match[1] for match in re.compile(r'\[\^([^\]]+)\]').finditer(text, start)]
                    self.assertEqual(expected, list(module._labels(text, start)))

    def test_unclosed_label_scan_visits_each_suffix_position_once(self):
        module = importlib.import_module('changelog_reference')

        class ObservedText(str):
            def __new__(cls, value):
                instance = super().__new__(cls, value)
                instance.positions = []
                return instance

            def startswith(self, prefix, start=0, *args):
                self.positions.append(start)
                return super().startswith(prefix, start, *args)

            def __getitem__(self, key):
                if isinstance(key, int):
                    self.positions.append(key)
                return super().__getitem__(key)

        for count in (12, 24):
            text = ObservedText('[^' * count + 'unfinished')
            self.assertEqual([], list(module._labels(text)))
            self.assertEqual(sorted(set(text.positions)), text.positions)
            self.assertLessEqual(len(text.positions), len(text))
        # The production path still scans footnote uses when no tokens occur.
        text = '## [Unreleased]\n' + '[^' * 12 + '\n'
        with patch.object(module, '_labels', wraps=module._labels) as scanner:
            self.assertEqual({'governing': [], 'historical': []},
                             module.classify_changelog_references(text, []))
            self.assertTrue(scanner.called)

    def test_actual_historical_footnote_reference_and_raw_bytes_preserved(self):
        if IS_STAGED_ARTIFACT:
            self.skipTest('the staged CHANGELOG.md is the public rendition, which carries no footnotes')
        path = ROOT / 'CHANGELOG.md'
        before = path.read_bytes()
        rows = classify(before.decode('utf-8'))
        matched = [row for row in rows['historical'] if row['token'] == TOKEN]
        self.assertEqual(1, len(matched))
        self.assertEqual('3.10.0', matched[0]['version'])
        self.assertEqual('2026-09-08', matched[0]['date'])
        self.assertEqual('none', matched[0]['authority'])
        self.assertFalse(any(row['token'] == TOKEN for row in rows['governing']))
        measure_line = next(index for index, line in enumerate(before.decode('utf-8').split('\n'), 1)
                            if line.startswith('[^measure-level]:'))
        measure_rows = [row for row in rows['historical'] if row['line'] == measure_line]
        self.assertTrue(measure_rows)
        self.assertTrue(all(row['authority'] == 'none' for row in measure_rows))
        self.assertEqual(hashlib.sha256(before).digest(), hashlib.sha256(path.read_bytes()).digest())

    def test_identical_tokens_in_prologue_unreleased_and_unknown_sections_remain_governing(self):
        for header in ('', '## [Unreleased]\n', '## Other\n',
                       '## [3.10.0]\n', '## [Unreleased] — 2026-09-08\n'):
            with self.subTest(header=header):
                rows = classify(header + TOKEN + '\n')
                self.assertEqual([TOKEN], [row['token'] for row in rows['governing']])
                self.assertEqual([], rows['historical'])

    def test_mixed_sections_classify_each_occurrence_without_whole_file_exemption(self):
        text = '## [Unreleased]\n' + TOKEN + '\n## [3.10.0] — 2026-09-08\n' + TOKEN + '\n'
        rows = classify(text)
        self.assertEqual(1, len(rows['governing']))
        self.assertEqual(1, len(rows['historical']))
        self.assertEqual(2, text[:rows['governing'][0]['start']].count('\n') + 1)
        self.assertEqual(4, rows['historical'][0]['line'])

    def test_fenced_heading_never_opens_historical_section(self):
        for fence in ('```', '~~~', '````'):
            with self.subTest(fence=fence):
                text = '## [Unreleased]\n' + fence + '\n## [3.10.0] — 2026-09-08\n' + fence + '\n' + TOKEN + '\n'
                self.assertEqual([TOKEN], [row['token'] for row in classify(text)['governing']])

    def test_invalid_date_and_unrecognized_version_remain_governing(self):
        for version, date in (('3.10.0', '2026-02-30'), ('3.10.0', '2026-13-01'),
                              ('notes', '2026-09-08'), ('3.10', '2026-09-08')):
            with self.subTest(version=version, date=date):
                text = f'## [{version}] — {date}\n{TOKEN}\n'
                self.assertEqual([TOKEN], [row['token'] for row in classify(text)['governing']])

    def test_current_footnote_use_keeps_historical_definition_governing(self):
        text = '## [Unreleased]\nCurrent claim.[^old]\n## [3.10.0] — 2026-09-08\n[^old]: ' + TOKEN + '\n'
        rows = classify(text)
        self.assertEqual([TOKEN], [row['token'] for row in rows['governing']])
        self.assertEqual([], rows['historical'])

    def test_unique_cross_release_footnote_reference_remains_historical(self):
        text = '## [3.11.0] — 2026-09-09\nHistorical claim.[^old]\n## [3.10.0] — 2026-09-08\n[^old]: ' + TOKEN + '\n'
        self.assertEqual([TOKEN], [row['token'] for row in classify(text)['historical']])

    def test_ambiguous_cross_section_definitions_do_not_hide_current_use(self):
        text = ('## [Unreleased]\nCurrent claim.[^old]\n## [3.11.0] — 2026-09-09\n[^old]: '
                + TOKEN + '\n## [3.10.0] — 2026-09-08\n[^old]: ' + TOKEN + '\n')
        rows = classify(text)
        self.assertEqual(2, len(rows['governing']))
        self.assertEqual([], rows['historical'])

    def test_duplicate_versions_even_different_dates_and_v_prefix_are_unresolved(self):
        text = f'## [3.10.0] — 2026-09-08\n{TOKEN}\n## [3.10.0] — 2026-09-09\n{TOKEN}\n'
        self.assertEqual(2, len(classify(text)['governing']))
        self.assertEqual([], classify(text)['historical'])
        self.assertEqual([TOKEN], [row['token'] for row in classify(f'## [v3.10.0] — 2026-09-08\n{TOKEN}\n')['governing']])

    def test_fenced_token_survives_and_unclosed_fence_suffix_is_governing(self):
        text = f'## [3.10.0] — 2026-09-08\n~~~~\n## [Unreleased]\n{TOKEN}\n~~~~\n'
        self.assertEqual([TOKEN], [row['token'] for row in classify(text)['historical']])
        text = f'## [3.10.0] — 2026-09-08\n{TOKEN}\n````\n```\n{TOKEN}\n'
        rows = classify(text)
        self.assertEqual(1, len(rows['historical']))
        self.assertEqual(1, len(rows['governing']))

    def test_normalized_labels_continuations_and_ambiguous_association(self):
        text = ('## [Unreleased]\nCurrent.[^ OLD  Label ]\n## [3.10.0] — 2026-09-08\n'
                '[^old label]: definition\n\n    ' + TOKEN + '\n')
        self.assertEqual([TOKEN], [row['token'] for row in classify(text)['governing']])
        for continuation in ('  ', ''):
            with self.subTest(continuation=continuation):
                text = '## [3.10.0] — 2026-09-08\n[^old]: definition\n' + continuation + TOKEN + '\n'
                self.assertEqual([TOKEN], [row['token'] for row in classify(text)['governing']])
        # Duplicate definitions block historical qualification even with no use.
        text = f'## [3.10.0] — 2026-09-08\n[^Old]: {TOKEN}\n\n[^old]: {TOKEN}\n'
        self.assertEqual(2, len(classify(text)['governing']))

    def test_current_footnote_reference_propagates_through_definition_references(self):
        text = ('## [Unreleased]\nCurrent.[^a]\n## [3.10.0] — 2026-09-08\n'
                '[^a]: nested.[^b]\n\n[^b]: ' + TOKEN + '\n')
        self.assertEqual([TOKEN], [row['token'] for row in classify(text)['governing']])

    def test_crlf_unicode_offsets_closed_occurrences_and_exhaustive_partition(self):
        module = importlib.import_module('changelog_reference')
        text = f'## [3.10.0] — 2026-09-08\r\nÉ historical {TOKEN}\r\nADR-0105/journal-index-row-is-derived-on-every-write\r\n'
        source = occurrences(text)
        result = module.classify_changelog_references(text, list(reversed(source)))
        self.assertEqual(len(source), len(result['governing']) + len(result['historical']))
        self.assertEqual(source, [{k: row[k] for k in ('token', 'start', 'end')} for row in result['historical']])
        self.assertEqual([2, 3], [row['line'] for row in result['historical']])
        for row in result['historical']:
            self.assertEqual(row['token'], text[row['start']:row['end']])
            self.assertEqual('dated-release', row['reference_kind'])
            self.assertEqual('none', row['authority'])
        for bad in ({'token': TOKEN, 'start': True, 'end': 2},
                    {'token': TOKEN, 'start': 0, 'end': 1},
                    {'token': TOKEN, 'start': 0, 'end': len(text) + 1},
                    dict(source[0], authority='none')):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'occurrence-refused'):
                module.classify_changelog_references(text, [bad])

    def test_pure_reader_import_and_call_have_no_filesystem_or_configuration_side_effects(self):
        code = compile((ROOT / 'crux/scripts/changelog_reference.py').read_text(), '<pure-reader>', 'exec')
        text = f'## [3.10.0] — 2026-09-08\n{TOKEN}\n'
        supplied = occurrences(text)
        with contextlib.ExitStack() as stack:
            for name in ('resolve', 'read_text', 'read_bytes', 'write_text', 'write_bytes',
                         'cwd', 'exists', 'stat', 'lstat', 'iterdir', 'glob'):
                stack.enter_context(patch.object(Path, name, side_effect=AssertionError('pure ' + name)))
            stack.enter_context(patch.object(builtins, 'open', side_effect=AssertionError('pure open')))
            stack.enter_context(patch.object(os, 'open', side_effect=AssertionError('pure os.open')))
            module = types.ModuleType('pure_changelog_reference')
            exec(code, module.__dict__)
            self.assertEqual([TOKEN], [row['token'] for row in module.classify_changelog_references(text, supplied)['historical']])

    def test_promotion_grammar_shared_with_no_other_promoter_behavior_change(self):
        module = importlib.import_module('changelog_reference')
        promoter = importlib.import_module('promote-changelog')
        self.assertIs(module.SEMVER_RE, promoter.SEMVER_RE)
        self.assertIs(module.DATE_RE, promoter.DATE_RE)


if __name__ == '__main__':
    unittest.main()
