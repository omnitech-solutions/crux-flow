"""The governs rule-length advisory: it warns, and it never does anything else.

WHY THIS EXISTS. A `governs` rule is projected into the summaries resolver and read by downstream
skills, so a rule that has grown into three obligations and a paragraph of rationale is harder to
apply than one that states an obligation. The recommended maximum is a REVIEW THRESHOLD, not a
target and not a limit: exceeding it warns, and nothing else happens.

WHY GRANDFATHERING IS LOAD-BEARING. Measured when the threshold was set: 219 rules in this corpus,
median 207, and 34 already above 768. An ADR body freezes when it leaves Proposed, so those 34
cannot be brought into compliance — without a baseline the advisory would emit 34 rows forever.
That is the noise-that-never-clears failure the project's prospective-cohort convention exists to
prevent, and the baseline is what makes this rule report ZERO at adoption.

WHY THE BASELINE IS AN IDENTITY SET AND NOT A NUMBER. Two sibling rules use `>= N` boundaries. An
ADR numbered BELOW such a boundary but authored after rollout escapes it, and the policy requires a
new ADR to be advised on whatever its number or date. `test_a_new_adr_numbered_below_the_baseline_is_
still_checked` is the test that distinguishes the two designs.

EVERY FIXTURE IS BUILT HERE. These run under the staged `unittest` gate where the documentation tree
does not exist, so a test reading a live tree path would skip exactly where it most needs to run.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
GATE = SCRIPTS / "check-governs-coverage.py"


def _load():
    spec = importlib.util.spec_from_file_location("_rule_len_probe", SCRIPTS / "summaries_projection.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_rule_len_probe", module)
    spec.loader.exec_module(module)
    return module


SP = _load()
MAX = SP.RULE_LENGTH_RECOMMENDED_MAX

ADR = """---
id: {aid}
title: "Fixture"
status: Accepted
date: 2026-09-17
proposed_date: 2026-09-17
accepted_date: 2026-09-17
deprecated_date: null
superseded_date: null
supersedes: []
superseded_by: null
deciders: [fixture]
tags: [fixture]
related_briefs: []
related_research: []
governs:
{entries}---

# {aid}
"""


def entry(slug: str, rule_yaml: str) -> str:
    return (f"  - domain: fixture\n"
            f"    rule: {rule_yaml}\n"
            f"    scope: \"the fixture\"\n"
            f"    handle: {slug}\n"
            f"    provenance: authored\n")


class Tree:
    def __init__(self, root: Path):
        self.root = root
        self.adrs = root / "tree" / "adrs"
        self.adrs.mkdir(parents=True)
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: tree\n', encoding="utf-8")
        self.baseline: list[str] | None = []

    def adr(self, aid: str, entries: str) -> None:
        (self.adrs / f"{aid}-fixture.md").write_text(
            ADR.format(aid=aid, entries=entries), encoding="utf-8")

    def manifest(self) -> dict:
        adr: dict = {"next_number": 9999}
        if self.baseline is not None:
            adr["governs_rule_baseline"] = list(self.baseline)
        return {"schema_version": "5", "adr": adr}

    def rows(self) -> list[dict]:
        return SP.rule_length_advisories(self.adrs, self.manifest())


def _tree(case: unittest.TestCase) -> Tree:
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    return Tree(Path(tmp.name))


class ThresholdTests(unittest.TestCase):
    """The boundary is exact, and it is measured on the parsed string."""

    def test_exactly_the_maximum_produces_no_warning(self):
        t = _tree(self)
        t.adr("ADR-0001", entry("ADR-0001/at-max", '"' + "a" * MAX + '"'))
        self.assertEqual(t.rows(), [])

    def test_one_over_the_maximum_produces_one_warning(self):
        """The paired control for the test above: one character longer, same fixture."""
        t = _tree(self)
        t.adr("ADR-0001", entry("ADR-0001/over-max", '"' + "a" * (MAX + 1) + '"'))
        rows = t.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["length"], MAX + 1)
        self.assertEqual(rows[0]["recommended_max"], MAX)
        self.assertEqual(rows[0]["handle"], "ADR-0001/over-max")

    def test_the_warning_names_the_path_handle_length_and_maximum(self):
        t = _tree(self)
        t.adr("ADR-0001", entry("ADR-0001/over", '"' + "b" * (MAX + 40) + '"'))
        row = t.rows()[0]
        self.assertTrue(row["file"].endswith("ADR-0001-fixture.md"))
        self.assertEqual(row["handle"], "ADR-0001/over")
        self.assertIn(str(MAX + 40), row["error"])
        self.assertIn(str(MAX), row["error"])
        self.assertIn("rationale and examples into the body", row["error"])

    def test_the_rule_text_is_never_echoed(self):
        """A diagnostic carries no file content — the sibling linter's stated discipline."""
        secret = "QUITEDISTINCTIVETOKEN"
        t = _tree(self)
        t.adr("ADR-0001", entry("ADR-0001/over", '"' + secret + "c" * (MAX + 1) + '"'))
        self.assertNotIn(secret, json.dumps(t.rows()))

    def test_each_overlong_rule_gets_its_own_row(self):
        t = _tree(self)
        t.adr("ADR-0001",
              entry("ADR-0001/one", '"' + "a" * (MAX + 1) + '"')
              + entry("ADR-0001/two", '"' + "b" * (MAX + 2) + '"')
              + entry("ADR-0001/short", '"a short rule"'))
        rows = t.rows()
        self.assertEqual([r["handle"] for r in rows], ["ADR-0001/one", "ADR-0001/two"])


