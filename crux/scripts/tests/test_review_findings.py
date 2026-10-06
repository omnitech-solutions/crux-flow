"""Conformance suite for `review_findings.py`, the decision-review finding grammar.

Every expected result here is derived by hand from the decision that fixed the
grammar — never by calling the code under test. A test that computes its
expectation from the parser validates the parser against itself and passes on
a parser that is wrong in both places.

Every refusal case carries a PAIRED POSITIVE CONTROL: the same fixture with
the one offending detail repaired, asserted to succeed. An "it refused"
assertion with no control is a false green, because a fixture that would have
been refused for an unrelated reason refuses just as loudly.

Reads `crux/scripts/` and nothing else — no `bionic/` tree, no live review
report — so the suite passes unchanged against the crux-only staged artifact.

Stdlib only. Run:
  uv run python3 -m unittest discover -s crux/scripts/tests -p test_review_findings.py -v
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parents[1]

# `_dev_surface` is a sibling test-dir helper, not a `crux.scripts` module —
# ADR-0108's preservation test reads two dated reports under `bionic/adrs/
# reviews/`, a dev-only surface that does not exist in the crux-only staged
# artifact `sync.sh` runs this suite against. `require_dev_surface` is the
# sanctioned guard (see `test_arch_verdicts.py`): skip in a staged artifact,
# fail loudly in dev if the surface is unexpectedly missing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dev_surface import require_dev_surface  # noqa: E402


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE execution: `dataclasses` resolves a frozen dataclass's
    # own module out of `sys.modules`, and an unregistered module fails there
    # rather than at the import.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rf = _load("review_findings_under_test", "review_findings.py")


# ── fixture builders ──────────────────────────────────────────────────────
#
# These compose report text. They call nothing in the module under test, so a
# fixture cannot inherit the parser's opinion about its own shape.

LIFECYCLE_FM = ('type: adr-review\ndate: {date}\n'
                'report_grammar: lifecycle\ndismissed: []')
LEGACY_FM = 'type: adr-review\ndate: {date}\ndismissed: []'


def summary_table(rows) -> str:
    """`rows` is a sequence of `(finding id, section)` pairs."""
    out = ["| finding id | section | objective | proposed act | size |",
           "|---|---|---|---|---|"]
    for fid, section in rows:
        out.append(f"| `{fid}` | {section} | OBJ-1 | write the thing | patch |")
    return "\n".join(out)


def record_table(rows) -> str:
    """`rows` is a sequence of `(source date, id, pass, event, locator)`."""
    out = ["| source report date | finding id | pass | event | locator |",
           "|---|---|---|---|---|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def assessment_table(rows) -> str:
    """`rows` is `(objective, measure, evidence, locator, domains,
    conclusion, findings)` tuples — the ADR-0108 seven-column table."""
    out = ["| objective | measure | evidence | locator | domains | "
           "conclusion | findings |",
           "|---|---|---|---|---|---|---|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def entry(fid: str, *extra: str) -> str:
    head = [f"### `{fid}` — one line about the gap",
            "- **subject:** ADR-0001",
            "- **objective:** OBJ-1 — the goal"]
    return "\n".join(head + list(extra))


def line_of(body: str, needle: str) -> int:
    """The 1-based line number of the first line holding `needle`.

    Deliberately uses `str.split("\\n")` rather than the module's own
    `split_lines`, so a line-number expectation is derived independently of the
    splitter the parser uses.
    """
    for number, line in enumerate(body.split("\n"), start=1):
        if needle in line:
            return number
    raise AssertionError(f"fixture carries no line holding {needle!r}")


class ReviewFindingsTestCase(unittest.TestCase):
    def parse(self, *, date, body, frontmatter=None, line_offset=0):
        if frontmatter is None:
            frontmatter = LIFECYCLE_FM.format(date=date)
        return rf.parse_report(date=date, frontmatter=frontmatter, body=body,
                               line_offset=line_offset)

    def refusal(self, callable_, *args, **kwargs) -> str:
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            callable_(*args, **kwargs)
        return caught.exception.problem


# ── V7 + outcome 7: the discriminator and the frozen legacy count ─────────

class DiscriminatorTests(ReviewFindingsTestCase):
    """Outcome 7. The `report_grammar` key, and what its absence means."""

    LEGACY_BODY = "\n".join([
        "# Decision review — 2026-09-08",
        "",
        "## Propose",
        "",
        entry("adr-review-alpha-one"),
        "",
        "## Repair",
        "",
        entry("adr-review-beta-two"),
        entry("adr-review-gamma-three"),
        "",
        "## Coverage",
        "",
        "The 2026-09-07 pass raised adr-review-delta-four and it now stands "
        "resolved, alongside adr-review-epsilon-five.",
    ])

    def test_legacy_count_is_the_token_set_not_the_definition_set(self):
        """V7. The frozen rule counts every token; the definitions are fewer.

        Derived by hand: the body carries five distinct `adr-review-` tokens —
        alpha-one, beta-two, gamma-three, delta-four, epsilon-five — and three
        `### ` definitions. A legacy report reports 5, and the SAME body read
        under the lifecycle grammar reports 3. The pair is the control: two
        counting rules that agreed on this body would prove nothing.
        """
        legacy = self.parse(date="2026-09-08", body=self.LEGACY_BODY,
                            frontmatter=LEGACY_FM.format(date="2026-09-08"))
        self.assertTrue(legacy.is_legacy)
        self.assertIsNone(legacy.grammar)
        self.assertEqual(legacy.raised, 5)
        self.assertEqual(
            legacy.legacy_ids,
            frozenset({"adr-review-alpha-one", "adr-review-beta-two",
                       "adr-review-gamma-three", "adr-review-delta-four",
                       "adr-review-epsilon-five"}))

        lifecycle = self.parse(date="2026-09-08", body=self.LEGACY_BODY)
        self.assertFalse(lifecycle.is_legacy)
        self.assertEqual(lifecycle.raised, 3)

    def test_legacy_coverage_prose_yields_no_record_and_no_note(self):
        """V7. Historical Coverage prose is never a confirmed lifecycle event."""
        legacy = self.parse(date="2026-09-08", body=self.LEGACY_BODY,
                            frontmatter=LEGACY_FM.format(date="2026-09-08"))
        self.assertEqual(legacy.records, ())
        self.assertEqual(legacy.notes, ())
        self.assertEqual(legacy.definitions, ())

    def test_the_legacy_token_regex_has_exactly_one_copy(self):
        """Outcome 7 freezes the count, so the pattern may not drift.

        It cannot drift if it exists once. `generate-reviews-index.py` used to
        carry its own `FINDING_TOKEN` beside this module's `LEGACY_TOKEN`, and
        a twin comparison over two copies can only find a divergence, never a
        shared error — the lesson `md_fences.py` was extracted for. So this row
        asserts the stronger property: the consumer defines no second copy, it
        imports this module, and the only spelling of the pattern in the
        regenerator is the import.
        """
        source = (SCRIPTS / "generate-reviews-index.py").read_text(encoding="utf-8")
        self.assertNotRegex(
            source, r"(?m)^FINDING_TOKEN\s*=",
            "generate-reviews-index.py must not redefine the legacy token regex")
        self.assertRegex(
            source, r"(?m)^import review_findings\b",
            "generate-reviews-index.py reads the grammar through this module")
        self.assertNotIn(
            rf.LEGACY_TOKEN.pattern, source,
            "the frozen legacy pattern is spelled once, in review_findings.py")
        # And the dead name is not kept alive in prose either. `FINDING_TOKEN`
        # names nothing in the tree, so a comment in THIS module that spells it
        # sends a reader looking for a constant that does not exist. The
        # incident belongs where it is explained — this test's own docstring —
        # never in the module the reader is trying to read.
        self.assertNotIn(
            "FINDING_TOKEN",
            (SCRIPTS / "review_findings.py").read_text(encoding="utf-8"),
            "review_findings.py names no dead FINDING_TOKEN constant")

    def test_absent_discriminator_after_the_boundary_is_refused(self):
        problem = self.refusal(
            self.parse, date="2026-09-09", body=self.LEGACY_BODY,
            frontmatter=LEGACY_FM.format(date="2026-09-09"))
        self.assertIn("report_grammar", problem)
        self.assertIn("2026-09-08", problem)

    def test_absent_discriminator_on_the_boundary_date_is_the_control(self):
        """The paired control: one day earlier, the same body reads as legacy."""
        report = self.parse(date="2026-09-08", body=self.LEGACY_BODY,
                            frontmatter=LEGACY_FM.format(date="2026-09-08"))
        self.assertTrue(report.is_legacy)
        self.assertEqual(report.raised, 5)

    def test_unrecognised_discriminator_value_is_refused(self):
        problem = self.refusal(
            self.parse, date="2026-09-10", body=self.LEGACY_BODY,
            frontmatter='type: adr-review\ndate: 2026-09-10\n'
                        'report_grammar: lifecycle-v2\ndismissed: []')
        self.assertIn("lifecycle-v2", problem)
        self.assertIn("report_grammar", problem)

    def test_recognised_discriminator_value_is_the_control(self):
        report = self.parse(date="2026-09-10", body=self.LEGACY_BODY)
        self.assertEqual(report.grammar, rf.LIFECYCLE)
        self.assertFalse(report.is_legacy)

    def test_a_date_outside_the_iso_grammar_is_refused(self):
        problem = self.refusal(self.parse, date="2026-9-9", body="# x")
        self.assertIn("2026-9-9", problem)
        self.assertIn("calendar date", problem)

    def test_an_iso_date_is_the_control(self):
        self.assertEqual(self.parse(date="2026-09-09", body="# x").date,
                         "2026-09-09")


# ── V1 + outcome 1: definition versus reference ──────────────────────────

class DefinitionTests(ReviewFindingsTestCase):
    """Outcome 1. What defines a finding, and what merely names one."""

    def test_a_definition_repeated_in_summary_and_coverage_counts_once(self):
        """V1. One id, three occurrences, one definition.

        Derived by hand: `adr-review-one-only` occurs in the summary table, in
        its `### ` heading, and in a lifecycle record under Coverage. Under
        outcome 1 exactly one of those is a definition, so `raised` is 1 — while
        the legacy token rule over the same body would answer 1 as well, which
        is why the paired assertion below adds a second id that occurs ONLY as
        a reference and moves the two rules apart.
        """
        body = "\n".join([
            "# Decision review — 2026-09-10",
            "",
            summary_table([("adr-review-one-only", "Repair")]),
            "",
            "## Repair",
            "",
            entry("adr-review-one-only"),
            "",
            "## Coverage",
            "",
            record_table([("2026-09-10", "`adr-review-one-only`", 1,
                           "re-verified", "crux/scripts/review_findings.py")]),
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(report.raised, 1)
        self.assertEqual([d.finding_id for d in report.definitions],
                         ["adr-review-one-only"])
        self.assertEqual(report.summary_row_ids, ("adr-review-one-only",))
        rf.validate_structure(report)

    def test_a_heading_carrying_no_finding_id_is_refused(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Repair", "",
            "### A heading with no id at all",
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("no finding id", problem)

    def test_a_heading_carrying_two_finding_ids_is_refused(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Repair", "",
            "### `adr-review-one` and `adr-review-two` together",
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("2 finding ids", problem)

    def test_a_heading_carrying_one_finding_id_is_the_control(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Repair", "",
            "### `adr-review-one` on its own",
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(report.raised, 1)

    def test_a_deeper_heading_inside_an_entry_defines_nothing(self):
        """Outcome 1: a `#### ` heading is neither a definition nor a refusal.

        The paired control below is the same two-id heading at `### `, which
        IS refused — so this row cannot pass by the heading being ignored for
        some unrelated reason.
        """
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-outer", "Repair")]), "",
            "## Repair", "",
            entry("adr-review-outer"),
            "#### `adr-review-inner` and `adr-review-second` in a sub-heading",
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual([d.finding_id for d in report.definitions],
                         ["adr-review-outer"])
        rf.validate_structure(report)

        promoted = body.replace("#### `adr-review-inner`", "### `adr-review-inner`")
        problem = self.refusal(self.parse, date="2026-09-10", body=promoted)
        self.assertIn("2 finding ids", problem)

    def test_a_heading_outside_a_finding_section_defines_nothing(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "",
            "### `adr-review-not-a-finding` — a coverage sub-heading",
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(report.definitions, ())
        self.assertEqual(report.raised, 0)

    def test_one_id_defined_twice_in_one_report_is_refused(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Propose", "", entry("adr-review-twice"), "",
            "## Repair", "", entry("adr-review-twice"),
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("adr-review-twice", problem)
        self.assertIn("defined twice", problem)

    def test_two_distinct_ids_in_one_report_are_the_control(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Propose", "", entry("adr-review-once"), "",
            "## Repair", "", entry("adr-review-other"),
        ])
        self.assertEqual(self.parse(date="2026-09-10", body=body).raised, 2)

    def test_one_id_defined_by_two_reports_is_refused(self):
        first = self.parse(date="2026-09-10", body="\n".join([
            "# a", "", "## Propose", "", entry("adr-review-shared")]))
        second = self.parse(date="2026-09-11", body="\n".join([
            "# b", "", "## Repair", "", entry("adr-review-shared")]))
        problem = self.refusal(rf.standing_by_finding, [first, second])
        self.assertIn("adr-review-shared", problem)
        self.assertIn("defined twice", problem)

    def test_two_reports_defining_distinct_ids_are_the_control(self):
        first = self.parse(date="2026-09-10", body="\n".join([
            "# a", "", "## Propose", "", entry("adr-review-first-one")]))
        second = self.parse(date="2026-09-11", body="\n".join([
            "# b", "", "## Repair", "", entry("adr-review-second-one")]))
        self.assertEqual(
            rf.standing_by_finding([first, second]),
            {"adr-review-first-one": "open", "adr-review-second-one": "open"})


# ── outcome 1: the summary-table postcondition ───────────────────────────

class SummaryTableTests(ReviewFindingsTestCase):

    def test_repeated_summary_row_ids_are_refused(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-dup", "Repair"),
                           ("adr-review-dup", "Propose")]), "",
            "## Repair", "", entry("adr-review-dup"),
        ])
        report = self.parse(date="2026-09-10", body=body)
        problem = self.refusal(rf.validate_structure, report)
        self.assertIn("pairwise distinct", problem)
        self.assertIn("adr-review-dup", problem)

    def test_equal_counts_with_unequal_sets_are_refused(self):
        """Outcome 1: equal counts alone do not satisfy the postcondition.

        Derived by hand: two summary rows, two definitions, and one id in each
        operand the other lacks. A count comparison passes this fixture; the
        set comparison the decision requires refuses it.
        """
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-in-table", "Repair"),
                           ("adr-review-in-both", "Repair")]), "",
            "## Repair", "",
            entry("adr-review-in-both"), "",
            entry("adr-review-in-sections"),
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(len(report.summary_row_ids), len(report.definitions))
        problem = self.refusal(rf.validate_structure, report)
        self.assertIn("adr-review-in-table", problem)
        self.assertIn("adr-review-in-sections", problem)

    def test_equal_sets_are_the_control(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-in-both", "Repair"),
                           ("adr-review-also-both", "Repair")]), "",
            "## Repair", "",
            entry("adr-review-in-both"), "",
            entry("adr-review-also-both"),
        ])
        self.assertIsNone(rf.validate_structure(self.parse(
            date="2026-09-10", body=body)))

    def test_a_summary_table_after_the_first_finding_section_is_refused(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Repair", "", entry("adr-review-late-table"), "",
            summary_table([("adr-review-late-table", "Repair")]),
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("summary table", problem)

    def test_a_summary_table_before_the_first_section_is_the_control(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-early-table", "Repair")]), "",
            "## Repair", "", entry("adr-review-early-table"),
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(report.summary_row_ids, ("adr-review-early-table",))

    def test_a_second_summary_table_is_refused(self):
        """One summary table, between the title and `## Propose`.

        The parser finds the table BY ITS HEADER ROW, so a second one silently
        appended its rows to the first table's id set and the set-equality
        postcondition then read a union nobody wrote.
        """
        table = summary_table([("adr-review-in-both", "Repair")])
        body = "\n".join([
            "# Decision review — 2026-09-10", "", table, "", table, "",
            "## Repair", "", entry("adr-review-in-both"),
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("second summary table", problem)

    def test_one_summary_table_is_the_control(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-in-both", "Repair")]), "",
            "## Repair", "", entry("adr-review-in-both"),
        ])
        self.assertEqual(self.parse(date="2026-09-10", body=body)
                         .summary_row_ids, ("adr-review-in-both",))

    def test_a_summary_row_naming_no_finding_or_two_is_refused(self):
        """One row names one finding. Zero and two are each refused.

        A row with no id contributed nothing to the id set and passed the
        postcondition by being invisible; a row naming two contributed the
        first and dropped the second.
        """
        for cell, expect in (("no id at all", "no finding id"),
                             ("`adr-review-one` and `adr-review-two`",
                              "2 finding ids")):
            with self.subTest(cell=cell):
                body = "\n".join([
                    "# Decision review — 2026-09-10", "",
                    "| finding id | section | objective | proposed act | size |",
                    "|---|---|---|---|---|",
                    f"| {cell} | Repair | OBJ-1 | write the thing | patch |", "",
                    "## Repair", "", entry("adr-review-one"),
                ])
                problem = self.refusal(self.parse, date="2026-09-10", body=body)
                self.assertIn(expect, problem)
                self.assertIn("one row names one finding", problem)

    def test_a_summary_row_naming_exactly_one_finding_is_the_control(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-one", "Repair")]), "",
            "## Repair", "", entry("adr-review-one"),
        ])
        self.assertEqual(self.parse(date="2026-09-10", body=body)
                         .summary_row_ids, ("adr-review-one",))

    def test_a_row_with_no_trailing_pipe_is_still_a_row(self):
        """`_cells` accepts a row that opens with `|` and does not close with one.

        Markdown renders both, so a report whose author dropped the closing
        pipe must parse to the same id set rather than losing the row from the
        postcondition's operand.
        """
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "| finding id | section | objective | proposed act | size |",
            "|---|---|---|---|---|",
            "| `adr-review-one` | Repair | OBJ-1 | write the thing | patch", "",
            "## Repair", "", entry("adr-review-one"),
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(report.summary_row_ids, ("adr-review-one",))
        self.assertIsNone(rf.validate_structure(report))

    def test_an_id_defined_but_absent_from_the_summary_table_is_refused(self):
        """The MISSING-ONLY arc of the set-inequality refusal.

        `test_equal_counts_with_unequal_sets_are_refused` drives both arcs at
        once. This one carries an empty `extra`, so it fails if the refusal
        ever reads only the extra set.
        """
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-in-both", "Repair")]), "",
            "## Repair", "",
            entry("adr-review-in-both"), "",
            entry("adr-review-only-defined"),
        ])
        problem = self.refusal(rf.validate_structure,
                               self.parse(date="2026-09-10", body=body))
        self.assertIn("adr-review-only-defined", problem)
        self.assertIn("summary table", problem)

    def test_legacy_reports_skip_the_postcondition(self):
        report = self.parse(date="2026-09-08",
                            frontmatter=LEGACY_FM.format(date="2026-09-08"),
                            body="# a\n\n## Repair\n\n### adr-review-legacy-one\n")
        self.assertIsNone(rf.validate_structure(report))


# ── V5: fences define nothing and record nothing ─────────────────────────

class FenceTests(ReviewFindingsTestCase):

    def test_a_fenced_example_creates_no_definition_no_record_no_note(self):
        """V5. Everything inside the fence is quoted material.

        The paired control is the same three lines unfenced, which DO produce
        one definition, one record and one note.
        """
        inner = [
            "### `adr-review-fenced-def` — an example heading",
            record_table([("2026-09-10", "`adr-review-real-one`", 2,
                           "resolved", "crux/scripts/review_findings.py")]),
            "- **resolved, pass 2** — 2026-09-10 — an example note",
        ]
        fenced = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Repair", "", entry("adr-review-real-one"), "",
            "## Coverage", "",
            "```markdown",
            *inner,
            "```",
        ])
        report = self.parse(date="2026-09-10", body=fenced)
        self.assertEqual([d.finding_id for d in report.definitions],
                         ["adr-review-real-one"])
        self.assertEqual(report.records, ())
        self.assertEqual(report.notes, ())

        unfenced = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Repair", "", entry("adr-review-real-one"),
            "- **resolved, pass 2** — 2026-09-10 — the real note", "",
            "## Coverage", "",
            record_table([("2026-09-10", "`adr-review-real-one`", 2,
                           "resolved", "crux/scripts/review_findings.py")]),
        ])
        control = self.parse(date="2026-09-10", body=unfenced)
        self.assertEqual(len(control.records), 1)
        self.assertEqual(len(control.notes), 1)

    def test_a_tilde_fence_closes_only_on_tildes(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Repair", "", entry("adr-review-outside"), "",
            "## Coverage", "",
            "~~~text",
            "```",
            record_table([("2026-09-10", "`adr-review-outside`", 1,
                           "disputed", "crux/scripts/review_findings.py")]),
            "~~~",
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(report.records, ())


# ── outcomes 2 and 5: record fields ──────────────────────────────────────

class RecordFieldTests(ReviewFindingsTestCase):

    def coverage(self, rows, *, date="2026-09-10", fid="adr-review-subject-one"):
        return "\n".join([
            "# Decision review — " + date, "",
            summary_table([(fid, "Repair")]), "",
            "## Repair", "", entry(fid), "",
            "## Coverage", "", record_table(rows),
        ])

    def test_a_well_formed_record_parses_into_its_five_fields(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 3,
                               "disputed", "bionic/adrs/ADR-0106.md")])
        record = self.parse(date="2026-09-10", body=body).records[0]
        self.assertEqual(record.source_date, "2026-09-10")
        self.assertEqual(record.finding_id, "adr-review-subject-one")
        self.assertEqual(record.pass_ordinal, 3)
        self.assertEqual(record.event, "disputed")
        self.assertEqual(record.locator, "bionic/adrs/ADR-0106.md")
        self.assertEqual(record.report_date, "2026-09-10")

    def test_the_event_raised_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "raised", "bionic/adrs/ADR-0106.md")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("raised", problem)
        self.assertIn("never written as a record", problem)

    def test_an_unknown_event_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "reopened", "bionic/adrs/ADR-0106.md")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("reopened", problem)

    def test_each_admitted_event_is_the_control(self):
        for kind in ("re-verified", "resolved", "disputed"):
            with self.subTest(kind=kind):
                body = self.coverage([("2026-09-10", "`adr-review-subject-one`",
                                       1, kind, "bionic/adrs/ADR-0106.md")])
                record = self.parse(date="2026-09-10", body=body).records[0]
                self.assertEqual(record.event, kind)

    def test_a_bare_slug_finding_id_is_refused(self):
        body = self.coverage([("2026-09-10", "`subject-one`", 1,
                               "resolved", "bionic/adrs/ADR-0106.md")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("subject-one", problem)
        self.assertIn("full form", problem)

    def test_a_non_calendar_source_date_is_refused(self):
        body = self.coverage([("2026-02-30", "`adr-review-subject-one`", 1,
                               "resolved", "bionic/adrs/ADR-0106.md")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("2026-02-30", problem)
        self.assertIn("calendar date", problem)

    def test_a_zero_pass_ordinal_is_refused(self):
        # The message names the grammar rather than "a positive integer": a
        # pass ordinal is 1 to 4 ASCII digits with no leading zero, and a
        # reader who is told "positive integer" cannot tell why `01` refuses.
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 0,
                               "resolved", "bionic/adrs/ADR-0106.md")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("pass ordinal", problem)
        self.assertIn("no leading zero", problem)

    def test_a_leading_zero_pass_ordinal_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", "01",
                               "resolved", "bionic/adrs/ADR-0106.md")])
        self.assertIn("pass ordinal",
                      self.refusal(self.parse, date="2026-09-10", body=body))

    def test_a_non_numeric_pass_ordinal_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`",
                               "second", "resolved", "bionic/adrs/ADR-0106.md")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("second", problem)
        self.assertIn("pass ordinal", problem)

    def test_a_short_row_is_refused_rather_than_padded(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "",
            "| source report date | finding id | pass | event | locator |",
            "|---|---|---|---|---|",
            "| 2026-09-10 | `adr-review-subject-one` | 1 |",
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("5 cells", problem)

    def test_a_locator_carrying_a_backtick_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "resolved", "`bionic/adrs/ADR-0106.md`")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("locator", problem)

    def test_a_locator_carrying_a_wiki_link_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "resolved", "[[adrs/ADR-0106]]")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("locator", problem)

    def test_a_locator_carrying_a_footnote_marker_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "resolved", "see the note[^rules]")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("locator", problem)

    def test_a_locator_past_the_length_bound_is_refused_never_truncated(self):
        """Outcome 2: out of grammar is refused, never truncated into admission.

        Derived by hand: the bound is 200 characters, so a 201-character
        locator is out of grammar and a 200-character one is in it. The control
        below proves the boundary is where the module says it is, so this row
        cannot pass on a parser that refuses every long locator.
        """
        too_long = "a" * 201
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "resolved", too_long)])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("locator", problem)

        at_bound = "b" * 200
        control = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                                  "resolved", at_bound)])
        record = self.parse(date="2026-09-10", body=control).records[0]
        self.assertEqual(record.locator, at_bound)

    def test_an_empty_locator_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "resolved", "")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("locator", problem)


# ── outcome 3: the two Coverage tables never merge ───────────────────────

class CoverageTableTests(ReviewFindingsTestCase):

    GOAL_MATRIX = "\n".join([
        "| objective | signals that measured it | domains that measured it |",
        "|---|---|---|",
        "| OBJ-1 | dormancy | decision-review |",
    ])

    def test_the_goal_matrix_beside_the_record_table_yields_one_record(self):
        """Outcome 3: identified by header row, never by position."""
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-subject-one", "Repair")]), "",
            "## Repair", "", entry("adr-review-subject-one"), "",
            "## Coverage", "",
            self.GOAL_MATRIX, "",
            record_table([("2026-09-10", "`adr-review-subject-one`", 1,
                           "re-verified", "crux/scripts/review_findings.py")]),
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(len(report.records), 1)
        self.assertEqual(report.records[0].event, "re-verified")

    def test_the_goal_matrix_alone_yields_no_record(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "", self.GOAL_MATRIX,
        ])
        self.assertEqual(self.parse(date="2026-09-10", body=body).records, ())

    def test_a_second_record_table_is_refused(self):
        rows = [("2026-09-10", "`adr-review-subject-one`", 1, "re-verified",
                 "crux/scripts/review_findings.py")]
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "", record_table(rows), "", record_table(rows),
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("one table", problem)

    def test_one_record_table_is_the_control(self):
        rows = [("2026-09-10", "`adr-review-subject-one`", 1, "re-verified",
                 "crux/scripts/review_findings.py")]
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "", record_table(rows),
        ])
        self.assertEqual(len(self.parse(date="2026-09-10", body=body).records), 1)

    def test_a_record_table_outside_coverage_is_refused(self):
        rows = [("2026-09-10", "`adr-review-subject-one`", 1, "re-verified",
                 "crux/scripts/review_findings.py")]
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Repair", "", entry("adr-review-subject-one"), "",
            record_table(rows),
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("Coverage", problem)


# ── outcome 3: note pairing ──────────────────────────────────────────────

class NotePairingTests(ReviewFindingsTestCase):

    PROSE = "the owner reworded the clause on the fourteenth"

    def report_body(self, *, note_kind, note_pass, record_rows):
        return "\n".join([
            "# Decision review — 2026-09-11", "",
            summary_table([("adr-review-noted-one", "Repair")]), "",
            "## Repair", "",
            entry("adr-review-noted-one"),
            f"- **{note_kind}, pass {note_pass}** — 2026-09-11 — {self.PROSE}", "",
            "## Coverage", "", record_table(record_rows),
        ])

    def test_a_paired_note_validates(self):
        body = self.report_body(
            note_kind="disputed", note_pass=2,
            record_rows=[("2026-09-11", "`adr-review-noted-one`", 2,
                          "disputed", "bionic/adrs/ADR-0106.md")])
        report = self.parse(date="2026-09-11", body=body)
        self.assertEqual([(n.finding_id, n.kind, n.pass_ordinal)
                          for n in report.notes],
                         [("adr-review-noted-one", "disputed", 2)])
        self.assertIsNone(rf.validate_structure(report))

    def test_a_note_borrowing_an_earlier_pass_record_is_refused(self):
        """Outcome 3: the pass is the note's own writing pass."""
        body = self.report_body(
            note_kind="disputed", note_pass=2,
            record_rows=[("2026-09-11", "`adr-review-noted-one`", 1,
                          "disputed", "bionic/adrs/ADR-0106.md")])
        report = self.parse(date="2026-09-11", body=body)
        problem = self.refusal(rf.validate_structure, report)
        self.assertIn("adr-review-noted-one", problem)

    def test_a_note_of_a_different_kind_than_its_record_is_refused(self):
        body = self.report_body(
            note_kind="disputed", note_pass=2,
            record_rows=[("2026-09-11", "`adr-review-noted-one`", 2,
                          "re-verified", "bionic/adrs/ADR-0106.md")])
        report = self.parse(date="2026-09-11", body=body)
        problem = self.refusal(rf.validate_structure, report)
        self.assertIn("disputed", problem)

    def test_the_pairing_refusal_names_the_id_and_line_and_hides_the_prose(self):
        """Outcome 2: a pairing refusal renders the id and the line, never prose.

        The line number is derived independently — the fixture is searched with
        `str.split` for the note's own marker text.
        """
        body = self.report_body(
            note_kind="re-verified", note_pass=1,
            record_rows=[("2026-09-11", "`adr-review-noted-one`", 4,
                          "re-verified", "bionic/adrs/ADR-0106.md")])
        expected_line = line_of(body, "**re-verified, pass 1**")
        report = self.parse(date="2026-09-11", body=body)
        problem = self.refusal(rf.validate_structure, report)
        self.assertIn("adr-review-noted-one", problem)
        self.assertIn(str(expected_line), problem)
        self.assertNotIn(self.PROSE, problem)
        self.assertNotIn("owner reworded", problem)

    def test_the_line_offset_moves_the_reported_line(self):
        """W2 reads a file, so the module takes the frontmatter's line count."""
        body = self.report_body(
            note_kind="re-verified", note_pass=1,
            record_rows=[("2026-09-11", "`adr-review-noted-one`", 4,
                          "re-verified", "bionic/adrs/ADR-0106.md")])
        expected_line = line_of(body, "**re-verified, pass 1**") + 7
        report = self.parse(date="2026-09-11", body=body, line_offset=7)
        problem = self.refusal(rf.validate_structure, report)
        self.assertIn(str(expected_line), problem)

    def test_a_note_outside_any_entry_is_not_a_note(self):
        body = "\n".join([
            "# Decision review — 2026-09-11", "",
            "## Coverage", "",
            "- **disputed, pass 1** — 2026-09-11 — prose outside every entry",
        ])
        report = self.parse(date="2026-09-11", body=body)
        self.assertEqual(report.notes, ())
        self.assertIsNone(rf.validate_structure(report))

    def test_a_resolved_note_needs_no_paired_record(self):
        """ADR-0106 outcome 3 binds the disputed and re-verified notes alone."""
        body = self.report_body(note_kind="resolved", note_pass=1,
                                record_rows=[])
        report = self.parse(date="2026-09-11", body=body)
        self.assertEqual(len(report.notes), 1)
        self.assertIsNone(rf.validate_structure(report))


