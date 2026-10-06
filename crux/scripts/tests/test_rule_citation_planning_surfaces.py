"""The planning surfaces cite rules as `rule:<slug>` (the rule-citation decision, clause 5).

Five surfaces change together and are pinned here as one unit:

  1. The cycle dev module's substitution token is `{RULE_REFS}` — one or more
     `rule:<slug>` citations the loop implements — and the old `ADR_REFS`
     brace token is gone from every shipped surface (archived books under
     the tree are frozen records and keep it; they are outside `crux/`).
  2. The ADR module records the new ADR's rule citations in the run's
     Artifacts BESIDE the `ADR-NNNN` id, never instead of it.
  3. The review module's consistency reviewer checks the change against the
     cited rules.
  4. `log-work --refs` accepts a `rule:<slug>` citation beside wiki-links, so
     the checklist line demanding `[[...]]` on every ref is retired.
  5. Whiteboarding reads the doctrine domains before options are drafted, and
     both it and the brief template carry the placeholder authoring rule.

Grammar (clause 1): a citation is `rule:` followed by a lowercase letter then
lowercase letters, digits and hyphens; the token ends at the first character
outside that set. The placeholder form `rule:<new-thing>` is a NON-token because
`<` is not a lowercase letter — that is the whole mechanism by which a brief
may name a rule that does not exist yet without tripping the citation lint.

The linter that enforces this repo-wide lives with the lint group; this file
carries the grammar locally so the planning surfaces are gated even before
that linter lands, and so the resolver check here is derived from disk (the
committed resolver's `slugs` / `retired_slugs` maps) rather than a list.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dev_surface import TREE, require_dev_surface  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
CRUX = REPO_ROOT / "crux"
TEMPLATES = CRUX / "templates"
SKILLS = CRUX / "skills"
RESOLVER = REPO_ROOT / TREE / "adrs" / "summaries" / "resolver.json"

DEV_MODULE = TEMPLATES / "cycle-module-dev.yaml"
VERIFY_MODULE = TEMPLATES / "cycle-module-verify.yaml"
ADR_MODULE = TEMPLATES / "cycle-module-adr.yaml"
REVIEW_MODULE = TEMPLATES / "cycle-module-review.yaml"
ITERATE_TEMPLATE = TEMPLATES / "iterate-promptbook-template.yaml"
BRIEF_TEMPLATE = TEMPLATES / "BRIEF-template.md"
ITERATE_SKILL = SKILLS / "iterate" / "SKILL.md"
DEV_CYCLE_SKILL = SKILLS / "dev-cycle" / "SKILL.md"
LOG_WORK_SKILL = SKILLS / "log-work" / "SKILL.md"
LOG_WORK_JOURNAL = SKILLS / "log-work" / "references" / "journal.md"
WHITEBOARDING_SKILL = SKILLS / "whiteboarding" / "SKILL.md"

#: The surfaces this unit owns. Every citation-grammar assertion below runs
#: over exactly these files.
PLANNING_SURFACES: tuple[Path, ...] = (
    DEV_MODULE,
    VERIFY_MODULE,
    ADR_MODULE,
    REVIEW_MODULE,
    ITERATE_TEMPLATE,
    BRIEF_TEMPLATE,
    ITERATE_SKILL,
    DEV_CYCLE_SKILL,
    LOG_WORK_SKILL,
    WHITEBOARDING_SKILL,
)

#: The surfaces that carried the old brace token and must now carry `{RULE_REFS}`.
#: The verify module and the iterate template refer to the token by name
#: (they explain what `iterate` substitutes it with); the rest use it.
TOKEN_SITES: tuple[Path, ...] = (
    DEV_MODULE,
    VERIFY_MODULE,
    ITERATE_TEMPLATE,
    ITERATE_SKILL,
    DEV_CYCLE_SKILL,
)

# Assembled rather than spelled, so this file is not itself a stale site
# when the whole plugin tree is scanned for the old token below.
OLD_TOKEN = "{" + "ADR_REFS" + "}"
NEW_TOKEN = "{RULE_REFS}"

# Clause-1 grammar. A token is the citation; a placeholder is the authoring
# convention. Everything else after `rule:` is classified by `_classify`.
RULE_TOKEN_RE = re.compile(r"rule:([a-z][a-z0-9-]*)")
PLACEHOLDER_RE = re.compile(r"rule:<([a-z][a-z0-9-]*)>")
RULE_PREFIX_RE = re.compile(r"rule:(.?)")

TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".py", ".json", ".tmpl", ".txt"}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _files_containing(root: Path, needle: str) -> list[Path]:
    """Every text file under `root` whose bytes contain `needle`."""
    hits: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        if needle in p.read_text(encoding="utf-8", errors="replace"):
            hits.append(p)
    return hits


def _classify(text: str) -> dict[str, list[str]]:
    """Bucket every `rule:` occurrence in `text`.

    `token`       — `rule:<slug>` per the clause-1 grammar (must resolve).
    `placeholder` — `rule:<name>` in angle brackets (the authoring rule).
    `mention`     — the bare prefix in prose (`rule:` before a backtick, a
                    space, or end of text), which cites nothing.
    `malformed`   — anything else: an uppercase letter, a digit, an
                    underscore, or a `<` not closed as a placeholder.
    """
    out: dict[str, list[str]] = {
        "token": [], "placeholder": [], "mention": [], "malformed": [],
    }
    for m in RULE_PREFIX_RE.finditer(text):
        nxt = m.group(1)
        at = m.start()
        if nxt and nxt.islower() and nxt.isalpha():
            out["token"].append(RULE_TOKEN_RE.match(text, at).group(1))
        elif nxt == "<":
            pm = PLACEHOLDER_RE.match(text, at)
            if pm:
                out["placeholder"].append(pm.group(1))
            else:
                out["malformed"].append(text[at:at + 40])
        elif nxt in ("", "`", " ", "\n", ")", ","):
            out["mention"].append(text[at:at + 40])
        else:
            out["malformed"].append(text[at:at + 40])
    return out


def _prompt_blocks(yaml_text: str) -> list[str]:
    """Split a cycle-module YAML partial into its `- title:` prompt blocks."""
    parts = re.split(r"(?m)^- title:", yaml_text)
    return [p for p in parts[1:]]


def _section(text: str, start: str, end: str | None) -> str:
    """The slice of `text` from the line containing `start` up to `end`."""
    i = text.index(start)
    if end is None:
        return text[i:]
    j = text.index(end, i + len(start))
    return text[i:j]


class TokenRenameTests(unittest.TestCase):
    """The old token → `{RULE_REFS}`: the dev module's token names rule citations."""

    def test_dev_module_contract_defines_rule_refs_as_citations(self):
        t = _read(DEV_MODULE)
        contract = [ln for ln in t.splitlines() if ln.startswith("# - `" + NEW_TOKEN + "`")]
        self.assertEqual(len(contract), 1, "the dev module's token contract must define {RULE_REFS} once")
        # The contract block runs from that line to the next `# - ` bullet.
        block = _section(t, contract[0], "\n#\n")
        self.assertIn("rule:<slug>", block, "the contract must define the token as rule:<slug> citations")
        self.assertNotIn("ADR-NNNN", block, "the token no longer names ADR ids")

    def test_dev_module_prompt_body_uses_rule_refs(self):
        t = _read(DEV_MODULE)
        body_lines = [ln for ln in t.splitlines() if NEW_TOKEN in ln and not ln.startswith("#")]
        self.assertTrue(body_lines, "the planning prompt must substitute {RULE_REFS} in its body")

    def test_every_token_site_carries_the_new_token(self):
        for p in TOKEN_SITES:
            with self.subTest(site=p.name):
                self.assertIn(NEW_TOKEN, _read(p))

    def test_old_token_is_gone_from_the_shipped_tree(self):
        # Positive control first: the scanner sees the old token when present.
        with tempfile.TemporaryDirectory() as d:
            fx = Path(d) / "fixture.yaml"
            fx.write_text(f"prompt: read {OLD_TOKEN}\n", encoding="utf-8")
            self.assertEqual(_files_containing(Path(d), OLD_TOKEN), [fx])
        stale = _files_containing(CRUX, OLD_TOKEN)
        self.assertEqual(
            stale, [],
            f"{OLD_TOKEN} still present under crux/: {[str(p.relative_to(REPO_ROOT)) for p in stale]}",
        )
        renamed = _files_containing(CRUX, NEW_TOKEN)
        self.assertTrue(set(TOKEN_SITES) <= set(renamed), "every token site must carry {RULE_REFS}")

    def test_iterate_keeps_its_literal_substitution_warning_under_the_new_name(self):
        t = _read(ITERATE_SKILL)
        self.assertIn(f"**Substituting `{NEW_TOKEN}` literally**", t)
        # The substitution's MEANING is unchanged: the verified Diagnosis + the
        # commit-approach journal entry is what an iterate cycle substitutes.
        self.assertIn("the verified Diagnosis", t)
        self.assertIn("commit-approach journal entry", t)

    def test_dev_cycle_states_the_yaml_mapping_key_caveat(self):
        """Clause 7(d): a `rule:` token opening a YAML line is a mapping key;
        inside a block scalar it is text. The assembler must know that."""
        t = _read(DEV_CYCLE_SKILL)
        self.assertRegex(t, r"mapping key")
        self.assertRegex(t, r"block scalar")