class YamlStyleTests(unittest.TestCase):
    """Every style is measured by its RESULTING STRING VALUE, never by its source bytes.

    Four styles are live in this corpus; a block literal is not, which is exactly why trimming is
    still required — a literal retains its trailing newline and would otherwise measure one long.
    """

    def _one_style(self, rule_yaml: str) -> list[dict]:
        t = _tree(self)
        t.adr("ADR-0001", entry("ADR-0001/styled", rule_yaml))
        return t.rows()

    def test_a_plain_scalar_at_the_maximum_does_not_warn(self):
        self.assertEqual(self._one_style("a" * MAX), [])

    def test_a_double_quoted_scalar_one_over_warns(self):
        self.assertEqual(len(self._one_style('"' + "a" * (MAX + 1) + '"')), 1)

    def test_a_single_quoted_scalar_one_over_warns(self):
        self.assertEqual(len(self._one_style("'" + "a" * (MAX + 1) + "'")), 1)

    def test_a_folded_scalar_is_measured_after_folding(self):
        """`>-` folds newlines to spaces, and the fixture must SIT BETWEEN the two readings.

        150 lines of `word`: 1653 characters of source text, 749 once folded. A source-length
        implementation warns here and a parsed one must not. An earlier version used 60 lines —
        663 source, 299 folded — which is under the maximum BOTH ways, so it would have passed
        against an implementation measuring the raw block.
        """
        folded = ">-\n" + "".join("      word\n" for _ in range(150))
        self.assertGreater(len(folded), MAX, "the fixture must be over the maximum as source text")
        rows = self._one_style(folded)
        self.assertEqual(rows, [], "a folded scalar must be measured after folding")

    def test_a_folded_scalar_over_the_maximum_still_warns(self):
        """The paired control: the same style, genuinely long once folded."""
        folded = ">-\n" + "".join("      " + "x" * 60 + "\n" for _ in range(20))
        rows = self._one_style(folded)
        self.assertEqual(len(rows), 1)

    def test_a_block_literal_trailing_newline_is_trimmed(self):
        """A literal block keeps its trailing newline. At exactly the maximum plus that
        newline, an untrimmed reading would warn and a trimmed one must not."""
        literal = "|\n" + "      " + "a" * MAX + "\n"
        self.assertEqual(self._one_style(literal), [],
                         "the trailing newline of a literal block must be trimmed away")


