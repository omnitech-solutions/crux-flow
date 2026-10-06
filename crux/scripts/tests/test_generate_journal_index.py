"""Tests for the journal-index derivation and its vendored regenerator.

TWO HALVES, ONE FILE. The first half drives the shipped helpers in
`crux/scripts/journal_index.py` directly — the positional collapse an unclosed
fence produces, the five fence shapes, the date hardening, and the three
render/parse contracts. The second half drives
`crux/scripts/generate-journal-index.py` as a subprocess in a miniature repo
root — the exit map, the refuse-and-preserve lane, the filename grammar, the
symlink guards, the output-index guard, and check mode.

NO HELPER IS RE-DECLARED HERE. Every name under test is imported from the
shipped module. A test file that carries its own copy of the parser can only
compare that copy with itself, which is the property that made the pre-ship
fixture helpers unable to find a shared defect.

EVERY FIXTURE LIVES UNDER A `tempfile.TemporaryDirectory()`. NOTHING reads or
writes the live `bionic/` tree: these are properties of the CODE, not of this
repo's content, and a test that read the live tree would false-fail when
`sync.sh` runs this suite against the crux-only staged artifact (which carries
no `bionic/`). `journal_index.py` and `generate-journal-index.py` both ship
under `crux/scripts/`, so both are inside the staged allowlist.

EVERY EXPECTED VALUE IS WRITTEN INDEPENDENTLY of the implementation under
test, from the fixture the test itself builds. None is copied from a
historical note about this repo's own journal, and none is computed by calling
the function being asserted.

EVERY ABSENCE ASSERTION IS PAIRED WITH A POSITIVE CONTROL. "Nothing was
written", "no entry was opened" and "the victim survives" are all satisfied by
a script that never ran, so each is accompanied by a near-identical fixture
with the guard removed from the input's path, showing the same machinery does
produce the write / the entry / the read when nothing refuses it.

Stdlib only. Run:
  uv run python3 -m unittest discover -s crux/scripts/tests -p 'test_generate_journal_index.py'
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS / "generate-journal-index.py"
#: `crux/skills/` crosses the sync boundary, so reading a SKILL.md here is
#: staged-artifact safe and needs no dev-surface guard.
CHECK_DRIFT_SKILL = SCRIPTS.parent / "skills" / "check-drift" / "SKILL.md"

sys.path.insert(0, str(SCRIPTS))

import journal_index  # noqa: E402
from journal_index import (  # noqa: E402
    CATEGORIES,
    MONTH_RE,
    derive_row,
    find_unclosed_fence,
    journal_entries,
    parse_index_row,
    render_index,
    unknown_category_headings,
)

MONTH = "2026-09"
DASH = "—"  # EM DASH, the empty-cell placeholder

# The index header and separator, spelled here as a literal rather than read
# off any file on disk. Byte-equality against a rendered index is only
# meaningful if the expectation is written independently of the renderer.
HEADER = (
    "# Journal index\n"
    "\n"
    "_Last updated: {last_updated}_\n"
    "\n"
    "| month | first entry | last entry | entries | top categories |\n"
    "|-------|-------------|------------|---------|----------------|\n"
)


def lines(*rows: str) -> str:
    """A document from explicit line literals, newline-terminated.

    Fixtures are built line by line so a fence run, an indent, or a lone
    control character is visible in the source rather than buried in a triple-
    quoted block.
    """
    return "".join(row + "\n" for row in rows)


def heading(date: str, category: str = "bug", subject: str = "x",
            time: str = "10:00") -> str:
    return f"## [{date} {time}] {category} | {subject}"


# ═══════════════════════════════════════════════════════════════════════════
# D1 — the shipped helpers, driven directly
# ═══════════════════════════════════════════════════════════════════════════

class PositionalCollapseTests(unittest.TestCase):
    """An unclosed opener swallows the entries BELOW it, not the whole file.

    The month file is newest-first, so an opener injected immediately under
    entry #N leaves entries #1..#N above it and runs the fence to end of file
    over everything below. The surviving count is therefore N — the count of
    entries AT OR ABOVE the injection point — and not the constant `1` the
    newest-entry case makes it easy to generalize from. `1` is one row of this
    table, at the one position where "at or above" means "the newest entry
    alone".

    Two of the five positions are deliberately NON-newest and are the rows
    that discriminate: #2 and #5. If the collapse really were to `1`,
    positions #2, #5, #15 and #30 would each report `1` and fail here.
    """

    TOTAL = 30

    def month_file(self, inject_after: int | None = None) -> str:
        """`TOTAL` entries newest-first, with an optional unclosed opener.

        Entry #k is dated `2026-09-<TOTAL + 1 - k>`, so #1 is the newest.
        `inject_after=N` puts a bare ``` opener directly beneath entry #N's
        body — never at the very top, so the fixture cannot pass by accident.
        """
        out = ["# Journal " + DASH + " " + MONTH, ""]
        for k in range(1, self.TOTAL + 1):
            day = self.TOTAL + 1 - k
            out += [heading(f"{MONTH}-{day:02d}", "bug", f"entry {k}"), "",
                    "One body line.", ""]
            if inject_after == k:
                out += ["```text", ""]
        return lines(*out)

    def test_the_surviving_count_is_the_entries_at_or_above_the_opener(self):
        # Each expected count is written out here, position by position, from
        # the fixture's own construction: an opener under entry #N leaves N
        # entries above it. Nothing below reads the implementation.
        for position, expected in ((1, 1), (2, 2), (5, 5), (15, 15), (30, 30)):
            with self.subTest(position=position):
                text = self.month_file(inject_after=position)
                self.assertEqual(len(journal_entries(text, MONTH)), expected)
                self.assertEqual(derive_row(MONTH, text)["entries"], expected)

    def test_positive_control_the_same_fixture_without_an_opener_keeps_all_30(self):
        """The fixture can carry 30 entries, so the collapse is the fence."""
        text = self.month_file(inject_after=None)
        self.assertEqual(len(journal_entries(text, MONTH)), 30)
        self.assertIsNone(find_unclosed_fence(text))

    def test_the_newest_entry_case_is_one_row_of_the_table_not_the_rule(self):
        """`1` and `2` come from the SAME fixture, one line apart."""
        at_one = journal_entries(self.month_file(inject_after=1), MONTH)
        at_two = journal_entries(self.month_file(inject_after=2), MONTH)
        self.assertEqual([s for _, s in at_one], ["bug"])
        self.assertEqual(len(at_one), 1)
        self.assertEqual(len(at_two), 2)
        self.assertEqual([d for d, _ in at_two], ["2026-09-30", "2026-09-29"])


class FenceShapeTests(unittest.TestCase):
    """The five fence shapes the entry count has to read the way CommonMark does."""

    ABOVE = heading("2026-09-05", "bug", "above")
    BELOW = heading("2026-09-04", "learning", "below")

    def test_an_unclosed_tilde_opener_swallows_everything_below_it(self):
        text = lines(self.ABOVE, "", "~~~", "quoted", self.BELOW)
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-05", "bug")])
        self.assertEqual(find_unclosed_fence(text), 3)

    def test_an_unclosed_bare_backtick_opener_swallows_everything_below_it(self):
        text = lines(self.ABOVE, "", "```", "quoted", self.BELOW)
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-05", "bug")])
        self.assertEqual(find_unclosed_fence(text), 3)

    def test_a_longer_run_closes_a_shorter_one_and_the_row_survives_intact(self):
        text = lines(self.ABOVE, "", "```", "quoted", "````", "", self.BELOW)
        self.assertEqual(
            journal_entries(text, MONTH),
            [("2026-09-05", "bug"), ("2026-09-04", "learning")])
        self.assertIsNone(find_unclosed_fence(text))
        self.assertEqual(
            derive_row(MONTH, text),
            {"month": MONTH, "first": "2026-09-04", "last": "2026-09-05",
             "entries": 2, "categories": "bug, learning"})

    def test_a_shorter_run_does_not_close_a_longer_one(self):
        text = lines(self.ABOVE, "", "````", "quoted", "```", "", self.BELOW)
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-05", "bug")])
        self.assertEqual(find_unclosed_fence(text), 3)

    def test_a_tilde_run_does_not_close_a_backtick_opener(self):
        text = lines(self.ABOVE, "", "```", "quoted", "~~~", "", self.BELOW)
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-05", "bug")])
        self.assertEqual(find_unclosed_fence(text), 3)


