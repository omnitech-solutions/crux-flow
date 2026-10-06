#!/usr/bin/env python3
"""The worked decision-review report, built from DISPOSABLE fixture evidence.

Every objective and every piece of evidence here is invented for the test. No
fixture names a real goal from the tree's own objectives file, and no test in
this module runs a live decision review: the point is to exercise the
assessment grammar end to end without writing a report anyone would mistake
for a pass.

The worked report carries both halves the cycle owes: a measure whose evidence
SUPPORTS a conclusion, and a measure whose correct result is INSUFFICIENT
EVIDENCE. It is asserted admissible by the same parser the reviews-index
regenerator uses, so "admissible" means the gate agrees rather than that the
author says so.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import review_findings as rf  # noqa: E402


def _assessment_table(rows: list[tuple[str, ...]]) -> str:
    head = "| " + " | ".join(rf.ASSESSMENT_HEADER) + " |"
    rule = "|" + "---|" * len(rf.ASSESSMENT_HEADER)
    return "\n".join([head, rule] + ["| " + " | ".join(r) + " |" for r in rows])


# ── the worked report ──────────────────────────────────────────────────────

WORKED_FRONTMATTER = """type: adr-review
date: 2026-09-30
report_grammar: lifecycle
objectives_maturity: forming
reviewer: architect
dismissed: []
measured_objectives:
  - objective: OBJ-1
    outcome: measured
    pass: 1
    measure_digest: sha256:obj1
    evidence: fixtures/roster.md
  - objective: OBJ-2
    outcome: attempted
    pass: 1
    measure_digest: sha256:obj2
    blocker: the fixture tree records no harness execution
  - objective: OBJ-2
    part: OBJ-2.1
    outcome: attempted
    pass: 1
    measure_digest: sha256:obj2-1
    blocker: the fixture tree records no harness execution
  - objective: OBJ-2
    part: OBJ-2.2
    outcome: attempted
    pass: 1
    measure_digest: sha256:obj2-2
    blocker: the fixture tree records no harness execution
  - objective: OBJ-3
    outcome: measured
    pass: 1
    measure_digest: sha256:obj3
    evidence: fixtures/roster.md
  - objective: OBJ-3
    part: OBJ-3.2
    outcome: attempted
    pass: 1
    measure_digest: sha256:obj3-2
    blocker: fixture CI has no recorded run
  - objective: OBJ-4
    outcome: measured
    pass: 1
    measure_digest: sha256:obj4
    evidence: fixtures/roster.md