# ── V2, V3, V4 + outcome 5: ordering and standing ────────────────────────

class StandingTests(ReviewFindingsTestCase):

    def definition_report(self, date, fid):
        body = "\n".join([
            f"# Decision review — {date}", "",
            summary_table([(fid, "Repair")]), "",
            "## Repair", "", entry(fid),
        ])
        return self.parse(date=date, body=body)

    def record_report(self, date, rows, *, own=()):
        pieces = ["# Decision review — " + date, ""]
        if own:
            pieces += [summary_table([(fid, "Repair") for fid in own]), "",
                       "## Repair", ""]
            pieces += [entry(fid) for fid in own]
        pieces += ["", "## Coverage", "", record_table(rows)]
        return self.parse(date=date, body="\n".join(pieces))

    def test_a_definition_with_no_record_is_open(self):
        report = self.definition_report("2026-09-10", "adr-review-alone-one")
        self.assertEqual(rf.standing_by_finding([report]),
                         {"adr-review-alone-one": "open"})

    def test_a_later_report_resolves_an_earlier_finding_and_raises_none(self):
        """V2. The recording report's own `raised` counts its definitions only.

        Derived by hand: the 2026-09-11 report defines nothing and carries one
        record naming the 2026-09-10 finding, so its `raised` is 0 and the
        earlier finding stands resolved.
        """
        first = self.definition_report("2026-09-10", "adr-review-carried-one")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-carried-one`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        self.assertEqual(second.raised, 0)
        self.assertEqual(rf.standing_by_finding([first, second]),
                         {"adr-review-carried-one": "resolved"})
        self.assertEqual(rf.events_elsewhere(second, [first, second]), 1)
        self.assertEqual(rf.events_elsewhere(first, [first, second]), 0)

    def test_a_dispute_stays_disputed_through_a_later_re_verification(self):
        """V3. Re-verification clears nothing."""
        first = self.definition_report("2026-09-10", "adr-review-argued-one")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-argued-one`", 1, "disputed",
             "crux/skills/review-decisions/SKILL.md")])
        third = self.record_report("2026-09-12", [
            ("2026-09-10", "`adr-review-argued-one`", 1, "re-verified",
             "crux/skills/review-decisions/SKILL.md")])
        self.assertEqual(rf.standing_by_finding([first, second, third]),
                         {"adr-review-argued-one": "disputed"})

    def test_a_resolution_clears_a_dispute(self):
        first = self.definition_report("2026-09-10", "adr-review-argued-two")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-argued-two`", 1, "disputed",
             "crux/skills/review-decisions/SKILL.md")])
        third = self.record_report("2026-09-12", [
            ("2026-09-10", "`adr-review-argued-two`", 1, "resolved",
             "crux/skills/review-decisions/SKILL.md")])
        self.assertEqual(rf.standing_by_finding([first, second, third]),
                         {"adr-review-argued-two": "resolved"})

    def test_a_re_verification_leaves_an_open_finding_open(self):
        first = self.definition_report("2026-09-10", "adr-review-checked-one")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-checked-one`", 1, "re-verified",
             "crux/scripts/review_findings.py")])
        self.assertEqual(rf.standing_by_finding([first, second]),
                         {"adr-review-checked-one": "open"})

    def test_a_record_after_a_resolution_is_refused(self):
        first = self.definition_report("2026-09-10", "adr-review-closed-one")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-closed-one`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        third = self.record_report("2026-09-12", [
            ("2026-09-10", "`adr-review-closed-one`", 1, "disputed",
             "crux/scripts/review_findings.py")])
        problem = self.refusal(rf.standing_by_finding, [first, second, third])
        self.assertIn("adr-review-closed-one", problem)
        self.assertIn("closed", problem)

    def test_the_same_two_records_in_the_opposite_order_are_the_control(self):
        """The refusal above is about ordering, not about carrying two records."""
        first = self.definition_report("2026-09-10", "adr-review-closed-two")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-closed-two`", 1, "disputed",
             "crux/scripts/review_findings.py")])
        third = self.record_report("2026-09-12", [
            ("2026-09-10", "`adr-review-closed-two`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        self.assertEqual(rf.standing_by_finding([first, second, third]),
                         {"adr-review-closed-two": "resolved"})

    def test_two_records_for_one_finding_from_one_pass_are_refused(self):
        first = self.definition_report("2026-09-10", "adr-review-twice-one")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-twice-one`", 2, "re-verified",
             "crux/scripts/review_findings.py"),
            ("2026-09-10", "`adr-review-twice-one`", 2, "disputed",
             "crux/scripts/review_findings.py")])
        problem = self.refusal(rf.standing_by_finding, [first, second])
        self.assertIn("adr-review-twice-one", problem)
        self.assertIn("pass 2", problem)

    def test_two_records_from_two_passes_are_the_control(self):
        first = self.definition_report("2026-09-10", "adr-review-twice-two")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-twice-two`", 2, "re-verified",
             "crux/scripts/review_findings.py"),
            ("2026-09-10", "`adr-review-twice-two`", 3, "disputed",
             "crux/scripts/review_findings.py")])
        self.assertEqual(rf.standing_by_finding([first, second]),
                         {"adr-review-twice-two": "disputed"})

    def test_a_record_predating_its_definition_is_refused(self):
        later = self.definition_report("2026-09-12", "adr-review-future-one")
        earlier = self.record_report("2026-09-11", [
            ("2026-09-12", "`adr-review-future-one`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        problem = self.refusal(rf.standing_by_finding, [later, earlier])
        self.assertIn("adr-review-future-one", problem)
        self.assertIn("predates", problem)

    def test_a_same_date_record_is_ordered_after_its_definition(self):
        """V4/outcome 5: a definition precedes every record in its own report.

        Pass 1 records against a finding the same report defines are admitted
        whatever the pass ordinal, so this control also covers pass 1.
        """
        body = "\n".join([
            "# Decision review — 2026-09-12", "",
            summary_table([("adr-review-same-day", "Repair")]), "",
            "## Repair", "", entry("adr-review-same-day"), "",
            "## Coverage", "",
            record_table([("2026-09-12", "`adr-review-same-day`", 1, "disputed",
                           "crux/scripts/review_findings.py")]),
        ])
        report = self.parse(date="2026-09-12", body=body)
        self.assertEqual(rf.standing_by_finding([report]),
                         {"adr-review-same-day": "disputed"})

    def test_a_record_naming_no_definition_is_refused(self):
        report = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-nowhere-one`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        problem = self.refusal(rf.standing_by_finding, [report])
        self.assertIn("adr-review-nowhere-one", problem)
        self.assertIn("no definition", problem)

    def test_a_record_naming_the_wrong_date_is_refused(self):
        """The definition exists — on another date. The date is part of identity."""
        first = self.definition_report("2026-09-10", "adr-review-misdated-one")
        second = self.record_report("2026-09-12", [
            ("2026-09-11", "`adr-review-misdated-one`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        problem = self.refusal(rf.standing_by_finding, [first, second])
        self.assertIn("2026-09-11", problem)

        control = self.record_report("2026-09-12", [
            ("2026-09-10", "`adr-review-misdated-one`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        self.assertEqual(rf.standing_by_finding([first, control]),
                         {"adr-review-misdated-one": "resolved"})

    def test_a_legacy_definition_is_unknown_and_stays_unknown(self):
        legacy = self.parse(
            date="2026-09-08", frontmatter=LEGACY_FM.format(date="2026-09-08"),
            body="# a\n\n## Repair\n\n### `adr-review-old-one` — a gap\n")
        later = self.record_report("2026-09-11", [
            ("2026-09-08", "`adr-review-old-one`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        self.assertEqual(rf.standing_by_finding([legacy, later]),
                         {"adr-review-old-one": "unknown"})
        self.assertEqual(rf.events_elsewhere(later, [legacy, later]), 1)

    def test_standing_is_independent_of_the_order_the_reports_arrive_in(self):
        """V8's determinism leg, at the module's own level."""
        first = self.definition_report("2026-09-10", "adr-review-order-one")
        second = self.record_report("2026-09-11", [
            ("2026-09-10", "`adr-review-order-one`", 1, "disputed",
             "crux/scripts/review_findings.py")])
        third = self.record_report("2026-09-12", [
            ("2026-09-10", "`adr-review-order-one`", 1, "resolved",
             "crux/scripts/review_findings.py")])
        forwards = rf.standing_by_finding([first, second, third])
        backwards = rf.standing_by_finding([third, second, first])
        self.assertEqual(forwards, {"adr-review-order-one": "resolved"})
        self.assertEqual(forwards, backwards)


# ── outcome 5: the injected locator-existence probe ──────────────────────

class LocatorExistenceTests(ReviewFindingsTestCase):

    def reports(self, event="resolved"):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-probed-one", "Repair")]), "",
            "## Repair", "", entry("adr-review-probed-one"), "",
            "## Coverage", "",
            record_table([("2026-09-10", "`adr-review-probed-one`", 1, event,
                           "crux/scripts/does-not-exist.py")]),
        ])
        return [self.parse(date="2026-09-10", body=body)]

    def test_a_resolved_locator_that_does_not_exist_is_refused(self):
        problem = self.refusal(rf.standing_by_finding, self.reports(),
                               locator_exists=lambda locator: False)
        self.assertIn("crux/scripts/does-not-exist.py", problem)

    def test_a_resolved_locator_that_exists_is_the_control(self):
        self.assertEqual(
            rf.standing_by_finding(self.reports(),
                                   locator_exists=lambda locator: True),
            {"adr-review-probed-one": "resolved"})

    def test_no_probe_skips_the_existence_check(self):
        self.assertEqual(rf.standing_by_finding(self.reports()),
                         {"adr-review-probed-one": "resolved"})

    def test_the_probe_is_asked_only_about_resolved_records(self):
        asked = []

        def probe(locator):
            asked.append(locator)
            return False

        self.assertEqual(
            rf.standing_by_finding(self.reports("disputed"), locator_exists=probe),
            {"adr-review-probed-one": "disputed"})
        self.assertEqual(asked, [])

        # Control: the same probe IS asked about a `resolved` record.
        with self.assertRaises(rf.ReviewFindingsError):
            rf.standing_by_finding(self.reports("resolved"), locator_exists=probe)
        self.assertEqual(asked, ["crux/scripts/does-not-exist.py"])


# ── outcome 6: the per-report standing counts ────────────────────────────

class ReportStandingTests(ReviewFindingsTestCase):

    def test_a_legacy_report_carries_its_frozen_count_as_unknown(self):
        body = ("# a\n\n## Repair\n\n### `adr-review-l-one` — a\n\n"
                "### `adr-review-l-two` — b\n\nAlso adr-review-l-three.\n")
        legacy = self.parse(date="2026-09-08", body=body,
                            frontmatter=LEGACY_FM.format(date="2026-09-08"))
        self.assertEqual(legacy.raised, 3)
        self.assertEqual(rf.report_standing(legacy, {}), {"unknown": 3})

    def test_a_lifecycle_report_counts_its_own_findings_per_state(self):
        """Derived by hand: three definitions, one resolved, two open."""
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-c-one", "Repair"),
                           ("adr-review-c-two", "Repair"),
                           ("adr-review-c-three", "Repair")]), "",
            "## Repair", "",
            entry("adr-review-c-one"), "",
            entry("adr-review-c-two"), "",
            entry("adr-review-c-three"), "",
            "## Coverage", "",
            record_table([("2026-09-10", "`adr-review-c-two`", 1, "resolved",
                           "crux/scripts/review_findings.py")]),
        ])
        report = self.parse(date="2026-09-10", body=body)
        standing = rf.standing_by_finding([report])
        self.assertEqual(standing, {"adr-review-c-one": "open",
                                    "adr-review-c-two": "resolved",
                                    "adr-review-c-three": "open"})
        self.assertEqual(rf.report_standing(report, standing),
                         {"open": 2, "resolved": 1})

    def test_a_later_report_event_is_not_projected_onto_the_legacy_row(self):
        """Outcome 6: the legacy row's cells do not move."""
        legacy = self.parse(
            date="2026-09-08", frontmatter=LEGACY_FM.format(date="2026-09-08"),
            body="# a\n\n## Repair\n\n### `adr-review-frozen-one` — a gap\n")
        later_body = "\n".join([
            "# Decision review — 2026-09-11", "",
            "## Coverage", "",
            record_table([("2026-09-08", "`adr-review-frozen-one`", 1,
                           "resolved", "crux/scripts/review_findings.py")]),
        ])
        later = self.parse(date="2026-09-11", body=later_body)
        standing = rf.standing_by_finding([legacy, later])
        self.assertEqual(rf.report_standing(legacy, standing), {"unknown": 1})
        self.assertEqual(rf.report_standing(later, standing), {})
        self.assertEqual(rf.events_elsewhere(later, [legacy, later]), 1)


# ── V4: two passes on one date ───────────────────────────────────────────

class TwoPassTests(ReviewFindingsTestCase):

    def test_a_second_pass_adds_a_definition_and_a_record_and_keeps_the_first(self):
        """V4. Derived by hand: two definitions, one record, standing open and
        disputed. The record names the first pass's finding, so it is a
        follow-up event rather than a third definition."""
        body = "\n".join([
            "# Decision review — 2026-09-13", "",
            summary_table([("adr-review-pass-one-gap", "Repair"),
                           ("adr-review-pass-two-gap", "Propose")]), "",
            "## Propose", "",
            entry("adr-review-pass-two-gap"), "",
            "## Repair", "",
            entry("adr-review-pass-one-gap",
                  "- **disputed, pass 2** — 2026-09-13 — the second pass disputes it"),
            "",
            "## Coverage", "",
            record_table([("2026-09-13", "`adr-review-pass-one-gap`", 2,
                           "disputed", "bionic/adrs/ADR-0106.md")]),
        ])
        report = self.parse(date="2026-09-13", body=body)
        self.assertEqual(report.raised, 2)
        rf.validate_structure(report)
        standing = rf.standing_by_finding([report])
        self.assertEqual(standing, {"adr-review-pass-one-gap": "disputed",
                                    "adr-review-pass-two-gap": "open"})
        self.assertEqual(rf.report_standing(report, standing),
                         {"disputed": 1, "open": 1})
        self.assertEqual(rf.events_elsewhere(report, [report]), 0)


# ── V6: every refusal names the problem ──────────────────────────────────

class DiagnosticTests(ReviewFindingsTestCase):

    def test_every_refusal_carries_a_problem_string(self):
        error = rf.ReviewFindingsError("the problem, stated")
        self.assertEqual(error.problem, "the problem, stated")
        self.assertIn("the problem, stated", str(error))

    def test_a_hostile_value_is_bounded_and_redacted_in_the_refusal(self):
        """Outcome 2: every refused parsed value renders through `redact`.

        Derived by hand: the event cell carries 400 characters and a control
        character, so the refusal must show neither the raw newline nor the
        full length. The paired control below is a short printable event value,
        whose refusal quotes it whole.
        """
        hostile = "x" * 400 + "\x07"
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "",
            "| source report date | finding id | pass | event | locator |",
            "|---|---|---|---|---|",
            f"| 2026-09-10 | `adr-review-h-one` | 1 | {hostile} | a/path.md |",
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertNotIn("\x07", problem)
        self.assertLess(len(problem), 600)
        self.assertIn("truncated from", problem)

        control_body = body.replace(hostile, "reopened")
        control = self.refusal(self.parse, date="2026-09-10", body=control_body)
        self.assertIn("reopened", control)
        self.assertNotIn("truncated from", control)


# ── [SECURITY:S1]: every rendered value is BOUNDED, not merely redacted ───

class BoundedOrdinalTests(ReviewFindingsTestCase):
    """The pass ordinal is a bounded ASCII digit run, and so is its rendering.

    Derived by hand from outcome 2: the four closed-vocabulary fields render
    through the bound-and-redact rule. `redact` bounds a value it is GIVEN a
    string; an ordinal that reached `int()` and came back as an int was
    rendered raw, so a 4000-digit cell put 4000 characters on stderr and in
    the JSON envelope. The grammar bound is the fix, and the rendering bound
    is the backstop. Each case carries the paired in-grammar control.
    """

    def coverage(self, rows, *, date="2026-09-10", fid="adr-review-subject-one"):
        return "\n".join([
            "# Decision review — " + date, "",
            summary_table([(fid, "Repair")]), "",
            "## Repair", "", entry(fid), "",
            "## Coverage", "", record_table(rows),
        ])

    def test_a_four_thousand_digit_pass_ordinal_is_refused_and_bounded(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`",
                               "1" * 4000, "resolved", "a/path.md")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("pass", problem)
        self.assertLess(len(problem), 600, "the refusal rendered the raw run")

    def test_a_five_thousand_digit_pass_ordinal_is_refused_and_bounded(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`",
                               "9" * 5000, "resolved", "a/path.md")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertLess(len(problem), 600)

    def test_a_two_digit_pass_ordinal_is_the_control(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 12,
                               "resolved", "a/path.md")])
        self.assertEqual(self.parse(date="2026-09-10", body=body).records[0]
                         .pass_ordinal, 12)

    def test_the_ordinal_bound_admits_9999_and_refuses_10000(self):
        """The boundary, so the bound cannot be satisfied by refusing every
        multi-digit ordinal."""
        admitted = self.coverage([("2026-09-10", "`adr-review-subject-one`",
                                   9999, "resolved", "a/path.md")])
        self.assertEqual(self.parse(date="2026-09-10", body=admitted)
                         .records[0].pass_ordinal, 9999)
        refused = self.coverage([("2026-09-10", "`adr-review-subject-one`",
                                  10000, "resolved", "a/path.md")])
        self.assertIn("pass", self.refusal(self.parse, date="2026-09-10",
                                           body=refused))

    def note_body(self, marker: str) -> str:
        fid = "adr-review-subject-one"
        return "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([(fid, "Repair")]), "",
            "## Repair", "",
            entry(fid, marker),
            "", "## Coverage", "",
        ])

    def test_a_note_ordinal_outside_the_grammar_is_refused_naming_the_line(self):
        """A near-miss marker is REFUSED, never degraded to prose.

        `\\d` matches Arabic-Indic digits in a `str` pattern, so an ordinal
        outside the ASCII run once parsed and reached `int()`. Bounding the run
        fixed that and opened a quieter failure: the line fell out of the marker
        grammar, the parser read it as prose, and a pass that believed it wrote
        a note wrote nothing the pairing check sees. All three near-misses below
        now name their line.
        """
        for ordinal in ("10000", "0", "\u0662"):
            with self.subTest(ordinal=ordinal):
                problem = self.refusal(
                    self.parse, date="2026-09-10",
                    body=self.note_body(
                        f"- **disputed, pass {ordinal}** — prose about it."))
                self.assertIn("outside the ordinal grammar", problem)
                self.assertIn("line 12", problem)

    def test_a_line_that_is_not_a_note_marker_at_all_stays_prose(self):
        """The control for the refusal above: prose is still prose.

        Without this, the near-miss refusal could be reading every bulleted
        bold line in an entry, which would refuse a report for its narrative.
        """
        parsed = self.parse(
            date="2026-09-10",
            body=self.note_body("- **something else entirely** — prose."))
        self.assertEqual(parsed.notes, ())

    def test_an_ascii_digit_note_marker_is_the_control(self):
        fid = "adr-review-subject-one"
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([(fid, "Repair")]), "",
            "## Repair", "",
            entry(fid, "- **disputed, pass 2** — prose about the event."),
            "", "## Coverage", "",
        ])
        notes = self.parse(date="2026-09-10", body=body).notes
        self.assertEqual([(n.kind, n.pass_ordinal) for n in notes],
                         [("disputed", 2)])

    def test_a_control_character_in_a_locator_is_refused(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "resolved", "a/pa\x07th.md")])
        self.assertIn("locator", self.refusal(self.parse, date="2026-09-10",
                                              body=body))

    def test_the_same_locator_without_the_control_character_is_the_control(self):
        body = self.coverage([("2026-09-10", "`adr-review-subject-one`", 1,
                               "resolved", "a/path.md")])
        self.assertEqual(self.parse(date="2026-09-10", body=body).records[0]
                         .locator, "a/path.md")


class SetDifferenceBoundTests(ReviewFindingsTestCase):
    """The summary-table set-difference refusal names at most ten ids.

    Derived by hand: a hand-editable table can carry any number of rows, and
    joining the whole difference put the entire table into one error string.
    Ten plus a count is the bound; the control proves a small difference is
    still named in full, so the cap is not a blanket elision.
    """

    def report(self, extra_rows):
        fid = "adr-review-subject-one"
        rows = [(fid, "Repair")] + [(e, "Repair") for e in extra_rows]
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table(rows), "",
            "## Repair", "", entry(fid), "", "## Coverage", "",
        ])
        return self.parse(date="2026-09-10", body=body)

    def test_five_thousand_extra_summary_rows_render_a_bounded_refusal(self):
        parsed = self.report([f"adr-review-extra-{n:05d}" for n in range(5000)])
        problem = self.refusal(rf.validate_structure, parsed)
        self.assertLess(len(problem), 2048, "the refusal joined every id")
        self.assertIn("more", problem)

    def test_two_extra_summary_rows_name_both_ids_in_full(self):
        parsed = self.report(["adr-review-extra-one", "adr-review-extra-two"])
        problem = self.refusal(rf.validate_structure, parsed)
        self.assertIn("adr-review-extra-one", problem)
        self.assertIn("adr-review-extra-two", problem)
        self.assertNotIn("more", problem)


class OrphanRecordRowTests(ReviewFindingsTestCase):
    """A five-cell, ISO-dated row under `## Coverage` that no table owns.

    Derived by hand from outcome 3: the record table is found BY ITS HEADER
    ROW. A mistyped header therefore left every row beneath it unowned, and an
    unowned row was skipped in silence — a report that believed it recorded a
    resolution recorded nothing, and the index computed the finding open. That
    disagreement is a document-lane refusal, not a silent drop.
    """

    ROW = "| 2026-09-10 | `adr-review-subject-one` | 1 | resolved | a/path.md |"

    def body(self, header):
        fid = "adr-review-subject-one"
        return "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([(fid, "Repair")]), "",
            "## Repair", "", entry(fid), "",
            "## Coverage", "", header, "|---|---|---|---|---|", self.ROW,
        ])

    def test_an_unowned_iso_dated_five_cell_row_is_refused_naming_its_line(self):
        body = self.body(
            "| source report date | finding id | pass | event | evidence |")
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn(str(line_of(body, self.ROW)), problem)
        self.assertIn("header row", problem)

    def test_the_same_row_under_the_record_header_is_the_control(self):
        body = self.body(
            "| source report date | finding id | pass | event | locator |")
        record = self.parse(date="2026-09-10", body=body).records[0]
        self.assertEqual(record.event, "resolved")


class LocatorShapeTests(ReviewFindingsTestCase):
    """Outcome 2's three textual refusals hold on EVERY record.

    Derived by hand: the decision says a locator outside the grammar is
    refused, without qualification. The absolute / `~` / `..` refusals used to
    live only in the injected existence probe, which is asked about a
    `resolved` record alone — so a `re-verified` record naming `/etc/passwd`
    was admitted, and the skill's and the template's "is refused" promise was
    false for two of the three event kinds. The check is a pure string check;
    this module still does no I/O after import.
    """

    def coverage(self, event, locator, *, fid="adr-review-subject-one"):
        return "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([(fid, "Repair")]), "",
            "## Repair", "", entry(fid), "",
            "## Coverage", "",
            record_table([("2026-09-10", f"`{fid}`", 1, event, locator)]),
        ])

    def test_an_absolute_locator_on_a_re_verified_record_is_refused(self):
        problem = self.refusal(self.parse, date="2026-09-10",
                               body=self.coverage("re-verified", "/etc/passwd"))
        self.assertIn("resolves only inside the repository root", problem)
        self.assertNotIn("is no surface in the tree", problem)

    def test_a_home_relative_locator_on_a_re_verified_record_is_refused(self):
        problem = self.refusal(self.parse, date="2026-09-10",
                               body=self.coverage("re-verified", "~/.ssh/id_rsa"))
        self.assertIn("resolves only inside the repository root", problem)

    def test_a_parent_traversal_locator_on_a_disputed_record_is_refused(self):
        problem = self.refusal(self.parse, date="2026-09-10",
                               body=self.coverage("disputed", "../outside/x.md"))
        self.assertIn("traverses out of the repository root", problem)

    def test_a_backslash_parent_traversal_is_refused(self):
        """`a\\..\\b` is ONE component to `PurePosixPath` and three on Windows.

        The absolute check already asked both path flavours; the `..` check
        asked only the POSIX one, so a value that traverses wherever `\\` is a
        separator was admitted by the leg meant to refuse traversal.
        """
        problem = self.refusal(self.parse, date="2026-09-10",
                               body=self.coverage("disputed", "a\\..\\b"))
        self.assertIn("traverses out of the repository root", problem)

    def test_a_backslash_in_a_locator_with_no_traversal_is_the_control(self):
        record = self.parse(date="2026-09-10",
                            body=self.coverage("disputed", "a\\b")).records[0]
        self.assertEqual(record.locator, "a\\b")

    def test_a_relative_locator_on_a_re_verified_record_is_the_control(self):
        record = self.parse(date="2026-09-10",
                            body=self.coverage("re-verified",
                                               "bionic/adrs/ADR-0001-x.md")).records[0]
        self.assertEqual(record.locator, "bionic/adrs/ADR-0001-x.md")


class BacktickedCellTests(ReviewFindingsTestCase):
    """ONE backtick rule, stated once and implemented once.

    Derived by hand: backticks are Markdown formatting, and a reader who
    backticks the id cell backticks the pass and event cells beside it. They
    are stripped before the closed-vocabulary check. The LOCATOR cell is the
    exception and stays refused: a backtick run terminates a fence, so the
    grammar refuses the character outright in the one free field.
    """

    def coverage(self, pass_cell, event_cell, locator,
                 *, fid="adr-review-subject-one"):
        return "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([(fid, "Repair")]), "",
            "## Repair", "", entry(fid), "",
            "## Coverage", "",
            record_table([("2026-09-10", f"`{fid}`", pass_cell, event_cell,
                           locator)]),
        ])

    def test_backticked_pass_and_event_cells_parse(self):
        record = self.parse(date="2026-09-10",
                            body=self.coverage("`1`", "`resolved`",
                                               "a/path.md")).records[0]
        self.assertEqual((record.pass_ordinal, record.event), (1, "resolved"))

    def test_a_backticked_locator_is_still_refused(self):
        problem = self.refusal(self.parse, date="2026-09-10",
                               body=self.coverage(1, "resolved", "`a/path.md`"))
        self.assertIn("locator", problem)


class ProbeRefusalContextTests(ReviewFindingsTestCase):
    """A probe refusal is re-raised carrying the record's line and date.

    Derived by hand: the probe is handed a locator and nothing else, so its
    refusal named neither the record nor the report it came from — on a corpus
    of several reports the reader had to grep the string to find the row. The
    stream is the layer that knows both, so it is the layer that adds them.
    """

    def report(self, date, *, findings=(), records=()):
        rows = [(fid, "Repair") for fid in findings]
        out = [f"# Decision review — {date}", ""]
        if rows:
            out += [summary_table(rows), ""]
        out += ["## Repair", ""]
        for fid in findings:
            out += [entry(fid), ""]
        out += ["## Coverage", ""]
        if records:
            out.append(record_table(records))
        body = "\n".join(out)
        return self.parse(date=date, body=body), body

    def test_a_probe_that_answers_false_is_named_with_line_and_date(self):
        first, _ = self.report("2026-09-10", findings=["adr-review-subject-one"])
        second, body = self.report(
            "2026-09-11",
            records=[("2026-09-10", "`adr-review-subject-one`", 1, "resolved",
                      "bionic/adrs/ADR-9999-nothing.md")])

        def probe(_locator):
            raise rf.ReviewFindingsError("the locator names no surface")

        problem = self.refusal(rf.standing_by_finding, [first, second],
                               locator_exists=probe)
        self.assertIn("2026-09-11", problem)
        self.assertIn(str(line_of(body, "ADR-9999-nothing.md")), problem)
        self.assertIn("the locator names no surface", problem)

# ── ADR-0108: the assessment table, its vocabulary, and the derivations ───

class AssessmentParseTests(ReviewFindingsTestCase):
    """WU1: assessment-row parsing under the seven-column table."""

    def assessment_body(self, rows, *, date="2026-09-10"):
        return "\n".join([
            f"# Decision review — {date}", "",
            "## Coverage", "",
            assessment_table(rows),
        ])

    def test_a_well_formed_row_parses_into_its_fields(self):
        body = self.assessment_body([
            ("OBJ-1", "OBJ-1.1 — the rule holds under real runs", "resolved",
             "crux/scripts/review_findings.py", "decision-review", "serves",
             "")])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(len(report.assessments), 1)
        a = report.assessments[0]
        self.assertEqual(a.objective, "OBJ-1")
        self.assertEqual(a.part, "OBJ-1.1")
        self.assertEqual(a.label, "the rule holds under real runs")
        self.assertEqual(a.evidence, "resolved")
        self.assertEqual(a.locator, "crux/scripts/review_findings.py")
        self.assertEqual(a.domains, ("decision-review",))
        self.assertEqual(a.conclusion, "serves")
        self.assertEqual(a.findings, ())
        self.assertIsNone(rf.validate_structure(report))

    def test_backticked_objective_evidence_and_conclusion_parse(self):
        """The objective, evidence and conclusion cells MAY be backticked."""
        body = self.assessment_body([
            ("`OBJ-1`", "OBJ-1.1 — the rule holds", "`resolved`",
             "crux/scripts/review_findings.py", "decision-review",
             "`serves`", "")])
        a = self.parse(date="2026-09-10", body=body).assessments[0]
        self.assertEqual((a.objective, a.evidence, a.conclusion),
                         ("OBJ-1", "resolved", "serves"))

    def test_a_backticked_locator_is_still_refused(self):
        """The locator cell is NEVER backticked, exactly like the record one."""
        body = self.assessment_body([
            ("OBJ-1", "OBJ-1.1 — the rule holds", "resolved",
             "`crux/scripts/review_findings.py`", "decision-review",
             "serves", "")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("locator", problem)

    def test_a_second_assessment_table_is_refused(self):
        table = assessment_table([("OBJ-1", "OBJ-1.1 — a", "resolved",
                                   "a/path.md", "decision-review", "serves",
                                   "")])
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "", table, "", table,
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("second assessment table", problem)

    def test_one_assessment_table_is_the_control(self):
        body = self.assessment_body([
            ("OBJ-1", "OBJ-1.1 — a", "resolved", "a/path.md",
             "decision-review", "serves", "")])
        self.assertEqual(len(self.parse(date="2026-09-10", body=body)
                            .assessments), 1)

    def test_an_assessment_table_outside_coverage_is_refused(self):
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            assessment_table([("OBJ-1", "OBJ-1.1 — a", "resolved",
                              "a/path.md", "decision-review", "serves", "")]),
        ])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("Coverage", problem)

    def test_the_measure_cell_grammar_is_enforced(self):
        body = self.assessment_body([
            ("OBJ-1", "not a measure part at all", "resolved", "a/path.md",
             "decision-review", "serves", "")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("measure", problem)

    def test_a_finding_in_the_findings_cell_matches_the_id_grammar(self):
        body = self.assessment_body([
            ("OBJ-1", "OBJ-1.1 — a", "resolved", "a/path.md",
             "decision-review", "serves", "not-a-full-form-id")])
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("not-a-full-form-id", problem)
        self.assertIn("full form", problem)


class OrphanAssessmentRowTests(ReviewFindingsTestCase):
    """WU1: the seven-cell OBJ-headed orphan-row trap, mirroring the ISO one.

    A mistyped assessment-table header must REFUSE the row beneath it rather
    than silently drop it — exactly the hazard the existing ISO-dated,
    five-cell record trap exists to catch, at the assessment table's own
    arity and first-cell shape.
    """

    ROW = ("| OBJ-1 | OBJ-1.1 — the rule holds | resolved | a/path.md | "
           "decision-review | serves | |")

    def body(self, header):
        return "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "", header, "|---|---|---|---|---|---|---|",
            self.ROW,
        ])

    def test_an_unowned_obj_headed_seven_cell_row_is_refused_naming_its_line(self):
        mistyped = ("| objective | measure | evidence | locator | domains | "
                    "verdict | findings |")
        body = self.body(mistyped)
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn(str(line_of(body, self.ROW)), problem)
        self.assertIn("header row", problem)

    def test_the_same_row_under_the_real_header_is_the_positive_control(self):
        """Same row, correctly-spelled header: it parses as one assessment.

        Without this control, the refusal above could pass on a parser that
        refuses every seven-cell row under Coverage regardless of header.
        """
        correct = ("| objective | measure | evidence | locator | domains | "
                   "conclusion | findings |")
        body = self.body(correct)
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(len(report.assessments), 1)
        self.assertEqual(report.assessments[0].conclusion, "serves")


class VocabularyAndPairTests(ReviewFindingsTestCase):
    """WU2: the six legal pairs, named refusals, and the two total functions."""

    def assessment_body_of(self, evidence, conclusion, *, date="2026-09-10"):
        return "\n".join([
            f"# Decision review — {date}", "",
            "## Coverage", "",
            assessment_table([("OBJ-1", "OBJ-1.1 — a claim", evidence,
                              "a/path.md", "decision-review", conclusion,
                              "")]),
        ])

    def test_resolved_with_inconclusive_is_refused_by_name(self):
        body = self.assessment_body_of("resolved", "inconclusive")
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("resolved", problem)
        self.assertIn("inconclusive", problem)

    def test_unavailable_with_gap_is_refused_by_name(self):
        body = self.assessment_body_of("unavailable", "gap")
        problem = self.refusal(self.parse, date="2026-09-10", body=body)
        self.assertIn("unavailable", problem)
        self.assertIn("gap", problem)

    def test_every_legal_pair_is_the_control(self):
        """Positive control for the two refusals above: EVERY legal pair parses."""
        for evidence, conclusion in rf.LEGAL_PAIRS:
            with self.subTest(evidence=evidence, conclusion=conclusion):
                body = self.assessment_body_of(evidence, conclusion)
                a = self.parse(date="2026-09-10", body=body).assessments[0]
                self.assertEqual((a.evidence, a.conclusion),
                                 (evidence, conclusion))

    def test_legal_pairs_is_exactly_six(self):
        self.assertEqual(len(rf.LEGAL_PAIRS), 6)

    def test_discriminating_pairs_is_exactly_three(self):
        self.assertEqual(len(rf.DISCRIMINATING_PAIRS), 3)
        self.assertEqual(
            rf.DISCRIMINATING_PAIRS,
            {("resolved", "serves"), ("resolved", "gap"),
             ("partial", "gap")})
        # Every discriminating pair is itself legal — a pair that discriminates
        # but that the vocabulary refuses would be unreachable.
        self.assertTrue(rf.DISCRIMINATING_PAIRS <= rf.LEGAL_PAIRS)

    # ── the two derived, total functions ──

    def test_a_partial_component_beside_a_full_check_stays_partial_not_serves(self):
        """A multi-part measure with one component checked never rolls up to
        `serves` — the rollup is `inconclusive` here, distinctly not `serves`."""
        rollup = rf.alignment_rollup(["serves", "not-assessed"])
        self.assertEqual(rollup, "inconclusive")
        self.assertNotEqual(rollup, "serves")

    def test_a_null_baseline_pair_yields_attempted_not_measured(self):
        """`unavailable`+`inconclusive`: attempted, never measured."""
        self.assertEqual(
            rf.measurement_outcome([("unavailable", "inconclusive")]),
            "attempted")

    def test_resolved_gap_discriminates_to_measured(self):
        """A rule exists but observed runs violate it: `resolved`+`gap`."""
        self.assertEqual(rf.measurement_outcome([("resolved", "gap")]),
                         "measured")

    def test_one_serves_and_one_gap_in_one_goal_rolls_up_to_gap(self):
        """`gap` outranks `serves` in the same goal's rollup."""
        self.assertEqual(rf.alignment_rollup(["serves", "gap"]), "gap")

    def test_partial_inconclusive_is_attempted_never_measured(self):
        """The case the council specifically flagged."""
        outcome = rf.measurement_outcome([("partial", "inconclusive")])
        self.assertEqual(outcome, "attempted")
        self.assertNotEqual(outcome, "measured")

    def test_the_zero_part_rollup_and_outcome_are_both_not_assessed(self):
        """A goal whose declared measure is the recorded absence of one."""
        self.assertEqual(rf.alignment_rollup([]), "not-assessed")
        self.assertEqual(rf.measurement_outcome([]), "not-assessed")


class ObjectiveOrderingTests(ReviewFindingsTestCase):
    """WU2: assessment rows list objectives in NUMERIC, not lexicographic, order."""

    def body_for(self, objectives):
        rows = [(obj, f"{obj}.1 — a claim", "resolved", "a/path.md",
                 "decision-review", "serves", "") for obj in objectives]
        return "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "", assessment_table(rows),
        ])

    def test_obj_2_then_obj_10_passes(self):
        report = self.parse(date="2026-09-10",
                            body=self.body_for(["OBJ-2", "OBJ-10"]))
        self.assertIsNone(rf.validate_structure(report))

    def test_obj_10_then_obj_2_is_refused(self):
        """The bug a lexicographic comparison would miss: 'OBJ-10' < 'OBJ-2'
        as strings, so only an integer comparison catches this ordering."""
        report = self.parse(date="2026-09-10",
                            body=self.body_for(["OBJ-10", "OBJ-2"]))
        problem = self.refusal(rf.validate_structure, report)
        self.assertIn("numeric order", problem)


class FindingReferenceTests(ReviewFindingsTestCase):
    """WU2: a finding id in a findings cell is a REFERENCE, never a definition."""

    def test_a_findings_cell_id_defines_nothing(self):
        """The id is a REFERENCE: it enters neither definitions nor the
        summary-table row set, even though the report defines it elsewhere."""
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            summary_table([("adr-review-some-gap", "Repair")]), "",
            "## Repair", "", entry("adr-review-some-gap"), "",
            "## Coverage", "",
            assessment_table([("OBJ-1", "OBJ-1.1 — a claim", "resolved",
                              "a/path.md", "decision-review", "gap",
                              "adr-review-some-gap")]),
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertEqual(report.assessments[0].findings,
                         ("adr-review-some-gap",))
        # ONE definition — the `### ` heading — and the findings cell added
        # none. That is the whole claim.
        self.assertEqual(1, len(report.definitions))
        self.assertEqual(1, report.raised)
        self.assertIsNone(rf.validate_structure(report))

    def test_a_findings_cell_id_the_report_never_defines_is_refused(self):
        """A dangling reference points at a gap nobody can read."""
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "",
            assessment_table([("OBJ-1", "OBJ-1.1 — a claim", "resolved",
                              "a/path.md", "decision-review", "gap",
                              "adr-review-defined-nowhere")]),
        ])
        report = self.parse(date="2026-09-10", body=body)
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.validate_structure(report)
        self.assertIn("neither defines nor records", str(caught.exception))

    def test_a_findings_cell_may_name_a_finding_a_record_carries(self):
        """Positive control: a cross-date reference resolves through a record.

        An earlier date's finding is named in THIS report by a lifecycle
        record, so the reference resolves without a definition here.
        """
        body = "\n".join([
            "# Decision review — 2026-09-10", "",
            "## Coverage", "",
            assessment_table([("OBJ-1", "OBJ-1.1 — a claim", "resolved",
                              "a/path.md", "decision-review", "gap",
                              "adr-review-raised-earlier")]), "",
            record_table([("2026-09-09", "`adr-review-raised-earlier`", 1,
                           "re-verified", "crux/scripts/review_findings.py")]),
        ])
        report = self.parse(date="2026-09-10", body=body)
        self.assertIsNone(rf.validate_structure(report))