class AdrModuleTests(unittest.TestCase):
    """Clause 5: the ADR module records the rule citations beside the id."""

    def test_propose_prompt_records_rule_citations_beside_the_id(self):
        first = _prompt_blocks(_read(ADR_MODULE))[0]
        self.assertIn("Artifacts", first)
        self.assertIn("ADR-NNNN", first, "the id stays — beside, not instead")
        self.assertRegex(first, r"`rule:<slug>`", "the rule citations are recorded in Artifacts")
        # Beside: the id and the citations are recorded in the same instruction.
        record = _section(first, "Record the ADR id", "expected_output")
        self.assertIn("rule:", record)


class ReviewModuleTests(unittest.TestCase):
    """Clause 5: the consistency reviewer checks against the cited rules."""

    def _consistency_clause(self) -> str:
        first = _prompt_blocks(_read(REVIEW_MODULE))[0]
        return _section(first, "(b) Consistency", "(c) Clarity")

    def test_consistency_reviewer_checks_the_cited_rules(self):
        clause = self._consistency_clause()
        self.assertIn("rule:", clause)
        self.assertIn("cited", clause)

    def test_consistency_reviewer_no_longer_checks_against_bare_adrs(self):
        clause = self._consistency_clause()
        self.assertNotIn("checks the change against ADRs", clause)
        # Positive control for the absence: the clause is non-trivial and names the cited rules.
        self.assertRegex(clause, r"rule:")