class DateHardeningTests(unittest.TestCase):
    """`[0-9]` plus a calendar round-trip, asserted where each one bites."""

    REAL = heading("2026-09-08", "bug", "real")

    def test_a_well_formed_but_unreal_date_opens_no_entry(self):
        text = lines(self.REAL, "", heading("2026-09-99", "bug", "forged"))
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-08", "bug")])

    def test_positive_control_the_same_line_with_a_real_day_opens_an_entry(self):
        text = lines(self.REAL, "", heading("2026-09-09", "bug", "forged"))
        self.assertEqual(
            journal_entries(text, MONTH),
            [("2026-09-08", "bug"), ("2026-09-09", "bug")])

    def test_a_two_digit_arabic_indic_day_opens_no_entry(self):
        forged_date = "2026-09-٩٩"  # ARABIC-INDIC DIGIT NINE, twice
        text = lines(self.REAL, "", heading(forged_date, "bug", "forged"))
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-08", "bug")])

    def test_a_two_digit_arabic_indic_day_cannot_capture_the_last_entry_cell(self):
        """The cell the forged date was reaching for, and why `\\d` loses it.

        A two-digit Arabic-Indic day sorts ABOVE every ASCII date under plain
        string comparison, so a `\\d`-spelled pattern would have put a non-ASCII
        string in the `last entry` cell. Both legs of that claim are asserted
        here against the counterfactual pattern, so the row below is a measured
        consequence rather than an assertion of faith.
        """
        forged_date = "2026-09-٩٩"
        forged_line = heading(forged_date, "bug", "forged")
        loose = re.compile(r"^## \[(?P<date>\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\] "
                           r"(?P<category>" + "|".join(CATEGORIES) + r") \| \S")
        self.assertIsNotNone(loose.match(forged_line))       # `\d` admits it
        self.assertGreater(forged_date, "2026-09-08")        # and it sorts first

        text = lines(self.REAL, "", forged_line)
        self.assertEqual(derive_row(MONTH, text)["last"], "2026-09-08")
        self.assertEqual(derive_row(MONTH, text)["entries"], 1)

    def test_a_pipe_inside_the_category_token_matches_nothing(self):
        text = lines(self.REAL, "",
                     "## [2026-09-09 10:00] re|view | forged")
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-08", "bug")])

    def test_positive_control_the_same_category_without_the_pipe_matches(self):
        text = lines(self.REAL, "",
                     "## [2026-09-09 10:00] review | forged")
        self.assertEqual(
            journal_entries(text, MONTH),
            [("2026-09-08", "bug"), ("2026-09-09", "review")])

    def test_extra_pipes_in_the_subject_are_ignored(self):
        text = lines("## [2026-09-08 10:00] bug | a | b | c")
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-08", "bug")])


class DeriveRowTests(unittest.TestCase):

    def month_file(self, *entries: tuple[str, str]) -> str:
        out: list[str] = ["# Journal " + DASH + " " + MONTH, ""]
        for date, category in entries:
            out += [heading(date, category, "s"), "", "One body line.", ""]
        return lines(*out)

    def test_the_rollup_breaks_a_count_tie_name_ascending(self):
        # decision x3; learning x2 and refactor x2 tie at second; review x1
        # loses the third slot to the tie-break, not to its own count.
        text = self.month_file(
            ("2026-09-01", "decision"), ("2026-09-02", "decision"),
            ("2026-09-03", "decision"), ("2026-09-04", "learning"),
            ("2026-09-05", "learning"), ("2026-09-06", "refactor"),
            ("2026-09-07", "refactor"), ("2026-09-08", "review"))
        self.assertEqual(derive_row(MONTH, text)["categories"],
                         "decision, learning, refactor")

    def test_the_rollup_is_cut_at_three_names(self):
        text = self.month_file(
            ("2026-09-01", "bug"), ("2026-09-02", "decision"),
            ("2026-09-03", "learning"), ("2026-09-04", "meeting"),
            ("2026-09-05", "misc"))
        row = derive_row(MONTH, text)
        self.assertEqual(row["categories"], "bug, decision, learning")
        self.assertEqual(len(row["categories"].split(", ")), 3)
        self.assertEqual(row["entries"], 5)

    def test_an_entry_dated_outside_the_month_contributes_nothing(self):
        text = self.month_file(
            ("2026-08-31", "decision"), ("2026-09-01", "bug"),
            ("2026-10-01", "review"))
        self.assertEqual(
            derive_row(MONTH, text),
            {"month": MONTH, "first": "2026-09-01", "last": "2026-09-01",
             "entries": 1, "categories": "bug"})

    def test_positive_control_the_same_entries_inside_the_month_all_count(self):
        text = self.month_file(
            ("2026-09-30", "decision"), ("2026-09-01", "bug"),
            ("2026-09-15", "review"))
        self.assertEqual(
            derive_row(MONTH, text),
            {"month": MONTH, "first": "2026-09-01", "last": "2026-09-30",
             "entries": 3, "categories": "bug, decision, review"})

    def test_an_all_empty_month_yields_zero_and_three_em_dashes(self):
        text = lines("# Journal " + DASH + " " + MONTH, "",
                     "_Append-only. Newest entries at the top._")
        self.assertEqual(
            derive_row(MONTH, text),
            {"month": MONTH, "first": DASH, "last": DASH,
             "entries": 0, "categories": DASH})


class RenderIndexTests(unittest.TestCase):

    ROWS = [
        {"month": "2026-09", "first": "2026-09-01", "last": "2026-09-08",
         "entries": 4, "categories": "decision, learning, review"},
        {"month": "2026-08", "first": DASH, "last": DASH,
         "entries": 0, "categories": DASH},
    ]

    def test_the_body_is_byte_equal_to_the_written_out_fixture(self):
        expected = HEADER.format(last_updated="2026-09-08") + (
            "| 2026-09 | 2026-09-01 | 2026-09-08 | 4 "
            "| decision, learning, review |\n"
            f"| 2026-08 | {DASH} | {DASH} | 0 | {DASH} |\n"
        )
        self.assertEqual(render_index(self.ROWS, "2026-09-08"), expected)

    def test_last_updated_is_the_maximum_over_the_rows_carrying_a_date(self):
        # The caller computes the maximum; this pins the cell the renderer
        # emits for it, against a value written out here rather than derived.
        dated = [r["last"] for r in self.ROWS if r["last"] != DASH]
        self.assertEqual(dated, ["2026-09-08"])
        self.assertIn("_Last updated: 2026-09-08_\n",
                      render_index(self.ROWS, max(dated)))

    def test_an_em_dash_stands_in_when_no_row_carries_a_date(self):
        undated = [self.ROWS[1]]
        self.assertEqual(
            render_index(undated, DASH),
            HEADER.format(last_updated=DASH)
            + f"| 2026-08 | {DASH} | {DASH} | 0 | {DASH} |\n")

    def test_an_empty_row_set_still_renders_the_header_and_separator(self):
        self.assertEqual(render_index([], DASH), HEADER.format(last_updated=DASH))


class ParseIndexRowTests(unittest.TestCase):

    def test_an_em_dash_entries_cell_is_returned_rather_than_raised_on(self):
        text = HEADER.format(last_updated=DASH) + \
            f"| 2026-09 | {DASH} | {DASH} | {DASH} | {DASH} |\n"
        row = parse_index_row(text, MONTH)
        self.assertEqual(row["entries"], DASH)
        self.assertNotIsInstance(row["entries"], int)

    def test_positive_control_an_integer_cell_comes_back_as_an_int(self):
        text = HEADER.format(last_updated="2026-09-08") + \
            "| 2026-09 | 2026-09-01 | 2026-09-08 | 7 | bug |\n"
        row = parse_index_row(text, MONTH)
        self.assertEqual(row["entries"], 7)
        self.assertIsInstance(row["entries"], int)

    def test_an_absent_month_returns_none(self):
        self.assertIsNone(parse_index_row(HEADER.format(last_updated=DASH), MONTH))



class CategoryEnumTests(unittest.TestCase):
    """The category token is matched against a closed nine-member enum.

    The sibling `test_a_pipe_inside_the_category_token_matches_nothing` tests
    a DIFFERENT property: a token carrying the cell delimiter. This class
    tests the plain case — a well-formed word that is simply not one of the
    nine — because a pattern that admitted arbitrary words would still refuse
    the pipe-injected one and pass that lane.
    """

    REAL = heading("2026-09-08", "bug", "a real entry")

    def test_a_plain_out_of_enum_category_opens_no_entry(self):
        self.assertNotIn("chore", CATEGORIES)
        text = lines(self.REAL, "", "## [2026-09-09 10:00] chore | tidied up")
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-08", "bug")])
        # And it never reaches the rollup cell it would otherwise populate.
        self.assertEqual(derive_row(MONTH, text)["categories"], "bug")

    def test_positive_control_an_in_enum_category_in_that_slot_opens_an_entry(self):
        text = lines(self.REAL, "", "## [2026-09-09 10:00] refactor | tidied up")
        self.assertEqual(journal_entries(text, MONTH),
                         [("2026-09-08", "bug"), ("2026-09-09", "refactor")])
        self.assertEqual(derive_row(MONTH, text)["categories"], "bug, refactor")


