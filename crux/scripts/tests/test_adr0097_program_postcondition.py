"""ADR-0097's Program postcondition (bionic/adrs/ADR-0097-scope-the-arch-input-
class-rule-to-what-the-code-h.md, "Program postcondition" paragraph and part 7):
after the summaries and doctrine projections regenerate, no doctrine surface
publishes a statement the code contradicts as of v3.3.0 — each of the four
false rules is retired with a true replacement in its domain, and no column
claims an implementation no one measured.

This is deliberately a LIVE-TREE test: it reads the regenerated projections at
`bionic/adrs/summaries/rule-table.md` and `bionic/adrs/doctrine/index.md`
rather than a fixture, because the postcondition is a claim about what those
two files actually publish once the machinery lands and the projections
regenerate. Two sibling agents are landing that machinery separately; until
both land AND the projections regenerate, this test is expected to FAIL RED —
that is the intended shape, not a bug in the test.

Pins, one named test per claim:

  - `test_adr0097_program_postcondition_holds` — asserts all three properties
    together, over the live-tree projections:
      1. No retired handle (ADR-0096/declared-input-class-never-source-regex,
         ADR-0067/static-parsing-only, ADR-0068/static-parsing-only,
         ADR-0069/static-parsing-only) appears in the rule table's LIVE rows
         (the segment before the first `## ` heading that follows the live
         table) or anywhere in the doctrine index. Each DOES appear in the
         rule table's `## Retired handles` roster — the record survives the
         retirement, so "absent from live rows" cannot be satisfied by the
         handle vanishing from the projection altogether.
      2. Each of the four replacement handles
         (ADR-0097/declared-input-class-and-roster,
         ADR-0097/ruby-static-declared-parsers,
         ADR-0097/node-static-declared-parsers,
         ADR-0097/elixir-static-declared-parsers) appears in the rule
         table's live rows AND in the doctrine index.
      3. `implemented_claims()` finds nothing in the doctrine index, and the
         `| basis |` column heading is present in its rules tables.
  - `ImplementedClaimsPredicateTests` — the mandatory positive control for
    assertion 3 (an absence claim over a generated file), consulting the
    project-local `false-green-test-guard` skill. Feeds `implemented_claims`
    a synthetic doctrine-shaped document carrying "implemented" in each of
    the three banned positions (a table header cell, a non-table prose line,
    and a non-`rule` data cell) and asserts one named finding per position.
    A second synthetic document carries "implemented" ONLY inside a `rule`
    column cell (verbatim ADR-0097 rule text, exactly as the doctrine index
    legitimately renders it) and asserts the predicate returns nothing for
    it — so the carve-out is exercised, not merely assumed.

Runs under: uv run pytest crux/scripts/tests/test_adr0097_program_postcondition.py -q
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# The tests below read `bionic/` — a dev-only surface absent from the staged
# crux-only release artifact (ADR-0036 boundary). Guard, never false-fail.
try:
    from ._dev_surface import require_dev_surface
except ImportError:
    from _dev_surface import require_dev_surface

sys.path.insert(0, str(REPO_ROOT / "crux" / "scripts"))

RULE_TABLE_PATH = REPO_ROOT / "bionic" / "adrs" / "summaries" / "rule-table.md"
DOCTRINE_INDEX_PATH = REPO_ROOT / "bionic" / "adrs" / "doctrine" / "index.md"
#: The THIRD published surface, and the one the first cut of this test omitted.
#: `retires` removes a handle from the rule table's live rows AND from the
#: resolver; checking only the two markdown surfaces would let a retired handle
#: keep resolving from the machine-readable one, which is the surface another
#: tool reads. ADR-0097 part 6 names the resolver alongside the live rows.
RESOLVER_PATH = REPO_ROOT / "bionic" / "adrs" / "summaries" / "resolver.json"

RETIRED_HANDLES = [
    "ADR-0096/declared-input-class-never-source-regex",
    "ADR-0067/static-parsing-only",
    "ADR-0068/static-parsing-only",
    "ADR-0069/static-parsing-only",
]

REPLACEMENT_HANDLES = [
    "ADR-0097/declared-input-class-and-roster",
    "ADR-0097/ruby-static-declared-parsers",
    "ADR-0097/node-static-declared-parsers",
    "ADR-0097/elixir-static-declared-parsers",
]

RETIRED_HANDLES_HEADING = "## Retired handles"

_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|?\s*$")


def _split_cells(line: str) -> list[str]:
    """Split a `| a | b | c |` markdown table row into stripped cell strings."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _live_segment(rule_table_text: str) -> str:
    """The rule table's LIVE rows: everything before the first `## ` heading
    (the removed/retired-handles rosters both live under `## ` headings,
    below the file's single `# ` title)."""
    lines = rule_table_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("## "):
            return "".join(lines[:i])
    return rule_table_text


