"""Conformance suite for `md_fences.py`, the CommonMark fenced-code-block subset.

ONE table, driven through THREE names: the module's own `fence_marker`, and
the `_fence_marker` each caller imports (`adr-signals.py` and
`check_template_parity.py`). The three-way drive is the point. The subset used
to be two hand-copied functions, and a differential test between the two
copies passed 486 inputs while both copies carried the SAME defect — a twin
comparison finds a divergence and never a shared error. A table with an
EXPECTED column finds a shared error, and pinning both callers to it makes a
future re-copy fail here rather than silently.

Every case cites the numbered rule of the `md_fences` module contract it
covers, so a row cannot be deleted without deleting the rule it stands for.

Reads `crux/scripts/` and nothing else, so the suite passes unchanged against
the crux-only staged artifact.
"""

from __future__ import annotations

import ast
import re
import importlib.util
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


md_fences = _load("md_fences_under_test", "md_fences.py")
sig = _load("adr_signals_under_test", "adr-signals.py")
ctp = _load("check_template_parity_under_test", "check_template_parity.py")

#: The three names the table is driven through. A caller that stops importing
#: the shared subset — by re-copying it, or by drifting its own rules — fails
#: every row it disagrees with.
IMPLEMENTATIONS = (
    ("md_fences.fence_marker", md_fences.fence_marker),
    ("adr-signals._fence_marker", sig._fence_marker),
    ("check_template_parity._fence_marker", ctp._fence_marker),
)

CLOSERS = (
    ("md_fences.closes_fence", md_fences.closes_fence),
    ("adr-signals._closes_fence", sig._closes_fence),
    ("check_template_parity._closes_fence", ctp._closes_fence),
)

# (line, expected marker or None, the contract rule the row stands for)
CASES: tuple[tuple[str, tuple[str, int, str] | None, str], ...] = (
    # ── rule 1: the fence character, and the two do not mix ──────────────
    ("```", ("`", 3, ""), "1 backtick run"),
    ("~~~", ("~", 3, ""), "1 tilde run"),
    ("***", None, "1 no other character is a fence character"),
    ("---", None, "1 a thematic break is not a fence"),
    ("`~`~`~", None, "1 the two characters do not mix"),

    # ── rule 2: run length ───────────────────────────────────────────────
    ("``", None, "2 two backticks is not a run"),
    ("~~", None, "2 two tildes is not a run"),
    ("````", ("`", 4, ""), "2 four is a run"),
    ("``````````", ("`", 10, ""), "2 ten is a run"),

    # ── rule 3: the 0-3 column indent bound ──────────────────────────────
    (" ```", ("`", 3, ""), "3 one column is admitted"),
    ("  ```", ("`", 3, ""), "3 two columns are admitted"),
    ("   ```", ("`", 3, ""), "3 three columns are admitted"),
    ("    ```", None, "3 four columns is indented code"),
    ("     ```", None, "3 five columns is indented code"),
    ("   ~~~", ("~", 3, ""), "3 the bound applies to a tilde run"),
    ("    ~~~", None, "3 the bound applies to a tilde run"),

    # ── rule 4: tab expansion at four columns ────────────────────────────
    ("\t```", None, "4 a tab alone is four columns"),
    (" \t```", None, "4 one space then a tab reaches column four"),
    ("   \t```", None, "4 three spaces then a tab reaches column four"),
    ("\t\t```", None, "4 two tabs are eight columns"),
    # THE DISCRIMINATING ROW FOR RULE 4. Every row above expects None, and a
    # tab is not matched by the run pattern either way — so `TAB_STOP` 4->8,
    # and deleting `expandtabs` outright, both left this suite green while
    # rule 4 went unguarded. This row reads the expansion's RESULT: at stop 4
    # the tab after a three-character run advances one column, so the info
    # string is one space; at stop 8 it would be five, and with no expansion
    # at all it would be the tab itself.
    ("```\tpython", ("`", 3, " python"), "4 a tab in the info string expands to the next stop"),
    ("````\tx", ("`", 4, "    x"), "4 the expansion reaches the info string, not only the indent"),

    # ── rule 5: indentation is spaces and tabs and NOTHING else ──────────
    # THE DEFECT THE EXTRACTION CLOSED. Every character below is whitespace
    # to `str.lstrip()` and is NOT indentation to CommonMark, so each of these
    # lines opens on a character that is not a fence character and is not a
    # marker. The two copies read all of them as markers.
    ("\x0b```", None, "5 U+000B is not indentation"),
    ("\x0c```", None, "5 U+000C is not indentation"),
    ("\r```", None, "5 U+000D is not indentation"),
    ("\x1c```", None, "5 U+001C is not indentation"),
    ("\x1d```", None, "5 U+001D is not indentation"),
    ("\x1e```", None, "5 U+001E is not indentation"),
    ("\x85```", None, "5 U+0085 is not indentation"),
    ("\xa0```", None, "5 U+00A0 is not indentation"),
    (" ```", None, "5 U+2028 is not indentation"),
    (" ```", None, "5 U+2029 is not indentation"),
    # The measured end-to-end forgery: one non-space character in front of a
    # four-space indent made the indent measure as ZERO.
    ("     ```", None, "5 a non-space prefix does not zero the indent"),
    ("\xa0   ```", None, "5 a non-space prefix does not zero the indent"),
    # A non-space character AFTER admitted indentation is likewise not a run.
    ("  \xa0```", None, "5 the run must begin at the first non-space"),

    # ── rule 6: the info string, and the backtick restriction ────────────
    ("```python", ("`", 3, "python"), "6 the info string is captured"),
    ("``` text ", ("`", 3, " text "), "6 the info string is captured verbatim"),
    ("``` `x", None, "6 a backtick in a backtick info string is no fence"),
    ("~~~ `x", ("~", 3, " `x"), "6 a tilde info string may carry a backtick"),
    ("~~~~~~", ("~", 6, ""), "6 a tilde run is not its own info string"),
)

