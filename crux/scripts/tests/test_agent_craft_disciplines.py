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

A second class pins the base-SHA handoff: the dev-lead names its HEAD SHA in
every developer dispatch, and the developer fast-forwards to it or reports
`BLOCKED` before any edit. Its absence check (each file's statements stay out of
the other) has its positive control in the presence assertions over the same
needles, run by the same matcher against the owning file.

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


COMMANDER_MD = AGENTS_DIR / "commander.md"

# The base-SHA handoff. A harness worktree can branch from the remote default
# branch rather than the lead's HEAD, so a developer dispatched with isolation
# starts without the lead's unpushed commits. The lead names its HEAD SHA in
# every dispatch; the developer fast-forwards to it or reports BLOCKED before
# any edit.
DEV_LEAD_BASE_SHA = {
    "dispatch carries the SHA": (
        "Every developer dispatch carries your `git rev-parse HEAD` SHA as the "
        "base the developer starts from."
    ),
    "commit first": (
        "Commit what the developer needs before you dispatch; the SHA carries "
        "committed work only."
    ),
}
DEVELOPER_BASE_SHA = {
    "compare before any edit": (
        "Before any edit, run `git rev-parse HEAD` and compare it to that SHA:"
    ),
    "SHA format checked": (
        "The SHA must be 40 or 64 lowercase hex characters; anything else gets "
        "`BLOCKED` before you run a git command with it."
    ),
    "equal proceeds": "If HEAD equals the SHA, proceed.",
    "ancestor condition": (
        "If HEAD is an ancestor of the SHA (`git merge-base --is-ancestor HEAD "
        "<sha>` exits 0), run `git merge --ff-only <sha>`."
    ),
    "failed merge blocks": (
        "If the merge fails, or HEAD still differs from the SHA, report "
        "`BLOCKED` and edit nothing; otherwise proceed."
    ),
    "otherwise blocked": (
        "Otherwise, report `BLOCKED` with both SHAs and edit nothing."
    ),
    "no SHA needs context": (
        "A dispatch that names no base SHA gets `NEEDS_CONTEXT` before any edit."
    ),
}
BASE_SHA_HEADING = "\n## First: start from your lead's commit\n"


class BaseShaHandoffTests(unittest.TestCase):
    """The lead names its HEAD SHA; the developer starts from it or stops."""

    def test_dev_lead_dispatch_carries_head_sha(self):
        text = body(DEV_LEAD_MD)
        for label, needle in DEV_LEAD_BASE_SHA.items():
            with self.subTest(statement=label):
                self.assertEqual(occurrences(text, needle), 1, needle)

    def test_developer_checks_base_sha_before_any_edit(self):
        text = body(DEVELOPER_MD)
        for label, needle in DEVELOPER_BASE_SHA.items():
            with self.subTest(statement=label):
                self.assertEqual(occurrences(text, needle), 1, needle)

    def test_developer_steps_run_in_order(self):
        text = normalize(body(DEVELOPER_MD))
        order = [
            normalize(DEVELOPER_BASE_SHA[k])
            for k in ("no SHA needs context", "SHA format checked",
                      "compare before any edit", "equal proceeds",
                      "ancestor condition", "failed merge blocks",
                      "otherwise blocked")
        ]
        positions = [text.index(n) for n in order]
        self.assertEqual(positions, sorted(positions))

    def test_developer_base_check_is_the_first_section(self):
        text = body(DEVELOPER_MD)
        self.assertEqual(text.count(BASE_SHA_HEADING), 1)
        first = text.index(BASE_SHA_HEADING)
        self.assertLess(first, text.index("\n## Before you write the first test\n"))
        self.assertLess(first, text.index("\n## Embedded disciplines\n"))

    def test_base_sha_statements_stay_in_their_own_file(self):
        lead, dev = body(DEV_LEAD_MD), body(DEVELOPER_MD)
        for needle in DEVELOPER_BASE_SHA.values():
            self.assertEqual(occurrences(lead, needle), 0, needle)
        for needle in DEV_LEAD_BASE_SHA.values():
            self.assertEqual(occurrences(dev, needle), 0, needle)

    def test_commander_dispatching_developers_carries_the_sha(self):
        # The commander reaches developers only through the dev-lead today. If
        # it ever gains a direct developer dispatch, it owes the same handoff.
        text = body(COMMANDER_MD)
        if "Agent(developer)" in text:
            self.assertEqual(
                occurrences(text, DEV_LEAD_BASE_SHA["dispatch carries the SHA"]), 1
            )
        else:
            self.assertEqual(occurrences(text, "Agent(dev-lead)"), 1)


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


HISTORIAN_MD = AGENTS_DIR / "historian.md"

# The historian reads the objectives before any work, verbatim transcription
# included. The contract route was chosen over a carve-out in the rule.
HISTORIAN_READ = (
    "Read the resolved `<docs_dir>/objectives.md` before starting any work, "
    "including verbatim transcription of another agent's report."
)
HISTORIAN_WRITE_ONLY = (
    "Read the objectives before any write, except verbatim transcription."
)
HISTORIAN_CARVE_OUTS = (
    "before any write",
    "except verbatim",
    "transcription is exempt",
    "except transcription",
)