class UnicodeTests(unittest.TestCase):
    """Code points, not bytes — and not grapheme clusters either.

    Measured on the live corpus: 41 of 219 rules carry non-ASCII, and ZERO would change verdict
    between the two counts. So this property cannot be demonstrated against the tree and is
    demonstrated here instead, on a rule constructed to sit between the two readings.
    """

    def test_a_rule_under_the_maximum_in_characters_but_over_it_in_bytes_does_not_warn(self):
        # Em dashes are three UTF-8 bytes each. 700 of them plus 60 ASCII is 760 code points
        # and 2160 bytes: under the maximum by the policy's unit, far over it by the wrong one.
        rule = "—" * 700 + "a" * 60
        self.assertEqual(len(rule), 760)
        self.assertGreater(len(rule.encode("utf-8")), MAX)
        t = _tree(self)
        t.adr("ADR-0001", entry("ADR-0001/unicode", '"' + rule + '"'))
        self.assertEqual(t.rows(), [], "a byte count would have warned here; a code-point count must not")

    def test_the_same_rule_one_code_point_over_does_warn(self):
        """The paired control, so the test above cannot pass by measuring nothing."""
        rule = "—" * 700 + "a" * (MAX + 1 - 700)
        self.assertEqual(len(rule), MAX + 1)
        t = _tree(self)
        t.adr("ADR-0001", entry("ADR-0001/unicode", '"' + rule + '"'))
        self.assertEqual(len(t.rows()), 1)

    def test_a_composed_character_is_not_normalised_away(self):
        """`e` + combining acute is TWO code points and must count as two. Normalising would
        fold it to one and silently change the verdict, so normalisation is not applied."""
        rule = "é" * 400 + "a" * 1   # 801 code points
        self.assertEqual(len(rule), 801)
        t = _tree(self)
        t.adr("ADR-0001", entry("ADR-0001/composed", '"' + rule + '"'))
        self.assertEqual(len(t.rows()), 1)


class GrandfatheringTests(unittest.TestCase):
    """Exemption is by IDENTITY, never by date or number."""

    def test_an_adr_in_the_baseline_is_exempt(self):
        t = _tree(self)
        t.baseline = ["ADR-0001"]
        t.adr("ADR-0001", entry("ADR-0001/over", '"' + "a" * (MAX + 500) + '"'))
        self.assertEqual(t.rows(), [])

    def test_the_same_adr_absent_from_the_baseline_is_checked(self):
        """The paired control: identical fixture, one id removed from the baseline."""
        t = _tree(self)
        t.baseline = []
        t.adr("ADR-0001", entry("ADR-0001/over", '"' + "a" * (MAX + 500) + '"'))
        self.assertEqual(len(t.rows()), 1)

    def test_an_edit_to_a_baseline_adr_stays_exempt(self):
        """'Subsequent edits remain exempt' — the exemption keys on identity, and nothing about
        the file's content or mtime enters the decision."""
        t = _tree(self)
        t.baseline = ["ADR-0001"]
        t.adr("ADR-0001", entry("ADR-0001/over", '"' + "a" * (MAX + 1) + '"'))
        self.assertEqual(t.rows(), [])
        t.adr("ADR-0001", entry("ADR-0001/over", '"' + "a" * (MAX + 4000) + '"'))
        self.assertEqual(t.rows(), [], "an edit to a grandfathered ADR must stay exempt")

    def test_a_new_adr_numbered_below_the_baseline_is_still_checked(self):
        """THE TEST THAT DISTINGUISHES AN IDENTITY SET FROM A NUMERIC BOUNDARY.

        The baseline holds a high-numbered ADR; a LOW-numbered one is added afterwards. Under a
        `>= N` cohort the new ADR would fall below the boundary and escape. Under an identity set
        it is simply absent from the baseline, and is checked.
        """
        t = _tree(self)
        t.baseline = ["ADR-0900"]
        t.adr("ADR-0900", entry("ADR-0900/old", '"' + "a" * (MAX + 1) + '"'))
        t.adr("ADR-0002", entry("ADR-0002/new", '"' + "b" * (MAX + 1) + '"'))
        rows = t.rows()
        self.assertEqual([r["handle"] for r in rows], ["ADR-0002/new"])

    def test_a_new_adr_is_checked_whatever_its_date(self):
        """Dates are not read at all; this pins that by dating the new ADR in the past."""
        t = _tree(self)
        t.baseline = ["ADR-0900"]
        text = ADR.format(aid="ADR-0002", entries=entry("ADR-0002/new", '"' + "a" * (MAX + 1) + '"'))
        (t.adrs / "ADR-0002-fixture.md").write_text(
            text.replace("2026-09-17", "1999-01-01"), encoding="utf-8")
        self.assertEqual(len(t.rows()), 1)

    def test_an_absent_baseline_key_leaves_the_advisory_inert(self):
        t = _tree(self)
        t.baseline = None
        t.adr("ADR-0001", entry("ADR-0001/over", '"' + "a" * (MAX + 500) + '"'))
        self.assertEqual(t.rows(), [])

    def test_an_empty_baseline_checks_everything(self):
        """The shipped template's value. Distinct from absent, and the distinction is the point:
        a new tree with no ADRs gets the advisory switched on and self-maintaining."""
        t = _tree(self)
        t.baseline = []
        t.adr("ADR-0001", entry("ADR-0001/over", '"' + "a" * (MAX + 1) + '"'))
        self.assertEqual(len(t.rows()), 1)


