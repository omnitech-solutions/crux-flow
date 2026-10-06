"""The prose, schema and template-twin consumers of the rule:<slug> citation form.

Pins four surfaces to the decision they implement
(rule:footnote-definition-names-the-rule, rule:rule-slug-citation-token):

  1. canonical writing rule 7 in the repo-root AGENTS.md — footnote form kept,
     the definition names the rule and carries no ADR number;
  2. the prose-review skill — a seventh check beside the six, and every count
     word in its body agrees with the number of rules the generated region
     carries; the tree schema's §16 preamble is held to the same count;
  3. the §9 code-citation addendum in the tree's operational schema and its
     shipped template twin, in lock-step, plus the parity-manifest clause that
     binds the pair;
  4. every `§N` cross-reference in the shipped twin resolves to a heading the
     twin has.

Every expectation is derived from disk — no rule count, slug or line number is
a literal here. Dev-only surfaces (the repo-root AGENTS.md, the tree's
AGENTS.md, the summaries resolver) are guarded with `require_dev_surface` so
the file is inert against the staged sync artifact; the skill, the twin and the
parity manifest ship and are read unguarded.
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "crux" / "scripts"

try:  # package-relative when run as a module, flat when run by discovery
    from ._dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

ROOT_AGENTS_MD = REPO_ROOT / "AGENTS.md"
SKILL_MD = REPO_ROOT / "crux" / "skills" / "prose-review" / "SKILL.md"
TWIN = REPO_ROOT / "crux" / "templates" / "AGENTS.md.tmpl"
PARITY_MANIFEST = SCRIPTS / "template_parity_manifest.json"
RESOLVER = REPO_ROOT / TREE / "adrs" / "summaries" / "resolver.json"

CANONICAL_BEGIN = "<!-- BEGIN CANONICAL: writing-rules -->"
CANONICAL_END = "<!-- END CANONICAL: writing-rules -->"
GENERATED_BEGIN = "<!-- BEGIN GENERATED: writing-rules -->"
GENERATED_END = "<!-- END GENERATED: writing-rules -->"

SECTION_9_ANCHOR = "## 9. Slug, date, and wiki-link rules"
SECTION_16_ANCHOR = "## 16. Writing rules"
ADDENDUM_LEAD = "**Rule citations**:"
PARITY_CLAUSE_ID = "rule-citation-form"

_RULE_HEAD = re.compile(r"^\*\*(\d+)\. ", re.MULTILINE)
_CHECK_HEAD = re.compile(r"^### (\d+)\. ", re.MULTILINE)
_ADR_NUMBER = re.compile(r"ADR-\d{4}")
_RULE_TOKEN = re.compile(r"rule:([a-z][a-z0-9-]*)")
_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}


def _region(text: str, begin: str, end: str) -> str:
    i = text.index(begin) + len(begin)
    j = text.index(end, i)
    return text[i:j]


def _rule_paragraph(block: str, n: int) -> str:
    """The text of rule `n` in a writing-rules block, up to the next rule or the coda."""
    heads = list(_RULE_HEAD.finditer(block))
    for k, m in enumerate(heads):
        if int(m.group(1)) == n:
            stop = heads[k + 1].start() if k + 1 < len(heads) else block.index("When in doubt")
            return block[m.start():stop]
    raise AssertionError(f"rule {n} not found in the writing-rules block")


def _section(text: str, anchor: str) -> str:
    """From the anchor heading up to the next `## ` heading (the parity checker's cut)."""
    start = text.index(anchor)
    m = re.compile(r"^## ", re.MULTILINE).search(text, start + len(anchor))
    return text[start:m.start()] if m else text[start:]


def _headings(text: str) -> set[str]:
    """Numbered headings at any depth: `## 11.D. Title` and `### 14.1 Title` alike."""
    return set(re.findall(r"^#{2,4} ([0-9]+(?:\.[A-Z0-9]+)*)\.? ", text, re.MULTILINE))


def _section_refs(text: str) -> list[str]:
    return [r.rstrip(".") for r in re.findall(r"§([0-9]+(?:\.[A-Z0-9]+)*)", text)]


def _assert_no_stale_count_word(
    tc: unittest.TestCase, text: str, n: int, label: str
) -> None:
    """No number word but `n`'s qualifies the rules anywhere in `text`.

    `writing rules` is in the alternation because that is the exact shape the
    six/seven contradiction hid in: `six writing rules` slips past a pattern
    that only looks for `<word> rules`.
    """
    for k, w in _NUMBER_WORDS.items():
        if k == n:
            continue
        tc.assertNotRegex(
            text,
            rf"(?i)\b{w} (writing rules|rules|checks)\b",
            f"{label}: stale count word {w!r} (the block on disk carries {n} rules)",
        )


class CanonicalRuleSevenTests(unittest.TestCase):
    """Task 1 — the canonical rule keeps its form and changes its content."""

    def setUp(self) -> None:
        require_dev_surface(self, ROOT_AGENTS_MD, "repo-root AGENTS.md")
        require_dev_surface(self, RESOLVER, f"{TREE}/adrs/summaries/resolver.json")
        block = _region(ROOT_AGENTS_MD.read_text(encoding="utf-8"), CANONICAL_BEGIN, CANONICAL_END)
        self.n_rules = len(_RULE_HEAD.findall(block))
        self.rule = _rule_paragraph(block, self.n_rules)
        self.live_slugs = json.loads(RESOLVER.read_text(encoding="utf-8"))["slugs"]

    def test_the_last_rule_is_the_footnote_rule(self) -> None:
        self.assertIn("footnote", self.rule)

    def test_definition_names_the_rule_and_carries_no_adr_number(self) -> None:
        self.assertIn("`rule:<slug>`", self.rule)
        self.assertIn("carries no ADR number", self.rule)

    def test_marker_stays_free_text(self) -> None:
        self.assertRegex(self.rule, r"marker[^.\n]*free text")

    def test_both_carve_outs_stand_with_the_journal_fallback(self) -> None:
        self.assertIn("ADR bodies", self.rule)
        self.assertIn("journal", self.rule)
        self.assertIn("`ADR-NNNN`", self.rule, "the journal cites ADR-NNNN where no rule exists")
        self.assertIn("governs block", self.rule)

    def test_but_example_cites_a_live_slug_and_no_adr_number(self) -> None:
        but = next(line for line in self.rule.splitlines() if line.startswith("> But:"))
        not_ = next(line for line in self.rule.splitlines() if line.startswith("> Not:"))
        self.assertNotRegex(but, _ADR_NUMBER)
        slugs = _RULE_TOKEN.findall(but)
        self.assertTrue(slugs, "the But: example must show a rule:<slug> definition end-to-end")
        for slug in slugs:
            self.assertIn(slug, self.live_slugs, f"rule:{slug} does not resolve against the live corpus")
        # positive control: the Not: example still shows an inline ADR reference
        self.assertRegex(not_, r"\bADR\b")

    def test_but_example_definition_passes_the_footnote_check(self) -> None:
        C = importlib.import_module("crux.prose.adr_footnote_check")
        but = next(line for line in self.rule.splitlines() if line.startswith("> But:"))
        definition = re.search(r"`(\[\^[^\]]+\]: [^`]+)`", but)
        self.assertIsNotNone(definition, "the But: example must show the definition line verbatim")
        self.assertEqual(C.find_inline_adr_refs(definition.group(1)), [])
        # positive control: the displaced form of the same definition is a finding
        old = definition.group(1).split(":", 1)[0] + ": [[adrs/ADR-0064-x]]"
        self.assertEqual([m for _, m in C.find_inline_adr_refs(old)], ["ADR-0064"])


class ProseReviewSkillTests(unittest.TestCase):
    """Task 2 — the skill carries one check per rule and says so consistently."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SKILL_MD.read_text(encoding="utf-8")
        cls.rules = _region(cls.text, GENERATED_BEGIN, GENERATED_END)
        cls.body = cls.text.replace(cls.rules, "")
        cls.n = len(_RULE_HEAD.findall(cls.rules))
        cls.word = _NUMBER_WORDS[cls.n]

    def test_one_check_section_per_rule(self) -> None:
        self.assertEqual(
            [int(x) for x in _CHECK_HEAD.findall(self.body)], list(range(1, self.n + 1))
        )

    def test_every_count_word_in_the_body_matches_the_rule_count(self) -> None:
        expected = [
            f"Review text against {self.word} rules",
            f"## The {self.word} rules",
            f"## The {self.word} checks",
            f"Accuracy beats all {self.word} rules",
            f"apply rules 2 through {self.n}",
        ]
        for phrase in expected:
            self.assertIn(phrase, self.body, phrase)
        # no stale count word survives beside the right one
        _assert_no_stale_count_word(self, self.body, self.n, "prose-review SKILL.md")

    def test_every_count_word_in_the_tree_schema_preamble_matches_the_rule_count(self) -> None:
        """The same property one file away — §16's preamble, where the stale six survived.

        The count is read off the canonical block on disk, never spelled here: a
        literal seven is the frozen-world value this whole surface keeps tripping on.
        """
        require_dev_surface(self, ROOT_AGENTS_MD, "repo-root AGENTS.md")
        require_dev_surface(self, TREE_AGENTS_MD, f"{TREE}/AGENTS.md")
        canonical = _region(
            ROOT_AGENTS_MD.read_text(encoding="utf-8"), CANONICAL_BEGIN, CANONICAL_END
        )
        n = len(_RULE_HEAD.findall(canonical))
        word = _NUMBER_WORDS[n]
        text = TREE_AGENTS_MD.read_text(encoding="utf-8")
        start = text.index(SECTION_16_ANCHOR)
        # the preamble only — the generated region below it is the regenerator's to own
        preamble = text[start:text.index(GENERATED_BEGIN, start)]
        self.assertIn(
            f"{word} writing rules",
            preamble.lower(),
            f"§16's preamble must state the {n} rules the canonical block carries",
        )
        _assert_no_stale_count_word(self, preamble, n, f"{TREE}/AGENTS.md §16 preamble")

    def test_last_check_is_the_footnote_definition_check(self) -> None:
        heads = list(_CHECK_HEAD.finditer(self.body))
        last = self.body[heads[-1].start():self.body.index("## Precedence")]
        self.assertIn("footnote definition", last)
        self.assertIn("`rule:<slug>`", last)
        self.assertIn("running text", last)
        for part in ("**Test.**", "**Don't flag.**", "**Rewrite.**"):
            self.assertIn(part, last, f"check {self.n} lacks the {part} part its siblings carry")

    def test_shipped_skill_carries_no_adr_number(self) -> None:
        self.assertNotRegex(self.text, _ADR_NUMBER)
        # positive control: the placeholder spelling is present and is not a number
        self.assertIn("ADR-NNNN", self.text)


class SectionNineAddendumTests(unittest.TestCase):
    """Task 4 — the code-citation form, stated in §9 of both files in lock-step."""

    def _addendum(self, text: str, label: str) -> str:
        sec = _section(text, SECTION_9_ANCHOR)
        lines = [ln for ln in sec.splitlines() if ADDENDUM_LEAD in ln]
        self.assertEqual(len(lines), 1, f"{label}: expected exactly one addendum line in §9")
        return lines[0].strip()

    def test_twin_addendum_states_the_form(self) -> None:
        line = self._addendum(TWIN.read_text(encoding="utf-8"), "twin")
        self.assertIn("`rule:<slug>`", line)
        self.assertIn("after the slash", line)
        for surface in ("code comments", "docstrings", "prose", "promptbook prompts",
                        "run-snapshot artifacts", "journal refs"):
            self.assertIn(surface, line)
        self.assertIn("lowercase letter followed by lowercase letters, digits", line)
        self.assertIn("first character outside that set", line)
        self.assertIn("`ADR-NNNN`", line)
        self.assertIn("`ADR-NNNN/<slug>`", line)
        self.assertIn("`OBS-NNNN/<slug>`", line)
        self.assertIn("must resolve", line)
        self.assertIn("mapping key", line)
        self.assertIn("block scalar", line)
        self.assertIn("`rule:<new-thing>`", line)
        self.assertIn("non-token", line)
        # the twin ships: the placeholder is the only ADR spelling it may carry
        self.assertNotRegex(line, _ADR_NUMBER)
        self.assertNotIn("[[adrs/", line)

    def test_canonical_addendum_is_byte_identical_to_the_twin(self) -> None:
        require_dev_surface(self, TREE_AGENTS_MD, f"{TREE}/AGENTS.md")
        canon = self._addendum(TREE_AGENTS_MD.read_text(encoding="utf-8"), "canonical")
        twin = self._addendum(TWIN.read_text(encoding="utf-8"), "twin")
        self.assertEqual(canon, twin)

    def test_addendum_placeholders_are_non_tokens(self) -> None:
        # every `rule:` spelling on the line is a placeholder in angle brackets,
        # so the governs linter has nothing to resolve there.
        line = self._addendum(TWIN.read_text(encoding="utf-8"), "twin")
        self.assertEqual(_RULE_TOKEN.findall(line), [])
        self.assertGreaterEqual(line.count("rule:<"), 2)


class TwinSectionReferencesTests(unittest.TestCase):
    """Task 5 — a `§N` in the shipped twin names a section the twin has."""

    def test_helper_catches_a_dangling_reference(self) -> None:
        text = "## 3. Layout\n\n### 3.1 Sub\n\nSee §16's rules, §3 and §3.1.\n"
        self.assertEqual(sorted(set(_section_refs(text)) - _headings(text)), ["16"])

    def test_every_section_reference_in_the_twin_resolves(self) -> None:
        text = TWIN.read_text(encoding="utf-8")
        refs, heads = _section_refs(text), _headings(text)
        self.assertTrue(refs, "the twin carries no § cross-references at all")
        self.assertEqual(sorted(set(refs) - heads), [])

    def test_the_writing_rules_pointer_names_the_shipped_skill(self) -> None:
        text = TWIN.read_text(encoding="utf-8")
        sec = _section(text, "## 11.D.")
        self.assertIn("`prose-review`", sec)
        self.assertIn("writing rules", sec)


class ParityManifestClauseTests(unittest.TestCase):
    """Task 6 — one clause binds the §9 addendum across the twin pair."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.clauses = json.loads(PARITY_MANIFEST.read_text(encoding="utf-8"))["clauses"]
        cls.ctp = importlib.import_module("check_template_parity")

    def _clause(self, cid: str) -> dict:
        for c in self.clauses:
            if c.get("id") == cid:
                return c
        self.fail(f"no clause {cid!r} in {PARITY_MANIFEST}")

    def test_clause_mirrors_the_sibling_section_nine_clause(self) -> None:
        sibling = self._clause("wikilink-promptbook-lifecycle-neutral")
        clause = self._clause(PARITY_CLAUSE_ID)
        self.assertEqual(clause["canonical"], sibling["canonical"])
        self.assertEqual(clause["twin"], sibling["twin"])
        self.assertIn("Slug, date, and wiki-link rules", clause["anchor"])
        self.assertIn("pattern", clause)

    def test_clause_is_live_and_in_parity_against_the_real_repo(self) -> None:
        require_dev_surface(self, TREE_AGENTS_MD, f"{TREE}/AGENTS.md")
        results = {r["id"]: r for r in self.ctp.check_parity(PARITY_MANIFEST, REPO_ROOT)}
        self.assertIn(PARITY_CLAUSE_ID, results)
        self.assertEqual(results[PARITY_CLAUSE_ID]["status"], "OK", results[PARITY_CLAUSE_ID])

    def test_clause_reports_drift_when_the_twin_addendum_changes(self) -> None:
        require_dev_surface(self, TREE_AGENTS_MD, f"{TREE}/AGENTS.md")
        clause = self._clause(PARITY_CLAUSE_ID)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel in (clause["canonical"], clause["twin"]):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO_ROOT / rel, root / rel)
            twin = root / clause["twin"]
            text = twin.read_text(encoding="utf-8")
            self.assertIn("`rule:<slug>`", text)
            twin.write_text(text.replace("`rule:<slug>`", "`rule:<slgu>`", 1), encoding="utf-8")
            results = {r["id"]: r for r in self.ctp.check_parity(PARITY_MANIFEST, root)}
            self.assertEqual(results[PARITY_CLAUSE_ID]["status"], "DRIFT", results[PARITY_CLAUSE_ID])


if __name__ == "__main__":
    unittest.main()
