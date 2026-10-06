"""The review paragraphs in the promptbook templates sit between whole sentences.

A paragraph inserted into the middle of a sentence splits it: the text before
the paragraph ends with no terminal punctuation and the text after it starts
lowercase. Two paragraphs are inserted into review prompts by name, "Commission
these reviewers yourself" and "Where you fixed a MUST-FIX yourself". This suite
parses each template that carries one as YAML and checks two things:

1. the paragraph before each of those two paragraphs ends in terminal
   punctuation, and
2. the sentences the paragraphs once split are present whole, after whitespace
   normalisation.

The scan and the checks take injected strings, so the positive controls
re-splice a paragraph in memory and expect the checks to report it.
"""

import re
import unittest
from pathlib import Path

import yaml

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / 'templates'

TEMPLATE_NAMES = (
    'cycle-promptbook-template.yaml',
    'iterate-promptbook-template.yaml',
    'cycle-module-review.yaml',
    'patch-promptbook-template.yaml',
)

INSERTED_PARAGRAPH_STARTS = (
    'Commission these reviewers yourself',
    'Where you fixed a MUST-FIX yourself',
)

# Sentences the inserted paragraphs once split, per template.
WHOLE_SENTENCES = {
    'cycle-promptbook-template.yaml': (
        'If they still pass AND the original reviewers',
        'optimizes for the gate rather than for the user the fix is meant to serve',
    ),
    'iterate-promptbook-template.yaml': (
        'scoped to the full cycle diff',
        'optimizes for the gate rather than for the user the fix is meant to serve',
    ),
}

TERMINAL = re.compile(r'[.!?:][)\]*`"\']*$')


def collect_strings(node):
    """Return every string value in a parsed YAML document."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in collect_strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in collect_strings(v)]
    return []


def normalise(text):
    return re.sub(r'\s+', ' ', text).strip()


def paragraph_problems(strings):
    """Name each inserted paragraph whose predecessor lacks terminal punctuation."""
    problems = []
    for text in strings:
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text)]
        for index, paragraph in enumerate(paragraphs):
            if index == 0 or not paragraph.startswith(INSERTED_PARAGRAPH_STARTS):
                continue
            before = normalise(paragraphs[index - 1])
            if not TERMINAL.search(before):
                problems.append(
                    f'paragraph before {paragraph[:40]!r} ends mid-sentence: ...{before[-50:]!r}')
    return problems


def sentence_problems(name, strings):
    """Name each expected whole sentence missing from the strings of one template."""
    joined = normalise(' '.join(strings))
    return [f'{name}: sentence missing whole: {s!r}'
            for s in WHOLE_SENTENCES.get(name, ()) if s not in joined]


def splice(text, start):
    """Move the paragraph beginning `start` into the sentence before it.

    Returns the re-spliced text: the previous paragraph is cut three words
    before its end and the moved paragraph sits at the cut.
    """
    paragraphs = re.split(r'\n\s*\n', text)
    index = next(i for i, p in enumerate(paragraphs) if p.strip().startswith(start))
    words = paragraphs[index - 1].rstrip().split(' ')
    head, tail = ' '.join(words[:-3]), ' '.join(words[-3:])
    rebuilt = paragraphs[:index - 1] + [head, paragraphs[index], tail] + paragraphs[index + 1:]
    return '\n\n'.join(rebuilt)


def load_strings(name):
    return collect_strings(yaml.safe_load((TEMPLATES_DIR / name).read_text(encoding='utf-8')))


class TemplateSpliceTests(unittest.TestCase):

    def test_every_template_parses_and_carries_an_inserted_paragraph(self):
        for name in TEMPLATE_NAMES:
            with self.subTest(template=name):
                strings = load_strings(name)
                found = [s for s in strings if any(m in s for m in INSERTED_PARAGRAPH_STARTS)]
                self.assertGreaterEqual(len(found), 1, f'{name} carries no inserted paragraph')

    def test_paragraph_before_each_inserted_paragraph_is_a_whole_sentence(self):
        checked = 0
        for name in TEMPLATE_NAMES:
            strings = load_strings(name)
            checked += sum(
                1 for s in strings for p in re.split(r'\n\s*\n', s)
                if p.strip().startswith(INSERTED_PARAGRAPH_STARTS))
            with self.subTest(template=name):
                self.assertEqual(paragraph_problems(strings), [])
        self.assertGreaterEqual(checked, 7, 'expected the seven known inserted paragraphs')

    def test_split_sentences_are_present_whole(self):
        for name in WHOLE_SENTENCES:
            with self.subTest(template=name):
                self.assertEqual(sentence_problems(name, load_strings(name)), [])

    # ---- positive controls: a re-spliced paragraph turns the checks red ----

    def test_control_resplice_turns_paragraph_check_red(self):
        for name in ('cycle-promptbook-template.yaml', 'iterate-promptbook-template.yaml'):
            for start in INSERTED_PARAGRAPH_STARTS:
                strings = load_strings(name)
                hosts = [i for i, s in enumerate(strings)
                         if any(p.strip().startswith(start) for p in re.split(r'\n\s*\n', s))]
                with self.subTest(template=name, paragraph=start):
                    self.assertTrue(hosts, 'fixture premise: a host string exists')
                    for i in hosts:
                        strings[i] = splice(strings[i], start)
                    self.assertGreaterEqual(len(paragraph_problems(strings)), 1)

    def test_control_resplice_turns_sentence_check_red(self):
        name = 'iterate-promptbook-template.yaml'
        start = 'Commission these reviewers yourself'
        strings = load_strings(name)
        hosts = [i for i, s in enumerate(strings) if start in s]
        self.assertTrue(hosts)
        for i in hosts:
            paragraphs = re.split(r'\n\s*\n', strings[i])
            moved = next(p for p in paragraphs if p.strip().startswith(start))
            rest = '\n\n'.join(p for p in paragraphs if p is not moved)
            self.assertIn('scoped to the full cycle diff', normalise(rest))
            respliced = re.sub(
                r'scoped to the\s+full', lambda _: 'scoped to the\n\n' + moved + '\n\nfull', rest)
            self.assertNotEqual(respliced, rest, 'fixture premise: the splice point exists')
            strings[i] = respliced
        self.assertGreaterEqual(len(sentence_problems(name, strings)), 1)


if __name__ == '__main__':
    unittest.main()