class MeasuredObjectivesShapeTests(ReviewFindingsTestCase):
    """WU3: the three `measured_objectives` shapes, plus empty-is-zero."""

    def test_an_absent_key_is_unknown_shape_absent(self):
        outcomes, shape = rf.measured_objectives(
            "type: adr-review\ndate: 2026-09-10\ndismissed: []")
        self.assertEqual(shape, "absent")
        self.assertEqual(outcomes, {})

    def test_an_empty_bracket_list_is_the_positive_zero_record(self):
        outcomes, shape = rf.measured_objectives(
            "type: adr-review\nmeasured_objectives: []\ndismissed: []")
        self.assertEqual(shape, "empty")
        self.assertEqual(outcomes, {})

    def test_a_flat_list_of_bare_ids_demotes_every_id_to_attempted(self):
        outcomes, shape = rf.measured_objectives(
            "measured_objectives: [OBJ-1, OBJ-2, OBJ-3]")
        self.assertEqual(shape, "flat")
        self.assertEqual({goal: fields["outcome"]
                          for goal, fields in outcomes.items()},
                         {"OBJ-1": "attempted", "OBJ-2": "attempted",
                          "OBJ-3": "attempted"})

    def test_a_structured_block_reads_outcomes_as_recorded(self):
        frontmatter = "\n".join([
            "type: adr-review",
            "measured_objectives:",
            "  - objective: OBJ-1",
            "    outcome: measured",
            "    pass: 3",
            "    measure_digest: sha256:fixture-1",
            "    evidence: crux/scripts/review_findings.py:1-2",
            "  - objective: OBJ-2",
            "    outcome: attempted",
            "    pass: 3",
            "    measure_digest: sha256:fixture-2",
            "    blocker: the baseline is null",
            "dismissed: []",
        ])
        outcomes, shape = rf.measured_objectives(frontmatter)
        self.assertEqual(shape, "structured")
        self.assertEqual(outcomes["OBJ-1"]["outcome"], "measured")
        self.assertEqual(outcomes["OBJ-2"]["outcome"], "attempted")
        self.assertEqual(outcomes["OBJ-2"]["blocker"], "the baseline is null")

    def test_a_present_but_empty_structured_key_is_the_control_for_absent(self):
        """Present-and-empty and absent are different shapes over the same {}."""
        empty_outcomes, empty_shape = rf.measured_objectives(
            "measured_objectives:\ndismissed: []")
        absent_outcomes, absent_shape = rf.measured_objectives(
            "dismissed: []")
        self.assertEqual((empty_outcomes, absent_outcomes), ({}, {}))
        self.assertNotEqual(empty_shape, absent_shape)
        self.assertEqual(empty_shape, "empty")
        self.assertEqual(absent_shape, "absent")

    # ── ADR-0109: the counted-thing key, and `measure_digest` ─────────────

    def test_a_part_field_keys_the_entry_on_the_part_id_not_the_objective(self):
        frontmatter = "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1",
            "    part: OBJ-1.2",
            "    outcome: attempted",
            "    measure_digest: sha256:abc",
            "    blocker: no baseline",
        ])
        outcomes, shape = rf.measured_objectives(frontmatter)
        self.assertEqual(shape, "structured")
        self.assertNotIn("OBJ-1", outcomes)
        self.assertEqual(outcomes["OBJ-1.2"]["outcome"], "attempted")
        self.assertEqual(outcomes["OBJ-1.2"]["measure_digest"], "sha256:abc")

    def test_a_part_less_and_a_part_level_entry_are_two_counted_things(self):
        """A goal's own entry and one of its part's entries never merge."""
        frontmatter = "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1",
            "    outcome: measured",
            "    measure_digest: sha256:goal",
            "    evidence: a/p.md",
            "  - objective: OBJ-1",
            "    part: OBJ-1.2",
            "    outcome: attempted",
            "    measure_digest: sha256:part",
            "    blocker: no baseline",
        ])
        outcomes, _ = rf.measured_objectives(frontmatter)
        self.assertEqual(set(outcomes), {"OBJ-1", "OBJ-1.2"})
        self.assertEqual(outcomes["OBJ-1"]["outcome"], "measured")
        self.assertEqual(outcomes["OBJ-1.2"]["outcome"], "attempted")

    def test_the_strongest_outcome_wins_within_one_part_and_never_across_two(self):
        """A part's `attempted` must never be buried under, nor bury, a
        sibling part's `measured` — regression for the rule this key change
        exists to satisfy."""
        frontmatter = "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1",
            "    part: OBJ-1.1",
            "    outcome: measured",
            "    measure_digest: sha256:p1",
            "    evidence: a/p.md",
            "  - objective: OBJ-1",
            "    part: OBJ-1.2",
            "    outcome: attempted",
            "    measure_digest: sha256:p2",
            "    blocker: no baseline",
        ])
        outcomes, _ = rf.measured_objectives(frontmatter)
        self.assertEqual(outcomes["OBJ-1.1"]["outcome"], "measured")
        self.assertEqual(outcomes["OBJ-1.2"]["outcome"], "attempted")

    def test_a_malformed_part_id_is_refused(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.measured_objectives(
                "measured_objectives:\n  - objective: OBJ-1\n"
                "    part: OBJ-1\n    outcome: attempted\n")
        self.assertIn("OBJ-N.k", str(caught.exception))

    def test_a_part_naming_a_different_goal_than_its_objective_is_refused(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.measured_objectives(
                "measured_objectives:\n  - objective: OBJ-1\n"
                "    part: OBJ-2.1\n    outcome: attempted\n")
        self.assertIn("goal numbers disagree", str(caught.exception))

    def test_a_structured_entry_missing_measure_digest_is_refused(self):
        """ADR-0110 `rotation-discharges-on-attempt-or-measurement`: `measure_digest`
        is REQUIRED on every structured entry, goal-level and part-level
        alike — a correctness requirement, not a convenience."""
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.measured_objectives(
                "measured_objectives:\n  - objective: OBJ-1\n"
                "    outcome: measured\n    evidence: a/p.md\n")
        self.assertIn("measure_digest", str(caught.exception))

    def test_the_same_entry_carrying_measure_digest_is_the_control(self):
        outcomes, shape = rf.measured_objectives(
            "measured_objectives:\n  - objective: OBJ-1\n"
            "    outcome: measured\n    measure_digest: sha256:x\n"
            "    evidence: a/p.md\n")
        self.assertEqual(shape, "structured")
        self.assertEqual(outcomes["OBJ-1"]["measure_digest"], "sha256:x")

    def test_a_part_level_entry_missing_measure_digest_is_also_refused(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.measured_objectives(
                "measured_objectives:\n  - objective: OBJ-1\n"
                "    part: OBJ-1.1\n    outcome: attempted\n"
                "    blocker: no baseline\n")
        self.assertIn("measure_digest", str(caught.exception))


class RotationAndEscalationTests(ReviewFindingsTestCase):
    """ADR-0109: the coverage window and the escalation history are two
    spans, and never one variable."""

    def _fm(self, outcome, *, part=None, digest="sha256:fixture"):
        lines = ["type: adr-review", "measured_objectives:",
                "  - objective: OBJ-1"]
        if part:
            lines.append(f"    part: {part}")
        lines.append(f"    outcome: {outcome}")
        lines.append(f"    measure_digest: {digest}")
        if outcome == "attempted":
            lines.append("    blocker: no baseline")
        else:
            lines.append("    evidence: a/path.md:1-2")
        return "\n".join(lines)

    STRUCTURED = None   # set below; kept as an attribute for readability
    MEASURED = None
    FLAT_ATTEMPTED = "type: adr-review\nmeasured_objectives: [OBJ-1]\n"
    SILENT = "type: adr-review\ndismissed: []\n"

    def setUp(self):
        super().setUp()
        self.STRUCTURED = self._fm("attempted")
        self.MEASURED = self._fm("measured")

    def test_two_consecutive_structured_attempts_carry_two_strikes(self):
        state = rf.rotation_state(
            [("2026-09-08", self.STRUCTURED), ("2026-09-09", self.STRUCTURED)],
            ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["escalation"]["strikes"], 2)
        self.assertTrue(state["OBJ-1"]["discharged"])

    def test_a_measured_outcome_resets_the_strike_count_to_zero(self):
        state = rf.rotation_state(
            [("2026-09-07", self.STRUCTURED), ("2026-09-08", self.STRUCTURED),
             ("2026-09-09", self.MEASURED)],
            ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["escalation"]["strikes"], 0)
        self.assertFalse(state["OBJ-1"]["escalation"]["owed"])
        self.assertTrue(state["OBJ-1"]["discharged"])

    def test_a_demoted_flat_attempted_entry_discharges_and_starts_no_strike(self):
        """A demoted (flat-list) entry discharges the rotation but is not a
        strike — it resets the count same as a `measured` outcome would."""
        state = rf.rotation_state(
            [("2026-09-08", self.STRUCTURED),
             ("2026-09-09", self.FLAT_ATTEMPTED)],
            ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["escalation"]["strikes"], 0)
        self.assertTrue(state["OBJ-1"]["discharged"])
        self.assertEqual(state["OBJ-1"]["coverage"]["2026-09-09"],
                         ("attempted", "flat"))

    def test_a_silent_date_steps_over_without_resetting_or_advancing(self):
        """A date recording neither outcome (`unknown`) is stepped over: the
        strike run continues across it rather than resetting at it."""
        state = rf.rotation_state(
            [("2026-09-07", self.STRUCTURED), ("2026-09-08", self.SILENT),
             ("2026-09-09", self.STRUCTURED)],
            ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["coverage"]["2026-09-08"],
                         ("unknown", "absent"))
        self.assertEqual(state["OBJ-1"]["escalation"]["strikes"], 2)

    def test_fewer_than_three_dates_spans_only_what_exists(self):
        state = rf.rotation_state([("2026-09-09", self.STRUCTURED)], ["OBJ-1"])
        self.assertEqual(len(state["OBJ-1"]["coverage"]), 1)
        self.assertEqual(state["OBJ-1"]["escalation"]["strikes"], 1)

    def test_the_coverage_window_still_reads_only_the_three_newest_dates(self):
        """The COVERAGE span stays three dates even though the escalation
        span (below) now reads further back — the two are separate
        variables."""
        state = rf.rotation_state(
            [("2026-09-01", self.MEASURED), ("2026-09-05", self.STRUCTURED),
             ("2026-09-06", self.STRUCTURED), ("2026-09-07", self.STRUCTURED)],
            ["OBJ-1"])
        self.assertEqual(set(state["OBJ-1"]["coverage"]),
                         {"2026-09-05", "2026-09-06", "2026-09-07"})

    def test_rotation_state_hands_escalation_the_full_history(self):
        """`rotation_state` passes the FULL dated history to
        `escalation_state`, never its own three-date coverage window. Narrow
        it to the window and this run reads two strikes, not three — the D4
        defect, reintroduced at the one site that composes the two spans.
        """
        dated = [("2026-09-05", self.STRUCTURED), ("2026-09-06", self.SILENT),
                 ("2026-09-07", self.STRUCTURED), ("2026-09-08", self.SILENT),
                 ("2026-09-09", self.STRUCTURED)]
        state = rf.rotation_state(dated, ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["escalation"]["strikes"], 3)
        self.assertTrue(state["OBJ-1"]["escalation"]["owed"])
        # PAIRED CONTROL: the coverage window is STILL three dates wide, so
        # the assertion above reads a wider escalation span rather than a
        # widened coverage window.
        self.assertEqual(set(state["OBJ-1"]["coverage"]),
                         {"2026-09-07", "2026-09-08", "2026-09-09"})

    # ── D4: the defect this decision fixes ─────────────────────────────────

    def test_five_dates_attempted_silent_attempted_silent_attempted_give_three(self):
        """The escalation span reads the FULL history and steps over silent
        dates, so this run gives three strikes — never the two a
        three-date coverage window would see (the D4 defect)."""
        state = rf.escalation_state(
            [("2026-09-05", self.STRUCTURED), ("2026-09-06", self.SILENT),
             ("2026-09-07", self.STRUCTURED), ("2026-09-08", self.SILENT),
             ("2026-09-09", self.STRUCTURED)],
            ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["strikes"], 3)
        self.assertTrue(state["OBJ-1"]["owed"])
        # None of these five entries carries a `escalation_strikes` claim, so
        # this is the RECONSTRUCTION path — `owed_since` is always `None`
        # there, because reconstruction cannot recover the true first-owed
        # date (it may lie outside the bounded window, or an obligation
        # inside it may already have been spent in a way outcomes alone
        # cannot show).
        self.assertIsNone(state["OBJ-1"]["owed_since"])
        self.assertFalse(state["OBJ-1"]["verified"])

    def test_attempts_separated_by_more_than_three_dates_still_accumulate(self):
        state = rf.escalation_state(
            [("2026-09-01", self.STRUCTURED), ("2026-09-02", self.SILENT),
             ("2026-09-03", self.SILENT), ("2026-09-04", self.SILENT),
             ("2026-09-05", self.SILENT), ("2026-09-06", self.STRUCTURED)],
            ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["strikes"], 2)

    # ── rewritten-measure reset ─────────────────────────────────────────────

    def test_a_changed_measure_digest_resets_the_count(self):
        old = self._fm("attempted", digest="sha256:old")
        new = self._fm("attempted", digest="sha256:new")
        state = rf.escalation_state(
            [("2026-09-07", old), ("2026-09-08", old), ("2026-09-09", new)],
            ["OBJ-1"])
        # The rewrite clears the two prior strikes, and the rewriting date's
        # own attempted outcome starts the count fresh at one.
        self.assertEqual(state["OBJ-1"]["strikes"], 1)
        self.assertFalse(state["OBJ-1"]["owed"])

    def test_an_unchanged_digest_is_the_control_for_the_reset(self):
        same = self._fm("attempted", digest="sha256:same")
        state = rf.escalation_state(
            [("2026-09-07", same), ("2026-09-08", same), ("2026-09-09", same)],
            ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["strikes"], 3)

    # ── the per-pass finding cap ────────────────────────────────────────────

    def test_an_escalation_deferred_by_the_finding_cap_remains_owed(self):
        state = {"OBJ-1": {"strikes": 3, "owed": True,
                           "owed_since": "2026-09-01", "verified": True},
                "OBJ-2": {"strikes": 3, "owed": True,
                         "owed_since": "2026-09-02", "verified": True}}
        spend, deferred = rf.spend_escalations(state, cap_remaining=1)
        self.assertEqual(spend, ["OBJ-1"])
        self.assertEqual(deferred, ["OBJ-2"])
        # OBJ-2's obligation is still `owed` in the state itself — spending
        # is a scheduling decision over the state, never a mutation of it.
        self.assertTrue(state["OBJ-2"]["owed"])

    def test_zero_cap_remaining_defers_every_owed_escalation(self):
        """Positive control: nothing is spent when the cap is exhausted."""
        state = {"OBJ-1": {"strikes": 3, "owed": True,
                           "owed_since": "2026-09-01", "verified": True}}
        spend, deferred = rf.spend_escalations(state, cap_remaining=0)
        self.assertEqual(spend, [])
        self.assertEqual(deferred, ["OBJ-1"])

    def test_an_unverified_obligation_orders_ahead_of_every_dated_one(self):
        state = {
            "OBJ-1": {"strikes": 3, "owed": True,
                     "owed_since": "2020-01-01", "verified": True},
            "OBJ-2": {"strikes": 3, "owed": True,
                     "owed_since": None, "verified": False},
        }
        self.assertEqual(rf.owed_escalations(state), ["OBJ-2", "OBJ-1"])

    def test_a_part_less_obligation_orders_before_a_part_of_the_same_goal(self):
        state = {
            "OBJ-1.1": {"strikes": 3, "owed": True,
                       "owed_since": "2026-09-01", "verified": True},
            "OBJ-1": {"strikes": 3, "owed": True,
                     "owed_since": "2026-09-01", "verified": True},
        }
        self.assertEqual(rf.owed_escalations(state), ["OBJ-1", "OBJ-1.1"])

    # ── carried escalation state: verify, contradiction-refuse, reconstruct ─

    def test_a_cap_deferred_escalation_is_not_re_owed_next_pass(self):
        """The case deviation-2 would have broken: pass N reaches three
        strikes and carries `owed: true` because the five-finding cap was
        already spent on OTHER findings this pass — the obligation itself is
        NOT about to be written twice. Pass N+1 stays blocked; the carried
        state must NOT re-owe a second obligation nor move `owed_since`."""
        digest = "sha256:blocked"
        pass1 = ("measured_objectives:\n  - objective: OBJ-1\n"
                "    outcome: attempted\n    blocker: no baseline\n"
                f"    measure_digest: {digest}\n"
                "    escalation_strikes: 3\n    escalation_owed: true\n"
                "    escalation_owed_since: 2026-09-09\n")
        pass2 = ("measured_objectives:\n  - objective: OBJ-1\n"
                "    outcome: attempted\n    blocker: no baseline\n"
                f"    measure_digest: {digest}\n"
                "    escalation_strikes: 4\n    escalation_owed: true\n"
                "    escalation_owed_since: 2026-09-09\n")
        state = rf.escalation_state(
            [("2026-09-09", pass1), ("2026-09-10", pass2)], ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["strikes"], 4)
        self.assertTrue(state["OBJ-1"]["owed"])
        # Still the ORIGINAL first-owed date — not advanced to pass 2's date,
        # which would be the duplicate-obligation shape deviation-2 produced.
        self.assertEqual(state["OBJ-1"]["owed_since"], "2026-09-09")
        self.assertTrue(state["OBJ-1"]["verified"])

    def test_a_carried_count_that_contradicts_the_stepped_value_is_refused(self):
        digest = "sha256:x"
        prev = ("measured_objectives:\n  - objective: OBJ-1\n"
               "    outcome: attempted\n    blocker: no baseline\n"
               f"    measure_digest: {digest}\n"
               "    escalation_strikes: 1\n    escalation_owed: false\n")
        # One `attempted` step from strikes=1 must yield strikes=2. Claiming
        # 5 is a contradiction — refused, never silently corrected.
        newest = ("measured_objectives:\n  - objective: OBJ-1\n"
                 "    outcome: attempted\n    blocker: no baseline\n"
                 f"    measure_digest: {digest}\n"
                 "    escalation_strikes: 5\n    escalation_owed: false\n")
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.escalation_state(
                [("2026-09-09", prev), ("2026-09-10", newest)], ["OBJ-1"])
        self.assertIn("contradicts", str(caught.exception))

    def test_the_correctly_stepped_count_is_the_control(self):
        """Positive control for the refusal above: strikes=1 -> `attempted`
        -> strikes=2 is the correct single step, and is accepted."""
        digest = "sha256:x"
        prev = ("measured_objectives:\n  - objective: OBJ-1\n"
               "    outcome: attempted\n    blocker: no baseline\n"
               f"    measure_digest: {digest}\n"
               "    escalation_strikes: 1\n    escalation_owed: false\n")
        newest = ("measured_objectives:\n  - objective: OBJ-1\n"
                 "    outcome: attempted\n    blocker: no baseline\n"
                 f"    measure_digest: {digest}\n"
                 "    escalation_strikes: 2\n    escalation_owed: false\n")
        state = rf.escalation_state(
            [("2026-09-09", prev), ("2026-09-10", newest)], ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["strikes"], 2)
        self.assertTrue(state["OBJ-1"]["verified"])

    def test_a_broken_chain_reconstructs_and_orders_ahead_of_a_dated_one(self):
        """No report here carries an `escalation_strikes` claim at all, so
        the chain is broken from the start: reconstruction recovers the
        count, marks `unverified`, and — ordered beside a normal dated
        obligation — comes first."""
        attempted = ("measured_objectives:\n  - objective: OBJ-1\n"
                    "    outcome: attempted\n    blocker: no baseline\n"
                    "    measure_digest: sha256:x\n")
        broken = rf.escalation_state(
            [("2026-09-07", attempted), ("2026-09-08", attempted),
             ("2026-09-09", attempted)], ["OBJ-1"])
        self.assertFalse(broken["OBJ-1"]["verified"])
        self.assertTrue(broken["OBJ-1"]["owed"])
        self.assertIsNone(broken["OBJ-1"]["owed_since"])

        combined = {
            "OBJ-1": broken["OBJ-1"],
            "OBJ-2": {"strikes": 3, "owed": True,
                     "owed_since": "2026-09-01", "verified": True},
        }
        self.assertEqual(rf.owed_escalations(combined), ["OBJ-1", "OBJ-2"])

    # ── per-part escalation beside a measured sibling ───────────────────────

    def test_a_measured_part_coexists_with_a_siblings_unresolved_blocker(self):
        """OBJ-1.1 is measured on every date; OBJ-1.2 is attempted every
        date. The sibling's `measured` outcome must never reset OBJ-1.2's
        count — the defect ADR-0109 rule 6 fixes."""
        def fm(date_index):
            return "\n".join([
                "measured_objectives:",
                "  - objective: OBJ-1", "    part: OBJ-1.1",
                "    outcome: measured", "    measure_digest: sha256:p1",
                "    evidence: a/p.md",
                "  - objective: OBJ-1", "    part: OBJ-1.2",
                "    outcome: attempted", "    measure_digest: sha256:p2",
                "    blocker: no baseline",
            ])
        dates = ["2026-09-07", "2026-09-08", "2026-09-09"]
        state = rf.escalation_state([(d, fm(i)) for i, d in enumerate(dates)],
                                    ["OBJ-1.1", "OBJ-1.2"])
        self.assertEqual(state["OBJ-1.1"]["strikes"], 0)
        self.assertFalse(state["OBJ-1.1"]["owed"])
        self.assertEqual(state["OBJ-1.2"]["strikes"], 3)
        self.assertTrue(state["OBJ-1.2"]["owed"])

    def test_a_part_blocked_three_dates_then_discriminating_clears_cleanly(self):
        """A part blocked on three dates then discriminating on the fourth:
        the live pass and a later reconstruction over the same corpus must
        AGREE that no obligation remains."""
        blocked = "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1", "    part: OBJ-1.1",
            "    outcome: attempted", "    measure_digest: sha256:same",
            "    blocker: no baseline",
        ])
        discriminated = "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1", "    part: OBJ-1.1",
            "    outcome: measured", "    measure_digest: sha256:same",
            "    evidence: a/p.md",
        ])
        # The VERIFY leg: each blocked entry carries the count that date's own
        # pass computed. Without the claims both legs would reconstruct, and
        # the agreement below would hold by construction rather than by test.
        carried = [blocked + f"\n    escalation_strikes: {n}"
                            + f"\n    escalation_owed: {str(n >= 3).lower()}"
                            + ("\n    escalation_owed_since: 2026-09-08"
                               if n >= 3 else "")
                   for n in (1, 2, 3)]
        dates = ["2026-09-06", "2026-09-07", "2026-09-08"]
        claimed = list(zip(dates, carried))
        stripped = [(date, blocked) for date in dates]

        # At the third date the two legs are DISTINGUISHABLE: the carried
        # claim is verified and recovers the true first-owed date, which a
        # reconstruction cannot know.
        live_third = rf.escalation_state(claimed, ["OBJ-1.1"])["OBJ-1.1"]
        rebuilt_third = rf.escalation_state(stripped, ["OBJ-1.1"])["OBJ-1.1"]
        self.assertTrue(live_third["verified"])
        self.assertFalse(rebuilt_third["verified"])
        self.assertEqual(live_third["owed_since"], "2026-09-08")
        self.assertIsNone(rebuilt_third["owed_since"])
        self.assertEqual(live_third["strikes"], rebuilt_third["strikes"])
        self.assertTrue(live_third["owed"])
        self.assertTrue(rebuilt_third["owed"])

        # On the fourth date the part discriminates, and both legs agree that
        # nothing remains owed.
        fourth = ("2026-09-09", discriminated)
        live_pass = rf.escalation_state(claimed + [fourth], ["OBJ-1.1"])
        reconstruction = rf.escalation_state(stripped + [fourth], ["OBJ-1.1"])

        self.assertEqual(live_pass, reconstruction)
        self.assertEqual(live_pass["OBJ-1.1"]["strikes"], 0)
        self.assertFalse(live_pass["OBJ-1.1"]["owed"])