class UnknownCategoryNearMissTests(unittest.TestCase):
    """A heading that is an entry in every respect but its category.

    `CategoryEnumTests` above fixes what such a heading does NOT do: it opens
    no entry. This class fixes what it DOES do — it is reported, with the line
    it sits on and the token that failed, so a category added to the schema
    and not to `CATEGORIES` reddens a gate instead of shrinking a count. The
    near miss is bounded deliberately: only a heading whose date is real and
    whose shape is otherwise exact qualifies, so a forged date is diagnosed as
    a date rather than mislabelled a category.
    """

    def test_an_out_of_enum_category_is_reported_with_its_line_and_token(self):
        self.assertNotIn("chore", CATEGORIES)
        text = lines(heading("2026-09-08"), "", "## [2026-09-09 10:00] chore | tidied up")
        self.assertEqual(unknown_category_headings(text), [(3, "chore")])

    def test_the_reported_line_number_is_one_based_and_counts_every_line(self):
        text = lines("# Journal", "", "body", "", "## [2026-09-09 10:00] chore | x")
        self.assertEqual(unknown_category_headings(text), [(5, "chore")])

    def test_every_near_miss_is_reported_not_only_the_first(self):
        text = lines("## [2026-09-09 10:00] chore | x",
                     "## [2026-09-10 10:00] shipped | y")
        self.assertEqual(unknown_category_headings(text),
                         [(1, "chore"), (2, "shipped")])

    def test_a_near_miss_inside_a_fence_is_content_and_is_not_reported(self):
        text = lines("```", "## [2026-09-09 10:00] chore | x", "```")
        self.assertEqual(unknown_category_headings(text), [])

    def test_a_near_miss_under_an_unclosed_fence_is_not_reported(self):
        # The opener runs to end of file per the shared fence subset, so every
        # line below it is content. The driver refuses such a file for the
        # unclosed fence itself, which is the accurate diagnosis.
        text = lines("~~~", "## [2026-09-09 10:00] chore | x")
        self.assertEqual(unknown_category_headings(text), [])

    def test_an_in_enum_heading_is_not_reported(self):
        # PAIRED POSITIVE CONTROL: every category the enum carries is admitted
        # in the same slot the near miss occupies.
        for category in CATEGORIES:
            with self.subTest(category=category):
                text = lines(f"## [2026-09-09 10:00] {category} | x")
                self.assertEqual(unknown_category_headings(text), [])

    def test_an_unreal_date_is_not_reported_as_a_category_near_miss(self):
        # The date is the defect; calling it a category near miss would send a
        # reader to the wrong token.
        text = lines("## [2026-09-31 10:00] chore | x")
        self.assertEqual(unknown_category_headings(text), [])

    def test_a_non_ascii_digit_date_is_not_reported(self):
        text = lines("## [2026-09-\u0669\u0669 10:00] chore | x")
        self.assertEqual(unknown_category_headings(text), [])

    def test_a_heading_with_no_subject_is_not_reported(self):
        # ` | ` with nothing after it is not an entry heading in any category,
        # so it is not a category near miss either.
        text = lines("## [2026-09-09 10:00] chore | ")
        self.assertEqual(unknown_category_headings(text), [])

    def test_ordinary_prose_and_other_headings_are_not_reported(self):
        text = lines("# Journal", "## Notes", "A line about chore work.",
                     "## [2026-09-09] chore | no time component")
        self.assertEqual(unknown_category_headings(text), [])

    def test_a_category_differing_only_in_case_is_reported(self):
        # The enum is case-sensitive, so `Bug` is outside it — and it is
        # exactly the near miss a human hand-edit produces.
        text = lines("## [2026-09-09 10:00] Bug | x")
        self.assertEqual(unknown_category_headings(text), [(1, "Bug")])

    def test_the_live_journal_tree_produces_no_near_miss(self):
        # FALSE-POSITIVE CONTROL against real content rather than fixtures: a
        # guard calibrated only on its own exemplar cannot see a false
        # positive. Skipped where the dogfood tree is absent (a staged
        # artifact carries `crux/` without `bionic/`).
        journal_dir = SCRIPTS.parent.parent / "bionic" / "journal"
        if not journal_dir.is_dir():
            self.skipTest("no dogfood journal tree in this checkout")
        for month_file in sorted(journal_dir.glob("*.md")):
            with self.subTest(month=month_file.name):
                found = unknown_category_headings(
                    month_file.read_text(encoding="utf-8"))
                self.assertEqual(found, [], f"{month_file.name}: {found}")


class BalancedFenceTests(unittest.TestCase):
    """A heading inside a CLOSED fence is content, for the tilde run too.

    `FenceShapeTests` above covers UNCLOSED openers, which collapse the file
    positionally. This class covers the balanced case, where the file is
    well-formed and only the wrapped heading is suppressed. The backtick
    equivalent is pinned by `test_review_recording_contract.py`'s
    `FenceAwareCountTests`; the tilde equivalent had no lane at all.
    """

    REAL = heading("2026-09-08", "bug", "a real entry")
    QUOTED = heading("2026-09-09", "decision", "a quoted heading")

    def test_a_balanced_tilde_fence_hides_the_heading_it_wraps(self):
        text = lines(self.REAL, "", "~~~text", self.QUOTED, "~~~")
        # Balanced, so this is the suppression lane and not the collapse lane.
        self.assertIsNone(find_unclosed_fence(text))
        self.assertEqual(journal_entries(text, MONTH), [("2026-09-08", "bug")])
        self.assertEqual(derive_row(MONTH, text)["entries"], 1)

    def test_positive_control_the_same_heading_unfenced_opens_an_entry(self):
        text = lines(self.REAL, "", self.QUOTED)
        self.assertEqual(journal_entries(text, MONTH),
                         [("2026-09-08", "bug"), ("2026-09-09", "decision")])
        self.assertEqual(derive_row(MONTH, text)["entries"], 2)


class WrittenPolicyTests(unittest.TestCase):
    """The `WRITTEN POLICY` block in `journal_index.py`'s module docstring.

    The block states its own purpose — "so a test can assert against it
    rather than against this module's behaviour" — and until this class no
    test asserted against it, so the policy prose and the code could drift
    apart with the suite green. Each clause below is written out here
    independently of the docstring's line wrapping (the block is whitespace-
    normalised first) and is PAIRED with the behaviour it describes, so a
    clause cannot survive as prose after the behaviour it names is gone.
    """

    #: Every clause, written out here rather than sliced from the docstring.
    CLAUSES = (
        "so a test can assert against it rather than against this module's behaviour",
        "four ASCII digits, a literal hyphen, two ASCII digits, a literal hyphen, "
        "two ASCII digits",
        "never matched with `\\d`, which admits non-ASCII digit scripts",
        "round-trips `datetime.date.fromisoformat` as a real calendar date",
        "it opens no entry, and it is left untouched as body prose",
        "A heading whose category is not one of the enum members, "
        "case-sensitive and no superstring, is likewise not an entry heading",
        "`unknown_category_headings` returns its 1-based line number and its "
        "category token so the caller can refuse the file",
        "A heading whose date is not a real calendar date is not reported as a "
        "near miss",
        "the entry is dropped from the count, the dates, and the rollup bounded "
        "by that month",
    )

    def policy(self) -> str:
        """The block, whitespace-normalised, from the shipped docstring."""
        doc = journal_index.__doc__
        start = doc.index("WRITTEN POLICY")
        end = doc.index("This module is stdlib-only", start)
        return " ".join(doc[start:end].split())

    def test_the_block_states_every_clause(self):
        policy = self.policy()
        for clause in self.CLAUSES:
            with self.subTest(clause=clause[:40]):
                self.assertIn(clause, policy)

    def test_the_block_is_not_matched_by_an_arbitrary_sentence(self):
        # PAIRED POSITIVE CONTROL for the substring assertions above: the
        # block is finite text, not something every string is found in.
        self.assertNotIn("a heading in a fence opens an entry", self.policy())

    def test_each_clause_describes_live_behaviour(self):
        # A real, in-month, in-enum heading: the admissible case the four
        # refusals below each deviate from by exactly one property.
        self.assertEqual(journal_entries(lines(heading("2026-09-08")), MONTH),
                         [("2026-09-08", "bug")])
        # "round-trips `datetime.date.fromisoformat` as a real calendar date"
        self.assertEqual(journal_entries(lines(heading("2026-09-31")), MONTH), [])
        # "never matched with `\d`, which admits non-ASCII digit scripts"
        self.assertEqual(journal_entries(lines(heading("2026-09-\u0669\u0669")), MONTH), [])
        # "not one of the enum members"
        self.assertIn("release", CATEGORIES)  # the enum grows; the clause no longer pins a count
        self.assertEqual(
            journal_entries(lines("## [2026-09-08 10:00] chore | x"), MONTH), [])
        # "the entry is dropped from the count, the dates, and the rollup
        # bounded by that month"
        self.assertEqual(journal_entries(lines(heading("2026-08-31")), MONTH), [])
        # "`unknown_category_headings` returns its 1-based line number and its
        # category token so the caller can refuse the file"
        self.assertEqual(
            unknown_category_headings(lines("## [2026-09-08 10:00] chore | x")),
            [(1, "chore")])
        # "A heading whose date is not a real calendar date is not reported as
        # a near miss"
        self.assertEqual(
            unknown_category_headings(lines("## [2026-09-31 10:00] chore | x")), [])


# ═══════════════════════════════════════════════════════════════════════════
# D2 — the driver, run as a subprocess in a miniature repo root
# ═══════════════════════════════════════════════════════════════════════════

# The tree-relative paths the driver quotes back in its JSON. These are
# EXPECTED-VALUE LITERALS compared against a payload, never a path this suite
# reads: every fixture lives under a `tempfile.TemporaryDirectory()` and the
# only `bionic/` any lane touches is the disposable one it just built.
REL_INDEX = str(Path("bionic") / "journal" / "index.md")
REL_MONTH = str(Path("bionic") / "journal" / f"{MONTH}.md")
REL_DOTFILE = str(Path("bionic") / "journal" / ".hidden")
WITNESS = "OUTSIDE-VICTIM-WITNESS\n"

CLEAN_MONTH = lines(
    "# Journal " + DASH + " " + MONTH, "",
    heading("2026-09-08", "review", "a decision review"), "",
    "One body line.", "",
    heading("2026-09-01", "bug", "a defect"), "",
    "One body line.", "",
)


class JournalCliTestCase(unittest.TestCase):
    """A disposable repo root carrying a `bionic/journal/` surface."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        self.docs = self.root / "bionic"
        self.docs.mkdir()
        (self.docs / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [journal]\n', encoding="utf-8")
        self.journal = self.docs / "journal"
        self.journal.mkdir()
        self.index = self.journal / "index.md"

    # ── drivers ─────────────────────────────────────────────────────────────

    def run_cli(self, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        # `timeout` so a regenerator that hangs fails this suite instead of
        # hanging it. 60s is far above the observed runtime and is a liveness
        # bound, not a performance assertion.
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.root), *args],
            input=stdin if stdin is not None else b"",
            capture_output=True, timeout=60)

    def out(self, result: subprocess.CompletedProcess) -> str:
        return result.stdout.decode("utf-8")

    def err(self, result: subprocess.CompletedProcess) -> str:
        return result.stderr.decode("utf-8", "replace")

    def payload(self, result: subprocess.CompletedProcess) -> dict:
        return json.loads(self.out(result))

    def write_month(self, month: str, text: str) -> Path:
        path = self.journal / f"{month}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def outside_victim(self) -> Path:
        """A file outside the repo root, carrying a witness string."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        victim = Path(tmp.name) / "victim.md"
        victim.write_text(WITNESS, encoding="utf-8")
        return victim

    def seed_written_index(self) -> bytes:
        """One clean month, regenerated. Returns the index bytes on disk."""
        self.write_month(MONTH, CLEAN_MONTH)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        return self.index.read_bytes()