class LogWorkTests(unittest.TestCase):
    """Clause 5: the selected journal procedure accepts rule citations."""

    def _refs_flag(self) -> str:
        self.assertIn("`references/journal.md`", _read(LOG_WORK_SKILL))
        t = _read(LOG_WORK_JOURNAL)
        lines = [ln for ln in t.splitlines() if "Supply refs through `--refs`" in ln]
        self.assertEqual(len(lines), 1, "the selected procedure must define --refs once")
        return lines[0]

    def test_refs_flag_accepts_a_rule_citation(self):
        flag = self._refs_flag()
        self.assertIn("rule:<slug>", flag)
        self.assertIn("[[", flag, "wiki-links remain accepted beside citations")

    def test_entry_format_admits_a_rule_citation_on_the_refs_line(self):
        t = _read(LOG_WORK_JOURNAL)
        self.assertIn("`Refs: <wiki-links and rule:<slug> citations>`", t)
        self.assertIn("Omit the `Refs:` line when there are no refs", t)

    def test_checklist_no_longer_demands_wiki_link_syntax_on_every_ref(self):
        checklist = _read(LOG_WORK_JOURNAL)
        self.assertNotIn("every link uses `[[...]]` syntax", checklist)
        # Positive control: final verification checks both accepted forms.
        refs_items = [ln for ln in checklist.splitlines() if "If `Refs:` is present, verify" in ln]
        self.assertEqual(len(refs_items), 1)
        self.assertIn("rule:<slug>", refs_items[0])
        self.assertIn("[[...]]", refs_items[0])

    def test_journal_carve_out_is_stated(self):
        """The journal cites rule:<slug> where a rule exists and ADR-NNNN where
        the ADR carries no governs block — both halves must be on the surface."""
        t = _read(LOG_WORK_JOURNAL)
        self.assertIn("rule:<slug>", t)
        self.assertIn("ADR-NNNN", t)
        self.assertIn("no `governs` block", t)