def _section(text: str, heading: str) -> str:
    """The body of the named `## <heading>` section, up to the next `## `
    heading or end of file. Empty string when the heading is absent."""
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "".join(lines[start:end])


# The columns whose content an ADR authored and doctrine quotes verbatim. A
# sweep for doctrine's own claims must not read a projected quotation as one.
_AUTHORED_COLUMNS = ("rule", "handle", "citation")


def implemented_claims(text: str) -> list[str]:
    """Return one human-readable location string per line of `text` that
    claims (or implies) a rule "is implemented", scoped mechanically:

      - a table HEADER row (a `|`-led line whose next line is a
        `|---|---|`-style separator) containing "implemented" in any cell;
      - a NON-TABLE line (not starting with `|`) containing "implemented";
      - a table DATA cell containing "implemented" outside that table's own
        `rule`, `handle` and `citation` columns (all resolved from that
        table's own header row).

    Those three columns are deliberately exempt, and for one reason rather
    than three: each carries text an ADR AUTHORED and doctrine quotes
    verbatim, so none is doctrine claiming anything. `citation` is the slug
    half of `handle` behind a `rule:` prefix, so exempting the handle while
    sweeping the slug cut out of it would flag one string and not the other.
    `ADR-0097/basis-not-implemented` renders the citation
    `rule:basis-not-implemented`, and "implemented" sits inside the ADR's own
    minted slug. ADR-0097's own governs rule reads
    "The doctrine index claims of no rule that it is implemented ...", and the
    handle it minted for that rule is `ADR-0097/basis-not-implemented`. A
    sweep that flagged either would be asserting that the projection may not
    reproduce the decision it projects. Every other cell — `source ADR`,
    `disposition`, `basis`, and every cell of the evidence and reconciliation
    tables — is doctrine-derived and is checked.
    """
    findings: list[str] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    in_table = False
    exempt_cols: set[int] = set()
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("|"):
            next_line = lines[i + 1] if i + 1 < n else ""
            if _TABLE_SEP_RE.match(next_line.strip()):
                # Header row.
                cells = _split_cells(line)
                if any("implemented" in c.lower() for c in cells):
                    findings.append(f"header row {i + 1}: {stripped}")
                exempt_cols = {idx for idx, c in enumerate(cells)
                               if c.strip().lower() in _AUTHORED_COLUMNS}
                in_table = True
                i += 2
                continue
            if in_table:
                # Data row.
                cells = _split_cells(line)
                for idx, c in enumerate(cells):
                    if idx in exempt_cols:
                        continue
                    if "implemented" in c.lower():
                        findings.append(
                            f"data row {i + 1} col {idx} "
                            f"(not an ADR-authored column): {stripped}"
                        )
                i += 1
                continue
            # A `|`-led line with no header context yet (e.g. a stray pipe
            # line outside any recognized table) is not evaluated — there is
            # no table shape to resolve a rule column against.
            i += 1
            continue
        # Non-table line.
        in_table = False
        exempt_cols = set()
        if stripped and "implemented" in line.lower():
            findings.append(f"non-table line {i + 1}: {stripped}")
        i += 1
    return findings


