"""Pins the amended craft disciplines in the developer and dev-lead agent files.

The decision behind this suite amends four statements the two agent files embed:
the proof a test gives, the three-failed-fixes tripwire, where a developer sends a
capability gap, and what the dev-lead runs per unit, at integration and at the
exit gate. Each amended statement lives exactly once in each file it belongs to,
and the retired wording is absent. The patch template's implement prompt carries
the same proof statement.

Every absence check has a positive control: the same detector, run over the
retired wording (line-wrapped the way the agent files wrap it), must find it.
Without that control an absence assertion would pass on a detector that matches
nothing.

The files read here all cross the sync boundary (`crux/agents/`,
`crux/templates/`), so the suite runs unchanged against the staged artifact.

Stdlib unittest plus PyYAML, which the test package already requires.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "crux" / "agents"
DEVELOPER_MD = AGENTS_DIR / "developer.md"
DEV_LEAD_MD = AGENTS_DIR / "dev-lead.md"
PATCH_TEMPLATE = REPO_ROOT / "crux" / "templates" / "patch-promptbook-template.yaml"


def normalize(text: str) -> str:
    """Collapse whitespace and drop bold markers, so a wrapped line matches."""
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def occurrences(text: str, needle: str) -> int:
    return normalize(text).count(normalize(needle))


def body(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Statements shared verbatim by both agent files.
# ---------------------------------------------------------------------------

PROOF = (
    "A test is evidence that a change alters behaviour only once it has been "
    "seen to fail, for the reason the change addresses, against the code "
    "without the change."
)
TEST_FIRST_DEFAULT = "Test-first is the default"
REFACTOR = (
    "A refactor is shown by the tests covering its behaviour passing before "
    "and after it."
)
REMEDY = (
    "When a test has no observed failure, obtain it: disable or revert the "
    "change, watch the test fail for the reason the change addresses, restore "
    "the change, and watch it pass."
)
FIX_THE_TEST = (
    "If the test still passes without the change, it does not exercise the "
    "change, so fix the test."
)
NEVER_DELETE = (
    "Never delete working code only because its test has no observed failure."
)
DISPOSITIONS = (
    "Its Evidence item is `unobserved` until both observations exist. It is "
    "`contradicted` if the test fails for the addressed reason against the "
    "restored change; the change is then what gets fixed, and each failed "
    "attempt is a failed fix."
)
THRESHOLD = "Three failed fixes to one problem stop the fixing."
REASSESS = (
    "Reassess your hypothesis about the defect, your environment and the "
    "architecture, and record the evidence for each; presume none of them is "
    "the cause."
)
SECOND_RUN = "a second run of three failed fixes on the same problem"

SHARED = {
    "proof": PROOF,
    "test-first default": TEST_FIRST_DEFAULT,
    "refactor": REFACTOR,
    "remedy": REMEDY,
    "fix the test": FIX_THE_TEST,
    "never delete": NEVER_DELETE,
    "dispositions": DISPOSITIONS,
    "threshold": THRESHOLD,
    "reassess": REASSESS,
    "second run": SECOND_RUN,
}

# ---------------------------------------------------------------------------
# Statements owned by one file.
# ---------------------------------------------------------------------------

DEVELOPER_ONLY = {
    "own-scope correction": (
        "A hypothesis or environment fault inside your unit is yours to "
        "correct, and the work continues."
    ),
    "routes to the lead": "goes to your lead as `BLOCKED` with the reassessment, in your report.",
    "gaps in the report": "any capability gap you met outside your unit",
}

DEV_LEAD_ONLY = {
    "own-scope correction": (
        "A hypothesis or environment fault inside your own scope is yours to "
        "correct, and the work continues."
    ),
    "routes to the caller": "goes to your caller as a report.",
    "handoff is a report": "A handoff is a report inside the run, not a stop.",
    "contradicted premise": (
        "You judge whether an architecture finding contradicts the accepted "
        "plan; if it does, report a contradicted premise."
    ),
    "module-loop round": (
        "A problem that stays unresolved counts as a non-converging round of "
        "the module loop it occurred in."
    ),
    "developer gap gets own reflex": (
        "A capability gap a developer reports gets your own capability-gap "
        "reflex."
    ),
    "worktree per-unit gate": "run each worktree's per-unit gate before integrating",
    "per-unit reachable tests": (
        "run the tests that bear on the unit and every test the change could "
        "reach, including tests that import, invoke, read or enumerate what "
        "the unit changed."
    ),
    "unbounded falls back": "Where you cannot bound that set, run the full suite.",
    "not done on red": (
        "A unit is not done while any of those tests is red, or when they "
        "cannot be run."
    ),
    "integration run": (
        "Run the full suite once after integration, before you hand the work "
        "to review."
    ),
    "integration is the quality-gate run": (
        "In a cycle, that run is the dev module's quality-gate full suite, not "
        "a second run."
    ),
    "validity": (
        "A green full-suite result stays valid while no file in the tree it "
        "ran against has been added, removed or edited since that run"
    ),
    "exit-gate reuse": (
        "At the exit gate before you offer merge, a green full-suite result "
        "that is still valid satisfies the gate."
    ),
    "exit-gate re-run on change": (
        "Re-run the full suite there only when a file has changed since that "
        "result."
    ),
    "validity serves the exit gate only": (
        "Validity satisfies the exit gate and no other"
    ),
    "merge offer needs green": (
        "The full suite must be green before you offer merge/PR options"
    ),
}

# The retired wording, as the files carried it before the amendment. Each is a
# positive control for the absence detector below: wrapped across a line break
# and inside bold markers, as the originals were.
RETIRED = {
    "proves nothing": "a test written after code passes immediately and proves\n  nothing",
    "delete and restart": "is unverifiable; delete and\n  restart.",
    "architecture is wrong": "**3 failed fixes ⇒ the architecture is\n  wrong**",
    "likely wrong": "the approach (not the next tweak) is likely\n  wrong.",
}
RETIRED_DEV_LEAD = {
    "baseline test": "run the project's\nbaseline test suite via `Bash`",
}

# Rules each file cites in a footnote definition, by slug.
DEVELOPER_RULES = [
    "observed-failure-is-the-proof",
    "missing-failure-is-obtained-not-deleted",
    "three-failed-fixes-stop-and-reassess",
    "reassessment-routes-by-its-finding",
    "developer-reports-gaps-outside-its-unit",
]
DEV_LEAD_RULES = [
    "observed-failure-is-the-proof",
    "missing-failure-is-obtained-not-deleted",
    "three-failed-fixes-stop-and-reassess",
    "reassessment-routes-by-its-finding",
    "per-unit-gate-runs-reachable-tests",
    "full-suite-at-integration-and-exit",
]

FOOTNOTE_DEF_RE = re.compile(r"^\[\^[^\]]+\]:.*$", re.MULTILINE)


def footnote_rule_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    for line in FOOTNOTE_DEF_RE.findall(text):
        slugs.update(re.findall(r"rule:([a-z][a-z0-9-]*)", line))
    return slugs


class DetectorControlTests(unittest.TestCase):
    """Positive controls: the detectors find what the absence checks rule out."""

    def test_absence_detector_finds_each_retired_phrase(self):
        for phrase, sample in {**RETIRED, **RETIRED_DEV_LEAD}.items():
            with self.subTest(phrase=phrase):
                self.assertGreaterEqual(
                    occurrences(sample, phrase), 1,
                    f"detector misses {phrase!r} in its retired, wrapped form",
                )

    def test_presence_detector_matches_across_a_line_wrap(self):
        wrapped = "  " + PROOF.replace(" only once ", "\n  only once ")
        self.assertEqual(occurrences(wrapped, PROOF), 1)

    def test_footnote_detector_ignores_inline_citations(self):
        sample = "Body cites rule:inline-only here.\n\n[^x]: rule:in-footnote\n"
        self.assertEqual(footnote_rule_slugs(sample), {"in-footnote"})


class DeveloperDisciplineTests(unittest.TestCase):
    def setUp(self):
        self.text = body(DEVELOPER_MD)

    def test_each_amended_statement_appears_exactly_once(self):
        for label, needle in {**SHARED, **DEVELOPER_ONLY}.items():
            with self.subTest(statement=label):
                self.assertEqual(
                    occurrences(self.text, needle), 1,
                    f"developer.md: expected {label!r} exactly once: {needle!r}",
                )

    def test_retired_wording_is_absent(self):
        for phrase in [*RETIRED, *RETIRED_DEV_LEAD]:
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    occurrences(self.text, phrase), 0,
                    f"developer.md still carries {phrase!r}",
                )

    def test_dev_lead_only_statements_stay_out(self):
        for label in ("routes to the caller", "exit-gate reuse", "per-unit reachable tests"):
            with self.subTest(statement=label):
                self.assertEqual(occurrences(self.text, DEV_LEAD_ONLY[label]), 0)

    def test_footnotes_cite_the_rules(self):
        self.assertTrue(
            set(DEVELOPER_RULES) <= footnote_rule_slugs(self.text),
            f"developer.md footnotes cite {sorted(footnote_rule_slugs(self.text))}",
        )

    def test_frontmatter_and_headings_unchanged(self):
        self.assertIn(
            "tools: Read, Grep, Glob, Edit, Write, Bash, Skill, TodoWrite\n", self.text
        )
        self.assertIn("skills: [forge-skill, log-work]\n", self.text)
        self.assertIn("\n## Before you write the first test\n", self.text)
        self.assertIn("\n## Reporting back\n", self.text)
        self.assertIn("\n## Embedded disciplines\n", self.text)


class DevLeadDisciplineTests(unittest.TestCase):
    def setUp(self):
        self.text = body(DEV_LEAD_MD)

    def test_each_amended_statement_appears_exactly_once(self):
        for label, needle in {**SHARED, **DEV_LEAD_ONLY}.items():
            with self.subTest(statement=label):
                self.assertEqual(
                    occurrences(self.text, needle), 1,
                    f"dev-lead.md: expected {label!r} exactly once: {needle!r}",
                )

    def test_retired_wording_is_absent(self):
        for phrase in [*RETIRED, *RETIRED_DEV_LEAD]:
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    occurrences(self.text, phrase), 0,
                    f"dev-lead.md still carries {phrase!r}",
                )

    def test_developer_only_statements_stay_out(self):
        for label in ("routes to the lead", "gaps in the report"):
            with self.subTest(statement=label):
                self.assertEqual(occurrences(self.text, DEVELOPER_ONLY[label]), 0)

    def test_footnotes_cite_the_rules(self):
        self.assertTrue(
            set(DEV_LEAD_RULES) <= footnote_rule_slugs(self.text),
            f"dev-lead.md footnotes cite {sorted(footnote_rule_slugs(self.text))}",
        )

    def test_tool_allowlist_and_skills_unchanged(self):
        self.assertIn(
            "tools: Read, Grep, Glob, Edit, Write, Bash, Agent(developer), "
            "Agent(historian), Agent(reviewer), Agent(wayfinder), Skill, TodoWrite\n",
            self.text,
        )
        self.assertIn("skills: [forge-skill, log-work]\n", self.text)


class PatchTemplateImplementPromptTests(unittest.TestCase):
    """The patch template's implement prompt states the proof and its remedy."""

    PROOF_SENTENCE = (
        "A test is evidence of a change only once it has been seen to fail, "
        "for the reason the change addresses, against the code without the "
        "change."
    )
    REMEDY_SENTENCE = (
        "If a test has no observed failure, disable the change, watch the test "
        "fail for that reason, then restore the change and watch it pass."
    )

    def setUp(self):
        raw = PATCH_TEMPLATE.read_text(encoding="utf-8")
        book = yaml.safe_load(raw)
        implement = [p for p in book["prompts"] if p.get("phase") == "implement"]
        self.assertEqual(len(implement), 1, "expected one implement prompt")
        self.prompt = implement[0]["prompt"]

    def test_implement_prompt_keeps_test_first(self):
        self.assertEqual(
            occurrences(self.prompt, "Write the failing test first, watch it fail"), 1
        )

    def test_implement_prompt_states_proof_and_remedy(self):
        self.assertEqual(occurrences(self.prompt, self.PROOF_SENTENCE), 1)
        self.assertEqual(occurrences(self.prompt, self.REMEDY_SENTENCE), 1)

    def test_implement_prompt_drops_proves_nothing(self):
        self.assertEqual(occurrences(self.prompt, "proves nothing"), 0)

    def test_no_prompt_line_opens_with_a_rule_token(self):
        opening = [ln for ln in self.prompt.splitlines() if ln.lstrip().startswith("rule:")]
        self.assertEqual(opening, [])

    def test_rule_token_detector_control(self):
        sample = "Implement it.\n  rule:some-slug opens this line\n"
        opening = [ln for ln in sample.splitlines() if ln.lstrip().startswith("rule:")]
        self.assertEqual(len(opening), 1)


if __name__ == "__main__":
    unittest.main()