class HistorianObjectivesTests(unittest.TestCase):
    def setUp(self):
        self.text = body(HISTORIAN_MD)

    def test_read_sentence_appears_once(self):
        self.assertEqual(occurrences(self.text, HISTORIAN_READ), 1)

    def test_section_sits_between_the_intro_and_what_you_do(self):
        text = normalize(self.text)
        head = text.index("## Mission and objectives")
        read = text.index(normalize(HISTORIAN_READ))
        what = text.index("## What you do")
        self.assertLess(head, read)
        self.assertLess(read, what)

    def test_no_carve_out_for_transcription(self):
        for phrase in HISTORIAN_CARVE_OUTS:
            with self.subTest(phrase=phrase):
                self.assertEqual(occurrences(self.text, phrase), 0)

    def test_rules_and_populate_clause_appear_once(self):
        for needle in (
            "`rule:objectives-read-before-work`",
            "`rule:objectives-context-travels-with-every-delegation`",
            "you never populate it",
        ):
            with self.subTest(needle=needle):
                self.assertEqual(occurrences(self.text, needle), 1)

    def test_bash_clause_matches_the_tool_allowlist(self):
        self.assertIn("Bash", self.text.split("---")[1].split("tools:")[1].split("\n")[0])
        self.assertEqual(occurrences(self.text, "you hold `Bash`"), 1)

    def test_detector_controls(self):
        wrapped = "  " + HISTORIAN_READ.replace(" before starting ", "\n  before starting ")
        self.assertEqual(occurrences(wrapped, HISTORIAN_READ), 1)
        self.assertGreaterEqual(occurrences(HISTORIAN_WRITE_ONLY, "before any write"), 1)
        self.assertGreaterEqual(occurrences(HISTORIAN_WRITE_ONLY, "except verbatim"), 1)


GATES_MD = REPO_ROOT / "crux" / "skills" / "run-promptbook" / "references" / "gates.md"

# The deferral check against the book's Outcome and Evidence sentences.
COMMANDER_CHECK = (
    "Before you record any deferral or known limitation, check it against each "
    "Outcome and Evidence sentence of the book's `goal`, and against any "
    "narrowing the run snapshot records."
)
COMMANDER_NOTES = (
    "Record the check in the run Notes, by one of the two routes above, as one "
    'line beside the deferral: "checked against Outcome/Evidence: no conflict", '
    "or the sentence it contradicts."
)
# The rule supports "a finding" (a contradicted item is a finding); the known-limitation
# clause is the contract's own, so the citation sits before it.
COMMANDER_FINDING = (
    "A deferral that contradicts an Outcome or Evidence sentence is a finding "
    "(`rule:completion-separates-verified-from-unobserved`), never a known "
    "limitation."
)
COMMANDER_OPEN_FINDING = (
    "it enters the next council round's question as an open blocking finding "
    "that names its originating item and the Outcome or Evidence sentence it "
    "contradicts."
)
COMMANDER_STOP = (
    "report a contradicted-premise stop so the owner decides, and never record "
    "it as a known limitation."
)
COMMANDER_RECORDING = (
    "Have the historian record notes, deferrals with their Outcome/Evidence "
    "check lines, and gate tokens after the advance."
)
COMMANDER_PENDING = (
    "When its `adr_acceptance_pending` list is non-empty, do not issue the "
    "prompt. For an entry whose `remedy` is `transition-adr` and whose module "
    "has not yet run `transition-adr`, dispatch the accepting architect to run "
    "it per `references/gates.md`. Every other entry takes a "
    "contradicted-premise stop for the owner, including an entry whose "
    "`transition-adr` already failed."
)
GATES_DEFERRAL = (
    "Before a deferral is recorded, check it against the book's Outcome and "
    "Evidence sentences, and against any narrowing the run snapshot records. "
    "Write the check as one line in the run Notes beside it."
)
SEEDED_OLD_ROUTE = "so the council or the owner decides"
# Retired wording: a write instruction to a role with no write tool, and "limit" for
# "known limitation".
RETIRED_COMMANDER = ("Write the check into the run Notes", "never a limit (")


class CommanderDeferralCheckTests(unittest.TestCase):
    def setUp(self):
        self.commander = body(COMMANDER_MD)
        self.gates = body(GATES_MD)

    def test_commander_carries_each_deferral_statement_once(self):
        for needle in (COMMANDER_CHECK, COMMANDER_NOTES, COMMANDER_FINDING, COMMANDER_OPEN_FINDING,
                       COMMANDER_STOP, COMMANDER_RECORDING, COMMANDER_PENDING):
            with self.subTest(needle=needle[:40]):
                self.assertEqual(occurrences(self.commander, needle), 1)

    def test_old_route_is_absent(self):
        self.assertEqual(occurrences(self.commander, SEEDED_OLD_ROUTE), 0)
        for retired in RETIRED_COMMANDER:
            with self.subTest(retired=retired):
                self.assertEqual(occurrences(self.commander, retired), 0)

    def test_gates_carries_the_deferral_sentence_once(self):
        self.assertEqual(occurrences(self.gates, GATES_DEFERRAL), 1)
        for clause in (
            "A deferral that contradicts an Outcome or Evidence sentence is a finding.",
            "it enters the next council round's question as an open blocking finding "
            "that names its originating item and the sentence it contradicts",
            "When none is ahead, it is a contradicted-premise stop for the owner.",
            "It is never recorded as a known limitation.",
        ):
            with self.subTest(clause=clause[:40]):
                self.assertEqual(occurrences(self.gates, clause), 1)


class DeferralDetectorControlTests(unittest.TestCase):
    def test_wrapped_copies_still_count_once(self):
        wrapped = "  " + COMMANDER_CHECK.replace(" check it ", "\n  check it ")
        self.assertEqual(occurrences(wrapped, COMMANDER_CHECK), 1)
        wrapped = "  " + HISTORIAN_READ.replace(" before starting ", "\n  before starting ")
        self.assertEqual(occurrences(wrapped, HISTORIAN_READ), 1)

    def test_seeded_old_route_is_found(self):
        sample = "Raise it " + SEEDED_OLD_ROUTE + "."
        self.assertGreaterEqual(occurrences(sample, SEEDED_OLD_ROUTE), 1)


if __name__ == "__main__":
    unittest.main()
