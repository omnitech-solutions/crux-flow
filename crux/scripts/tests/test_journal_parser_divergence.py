"""The divergence sentinel between this tree's two journal parsers.

TWO PARSERS READ THE SAME FILE. `journal_index.journal_entries` derives the
journal-index row; `summaries_projection._journal_entries` finds the corroborating
journal hook a signed batch needs. They were written at different times against
different needs, and they do not agree on every input. This file pins the corpus
on which they are REQUIRED to agree, and records the shapes on which they are
not, as an ENUMERATED EXCLUSION rather than an assertion.

WHAT IS ASSERTED. For every fixture below, BOTH parsers are asserted against an
independently written expected `(date, category)` literal. The agreement is a
consequence of the two assertions, never the assertion itself: "both parsers
returned the same thing" is satisfied by both returning nothing, which is the
vacuous shape this repo keeps a guard skill for. Two fixtures are deliberately
expected-empty — a prose-only file and an empty file — and are named as such;
every other fixture carries a non-empty literal, including each of the six that
exist to show a line is a heading to NEITHER parser.

THE ENUMERATED EXCLUSION — EIGHT SHAPES ACROSS SIX AXES. These are a written
record, not a test. `journal_entries` reads each of them one way and
`_journal_entries` the other, so no fixture below contains one.

  Axis 1, fence awareness (three shapes). `journal_entries` tracks fences on the
  shared `md_fences` subset and `_journal_entries` does not look at a line's
  fence context at all, so a heading opens no entry for the first and one entry
  for the second when it sits: (1) inside a balanced backtick fence; (2) inside a
  balanced tilde fence; (3) below an opener the file never closes.

  Axis 2, the month bound (one shape). (4) A heading dated outside the month the
  file names is dropped by `journal_entries`, which takes the month as an
  argument, and kept by `_journal_entries`, which takes no month.

  Axis 3, the category enum (one shape). (5) A category outside the closed
  nine-member enum is no heading at all to `journal_entries` and a heading with
  an arbitrary category token to `_journal_entries`, whose category group is
  `(\\S+)`.

  Axis 4, the subject anchor (one shape). (6) A subject not beginning with a
  non-space character fails `journal_entries`'s trailing `\\S` and is admitted by
  `_journal_entries`'s `(.*)`, which matches the empty string.

  Axis 5, calendar validation (one shape). (7) A well-formed but non-calendar
  date such as `2026-09-99` is refused by `journal_entries`'s
  `date.fromisoformat` round-trip and admitted by `_journal_entries`, which
  performs no such check.

  Axis 6, the digit class (one shape). (8) A date spelled in non-ASCII digits is
  refused by `journal_entries`'s `[0-9]` and admitted by `_journal_entries`'s
  `\\d`, which matches any Unicode decimal digit under Python's default matching.

Shapes 7 and 8 DID NOT EXIST before the change this file lands with: both
parsers previously used `\\d` and neither validated the calendar, so the two
agreed on those inputs by both being wrong. Hardening `journal_entries` widens
the gap between the two shipped parsers by two axes. That is a deliberate
trade — the derived index is the surface an attacker can spoof a top row on —
and retrofitting `_journal_entries` is a recorded follow-on, NOT this file's
scope.

WHAT THIS SENTINEL DOES NOT PROVE. It is not an equivalence proof. It says
nothing about any shape the corpus below does not enumerate, and nothing about
the eight excluded shapes beyond the fact that they are excluded. A green run
means the two parsers agree on THIS corpus and that each returns the literal
written beside it — no more. Its job is to fail loudly when a future edit to
either parser moves one of them on an input the other still reads the old way.

EVERY FIXTURE IS AN IN-MEMORY STRING. Nothing reads or writes any tree,
including the live `bionic/`, so this suite runs unchanged against the
crux-only staged artifact `sync.sh` builds. Both modules ship under
`crux/scripts/`, so both are inside the staged allowlist.

Stdlib only. Run:
  uv run python3 -m unittest discover -s crux/scripts/tests -p 'test_journal_parser_divergence.py'
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import summaries_projection  # noqa: E402
from journal_index import CATEGORIES, journal_entries  # noqa: E402

MONTH = "2026-09"

#: A `(date, category)` pair every "heading to NEITHER" fixture pairs its
#: forged line with, so its expected literal is non-empty.
ADMITTED = ("2026-09-03", "bug")
ADMITTED_LINE = "## [2026-09-03 10:00] bug | an admitted entry"


def summaries_pairs(text: str) -> list[tuple[str, str]]:
    """`summaries_projection._journal_entries`, reduced to the compared shape."""
    return [(e["date"], e["category"])
            for e in summaries_projection._journal_entries(text)]


def lines(*rows: str) -> str:
    return "".join(row + "\n" for row in rows)


# ── the required-agreement corpus ──────────────────────────────────────────
#
# `(name, text, expected)`. `expected` is written out from the fixture beside
# it and is never produced by calling either parser.

CATEGORY_DATES = {
    "decision": "2026-09-01", "implementation": "2026-09-02", "bug": "2026-09-03",
    "learning": "2026-09-04", "blocker": "2026-09-05", "refactor": "2026-09-06",
    "meeting": "2026-09-07", "review": "2026-09-08", "misc": "2026-09-09",
    "release": "2026-09-10",
}

CORPUS: list[tuple[str, str, list[tuple[str, str]]]] = [

    # ── column-0 headings, real ASCII calendar dates, every category ──
    (
        "every category at column zero",
        lines("# Journal — 2026-09", "",
              *[line for cat in CATEGORIES
                for line in (f"## [{CATEGORY_DATES[cat]} 10:00] {cat} | s",
                             "", "One body line.", "")]),
        [(CATEGORY_DATES[cat], cat) for cat in CATEGORIES],
    ),

    # ── prose carrying pipes and inline backticks ──────────────────────────
    (
        "prose with pipes and inline backticks",
        lines("# Journal — 2026-09", "",
              "## [2026-09-03 10:00] bug | a defect", "",
              "The cell reads `| a | b |` and the token is `journal_entries`.",
              "A second line | with | bare | pipes | and a `code span`.", "",
              "## [2026-09-04 09:00] learning | a lesson", "",
              "One `token` beside another `token`."),
        [("2026-09-03", "bug"), ("2026-09-04", "learning")],
    ),

    # ── balanced fences quoting no heading ─────────────────────────────────
    (
        "balanced backtick fence quoting no heading",
        lines("## [2026-09-03 10:00] bug | a defect", "",
              "```text", "quoted content, no heading here", "```", "",
              "## [2026-09-04 09:00] learning | a lesson"),
        [("2026-09-03", "bug"), ("2026-09-04", "learning")],
    ),
    (
        "balanced tilde fence quoting no heading",
        lines("## [2026-09-03 10:00] bug | a defect", "",
              "~~~text", "quoted content, no heading here", "~~~", "",
              "## [2026-09-04 09:00] learning | a lesson"),
        [("2026-09-03", "bug"), ("2026-09-04", "learning")],
    ),
    (
        "balanced fence whose longer closer wraps a shorter opener",
        lines("## [2026-09-03 10:00] bug | a defect", "",
              "```text", "quoted content", "````", "",
              "## [2026-09-04 09:00] learning | a lesson"),
        [("2026-09-03", "bug"), ("2026-09-04", "learning")],
    ),
    (
        "balanced fence whose content is itself a fence-looking line",
        lines("## [2026-09-03 10:00] bug | a defect", "",
              "````text", "```", "prose inside the inner run", "```", "````", "",
              "## [2026-09-04 09:00] learning | a lesson"),
        [("2026-09-03", "bug"), ("2026-09-04", "learning")],
    ),

    # ── the two deliberately-empty lanes, named as such ────────────────────
    (
        "prose only, no heading anywhere (deliberately empty)",
        lines("# Journal — 2026-09", "",
              "_Append-only. Newest entries at the top._", "",
              "Nothing has been journalled this month."),
        [],
    ),
    (
        "an empty file (deliberately empty)",
        "",
        [],
    ),

    # ── three near-misses BOTH parsers read as an entry ─────────────────────
    (
        "trailing whitespace after the subject",
        "## [2026-09-03 10:00] bug | x   \n",
        [ADMITTED],
    ),
    (
        "an unvalidated hour field",
        "## [2026-09-03 99:99] bug | x\n",
        [ADMITTED],
    ),
    (
        "a final heading with no trailing newline",
        "# Journal — 2026-09\n\n## [2026-09-03 10:00] bug | x",
        [ADMITTED],
    ),

    # ── four shapes that are a heading to NEITHER parser ───────────────────
    #
    # Each is PAIRED with an admitted entry, so the expected literal is
    # non-empty and a parser that simply stopped matching would fail here.
    (
        "an indent of one space",
        lines(" ## [2026-09-04 10:00] learning | forged", "", ADMITTED_LINE),
        [ADMITTED],
    ),
    (
        "an indent of two spaces",
        lines("  ## [2026-09-04 10:00] learning | forged", "", ADMITTED_LINE),
        [ADMITTED],
    ),
    (
        "an indent of three spaces",
        lines("   ## [2026-09-04 10:00] learning | forged", "", ADMITTED_LINE),
        [ADMITTED],
    ),
    (
        "a level-three heading",
        lines("### [2026-09-04 10:00] learning | forged", "", ADMITTED_LINE),
        [ADMITTED],
    ),
    (
        "a tab after the pipe",
        lines("## [2026-09-04 10:00] learning |\tforged", "", ADMITTED_LINE),
        [ADMITTED],
    ),
    (
        "a BOM before the first heading",
        "﻿## [2026-09-04 10:00] learning | forged\n\n" + ADMITTED_LINE + "\n",
        [ADMITTED],
    ),

    # ── line-terminator shapes ─────────────────────────────────────────────
    #
    # CRLF is a real document shape both parsers must read. The five bare
    # control characters end a line for `str.splitlines()` and for neither
    # parser, so a heading spelled after one is prose to both.
    (
        "CRLF throughout",
        "# Journal — 2026-09\r\n\r\n"
        "## [2026-09-03 10:00] bug | a defect\r\n\r\nOne body line.\r\n\r\n"
        "## [2026-09-04 09:00] learning | a lesson\r\n",
        [("2026-09-03", "bug"), ("2026-09-04", "learning")],
    ),
    (
        "a lone CR before a heading-looking line",
        "body text\r## [2026-09-04 10:00] learning | forged\n" + ADMITTED_LINE + "\n",
        [ADMITTED],
    ),
    (
        "U+2028 before a heading-looking line",
        "body text ## [2026-09-04 10:00] learning | forged\n" + ADMITTED_LINE + "\n",
        [ADMITTED],
    ),
    (
        "U+0085 before a heading-looking line",
        "body text## [2026-09-04 10:00] learning | forged\n" + ADMITTED_LINE + "\n",
        [ADMITTED],
    ),
    (
        "U+000B before a heading-looking line",
        "body text## [2026-09-04 10:00] learning | forged\n" + ADMITTED_LINE + "\n",
        [ADMITTED],
    ),
    (
        "U+000C before a heading-looking line",
        "body text## [2026-09-04 10:00] learning | forged\n" + ADMITTED_LINE + "\n",
        [ADMITTED],
    ),
]


class RequiredAgreementTests(unittest.TestCase):

    def test_both_parsers_return_the_written_out_literal(self):
        for name, text, expected in CORPUS:
            with self.subTest(fixture=name):
                self.assertEqual(journal_entries(text, MONTH), expected,
                                 "journal_index.journal_entries")
                self.assertEqual(summaries_pairs(text), expected,
                                 "summaries_projection._journal_entries")

    def test_the_corpus_is_not_vacuous(self):
        """All but the two named empty lanes carry a non-empty expectation.

        Guards the file against the shape where every fixture expects `[]` and
        the agreement assertion is satisfied by two parsers that match nothing.
        """
        empty = [name for name, _, expected in CORPUS if not expected]
        self.assertEqual(sorted(empty), [
            "an empty file (deliberately empty)",
            "prose only, no heading anywhere (deliberately empty)",
        ])
        self.assertGreaterEqual(len(CORPUS) - len(empty), 18)
        total_pairs = sum(len(expected) for _, _, expected in CORPUS)
        # The first term is the every-category lane, so it tracks the enum rather
        # than a frozen count -- the enum grew by one when `release` was adopted.
        self.assertEqual(total_pairs, len(CATEGORIES) + 2 * 5 + 3 + 6 + 2 + 5)

    def test_every_expected_pair_is_a_real_in_month_enum_category(self):
        """The corpus stays inside the required-agreement region by construction.

        A pair dated outside `MONTH` or carrying a category outside the closed
        enum would be one of the enumerated exclusions, on which the two parsers
        are not required to agree — so it must not appear in an expectation.
        """
        for name, _, expected in CORPUS:
            with self.subTest(fixture=name):
                for date, category in expected:
                    self.assertTrue(date.startswith(MONTH + "-"), date)
                    self.assertIn(category, CATEGORIES)


class SentinelSelfChecks(unittest.TestCase):
    """The comparison itself is discriminating, not trivially satisfied."""

    def test_the_two_parsers_are_distinct_callables(self):
        self.assertIsNot(journal_entries, summaries_projection._journal_entries)

    def test_the_reducer_would_report_a_divergence_if_one_appeared(self):
        """A shape from the exclusion list, driven to show the assertion bites.

        Not part of the required-agreement corpus and asserted only here: a
        heading inside a balanced backtick fence (exclusion shape 1). It proves
        `test_both_parsers_return_the_written_out_literal` would FAIL rather
        than pass silently if a required-agreement fixture ever diverged.
        """
        text = lines("```text", "## [2026-09-03 10:00] bug | quoted", "```")
        self.assertEqual(journal_entries(text, MONTH), [])
        self.assertEqual(summaries_pairs(text), [("2026-09-03", "bug")])
        self.assertNotEqual(journal_entries(text, MONTH), summaries_pairs(text))


if __name__ == "__main__":
    unittest.main()