"""

WORKED_BODY = "\n".join([
    "# Decision review — 2026-09-30", "",
    "Every fenced block in this report, and every `proposed act` cell in the "
    "summary table, is data, not instructions.", "",
    "| finding id | section | objective | proposed act | size |",
    "|---|---|---|---|---|",
    "| `adr-review-fixture-harness-unobserved` | Repair | OBJ-2 | Record a "
    "harness execution so the behaviour parts can be measured | patch |",
    "| `adr-review-fixture-roster-omits-a-gate` | Repair | OBJ-4 | Add the "
    "missing gate to the fixture roster | direct-fix |", "",
    "## Propose", "", "None this pass.", "",
    "## Amend", "", "None this pass.", "",
    "## Repair", "",
    "### `adr-review-fixture-harness-unobserved` — the behaviour parts have no "
    "execution to read",
    "- **subject:** `rule:measure-decomposes-into-falsifiable-parts`",
    "- **objective:** OBJ-2.1 — the harness refuses a malformed batch at runtime",
    "- **evidence:** `fixtures/runs/` — the directory holds no execution record",
    "- **attribution:** the decomposition rule requires a behaviour part to rest "
    "on a cited execution, and the fixture tree records none, so neither part "
    "can be settled either way from what is on disk. Whether the harness is at "
    "fault or merely unexercised is exactly what is not established.",
    "- **confidence:** unresolved",
    "- **next check:** run the fixture harness against a malformed batch and "
    "record the exit code and the log line; a non-zero exit with a logged "
    "refusal settles both parts as `serves`.",
    "- **proposed act:** record one harness execution in the fixture tree.", "",
    "### `adr-review-fixture-roster-omits-a-gate` — the roster is missing a gate "
    "the tree carries",
    "- **subject:** `rule:assessment-row-is-the-measure-part`",
    "- **objective:** OBJ-4.1 — every gate the tree carries is on the roster",
    "- **evidence:** `fixtures/roster.md` — the roster lists three gates; the "
    "fixture tree carries four",
    "- **attribution:** the roster is hand-kept and the fixture tree gained a "
    "gate without a matching row, so the omission follows from the roster "
    "having no regenerator rather than from any decision about gates.",
    "- **confidence:** established",
    "- **next check:** re-count the roster rows against the fixture tree after "
    "the row lands; equal counts settle the part as `serves`.",
    "- **proposed act:** add the missing row to the fixture roster.", "",
    "## Revoke", "", "None this pass. Every disposition below is `keep`.", "",
    "- FIX-0001 — `keep` — dormant, and dormancy alone establishes nothing.", "",
    "## Keep", "",
    "- FIX-0002 / `rule:fixture-gate-runs` — still serves OBJ-1.", "",
    "## Coverage", "",
    "- **Objectives maturity read:** `forming`.",
    "- **Measures read, verbatim, beside the parts they were decomposed into.** "
    "OBJ-1: \"the fixture gate runs on every change\" (1 part). OBJ-2: \"the "
    "harness refuses a malformed batch and logs the refusal\" (2 parts, both "
    "behaviour). OBJ-3: \"the roster lists every gate and each gate runs in "
    "CI\" (2 parts). OBJ-4: \"every gate the tree carries is on the roster\" "
    "(1 part).",
    "- **Investigation 1** — objective: OBJ-2 — measure: \"the harness refuses "
    "a malformed batch and logs the refusal\" — question: does any recorded run "
    "show a refusal? — domain: fixture-harness — intended evidence: "
    "`fixtures/harness.md`, `fixtures/runs/`.", "",
    _assessment_table([
        ("OBJ-1", "OBJ-1.1 — the gate is on the roster", "resolved",
         "fixtures/roster.md", "fixture-gates", "serves", "—"),
        ("OBJ-2", "OBJ-2.1 — the harness refuses at runtime", "unavailable",
         "fixtures/runs/", "fixture-harness", "inconclusive",
         "adr-review-fixture-harness-unobserved"),
        ("OBJ-2", "OBJ-2.2 — the refusal is logged", "unavailable",
         "fixtures/runs/", "fixture-harness", "inconclusive", "—"),
        ("OBJ-3", "OBJ-3.1 — the roster lists every gate", "resolved",
         "fixtures/roster.md", "fixture-gates", "serves", "—"),
        ("OBJ-3", "OBJ-3.2 — each gate runs in CI", "unavailable",
         "fixtures/ci/", "fixture-gates", "inconclusive", "—"),
        ("OBJ-4", "OBJ-4.1 — every gate is on the roster", "resolved",
         "fixtures/roster.md", "fixture-gates", "gap",
         "adr-review-fixture-roster-omits-a-gate"),
    ]), "",
    "| objective | alignment rollup | signals that measured it "
    "| domains that measured it |",
    "|---|---|---|---|",
    "| OBJ-1 | serves | — | fixture-gates |",
    "| OBJ-2 | inconclusive | — | fixture-harness |",
    "| OBJ-3 | inconclusive | — | fixture-gates |",
    "| OBJ-4 | gap | — | fixture-gates |",
])


class WorkedReportTests(unittest.TestCase):
    """The worked report is admissible, and says both things honestly."""

    def setUp(self) -> None:
        self.report = rf.parse_report(date="2026-09-30",
                                      frontmatter=WORKED_FRONTMATTER,
                                      body=WORKED_BODY)
        rf.validate_structure(self.report)

    def test_the_worked_report_parses_and_validates(self):
        self.assertEqual(6, len(self.report.assessments))
        self.assertEqual(2, len(self.report.definitions))

    def test_one_measure_reaches_a_supported_conclusion(self):
        supported = [a for a in self.report.assessments if a.part == "OBJ-1.1"]
        self.assertEqual(1, len(supported))
        self.assertEqual("resolved", supported[0].evidence)
        self.assertEqual("serves", supported[0].conclusion)

    def test_one_measure_is_correctly_insufficient_evidence(self):
        insufficient = [a for a in self.report.assessments if a.part == "OBJ-2.1"]
        self.assertEqual(1, len(insufficient))
        self.assertEqual("unavailable", insufficient[0].evidence)
        self.assertEqual("inconclusive", insufficient[0].conclusion)

    def test_a_goal_can_be_measured_while_its_alignment_is_inconclusive(self):
        """OBJ-3: a STATIC part settles; a BEHAVIOUR part cannot. Two axes.

        OBJ-3.1 is a list check, so `resolved`+`serves` is honest evidence for
        it. OBJ-3.2 is a behaviour claim with no execution to read. The goal is
        therefore measured and its alignment is still inconclusive.
        """
        parts = [a for a in self.report.assessments if a.objective == "OBJ-3"]
        pairs = [(a.evidence, a.conclusion) for a in parts]
        self.assertEqual("measured", rf.measurement_outcome(pairs))
        self.assertEqual("inconclusive",
                         rf.alignment_rollup(a.conclusion for a in parts))

    def test_a_behaviour_only_goal_is_attempted_never_measured(self):
        """OBJ-2's parts are BOTH behaviour, so nothing discriminates.

        This is the regression for the defect the review caught: an earlier
        draft decomposed this measure into a part reading "the refusal is
        specified" at `resolved`+`serves`, which is the rule-existence gaming
        vector, and it made the rows say `measured` while the frontmatter said
        `attempted`.
        """
        parts = [a for a in self.report.assessments if a.objective == "OBJ-2"]
        pairs = [(a.evidence, a.conclusion) for a in parts]
        self.assertEqual("attempted", rf.measurement_outcome(pairs))
        for a in parts:
            self.assertEqual("unavailable", a.evidence)

    def test_no_part_reads_the_rule_existence_shape(self):
        """No part cites a specification as evidence for a behaviour claim."""
        for a in self.report.assessments:
            if a.locator.endswith(".md") and a.conclusion == "serves":
                self.assertNotIn("runtime", a.label)
                self.assertNotIn("refuses", a.label)

    def test_the_frontmatter_outcome_agrees_with_the_rows_for_every_goal(self):
        """The record must not say one thing in two places."""
        outcomes, _ = rf.measured_objectives(WORKED_FRONTMATTER)
        by_goal = {}
        for a in self.report.assessments:
            by_goal.setdefault(a.objective, []).append((a.evidence, a.conclusion))
        for goal, pairs in by_goal.items():
            self.assertEqual(rf.measurement_outcome(pairs),
                             outcomes[goal]["outcome"],
                             f"{goal}: frontmatter and rows disagree")

    def test_evidence_supports_one_measure_and_shows_a_gap_on_another(self):
        by_part = {a.part: a for a in self.report.assessments}
        self.assertEqual("serves", by_part["OBJ-1.1"].conclusion)
        self.assertEqual("gap", by_part["OBJ-4.1"].conclusion)
        self.assertEqual("resolved", by_part["OBJ-4.1"].evidence)

    def test_the_partial_evidence_never_reaches_serves(self):
        for a in self.report.assessments:
            if a.evidence in ("partial", "unavailable"):
                self.assertNotEqual("serves", a.conclusion)

    def test_the_uncertain_attribution_yields_no_revoke_finding(self):
        """A gap with unresolved attribution routes to Repair, never Revoke."""
        for finding in self.report.definitions:
            self.assertEqual("repair", finding.section)
        self.assertIn("**confidence:** unresolved", WORKED_BODY)
        revoke = WORKED_BODY[WORKED_BODY.index("## Revoke"):
                             WORKED_BODY.index("## Keep")]
        self.assertNotIn("###", revoke)

    def test_dormancy_alone_is_disposed_keep_and_never_revoked(self):
        revoke = WORKED_BODY[WORKED_BODY.index("## Revoke"):
                             WORKED_BODY.index("## Keep")]
        self.assertIn("dormancy alone establishes nothing", revoke)

    def test_an_objective_with_no_signal_ran_a_bounded_investigation(self):
        coverage = WORKED_BODY[WORKED_BODY.index("## Coverage"):]
        self.assertIn("**Investigation 1**", coverage)
        for field in ("objective:", "measure:", "question:", "domain:",
                      "intended evidence:"):
            self.assertIn(field, coverage)

    def test_the_coverage_finding_reference_defines_nothing(self):
        """The id in the assessment row's findings cell is a REFERENCE."""
        referenced = [a for a in self.report.assessments if a.findings]
        self.assertEqual(2, len(referenced))
        self.assertEqual(2, self.report.raised)
        self.assertEqual(
            {"adr-review-fixture-harness-unobserved",
             "adr-review-fixture-roster-omits-a-gate"},
            set(self.report.summary_row_ids))

    def test_the_frontmatter_records_both_measured_and_attempted(self):
        outcomes, shape = rf.measured_objectives(WORKED_FRONTMATTER)
        self.assertEqual("structured", shape)
        self.assertEqual("measured", outcomes["OBJ-1"]["outcome"])
        self.assertEqual("attempted", outcomes["OBJ-2"]["outcome"])
        self.assertIn("blocker", outcomes["OBJ-2"])
        self.assertIn("evidence", outcomes["OBJ-1"])

    def test_coverage_records_each_measure_verbatim_beside_its_parts(self):
        """rule:measure-decomposes-into-falsifiable-parts's postcondition needs a surface."""
        coverage = WORKED_BODY[WORKED_BODY.index("## Coverage"):]
        self.assertIn("Measures read, verbatim", coverage)
        for goal in ("OBJ-1:", "OBJ-2:", "OBJ-3:", "OBJ-4:"):
            self.assertIn(goal, coverage)