#: (info string carried by the closing marker, does it close a ``` fence,
#: the contract rule the row stands for). The MIRROR of the rule-5 rows
#: above, on the trailing side.
#:
#: WHY THIS TABLE EXISTS. Rule 5 bounds the characters that may precede a run
#: to spaces and tabs, and the table above pins ten non-space whitespace
#: characters against it. The CLOSER's tail was left on a bare `.strip()`,
#: which admits every character `str.isspace()` does — so the same U+00A0 and
#: U+2028 the leading side refuses still closed a fence from the trailing
#: side, and the end-to-end effect was identical: `gate_count` read a decoy
#: roster's 1 where the honest document gives 2. A defect class closed on one
#: side and left on its sibling is the shape these rows exist to stop.
CLOSER_TAIL_CASES: tuple[tuple[str, bool, str], ...] = (
    # ── rule 6, closer half: spaces and tabs close; anything else does not ──
    ("", True, "6 a bare run closes"),
    (" ", True, "6 a trailing space closes"),
    ("   ", True, "6 trailing spaces close"),
    ("\t", True, "6 a trailing tab closes"),
    (" \t  \t", True, "6 trailing spaces and tabs close"),
    ("python", False, "6 an info string does not close"),
    (" x ", False, "6 a word between spaces does not close"),
    # ── rule 6, closer half: the non-space whitespace class, mirroring 5 ──
    ("\x0b", False, "6 U+000B after the run does not close"),
    ("\x0c", False, "6 U+000C after the run does not close"),
    ("\r", False, "6 U+000D after the run does not close"),
    ("\x1c", False, "6 U+001C after the run does not close"),
    ("\x1d", False, "6 U+001D after the run does not close"),
    ("\x1e", False, "6 U+001E after the run does not close"),
    ("\x85", False, "6 U+0085 after the run does not close"),
    ("\xa0", False, "6 U+00A0 after the run does not close"),
    ("\u2028", False, "6 U+2028 after the run does not close"),
    ("\u2029", False, "6 U+2029 after the run does not close"),
    ("\u1680", False, "6 U+1680 after the run does not close"),
    # A hostile character hidden BEHIND legitimate trailing space is refused
    # too — the strip is a character class, not a prefix test.
    ("  \xa0", False, "6 a non-space behind trailing spaces does not close"),
    ("\xa0  ", False, "6 a non-space before trailing spaces does not close"),
)


