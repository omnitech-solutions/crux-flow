"""The cycle module templates and their hand twins carry the orchestration contract.

The contract: one designated tester runs full suites, the Tester record is
the quality gate's evidence, no reviewer starts a full suite, the run has one
commit lane, the internal review checks the quality-gate prompt's Tester record against
the bookkeeping paths, and "the owner's instruction" is an owner decision captured
in the session. The module partials are hand-mirrored into the cycle, iterate
and patch templates with no regenerator, so each sentence is pinned in every
file that must carry it. Pins compare whitespace-normalised text.
"""

import re
import unittest
from pathlib import Path

import yaml

CRUX = Path(__file__).resolve().parent.parent.parent
TEMPLATES = CRUX / 'templates'
GATES = CRUX / 'skills' / 'run-promptbook' / 'references' / 'gates.md'

MODULE_DEV = 'cycle-module-dev.yaml'
MODULE_REVIEW = 'cycle-module-review.yaml'
MODULE_ADR = 'cycle-module-adr.yaml'
CYCLE = 'cycle-promptbook-template.yaml'
ITERATE = 'iterate-promptbook-template.yaml'
PATCH = 'patch-promptbook-template.yaml'

NO_RUNNER = (
    "A project whose full-suite runner takes no `--gate` or `--reuse` option "
    "still names the gate label in the Tester record and runs the suite each time."
)

TESTER_GATE = (
    "The run's designated tester runs the full suite. No agent other than "
    "the tester or the dev-lead starts a full suite, and one full suite runs "
    "at a time.",
    "The historian records the Tester record in the run notes under "
    "`Tester record`: gate label, command, HEAD, clean-tree status at start "
    "and end, result tokens and wall time.",
    "The quality-gate run carries the gate label "
    "`--gate <book-id>/<run-id>/p<N>/quality-gate`.",
    "Gate labels come from the closed set `integration`, `quality-gate`, `exit`.",
    "`<book-id>` is the book's `id` field (for example `PB-0144`), and "
    "`<run-id>` is the run's `run_id` (for example `RUN-001`).",
    "A cycle run labels its integration run `quality-gate`.",
    "`exit` labels the full suite at the exit gate before merge is offered.",
    "`integration` labels an integration full suite in a book that has no "
    "quality-gate prompt.",
    "Per-unit runs pass no `--gate` and are never reusable.",
    "Before re-running a gate's full suite, the tester may pass `--reuse` "
    "with that gate's label: the full-suite runner reports the matching record "
    "and starts no second run, and otherwise runs the suites.",
    "A record for another gate never matches `--reuse` for this one.",
    NO_RUNNER,
    "While the tester's full suite or a live-tree tool runs, no agent "
    "commits and no council convenes.",
)

REVIEWER = (
    "No reviewer starts a full suite; reviewers cite the Tester record.",
)

LANE = (
    "Integrate commits only outside the tester's window.",
    "The tester's window runs from the tester's dispatch until the tester "
    "returns.",
    "The agent that dispatched the tester holds the window.",
    "While it is open, that agent commits nothing to the main checkout, "
    "convenes no council, runs no live-tree tool, and dispatches no agent "
    "that does.",
    "Every dispatch you make while the window is open says so.",
    "The run has one commit lane: no commit and no council while the "
    "tester's full suite or a live-tree tool (compile-doctrine.py, "
    "summarize-adrs.py, derive-arch.py, run-drift-gates.py, the council "
    "runner) runs.",
)

