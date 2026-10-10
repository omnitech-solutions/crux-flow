"""Pins the prose that routes replaceable approaches and records delivery.

Four statements, each stated once where it belongs:
the iterate and patch-cycle skills route on whether an enduring constraint
changes, and name the Implementation Decision trigger with one phrase at every
site; the cycle template's completion prompts key their references on the
declared module kinds; the developer handoff names the selected approval binding
as the only source of delivery intent; each cycle kind's completion prompt
directs the result record to be written, names the schema that defines its
shape, states when it may be complete, and lists it in expected_output.

Every crux/ file read crosses the sync boundary. The OpenCode projection does
not, so its case skips in the staged artifact and fails in the dev checkout
when the file is missing. The root USER_GUIDE.md case skips in the staged
artifact too, where that path holds the public rendition. Whitespace is normalized before matching, because
the files wrap.
"""
import re
import unittest
from pathlib import Path

from _dev_surface import IS_STAGED_ARTIFACT, REPO_ROOT as ROOT, require_dev_surface

CRUX = ROOT / "crux"


def flat(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def between(text: str, start: str, end: str | None) -> str:
    begin = text.index(start)
    stop = text.index(end, begin) if end else len(text)
    return text[begin:stop]


TRIGGER = "replaceable approach whose reasoning must survive retrieval"
FORMAL_TRIGGER = "replaceable approach or material alternative whose reasoning must survive retrieval"


class RoutingTests(unittest.TestCase):
    def test_iterate_routes_on_enduring_constraint(self):
        text = flat(CRUX / "skills" / "iterate" / "SKILL.md")
        self.assertIn("enduring constraint", text)
        self.assertNotIn("a decision worth recording", text)
        self.assertNotIn("worth remembering", text)

    def test_patch_cycle_routes_on_enduring_constraint(self):
        text = flat(CRUX / "skills" / "patch-cycle" / "SKILL.md")
        self.assertIn("enduring constraint", text)
        self.assertNotIn("work that needs a decision recorded", text)
        self.assertNotIn("worth remembering", text)

    def test_iterate_names_the_trigger_at_every_site(self):
        text = flat(CRUX / "skills" / "iterate" / "SKILL.md")
        when = between(text, "Net-new or architectural work", "A reversible fix")
        row = between(text, "This bug is architectural-ish", "The fix is tiny")
        formal = between(text, "### Formal implementation reasoning", "A genuine conflict")
        for part in (when, row):
            self.assertIn(TRIGGER, part)
        # The retrieval qualifier governs both nouns at the formal site.
        self.assertIn(FORMAL_TRIGGER, formal)
        self.assertNotIn("or for a material alternative", formal)
        for part in (when, row):
            self.assertIn("Implementation Decision", part)
        self.assertIn("before start", when)

    def test_patch_cycle_names_the_trigger_at_every_site(self):
        text = flat(CRUX / "skills" / "patch-cycle" / "SKILL.md")
        bullet = between(text, "**Work that needs an ADR.**", "**A fix you cannot bound**")
        formal = between(text, "### Formal implementation reasoning", "Diagnosis-only")
        self.assertIn(TRIGGER, bullet)
        self.assertIn(TRIGGER, formal)
        self.assertIn("Implementation Decision", bullet)
        self.assertIn("`patch` slot", bullet)
        # "this" must not trail the Implementation Decision sentence.
        self.assertIn("The archive check enforces the ADR boundary", bullet)


class RoutingNameTests(unittest.TestCase):
    def test_no_site_keeps_the_old_significant_name(self):
        for rel in ("templates/cycle-module-verify.yaml", "skills/dev-cycle/SKILL.md"):
            text = flat(CRUX / rel)
            self.assertNotIn("significant replaceable approach", text, rel)
            self.assertIn(TRIGGER, text, rel)

    def test_patch_template_formal_site(self):
        text = flat(CRUX / "templates" / "patch-promptbook-template.yaml")
        self.assertIn(FORMAL_TRIGGER, text)


class CompletionReferenceTests(unittest.TestCase):
    def test_cycle_completion_keys_on_module_kind(self):
        text = flat(CRUX / "templates" / "cycle-promptbook-template.yaml")
        prep = between(text, 'title: "Prep: changelog', 'title: "Summary:')
        summary = between(text, 'title: "Summary:', None)[:4000]
        for part in (prep, summary):
            self.assertIn("modules.adrs", part)
            self.assertIn("modules.implementations", part)
            self.assertIn("implementation_bindings", part)

    def test_cycle_changelog_item_keys_on_module_kind(self):
        text = flat(CRUX / "templates" / "cycle-promptbook-template.yaml")
        prep = between(text, 'title: "Prep: changelog', 'title: "Summary:')
        item = between(prep, "1. **Changelog**", "2. **Documentation**")
        self.assertIn("modules.adrs", item)
        self.assertIn("modules.implementations", item)
        self.assertNotIn("Include a wiki-link to the Accepted ADR's page", item)

    def test_cycle_summary_links_the_result_records(self):
        text = flat(CRUX / "templates" / "cycle-promptbook-template.yaml")
        summary = between(text, 'title: "Summary:', None)[:4000]
        self.assertIn("result-NNN.yaml", summary)
        self.assertIn("the Prep result-record step wrote", summary)


class DeveloperHandoffTests(unittest.TestCase):
    NEEDLE = "selected approval binding"

    def test_binding_defines_delivery_intent(self):
        projection = ROOT / "opencode" / "agents" / "developer.md"
        for path in (CRUX / "agents" / "developer.md", projection):
            if path == projection:
                require_dev_surface(self, path, "opencode/agents/developer.md")
            text = flat(path)
            self.assertIn(self.NEEDLE, text, path)
            self.assertIn("background imposes no obligation", text, path)


class DeliveryRecordTests(unittest.TestCase):
    def check(self, name: str, start: str, end: str | None):
        text = flat(CRUX / "templates" / name)
        part = between(text, start, end)
        self.assertIn("Result record", part)
        self.assertNotIn("Delivery record", part)
        self.assertIn("implementation_bindings", part)
        self.assertIn("implementation-decisions.py", part)
        self.assertIn("result --decision", part)
        self.assertIn("schemas/implementation-result.schema.json", part)
        self.assertIn("implementation-result-template.yaml` is an example", part)
        self.assertIn("every file changed under the scope", part)
        self.assertIn("`partial` when part of the scope was delivered", part)
        self.assertIn("`unimplemented` with empty `scope` and `sources` when none was", part)
        self.assertIn("`written` path the command prints", part)
        self.assertIn("each by explicit path", part)
        expected = part[part.index("expected_output:"):]
        expected = expected[: expected.index("side_effects:")] if "side_effects:" in expected else expected
        self.assertIn("result record", expected)
        self.assertIn("implementation_bindings is empty", expected)

    def test_cycle_prep(self):
        self.check("cycle-promptbook-template.yaml", 'title: "Prep: changelog', 'title: "Summary:')

    def test_iterate_prep(self):
        self.check("iterate-promptbook-template.yaml", 'title: "Prep: changelog', 'title: "Summary:')

    def test_patch_summary(self):
        self.check("patch-promptbook-template.yaml", 'title: "Summary:', None)


class ResultTemplateTests(unittest.TestCase):
    def test_scope_comment_says_files_not_directories(self):
        text = flat(CRUX / "templates" / "implementation-result-template.yaml")
        self.assertIn("scope lists delivered files, never a directory", text)
        self.assertIn("each file changed under it", text)


class PrepRowTests(unittest.TestCase):
    def test_module_tables_name_the_result_record(self):
        for skill in ("dev-cycle", "iterate"):
            text = flat(CRUX / "skills" / skill / "SKILL.md")
            self.assertIn("changelog, docs, journal, result record, PR draft", text, skill)


class UserGuideTests(unittest.TestCase):
    def check(self, path: Path):
        text = flat(path)
        self.assertIn('`{"remedy": "<next step>"}` line on stderr', text)
        self.assertIn("migration-source-unreadable", text)
        self.assertIn("migration-symlink-refused", text)
        self.assertIn("migration-citation-scope-refused", text)
        self.assertIn("stage its deletion", text)
        # Every governing reader refuses; only three of them print the remedy line.
        self.assertIn("(summaries, doctrine, rules catalog, arch, reviews index and the "
                      "`query-docs` authority view) refuses", text)
        self.assertIn("Summaries, doctrine and the authority view also print", text)
        self.assertIn("The rules catalog, arch and the reviews index print the code without "
                      "a remedy line", text)
        self.assertIn("including a harmless mirror link", text)
        self.assertNotIn("Each of these readers also prints", text)
        # The submodule steps match the remedy's order and conditions, and fit readers that
        # print no remedy line.
        steps = ("To keep a submodule's skill as regular files, follow these steps in order "
                 "(summaries, doctrine and the authority view also print them as the remedy line): "
                 "untrack the submodule with `git rm --cached`; if `.gitmodules` has the submodule's "
                 "section, remove that section and stage `.gitmodules` with `git add`; move the `.git` "
                 "entry at the top of the submodule path out of the repository (moving it keeps the "
                 "nested repository's history; deleting an embedded clone's `.git` directory loses any "
                 "history not pushed elsewhere); then `git add` the files.")
        self.assertIn(steps, text)
        # The condition covers the stage step too: on an embedded clone there is no
        # `.gitmodules`, and `git add .gitmodules` exits 128.
        self.assertNotIn("section if it has one and stage", text)
        self.assertNotIn("the refusal's remedy line", text)

    def test_template_copy(self):
        self.check(CRUX / "templates" / "USER_GUIDE.md")

    def test_repository_copy(self):
        # A staged artifact's root USER_GUIDE.md is the public rendition, which a model
        # rewrites at release; only the dev checkout holds the verbatim twin.
        if IS_STAGED_ARTIFACT:
            self.skipTest("root USER_GUIDE.md is the public rendition in a staged artifact")
        path = ROOT / "USER_GUIDE.md"
        require_dev_surface(self, path, "USER_GUIDE.md")
        self.check(path)


if __name__ == "__main__":
    unittest.main()