class FenceMarkerConformanceTests(unittest.TestCase):
    """The enumerated subset, one row per rule, three implementations."""

    def test_every_case_agrees_across_every_implementation(self):
        for line, expected, rule in CASES:
            for label, impl in IMPLEMENTATIONS:
                with self.subTest(rule=rule, line=repr(line), impl=label):
                    self.assertEqual(impl(line), expected)

    def test_the_table_covers_every_numbered_rule_of_the_contract(self):
        """A positive control on the TABLE, not on the code.

        Rules 1-6 are `fence_marker`'s; rule 7 is `closes_fence`'s and is
        covered by the closer cases below. Deleting a rule's rows would
        otherwise leave this suite green while the rule went unguarded.
        """
        covered = {rule.split()[0] for _line, _expected, rule in CASES}
        self.assertEqual(covered, {"1", "2", "3", "4", "5", "6"})
        # Rule 6's CLOSER half lives in its own table, so it is asserted here
        # too — otherwise the table above could satisfy "6 is covered" with
        # opener rows alone while the closer went unguarded, which is how the
        # trailing-side defect survived a suite that named rule 6 covered.
        closer_covered = {rule.split()[0] for _tail, _ok, rule in CLOSER_TAIL_CASES}
        self.assertEqual(closer_covered, {"6"})

    def test_the_run_pattern_is_built_from_the_constants_that_name_it(self):
        """`MIN_RUN` and `FENCE_CHARS` GOVERN the pattern; they do not describe it.

        Both were declared in `md_fences.py` and read by nothing — the pattern
        spelled ``` `{3,}|~{3,} ``` itself — so editing `MIN_RUN` to 4 changed
        no behaviour and turned no test red, while the constant went on
        reading as if it were the rule. This row fails on a pattern that stops
        deriving from them, and the behaviour rows above fail on a constant
        edited to a value the subset does not mean.
        """
        expected = ("^("
                    + "|".join(f"{re.escape(c)}{{{md_fences.MIN_RUN},}}"
                               for c in md_fences.FENCE_CHARS)
                    + ")(.*)$")
        self.assertEqual(md_fences._FENCE_RUN.pattern, expected)
        self.assertEqual(md_fences.MIN_RUN, 3)
        self.assertEqual(md_fences.FENCE_CHARS, ("`", "~"))

    def test_the_indent_bound_is_pinned_by_its_paired_control(self):
        """Three columns is a marker, four is not — the discriminating pair.

        The suite this replaces asserted `'    ```'` and `'     ```'` are both
        refused, which is TRUE and proves nothing: a `fence_marker` that
        refused every indent would pass it. The pair that discriminates is
        three-versus-four, and the four-versus-five pair is kept beside it so
        the bound is shown to be a bound rather than an off-by-one.
        """
        for label, impl in IMPLEMENTATIONS:
            with self.subTest(impl=label):
                self.assertIsNotNone(impl("   ```"))   # 3 columns: a marker
                self.assertIsNone(impl("    ```"))     # 4 columns: code
                self.assertIsNone(impl("     ```"))    # 5 columns: code

    def test_a_leading_non_space_is_refused_and_its_bare_run_is_admitted(self):
        """The paired control for rule 5.

        The refusal is only meaningful beside the same run with nothing in
        front of it: without this pair, a `fence_marker` that refused every
        line would pass the rule-5 rows.
        """
        for prefix in ("\x0b", "\r", "\xa0", " ", "\x85"):
            for label, impl in IMPLEMENTATIONS:
                with self.subTest(prefix=repr(prefix), impl=label):
                    self.assertIsNone(impl(prefix + "```"))
                    self.assertEqual(impl("```"), ("`", 3, ""))