class Adr0097ProgramPostconditionTests(unittest.TestCase):
    """The Program postcondition, asserted as one test over the live-tree
    regenerated projections. Expected RED until the summaries + doctrine
    machinery lands and both projections regenerate; do not weaken an
    assertion to make it pass early."""

    def setUp(self):
        require_dev_surface(self, REPO_ROOT / "bionic" / "manifest.yml", "bionic/manifest.yml")
        self.rule_table = RULE_TABLE_PATH.read_text(encoding="utf-8")
        self.doctrine = DOCTRINE_INDEX_PATH.read_text(encoding="utf-8")
        self.resolver = RESOLVER_PATH.read_text(encoding="utf-8")

    def test_adr0097_program_postcondition_holds(self):
        live = _live_segment(self.rule_table)
        retired_roster = _section(self.rule_table, RETIRED_HANDLES_HEADING)

        # Property 1: no retired handle publishes as live, in either
        # surface — and each DOES survive in the retired-handles roster.
        for handle in RETIRED_HANDLES:
            self.assertNotIn(
                handle, live,
                f"{handle} still appears in rule-table.md's LIVE rows",
            )
            self.assertNotIn(
                handle, self.doctrine,
                f"{handle} still appears in the doctrine index",
            )
            self.assertNotIn(
                handle, self.resolver,
                f"{handle} still resolves from resolver.json — a retirement "
                "that leaves the machine-readable surface intact is not a "
                "retirement",
            )
            self.assertIn(
                handle, retired_roster,
                f"{handle} is missing from the rule table's "
                f"'{RETIRED_HANDLES_HEADING}' roster — retirement must "
                "record the handle, not merely vanish it",
            )

        # Property 2: each replacement publishes, in both surfaces.
        for handle in REPLACEMENT_HANDLES:
            self.assertIn(
                handle, live,
                f"{handle} is missing from rule-table.md's LIVE rows",
            )
            self.assertIn(
                handle, self.doctrine,
                f"{handle} is missing from the doctrine index",
            )
            self.assertIn(
                handle, self.resolver,
                f"{handle} is missing from resolver.json — the control for "
                "the resolver assertion above, which would pass just as well "
                "against an empty file",
            )

        # Property 3: no doctrine surface claims a rule is implemented,
        # scoped away from the `rule`-column verbatim ADR text.
        findings = implemented_claims(self.doctrine)
        self.assertEqual(
            findings, [],
            f"the doctrine index claims implementation outside the rule "
            f"column: {findings}",
        )
        self.assertIn(
            "| basis |", self.doctrine,
            "the doctrine index's rules tables are missing the "
            "'basis' column heading",
        )


# ── Positive control for `implemented_claims` (mandatory per the
# false-green-test-guard skill: assertion 3 above is an absence claim over a
# generated file, so it needs a paired control proving the predicate fires). ─

_BAD_DOC = "\n".join([
    "# doctrine index",
    "",
    "This document says a rule is implemented, which is banned prose.",
    "",
    "## table-with-bad-header",
    "",
    "| handle | rule | implemented |",
    "|---|---|---|",
    "| ADR-0001/a | some rule text | yes |",
    "",
    "## table-with-bad-data-cell",
    "",
    "| handle | rule | status |",
    "|---|---|---|",
    "| ADR-0002/b | another rule text | implemented somewhat |",
    "",
])

_GOOD_DOC = "\n".join([
    "# doctrine index",
    "",
    "## table-authored-columns-only",
    "",
    "| handle | rule | basis |",
    "|---|---|---|",
    (
        "| ADR-0097/basis-not-implemented | The doctrine index claims of "
        "no rule that it is implemented; each rule row names the "
        "mechanical basis standing behind it. | run-bound |"
    ),
    "",
])


class ImplementedClaimsPredicateTests(unittest.TestCase):
    """Positive control for `implemented_claims`. Drives the same predicate
    the absence assertion above depends on, over synthetic fixtures rather
    than the live tree, so the predicate's firing is proven independent of
    whether the live doctrine index happens to be clean today."""

    def test_fires_once_for_each_of_the_three_banned_positions(self):
        findings = implemented_claims(_BAD_DOC)
        self.assertEqual(
            len(findings), 3,
            f"expected exactly one finding per banned position, got: {findings}",
        )
        self.assertTrue(
            any(f.startswith("non-table line") for f in findings),
            f"no non-table-line finding among: {findings}",
        )
        self.assertTrue(
            any(f.startswith("header row") for f in findings),
            f"no header-row finding among: {findings}",
        )
        self.assertTrue(
            any(f.startswith("data row") for f in findings),
            f"no data-row finding among: {findings}",
        )

    def test_stays_silent_when_implemented_appears_only_in_authored_columns(self):
        # The fixture puts the word in BOTH exempt columns at once — the
        # handle `ADR-0097/basis-not-implemented` and the rule text that
        # decision authored — because those are the two real occurrences the
        # live doctrine index carries. Without this leg the carve-out is
        # untested and the predicate could be trivially over-broad (flagging
        # the projection for quoting the decision) or trivially inert.
        self.assertIn("implemented", _GOOD_DOC,
                      "fixture assumption: the good document must contain the "
                      "word, or this leg proves nothing")
        self.assertEqual(
            implemented_claims(_GOOD_DOC), [],
            "the authored-column carve-out is over-broad or the predicate is "
            "trivially inert: it should find nothing when 'implemented' "
            "occurs only inside a `rule` or `handle` cell",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