class ToleranceTests(unittest.TestCase):
    """The advisory neither introduces a failure nor suppresses one."""

    def test_a_non_string_rule_contributes_no_row_and_no_error(self):
        """The value must be LONG, or the test cannot fail.

        An earlier version used `rule: 12345` — five characters — so an implementation that
        coerced with `str()` before measuring would have produced no row either and passed. An
        800-digit integer is over the maximum once coerced, so only an implementation that
        refuses a non-string stays silent here.
        """
        big = "9" * 800
        self.assertGreater(len(big), MAX)
        t = _tree(self)
        t.adr("ADR-0001",
              f"  - domain: fixture\n    rule: {big}\n    scope: \"s\"\n"
              "    handle: ADR-0001/numeric\n    provenance: authored\n")
        self.assertEqual(t.rows(), [], "a coercing implementation would have warned here")

    def test_a_list_valued_rule_contributes_no_row_and_no_error(self):
        t = _tree(self)
        t.adr("ADR-0001",
              "  - domain: fixture\n    rule: [" + ", ".join(['"x" ' * 1] * 300) + "]\n"
              "    scope: \"s\"\n    handle: ADR-0001/list\n    provenance: authored\n")
        self.assertEqual(t.rows(), [])

    def test_an_unparseable_frontmatter_contributes_no_row_and_no_error(self):
        t = _tree(self)
        (t.adrs / "ADR-0001-broken.md").write_text(
            "---\nid: ADR-0001\n\tbad: indent\ngoverns: [\n---\n\n# broken\n", encoding="utf-8")
        self.assertEqual(t.rows(), [], "a malformed ADR is the existing validators' business")

    def test_a_malformed_adr_beside_a_good_one_does_not_hide_the_good_one(self):
        """Tolerance is per file, not a bail-out: one bad file must not silence the corpus."""
        t = _tree(self)
        (t.adrs / "ADR-0001-broken.md").write_text("---\ngoverns: [\n---\n", encoding="utf-8")
        t.adr("ADR-0002", entry("ADR-0002/over", '"' + "a" * (MAX + 1) + '"'))
        self.assertEqual([r["handle"] for r in t.rows()], ["ADR-0002/over"])

    def test_an_absent_adrs_directory_yields_no_rows_and_no_error(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(
            SP.rule_length_advisories(Path(tmp.name) / "nope", {"adr": {"governs_rule_baseline": []}}),
            [])


class GateIntegrationTests(unittest.TestCase):
    """The advisory rides the gate's verdict and never changes its exit code."""

    def _run(self, root: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(GATE), "--repo-root", str(root)],
                              capture_output=True, text=True)

    def _fixture(self, baseline, rule_len: int) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        t = Tree(root)
        t.adr("ADR-0001", entry("ADR-0001/r", '"' + "a" * rule_len + '"'))
        man = "schema_version: \"5\"\nconcerns_enabled: [adrs]\nadr:\n  next_number: 2\n"
        if baseline is not None:
            man += f"  governs_rule_baseline: [{', '.join(baseline)}]\n"
        (root / "tree" / "manifest.yml").write_text(man, encoding="utf-8")
        return root

    def test_a_length_warning_leaves_the_exit_code_successful(self):
        root = self._fixture([], MAX + 1)
        r = self._run(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(len(payload["warnings"]), 1)
        self.assertEqual(payload["uncovered"], [])

    def test_no_warning_when_nothing_is_over(self):
        """The paired control: same fixture, a rule one character shorter."""
        root = self._fixture([], MAX)
        r = self._run(root)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("warnings", json.loads(r.stdout))

    def test_an_absent_baseline_is_reported_rather_than_silently_inert(self):
        root = self._fixture(None, MAX + 1)
        r = self._run(root)
        self.assertEqual(r.returncode, 0)
        rows = json.loads(r.stdout)["warnings"]
        self.assertEqual(len(rows), 1)
        self.assertIn("governs_rule_baseline", rows[0]["error"])
        self.assertIn("inert", rows[0]["error"])

    def test_an_existing_failure_still_fails_and_carries_the_advisory_beside_it(self):
        """The advisory downgrades nothing, and rides a FAILING verdict too.

        The gate's own failing lane is an uncovered ADR. This fixture has one of those AND an
        over-length rule on a second ADR, so it pins both halves at once: the exit code is still
        1 and still explained by the coverage failure, and the warning is readable beside it.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        t = Tree(root)
        # An ADR in the cohort carrying no governs block at all: the gate's existing failure.
        (t.adrs / "ADR-0002-uncovered.md").write_text(
            ADR.format(aid="ADR-0002", entries="").replace("governs:\n", ""), encoding="utf-8")
        # A second ADR whose rule is over length: the advisory.
        t.adr("ADR-0003", entry("ADR-0003/over", '"' + "a" * (MAX + 1) + '"'))
        (root / "tree" / "manifest.yml").write_text(
            "schema_version: \"5\"\nconcerns_enabled: [adrs]\nadr:\n"
            "  next_number: 9\n  governs_from: 1\n  governs_rule_baseline: []\n",
            encoding="utf-8")
        r = self._run(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["uncovered"], ["ADR-0002"],
                         "the coverage failure must still be what explains the exit code")
        self.assertEqual(len(payload["warnings"]), 1)
        self.assertEqual(payload["warnings"][0]["handle"], "ADR-0003/over")


class DocumentedContractTests(unittest.TestCase):
    """The gate's own docstring names the `warnings` key it emits.

    The advisory added a top-level output key to BOTH payloads while the module docstring
    still described the old two. A caller reading that contract to decide what it may see
    would not know `warnings` exists, and a reader keying off it as a failure would be wrong
    about the exit code. The docstring IS the published contract for a shipped script, so it
    is pinned here rather than left to review.
    """

    def _docstring(self) -> str:
        """The docstring with runs of whitespace collapsed.

        The contract is hard-wrapped prose, so a phrase it states can straddle a newline.
        Matching against the raw text would pin the WRAPPING rather than the statement, and
        a reflow that changed no words would fail.
        """
        import ast
        doc = ast.get_docstring(ast.parse(GATE.read_text(encoding="utf-8")))
        self.assertIsNotNone(doc, "the gate must carry a module docstring")
        return " ".join(doc.split())

    @staticmethod
    def _documents(doc: str, key: str) -> bool:
        """A key is documented if the contract names it as a token, however it is quoted.

        Backticks are the prose form; the JSON example quotes its keys instead. Both are the
        contract naming the key, so both count — and a bare substring is not enough, or
        `backfill` would spuriously satisfy `backfill_errors`.
        """
        return f"`{key}`" in doc or f'"{key}"' in doc

    def test_the_docstring_names_the_warnings_key(self):
        self.assertIn("`warnings`", self._docstring(),
                      "the emitted key is absent from the documented output contract")

    def test_the_docstring_says_the_advisory_does_not_change_the_exit_code(self):
        doc = self._docstring().lower()
        self.assertIn("never changes the exit code", doc,
                      "the contract must state that a warning is not a failure")
        self.assertIn("must not treat its presence as a failure", doc)

    def test_every_top_level_key_the_gate_emits_is_documented(self):
        """A positive control over the real payloads, not a keyword scan.

        Drives the gate twice — a clean tree carrying a warning, and the absent-baseline
        lane — and asserts each top-level key it actually prints appears in the docstring.
        A key added later without a contract line fails here.
        """
        doc = self._docstring()
        seen: set[str] = set()
        for baseline in ([], None):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            root = Path(tmp.name)
            t = Tree(root)
            t.adr("ADR-0001", entry("ADR-0001/r", '"' + "a" * (MAX + 1) + '"'))
            man = "schema_version: \"5\"\nconcerns_enabled: [adrs]\nadr:\n  next_number: 2\n"
            if baseline is not None:
                man += "  governs_rule_baseline: []\n"
            (root / "tree" / "manifest.yml").write_text(man, encoding="utf-8")
            r = subprocess.run([sys.executable, str(GATE), "--repo-root", str(root)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            seen.update(json.loads(r.stdout).keys())
        self.assertIn("warnings", seen, "the fixture must actually produce a warning")
        undocumented = [k for k in sorted(seen) if not self._documents(doc, k)]
        self.assertEqual(undocumented, [],
                         f"the gate emits top-level keys its docstring never names: {undocumented}")


if __name__ == "__main__":
    unittest.main()