class ClosesFenceConformanceTests(unittest.TestCase):
    """Rule 7, plus the closer half of rules 1 and 6."""

    def test_a_closer_needs_the_same_character(self):
        for label, closes in CLOSERS:
            with self.subTest(impl=label):
                self.assertTrue(closes(("`", 3, ""), ("`", 3)))
                self.assertFalse(closes(("~", 3, ""), ("`", 3)))
                self.assertFalse(closes(("`", 3, ""), ("~", 3)))

    def test_a_closer_run_is_at_least_the_openers(self):
        for label, closes in CLOSERS:
            with self.subTest(impl=label):
                # Longer and equal close; shorter does not — which is what
                # lets four backticks wrap three-backtick content.
                self.assertTrue(closes(("`", 4, ""), ("`", 3)))
                self.assertTrue(closes(("`", 3, ""), ("`", 3)))
                self.assertFalse(closes(("`", 3, ""), ("`", 4)))

    def test_a_closer_carries_nothing_but_spaces_and_tabs_after_its_run(self):
        """The whole `CLOSER_TAIL_CASES` table, through all three closers.

        Both signs are in the table, so a `closes_fence` that refused every
        tail and one that accepted every tail each fail rows here.
        """
        for tail, expected, rule in CLOSER_TAIL_CASES:
            for label, closes in CLOSERS:
                with self.subTest(rule=rule, tail=repr(tail), impl=label):
                    self.assertIs(closes(("`", 3, tail), ("`", 3)), expected)

    def test_the_closer_tail_class_is_the_same_on_a_tilde_fence(self):
        """Rule 1 does not change the tail class. The trailing-side defect
        reached a `~~~` fence exactly as it reached a ``` one, so the table
        is driven through both fence characters rather than one."""
        for tail, expected, rule in CLOSER_TAIL_CASES:
            for label, closes in CLOSERS:
                with self.subTest(rule=rule, tail=repr(tail), impl=label):
                    self.assertIs(closes(("~", 3, tail), ("~", 3)), expected)

    def test_the_closer_table_covers_both_signs_of_the_whitespace_class(self):
        """A positive control on the TABLE, not on the code.

        Deleting every False row would leave `test_a_closer_carries...` green
        against a closer that accepts U+00A0 again — which is precisely how
        the trailing side shipped. Deleting every True row would leave it
        green against a closer that accepts nothing at all.
        """
        accepted = [t for t, ok, _ in CLOSER_TAIL_CASES if ok]
        refused = [t for t, ok, _ in CLOSER_TAIL_CASES if not ok]
        self.assertGreaterEqual(len(accepted), 4)
        self.assertGreaterEqual(len(refused), 10)
        # Every refused tail that is NOT a word is whitespace to Python and
        # is not whitespace to CommonMark — the class the bare `.strip()`
        # could not tell apart.
        hostile = [t for t in refused if t.strip(" \t") and t.strip(" \t").isspace()]
        self.assertGreaterEqual(len(hostile), 10)

    def test_a_non_marker_closes_nothing_and_needs_no_none_test_first(self):
        """`closes_fence` takes `fence_marker`'s verdict straight through.

        Every call site passes the marker without testing it for None first,
        so the None case is part of the contract rather than a caller's
        responsibility.
        """
        for label, closes in CLOSERS:
            with self.subTest(impl=label):
                self.assertFalse(closes(None, ("`", 3)))
                self.assertFalse(closes(("`", 3, ""), None))
                self.assertFalse(closes(None, None))