PRECONDITION = (
    "This review requires the Tester record from this module's quality-gate "
    "prompt.",
    "It holds when `git merge-base --is-ancestor <recorded HEAD> HEAD` "
    "exits 0, every path printed by `git diff --name-only <recorded HEAD> "
    "HEAD` lies under a bookkeeping path, and `git status --porcelain "
    "--untracked-files=all` is empty.",
    "The bookkeeping paths are the run directory, the run's own book file, "
    "`<docs_dir>/log.md` and `<docs_dir>/journal/`.",
    "The book file counts only while its frozen-plan hash equals the run's "
    "`book_content_hash`; compare them with audit-docs CHK-PB-BIND, or "
    "compute_book_hash from `${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py`.",
    "The frozen-plan subset excludes the run-state fields `current_run`, "
    "`current_prompt`, `status` and every key outside the plan subset.",
    "Name the result in the run notes.",
    "This is citation, never a substitute for the integration run, the "
    "exit gate or a release gate.",
    "When any precondition check fails, the run stays at this prompt: no "
    "advance-run route returns it to the quality-gate prompt.",
    "When `git status --porcelain --untracked-files=all` is not empty, the "
    "conductor first commits the run's own changes. It names every other path "
    "to the owner and stops; it deletes nothing.",
    "Then the conductor has the tester re-run the full suite of this module's "
    "quality-gate prompt, the historian records the new Tester record, and "
    "the checks run again.",
    "When the fixes in this prompt touch any path outside the bookkeeping "
    "paths, the tester runs a new full suite under this module's quality-gate "
    "label at the post-fix HEAD, and the historian records it as a new Tester "
    "record, before the review module cites one.",
)

OWNER = (
    "The owner's instruction is a decision the owner stated in the owner's "
    "own message in the session.",
    "Whoever acts on it quotes that message and its date: in the record's "
    "reason for an owner-exception record, and in the run notes for a "
    "recovery run with `--owner-commit-pending`.",
    "An agent report, a result file, a run note or a council verdict is "
    "never an owner's instruction.",
    "A standing council authorization covers council calls, never an "
    "owner-exception record.",
)

PINS = {
    MODULE_DEV: TESTER_GATE + LANE + PRECONDITION,
    CYCLE: TESTER_GATE + REVIEWER + LANE + PRECONDITION + OWNER,
    ITERATE: TESTER_GATE + REVIEWER + LANE + PRECONDITION,
    PATCH: TESTER_GATE + REVIEWER + LANE,
    MODULE_REVIEW: REVIEWER,
    MODULE_ADR: OWNER,
}


def norm(text):
    return re.sub(r'\s+', ' ', text)


def read(name):
    return norm((TEMPLATES / name).read_text(encoding='utf-8'))


class TemplateContractPins(unittest.TestCase):
    def test_each_template_carries_its_sentences(self):
        for name, sentences in PINS.items():
            text = read(name)
            for sentence in sentences:
                with self.subTest(template=name, sentence=sentence[:60]):
                    self.assertIn(sentence, text)

    def test_templates_still_parse_as_yaml(self):
        for name in PINS:
            with self.subTest(template=name):
                yaml.safe_load((TEMPLATES / name).read_text(encoding='utf-8'))

    def test_no_template_tells_a_reviewer_to_rerun_the_full_suite(self):
        for name in (MODULE_REVIEW, CYCLE, ITERATE):
            with self.subTest(template=name):
                text = read(name)
                self.assertNotIn('same mandate as the dev gate', text)
                self.assertNotIn('same mandate as Prompt 7', text)
                self.assertNotIn('including the **full test suite**', text)

    def test_no_template_names_root_as_tester(self):
        for name in PINS:
            with self.subTest(template=name):
                self.assertNotRegex(read(name).lower(), r'root (is|as) (the )?tester')

    def test_added_text_carries_no_inline_adr_number(self):
        for name, sentences in PINS.items():
            for sentence in sentences:
                with self.subTest(template=name, sentence=sentence[:40]):
                    self.assertNotRegex(sentence, r'ADR-\d')


class GatesReference(unittest.TestCase):
    def test_owner_instruction_is_defined(self):
        text = norm(GATES.read_text(encoding='utf-8'))
        for sentence in OWNER:
            self.assertIn(sentence, text)

    def test_owner_instruction_is_defined_before_its_first_use(self):
        text = norm(GATES.read_text(encoding='utf-8'))
        section = text.index('## 4. The council-gate procedure')
        definition = text.index(OWNER[0])
        self.assertLess(section, definition)
        self.assertLess(definition, text.index('--owner-commit-pending', section))
        self.assertLess(definition, text.index("at the owner's instruction", section))


if __name__ == '__main__':
    unittest.main()