class WhiteboardingTests(unittest.TestCase):
    """Clause 5: read the doctrine domains before options are drafted, and
    state the placeholder rule for a brief proposing a rule that does not exist."""

    def test_doctrine_read_precedes_the_options_step(self):
        t = _read(WHITEBOARDING_SKILL)
        method = _section(t, "## The method", "## Output")
        doctrine_at = method.find("adrs/doctrine/index.md")
        options_at = method.find("Propose 2")
        self.assertGreater(doctrine_at, -1, "the method must read the doctrine index")
        self.assertGreater(options_at, -1)
        self.assertLess(doctrine_at, options_at, "the doctrine read comes before options are drafted")

    def test_output_guidance_carries_the_placeholder_rule(self):
        t = _read(WHITEBOARDING_SKILL)
        output = _section(t, "## Output", "## Rules")
        self.assertTrue(PLACEHOLDER_RE.search(output), "the session-output guidance states the placeholder form")


class BriefTemplateTests(unittest.TestCase):
    def test_brief_template_carries_the_placeholder_rule_and_no_live_citation(self):
        t = _read(BRIEF_TEMPLATE)
        buckets = _classify(t)
        self.assertTrue(buckets["placeholder"], "the template states the rule:<new-thing> form")
        # A shipped template must not cite a slug from THIS repo's resolver.
        self.assertEqual(buckets["token"], [])
        self.assertEqual(buckets["malformed"], [])


class CitationGrammarTests(unittest.TestCase):
    """Every citation on the planning surfaces resolves; every placeholder is a non-token."""

    def test_classifier_separates_the_four_shapes(self):
        # Positive control for every bucket the assertions below rely on.
        b = _classify("`rule:live-slug` and `rule:<new-thing>` and a `rule:` prefix and rule:Bad and rule:<x y>")
        self.assertEqual(b["token"], ["live-slug"])
        self.assertEqual(b["placeholder"], ["new-thing"])
        self.assertEqual(len(b["mention"]), 1)
        self.assertEqual(len(b["malformed"]), 2)

    def test_every_citation_on_the_planning_surfaces_resolves(self):
        require_dev_surface(self, RESOLVER, f"{TREE}/adrs/summaries/resolver.json")
        resolver = json.loads(_read(RESOLVER))
        live = resolver["slugs"]
        retired = resolver["retired_slugs"]
        found: dict[str, list[str]] = {}
        for p in PLANNING_SURFACES:
            for slug in _classify(_read(p))["token"]:
                found.setdefault(slug, []).append(p.name)
        self.assertTrue(found, "at least one planning surface cites a live rule (positive control)")
        for slug, where in found.items():
            with self.subTest(slug=slug, where=where):
                self.assertNotIn(slug, retired, f"rule:{slug} is retired; cite {retired.get(slug)}")
                self.assertIn(slug, live, f"rule:{slug} does not resolve")

    def test_every_placeholder_is_angle_bracketed_and_nothing_is_malformed(self):
        placeholders: list[str] = []
        for p in PLANNING_SURFACES:
            with self.subTest(surface=p.name):
                b = _classify(_read(p))
                self.assertEqual(b["malformed"], [], f"malformed rule: spelling in {p.name}")
                placeholders += b["placeholder"]
        self.assertTrue(placeholders, "the authoring rule is stated somewhere (positive control)")


if __name__ == "__main__":
    unittest.main()