class TableFidelityTests(unittest.TestCase):
    """A row labelled U+2028 must CARRY U+2028.

    Every hostile character in both tables is an invisible one, and several
    of them normalize to a plain space when the file is edited through a tool
    that touches Unicode. A row whose label says U+2028 while its data holds
    U+0020 asserts the opposite of what it reads as — U+0020 SHOULD close a
    fence — and it would pass, silently, forever. This is the same false-green
    family the rest of this round closes: an assertion that reads as a guard
    and grades nothing. Measured during authoring: three closer rows were
    written with real separators and reached disk as plain spaces.
    """

    def _codepoint_rows(self, table, prefix: str):
        for row in table:
            data, _expected, rule = row
            if rule.startswith(prefix):
                yield data, rule, int(rule.split()[1][2:], 16)

    def test_every_rule_5_row_carries_the_codepoint_its_label_names(self):
        rows = list(self._codepoint_rows(CASES, "5 U+"))
        self.assertGreaterEqual(len(rows), 10)
        for line, rule, want in rows:
            with self.subTest(rule=rule):
                self.assertEqual(ord(line[0]), want,
                                 f"{rule}: row carries U+{ord(line[0]):04X}")

    def test_every_closer_row_carries_the_codepoint_its_label_names(self):
        rows = list(self._codepoint_rows(CLOSER_TAIL_CASES, "6 U+"))
        self.assertGreaterEqual(len(rows), 10)
        for tail, rule, want in rows:
            with self.subTest(rule=rule):
                self.assertEqual(ord(tail[0]), want,
                                 f"{rule}: row carries U+{ord(tail[0]):04X}")