class EscalationClearingLeavesStandingUntouchedTests(ReviewFindingsTestCase):
    """`escalation_state`'s docstring (WU-ADR-0109) asserts clearing an
    obligation is NOT a finding event: "that ledger is `standing_by_finding`'s,
    untouched here." Nothing before this drove both functions over ONE corpus
    and compared — the property held by construction (`escalation_state` and
    `standing_by_finding` walk disjoint call graphs), not by test. A future
    refactor that coupled the two ledgers — say, one that treated a cleared
    obligation as an implicit `resolved` record — would ship silently.

    Reuses the exact escalation corpus from
    `test_a_part_blocked_three_dates_then_discriminating_clears_cleanly`
    (blocked on three dates, discriminating on the fourth) and layers two
    real findings on top: one that stays `open` throughout and one the third
    date's report `resolved`. Both ledgers are read over the SAME reports.
    """

    def _corpus(self):
        # ── the escalation half — verbatim shape from the reused test ──────
        blocked = "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1", "    part: OBJ-1.1",
            "    outcome: attempted", "    measure_digest: sha256:same",
            "    blocker: no baseline",
        ])
        discriminated = "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1", "    part: OBJ-1.1",
            "    outcome: measured", "    measure_digest: sha256:same",
            "    evidence: a/p.md",
        ])
        carried = [blocked + f"\n    escalation_strikes: {n}"
                            + f"\n    escalation_owed: {str(n >= 3).lower()}"
                            + ("\n    escalation_owed_since: 2026-09-08"
                               if n >= 3 else "")
                   for n in (1, 2, 3)]
        dates = ["2026-09-06", "2026-09-07", "2026-09-08"]
        claimed = list(zip(dates, carried))
        fourth = ("2026-09-09", discriminated)

        # ── the standing half — two findings riding the same four dates ────
        definitions_body = "\n".join([
            "# Decision review — 2026-09-06", "",
            summary_table([("adr-review-escalation-open", "Repair"),
                           ("adr-review-escalation-resolved", "Repair")]), "",
            "## Repair", "",
            entry("adr-review-escalation-open"), "",
            entry("adr-review-escalation-resolved"),
        ])
        resolution_body = "\n".join([
            "# Decision review — 2026-09-08", "",
            "## Coverage", "",
            record_table([("2026-09-06", "`adr-review-escalation-resolved`",
                           1, "resolved",
                           "crux/scripts/review_findings.py")]),
        ])

        frontmatters = {date: LIFECYCLE_FM.format(date=date) + "\n" + text
                        for date, text in claimed + [fourth]}

        report1 = self.parse(date="2026-09-06",
                             frontmatter=frontmatters["2026-09-06"],
                             body=definitions_body)
        report2 = self.parse(date="2026-09-07",
                             frontmatter=frontmatters["2026-09-07"], body="")
        report3 = self.parse(date="2026-09-08",
                             frontmatter=frontmatters["2026-09-08"],
                             body=resolution_body)
        report4 = self.parse(date="2026-09-09",
                             frontmatter=frontmatters["2026-09-09"], body="")

        return claimed, fourth, [report1, report2, report3, report4]

    def test_clearing_the_obligation_moves_no_finding(self):
        claimed, fourth, reports = self._corpus()
        before_reports, clearing_report = reports[:3], reports[3]

        # POSITIVE CONTROL, leg 1: the obligation is genuinely OWED before
        # the fourth date and genuinely CLEARS on it — otherwise the
        # equality below would be a comparison of two computations of
        # "nothing happened" and would prove nothing about clearing.
        owed_before = rf.escalation_state(claimed, ["OBJ-1.1"])["OBJ-1.1"]
        cleared_after = rf.escalation_state(claimed + [fourth],
                                            ["OBJ-1.1"])["OBJ-1.1"]
        self.assertTrue(owed_before["owed"])
        self.assertEqual(owed_before["strikes"], 3)
        self.assertFalse(cleared_after["owed"])
        self.assertEqual(cleared_after["strikes"], 0)

        # POSITIVE CONTROL, leg 2: the standing map is non-empty and holds
        # real, distinguishable standings — not the vacuous `{}` an
        # empty-corpus bug would also pass under.
        standing_before = rf.standing_by_finding(before_reports)
        expected = {"adr-review-escalation-open": "open",
                    "adr-review-escalation-resolved": "resolved"}
        self.assertEqual(standing_before, expected)

        # THE ASSERTION: adding the clearing report changes NOTHING about
        # standing, even though it just cleared a real obligation.
        standing_after = rf.standing_by_finding(reports)
        self.assertEqual(standing_before, standing_after)
        self.assertEqual(standing_after, expected)