class TenGoalReadabilityTests(unittest.TestCase):
    """A ten-goal report stays admissible without dropping required evidence."""

    def test_ten_goals_with_two_parts_each_parse_and_validate(self):
        rows = []
        for n in range(1, 11):
            rows.append((f"OBJ-{n}", f"OBJ-{n}.1 — the specified half",
                         "resolved", f"fixtures/spec-{n}.md", "fixture-domain",
                         "serves", "—"))
            rows.append((f"OBJ-{n}", f"OBJ-{n}.2 — the observed half",
                         "unavailable", f"fixtures/runs-{n}/", "fixture-domain",
                         "inconclusive", "—"))
        body = "\n".join([
            "# Decision review — 2026-10-01", "",
            "## Coverage", "", _assessment_table(rows),
        ])
        # ADR-0109 `a-blocked-part-is-counted-in-its-own-right`: every
        # `.2` part above is blocked (`unavailable`/`inconclusive`), so each
        # needs its own part-level `measured_objectives` entry.
        blocked_entries = "\n".join(
            f"  - objective: OBJ-{n}\n    part: OBJ-{n}.2\n"
            f"    outcome: attempted\n    measure_digest: sha256:obj{n}-2\n"
            f"    blocker: no observed run recorded"
            for n in range(1, 11))
        report = rf.parse_report(
            date="2026-10-01",
            frontmatter=("type: adr-review\ndate: 2026-10-01\n"
                        "report_grammar: lifecycle\nmeasured_objectives:\n"
                        + blocked_entries + "\n"),
            body=body)
        rf.validate_structure(report)
        self.assertEqual(20, len(report.assessments))
        # Every goal is honestly inconclusive: the observed half is unavailable,
        # so no goal is inflated to `serves` by its specified half alone.
        for n in range(1, 11):
            parts = [a for a in report.assessments if a.objective == f"OBJ-{n}"]
            self.assertEqual("inconclusive",
                             rf.alignment_rollup(a.conclusion for a in parts))
        # Twenty rows plus two header lines: the table costs 22 lines for ten
        # goals, which is what keeps the advisory narrative target reachable.
        self.assertEqual(22, len(_assessment_table(rows).splitlines()))

    def test_numeric_ordering_puts_obj_2_before_obj_10(self):
        """A lexicographic sort would put OBJ-10 first. It must not."""
        ordered = sorted(("OBJ-2", "OBJ-10"),
                         key=lambda o: int(o.split("-")[1]))
        self.assertEqual(["OBJ-2", "OBJ-10"], ordered)