class ExitMapTests(JournalCliTestCase):

    def test_write_mode_reports_the_written_relative_path(self):
        self.write_month(MONTH, CLEAN_MONTH)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result), {"written": REL_INDEX})
        self.assertEqual(
            self.index.read_text(encoding="utf-8"),
            HEADER.format(last_updated="2026-09-08")
            + "| 2026-09 | 2026-09-01 | 2026-09-08 | 2 | bug, review |\n")

    def test_dry_run_is_clean_after_a_write(self):
        self.seed_written_index()
        result = self.run_cli("--dry-run")
        self.assertEqual(result.returncode, 0, self.out(result) + self.err(result))
        # `orphan_rows` is deliberately ABSENT from this payload. The sibling
        # `generate-reviews-index.py` computes one; this regenerator carried
        # the key hardcoded to `[]`, which reported "no orphans" on a corpus it
        # had never inspected. The key was dropped rather than faked.
        self.assertEqual(self.payload(result), {"drift": False, "path": REL_INDEX})
        self.assertNotIn("orphan_rows", self.payload(result))

    def test_drift_exits_one_and_names_the_drifted_path(self):
        self.seed_written_index()
        # A third entry lands in the month file; the index keeps the old row.
        self.write_month(MONTH, CLEAN_MONTH
                         + lines("", heading("2026-09-09", "learning", "later"),
                                 "", "One body line."))
        result = self.run_cli("--dry-run")
        self.assertEqual(result.returncode, 1, self.out(result))
        payload = self.payload(result)
        self.assertIs(payload["drift"], True)
        self.assertEqual(payload["path"], REL_INDEX)
        self.assertNotIn("validation_errors", payload)
        # PAIRED POSITIVE CONTROL: write mode repairs it and the gate reads
        # clean, so `drift: true` above was the stale row and not a broken gate.
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.run_cli("--dry-run").returncode, 0)

    def test_an_absent_journal_directory_reports_surface_absent_at_exit_zero(self):
        self.journal.rmdir()
        for args in ((), ("--dry-run",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, self.err(result))
                payload = self.payload(result)
                self.assertIs(payload["surface_absent"], True)
                self.assertEqual(payload["path"], REL_INDEX)
        # PAIRED POSITIVE CONTROL: with the surface present the discriminator
        # is absent, so the assertion above distinguishes two states rather
        # than naming a key the script always sets.
        self.journal.mkdir()
        self.write_month(MONTH, CLEAN_MONTH)
        control = self.payload(self.run_cli("--dry-run"))
        self.assertNotIn("surface_absent", control)
        self.assertIs(control["drift"], True)

    def test_last_updated_is_the_maximum_over_the_dated_rows(self):
        self.write_month(MONTH, CLEAN_MONTH)
        self.write_month("2026-08", lines("# Journal " + DASH + " 2026-08", "",
                                          "No entries yet."))
        self.assertEqual(self.run_cli().returncode, 0)
        body = self.index.read_text(encoding="utf-8")
        self.assertIn("_Last updated: 2026-09-08_\n", body)
        self.assertIn(f"| 2026-08 | {DASH} | {DASH} | 0 | {DASH} |\n", body)

    def test_an_em_dash_stands_in_when_no_month_carries_an_entry(self):
        self.write_month(MONTH, lines("# Journal " + DASH + " " + MONTH, "",
                                      "No entries yet."))
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertIn(f"_Last updated: {DASH}_\n",
                      self.index.read_text(encoding="utf-8"))


class OpenFenceRefusalTests(JournalCliTestCase):
    """A month file ending inside an open fence: refuse, and preserve the index.

    The refusal is a DOCUMENT finding — a human hand-edited a journal file — so
    it takes the exit-1 `validation_errors` lane, never the exit-2 environment
    lane. The exit code alone cannot tell this from a drift verdict, so every
    assertion pins the payload as its content discriminator.
    """

    #: Line 5 (1-based) is the opener. Written out here rather than searched
    #: for, and cross-checked against the fixture below.
    OPENER_LINE = 5
    DEFECTIVE = lines(
        "# Journal " + DASH + " " + MONTH,           # 1
        "",                                          # 2
        heading("2026-09-08", "review", "above"),    # 3
        "",                                          # 4
        "```text",                                   # 5  <- never closed
        heading("2026-09-01", "bug", "swallowed"),   # 6
    )

    def test_the_fixtures_opener_really_sits_on_the_named_line(self):
        self.assertEqual(self.DEFECTIVE.split("\n")[self.OPENER_LINE - 1], "```text")

    def test_both_modes_refuse_and_the_existing_index_bytes_are_untouched(self):
        before = self.seed_written_index()
        self.write_month(MONTH, self.DEFECTIVE)
        for args in ((), ("--dry-run",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1, self.out(result))
                errors = self.payload(result)["validation_errors"]
                self.assertEqual(len(errors), 1, errors)
                self.assertEqual(errors[0]["file"], REL_MONTH)
                self.assertIn(f"line {self.OPENER_LINE} opens a fence never closed",
                              errors[0]["error"])
                self.assertIn("opens a fence never closed", self.err(result))
                self.assertEqual(self.index.read_bytes(), before)

    def test_positive_control_closing_the_fence_lets_the_same_file_through(self):
        """The refusal is the missing closer, not the fence or the fixture."""
        self.seed_written_index()
        self.write_month(MONTH, self.DEFECTIVE + lines("```"))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result), {"written": REL_INDEX})
        # The heading under the (now balanced) fence stays quoted content.
        self.assertIn("| 2026-09 | 2026-09-08 | 2026-09-08 | 1 | review |\n",
                      self.index.read_text(encoding="utf-8"))


class FilenameGrammarTests(JournalCliTestCase):
    """Guard 6, and the two spellings that would have let a forged name in."""

    def assert_refused(self, name: str, fragment: str) -> None:
        for args in ((), ("--dry-run",)):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 1, f"{args}: {self.out(result)}")
            errors = self.payload(result)["validation_errors"]
            self.assertEqual(len(errors), 1, errors)
            self.assertIn(fragment, errors[0]["error"])
        self.assertFalse(self.index.exists(), "a refusal wrote an index")

    def test_a_well_named_month_file_is_accepted(self):
        """The positive control every refusal below is measured against."""
        self.write_month(MONTH, CLEAN_MONTH)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertTrue(self.index.is_file())

    def test_an_arabic_indic_month_name_is_refused(self):
        forged = "٢٠٢٦-09"  # ARABIC-INDIC for 2026
        # Both legs of the rationale, asserted rather than asserted-about: a
        # `\d`-spelled grammar admits the name, and the name sorts ABOVE every
        # ASCII month, so the newest-first walk would have put it on the top row.
        loose = re.compile(r"^\d{4}-(0[1-9]|1[0-2])\.md\Z")
        self.assertIsNotNone(loose.fullmatch(f"{forged}.md"))
        self.assertEqual(sorted([MONTH, forged], reverse=True)[0], forged)

        (self.journal / f"{forged}.md").write_text(CLEAN_MONTH, encoding="utf-8")
        self.assert_refused(f"{forged}.md", "outside the journal grammar")

    def test_a_month_name_with_a_trailing_newline_is_refused(self):
        name = f"{MONTH}.md\n"
        # `$` under `match` admits a trailing newline; `\Z` under `fullmatch`
        # does not. The shipped grammar is spelled the second way.
        self.assertIsNotNone(re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])\.md$").match(name))
        self.assertIsNone(re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])\.md\Z").fullmatch(name))

        (self.journal / name).write_text(CLEAN_MONTH, encoding="utf-8")
        self.assert_refused(name, "outside the journal grammar")

    def test_a_one_digit_month_is_refused_rather_than_silently_skipped(self):
        """`2026-9.md` is a typo a human made; it earns a finding, not silence."""
        (self.journal / "2026-9.md").write_text(CLEAN_MONTH, encoding="utf-8")
        self.assert_refused("2026-9.md", "outside the journal grammar")

    def test_the_index_itself_is_skipped_rather_than_refused(self):
        self.write_month(MONTH, CLEAN_MONTH)
        self.index.write_text("stale hand-written index\n", encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result), {"written": REL_INDEX})
        # PAIRED POSITIVE CONTROL: the SAME content under a non-index name is
        # refused, so `index.md` is skipped by name and not by luck.
        (self.journal / "notes.md").write_text("stale hand-written index\n",
                                               encoding="utf-8")
        control = self.run_cli("--dry-run")
        self.assertEqual(control.returncode, 1, self.out(control))
        self.assertIn("outside the journal grammar",
                      self.payload(control)["validation_errors"][0]["error"])

    def test_a_plain_dotfile_is_skipped_rather_than_refused(self):
        self.write_month(MONTH, CLEAN_MONTH)
        (self.journal / ".DS_Store").write_bytes(b"\x00binary junk")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result), {"written": REL_INDEX})

    def test_a_subdirectory_is_refused(self):
        (self.journal / "2026-08").mkdir()
        self.assert_refused("2026-08", "is not a regular file")

    def test_a_non_regular_file_is_refused(self):
        fifo = self.journal / "2026-08.md"
        try:
            os.mkfifo(fifo)
        except (AttributeError, OSError) as exc:  # pragma: no cover - platform
            self.skipTest(f"os.mkfifo unavailable here: {exc}")
        self.assert_refused("2026-08.md", "is not a regular file")

    def test_positive_control_a_regular_file_at_that_name_is_read(self):
        """The refusal above is the file TYPE, not the name `2026-08.md`."""
        self.write_month("2026-08", lines("# Journal " + DASH + " 2026-08", "",
                                          heading("2026-08-02", "misc", "s")))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertIn("| 2026-08 | 2026-08-02 | 2026-08-02 | 1 | misc |\n",
                      self.index.read_text(encoding="utf-8"))