class CommentsInTheKeyTests(ReviewFindingsTestCase):
    """`#` opens a comment in YAML, so a comment line never ends a block
    sequence. The SHIPPED TEMPLATE puts `##` guidance between the key and the
    entries it tells the writer to uncomment, and breaking there read the
    whole report as `empty` — a positive record of zero measurements, with no
    refusal — from a writer who only left the instructions in place.
    """

    ENTRIES = ("  - objective: OBJ-1\n    outcome: measured\n"
               "    measure_digest: sha256:x\n    evidence: a/p.md\n")

    def test_the_templates_own_guidance_lines_do_not_void_the_entries(self):
        with_prose = ("type: adr-review\nmeasured_objectives:\n"
                      "## Replace the [] above with one entry per counted thing.\n"
                      "## PROSE LINES START WITH ## AND ARE DELETED.\n"
                      + self.ENTRIES)
        outcomes, shape = rf.measured_objectives(with_prose)
        self.assertEqual(shape, "structured")
        self.assertIn("OBJ-1", outcomes)
        # PAIRED CONTROL: the same entries with the guidance deleted read the
        # same way, so the prose changes nothing rather than being tolerated
        # into a different answer.
        without = ("type: adr-review\nmeasured_objectives:\n" + self.ENTRIES)
        self.assertEqual(rf.measured_objectives(without),
                         (outcomes, shape))

    def test_a_comment_between_two_entries_drops_neither(self):
        text = ("type: adr-review\nmeasured_objectives:\n" + self.ENTRIES
                + "  # why OBJ-2 is still blocked\n"
                "  - objective: OBJ-2\n    outcome: attempted\n"
                "    measure_digest: sha256:y\n    blocker: no baseline\n")
        outcomes, shape = rf.measured_objectives(text)
        self.assertEqual(shape, "structured")
        self.assertEqual(sorted(outcomes), ["OBJ-1", "OBJ-2"])

    def test_a_real_key_after_the_sequence_still_ends_it(self):
        """The control on the control: a NON-comment line at or below the
        list indent is still a terminator, so making comments transparent
        did not make the reader run past the key's own value."""
        text = ("type: adr-review\nmeasured_objectives:\n" + self.ENTRIES
                + "reviewer: architect\ndismissed: []\n")
        outcomes, shape = rf.measured_objectives(text)
        self.assertEqual(sorted(outcomes), ["OBJ-1"])
        self.assertEqual(shape, "structured")