class MaturityBranchFixtureTests(unittest.TestCase):
    """The three populate-gate branches, pinned against the skill's own prose."""

    @classmethod
    def setUpClass(cls) -> None:
        skills = Path(__file__).resolve().parent.parent.parent / "skills"
        cls.text = (skills / "review-decisions" / "SKILL.md").read_text(
            encoding="utf-8")

    def test_missing_and_placeholder_stop_the_dependent_assessment(self):
        self.assertIn("missing", self.text)
        self.assertIn("placeholder", self.text)
        # The branch must say NO assessment rows at all — a `not-assessed` row
        # would still need an OBJ-N the file does not carry.
        self.assertRegex(self.text,
                         r"(?is)placeholder.{0,400}no assessment row")

    def test_exploring_reads_goals_as_questions(self):
        self.assertRegex(self.text, r"(?s)`exploring`.{0,400}not-assessed")

    def test_forming_and_settled_admit_the_full_vocabulary(self):
        self.assertRegex(self.text,
                         r"(?s)`forming`.{0,600}(full vocabulary|yardstick)")




# ── regressions for the internal review's seven findings ───────────────────

class InternalReviewRegressionTests(unittest.TestCase):
    """One test per finding the dev module's internal review confirmed."""

    FM = "type: adr-review\ndate: 2026-09-30\nreport_grammar: lifecycle\n"

    def _parse(self, rows):
        body = "\n".join(["# R", "", "## Coverage", "", _assessment_table(rows)])
        return rf.parse_report(date="2026-09-30", frontmatter=self.FM, body=body)

    def test_f1_a_row_written_from_the_shipped_template_parses(self):
        """The template's own placeholder shape must be admissible.

        Positive control for the refusal below: the prefix is what makes the
        difference, not some other property of the row.
        """
        template = (Path(__file__).resolve().parent.parent.parent
                    / "templates" / "adr-review-template.md").read_text()
        self.assertIn("| OBJ-N | OBJ-N.k — ", template)
        report = self._parse([("OBJ-1", "OBJ-1.1 — the gate is on the roster", "resolved",
                               "fixtures/x.txt", "d", "serves", "—")])
        self.assertEqual("OBJ-1.1", report.assessments[0].part)

    def test_f1_a_bare_label_with_no_part_id_is_refused(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            self._parse([("OBJ-1", "the gate runs", "resolved",
                          "fixtures/x.txt", "d", "serves", "—")])
        self.assertIn("OBJ-<n>.<k>", str(caught.exception))

    def test_f2_any_date_in_the_window_discharges_not_only_the_newest(self):
        measured = ("measured_objectives:\n  - objective: OBJ-1\n"
                    "    outcome: measured\n    pass: 1\n"
                    "    measure_digest: sha256:f2\n"
                    "    evidence: fixtures/x.txt\n")
        empty = "measured_objectives: []\n"
        state = rf.rotation_state(
            [("2026-09-07", measured), ("2026-09-08", empty),
             ("2026-09-09", empty)], ["OBJ-1"])
        self.assertTrue(state["OBJ-1"]["discharged"])

    def test_f2_a_window_with_no_outcome_at_all_stays_undischarged(self):
        """Positive control: `discharged` is not simply always True."""
        empty = "measured_objectives: []\n"
        state = rf.rotation_state(
            [("2026-09-07", empty), ("2026-09-08", empty)], ["OBJ-1"])
        self.assertFalse(state["OBJ-1"]["discharged"])

    def test_f3_the_strongest_outcome_on_one_date_wins(self):
        fm = ("measured_objectives:\n"
              "  - objective: OBJ-1\n    outcome: measured\n    pass: 1\n"
              "    measure_digest: sha256:f3\n"
              "    evidence: fixtures/x.txt\n"
              "  - objective: OBJ-1\n    outcome: attempted\n    pass: 2\n"
              "    measure_digest: sha256:f3\n"
              "    blocker: nothing to read\n")
        outcomes, shape = rf.measured_objectives(fm)
        self.assertEqual("structured", shape)
        self.assertEqual("measured", outcomes["OBJ-1"]["outcome"],
                         "a later pass's weaker outcome must not bury an "
                         "earlier pass's stronger one")

    def test_f3_a_later_stronger_outcome_still_wins(self):
        """Positive control: the merge is by strength, not by first-wins."""
        fm = ("measured_objectives:\n"
              "  - objective: OBJ-1\n    outcome: attempted\n    pass: 1\n"
              "    measure_digest: sha256:f3b\n"
              "    blocker: nothing to read\n"
              "  - objective: OBJ-1\n    outcome: measured\n    pass: 2\n"
              "    measure_digest: sha256:f3b\n"
              "    evidence: fixtures/x.txt\n")
        outcomes, _ = rf.measured_objectives(fm)
        self.assertEqual("measured", outcomes["OBJ-1"]["outcome"])

    def test_f4_a_blank_line_between_entries_does_not_truncate(self):
        fm = ("measured_objectives:\n"
              "  - objective: OBJ-1\n    outcome: measured\n    pass: 1\n"
              "    measure_digest: sha256:f4a\n"
              "    evidence: fixtures/x.txt\n"
              "\n"
              "  - objective: OBJ-2\n    outcome: attempted\n    pass: 1\n"
              "    measure_digest: sha256:f4b\n"
              "    blocker: nothing to read\n")
        outcomes, _ = rf.measured_objectives(fm)
        self.assertEqual({"OBJ-1", "OBJ-2"}, set(outcomes))

    def test_f4_an_entry_naming_no_objective_is_refused_not_dropped(self):
        fm = ("measured_objectives:\n"
              "  - outcome: measured\n    pass: 1\n    evidence: fixtures/x.txt\n")
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.measured_objectives(fm)
        self.assertIn("no `objective` field", str(caught.exception))

    def test_f5_a_part_filed_under_the_wrong_goal_is_refused(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            self._parse([("OBJ-2", "OBJ-1.3 — mismatched part", "resolved",
                          "fixtures/x.txt", "d", "serves", "—")])
        self.assertIn("goal numbers disagree", str(caught.exception))

    def test_f6_an_assessment_locator_refusal_names_the_assessment_row(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            self._parse([("OBJ-1", "OBJ-1.1 — the gate is on the roster", "resolved",
                          "../../etc/passwd", "d", "serves", "—")])
        message = str(caught.exception)
        self.assertIn("assessment row", message)
        self.assertNotIn("lifecycle record", message)

    def test_f7_the_template_entry_keys_match_what_the_reader_returns(self):
        """Every entry field the template documents is one the reader knows.

        Pinned as a MATCH rather than as literal strings, because the literal
        form let the template drift to `measure_version` while the reader
        required `measure_digest` — a report written from the shipped template
        was then hard-refused, and no test noticed.
        """
        template = (Path(__file__).resolve().parent.parent.parent
                    / "templates" / "adr-review-template.md").read_text()
        block = template[template.index("measured_objectives:"):]
        block = block[:block.index("---", 1)]
        documented = set(re.findall(r"^#?\s*[-#]?\s*([a-z_]+):", block, re.M))
        documented -= {"measured_objectives"}
        # Taken from the READER, not restated here: a literal set on this
        # side would drift exactly as the template's did.
        known = set(rf.ENTRY_FIELDS)
        self.assertTrue(documented, "the template documents no entry fields")
        self.assertEqual(set(), documented - known,
                         f"template documents fields the reader does not know: "
                         f"{sorted(documented - known)}")
        # The two the reader REFUSES an entry for must both be documented.
        self.assertIn("outcome", documented)
        self.assertIn("measure_digest", documented)

    def test_f7_the_shipped_template_frontmatter_is_admissible(self):
        """An unedited template must not hard-fail the reader.

        The placeholder block previously carried `OBJ-N` and
        `measured | attempted` as literal values, both outside their closed
        sets, so a pass that left it untouched was refused.
        """
        template = (Path(__file__).resolve().parent.parent.parent
                    / "templates" / "adr-review-template.md").read_text()
        frontmatter = re.match(r"\A---\n(.*?)\n---\n", template, re.S).group(1)
        outcomes, shape = rf.measured_objectives(frontmatter)
        self.assertEqual(({}, "empty"), (outcomes, shape))


# ── the three consistency checks ADR-0108 permits ──────────────────────────

def _matrix(rows):
    head = ("| objective | alignment rollup | signals that measured it "
            "| domains that measured it |")
    return "\n".join([head, "|---|---|---|---|"]
                     + ["| " + " | ".join(r) + " |" for r in rows])


class ConsistencyCheckTests(unittest.TestCase):
    """A record must not say two different things. NOT a truth check.

    These verify only that the report's own cells agree with each other.
    Whether an assessment is TRUE is the architect's judgment and no test
    here asserts on it — a checker claiming otherwise would restore the
    false green the whole grammar exists to remove.
    """

    FM_MEASURED = ("type: adr-review\ndate: 2026-09-30\n"
                   "report_grammar: lifecycle\nmeasured_objectives:\n"
                   "  - objective: OBJ-1\n    outcome: measured\n"
                   "    pass: 1\n    measure_digest: sha256:fm-measured\n"
                   "    evidence: a/p.md\n")

    def _report(self, rows, matrix_rows, frontmatter=None):
        body = "\n".join(["# R", "", "## Coverage", "",
                          _assessment_table(rows), "", _matrix(matrix_rows)])
        return rf.parse_report(
            date="2026-09-30",
            frontmatter=frontmatter or self.FM_MEASURED, body=body)

    ROW_SERVES = ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "d",
                  "serves", "—")

    def test_a_matrix_rollup_agreeing_with_its_parts_passes(self):
        """Positive control for the refusal below."""
        report = self._report([self.ROW_SERVES], [("OBJ-1", "serves", "—", "d")])
        self.assertIsNone(rf.validate_structure(report))

    def test_a_matrix_rollup_disagreeing_with_its_parts_is_refused(self):
        report = self._report([self.ROW_SERVES], [("OBJ-1", "gap", "—", "d")])
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.validate_structure(report)
        self.assertIn("assessment row(s) derive", str(caught.exception))

    def test_a_frontmatter_outcome_agreeing_with_the_rows_passes(self):
        """Positive control: `resolved`+`serves` discriminates, so measured."""
        report = self._report([self.ROW_SERVES], [("OBJ-1", "serves", "—", "d")])
        self.assertIsNone(rf.validate_structure(report))

    def test_a_frontmatter_outcome_disagreeing_with_the_rows_is_refused(self):
        """The exact defect the external review caught in the worked report.

        The rows discriminate, so the goal is `measured`; the frontmatter
        claims `attempted`. One record, two answers, and the rotation reads
        the wrong one.
        """
        attempted = ("type: adr-review\ndate: 2026-09-30\n"
                     "report_grammar: lifecycle\nmeasured_objectives:\n"
                     "  - objective: OBJ-1\n    outcome: attempted\n"
                     "    pass: 1\n    measure_digest: sha256:fm-attempted\n"
                     "    blocker: nothing to read\n")
        report = self._report([self.ROW_SERVES],
                              [("OBJ-1", "serves", "—", "d")], attempted)
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.validate_structure(report)
        message = str(caught.exception)
        self.assertIn("measured_objectives", message)
        self.assertIn("derived from the parts", message)

    def test_a_goal_claimed_measured_with_no_rows_is_refused(self):
        """Credit without measurement is the shape ADR-0108 exists to remove.

        Both derived values are DEFINED on the empty set — a goal with no
        parts is `not-assessed` — so the comparison is available and a
        frontmatter entry claiming `measured` for a goal this pass never
        decomposed is caught rather than skipped.
        """
        body = "\n".join(["# R", "", "## Coverage", "",
                          _assessment_table([("OBJ-2", "OBJ-2.1 — a claim",
                                              "resolved", "a/p.md", "d",
                                              "serves", "—")])])
        report = rf.parse_report(date="2026-09-30",
                                 frontmatter=self.FM_MEASURED, body=body)
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.validate_structure(report)
        self.assertIn("OBJ-1", str(caught.exception))

    def test_a_goal_recorded_not_assessed_with_no_rows_passes(self):
        """Positive control: `not-assessed` is what no parts derive."""
        fm = ("type: adr-review\ndate: 2026-09-30\n"
              "report_grammar: lifecycle\nmeasured_objectives:\n"
              "  - objective: OBJ-1\n    outcome: not-assessed\n"
              "    pass: 1\n    measure_digest: sha256:fm-not-assessed\n")
        body = "\n".join(["# R", "", "## Coverage", "",
                          _assessment_table([("OBJ-2", "OBJ-2.1 — a claim",
                                              "resolved", "a/p.md", "d",
                                              "serves", "—")])])
        report = rf.parse_report(date="2026-09-30", frontmatter=fm, body=body)
        self.assertIsNone(rf.validate_structure(report))


class NullBaselineGuidanceTests(unittest.TestCase):
    """The skill declines the ADR's null-baseline `gap` permission.

    `partial`+`gap` DISCRIMINATES, so recording it would make the goal
    `measured`, discharge the rotation and reset the strike count on the same
    null baseline every pass — and the three-strike escalation could never
    fire for the goal it exists to rescue.
    """

    @classmethod
    def setUpClass(cls) -> None:
        skills = Path(__file__).resolve().parent.parent.parent / "skills"
        cls.text = (skills / "review-decisions" / "SKILL.md").read_text(
            encoding="utf-8")

    def test_the_arithmetic_that_motivates_the_guidance_holds(self):
        self.assertIn(("partial", "gap"), rf.DISCRIMINATING_PAIRS)
        self.assertEqual("measured",
                         rf.measurement_outcome([("partial", "gap")]))
        self.assertEqual("attempted",
                         rf.measurement_outcome([("partial", "inconclusive")]))

    def test_the_skill_prescribes_partial_inconclusive_for_a_null_baseline(self):
        self.assertIn("Record a null baseline as `partial` + `inconclusive`",
                      self.text)

    def test_the_skill_names_the_one_case_where_gap_is_still_right(self):
        """A SEPARATE measurability part, not the trend row, is what may reach
        `gap`. Asserted by the claims the guidance has to make rather than by
        one phrasing of them, so rewording the paragraph does not fail here
        while dropping a claim still does.
        """
        para = next(line for line in self.text.splitlines()
                    if "WITHDRAWN" in line)
        self.assertRegex(para, r"(?i)\bdeclared measure\b")
        self.assertRegex(para, r"(?i)measurab")
        self.assertRegex(para, r"(?i)\bDISTINCT part\b|\bSEPARATE part\b")
        self.assertRegex(para, r"`resolved`.*`gap`")
        # And the trend row itself is still barred from `gap`.
        self.assertRegex(para, r"(?i)\bWITHDRAWN\b")


class MissingBaselineAdmissibleFixtureTests(unittest.TestCase):
    """ADR-0109 `a-missing-baseline-blocks-rather-than-concludes`, exercised
    through the actual grammar rather than through skill prose.

    A trend part whose baseline is null is `partial` evidence reaching
    `inconclusive` — never `gap` — and a SEPARATE measurability part may
    still reach `gap` honestly, because direct observation of the missing
    instrument is `resolved` evidence.
    """

    FM = ("type: adr-review\ndate: 2026-09-30\n"
          "report_grammar: lifecycle\nmeasured_objectives:\n"
          "  - objective: OBJ-9\n    part: OBJ-9.1\n"
          "    outcome: attempted\n    measure_digest: sha256:obj9-1\n"
          "    blocker: the trend baseline is null\n"
          "  - objective: OBJ-9\n    part: OBJ-9.2\n"
          "    outcome: measured\n    measure_digest: sha256:obj9-2\n"
          "    evidence: fixtures/instrument.md\n")

    def _report(self, rows):
        body = "\n".join(["# R", "", "## Coverage", "", _assessment_table(rows)])
        report = rf.parse_report(date="2026-09-30", frontmatter=self.FM, body=body)
        rf.validate_structure(report)
        return report

    def test_a_missing_trend_baseline_stays_attempted_inconclusive(self):
        report = self._report([
            ("OBJ-9", "OBJ-9.1 — the metric trends downward", "partial",
             "fixtures/trend.md", "fixture-domain", "inconclusive", "—"),
            ("OBJ-9", "OBJ-9.2 — the baseline is obtainable", "resolved",
             "fixtures/instrument.md", "fixture-domain", "gap", "—"),
        ])
        trend = next(a for a in report.assessments if a.part == "OBJ-9.1")
        self.assertEqual(("partial", "inconclusive"),
                         (trend.evidence, trend.conclusion))
        self.assertEqual(
            "attempted", rf.measurement_outcome([(trend.evidence, trend.conclusion)]))

    def test_a_measurability_part_earns_a_justified_gap_on_its_own_row(self):
        """The SEPARATE measurability part, not the trend row, carries the
        `gap` — and it alone is enough to make the goal `measured`."""
        report = self._report([
            ("OBJ-9", "OBJ-9.1 — the metric trends downward", "partial",
             "fixtures/trend.md", "fixture-domain", "inconclusive", "—"),
            ("OBJ-9", "OBJ-9.2 — the baseline is obtainable", "resolved",
             "fixtures/instrument.md", "fixture-domain", "gap", "—"),
        ])
        measurability = next(a for a in report.assessments
                             if a.part == "OBJ-9.2")
        self.assertEqual(("resolved", "gap"),
                         (measurability.evidence, measurability.conclusion))
        pairs = [(a.evidence, a.conclusion) for a in report.assessments]
        self.assertEqual("measured", rf.measurement_outcome(pairs))
        # `gap` outranks every other conclusion in the alignment rollup, so
        # the goal's rollup is `gap` even though its measurement OUTCOME is
        # `measured` — the two axes the rule keeps apart (ADR-0108
        # `assessment-row-is-the-measure-part`).
        self.assertEqual(
            "gap", rf.alignment_rollup(a.conclusion for a in report.assessments))


class CrashLaneTests(unittest.TestCase):
    """Malformed input must reach the DOCUMENT lane, never the crash lane.

    The repo's exit-code contract is `0` clean, `1` findings with JSON on
    stdout, and non-zero with empty/unparseable stdout = crash. A hand-kept
    report that pushes the regenerator into the crash lane sends a human to
    the filesystem instead of to the report, and `check-drift` reads it as an
    environment failure rather than a finding. Both inputs below did exactly
    that before the fix.
    """

    def _refusal(self, body):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            report = rf.parse_report(
                date="2026-09-30",
                frontmatter=("type: adr-review\ndate: 2026-09-30\n"
                             "report_grammar: lifecycle\n"),
                body=body)
            rf.validate_structure(report)
        return str(caught.exception)

    def test_an_unbounded_objective_digit_run_is_refused_not_crashed(self):
        """4301 digits exceeds CPython's int() conversion limit."""
        big = "OBJ-" + "9" * 4301
        body = "\n".join([
            "# R", "", "## Coverage", "",
            _assessment_table([
                (big, f"{big}.1 — a claim", "resolved", "a/p.md", "d",
                 "serves", "—"),
                ("OBJ-2", "OBJ-2.1 — a claim", "resolved", "a/p.md", "d",
                 "serves", "—")]),
        ])
        message = self._refusal(body)
        self.assertIn("`OBJ-N`", message)
        # The oversized cell renders through redact(), so the refusal cannot
        # be used to blast an unbounded value into a log or a report.
        self.assertIn("truncated from", message)
        self.assertLess(len(message), 500)

    def test_a_bounded_objective_id_is_still_accepted(self):
        """Positive control: four digits is inside the bound."""
        body = "\n".join([
            "# R", "", "## Coverage", "",
            _assessment_table([("OBJ-9999", "OBJ-9999.1 — a claim", "resolved",
                                "a/p.md", "d", "serves", "—")]),
        ])
        report = rf.parse_report(
            date="2026-09-30",
            frontmatter=("type: adr-review\ndate: 2026-09-30\n"
                         "report_grammar: lifecycle\n"),
            body=body)
        self.assertEqual("OBJ-9999", report.assessments[0].objective)

    def test_a_short_matrix_row_is_refused_not_crashed(self):
        """A one-cell row raised IndexError before the cell-count guard."""
        body = "\n".join([
            "# R", "", "## Coverage", "",
            "| objective | alignment rollup | signals that measured it "
            "| domains that measured it |",
            "|---|---|---|---|",
            "| OBJ-1 |",
        ])
        self.assertIn("carries 1 cells", self._refusal(body))

    def test_a_well_formed_matrix_row_is_still_accepted(self):
        """Positive control for the cell-count guard."""
        body = "\n".join([
            "# R", "", "## Coverage", "",
            _assessment_table([("OBJ-1", "OBJ-1.1 — a claim", "resolved",
                                "a/p.md", "d", "serves", "—")]), "",
            _matrix([("OBJ-1", "serves", "—", "d")]),
        ])
        report = rf.parse_report(
            date="2026-09-30",
            frontmatter=("type: adr-review\ndate: 2026-09-30\n"
                         "report_grammar: lifecycle\n"),
            body=body)
        self.assertEqual({"OBJ-1": "serves"}, report.matrix_rollups)

    def test_an_unclosed_flat_list_is_refused_not_read_as_zero_goals(self):
        message = ""
        try:
            rf.measured_objectives("measured_objectives: [\n  OBJ-1,\n]\n")
        except rf.ReviewFindingsError as caught:
            message = str(caught)
        self.assertIn("does not close on the same line", message)

    def test_a_single_line_flat_list_is_still_read(self):
        """Positive control: the shape the 2026-09-08 report actually uses."""
        outcomes, shape = rf.measured_objectives(
            "measured_objectives: [OBJ-1, OBJ-2]\n")
        self.assertEqual("flat", shape)
        self.assertEqual({"OBJ-1", "OBJ-2"}, set(outcomes))

    def test_an_entry_with_no_outcome_is_refused(self):
        message = ""
        try:
            rf.measured_objectives(
                "measured_objectives:\n  - objective: OBJ-1\n    pass: 1\n")
        except rf.ReviewFindingsError as caught:
            message = str(caught)
        self.assertIn("no `outcome` field", message)

    def test_a_goal_claimed_with_no_assessment_rows_is_refused(self):
        """Credit without measurement — the shape ADR-0108 exists to remove."""
        body = "\n".join([
            "# R", "", "## Coverage", "",
            _matrix([("OBJ-1", "serves", "—", "d")]),
        ])
        self.assertIn("assessment row(s) derive", self._refusal(body))


class SurvivingMutantTests(unittest.TestCase):
    """Guards a mutation run showed no test constrained.

    Each test names its own refusal message rather than merely asserting that
    something was raised, so a neighbouring refusal cannot satisfy it.
    """

    FM = ("type: adr-review\ndate: 2026-09-30\n"
          "report_grammar: lifecycle\n")

    def _refuse(self, body, frontmatter=None):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            report = rf.parse_report(date="2026-09-30",
                                     frontmatter=frontmatter or self.FM,
                                     body=body)
            rf.validate_structure(report)
        return str(caught.exception)

    def _coverage(self, *blocks):
        return "\n".join(["# R", "", "## Coverage", ""] + list(blocks))

    ROW = ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "d", "serves",
           "—")

    # S1 — the unanchored flat-list pattern renamed a goal.

    def test_an_over_long_flat_id_is_not_truncated_into_another_goal(self):
        outcomes, _ = rf.measured_objectives("measured_objectives: [OBJ-99999]")
        self.assertNotIn("OBJ-9999", outcomes,
                         "a five-digit id must not read as its four-digit "
                         "prefix — that discharges a goal nobody named")

    def test_a_four_digit_flat_id_is_still_read(self):
        """Positive control for the anchor."""
        outcomes, shape = rf.measured_objectives(
            "measured_objectives: [OBJ-9999]")
        self.assertEqual("flat", shape)
        self.assertEqual(["OBJ-9999"], list(outcomes))

    # S2 — the matrix had no orphan-row trap.

    def test_a_mistyped_matrix_header_refuses_rather_than_drops(self):
        body = self._coverage(
            "| objective | alignment rolup | signals that measured it "
            "| domains that measured it |",
            "|---|---|---|---|",
            "| OBJ-1 | serves | — | d |")
        self.assertIn("silently disables the rollup check", self._refuse(body))

    def test_the_same_row_under_the_real_matrix_header_is_the_control(self):
        body = self._coverage(_assessment_table([self.ROW]), "",
                              _matrix([("OBJ-1", "serves", "—", "d")]))
        report = rf.parse_report(date="2026-09-30", frontmatter=self.FM,
                                 body=body)
        self.assertEqual({"OBJ-1": "serves"}, report.matrix_rollups)

    # S3 — four matrix guards and three frontmatter refusals had no test.

    def test_a_second_matrix_is_refused(self):
        m = _matrix([("OBJ-1", "serves", "—", "d")])
        body = self._coverage(_assessment_table([self.ROW]), "", m, "", m)
        self.assertIn("second per-goal matrix", self._refuse(body))

    def test_a_matrix_outside_coverage_is_refused(self):
        body = "\n".join(["# R", "", "## Keep", "",
                          _matrix([("OBJ-1", "serves", "—", "d")])])
        self.assertIn("matrix lives under", self._refuse(body))

    def test_a_matrix_objective_outside_the_grammar_is_refused(self):
        body = self._coverage(_matrix([("OBJ_1", "serves", "—", "d")]))
        self.assertIn("`OBJ-N` grammar", self._refuse(body))

    def test_a_matrix_rollup_outside_the_vocabulary_is_refused(self):
        body = self._coverage(_matrix([("OBJ-1", "servez", "—", "d")]))
        self.assertIn("renders the alignment rollup", self._refuse(body))

    def test_a_short_assessment_row_is_refused_on_its_cell_count(self):
        body = self._coverage(
            "| " + " | ".join(rf.ASSESSMENT_HEADER) + " |",
            "|" + "---|" * len(rf.ASSESSMENT_HEADER),
            "| OBJ-1 | OBJ-1.1 — a claim | resolved | a/p.md | d | serves |")
        self.assertIn("cells", self._refuse(body))

    def test_a_frontmatter_objective_outside_the_grammar_is_refused(self):
        message = ""
        try:
            rf.measured_objectives(
                "measured_objectives:\n  - objective: obj-1\n"
                "    outcome: measured\n")
        except rf.ReviewFindingsError as caught:
            message = str(caught)
        self.assertIn("`OBJ-N` grammar", message)

    def test_a_frontmatter_outcome_outside_the_closed_set_is_refused(self):
        message = ""
        try:
            rf.measured_objectives(
                "measured_objectives:\n  - objective: OBJ-1\n"
                "    outcome: measurd\n")
        except rf.ReviewFindingsError as caught:
            message = str(caught)
        self.assertIn("outside the closed set", message)

    def test_an_unrecognised_scalar_is_refused_not_read_as_empty(self):
        message = ""
        try:
            rf.measured_objectives("measured_objectives: OBJ-1, OBJ-2\n")
        except rf.ReviewFindingsError as caught:
            message = str(caught)
        self.assertIn("outside the shapes this reader admits", message)

    def test_a_bracketed_empty_list_is_still_the_positive_record_of_zero(self):
        """Positive control for the refusal above."""
        outcomes, shape = rf.measured_objectives("measured_objectives: []\n")
        self.assertEqual(({}, "empty"), (outcomes, shape))

    # S4 — discriminators that a neighbouring refusal would also satisfy.

    def test_the_illegal_pair_refusal_names_the_pair_rule(self):
        body = self._coverage(_assessment_table([
            ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "d",
             "inconclusive", "—")]))
        message = self._refuse(body)
        self.assertIn("resolved", message)
        self.assertIn("inconclusive", message)

    def test_a_legal_pair_is_the_control_for_that_refusal(self):
        body = self._coverage(_assessment_table([self.ROW]))
        report = rf.parse_report(date="2026-09-30", frontmatter=self.FM,
                                 body=body)
        self.assertEqual(1, len(report.assessments))
if __name__ == "__main__":
    unittest.main()