class SymlinkTests(JournalCliTestCase):
    """[SECURITY:S5] Guard 4 runs before `is_file()` AND before the dotfile skip."""

    def test_a_symlinked_month_file_is_refused_rather_than_read(self):
        victim = self.outside_victim()
        (self.journal / "2026-08.md").symlink_to(victim)
        for args in ((), ("--dry-run",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1, self.out(result))
                errors = self.payload(result)["validation_errors"]
                self.assertIn("is a symlink", errors[0]["error"])
                self.assertNotIn(WITNESS.strip(), self.out(result))
        self.assertEqual(victim.read_text(encoding="utf-8"), WITNESS)
        self.assertFalse(self.index.exists())

        # PAIRED POSITIVE CONTROL: a REAL file at the same name is read and
        # described, so the refusal is the link and not the name.
        (self.journal / "2026-08.md").unlink()
        self.write_month("2026-08", lines(heading("2026-08-02", "misc", "s")))
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertIn("| 2026-08 | 2026-08-02 | 2026-08-02 | 1 | misc |\n",
                      self.index.read_text(encoding="utf-8"))

    def test_a_symlinked_dotfile_is_refused_rather_than_skipped(self):
        """The dotfile skip must not be a way past the symlink guard."""
        victim = self.outside_victim()
        (self.journal / ".hidden").symlink_to(victim)
        result = self.run_cli("--dry-run")
        self.assertEqual(result.returncode, 1, self.out(result))
        errors = self.payload(result)["validation_errors"]
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]["file"], REL_DOTFILE)
        self.assertIn("is a symlink", errors[0]["error"])
        self.assertNotIn(WITNESS.strip(), self.out(result))
        self.assertEqual(victim.read_text(encoding="utf-8"), WITNESS)

        # PAIRED POSITIVE CONTROL: a REAL dotfile of the same name IS skipped,
        # so the refusal above is the link and not the leading dot.
        (self.journal / ".hidden").unlink()
        (self.journal / ".hidden").write_text("editor debris\n", encoding="utf-8")
        self.write_month(MONTH, CLEAN_MONTH)
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control), {"written": REL_INDEX})

    def test_a_journal_directory_symlinked_outside_the_tree_fails_containment(self):
        """Guard 3 runs BEFORE the surface-absent test, and lands in exit 2.

        The verdict that matters is that the run FAILS CONTAINMENT rather than
        reporting `surface_absent` — a symlinked `journal` must never read as
        "this tree has no journal, nothing can have drifted". The lane is the
        environment lane (exit 2, empty stdout, message on stderr): the script
        documents a containment refusal on a directory a checkout controls as
        an environment failure, and reserves the exit-1 document lane for a
        hand-edited journal file.

        THIS EXIT CODE WAS CONTESTED AND SETTLED AT 2. The competing reading
        was that guards 4 and 7 refuse a symlink at exit 1, so guard 3 should
        too. The dividing line is layout versus contents, not the mechanism:
        guards 1-3 resolve WHICH DIRECTORY to read and no document edit
        repairs them, while guards 4-7 inspect FILES a checkout's author put
        inside an already-resolved surface. Exit 1 also promises a
        `{"file", "error"}` pair naming a document, and this refusal's subject
        is a directory with no such file to name. The full argument is the
        "WHY GUARD 3 EXITS 2" paragraph in the driver's module docstring; do
        not re-derive it here.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        elsewhere = Path(tmp.name) / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / f"{MONTH}.md").write_text(CLEAN_MONTH, encoding="utf-8")
        self.journal.rmdir()
        self.journal.symlink_to(elsewhere, target_is_directory=True)

        for args in ((), ("--dry-run",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2, self.out(result))
                self.assertEqual(self.out(result), "")
                self.assertNotIn("surface_absent", self.out(result))
                self.assertIn("not contained under", self.err(result))
                self.assertNotIn("Traceback", self.err(result))
        self.assertFalse((elsewhere / "index.md").exists())

        # PAIRED POSITIVE CONTROL: the identical corpus as a REAL directory
        # inside the tree is walked and described, so the refusal is
        # containment and not the corpus.
        self.journal.unlink()
        self.journal.mkdir()
        self.write_month(MONTH, CLEAN_MONTH)
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertTrue(self.index.is_file())


class OutputIndexGuardTests(JournalCliTestCase):
    """Guard 7: the OUTPUT path, validated before it is read, in BOTH modes.

    The sibling regenerator `generate-reviews-index.py` has no counterpart to
    this guard — it reads its own index with a bare `is_file()` test and no
    prior symlink or containment check — so these two lanes are written from
    scratch rather than adapted from that suite.

    Note on which guard fires: for a SYMLINKED `index.md` the directory walk's
    guard 4 refuses the entry before guard 7 is reached, because the walk
    checks `is_symlink()` on every entry ahead of the `index.md` skip. Guard 7
    is the backstop for the window between the walk and the read. The asserted
    behaviour — exit 1, a `validation_errors` payload, nothing written, in both
    modes, and no target content on stdout — is the same either way, and is
    what a caller depends on.
    """

    def assert_refused_both_modes(self, victim: Path | None = None) -> None:
        for args in ((), ("--dry-run",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1, self.out(result))
                payload = self.payload(result)
                errors = payload["validation_errors"]
                self.assertTrue(errors, payload)
                self.assertEqual(errors[0]["file"], REL_INDEX)
                self.assertNotIn("written", payload)
                # The symlink target's content never reaches stdout.
                self.assertNotIn(WITNESS.strip(), self.out(result))
                self.assertNotIn(WITNESS.strip(), self.err(result))
        if victim is not None:
            self.assertEqual(victim.read_text(encoding="utf-8"), WITNESS)

    def test_an_index_symlinked_outside_the_tree_is_refused_in_both_modes(self):
        self.write_month(MONTH, CLEAN_MONTH)
        victim = self.outside_victim()
        self.index.symlink_to(victim)
        self.assert_refused_both_modes(victim)

        # PAIRED POSITIVE CONTROL: the identical write to a REAL index.md
        # succeeds, so the refusal is the symlink and not a broken writer.
        self.index.unlink()
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control), {"written": REL_INDEX})
        self.assertTrue(self.index.is_file())

    def test_an_index_that_is_a_directory_is_refused_in_both_modes(self):
        self.write_month(MONTH, CLEAN_MONTH)
        self.index.mkdir()
        (self.index / "inside").write_text(WITNESS, encoding="utf-8")
        self.assert_refused_both_modes()
        self.assertTrue(self.index.is_dir(), "the directory was replaced")
        self.assertEqual((self.index / "inside").read_text(encoding="utf-8"), WITNESS)

        # PAIRED POSITIVE CONTROL: remove the directory and the same run writes.
        (self.index / "inside").unlink()
        self.index.rmdir()
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control), {"written": REL_INDEX})

    def test_an_index_that_is_a_fifo_is_refused_in_both_modes(self):
        self.write_month(MONTH, CLEAN_MONTH)
        try:
            os.mkfifo(self.index)
        except (AttributeError, OSError) as exc:  # pragma: no cover - platform
            self.skipTest(f"os.mkfifo unavailable here: {exc}")
        self.assert_refused_both_modes()


class CheckModeTests(JournalCliTestCase):
    """`--check-stdin --month YYYY-MM`: admissibility only, and never a write."""

    def check(self, text: bytes, month: str = MONTH,
              *args: str) -> subprocess.CompletedProcess:
        return self.run_cli("--check-stdin", "--month", month, *args, stdin=text)

    def test_admissible_content_reports_check_ok_with_its_entry_count(self):
        self.write_month(MONTH, CLEAN_MONTH)
        result = self.check(CLEAN_MONTH.encode("utf-8"))
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result),
                         {"check": "ok", "entries": 2, "month": MONTH})

    def test_a_defective_body_is_a_validation_error_at_exit_one(self):
        defective = lines(heading("2026-09-08", "review", "above"), "", "```text",
                          heading("2026-09-01", "bug", "swallowed"))
        result = self.check(defective.encode("utf-8"))
        self.assertEqual(result.returncode, 1, self.out(result))
        errors = self.payload(result)["validation_errors"]
        self.assertEqual(errors[0]["file"], REL_MONTH)
        self.assertIn("line 3 opens a fence never closed", errors[0]["error"])
        # PAIRED POSITIVE CONTROL: the same bytes plus a closer are admissible,
        # so the refusal is the open fence and not the stdin channel.
        control = self.check((defective + lines("```")).encode("utf-8"))
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control)["check"], "ok")

    def test_a_bad_month_is_a_validation_error_at_exit_one_not_two(self):
        result = self.check(CLEAN_MONTH.encode("utf-8"), month="2026-13")
        self.assertEqual(result.returncode, 1, self.out(result))
        errors = self.payload(result)["validation_errors"]
        self.assertEqual(errors[0]["file"], "--month")
        self.assertIn("is not a valid YYYY-MM month", errors[0]["error"])
        # The same grammar the shipped `MONTH_RE` enforces, checked here so the
        # expectation is not merely "the script said no".
        self.assertIsNone(MONTH_RE.fullmatch("2026-13"))
        self.assertIsNotNone(MONTH_RE.fullmatch(MONTH))

    def test_check_stdin_with_dry_run_is_an_argument_error_at_exit_two(self):
        result = self.run_cli("--check-stdin", "--month", MONTH, "--dry-run",
                              stdin=b"")
        self.assertEqual(result.returncode, 2, self.out(result))
        self.assertEqual(self.out(result), "")
        self.assertIn("not allowed with argument", self.err(result))

    def test_empty_stdin_is_admissible_at_zero_entries(self):
        result = self.check(b"")
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result),
                         {"check": "ok", "entries": 0, "month": MONTH})

    def test_a_stdin_decode_failure_is_a_validation_error_at_exit_one(self):
        result = self.check(b"\xff\xfe not utf-8")
        self.assertEqual(result.returncode, 1, self.out(result))
        errors = self.payload(result)["validation_errors"]
        self.assertEqual(errors[0]["file"], REL_MONTH)
        self.assertIn("stdin is not valid UTF-8", errors[0]["error"])
        # PAIRED POSITIVE CONTROL: decodable bytes on the same channel pass.
        control = self.check(" not utf-8".encode("utf-8"))
        self.assertEqual(control.returncode, 0, self.err(control))

    def test_check_mode_writes_nothing_under_any_input(self):
        """Four inputs, no index — with a control proving the tree is writable.

        "Nothing was written" is satisfied by a script that never ran, so the
        control at the end performs the SAME run without `--check-stdin` and
        asserts the index appears.
        """
        self.write_month(MONTH, CLEAN_MONTH)
        inputs = [
            (b"", "empty"),
            (CLEAN_MONTH.encode("utf-8"), "admissible"),
            (b"```text\n" + heading("2026-09-01").encode("utf-8"), "open fence"),
            (b"\xff\xfe", "undecodable"),
        ]
        for raw, label in inputs:
            with self.subTest(input=label):
                self.check(raw)
                self.assertFalse(self.index.exists(),
                                 f"check mode wrote an index for the {label} input")
        # PAIRED POSITIVE CONTROL: the same tree, the same month file, without
        # `--check-stdin` — the index lands, so the absence above is the mode.
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertTrue(self.index.is_file())

    def test_check_mode_does_not_repair_a_stale_index_and_reports_no_drift(self):
        """A stale row before a write is the normal state, not a finding."""
        before = self.seed_written_index()
        prospective = CLEAN_MONTH + lines("", heading("2026-09-09", "learning", "l"),
                                          "", "One body line.")
        result = self.check(prospective.encode("utf-8"))
        self.assertEqual(result.returncode, 0, self.err(result))
        payload = self.payload(result)
        self.assertEqual(payload, {"check": "ok", "entries": 3, "month": MONTH})
        self.assertNotIn("drift", payload)
        self.assertEqual(self.index.read_bytes(), before)

    def test_check_mode_works_on_a_tree_with_no_journal_directory_yet(self):
        """The first entry a brand-new tree ever journals still preflights."""
        self.journal.rmdir()
        result = self.check(CLEAN_MONTH.encode("utf-8"))
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result),
                         {"check": "ok", "entries": 2, "month": MONTH})


class LeftoverTemporaryFileTests(JournalCliTestCase):
    """The regenerator's own temporary file must not trip its own guard 6.

    `_atomic_write_text` creates its temporary file INSIDE the directory the
    walk scans under a closed filename grammar. A run killed between the
    `os.open` and the `os.replace` leaves that file behind. Under the undotted
    name `index.md.tmp` guard 6 then refused the whole run in all three modes
    — including `--dry-run` and the `--check-stdin` preflight `log-work`
    HALTs on — so one interrupted run stopped journaling tree-wide until a
    human deleted a file nothing told them to look for. The dotted name
    `.index.md.tmp` lands under the walk's existing dotfile skip instead.
    """

    def tmp_path(self) -> Path:
        return self.journal / ".index.md.tmp"

    def test_a_successful_run_leaves_no_temporary_file_behind(self):
        """The baseline the two lanes below deviate from."""
        self.write_month(MONTH, CLEAN_MONTH)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(sorted(q.name for q in self.journal.iterdir()),
                         sorted(["index.md", f"{MONTH}.md"]),
                         "a temporary file outlived a successful run")

    def test_leftover_debris_does_not_refuse_the_dry_run_or_the_preflight(self):
        self.write_month(MONTH, CLEAN_MONTH)
        self.seed_written_index()
        self.tmp_path().write_text("half-written debris\n", encoding="utf-8")

        dry = self.run_cli("--dry-run")
        self.assertEqual(dry.returncode, 0, self.out(dry) + self.err(dry))
        self.assertNotIn("validation_errors", self.out(dry))

        pre = self.run_cli("--check-stdin", "--month", MONTH,
                           stdin=CLEAN_MONTH.encode("utf-8"))
        self.assertEqual(pre.returncode, 0, self.out(pre) + self.err(pre))
        self.assertEqual(self.payload(pre)["check"], "ok")

        # PAIRED POSITIVE CONTROL: the SAME debris under the pre-fix undotted
        # name is still refused by guard 6, so the pass above is the dotfile
        # skip and not a guard that stopped working.
        self.tmp_path().unlink()
        (self.journal / "index.md.tmp").write_text("debris\n", encoding="utf-8")
        control = self.run_cli("--dry-run")
        self.assertEqual(control.returncode, 1, self.out(control))
        errors = self.payload(control)["validation_errors"]
        self.assertEqual(errors[0]["file"], "bionic/journal/index.md.tmp")
        self.assertIn("outside the journal grammar", errors[0]["error"])

    def test_the_file_exists_recovery_message_is_reachable_in_write_mode(self):
        """Guard 6 used to refuse first, so this message could never print."""
        self.write_month(MONTH, CLEAN_MONTH)
        self.tmp_path().write_text("half-written debris\n", encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, self.out(result) + self.err(result))
        self.assertIn("already exists", self.err(result))
        self.assertIn(".index.md.tmp", self.err(result))
        self.assertNotIn("outside the journal grammar", self.err(result))
        self.assertNotIn("Traceback", self.err(result))
        self.assertFalse(self.index.exists(), "the index was written anyway")

        # PAIRED POSITIVE CONTROL: remove the debris the message named and the
        # identical run writes, so the refusal is the leftover file.
        self.tmp_path().unlink()
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control), {"written": REL_INDEX})


class PreflightAdmitsOnlyWritableTrees(JournalCliTestCase):
    """Guard 7 runs in check mode, so the preflight's promise holds.

    `--check-stdin` used to return before guard 7. An `index.md` that is a
    directory or a FIFO is skipped by the walk (`name == "index.md"` is the
    output), so check mode never saw it: the preflight exited 0 and step 6
    then exited 1, leaving `log-work`'s entry on disk beside a stale row.
    """

    def preflight(self) -> subprocess.CompletedProcess:
        return self.run_cli("--check-stdin", "--month", MONTH,
                            stdin=CLEAN_MONTH.encode("utf-8"))

    def assert_preflight_agrees_with_write_mode(self) -> None:
        pre = self.preflight()
        write = self.run_cli()
        self.assertEqual(pre.returncode, 1, self.out(pre))
        self.assertEqual(write.returncode, 1, self.out(write))
        for label, result in (("preflight", pre), ("write", write)):
            with self.subTest(mode=label):
                errors = self.payload(result)["validation_errors"]
                self.assertEqual(errors[0]["file"], REL_INDEX)
                self.assertIn("is not a regular file", errors[0]["error"])
                self.assertNotIn("check", self.payload(result))

    def test_an_index_that_is_a_directory_is_refused_by_the_preflight(self):
        self.write_month(MONTH, CLEAN_MONTH)
        self.index.mkdir()
        self.assert_preflight_agrees_with_write_mode()

        # PAIRED POSITIVE CONTROL: remove the directory and the identical
        # preflight is admissible, so the refusal is the index and not stdin.
        self.index.rmdir()
        control = self.preflight()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control)["check"], "ok")

    def test_an_index_that_is_a_fifo_is_refused_by_the_preflight(self):
        self.write_month(MONTH, CLEAN_MONTH)
        try:
            os.mkfifo(self.index)
        except (AttributeError, OSError) as exc:  # pragma: no cover - platform
            self.skipTest(f"os.mkfifo unavailable here: {exc}")
        self.assert_preflight_agrees_with_write_mode()

    def test_an_index_symlinked_outside_the_tree_is_refused_by_the_preflight(self):
        self.write_month(MONTH, CLEAN_MONTH)
        victim = self.outside_victim()
        self.index.symlink_to(victim)
        result = self.preflight()
        self.assertEqual(result.returncode, 1, self.out(result))
        self.assertNotIn(WITNESS.strip(), self.out(result))
        self.assertNotIn(WITNESS.strip(), self.err(result))
        self.assertEqual(victim.read_text(encoding="utf-8"), WITNESS)

        # PAIRED POSITIVE CONTROL: remove the symlink and the identical
        # preflight is admissible, so the refusal is the symlink and not the
        # stdin channel or the fixture.
        self.index.unlink()
        control = self.preflight()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control)["check"], "ok")

    def test_the_refusal_names_the_tree_relative_path_not_the_checkout_path(self):
        """The `error` string agrees with the `file` key beside it."""
        self.write_month(MONTH, CLEAN_MONTH)
        self.index.mkdir()
        for args in ((), ("--dry-run",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                errors = self.payload(result)["validation_errors"]
                self.assertEqual(errors[0]["error"],
                                 f"{REL_INDEX} exists and is not a regular file")
                self.assertNotIn(str(self.root), self.out(result))
                self.assertNotIn(str(self.root), self.err(result))

class StaleRowRepairTests(JournalCliTestCase):
    """A stale row is REPLACED by the derived row, never adjusted from itself.

    `rule:journal-index-row-is-derived-on-every-write`. A regenerator that
    incremented would turn a hand-edited `99` into `100`; one that derives
    turns it into the month file's real count whatever the old cell said.
    """

    #: Three entries, two categories. Every expected cell below is read off
    #: this fixture by hand, never computed by the code under test.
    THREE_ENTRY_MONTH = lines(
        "# Journal " + DASH + " " + MONTH, "",
        heading("2026-09-01", "bug", "a"), "", "One body line.", "",
        heading("2026-09-02", "bug", "b"), "", "One body line.", "",
        heading("2026-09-03", "review", "c"), "", "One body line.", "",
    )

    def test_a_numerically_wrong_count_is_repaired_to_the_derived_value(self):
        self.write_month(MONTH, self.THREE_ENTRY_MONTH)
        self.index.write_text(
            HEADER.format(last_updated="2026-09-03")
            + "| 2026-09 | 2026-09-01 | 2026-09-03 | 99 | bug, review |\n",
            encoding="utf-8")

        drift = self.run_cli("--dry-run")
        self.assertEqual(drift.returncode, 1, self.out(drift))
        self.assertIs(self.payload(drift)["drift"], True)

        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(
            self.index.read_text(encoding="utf-8"),
            HEADER.format(last_updated="2026-09-03")
            + "| 2026-09 | 2026-09-01 | 2026-09-03 | 3 | bug, review |\n")
        body = self.index.read_text(encoding="utf-8")
        self.assertNotIn("| 99 |", body)   # the stale cell is gone
        self.assertNotIn("| 100 |", body)  # and it was not incremented

    def test_two_entries_on_one_date_collapse_both_date_cells_onto_it(self):
        self.write_month(MONTH, lines(
            "# Journal " + DASH + " " + MONTH, "",
            heading("2026-09-04", "bug", "a", time="09:00"), "",
            "One body line.", "",
            heading("2026-09-04", "learning", "b", time="17:30"), "",
            "One body line.", ""))
        self.assertEqual(self.run_cli().returncode, 0)

        body = self.index.read_text(encoding="utf-8")
        self.assertIn("_Last updated: 2026-09-04_\n", body)
        self.assertIn(
            "| 2026-09 | 2026-09-04 | 2026-09-04 | 2 | bug, learning |\n", body)
        # The cells, pinned individually as well as in the rendered line.
        row = parse_index_row(body, MONTH)
        self.assertEqual(row["first"], "2026-09-04")
        self.assertEqual(row["last"], "2026-09-04")
        self.assertEqual(row["entries"], 2)

    def test_a_second_month_drifts_the_index_then_lands_newest_first(self):
        self.seed_written_index()  # one row: 2026-09, last entry 2026-09-08
        self.write_month("2026-10", lines(
            "# Journal " + DASH + " 2026-10", "",
            heading("2026-10-02", "refactor", "later"), "", "One body line.", ""))

        drift = self.run_cli("--dry-run")
        self.assertEqual(drift.returncode, 1, self.out(drift))
        self.assertIs(self.payload(drift)["drift"], True)
        self.assertEqual(self.payload(drift)["path"], REL_INDEX)

        self.assertEqual(self.run_cli().returncode, 0)
        # Byte-equality pins the row ORDER and the moved "as of" line together.
        self.assertEqual(
            self.index.read_text(encoding="utf-8"),
            HEADER.format(last_updated="2026-10-02")
            + "| 2026-10 | 2026-10-02 | 2026-10-02 | 1 | refactor |\n"
            + "| 2026-09 | 2026-09-01 | 2026-09-08 | 2 | bug, review |\n")


class SymlinkedJournalDirectoryTests(JournalCliTestCase):
    """An in-tree `journal` symlink is REFUSED outright, never followed.

    Round 1 let guard 3 CONTAIN the journal directory rather than refuse every
    symlink, so an in-tree `journal -> real` link resolved under the tree and
    was admitted. Two defects fell out: the resolved directory's basename
    reached the `path`/`written` keys unredacted while the sibling
    `validation_errors.file` key redacted the identical prefix (guard 9), and
    a `journal -> <sibling concern>` link could clobber that sibling's
    `index.md` on write, because containment alone allows an in-tree target.
    This class pins the fix instead: any symlinked `journal`, in-tree or not,
    is refused, at exit 2, with no resolved-path reporting surface left to
    sanitise. The out-of-tree case keeps its own test above
    (`test_a_journal_directory_symlinked_outside_the_tree_fails_containment`);
    this class does not restate it.
    """

    def relink(self) -> Path:
        """Replace `<tree>/journal` with a relative symlink to `<tree>/real`."""
        real = self.docs / "real"
        real.mkdir()
        self.journal.rmdir()
        self.journal.symlink_to(real.name)
        return real

    def test_an_in_tree_symlinked_journal_is_refused_in_all_three_modes(self):
        real = self.relink()
        (real / f"{MONTH}.md").write_text(CLEAN_MONTH, encoding="utf-8")

        for args in ((), ("--dry-run",), ("--check-stdin", "--month", MONTH)):
            with self.subTest(args=args):
                stdin = CLEAN_MONTH.encode("utf-8") if "--check-stdin" in args else None
                result = self.run_cli(*args, stdin=stdin)
                self.assertEqual(result.returncode, 2, self.out(result))
                self.assertEqual(self.out(result), "")
                self.assertIn("journal", self.err(result))
                self.assertIn("symlink", self.err(result))
        self.assertFalse((real / "index.md").exists())
        self.assertFalse((self.docs / "journal" / "index.md").exists())

        # PAIRED POSITIVE CONTROL: the identical corpus as a REAL directory
        # succeeds, so the refusal above is the LINK and not the corpus.
        self.journal.unlink()
        self.journal.mkdir()
        self.write_month(MONTH, CLEAN_MONTH)
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertTrue(self.index.is_file())

    def test_a_hostile_basename_target_never_reaches_stdout(self):
        """The refused symlink's target name must not leak onto stdout.

        Built from explicit escapes rather than pasted raw control bytes: a
        pipe (table-row delimiter in `check-drift`'s rendering) and an ANSI
        escape.
        """
        hostile_name = "ev" + "\x1b[31m" + "il|row"
        hostile = self.docs / hostile_name
        hostile.mkdir()
        (hostile / f"{MONTH}.md").write_text(CLEAN_MONTH, encoding="utf-8")
        self.journal.rmdir()
        self.journal.symlink_to(hostile.name)

        result = self.run_cli()
        self.assertEqual(result.returncode, 2, self.out(result))
        raw_out = result.stdout
        self.assertEqual(raw_out, b"")
        self.assertNotIn(b"|", raw_out)
        self.assertNotIn(b"\x1b", raw_out)
        self.assertFalse((hostile / "index.md").exists())

        # PAIRED POSITIVE CONTROL: swap the link for a REAL journal directory
        # holding the same corpus (the hostile-named directory plays no part
        # once there is no link to it) and the run succeeds normally, so the
        # refusal above is the LINK and not the corpus or some reachable name.
        self.journal.unlink()
        self.journal.mkdir()
        self.write_month(MONTH, CLEAN_MONTH)
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control), {"written": REL_INDEX})

    def test_a_symlink_to_a_sibling_concern_never_clobbers_its_index(self):
        """Regression test for defect (b): `journal -> adrs` must not write there."""
        adrs = self.docs / "adrs"
        adrs.mkdir()
        witness = "WITNESS: sibling concern index\n"
        (adrs / "index.md").write_text(witness, encoding="utf-8")
        self.journal.rmdir()
        self.journal.symlink_to("adrs")

        result = self.run_cli()
        self.assertEqual(result.returncode, 2, self.out(result))
        self.assertEqual(self.out(result), "")
        self.assertEqual((adrs / "index.md").read_text(encoding="utf-8"), witness)

        # PAIRED POSITIVE CONTROL: the identical corpus as journal's OWN real
        # directory succeeds and writes journal/index.md, never adrs/index.md.
        self.journal.unlink()
        self.journal.mkdir()
        self.write_month(MONTH, CLEAN_MONTH)
        control = self.run_cli()
        self.assertEqual(control.returncode, 0, self.err(control))
        self.assertEqual(self.payload(control), {"written": REL_INDEX})
        self.assertEqual((adrs / "index.md").read_text(encoding="utf-8"), witness)

    def test_positive_control_a_plain_directory_reports_the_journal_path(self):
        # Same machinery, no symlink: the reported prefix is the literal one,
        # so the two lanes above distinguish layouts rather than always
        # printing whatever the resolver returned.
        self.write_month(MONTH, CLEAN_MONTH)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result), {"written": REL_INDEX})
        self.assertEqual(self.payload(self.run_cli("--dry-run")),
                         {"drift": False, "path": REL_INDEX})


class SurfaceAbsentVerdictProseTests(JournalCliTestCase):
    """`check-drift`'s N/A verdict, bound to the payload this script emits.

    The verdict was written when `generate-reviews-index.py` was the only gate
    emitting `surface_absent`, and it was defined by naming that script. This
    script emits the identical key, so the journal case fell through to the
    "clean" definition above it — which its payload satisfies exactly. A
    vacuous green, and the remedy on offer (`review-decisions`) was the wrong
    act for a tree whose journal surface was never created.

    Each lane pairs the prose with the live payload, so the verdict cannot
    describe a key nothing emits and the key cannot lose its documented
    reading.
    """

    def setUp(self) -> None:
        super().setUp()
        self.verdict = next(
            line for line in
            CHECK_DRIFT_SKILL.read_text(encoding="utf-8").splitlines()
            if line.startswith("- **N/A**"))

    def test_the_verdict_keys_on_the_payload_key_not_on_one_script(self):
        self.assertIn('exit 0 with `"surface_absent": true` on the JSON',
                      self.verdict)
        self.assertIn("The verdict keys on that payload key, not on which gate "
                      "printed it", self.verdict)

    def test_the_verdict_names_both_gates_that_emit_the_key(self):
        self.assertIn("generate-reviews-index.py", self.verdict)
        self.assertIn("generate-journal-index.py", self.verdict)

    def test_the_verdict_gives_a_remedy_per_surface_and_never_the_regenerator(self):
        self.assertIn("The fix is never the regenerator, and it differs by "
                      "surface", self.verdict)
        self.assertIn("run `review-decisions` when a review is due", self.verdict)
        self.assertIn("`init-docs` when the journal surface was never created",
                      self.verdict)

    def test_the_verdict_still_forbids_recording_it_clean(self):
        # PAIRED POSITIVE CONTROL for the three lanes above: the rule the
        # generalisation was meant to preserve is still in the same bullet.
        self.assertIn("Report it as N/A, never as clean", self.verdict)

    def test_the_clean_definition_excludes_the_key_rather_than_correcting_it_later(self):
        """The discriminator has to sit IN the clean bullet, four bullets up.

        `Report it as N/A, never as clean` is an override, and an override
        only reaches a reader who got that far. A reader matching the verdict
        bullets top-down hits `clean` first, and a `surface_absent` payload
        satisfies every clause that bullet used to state.
        """
        clean = next(line for line in
                     CHECK_DRIFT_SKILL.read_text(encoding="utf-8").splitlines()
                     if line.startswith("- **clean**"))
        self.assertIn("no `surface_absent` key", clean)
        # PAIRED POSITIVE CONTROL: the exclusion is an ADDITION to the clean
        # definition, not a replacement for it — the conditions a real clean
        # pass is graded on are still stated in the same bullet.
        self.assertIn('`{"drift": false}`', clean)
        self.assertIn("no `validation_errors`", clean)

    def test_this_script_really_emits_the_key_the_verdict_describes(self):
        """The binding half: the prose above is not about a dead key."""
        self.journal.rmdir()
        result = self.run_cli("--dry-run")
        self.assertEqual(result.returncode, 0, self.err(result))
        payload = self.payload(result)
        self.assertIn("surface_absent", payload, payload)
        self.assertIs(payload["surface_absent"], True)
        # The payload matches every OTHER clause of the "clean" definition
        # above it — `{"drift": false}`, no `validation_errors`. That overlap
        # is why the clean bullet has to exclude the key by name, which
        # `test_the_clean_definition_excludes_the_key_rather_than_correcting_it_later`
        # pins.
        self.assertIs(payload["drift"], False)
        self.assertNotIn("validation_errors", payload)

    def test_the_roster_table_carries_this_gate(self):
        # A verdict that named the gate but no roster row would leave the gate
        # unrun, and an N/A verdict for a gate nobody invokes is not a check.
        text = CHECK_DRIFT_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            "| `<docs_dir>/journal/index.md` | project | `generate-journal-index.py "
            "--dry-run` | `generate-journal-index.py` |", text)


class UnknownCategoryRefusalTests(JournalCliTestCase):
    """The driver refuses a month file carrying a category it cannot classify.

    The incident this closes: `release` joined the journal category enum in
    the schema and not in `CATEGORIES`, so every release entry was read as
    body prose. The month undercounted, the rollup cell omitted the category,
    and the drift gate compared the index on disk against the index the
    regenerator would write and found them equal. The gate was correct about
    the only thing it measured. These lanes give it something else to measure.
    """

    #: Line 5 (1-based) carries the near miss. Written out here rather than
    #: searched for, and cross-checked against the fixture below.
    NEAR_MISS_LINE = 5
    DEFECTIVE = lines(
        "# Journal " + DASH + " " + MONTH,            # 1
        "",                                           # 2
        heading("2026-09-08", "review", "above"),     # 3
        "",                                           # 4
        "## [2026-09-09 10:00] shipped | a release",  # 5  <- outside the enum
        "",                                           # 6
        "One body line.",                             # 7
    )

    def test_the_fixtures_near_miss_really_sits_on_the_named_line(self):
        self.assertEqual(self.DEFECTIVE.split("\n")[self.NEAR_MISS_LINE - 1],
                         "## [2026-09-09 10:00] shipped | a release")
        self.assertNotIn("shipped", CATEGORIES)

    def test_both_modes_refuse_and_the_existing_index_bytes_are_untouched(self):
        before = self.seed_written_index()
        self.write_month(MONTH, self.DEFECTIVE)
        for args in ((), ("--dry-run",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1, self.out(result))
                errors = self.payload(result)["validation_errors"]
                self.assertEqual(len(errors), 1, errors)
                self.assertEqual(errors[0]["file"], REL_MONTH)
                # The finding names the line and the token that failed.
                self.assertIn(f"line {self.NEAR_MISS_LINE}", errors[0]["error"])
                self.assertIn("shipped", errors[0]["error"])
                self.assertIn("shipped", self.err(result))
                self.assertEqual(self.index.read_bytes(), before)

    def test_positive_control_an_enum_category_in_that_slot_is_admitted(self):
        """The refusal is the token, not the heading, the line, or the fixture."""
        self.seed_written_index()
        self.write_month(MONTH, self.DEFECTIVE.replace(
            "] shipped | ", "] release | "))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result), {"written": REL_INDEX})
        # And the admitted entry reaches the row it was dropped from before.
        row = parse_index_row(self.index.read_text(encoding="utf-8"), MONTH)
        self.assertEqual(row["entries"], 2)
        self.assertIn("release", row["categories"])

    def test_a_fenced_example_is_content_and_the_run_is_clean(self):
        # FALSE-POSITIVE LANE: a month file that documents the heading grammar
        # inside a fence is admissible, and every journal month in this repo
        # is free to carry one.
        self.seed_written_index()
        self.write_month(MONTH, CLEAN_MONTH + lines(
            "```", "## [2026-09-09 10:00] shipped | an example", "```"))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, self.err(result))
        self.assertEqual(self.payload(result), {"written": REL_INDEX})

    def test_check_stdin_refuses_prospective_content_carrying_a_near_miss(self):
        # `log-work` preflights a prospective month file through this mode. A
        # preflight that admitted a heading write mode then refuses would
        # promise a write that cannot happen.
        self.write_month(MONTH, CLEAN_MONTH)
        result = self.run_cli("--check-stdin", "--month", MONTH,
                              stdin=self.DEFECTIVE.encode("utf-8"))
        self.assertEqual(result.returncode, 1, self.out(result))
        errors = self.payload(result)["validation_errors"]
        self.assertEqual(errors[0]["file"], REL_MONTH)
        self.assertIn("shipped", errors[0]["error"])

    def test_the_undercount_the_guard_prevents_is_one_entry_here(self):
        """The COST of the silent drop, stated as a number.

        The defective month and its corrected twin differ by one token. The
        corrected one derives two entries; the defective one derives one, and
        before this guard that is the index the regenerator wrote and the
        drift gate agreed with. Measured on the shipped derivation rather
        than on the CLI, so the number survives any change to how the driver
        reports it.
        """
        corrected = self.DEFECTIVE.replace("] shipped | ", "] release | ")
        self.assertEqual(derive_row(MONTH, corrected)["entries"], 2)
        self.assertEqual(derive_row(MONTH, self.DEFECTIVE)["entries"], 1)

    def test_every_other_refusal_lane_admits_this_file(self):
        """Why the drop was silent: nothing else had anything to say.

        The name matches the month grammar, the file is regular and not a
        symlink, the text decodes, and no fence is left open. Every guard the
        driver carried BEFORE this one passes, so the near-miss check is the
        only thing between this file and a silently short index. If a future
        change makes another lane catch it too, this test fails and the
        rationale above needs rewriting rather than quietly outliving itself.
        """
        self.assertIsNone(find_unclosed_fence(self.DEFECTIVE))
        self.assertTrue(MONTH_RE.fullmatch(MONTH))
        self.assertEqual(journal_entries(self.DEFECTIVE, MONTH),
                         [("2026-09-08", "review")])
        # And the heading really is well-formed apart from its category: same
        # line, in-enum token, opens an entry.
        corrected = self.DEFECTIVE.replace("] shipped | ", "] release | ")
        self.assertIn(("2026-09-09", "release"), journal_entries(corrected, MONTH))

    def test_check_stdin_reported_the_short_count_before_the_guard(self):
        """The pre-fix signature, pinned so the repair cannot regress quietly.

        With the guard removed, `--check-stdin` on this content exits 0 with
        `{"check": "ok", "entries": 1}` — a preflight vouching for a write
        that loses an entry. It now exits 1 instead. The assertion below pins
        BOTH halves: the refusal, and the count that would otherwise have
        been reported, so a future change that turns the refusal back into a
        pass cannot also quietly restore the wrong number.
        """
        self.write_month(MONTH, CLEAN_MONTH)
        result = self.run_cli("--check-stdin", "--month", MONTH,
                              stdin=self.DEFECTIVE.encode("utf-8"))
        self.assertEqual(result.returncode, 1, self.out(result))
        self.assertNotIn('"check": "ok"', self.out(result))
        self.assertEqual(derive_row(MONTH, self.DEFECTIVE)["entries"], 1)

    def test_every_near_miss_in_a_file_is_reported_not_only_the_first(self):
        self.seed_written_index()
        self.write_month(MONTH, lines(
            "## [2026-09-09 10:00] shipped | x",
            "## [2026-09-10 10:00] chore | y"))
        result = self.run_cli("--dry-run")
        self.assertEqual(result.returncode, 1, self.out(result))
        errors = self.payload(result)["validation_errors"]
        self.assertEqual(len(errors), 2, errors)
        self.assertIn("shipped", errors[0]["error"])
        self.assertIn("chore", errors[1]["error"])



if __name__ == "__main__":
    unittest.main()