class EmptyMappingShapeTests(ReviewFindingsTestCase):
    """`{}` is an explicit empty mapping. Alone it is a positive record of
    zero; with a block sequence beneath it, the two halves contradict and
    reading either one discards the other."""

    def test_an_explicit_empty_mapping_alone_is_a_positive_zero(self):
        outcomes, shape = rf.measured_objectives(
            "type: adr-review\nmeasured_objectives: {}\n")
        self.assertEqual(outcomes, {})
        self.assertEqual(shape, "empty")

    def test_an_empty_mapping_over_a_block_sequence_is_refused(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.measured_objectives(
                "type: adr-review\nmeasured_objectives: {}\n"
                "  - objective: OBJ-1\n    outcome: measured\n"
                "    measure_digest: sha256:x\n    evidence: a/p.md\n")
        self.assertIn("explicit empty mapping", str(caught.exception))
        # PAIRED CONTROL: the same sequence under a BARE key reads
        # structured, so the refusal is the `{}` and not the sequence.
        outcomes, shape = rf.measured_objectives(
            "type: adr-review\nmeasured_objectives:\n"
            "  - objective: OBJ-1\n    outcome: measured\n"
            "    measure_digest: sha256:x\n    evidence: a/p.md\n")
        self.assertEqual(shape, "structured")
        self.assertIn("OBJ-1", outcomes)

class SpentEscalationTests(ReviewFindingsTestCase):
    """ADR-0109 `escalation-history-is-carried-and-bounded`: reconstruction
    "cannot recover … whether one was already spent", so spent-ness is state
    the CARRIED chain holds. Without it a pass that raises the strike-three
    finding either records a contradiction or re-owes the same obligation on
    every later attempted date.
    """

    def entry(self, outcome="attempted", **carried):
        lines = ["measured_objectives:", "  - objective: OBJ-1",
                 f"    outcome: {outcome}", "    measure_digest: sha256:same"]
        lines.append("    blocker: no baseline" if outcome == "attempted"
                     else "    evidence: a/p.md")
        lines += [f"    {k}: {v}" for k, v in carried.items()]
        return "\n".join(lines)

    def owed_corpus(self):
        """Three consecutive attempts, the third owing the escalation."""
        return [
            ("2026-01-01", self.entry(escalation_strikes=1,
                                      escalation_owed="false")),
            ("2026-01-02", self.entry(escalation_strikes=2,
                                      escalation_owed="false")),
            ("2026-01-03", self.entry(escalation_strikes=3,
                                      escalation_owed="true",
                                      escalation_owed_since="2026-01-03")),
        ]

    def test_the_third_strike_owes_the_escalation(self):
        """PAIRED CONTROL for every test below: the obligation IS owed."""
        state = rf.escalation_state(self.owed_corpus(), ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], 3)
        self.assertTrue(state["owed"])
        self.assertFalse(state["spent"])
        self.assertEqual(state["owed_since"], "2026-01-03")

    def test_recording_the_spend_discharges_the_obligation(self):
        corpus = self.owed_corpus() + [
            ("2026-01-04", self.entry(escalation_strikes=4,
                                      escalation_owed="false",
                                      escalation_spent_on="2026-01-04"))]
        state = rf.escalation_state(corpus, ["OBJ-1"])
        self.assertTrue(state["OBJ-1"]["spent"])
        self.assertFalse(state["OBJ-1"]["owed"])
        self.assertEqual(state["OBJ-1"]["strikes"], 4)
        self.assertEqual(rf.owed_escalations(state), [])

    def test_a_spent_obligation_is_never_re_owed_on_a_later_attempt(self):
        """One obligation per RUN of consecutive attempts, not one per
        attempt past three."""
        corpus = self.owed_corpus() + [
            ("2026-01-04", self.entry(escalation_strikes=4,
                                      escalation_owed="false",
                                      escalation_spent_on="2026-01-04")),
            ("2026-01-05", self.entry(escalation_strikes=5,
                                      escalation_owed="false",
                                      escalation_spent_on="2026-01-04"))]
        state = rf.escalation_state(corpus, ["OBJ-1"])
        self.assertEqual(state["OBJ-1"]["strikes"], 5)
        self.assertFalse(state["OBJ-1"]["owed"])
        self.assertEqual(rf.owed_escalations(state), [])

    def test_a_measured_outcome_clears_the_spend_so_a_fresh_run_can_owe(self):
        corpus = self.owed_corpus() + [
            ("2026-01-04", self.entry(escalation_strikes=4,
                                      escalation_owed="false",
                                      escalation_spent_on="2026-01-04")),
            ("2026-01-05", self.entry("measured", escalation_strikes=0,
                                      escalation_owed="false"))]
        state = rf.escalation_state(corpus, ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], 0)
        self.assertFalse(state["spent"])
        self.assertFalse(state["owed"])

    def test_a_cap_deferred_obligation_can_be_spent_on_a_later_date(self):
        """The spend need not land on the first-owed date: an obligation the
        five-finding cap deferred keeps its `owed_since` until spent."""
        corpus = self.owed_corpus() + [
            ("2026-01-04", self.entry(escalation_strikes=4,
                                      escalation_owed="true",
                                      escalation_owed_since="2026-01-03")),
            ("2026-01-05", self.entry(escalation_strikes=5,
                                      escalation_owed="false",
                                      escalation_spent_on="2026-01-05"))]
        deferred = rf.escalation_state(corpus[:-1], ["OBJ-1"])["OBJ-1"]
        self.assertEqual(deferred["owed_since"], "2026-01-03")
        spent = rf.escalation_state(corpus, ["OBJ-1"])["OBJ-1"]
        self.assertTrue(spent["spent"])
        self.assertIsNone(spent["owed_since"])

    def test_owed_and_spent_at_once_is_refused(self):
        corpus = self.owed_corpus() + [
            ("2026-01-04", self.entry(escalation_strikes=4,
                                      escalation_owed="true",
                                      escalation_owed_since="2026-01-03",
                                      escalation_spent_on="2026-01-04"))]
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.escalation_state(corpus, ["OBJ-1"])
        self.assertIn("owed or spent and never both", str(caught.exception))

    def test_a_spend_claim_that_moves_the_count_is_still_a_contradiction(self):
        """The spend admits ONE divergence — the discharge — and no other. A
        claim that also rewrites `strikes` is refused as before.
        """
        corpus = self.owed_corpus() + [
            ("2026-01-04", self.entry(escalation_strikes=1,
                                      escalation_owed="false",
                                      escalation_spent_on="2026-01-04"))]
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.escalation_state(corpus, ["OBJ-1"])
        self.assertIn("contradicts", str(caught.exception))

    def test_reconstruction_never_recovers_the_spend(self):
        """The rule says outright that it cannot, and over-owing is the
        deliberate direction: deferring an obligation already met costs one
        finding, dropping one that was not met loses it silently."""
        plain = [(date, self.entry()) for date in
                 ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04")]
        state = rf.escalation_state(plain, ["OBJ-1"])["OBJ-1"]
        self.assertFalse(state["verified"])
        self.assertFalse(state["spent"])
        self.assertTrue(state["owed"])

class EscalationEdgeTests(ReviewFindingsTestCase):
    """The branches a mutation pass found unconstrained: the window edges,
    the two silent-date SHAPES, the spend order, the negative cap, the
    declared state, and the broken-link repair path. Each test states the
    value a mutant of the named branch would produce instead.
    """

    def fm(self, outcome="attempted", *, thing="OBJ-1",
           digest="sha256:same", **carried):
        objective = thing.split(".")[0]
        lines = ["type: adr-review", "measured_objectives:",
                 f"  - objective: {objective}"]
        if "." in thing:
            lines.append(f"    part: {thing}")
        lines += [f"    outcome: {outcome}", f"    measure_digest: {digest}"]
        lines.append("    blocker: no baseline" if outcome == "attempted"
                     else "    evidence: a/p.md")
        lines += [f"    {k}: {v}" for k, v in carried.items()]
        return "\n".join(lines)

    # ── the two shapes of a silent date ────────────────────────────────────

    def test_an_explicit_not_assessed_entry_steps_over_the_count(self):
        """A silent date is one recording NEITHER measured nor attempted, so
        an explicit `not-assessed` entry steps over rather than resetting. A
        mutant that resets there reads 1 strike."""
        state = rf.escalation_state(
            [("2026-01-01", self.fm()),
             ("2026-01-02", self.fm("not-assessed")),
             ("2026-01-03", self.fm())], ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], 2)
        # PAIRED CONTROL: a `measured` entry on that same date DOES reset.
        reset = rf.escalation_state(
            [("2026-01-01", self.fm()),
             ("2026-01-02", self.fm("measured")),
             ("2026-01-03", self.fm())], ["OBJ-1"])["OBJ-1"]
        self.assertEqual(reset["strikes"], 1)

    def test_a_structured_report_silent_about_this_thing_steps_over(self):
        """The OTHER silent shape: a structured report whose key names a
        different counted thing. The absent-key shape is covered elsewhere;
        a mutant that resets on this one reads 1 strike."""
        state = rf.escalation_state(
            [("2026-01-01", self.fm()),
             ("2026-01-02", self.fm(thing="OBJ-2")),
             ("2026-01-03", self.fm())], ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], 2)

    # ── the bounded reconstruction window, at both edges ───────────────────

    def test_twelve_consecutive_attempts_all_fit_the_window(self):
        dated = [(f"2026-01-{n:02d}", self.fm()) for n in range(1, 13)]
        self.assertEqual(len(dated), rf.ESCALATION_WINDOW)
        state = rf.escalation_state(dated, ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], rf.ESCALATION_WINDOW)

    def test_a_thirteenth_date_truncates_rather_than_widening(self):
        """A run longer than the window truncates, which DEFERS an
        obligation and never invents one — so the count stops at the window
        width rather than growing with the history."""
        dated = [(f"2026-01-{n:02d}", self.fm()) for n in range(1, 14)]
        state = rf.escalation_state(dated, ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], rf.ESCALATION_WINDOW)
        self.assertLess(state["strikes"], len(dated))

    # ── spend order ────────────────────────────────────────────────────────

    def owed(self, thing, since, *, verified=True):
        return {"strikes": 3, "owed": True, "owed_since": since,
                "spent": False, "verified": verified}

    def test_owed_escalations_order_oldest_first_not_by_id(self):
        """The two orderings disagree here on purpose: `OBJ-2` was owed
        first. A mutant ordering by id alone returns them reversed."""
        state = {"OBJ-1": self.owed("OBJ-1", "2026-09-02"),
                 "OBJ-2": self.owed("OBJ-2", "2026-09-01")}
        self.assertEqual(rf.owed_escalations(state), ["OBJ-2", "OBJ-1"])

    def test_a_tie_on_the_owed_date_breaks_by_part_number(self):
        """Same date, both parts of one objective, the higher part inserted
        first — the order is by part NUMBER, not by insertion or by string.
        """
        state = {"OBJ-1.2": self.owed("OBJ-1.2", "2026-09-01"),
                 "OBJ-1.1": self.owed("OBJ-1.1", "2026-09-01")}
        self.assertEqual(rf.owed_escalations(state), ["OBJ-1.1", "OBJ-1.2"])

    def test_a_negative_budget_spends_nothing_and_defers_everything(self):
        """`spend_escalations` documents a cap that "may be zero or
        negative". A mutant taking `cap_remaining` directly spends one
        obligation on a budget of -1."""
        state = {"OBJ-1": self.owed("OBJ-1", "2026-09-02"),
                 "OBJ-2": self.owed("OBJ-2", "2026-09-01")}
        self.assertEqual(rf.spend_escalations(state, -1),
                         ([], ["OBJ-2", "OBJ-1"]))
        # PAIRED CONTROL: a budget of one spends the oldest.
        self.assertEqual(rf.spend_escalations(state, 1),
                         (["OBJ-2"], ["OBJ-1"]))

    # ── the writer's own claims about the chain ────────────────────────────

    def test_a_declared_unverified_state_is_honoured_over_the_one_step_check(self):
        """The writer may know the chain is broken further back than one
        step, so a declared `unverified` stands even where the mechanical
        check passes."""
        chain = [("2026-01-01", self.fm(escalation_strikes=1,
                                        escalation_owed="false"))]
        declared = chain + [("2026-01-02", self.fm(
            escalation_strikes=2, escalation_owed="false",
            escalation_state="unverified"))]
        control = chain + [("2026-01-02", self.fm(
            escalation_strikes=2, escalation_owed="false"))]
        self.assertFalse(rf.escalation_state(declared, ["OBJ-1"])["OBJ-1"]["verified"])
        self.assertTrue(rf.escalation_state(control, ["OBJ-1"])["OBJ-1"]["verified"])

    def test_a_predecessor_that_named_the_thing_but_carried_no_claim_reconstructs(self):
        """The broken-link repair path: the predecessor NAMED this thing and
        carried no claim, so it supplies no baseline to verify against and
        the walk reconstructs. Stepping it from zero instead would refuse
        this corpus as a contradiction.
        """
        corpus = [("2026-01-01", self.fm()), ("2026-01-02", self.fm()),
                  ("2026-01-03", self.fm(escalation_strikes=3,
                                         escalation_owed="true",
                                         escalation_owed_since="2026-01-03"))]
        state = rf.escalation_state(corpus, ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], 3)
        self.assertTrue(state["owed"])
        self.assertFalse(state["verified"])
        # Reconstruction never keeps a claimed `owed_since`: it cannot know
        # the true first-owed date.
        self.assertIsNone(state["owed_since"])

