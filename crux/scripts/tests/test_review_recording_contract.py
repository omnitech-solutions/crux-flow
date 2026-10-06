"""A conformance suite over the decision-review recording contract.

SCOPE FIRST, because a green run here is easy to over-read. Of the five paths a
review pass writes, TWO now have production code in this file: the derived
reviews index, written end to end by `generate-reviews-index.py`, and the
journal index, whose every cell is derived and rendered by the shipped
`crux/scripts/journal_index.py` (this file only places the resulting bytes).
The other three — the dated report, the `adr-review` op, and the month's
journal entry — are written by this file's own fixture helpers. So the
five-paths lane asserts that files the fixture itself just wrote exist. On
those three it is a fixture self-check, not a check of the shipped skill.

`assert_journal_index_consistent` IS A ROUND TRIP, NOT AN INDEPENDENT CHECK.
It is the primary assertion in four classes below, and it compares
`parse_index_row` over the index against `derive_row` over the month file —
but `write_journal_index` built that index by calling `derive_row` and
`render_index`, so both sides of the comparison come from the same shipped
derivation. What it proves is that the derivation survives a render-and-parse
round trip: a renderer that dropped a cell, or a parser that mis-split one,
turns it red, and `ConsistencyNegativeControlTests` shows seeded drift does
too. What it does NOT prove is that any cell holds the right value. That is
pinned in `test_generate_journal_index.py`, where every expected cell is
written out by hand against a fixture the test itself built.

THE FIVE JOURNAL-INDEX HELPERS NOW SHIP AND ARE IMPORTED, NOT RE-DECLARED.
`CATEGORIES`, `ENTRY_RE`, `journal_entries`, `derive_row`, `render_index` and
`parse_index_row` were defined in this file as fixture-only helpers nothing
shipped. They live in `crux/scripts/journal_index.py` now, and the lanes below
import them from there, so this file's assertions run against production code
rather than against a local copy. Every expected value stays written out
independently: an expectation computed by calling the function under test would
compare the implementation with itself, which is exactly what the extraction
was meant to stop.

The edge cases the lanes pin are unchanged: a heading quoted inside a fence, an
absent month file, a row one entry behind its month file, and two passes on one
date.

`Ran 9 tests ... OK` is still NOT evidence that the SKILL conforms. The skill's
own contract — the `log-work` call form, the closed five-path write set, the
six-write accounting, and the two op kinds — lives in prose and is pinned
separately by `test_review_decisions_prose.py`. Read the two suites together.

Every lane builds a miniature repo root under a temporary directory and drives
`generate-reviews-index.py` there. NOTHING reads the live `bionic/` tree: the
contract is a property of the code and of the fixture, not of this repo's
content, and a test that read the live tree would false-fail when `sync.sh`
runs this suite against the crux-only staged artifact (which carries no
`bionic/`). `journal_index.py` and `generate-reviews-index.py` both ship under
`crux/scripts/`, so both are inside the staged allowlist.

The imported derivation is bounded the way the recording contract requires: the
entry count comes from a fence-aware split built on the shared subset in
`crux/scripts/md_fences.py`, the two dates are bounded by the month the file
names, and the category rollup is anchored on the journal's closed nine-member
category enum. Two shapes moved with the extraction, and the fixture below
accounts for both: `render_index` takes a `last_updated` argument where the
local copy hardcoded a date, and an empty cell renders as an em dash where the
local copy rendered an empty string.

The parser lanes here and the divergence sentinel in
`test_journal_parser_divergence.py` cover different questions — this file asks
whether a recorded pass leaves a consistent tree, that one asks whether this
tree's two journal parsers still agree where they must.

Stdlib only. Run:
  uv run python3 -m unittest discover -s crux/scripts/tests -p 'test_review_recording_contract.py'
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS / "generate-reviews-index.py"
JOURNAL_INDEX_SCRIPT = SCRIPTS / "generate-journal-index.py"

sys.path.insert(0, str(SCRIPTS))

import review_findings  # noqa: E402
from journal_index import (  # noqa: E402
    derive_row,
    journal_entries,
    parse_index_row,
    render_index,
)

#: The em-dash placeholder the shipped `derive_row` puts in an empty cell.
DASH = "—"

ADR_REVIEW_OP_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] adr-review \| ", re.M)
JOURNAL_OP_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] journal \| ", re.M)
#: The FROZEN LEGACY count's pattern, taken from the grammar module rather
#: than copied: the fixture report this file writes carries no
#: `report_grammar` key, so what this counts is the legacy token set and never
#: the live finding-definition count.
FINDING_RE = review_findings.LEGACY_TOKEN


def render_rows(rows: list[dict]) -> str:
    """`render_index` with the file-level "as of" line the driver computes.

    `last_updated` is the maximum over the rows carrying a real date, and the
    em dash when none does — the same expression
    `generate-journal-index.py` uses, spelled here because this fixture writes
    the index itself rather than shelling out to that regenerator.
    """
    dated = [r["last"] for r in rows if r["last"] != DASH]
    return render_index(rows, max(dated) if dated else DASH)


class RecordingTreeTestCase(unittest.TestCase):
    """A disposable repo root carrying the five paths a review pass writes."""

    MONTH = "2026-09"
    DATE = "2026-09-08"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        self.docs = self.root / "bionic"
        self.reviews = self.docs / "adrs" / "reviews"
        self.reviews.mkdir(parents=True)
        self.journal = self.docs / "journal"
        self.journal.mkdir(parents=True)
        self.log = self.docs / "log.md"
        self.log.write_text("# Operations log\n\n_Append-only. Newest first._\n",
                            encoding="utf-8")

    # ── the five paths ──────────────────────────────────────────────────────

    def write_report(self, date: str, findings: list[str]) -> None:
        body = "\n".join(
            f"### `{f}` — one line\n- **subject:** the decision it cites\n"
            for f in findings)
        (self.reviews / f"{date}.md").write_text(
            f"---\ntype: adr-review\ndate: {date}\ndismissed: []\n---\n\n"
            f"# Decision review — {date}\n\n{body}",
            encoding="utf-8")

    def append_op(self, date: str, op: str, subject: str) -> None:
        text = self.log.read_text(encoding="utf-8")
        self.log.write_text(
            text + f"\n## [{date}] {op} | {subject}\n\nOne body line.\n",
            encoding="utf-8")

    def append_journal_entry(self, month: str, date: str, time: str,
                             category: str, subject: str) -> None:
        path = self.journal / f"{month}.md"
        text = (path.read_text(encoding="utf-8") if path.exists()
                else f"# Journal — {month}\n\n_Append-only. Newest entries at the top._\n")
        path.write_text(
            text + f"\n## [{date} {time}] {category} | {subject}\n\nOne line.\n",
            encoding="utf-8")

    def write_journal_index(self, months: list[str]) -> None:
        rows = [derive_row(m, (self.journal / f"{m}.md").read_text(encoding="utf-8"))
                for m in months]
        (self.journal / "index.md").write_text(render_rows(rows), encoding="utf-8")

    def record_a_pass(self, date: str, findings: list[str], time: str = "11:20") -> None:
        """One completed `review-decisions` invocation: six writes, five paths."""
        month = date[:7]
        self.write_report(date, findings)                              # path 1
        self.regenerate()                                              # path 2
        self.append_op(date, "adr-review", f"decision review {date}")  # path 3
        self.append_journal_entry(month, date, time, "review",
                                  f"decision review {date}")           # path 4
        self.append_op(date, "journal", f"decision review {date}")     # path 3 again
        self.write_journal_index([month])                              # path 5

    # ── drivers ─────────────────────────────────────────────────────────────

    def run_index_cli(self, *args: str) -> subprocess.CompletedProcess:
        # `timeout` so a regenerator that hangs fails this suite instead of
        # hanging it. 60s is far above the observed runtime (well under 1s per
        # call) and is a liveness bound, not a performance assertion.
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.root), *args],
            capture_output=True, text=True, timeout=60)

    def regenerate(self) -> None:
        result = self.run_index_cli()
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_index_clean(self) -> None:
        result = self.run_index_cli("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        # Assert the payload's own verdict beside the exit code. That the
        # regenerator really reports `"drift": true` at exit 1 on a stale index
        # is NOT shown here — no lane in this file seeds one, so only the clean
        # leg is exercised. The stale leg is pinned by the sibling suite:
        # `test_generate_reviews_index.py`,
        # `test_a_stale_index_is_drift_in_dry_run_and_rebuilt_in_write_mode`.
        self.assertIs(payload["drift"], False, result.stdout)
        self.assertEqual(payload["orphan_rows"], [], result.stdout)

    def assert_journal_index_consistent(self, month: str) -> None:
        """The contract: the index row agrees with the month file it names."""
        month_text = (self.journal / f"{month}.md").read_text(encoding="utf-8")
        derived = derive_row(month, month_text)
        row = parse_index_row(
            (self.journal / "index.md").read_text(encoding="utf-8"), month)
        self.assertIsNotNone(row, f"no journal index row for {month}")
        self.assertEqual(row, derived)


class OneCompletedPassTests(RecordingTreeTestCase):

    def test_a_completed_pass_leaves_five_paths_six_writes_and_a_clean_index(self):
        self.record_a_pass(self.DATE,
                           ["adr-review-stale-gate", "adr-review-unused-rule"])

        for path in (self.reviews / f"{self.DATE}.md",
                     self.reviews / "index.md",
                     self.log,
                     self.journal / f"{self.MONTH}.md",
                     self.journal / "index.md"):
            self.assertTrue(path.is_file(), f"{path} not written")

        # Six writes over five paths: two of them land in the one log, as the
        # two operation kinds.
        log_text = self.log.read_text(encoding="utf-8")
        self.assertEqual(len(ADR_REVIEW_OP_RE.findall(log_text)), 1)
        self.assertEqual(len(JOURNAL_OP_RE.findall(log_text)), 1)

        entries = journal_entries(
            (self.journal / f"{self.MONTH}.md").read_text(encoding="utf-8"),
            self.MONTH)
        self.assertEqual([c for _, c in entries], ["review"])

        self.assert_index_clean()
        self.assert_journal_index_consistent(self.MONTH)

    def test_the_row_follows_the_entries_not_the_row_it_replaces(self):
        # Every cell is derived on every write: entries of other categories move
        # the count, the first-entry date and the rollup.
        self.append_journal_entry(self.MONTH, "2026-09-01", "09:00", "decision", "a")
        self.append_journal_entry(self.MONTH, "2026-09-02", "09:00", "decision", "b")
        self.append_journal_entry(self.MONTH, "2026-09-03", "09:00", "learning", "c")
        self.record_a_pass(self.DATE, ["adr-review-one"])

        row = parse_index_row(
            (self.journal / "index.md").read_text(encoding="utf-8"), self.MONTH)
        self.assertEqual(row["entries"], 4)
        self.assertEqual(row["first"], "2026-09-01")
        self.assertEqual(row["last"], self.DATE)
        # decision 2; learning 1 and review 1 tie, broken name-ascending.
        self.assertEqual(row["categories"], "decision, learning, review")
        self.assert_journal_index_consistent(self.MONTH)


class SecondPassOnOneDateTests(RecordingTreeTestCase):

    def test_two_passes_on_one_date_make_one_file_and_two_recorded_passes(self):
        first = ["adr-review-stale-gate", "adr-review-unused-rule"]
        self.record_a_pass(self.DATE, first, time="09:00")

        # The second pass amends the same dated report rather than opening a new
        # one, and inherits every finding id the first pass wrote.
        self.record_a_pass(self.DATE, first + ["adr-review-orphan-objective"],
                           time="16:40")

        reports = sorted(p.name for p in self.reviews.iterdir()
                         if p.name != "index.md")
        self.assertEqual(reports, [f"{self.DATE}.md"])

        body = (self.reviews / f"{self.DATE}.md").read_text(encoding="utf-8")
        for fid in first:
            self.assertIn(fid, body, f"inherited finding {fid} dropped")
        self.assertEqual(len(set(FINDING_RE.findall(body))), 3)

        log_text = self.log.read_text(encoding="utf-8")
        self.assertEqual(len(ADR_REVIEW_OP_RE.findall(log_text)), 2)
        self.assertEqual(len(JOURNAL_OP_RE.findall(log_text)), 2)

        entries = journal_entries(
            (self.journal / f"{self.MONTH}.md").read_text(encoding="utf-8"),
            self.MONTH)
        self.assertEqual([c for _, c in entries], ["review", "review"])

        index = (self.reviews / "index.md").read_text(encoding="utf-8")
        # The fixture report declares no grammar, so its row carries the
        # frozen legacy count, labelled, and the literal `unknown` beside it.
        self.assertIn(f"| {self.DATE} | 3 (legacy) | unknown 3 | 0 |", index)
        self.assert_index_clean()
        self.assert_journal_index_consistent(self.MONTH)


class AbsentMonthFileTests(RecordingTreeTestCase):

    def test_recording_works_when_the_month_file_does_not_exist_yet(self):
        self.assertFalse((self.journal / f"{self.MONTH}.md").exists())
        self.record_a_pass(self.DATE, ["adr-review-one"])
        self.assertTrue((self.journal / f"{self.MONTH}.md").is_file())
        self.assert_index_clean()
        self.assert_journal_index_consistent(self.MONTH)


class ConsistencyNegativeControlTests(RecordingTreeTestCase):
    """The consistency assertion is not vacuous: seeded drift turns it red.

    `test_the_consistency_assertion_passes_on_a_correct_row` is the paired
    positive control — same helper, same fixture, one seeded difference.
    """

    def setUp(self) -> None:
        super().setUp()
        self.record_a_pass(self.DATE, ["adr-review-one"])

    def test_the_consistency_assertion_passes_on_a_correct_row(self):
        self.assert_journal_index_consistent(self.MONTH)

    def test_a_count_one_behind_the_month_file_fails_the_consistency_assertion(self):
        # A second entry lands in the month file; the row keeps the old count.
        self.append_journal_entry(self.MONTH, "2026-09-09", "08:00",
                                  "learning", "later")
        with self.assertRaises(AssertionError):
            self.assert_journal_index_consistent(self.MONTH)

    def test_a_missing_row_fails_the_consistency_assertion(self):
        (self.journal / "index.md").write_text(render_rows([]), encoding="utf-8")
        with self.assertRaises(AssertionError):
            self.assert_journal_index_consistent(self.MONTH)


class FenceAwareCountTests(RecordingTreeTestCase):
    """The count comes from a fence-aware split, so a quoted heading opens no
    entry. The positive control writes the SAME heading text unfenced and
    proves the fixture can move the count."""

    HEADING = "## [2026-09-09 08:00] decision | a quoted heading"

    def test_a_heading_quoted_inside_a_fence_opens_no_entry(self):
        self.record_a_pass(self.DATE, ["adr-review-one"])
        path = self.journal / f"{self.MONTH}.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + f"\n```text\n{self.HEADING}\n```\n", encoding="utf-8")
        self.assertEqual(
            derive_row(self.MONTH, path.read_text(encoding="utf-8"))["entries"], 1)
        # The row written before the fenced quote still agrees with the file.
        self.assert_journal_index_consistent(self.MONTH)

    def test_positive_control_the_same_heading_unfenced_opens_an_entry(self):
        self.record_a_pass(self.DATE, ["adr-review-one"])
        path = self.journal / f"{self.MONTH}.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + f"\n{self.HEADING}\n\nOne line.\n", encoding="utf-8")
        self.assertEqual(
            derive_row(self.MONTH, path.read_text(encoding="utf-8"))["entries"], 2)
        with self.assertRaises(AssertionError):
            self.assert_journal_index_consistent(self.MONTH)


class MonthRolloverTests(RecordingTreeTestCase):
    """No lane before this drove a review pass landing on the FIRST of a new
    month — the case where `generate-journal-index.py` must both CREATE a new
    `journal/YYYY-MM.md` row and leave the PREVIOUS month's row untouched.

    `record_a_pass`'s own `write_journal_index` helper only ever regenerates
    the ONE month it is handed (see its call in `record_a_pass`, `[month]`),
    so a rollover this suite drove through that helper alone would silently
    drop the previous month's row rather than proving it survives — this
    lane instead drives the SHIPPED regenerator, which walks every month file
    under `journal/` on every run, and checks both rows it writes.
    """

    PREV_MONTH = "2026-08"
    NEW_DATE = "2026-09-01"

    def setUp(self) -> None:
        super().setUp()
        # `generate-journal-index.py` (unlike `generate-reviews-index.py`,
        # which the base fixture already satisfies) refuses to operate on a
        # tree with no `manifest.yml` carrying `schema_version` and
        # `concerns_enabled` — this is the one path in this file that drives
        # it, so this is the one fixture that needs the manifest.
        (self.docs / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [journal]\n',
            encoding="utf-8")

    def run_journal_index_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(JOURNAL_INDEX_SCRIPT),
             "--repo-root", str(self.root), *args],
            capture_output=True, text=True, timeout=60)

    def regenerate_journal_index(self) -> None:
        result = self.run_journal_index_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_pass_on_the_first_of_a_new_month_creates_the_month_and_keeps_the_prior_row(self):
        # Seed a populated previous month: three entries, three dates, three
        # distinct categories, then regenerate with the SHIPPED script — not
        # the fixture's own single-month helper — so the seeded row is one
        # the production regenerator itself wrote.
        self.append_journal_entry(self.PREV_MONTH, "2026-08-05", "09:00",
                                  "decision", "a")
        self.append_journal_entry(self.PREV_MONTH, "2026-08-12", "10:00",
                                  "bug", "b")
        self.append_journal_entry(self.PREV_MONTH, "2026-08-20", "11:00",
                                  "learning", "c")
        self.regenerate_journal_index()

        index_before = (self.journal / "index.md").read_text(encoding="utf-8")
        prev_row_before = parse_index_row(index_before, self.PREV_MONTH)

        # POSITIVE CONTROL: the seeded row is really there, non-trivial, and
        # matches the month file it was derived from — an empty or absent
        # row here would make the "unchanged" assertion below vacuous.
        self.assertIsNotNone(prev_row_before)
        self.assertEqual(prev_row_before["entries"], 3)
        self.assertEqual(prev_row_before["first"], "2026-08-05")
        self.assertEqual(prev_row_before["last"], "2026-08-20")
        self.assertEqual(prev_row_before["categories"], "bug, decision, learning")
        self.assertEqual(
            prev_row_before,
            derive_row(self.PREV_MONTH,
                      (self.journal / f"{self.PREV_MONTH}.md")
                      .read_text(encoding="utf-8")))

        # A review pass lands on the 1st of the NEXT month: no
        # `journal/2026-09.md` exists yet, so recording it must create one.
        new_month = self.NEW_DATE[:7]
        self.assertFalse((self.journal / f"{new_month}.md").exists())
        self.write_report(self.NEW_DATE, ["adr-review-rollover-one"])
        self.append_op(self.NEW_DATE, "adr-review",
                       f"decision review {self.NEW_DATE}")
        self.append_journal_entry(new_month, self.NEW_DATE, "09:15", "review",
                                  f"decision review {self.NEW_DATE}")
        self.append_op(self.NEW_DATE, "journal",
                       f"decision review {self.NEW_DATE}")
        self.assertTrue((self.journal / f"{new_month}.md").is_file())

        self.regenerate_journal_index()
        index_after = (self.journal / "index.md").read_text(encoding="utf-8")

        # THE NEW MONTH'S ROW is present and correct.
        new_row = parse_index_row(index_after, new_month)
        self.assertIsNotNone(new_row, f"no row for the new month {new_month}")
        self.assertEqual(new_row["entries"], 1)
        self.assertEqual(new_row["first"], self.NEW_DATE)
        self.assertEqual(new_row["last"], self.NEW_DATE)
        self.assertEqual(new_row["categories"], "review")

        # THE PREVIOUS MONTH'S ROW is unchanged: same counts, same dates.
        prev_row_after = parse_index_row(index_after, self.PREV_MONTH)
        self.assertIsNotNone(prev_row_after)
        self.assertEqual(prev_row_before, prev_row_after)

        self.assert_journal_index_consistent(self.PREV_MONTH)
        self.assert_journal_index_consistent(new_month)

    def test_negative_control_a_corrupted_previous_month_row_fails_consistency(self):
        """The check above has teeth: a wrong previous-month row is caught.

        Follows `ConsistencyNegativeControlTests`'s pattern — same
        `assert_journal_index_consistent` helper, one seeded corruption.
        """
        self.append_journal_entry(self.PREV_MONTH, "2026-08-05", "09:00",
                                  "decision", "a")
        self.append_journal_entry(self.PREV_MONTH, "2026-08-12", "10:00",
                                  "bug", "b")
        self.append_journal_entry(self.PREV_MONTH, "2026-08-20", "11:00",
                                  "learning", "c")
        self.regenerate_journal_index()

        new_month = self.NEW_DATE[:7]
        self.append_journal_entry(new_month, self.NEW_DATE, "09:15", "review",
                                  f"decision review {self.NEW_DATE}")
        self.regenerate_journal_index()

        # PAIRED POSITIVE CONTROL: before any corruption, both rows check out.
        self.assert_journal_index_consistent(self.PREV_MONTH)
        self.assert_journal_index_consistent(new_month)

        # Corrupt the previous month's row in place: a fourth entry the row
        # never counted. The month file itself is untouched, so only the
        # index row is now wrong.
        index_text = (self.journal / "index.md").read_text(encoding="utf-8")
        corrupted = index_text.replace(
            "| 2026-08 | 2026-08-05 | 2026-08-20 | 3 | bug, decision, learning |",
            "| 2026-08 | 2026-08-05 | 2026-08-20 | 4 | bug, decision, learning |")
        self.assertNotEqual(corrupted, index_text,
                            "fixture did not locate the row to corrupt")
        (self.journal / "index.md").write_text(corrupted, encoding="utf-8")

        with self.assertRaises(AssertionError):
            self.assert_journal_index_consistent(self.PREV_MONTH)
        # The untouched new-month row is still consistent — the corruption is
        # scoped to the row it targeted.
        self.assert_journal_index_consistent(new_month)


if __name__ == "__main__":
    unittest.main()