class SharedImplementationTests(unittest.TestCase):
    """Each caller's function is DEFINED IN `md_fences.py`, not in its own file.

    This is the row that fails if someone re-copies the subset back into
    either caller. Provenance, not behaviour: a copy that happens to agree
    with the table today would pass every case above and still reintroduce
    the class the extraction closed — two implementations that can drift, and
    that no differential test between them can grade.
    """

    def _assert_defined_in_md_fences(self, func, expected_name: str) -> None:
        code = func.__code__
        self.assertEqual(Path(code.co_filename).name, "md_fences.py")
        self.assertEqual(code.co_name, expected_name)

    def test_both_callers_import_the_shared_marker(self):
        self._assert_defined_in_md_fences(sig._fence_marker, "fence_marker")
        self._assert_defined_in_md_fences(ctp._fence_marker, "fence_marker")

    def test_both_callers_import_the_shared_closer(self):
        self._assert_defined_in_md_fences(sig._closes_fence, "closes_fence")
        self._assert_defined_in_md_fences(ctp._closes_fence, "closes_fence")

    def test_neither_caller_still_defines_a_fence_subset_of_its_own(self):
        """The paired negative: no `def _fence_marker` survives in either
        caller. Without it, a file could import the shared name AND shadow it
        with a local copy, and the provenance rows above would still pass on
        whichever binding won."""
        for filename in ("adr-signals.py", "check_template_parity.py"):
            source = (SCRIPTS / filename).read_text(encoding="utf-8")
            with self.subTest(caller=filename):
                self.assertNotIn("def _fence_marker", source)
                self.assertNotIn("def fence_marker", source)
                self.assertNotIn("_FENCE_RUN = ", source)

    #: Every file that reads a Markdown or YAML document line by line. The ban
    #: below covers all three: the two callers, and the shared module that now
    #: holds the one split they are allowed to use.
    LINE_READERS = ("adr-signals.py", "check_template_parity.py", "md_fences.py")

    def test_no_line_reader_calls_splitlines(self):
        """The structural analogue of the fence-subset ban above.

        `str.splitlines()` ends a line on nine terminators no Markdown or YAML
        reader ends a line on, so one of them spelled mid-sentence forges a
        line nobody wrote — and every grammar these files anchor at the start
        of a line then reads it as real. Nine sites were converted in
        `adr-signals.py` and the TENTH, in `check_template_parity`, was
        missed; its failure mode was the quietest of the ten, because an
        unresolvable section reports P3 STALE and P3 does not flip the exit
        code.

        THE ABSENCE OF THIS ROW IS WHY THE TENTH SITE SURVIVED. A fix that
        enumerates call sites by hand misses the one in the other file; a
        structural ban cannot. Asserted over the AST rather than the text so a
        `splitlines` named in a docstring — several are, deliberately, to
        explain the ban — is not mistaken for a call.
        """
        for filename in self.LINE_READERS:
            tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
            calls = [
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "splitlines"
            ]
            with self.subTest(reader=filename):
                self.assertEqual(calls, [], f"{filename} calls splitlines()")

    def test_the_ban_is_asserted_over_a_parser_that_would_see_a_call(self):
        """The paired positive control for the ban.

        An AST walk that matched nothing — a wrong node type, a typo in the
        attribute name — would report every file clean forever. This drives
        the same matcher over source that DOES call `splitlines`, so the row
        above is known to be capable of failing.
        """
        tree = ast.parse("x = 'a'\ny = x.splitlines()\n")
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "splitlines"
        ]
        self.assertEqual(calls, ["splitlines"])

    def test_both_callers_import_the_shared_line_split(self):
        """`_lines` is DEFINED IN `md_fences.py` in both callers.

        Provenance, exactly as for the fence pair: a caller that re-copies the
        split would agree with the shared one today and drift tomorrow, and no
        differential test between two copies can grade a shared error.
        """
        self._assert_defined_in_md_fences(sig._lines, "split_lines")
        self._assert_defined_in_md_fences(ctp._lines, "split_lines")

    def test_neither_caller_still_defines_a_line_split_of_its_own(self):
        """The paired negative: no `def _lines` survives in either caller, so
        a file cannot import the shared name and shadow it with a local copy
        while the provenance row above passes on whichever binding won."""
        for filename in ("adr-signals.py", "check_template_parity.py"):
            source = (SCRIPTS / filename).read_text(encoding="utf-8")
            with self.subTest(caller=filename):
                self.assertNotIn("def _lines", source)
                self.assertNotIn("def split_lines", source)

    def test_every_line_reader_reads_files_untranslated(self):
        """No line reader may open a document in universal-newline mode.

        Banning `splitlines()` closes nine of the ten forged terminators and
        NOT `\\r`, because text mode rewrites a lone `\\r` to `\\n` before any
        split runs — the boundary is forged at the READ, one layer below the
        thing the ban governs. Every text read of a document in these files
        therefore passes `newline=""`. Measured: without it, a `\\r` before a
        fence run turned a live parity clause into an exit-0 P3 stale, which
        is the same false green U+000B produced through the split.

        A read whose result goes straight into `json.loads` is EXEMPT, and the
        exemption is computed rather than written down: JSON forbids a raw
        control character inside a string, and a `\\r` outside one is
        insignificant whitespace, so no line boundary can be forged there. The
        exemption is narrow on purpose — it covers the call it wraps and
        nothing else, so a new document read cannot inherit it.
        """
        for filename in self.LINE_READERS:
            tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
            exempt = {
                id(arg)
                for outer in ast.walk(tree)
                if isinstance(outer, ast.Call)
                and isinstance(outer.func, ast.Attribute)
                and outer.func.attr == "loads"
                for arg in outer.args
                if isinstance(arg, ast.Call)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or id(node) in exempt:
                    continue
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", None))
                if name not in ("open", "fdopen", "read_text"):
                    continue
                if any(k.arg == "encoding" for k in node.keywords) is False:
                    continue          # not a TEXT read
                kwargs = {k.arg for k in node.keywords}
                with self.subTest(reader=filename, line=node.lineno, call=name):
                    self.assertIn(
                        "newline", kwargs,
                        f"{filename}:{node.lineno} {name}() reads text with "
                        f"universal-newline translation left on",
                    )

    def test_the_subset_module_imports_nothing_outside_the_standard_library(self):
        """Stdlib only: the module is imported by a PEP 723 script that
        declares `dependencies = []`, so a third-party import here would
        break `check_template_parity.py` at every install."""
        tree = ast.parse((SCRIPTS / "md_fences.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual(names, {"re", "__future__"})


if __name__ == "__main__":
    unittest.main()