class CarriedClaimRefusalTests(ReviewFindingsTestCase):
    """`_carried_claim` refuses a claim that is PRESENT but malformed —
    only true ABSENCE falls through to reconstruction. Every refusal here
    survived mutation to `if False:` before these tests existed.
    """

    def entry(self, **carried):
        lines = ["measured_objectives:", "  - objective: OBJ-1",
                 "    outcome: attempted", "    measure_digest: sha256:same",
                 "    blocker: no baseline"]
        lines += [f"    {k}: {v}" for k, v in carried.items()]
        return "\n".join(lines)

    def corpus(self, **carried):
        return [("2026-01-01", self.entry(escalation_strikes=1,
                                          escalation_owed="false")),
                ("2026-01-02", self.entry(**carried))]

    def refusal(self, **carried):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.escalation_state(self.corpus(**carried), ["OBJ-1"])
        return str(caught.exception)

    def test_a_non_integer_strike_count_is_refused(self):
        self.assertIn("not a non-negative integer",
                      self.refusal(escalation_strikes=-1,
                                   escalation_owed="false"))

    def test_an_owed_outside_the_closed_set_is_refused(self):
        self.assertIn("outside the closed set true | false",
                      self.refusal(escalation_strikes=2,
                                   escalation_owed="maybe"))

    def test_owed_with_no_owed_since_is_refused(self):
        self.assertIn("carries the date it was first owed",
                      self.refusal(escalation_strikes=2,
                                   escalation_owed="true"))

    def test_not_owed_alongside_an_owed_since_is_refused(self):
        self.assertIn("discharged obligation carries no owed-since date",
                      self.refusal(escalation_strikes=2,
                                   escalation_owed="false",
                                   escalation_owed_since="2026-01-02"))

    def test_an_escalation_state_outside_the_closed_set_is_refused(self):
        """`unverfied` read as VERIFIED under a permissive check, silently
        demoting an unverified obligation out of its ahead-of-everything
        spend slot."""
        self.assertIn("outside the closed set verified | unverified",
                      self.refusal(escalation_strikes=2,
                                   escalation_owed="false",
                                   escalation_state="unverfied"))

    def test_the_same_corpus_without_the_defect_is_the_control(self):
        """PAIRED POSITIVE CONTROL for all five: the claim shape is otherwise
        exactly the one above, and it is accepted."""
        state = rf.escalation_state(
            self.corpus(escalation_strikes=2, escalation_owed="false",
                        escalation_state="verified"), ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], 2)
        self.assertTrue(state["verified"])

    def test_an_unknown_entry_field_is_refused_never_silently_kept(self):
        """A typo'd carried field was retained verbatim and the entry fell
        through to reconstruction — the chain broke and nothing said why."""
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.measured_objectives(
                "type: adr-review\n"
                + self.entry(eskalation_strikes=3, escalation_owed="false"))
        self.assertIn("this reader does not know", str(caught.exception))


class SpendDateGrammarTests(ReviewFindingsTestCase):
    """`escalation_spent_on` is a DATE, and it is checked like every other
    date-shaped value here. Unchecked, the spend was unfalsifiable: any
    non-empty string cleared the obligation, because the predicate reads
    only whether the field is present.
    """

    def entry(self, outcome="attempted", **carried):
        lines = ["measured_objectives:", "  - objective: OBJ-1",
                 f"    outcome: {outcome}", "    measure_digest: sha256:same"]
        lines.append("    blocker: no baseline" if outcome == "attempted"
                     else "    evidence: a/p.md")
        lines += [f"    {k}: {v}" for k, v in carried.items()]
        return "\n".join(lines)

    def corpus(self, spent_on):
        return [
            ("2026-01-01", self.entry(escalation_strikes=1,
                                      escalation_owed="false")),
            ("2026-01-02", self.entry(escalation_strikes=2,
                                      escalation_owed="false")),
            ("2026-01-03", self.entry(escalation_strikes=3,
                                      escalation_owed="true",
                                      escalation_owed_since="2026-01-03")),
            ("2026-01-04", self.entry(escalation_strikes=4,
                                      escalation_owed="false",
                                      escalation_spent_on=spent_on)),
        ]

    def test_a_spend_date_that_is_not_a_date_is_refused(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.escalation_state(self.corpus("banana"), ["OBJ-1"])
        self.assertIn("not an ISO calendar date", str(caught.exception))

    def test_a_spend_before_the_obligation_existed_is_refused(self):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.escalation_state(self.corpus("1999-01-01"), ["OBJ-1"])
        self.assertIn("the date the obligation was first owed",
                      str(caught.exception))

    def test_a_spend_after_the_report_recording_it_is_refused(self):
        """A pass records a finding it raised, never one it intends to."""
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.escalation_state(self.corpus("2027-01-01"), ["OBJ-1"])
        self.assertIn("a date AFTER the report recording it",
                      str(caught.exception))

    def test_a_real_spend_date_is_the_control(self):
        state = rf.escalation_state(self.corpus("2026-01-04"),
                                    ["OBJ-1"])["OBJ-1"]
        self.assertTrue(state["spent"])
        self.assertFalse(state["owed"])
        self.assertEqual(state["strikes"], 4)

    def test_a_spend_claimed_before_the_third_strike_is_a_contradiction(self):
        """The one conjunct of `_is_spend_of` that is load-bearing rather
        than redundant: `expected.owed`. Drop it and a claim spending an
        obligation that was never owed is accepted.
        """
        early = [("2026-01-01", self.entry(escalation_strikes=1,
                                           escalation_owed="false")),
                 ("2026-01-02", self.entry(escalation_strikes=2,
                                           escalation_owed="false",
                                           escalation_spent_on="2026-01-02"))]
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.escalation_state(early, ["OBJ-1"])
        self.assertIn("contradicts", str(caught.exception))


class ChainBrokenAtPredecessorTests(ReviewFindingsTestCase):
    """The ADR's own literal trigger — "Where that chain is broken by a
    legacy or absent-key report, the count is reconstructed". Reached when
    the NEWEST report carries a claim and the PREVIOUS report's shape is
    `absent` or `flat`, which is a different path from the newest report
    carrying no claim at all.
    """

    STRUCTURED = ("measured_objectives:\n  - objective: OBJ-1\n"
                  "    outcome: attempted\n    measure_digest: sha256:same\n"
                  "    blocker: no baseline\n    escalation_strikes: 1\n"
                  "    escalation_owed: false")
    FLAT = "measured_objectives: [OBJ-1]"
    ABSENT = "dismissed: []"

    def state(self, first):
        return rf.escalation_state(
            [("2026-01-01", "type: adr-review\n" + first),
             ("2026-01-02", "type: adr-review\n" + self.STRUCTURED)],
            ["OBJ-1"])["OBJ-1"]

    def test_a_flat_predecessor_forces_reconstruction(self):
        self.assertFalse(self.state(self.FLAT)["verified"])

    def test_an_absent_key_predecessor_forces_reconstruction(self):
        self.assertFalse(self.state(self.ABSENT)["verified"])

    def test_a_structured_predecessor_is_the_control(self):
        """PAIRED CONTROL: the same newest report against a predecessor that
        DOES carry a claim verifies rather than reconstructing."""
        prior = ("measured_objectives:\n  - objective: OBJ-1\n"
                 "    outcome: not-assessed\n    measure_digest: sha256:same\n"
                 "    escalation_strikes: 0\n    escalation_owed: false")
        self.assertTrue(self.state(prior)["verified"])

    def test_a_single_report_corpus_steps_from_zero(self):
        """First appearance: no earlier report names this thing at all, so
        it steps from a zero baseline rather than reconstructing."""
        state = rf.escalation_state(
            [("2026-01-01", "type: adr-review\n" + self.STRUCTURED)],
            ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], 1)
        self.assertTrue(state["verified"])

    def test_an_empty_corpus_reports_a_zero_state(self):
        state = rf.escalation_state([], ["OBJ-1"])["OBJ-1"]
        self.assertEqual(state["strikes"], 0)
        self.assertFalse(state["owed"])

class RowRefusalAndCellShapeTests(ReviewFindingsTestCase):
    """`_assessment_from_row`'s own refusals, and the em-dash cell the
    shipped template documents. Both derived values state that this function
    "has already refused anything else" — a stated precondition whose
    enforcement was untested.
    """

    def parse_row(self, row):
        body = "\n".join(["# R", "", "## Coverage", "", assessment_table([row])])
        return self.parse(date="2026-09-10", body=body,
                          frontmatter=LIFECYCLE_FM.format(date="2026-09-10"))

    def refusal(self, row):
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            self.parse_row(row)
        return str(caught.exception)

    def test_a_measure_cell_with_no_label_after_the_part_is_refused(self):
        """The one refusal here with NO backstop: the two vocabulary checks
        below are caught again by the legal-pairs check, this one is not."""
        self.assertIn("label", self.refusal(
            ("OBJ-1", "OBJ-1.1 —   ", "resolved", "a/p.md", "d", "serves", "—")))

    def test_an_evidence_value_outside_the_vocabulary_is_refused(self):
        self.assertIn("maybe", self.refusal(
            ("OBJ-1", "OBJ-1.1 — a claim", "maybe", "a/p.md", "d", "serves", "—")))

    def test_a_conclusion_value_outside_the_vocabulary_is_refused(self):
        self.assertIn("maybe", self.refusal(
            ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "d", "maybe", "—")))

    def test_the_same_row_with_legal_values_is_the_control(self):
        report = self.parse_row(
            ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "d", "serves", "—"))
        self.assertEqual(len(report.assessments), 1)

    def test_an_em_dash_cell_reads_as_no_entries_not_as_one_named_dash(self):
        """`—` is the SHIPPED TEMPLATE's own documented value for the domains
        and findings cells, and every other fixture in this file fills them.
        """
        report = self.parse_row(
            ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "—", "serves", "—"))
        assessment = report.assessments[0]
        self.assertEqual(assessment.findings, ())
        self.assertEqual(assessment.domains, ())
        # PAIRED CONTROL: a filled cell yields the entries it names, so the
        # empty read above is the dash's doing and not a dead parser.
        filled = self.parse_row(
            ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "d",
             "serves", "`adr-review-a-finding`"))
        self.assertEqual(filled.assessments[0].findings,
                         ("adr-review-a-finding",))


class NestedSequenceShapeTests(ReviewFindingsTestCase):
    """`_read_block_sequence`'s two remaining branches. Its blank-line and
    comment-line siblings are both pinned; these two were not, and the code
    comment on the deeper-`- ` branch records that an earlier version
    "dropped every entry below it in silence".

    Exercised against the reader DIRECTLY rather than through
    `measured_objectives`, because no field in `ENTRY_FIELDS` takes a list —
    the entry-shape refusal above would reject the fixture before this
    branch ran. The branch is the sequence reader's own robustness, so this
    is the level it is testable at.
    """

    def test_a_nested_sub_list_does_not_end_the_sequence(self):
        entries = rf._read_block_sequence(
            "\n  - objective: OBJ-1\n    outcome: measured\n"
            "    detail:\n      - one\n      - two\n"
            "  - objective: OBJ-2\n    outcome: attempted\n")
        self.assertEqual(len(entries), 2,
                         "the entry BELOW the nested list was dropped")
        self.assertEqual(entries[1][0], "objective: OBJ-2")
        # The nested items stay with the entry that owns them.
        self.assertIn("- one", entries[0])

    def test_a_shallower_dash_item_ends_the_sequence(self):
        """A `- ` at a SHALLOWER indent than the list belongs to something
        else, so the sequence stops there rather than absorbing it."""
        entries = rf._read_block_sequence(
            "\n  - objective: OBJ-1\n    outcome: measured\n"
            "- something: else\n")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0], "objective: OBJ-1")



class BlockedPartVisibilityTests(ReviewFindingsTestCase):
    """ADR-0109 `a-blocked-part-is-counted-in-its-own-right`: a blocked
    part's obligation must stay visible, in this report and across dates."""

    BLOCKED_ROW = ("OBJ-1", "OBJ-1.1 — a claim", "unavailable", "a/p.md",
                  "d", "inconclusive", "—")

    def test_a_blocked_part_omitted_from_measured_objectives_is_refused(self):
        body = "\n".join(["# R", "", "## Coverage", "",
                          assessment_table([self.BLOCKED_ROW])])
        report = self.parse(date="2026-09-10", body=body,
                            frontmatter=LIFECYCLE_FM.format(date="2026-09-10"))
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.validate_structure(report)
        self.assertIn("blocked part is counted in its own right",
                      str(caught.exception))

    def test_the_same_blocked_row_with_a_part_level_entry_is_the_control(self):
        body = "\n".join(["# R", "", "## Coverage", "",
                          assessment_table([self.BLOCKED_ROW])])
        frontmatter = LIFECYCLE_FM.format(date="2026-09-10") + "\n" + "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1", "    part: OBJ-1.1",
            "    outcome: attempted", "    measure_digest: sha256:same",
            "    blocker: no baseline",
        ])
        report = self.parse(date="2026-09-10", body=body,
                            frontmatter=frontmatter)
        self.assertIsNone(rf.validate_structure(report))

    def test_a_part_dropped_from_a_later_reports_key_is_refused(self):
        """The cross-date half: a part that carried a part-level entry on an
        earlier date and is still assessed must keep carrying one."""
        row = ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "d",
              "gap", "—")
        body = "\n".join(["# R", "", "## Coverage", "",
                          assessment_table([row])])
        first_fm = LIFECYCLE_FM.format(date="2026-09-09") + "\n" + "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1", "    part: OBJ-1.1",
            "    outcome: attempted", "    measure_digest: sha256:same",
            "    blocker: no baseline",
        ])
        first = self.parse(date="2026-09-09", body=body, frontmatter=first_fm)
        second_fm = LIFECYCLE_FM.format(date="2026-09-10")
        second = self.parse(date="2026-09-10", body=body,
                            frontmatter=second_fm)
        with self.assertRaises(rf.ReviewFindingsError) as caught:
            rf.check_part_carryover([first, second])
        self.assertIn("carried a part-level", str(caught.exception))

    def test_carrying_the_entry_forward_is_the_control(self):
        """Positive control: the same two dates, with the second report
        keeping the part-level entry — no refusal."""
        row = ("OBJ-1", "OBJ-1.1 — a claim", "resolved", "a/p.md", "d",
              "gap", "—")
        body = "\n".join(["# R", "", "## Coverage", "",
                          assessment_table([row])])
        first_fm = LIFECYCLE_FM.format(date="2026-09-09") + "\n" + "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1", "    part: OBJ-1.1",
            "    outcome: attempted", "    measure_digest: sha256:same",
            "    blocker: no baseline",
        ])
        first = self.parse(date="2026-09-09", body=body, frontmatter=first_fm)
        second_fm = LIFECYCLE_FM.format(date="2026-09-10") + "\n" + "\n".join([
            "measured_objectives:",
            "  - objective: OBJ-1", "    part: OBJ-1.1",
            "    outcome: measured", "    measure_digest: sha256:same",
            "    evidence: a/p.md",
        ])
        second = self.parse(date="2026-09-10", body=body,
                            frontmatter=second_fm)
        self.assertIsNone(rf.check_part_carryover([first, second]))


class HistoricalReportPreservationTests(ReviewFindingsTestCase):
    """PRESERVATION: the two historical reports still parse under ADR-0108.

    Both reports predate `report_grammar` and read under the frozen legacy
    counting rule; neither carries an assessment table. No rule this module
    gained for ADR-0108 may refuse either one — a legacy report returns early
    from `validate_structure`, and the new assessment-table rules apply only
    to rows that exist.

    Reads a dev-only surface (`bionic/adrs/reviews/`), guarded by
    `require_dev_surface` so this test skips against the crux-only staged
    artifact rather than false-failing it.
    """

    def _read(self, name: str) -> tuple[str, str, str]:
        path = REPO / "bionic" / "adrs" / "reviews" / name
        require_dev_surface(self, path, f"bionic/adrs/reviews/{name}")
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?\n)---\n", text, re.DOTALL)
        self.assertIsNotNone(match, f"{name} carries no frontmatter block")
        frontmatter = match.group(1)
        body = text[match.end():]
        date = name.removesuffix(".md")
        return frontmatter, body, date

    def test_the_2026_09_07_report_still_parses_as_legacy_six(self):
        frontmatter, body, date = self._read("2026-09-07.md")
        report = rf.parse_report(date=date, frontmatter=frontmatter, body=body)
        self.assertTrue(report.is_legacy)
        self.assertEqual(report.raised, 6)
        self.assertEqual(report.assessments, ())
        self.assertIsNone(rf.validate_structure(report))
        outcomes, shape = rf.measured_objectives(frontmatter)
        self.assertEqual((outcomes, shape), ({}, "absent"))

    def test_the_2026_09_08_report_still_parses_as_legacy_five(self):
        frontmatter, body, date = self._read("2026-09-08.md")
        report = rf.parse_report(date=date, frontmatter=frontmatter, body=body)
        self.assertTrue(report.is_legacy)
        self.assertEqual(report.raised, 5)
        self.assertEqual(report.assessments, ())
        self.assertIsNone(rf.validate_structure(report))
        outcomes, shape = rf.measured_objectives(frontmatter)
        self.assertEqual(shape, "flat")
        self.assertEqual(len(outcomes), 10)
        self.assertTrue(all(fields["outcome"] == "attempted"
                            for fields in outcomes.values()))

    #: Pinned SHA-256 of each historical report's exact bytes. ADR-0109 must
    #: not touch either file; this is the mechanical guard for "unmodified"
    #: beyond the parse-shape assertions above.
    _PINNED_SHA256 = {
        "2026-09-07.md":
            "f584c6fd7f464b0fdcbc1a8ddb9919289dbfcfe2fa628a55eaa149078085ef32",
        "2026-09-08.md":
            "1598435b6be18b63f0c47be06f541fc365dbd23ca106e8001aa116a7b57719eb",
    }

    def test_both_historical_reports_are_byte_identical_and_unmodified(self):
        import hashlib
        for name, pinned in self._PINNED_SHA256.items():
            path = REPO / "bionic" / "adrs" / "reviews" / name
            require_dev_surface(self, path, f"bionic/adrs/reviews/{name}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(pinned, digest,
                             f"{name} changed bytes; ADR-0109 must not touch "
                             "the two historical reports")


if __name__ == "__main__":
    unittest.main()
