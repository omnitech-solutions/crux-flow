"""init-docs defaults new repos to schema 5 + invariants.

Attribution split: the seven-concern / no-"six concerns" framing bar is
ADR-0053 criterion 4; the schema_version "5" + bionic/ tree unification is
ADR-0059 (ADR-0053 specified "4"; it predates the unification).
`TestInitDocsSkillProse` asserts the `init-docs` SKILL.md prose matches the
shipped templates rather than a hardcoded version literal, and carries no
obsolete v4 current-layout wording. No third-party deps beyond PyYAML
(for the manifest + bionic-yml templates).
"""
from __future__ import annotations

import functools
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TEMPLATES = REPO_ROOT / "crux" / "templates"

try:
    from ._dev_surface import IS_STAGED_ARTIFACT, TREE, TREE_AGENTS_MD, require_dev_surface
except ImportError:  # unittest discover imports test modules top-level
    from _dev_surface import IS_STAGED_ARTIFACT, TREE, TREE_AGENTS_MD, require_dev_surface

try:
    import yaml
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

SEVEN = {"code", "research", "adrs", "briefs", "journal", "promptbooks", "invariants"}


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ManifestTemplateTests(unittest.TestCase):
    def test_manifest_tmpl_is_v5_seven_concerns_plus_arch_and_observations(self):
        m = yaml.safe_load((TEMPLATES / "manifest.yml.tmpl").read_text())
        self.assertEqual(m["schema_version"], "5")
        self.assertEqual(set(m["concerns_enabled"]), SEVEN | {"arch", "observations"})

    def test_manifest_tmpl_seeds_the_friction_adoption_boundary(self):
        """A fresh tree measures friction from its first conforming entry.

        The template shipped no `journal:` block at all, so every new tree
        recorded no adoption date and every friction reader returned
        `unmeasurable` — a tree using the conforming journal writer from day
        one could not measure the thing that writer records. init-docs step 6
        substitutes `{{today}}`; the placeholder is QUOTED so this file stays
        YAML-parseable, which is what lets this test read it at all.
        """
        text = (TEMPLATES / "manifest.yml.tmpl").read_text()
        m = yaml.safe_load(text)
        self.assertEqual(m["journal"], {"friction_line_from": "{{today}}"})
        self.assertIn('friction_line_from: "{{today}}"', text)
        self.assertNotIn("friction_line_from: null", text)

    def test_init_docs_substitutes_the_friction_placeholder(self):
        """The instruction that fills the placeholder, and the rerun rule.

        Positive control on the first assertion: the placeholder token must
        appear in the skill text, so a renamed placeholder cannot make the
        substitution instruction silently absent.
        """
        skill = (REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md").read_text()
        self.assertIn("{{today}}", skill)
        self.assertIn("friction_line_from", skill)
        self.assertIn("PRIOR_FRICTION_FROM", skill)
        # The substituted template must parse, with a real date in the key.
        written = (TEMPLATES / "manifest.yml.tmpl").read_text().replace("{{today}}", "2026-09-10")
        self.assertEqual(yaml.safe_load(written)["journal"]["friction_line_from"], "2026-09-10")

    def test_manifest_tmpl_carries_observation_counter_without_schema_bump(self):
        # Additive enablement on the arch precedent: the counters arrive with
        # NO schema_version bump (docs/AGENTS.md §17, §17.5). init-docs step 6
        # copies this template VERBATIM, so this block IS a fresh tree's
        # `observation` block — the survey keys are absent from a new repo
        # unless they are seeded here.
        m = yaml.safe_load((TEMPLATES / "manifest.yml.tmpl").read_text())
        self.assertEqual(m["observation"], {"next_number": 1, "stale_days": 90,
                                            "next_survey_number": 1,
                                            "survey_stub_days": 1})
        self.assertEqual(m["schema_version"], "5")
        self.assertEqual(m["adr"], {"next_number": 1, "governs_rule_baseline": []})
        self.assertEqual(m["promptbook"], {"next_number": 1})

    def test_manifest_tmpl_ships_an_explicit_empty_rule_length_baseline(self):
        """The key must be PRESENT and EMPTY, and the distinction is the whole design.

        `init-docs` copies this template verbatim, so whatever is here is a fresh tree's manifest.
        An ABSENT key means "this tree has not snapshotted its existing ADRs yet" and leaves the
        rule-length advisory inert; an EMPTY list means "there is nothing to grandfather", which
        is true of a tree with no ADRs and switches the advisory on. Ship it absent and every
        freshly bootstrapped tree would carry the advisory permanently dead — the failure a
        council round caught in the design before it was written.
        """
        m = yaml.safe_load((TEMPLATES / "manifest.yml.tmpl").read_text())
        self.assertIn("governs_rule_baseline", m["adr"],
                      "an absent key leaves the advisory inert in every new tree")
        self.assertEqual(m["adr"]["governs_rule_baseline"], [],
                         "a new tree grandfathers nothing; the list is empty, not populated")

    def test_the_template_ships_no_adr_identity_of_this_project(self):
        """This project's own ADR ids are not universal exemptions for anyone else."""
        import re
        raw = (TEMPLATES / "manifest.yml.tmpl").read_text()
        m = yaml.safe_load(raw)
        self.assertEqual([x for x in m["adr"]["governs_rule_baseline"]], [])
        body = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("#"))
        self.assertEqual(re.findall(r"ADR-\d{4}", body), [],
                         "no ADR identifier may ship in the manifest template")

    def test_manifest_tmpl_agrees_with_claude_md_tmpl(self):
        # Twin lock-step: the AGENTS.md.tmpl §7 example manifest must show the
        # same schema + include invariants, so a fresh seed is self-consistent.
        c = (TEMPLATES / "AGENTS.md.tmpl").read_text()
        self.assertIn('schema_version: "5"', c)
        self.assertRegex(c, r"concerns_enabled:[\s\S]{0,400}- invariants")
        self.assertRegex(c, r"concerns_enabled:[\s\S]{0,400}- arch")

    def test_manifest_tmpl_observation_block_matches_claude_md_tmpl_section_7(self):
        # The gap that shipped the survey keys to the dogfood tree but not to a
        # fresh one: §7 of the AGENTS.md written INTO a new tree documents the
        # `observation` keys, and nothing compared that list against the
        # manifest init-docs actually writes. Every key §7 documents must exist
        # in the template, or a fresh repo reads a schema doc describing a
        # manifest it does not have.
        m = yaml.safe_load((TEMPLATES / "manifest.yml.tmpl").read_text())
        c = (TEMPLATES / "AGENTS.md.tmpl").read_text()
        block = re.search(r"\nobservation:.*?\n\n", c, re.DOTALL)
        self.assertIsNotNone(block, "no `observation:` block in AGENTS.md.tmpl §7")
        documented = set(re.findall(r"^  ([a-z_]+):", block.group(0), re.MULTILINE))
        # Positive control: §7 must actually document keys, so an empty match
        # cannot make the subset assertion below vacuously true.
        self.assertIn("next_number", documented)
        self.assertEqual(documented - set(m["observation"]), set())


class StrictFramingSurfaceTests(unittest.TestCase):
    """ADR-0053 criterion 4 strict bar: no residual 'six concern(s)' framing."""

    STRICT = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "USER_GUIDE.md",
        REPO_ROOT / "crux" / "templates" / "USER_GUIDE.md",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "crux" / "templates" / "manifest.yml.tmpl",
    ]

    def test_no_six_concern_framing_in_strict_surfaces(self):
        require_dev_surface(self, REPO_ROOT / "AGENTS.md", "AGENTS.md")
        pat = re.compile(r"six[- ]concern", re.IGNORECASE)
        for f in self.STRICT:
            self.assertIsNone(
                pat.search(f.read_text()),
                f"residual six-concern framing in {f.relative_to(REPO_ROOT)}",
            )

    def test_strict_surfaces_name_seven_concerns_or_invariants(self):
        require_dev_surface(self, REPO_ROOT / "AGENTS.md", "AGENTS.md")
        # Positive check: the framing actually moved to seven / names invariants.
        for f in (REPO_ROOT / "README.md", REPO_ROOT / "USER_GUIDE.md", REPO_ROOT / "AGENTS.md"):
            t = f.read_text()
            self.assertTrue(
                "seven concern" in t or "invariants" in t,
                f"{f.relative_to(REPO_ROOT)} does not name seven concerns / invariants",
            )


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class TestInitDocsSkillProse(unittest.TestCase):
    """ADR-0059 acceptance: init-docs SKILL.md prose matches the shipped
    templates (bionic/ tree unification) and carries no obsolete v4
    current-layout wording. Expectations are derived FROM the templates
    where practical rather than hardcoded, so a future schema bump that
    updates the templates doesn't silently desync this test from them.
    """

    SKILL_MD = REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md"

    def _skill_text(self) -> str:
        return self.SKILL_MD.read_text()

    def test_step6_schema_version_parenthetical_matches_manifest_template(self):
        manifest = yaml.safe_load((TEMPLATES / "manifest.yml.tmpl").read_text())
        current_schema = manifest["schema_version"]
        text = self._skill_text()
        m = re.search(r'currently `"([^"]+)"`', text)
        self.assertIsNotNone(
            m, "expected a step-6 `currently \"X\"` parenthetical in init-docs SKILL.md"
        )
        self.assertEqual(
            m.group(1),
            current_schema,
            "SKILL.md step-6 'currently' literal is out of sync with "
            "manifest.yml.tmpl's schema_version",
        )

    def test_no_obsolete_v4_current_layout_literals(self):
        text = self._skill_text()
        # Amendment A1: ban only obsolete CURRENT-LAYOUT literals; history /
        # migration references to older schema versions stay legitimate.
        banned = [
            'currently `"4"`',
            "tree at schema_version 4",
        ]
        for literal in banned:
            self.assertNotIn(
                literal,
                text,
                f"obsolete v4 current-layout literal {literal!r} found in init-docs SKILL.md",
            )

    def test_invariants_concern_is_one_folder_inside_the_tree(self):
        text = self._skill_text()
        # Positive: the suite + reconciliation are named INSIDE the tree.
        self.assertIn("${DOCS_DIR}/invariants/checks/", text)
        self.assertIn("${DOCS_DIR}/invariants/reconciliation.yml", text)
        # Negative: no peer-root instruction survives (the v4 split layout).
        self.assertNotIn("OUTSIDE docs/", text)
        self.assertNotIn("at the repo root under `bionic/`", text)
        self.assertNotIn("peer invariants check suite", text)

    def test_arch_concern_scaffolded_and_spine_deferred(self):
        # WU1 (arch enrollment): init eagerly creates the arch/ directory + a
        # placeholder index.md but DEFERS the derived spine (no derive-arch at
        # init), and the master index stays exempt (no ## Arch section).
        text = self._skill_text()
        # Instruction to create the arch dir + a placeholder index.md.
        self.assertIn("arch/", text)
        self.assertIn("${DOCS_DIR}/arch/index.md", text)
        # DEFER appears in the arch scaffolding block, and it states init must
        # not run the derive at bootstrap (deferred to first derive/audit).
        i = text.index("Arch concern")
        arch_block = text[i : i + 900]
        self.assertRegex(arch_block, r"(?i)defer")
        self.assertIn("MUST NOT invoke", arch_block)
        # It must NOT instruct adding an `## Arch` master-index section; the
        # only permitted mention is the exemption (no `## Arch` section).
        self.assertIn("NO `## Arch` section", text)

    def test_directory_tree_step_creates_under_docs_dir_not_literal_docs(self):
        text = self._skill_text()
        self.assertIn(
            "${REPO_ROOT}/${DOCS_DIR}/",
            text,
            "step-4 directory-tree instructions should create the tree under "
            "${REPO_ROOT}/${DOCS_DIR}/",
        )
        self.assertNotIn(
            "under `${REPO_ROOT}/docs/`",
            text,
            "step-4 must not instruct creation under the retired literal docs/ layout",
        )

    def test_checklist_guards_against_greenfield_docs_directory(self):
        text = self._skill_text()
        self.assertIn(
            "No directory literally named `docs` was created on a greenfield default init",
            text,
        )

    def test_directory_tree_diagram_root_matches_bionic_yml_template(self):
        bionic_cfg = yaml.safe_load((TEMPLATES / "bionic-yml.tmpl").read_text())
        docs_dir = bionic_cfg["docs_dir"]
        text = self._skill_text()
        # The step-4 fenced diagram's first line is the tree root name.
        m = re.search(r"```\n(\S+)/\n", text)
        self.assertIsNotNone(
            m, "expected the step-4 fenced directory-tree diagram in init-docs SKILL.md"
        )
        self.assertEqual(
            m.group(1),
            docs_dir,
            "step-4 diagram root does not match bionic-yml.tmpl's docs_dir",
        )

    def _section(self, heading: str) -> str:
        """Extract one pipeline step's body (### heading to the next ###)."""
        t = self._skill_text()
        i = t.index(heading)
        j = t.find("\n### ", i + len(heading))
        return t[i : j if j != -1 else len(t)]

    def test_step2_guard_keys_on_resolved_docs_dir(self):
        s = self._section("### 2. Resolve the tree location")
        self.assertIn("${REPO_ROOT}/${DOCS_DIR}/", s)
        self.assertIn("bionic-config.py", s)
        self.assertNotRegex(s, r"\$\{REPO_ROOT\}/(docs|bionic)/")
        self.assertIn("never guess `bionic`", s)

    def test_step6a_substitutes_resolved_docs_dir(self):
        s = self._section("### 6.A.")
        self.assertIn("substituting `docs_dir: ${DOCS_DIR}`", s)
        self.assertNotIn("Copy it verbatim", s)

    def test_log_seed_literal_matches_manifest_template(self):
        manifest = yaml.safe_load((TEMPLATES / "manifest.yml.tmpl").read_text())
        current_schema = manifest["schema_version"]
        # Positive derivation (amendment A1/SC-1): the seeded init entry must
        # record the CURRENT schema, whatever obsolete value history holds.
        self.assertIn(
            f"tree at schema_version {current_schema}",
            self._skill_text(),
            "step-8 log.md seed literal does not record the current schema_version",
        )

    def test_rollback_contract_restores_modifications_and_archive(self):
        s = self._section("**Rollback contract.**")
        self.assertIn("record the PRIOR content", s)
        self.assertIn("Restore the recorded prior content", s)
        self.assertIn("move `${ARCHIVE}` back to `${DOCS_DIR}`", s)
        self.assertIn("do NOT move and instead report both paths", s)
        self.assertIn("state the archive path in the failure report", s)
        self.assertIn("Never `rm -rf`", s)

    def test_user_guide_overwrite_requires_force_and_confirmation(self):
        t = self._skill_text()
        self.assertIn("overwrite ONLY when `--force` was passed AND the user has confirmed", t)

    def test_step2_refuses_non_directory_at_resolved_location(self):
        s = self._section("### 2. Resolve the tree location")
        self.assertIn("is not a directory, **STOP**", s)

    def test_checklist_bionic_yml_names_the_created_tree(self):
        self.assertIn(
            "`.bionic.yml` exists at the repo root and carries `docs_dir` naming "
            "the tree just created",
            self._skill_text(),
        )


# Pre-fix shipped prose, verbatim: the positive controls for the pointer-claim
# detector in RootAgentsPointerTests. Each asserted a repo-root pointer that
# init-docs does not always write.
_PRE_FIX_TEMPLATE_LINE_3 = (
    "Operational schema for the `docs/` tree in **{{repo_name}}**. The repo-root "
    "`AGENTS.md` references this file with a single line: \"See `docs/AGENTS.md` "
    "for documentation operations.\" This file is the single source of truth for "
    "everything Claude does under `docs/`."
)
_PRE_FIX_GUIDE_BULLET = (
    "- Edit the repo-root `AGENTS.md` to add project-specific notes Claude should "
    "know about (just don't remove the `See bionic/AGENTS.md` line)."
)

# Each pattern asserts, unconditionally, that a repo-root AGENTS.md points at the
# tree. Case-insensitive, so a sentence-initial "Don't" is caught too.
_ROOT_POINTER_CLAIMS = (
    re.compile(r"repo-root `AGENTS\.md` references this file", re.IGNORECASE),
    re.compile(r"don't remove the `See [^`]*AGENTS\.md` line", re.IGNORECASE),
)

_GUIDE_BULLET_PREFIX = "- Edit the repo-root `AGENTS.md`"


def _root_pointer_claims(text: str) -> list[str]:
    """Every unconditional root-pointer claim in `text`."""
    return [m.group(0) for p in _ROOT_POINTER_CLAIMS for m in p.finditer(text)]


def _line_3(path: Path) -> str:
    return path.read_text(encoding="utf-8").splitlines()[2]


# Line 3 as it stood before its quotes named the configured tree directory.
_PRE_FIX_DOCS_LINE_3 = (
    "It appends the line \"See `docs/AGENTS.md` for documentation operations.\" unless "
    "the file already references this one. It appends an instruction to read "
    "`docs/objectives.md` unless the file already names `objectives.md`."
)
_DOCS_DIR_GLOSS = ("Here, as everywhere in this file, `docs/` and `<docs_dir>` both name "
                   "the configured tree directory, `bionic` by default.")


def _fence_bodies(text: str, lang: str) -> list[str]:
    """The body of every ```lang fence in `text`, with list indentation removed."""
    bodies = []
    for m in re.finditer(rf"^([ \t]*)```{lang}\n(.*?)\n\1```$", text,
                         flags=re.DOTALL | re.MULTILINE):
        indent = m.group(1)
        bodies.append("\n".join(ln[len(indent):] if ln.startswith(indent) else ln
                                for ln in m.group(2).split("\n")).strip("\n"))
    return bodies


def _line_3_quote_problems(line3: str, see: str, objectives_line: str) -> list[str]:
    """How line 3's quotes of step 9's appends differ from what step 9 writes."""
    problems = []
    quoted_see = '"' + see.replace("${DOCS_DIR}", "<docs_dir>") + '"'
    if quoted_see not in line3:
        problems.append(f"line 3 does not quote {quoted_see}")
    token = re.match(r"Read (`\$\{DOCS_DIR\}/objectives\.md`)", objectives_line)
    if token is None:
        problems.append("step 9's objectives block does not open with its path")
    else:
        quoted = "read " + token.group(1).replace("${DOCS_DIR}", "<docs_dir>")
        if quoted not in line3:
            problems.append(f"line 3 does not say {quoted}")
    for literal in ("See `docs/AGENTS.md`", "`docs/objectives.md`"):
        if literal in line3:
            problems.append(f"line 3 quotes the literal {literal}")
    if _DOCS_DIR_GLOSS not in line3:
        problems.append("line 3 does not gloss `<docs_dir>`")
    return problems


def _claude_compat():
    import importlib.util
    path = REPO_ROOT / "crux" / "scripts" / "check-claude-compat.py"
    spec = importlib.util.spec_from_file_location("_cc_for_init_docs_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=None)
def _root_agents():
    import importlib.util
    import sys
    path = REPO_ROOT / "crux" / "scripts" / "root_agents.py"
    spec = importlib.util.spec_from_file_location("_root_agents_for_init_docs_tests", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_root_agents_for_init_docs_tests", module)
    spec.loader.exec_module(module)
    return module


def _harness_facts() -> list[str]:
    """The four documented harness facts the NOTE and the WARNING both carry.

    The version floor and the excluded distributions come from
    check-claude-compat.py, so a change there fails this test rather than
    leaving the shipped sentence stale.
    """
    cc = _claude_compat()
    floor = ".".join(map(str, cc.VERSION_FLOOR))
    names = [d.capitalize() for d in cc.EXCLUDED_DISTRIBUTIONS]
    dists = ", ".join(names[:-1]) + " and " + names[-1]
    return [
        "Codex and OpenCode read a repo-root AGENTS.md.",
        f"Claude Code reads it from version {floor} under its default settings, "
        f"except on the {dists} distributions.",
        "Under those defaults, three files stop Claude Code from reading AGENTS.md: "
        "CLAUDE.md, .claude/CLAUDE.md and CLAUDE.local.md.",
        "Each one does so in any directory from the repository root to the working directory.",
    ]


def _expected_note() -> str:
    return "\n".join([
        "NOTE: This repository held no AGENTS.md or CLAUDE.md at its root, "
        "so init-docs created a repo-root AGENTS.md.",
        "It holds a pointer to the documentation tree and an instruction to read its objectives.",
        *_harness_facts(),
        "To see which instruction files this host loads, run check-claude-compat.",
    ])


def _expected_warning(see: str, objectives: list[str]) -> str:
    """The whole WARNING fence body step 9 must carry, built from its sources."""
    return "\n".join([
        "WARNING: <lead>",
        *_harness_facts(),
        "- <entry name>: <sentence for its code>",
        "When every entry above is resolved, add these lines to the repo-root AGENTS.md, "
        "and create that file if it does not exist:",
        "",
        see,
        "",
        *objectives,
    ])


def _text_fence_problems(step9: str, marker: str, expected: str) -> list[str]:
    """How the ```text fence opening with `marker` departs from `expected`."""
    fences = [b for b in _fence_bodies(step9, "text") if b.startswith(marker)]
    if len(fences) != 1:
        return [f"step 9 carries {len(fences)} ```text fences opening {marker!r}, not 1"]
    got, want = fences[0].splitlines(), expected.splitlines()
    for n, (g, w) in enumerate(zip(got, want), start=1):
        if g != w:
            return [f"line {n} is {g!r}, expected {w!r}"]
    if len(got) != len(want):
        return [f"the fence has {len(got)} lines, expected {len(want)}"]
    return []


def _remedy_rows(step9: str) -> dict[str, str]:
    """The code -> sentence table in step 9."""
    rows = {}
    for m in re.finditer(r"^\| `([^`]+)` \| (.+?) \|$", step9, flags=re.MULTILINE):
        rows[m.group(1)] = m.group(2)
    return rows


# The retired no-create rule, in every shape the shipped prose stated it.
_RETIRED_NO_CREATE = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"do \*\*not\*\* create one",
    r"creates no repo-root `AGENTS\.md`",
    r"neither creates a file",
    r"do not create that file solely",
    r"if it carries none, nothing was created",
    r"never creates (?:a repo-root `AGENTS\.md`|that file)",
    r"only warns when there is none",
    r"warns when none does",
    r"adding the file is your call",
    r"warns and leaves adding one to you",
))


def _retired_no_create_claims(text: str) -> list[str]:
    """Every sentence of `text` that still states the retired no-create rule."""
    return [m.group(0) for p in _RETIRED_NO_CREATE for m in p.finditer(text)]


# Pre-fix shipped prose, verbatim: the positive controls for the stale detector.
_PRE_FIX_STEP9_BULLET = (
    "- If it does not exist, do **not** create one — that's the user's call. Surface the "
    "WARNING block given at the end of this step in the summary.")
_PRE_FIX_OVERVIEW = ("It creates no repo-root `AGENTS.md`: step 9 appends to one that exists "
                     "and only warns when there is none.")
_PRE_FIX_RED_FLAG = ("Both append and preserve existing content and generated regions; "
                     "neither creates a file that does not exist.")
_PRE_FIX_CHECKLIST = "If it carries none, nothing was created."
_PRE_FIX_CATALOG = ("It never creates a repo-root AGENTS.md: it appends a pointer to one that "
                    "exists and warns when none does.")
_PRE_FIX_LINE_3_NO_CREATE = ("`init-docs` never creates a repo-root `AGENTS.md`. When none "
                             "exists, it warns and leaves adding one to you.")
_PRE_FIX_GUIDE_NO_CREATE = ("`init-docs` never creates that file. When it exists, "
                            "`init-docs` appends")

#: The create rule as template line 3 states it.
_LINE_3_CREATE_RULE = (
    "`init-docs` creates a repo-root `AGENTS.md` when the repository root holds no "
    "`AGENTS.md` or `CLAUDE.md` in any letter case. A `CLAUDE.local.md` does not block "
    "that create.")


#: The only two places template line 3 names a `CLAUDE.md`: the create condition and the
#: list of entries init-docs never writes to. Any other mention is a stray reference.
_LINE_3_CLAUDE_MENTIONS = (
    "holds no `AGENTS.md` or `CLAUDE.md` in any letter case.",
    "writes nothing to a `CLAUDE.md` in any letter case,",
)


def _without_sanctioned_claude_mentions(line: str) -> str:
    for mention in _LINE_3_CLAUDE_MENTIONS:
        assert mention in line, mention
        line = line.replace(mention, "")
    return line


class RootAgentsPointerTests(unittest.TestCase):
    """What the shipped prose says init-docs does with a repo-root AGENTS.md.

    Step 9 creates the file when the root holds no AGENTS.md or CLAUDE.md in
    any letter case, appends to an exact regular one, and reports every legacy or
    unwritable entry with a remedy. The helper root_agents.py does the file
    work; step 9 holds the two blocks and every sentence the user reads.
    """

    SKILL_MD = REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md"
    TEMPLATE = TEMPLATES / "AGENTS.md.tmpl"
    GUIDE_TEMPLATE = TEMPLATES / "USER_GUIDE.md"
    CATALOG_GUIDE = REPO_ROOT / "tools" / "bionic-catalog" / "crux-learning-catalog-guide.json"

    def _skill_text(self) -> str:
        return self.SKILL_MD.read_text(encoding="utf-8")

    def _guide_bullet(self) -> str:
        bullets = [ln for ln in self.GUIDE_TEMPLATE.read_text(encoding="utf-8").splitlines()
                   if ln.startswith(_GUIDE_BULLET_PREFIX)]
        self.assertEqual(len(bullets), 1, bullets)
        return bullets[0]

    def _raw_step9(self) -> str:
        """Step 9 as written, with its line breaks and indentation intact."""
        text = self._skill_text()
        start = text.index("### 9. Create or update the repo-root `AGENTS.md`")
        return text[start:text.index("### 10.", start)]

    def _step9(self) -> str:
        return " ".join(self._raw_step9().split())

    def _step9_appends(self, step9: str) -> tuple[str, list[str]]:
        """The `See` line and the objectives block step 9 carries, verbatim."""
        fences = _fence_bodies(step9, "markdown")
        self.assertEqual(len(fences), 2, fences)
        see = [ln.strip() for body in fences for ln in body.splitlines()
               if ln.strip().startswith("See `${DOCS_DIR}/")]
        self.assertEqual(len(see), 1, see)
        objectives = [body for body in fences if body.startswith("Read `${DOCS_DIR}/")]
        self.assertEqual(len(objectives), 1, fences)
        return see[0], objectives[0].splitlines()

    # -- the create rule is pinned; the retired rule is gone ------------------

    def test_step9_states_the_create_rule_and_the_guards(self):
        step9 = self._step9()
        for pinned in (
                "The root holds no `AGENTS.md` or `CLAUDE.md` in any letter case: it "
                "creates `AGENTS.md` holding the pointer line, one blank line, then the "
                "objectives block, in LF line endings with one final newline, and no heading.",
                "Running the append rules below against that file appends nothing.",
                "it appends the pointer line only when the file does not reference the "
                "tree's `AGENTS.md`, and the objectives block only when the file does not "
                "name `objectives.md`.",
                "The root holds a `CLAUDE.md` or a variant, with no exact `AGENTS.md`: it "
                "creates nothing, appends nothing to that entry, and reports it.",
                "is a symlink (live or dangling), is not a regular file, cannot be read, "
                "or is not valid UTF-8: it writes nothing to it and reports it.",
                "A legacy directory limits only itself: an exact regular `AGENTS.md` beside "
                "it still gets its append.",
                "judged from the root's directory entries and never from a path probe.",
                "It never renames, deletes or rewrites a `CLAUDE.md` or a variant."):
            with self.subTest(pinned=pinned[:50]):
                self.assertIn(pinned, step9)

    def test_no_shipped_surface_states_the_retired_no_create_rule(self):
        surfaces = {p.relative_to(REPO_ROOT).as_posix(): p.read_text(encoding="utf-8")
                    for p in (self.SKILL_MD, self.TEMPLATE, self.GUIDE_TEMPLATE,
                              REPO_ROOT / "crux" / "skills" / "init-docs" / "references"
                              / "pitfalls.md")}
        if not IS_STAGED_ARTIFACT:
            for extra in (REPO_ROOT / "USER_GUIDE.md", self.CATALOG_GUIDE):
                require_dev_surface(self, extra, extra.name)
                surfaces[extra.name] = extra.read_text(encoding="utf-8")
        self.assertGreaterEqual(len(surfaces), 4)
        for name, text in surfaces.items():
            with self.subTest(surface=name):
                self.assertEqual(_retired_no_create_claims(text), [])

    def test_positive_control_the_stale_detector_flags_the_pre_fix_text(self):
        """The absence test above is not vacuous: each retired sentence is caught."""
        for label, old in (("step 9 bullet", _PRE_FIX_STEP9_BULLET),
                           ("overview", _PRE_FIX_OVERVIEW),
                           ("red flag", _PRE_FIX_RED_FLAG + " never creates that file"),
                           ("checklist", _PRE_FIX_CHECKLIST),
                           ("catalog guide", _PRE_FIX_CATALOG),
                           ("template line 3", _PRE_FIX_LINE_3_NO_CREATE),
                           ("user guide", _PRE_FIX_GUIDE_NO_CREATE)):
            with self.subTest(label):
                self.assertGreaterEqual(len(_retired_no_create_claims(old)), 1, old)
        # Neither the corrected line 3 nor the corrected bullet is flagged.
        self.assertEqual(_retired_no_create_claims(_line_3(self.TEMPLATE)), [])
        self.assertEqual(_retired_no_create_claims(self._guide_bullet()), [])

    def test_shipped_templates_assert_no_unconditional_root_pointer(self):
        for path in (self.TEMPLATE, self.GUIDE_TEMPLATE):
            with self.subTest(path=path.name):
                self.assertEqual(
                    _root_pointer_claims(path.read_text(encoding="utf-8")), [],
                    f"{path.relative_to(REPO_ROOT)} claims a repo-root pointer "
                    "that init-docs does not always write")

    def test_positive_control_detects_pre_fix_text(self):
        """The pointer-claim detector finds its verbatim pre-fix sentences."""
        self.assertGreaterEqual(len(_root_pointer_claims(_PRE_FIX_TEMPLATE_LINE_3)), 1)
        self.assertGreaterEqual(len(_root_pointer_claims(_PRE_FIX_GUIDE_BULLET)), 1)
        self.assertEqual(_root_pointer_claims(_line_3(self.TEMPLATE)), [])
        self.assertEqual(_root_pointer_claims(self._guide_bullet()), [])

    def test_dev_twins_match_templates(self):
        """Line 3 and the guide bullet stay in lock-step with their dev twins.

        The template parity manifest anchors no clause before section 1, so
        its checker cannot see line 3; this test does.
        """
        require_dev_surface(self, TREE_AGENTS_MD, f"{TREE}/AGENTS.md")
        self.assertEqual(_line_3(self.TEMPLATE).replace("{{repo_name}}", "crux"),
                         _line_3(TREE_AGENTS_MD))
        # A staged artifact's root USER_GUIDE.md is the public rendition, which
        # regenerates at release; only the dev checkout holds the twin.
        if IS_STAGED_ARTIFACT:
            self.skipTest("root USER_GUIDE.md is the public rendition in a staged artifact")
        guide = REPO_ROOT / "USER_GUIDE.md"
        require_dev_surface(self, guide, "USER_GUIDE.md")
        text = guide.read_text(encoding="utf-8")
        self.assertEqual(_root_pointer_claims(text), [])
        self.assertIn(self._guide_bullet(), text.splitlines())

    # -- overview, rollback contract, checklist, red flags ---------------------

    def test_overview_rollback_and_red_flags_carry_the_create_rule(self):
        text = " ".join(self._skill_text().split())
        for pinned in (
                "Step 9 creates a repo-root `AGENTS.md` when the root holds no `AGENTS.md` "
                "or `CLAUDE.md` in any letter case. A `CLAUDE.local.md` does not block that "
                "create. Step 9 appends to an exact `AGENTS.md` that exists, and it reports "
                "a `CLAUDE.md` or a case variant of `AGENTS.md` with its remedy instead of "
                "writing to it.",
                "The repo-root `AGENTS.md` that step 9 created is a new path. Remove it only "
                "through step 9's guarded rollback, never by path. That rollback deletes the "
                "file only when it still finds the regular file this run created.",
                "The one sanctioned create is step 9's, for a root that holds no `AGENTS.md` "
                "or `CLAUDE.md` in any letter case.",
                "About to create a repo-root `AGENTS.md` beside a `CLAUDE.md` or a case "
                "variant of `AGENTS.md`, to append to a case variant, or to write through a "
                "symlink."):
            with self.subTest(pinned=pinned[:50]):
                self.assertIn(pinned, text)

    def test_checklist_items_name_the_created_file_the_append_and_the_report(self):
        lines = self._skill_text().splitlines()
        for item in (
                "- [ ] If the repo root held no `AGENTS.md` or `CLAUDE.md` in any letter case, "
                "step 9 created a repo-root `AGENTS.md` holding the pointer line and the "
                "objectives block, and its NOTE was surfaced verbatim.",
                "- [ ] If the repo root carries an exact `AGENTS.md` that is a regular UTF-8 "
                "file, step 9 appended the tree reference and the objectives block where "
                "each was missing, preserving existing content and generated regions; no "
                "pre-existing content was rewritten.",
                "- [ ] Every repo-root entry step 9 reported (a `CLAUDE.md`, a case variant "
                "of `AGENTS.md`, or an `AGENTS.md` it could not write to) appears in the "
                "WARNING with the sentence for its code, and was left unedited."):
            with self.subTest(item=item[:60]):
                self.assertIn(item, lines)
        text = self._skill_text()
        self.assertNotIn("the four root files", text)
        self.assertEqual(text.count("If it carries none"), 0)

    # -- the NOTE and the WARNING ----------------------------------------------

    def test_note_is_verbatim_and_derives_its_facts_from_check_claude_compat(self):
        step9 = self._raw_step9()
        self.assertEqual(_text_fence_problems(step9, "NOTE:", _expected_note()), [])
        cc = _claude_compat()
        self.assertEqual((cc.VERSION_FLOOR, cc.EXCLUDED_DISTRIBUTIONS),
                         ((2, 1, 277), ("bedrock", "vertex", "foundry")))

    def test_positive_control_note_check_is_not_vacuous(self):
        step9 = self._raw_step9()
        for old, new in (
                ("from version 2.1.277", "from version 2.1.278"),
                ("Bedrock, Vertex and Foundry", "Bedrock and Vertex"),
                ("run check-claude-compat.", "run the compatibility check."),
                ("so init-docs created a repo-root AGENTS.md.", "so init-docs added nothing."),
                ("CLAUDE.md, .claude/CLAUDE.md and CLAUDE.local.md",
                 "CLAUDE.md and CLAUDE.local.md")):
            with self.subTest(old=old):
                mutated = step9.replace(old, new, 1)
                self.assertNotEqual(mutated, step9)
                self.assertNotEqual(
                    _text_fence_problems(mutated, "NOTE:", _expected_note()), [])

    def test_warning_is_verbatim_with_two_leads_and_one_sentence_per_code(self):
        step9 = self._raw_step9()
        see, objectives = self._step9_appends(step9)
        self.assertEqual(
            _text_fence_problems(step9, "WARNING:", _expected_warning(see, objectives)), [])
        flat = " ".join(step9.split())
        for lead in ("`init-docs created no repo-root AGENTS.md and appended no pointer to "
                     "the documentation tree.`",
                     "`init-docs left the repo-root entries below unchanged.`"):
            self.assertIn(lead, flat)
        self.assertIn("Drop the closing block, from \"When every entry above\" on, when "
                      "`\"outcome\"` is `appended` or `unchanged`.", flat)

    def test_positive_control_warning_check_is_not_vacuous(self):
        step9 = self._raw_step9()
        see, objectives = self._step9_appends(step9)
        want = _expected_warning(see, objectives)
        for old, new in (
                ("WARNING: <lead>", "WARNING: nothing was created."),
                (", .claude/CLAUDE.md and CLAUDE.local.md", " and CLAUDE.local.md"),
                ("default settings, except on the Bedrock", "default settings, including on the Bedrock"),
                ("and create that file if it does not exist:", "and never create that file:"),
                ("- <entry name>: <sentence for its code>", "- <entry name>"),
                ("When every entry above is resolved", "When one entry above is resolved")):
            with self.subTest(old=old):
                # The WARNING fence is the last text fence, so mutate the last occurrence:
                # the first one may sit in the NOTE, which this check does not read.
                head, sep, tail = step9.rpartition(old)
                self.assertTrue(sep, old)
                mutated = head + new + tail
                self.assertNotEqual(_text_fence_problems(mutated, "WARNING:", want), [])
        no_fence = re.sub(r"```text\nWARNING:.*?\n```\n", "", step9, flags=re.DOTALL)
        self.assertNotEqual(no_fence, step9)
        self.assertNotEqual(_text_fence_problems(no_fence, "WARNING:", want), [])

    def test_the_warning_and_note_are_true_for_every_harness(self):
        """No sentence of the NOTE, the WARNING or the table claims a host observation."""
        step9 = self._raw_step9()
        blocks = [b for lang in ("text",) for b in _fence_bodies(step9, lang)]
        blocks.append("\n".join(_remedy_rows(step9).values()))
        joined = "\n".join(blocks).lower()
        for banned in ("observed", "verified that", "confirmed that", "we tested"):
            self.assertNotIn(banned, joined)
        # The three harnesses: the NOTE names the two AGENTS.md readers by name.
        self.assertIn("codex and opencode read a repo-root agents.md.", joined)

    # -- the remedy table ------------------------------------------------------

    def test_remedy_table_carries_exactly_the_codes_the_helper_emits(self):
        rows = _remedy_rows(self._raw_step9())
        codes = _root_agents().REMEDY_CODES
        self.assertEqual(set(rows), set(codes))
        for code, sentence in rows.items():
            with self.subTest(code=code):
                self.assertTrue(sentence.endswith("."), sentence)
                self.assertNotIn("${", sentence)

    def test_positive_control_remedy_parity_fails_on_a_dropped_or_added_row(self):
        step9 = self._raw_step9()
        codes = set(_root_agents().REMEDY_CODES)
        dropped = re.sub(r"^\| `migrate-set-aside` \|.*\n", "", step9, flags=re.MULTILINE)
        self.assertNotEqual(dropped, step9)
        self.assertNotEqual(set(_remedy_rows(dropped)), codes)
        added = step9.replace("| `migrate` |", "| `migrate-all` | Do it. |\n| `migrate` |", 1)
        self.assertNotEqual(set(_remedy_rows(added)), codes)

    def test_remedy_sentences_never_say_migration_converts_what_it_does_not(self):
        """Clause 4: a not-applicable or blocked code says the remedy does not convert it."""
        rows = _remedy_rows(self._raw_step9())
        for code in ("not-applicable:symlink", "not-applicable:not-regular-file",
                     "not-applicable:no-git"):
            self.assertIn("does not apply", rows[code], code)
        for code in ("track-then-migrate", "unlist-then-migrate",
                     "track-and-unlist-then-migrate", "resolve-family-then-migrate",
                     "fix-agents-then-migrate", "resolve-partner-then-migrate"):
            self.assertIn("then run audit-docs --migrate", rows[code], code)
        # POSITIVE CONTROL: the sentence for a plain convertible entry carries no precondition.
        self.assertNotIn("then run", rows["migrate"])

    # -- the helper reads its blocks from step 9 -------------------------------

    def test_step9_holds_exactly_the_two_markdown_fences_the_helper_reads(self):
        step9 = self._raw_step9()
        see, objectives = self._step9_appends(step9)
        blocks = _root_agents().load_blocks(self.SKILL_MD, "bionic")
        self.assertEqual(blocks.pointer, see.replace("${DOCS_DIR}", "bionic"))
        self.assertEqual(blocks.objectives,
                         "\n".join(objectives).replace("${DOCS_DIR}", "bionic"))
        # POSITIVE CONTROL: a third markdown fence in step 9 makes the helper refuse.
        extra = self._skill_text().replace("### 10.", "```markdown\nx\n```\n\n### 10.", 1)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "SKILL.md"
            bad.write_text(extra, encoding="utf-8")
            with self.assertRaises(_root_agents().CapabilityError):
                _root_agents().load_blocks(bad, "bionic")

    def test_step9_invokes_the_helper_verbs_it_documents(self):
        step9 = self._step9()
        self.assertIn('uv run "${CRUX_PLUGIN_ROOT}/scripts/root_agents.py" apply --repo-root '
                      '"${REPO_ROOT}" --docs-dir "${DOCS_DIR}" --record "${RECORD}"', step9)
        self.assertIn('uv run "${CRUX_PLUGIN_ROOT}/scripts/root_agents.py" rollback '
                      '--repo-root "${REPO_ROOT}" --record "${RECORD}"', step9)
        self.assertIn("Exit `2` is a capability error. The helper wrote nothing to the root "
                      "unless its stderr says an append failed part-way or a created "
                      "`AGENTS.md` was kept.", step9)
        # The record is private and fresh, and rollback follows only a create.
        self.assertIn("Create a private directory with `mktemp -d`", step9)
        self.assertIn("only when this run's `\"outcome\"` was `created`", step9)
        # The rollback contract restores an appended file's prior content, so step 9
        # records that content before the helper can append to it.
        self.assertIn("record its prior content before you run the helper", step9)

    # -- template line 3 quotes step 9's appends, true for any docs_dir --------

    def test_line_3_states_the_create_rule_and_quotes_both_appends(self):
        """Template line 3 states the create rule and quotes the appends as step 9 writes them.

        Step 9 appends `${DOCS_DIR}`, which is `bionic` by default. A literal
        `docs/` in the quote is false for every tree init-docs creates.
        """
        see, objectives = self._step9_appends(self._raw_step9())
        line3 = _line_3(self.TEMPLATE)
        self.assertIn(_LINE_3_CREATE_RULE, line3)
        # Only the create condition and the never-written list name a CLAUDE.md.
        self.assertNotIn("CLAUDE.md", _without_sanctioned_claude_mentions(line3))
        self.assertEqual(_line_3_quote_problems(line3, see, objectives[0]), [])
        # POSITIVE CONTROLS: the pre-fix line 3 lacks the create rule, and the older
        # one quoted `docs/` and fails the quote check.
        self.assertNotIn(_LINE_3_CREATE_RULE, _PRE_FIX_LINE_3_NO_CREATE)
        self.assertGreaterEqual(
            len(_line_3_quote_problems(_PRE_FIX_DOCS_LINE_3, see, objectives[0])), 2)

    def test_dev_twin_line_3_quotes_both_step9_appends(self):
        require_dev_surface(self, TREE_AGENTS_MD, f"{TREE}/AGENTS.md")
        see, objectives = self._step9_appends(self._raw_step9())
        self.assertEqual(_line_3_quote_problems(_line_3(TREE_AGENTS_MD), see, objectives[0]),
                         [])

    def test_the_guide_bullet_states_the_create_rule(self):
        bullet = self._guide_bullet()
        self.assertIn("When the repository root holds no `AGENTS.md` or `CLAUDE.md` in any "
                      "letter case, `init-docs` creates that file", bullet)
        self.assertIn("A `CLAUDE.local.md` does not block that create.", bullet)
        self.assertIn("which names `audit-docs --migrate` where that migration applies",
                      bullet)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class TemplateCommentAgreementTests(unittest.TestCase):
    """A template's COMMENTS must agree with its own parsed VALUES — the
    drift class that shipped this bug (a "currently == \"4\"" comment
    beside schema_version: "5") is invisible to yaml.safe_load alone.
    """

    def test_manifest_tmpl_comments_agree_with_its_own_value(self):
        raw = (TEMPLATES / "manifest.yml.tmpl").read_text()
        v = yaml.safe_load(raw)["schema_version"]
        for m in re.finditer(r'currently == "(\d+)"', raw):
            self.assertEqual(
                m.group(1), v,
                "manifest.yml.tmpl comment contradicts its own schema_version",
            )

    def test_dot_crux_tmpl_comments_do_not_claim_docs_default(self):
        raw = (TEMPLATES / "dot-crux.tmpl").read_text()
        self.assertNotIn('Default: "docs"', raw)
        self.assertNotIn("docs tree at ./docs/", raw)
        self.assertIn("defaults to `bionic`", raw)

    def test_bionic_yml_tmpl_comments_agree_with_its_own_docs_dir(self):
        raw = (TEMPLATES / "bionic-yml.tmpl").read_text()
        self.assertNotIn("ledger at ./docs/", raw)
        docs_dir = yaml.safe_load(raw)["docs_dir"]
        self.assertIn(docs_dir, raw.split("docs_dir:", 1)[1])


def _frontmatter_keys(text: str) -> list[str]:
    """Top-level keys of a `---`-fenced frontmatter block, in file order."""
    parts = text.split("---\n")
    assert len(parts) >= 3, "missing a --- delimited frontmatter block"
    return re.findall(r"^([A-Za-z_][A-Za-z0-9_]*):", parts[1], re.MULTILINE)


def _section_17_1_keyset(claude_md: str) -> list[str]:
    """The backticked first-column tokens of the §17.1 table, in table order."""
    i = claude_md.index("### 17.1 ")
    j = claude_md.index("### 17.2 ", i)
    rows = re.findall(r"^\| `([a-z_]+)` \|", claude_md[i:j], re.MULTILINE)
    assert rows, "§17.1 table rows not found"
    return rows


class ObservationsConcernTests(unittest.TestCase):
    """The observations concern is enabled additively (docs/AGENTS.md §17):
    the template carries the §17.1 keyset one-for-one, the dogfood + template
    manifests carry the concern and its counter with NO schema_version bump,
    and init-docs eagerly creates the concern's surfaces.
    """

    OBS_TEMPLATE = TEMPLATES / "OBS-template.md"
    SKILL_MD = REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md"

    def test_obs_template_keyset_matches_section_17_1_in_order(self):
        require_dev_surface(self, TREE_AGENTS_MD, f"{TREE}/AGENTS.md")
        canonical = _section_17_1_keyset(TREE_AGENTS_MD.read_text())
        self.assertTrue(self.OBS_TEMPLATE.exists(), "crux/templates/OBS-template.md missing")
        self.assertEqual(_frontmatter_keys(self.OBS_TEMPLATE.read_text()), canonical)

    def test_obs_template_points_at_the_schema_and_restates_nothing(self):
        # §11.D rule 4: the template names §17.1 as the source of truth and
        # carries no copy of the field semantics (no `required`, no enum list).
        t = self.OBS_TEMPLATE.read_text()
        self.assertIn("§17.1", t)
        self.assertNotRegex(t, r"(?i)\brequired\b")
        self.assertNotIn("observed | ratified", t)

    def test_obs_template_body_skeleton_and_no_code_excerpt(self):
        t = self.OBS_TEMPLATE.read_text()
        for heading in (
            "# OBS-NNNN — <title>",
            "## What the code does",
            "## Evidence",
            "## Why this is observed, not decided",
        ):
            self.assertIn(heading, t)
        self.assertIn("path:line-range", t)
        self.assertNotIn("```", t, "no code excerpt anywhere in the template")

    def test_obs_template_is_a_clean_distributed_surface(self):
        t = self.OBS_TEMPLATE.read_text()
        self.assertNotRegex(t, r"ADR-\d{4}")
        self.assertNotIn("[[adrs/", t)

    @unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
    def test_dogfood_manifest_enables_observations_without_schema_bump(self):
        path = REPO_ROOT / TREE / "manifest.yml"
        require_dev_surface(self, path, f"{TREE}/manifest.yml")
        m = yaml.safe_load(path.read_text())
        self.assertIn("observations", m["concerns_enabled"])
        # The two counters are monotonic and move the first time the concern is
        # used — the dogfood tree scaffolded SVY-0001 on 2026-08-31 — so the
        # block is pinned by key set and thresholds, never by a counter's value.
        obs = m["observation"]
        self.assertEqual(set(obs), {"next_number", "stale_days", "next_survey_number", "survey_stub_days"})
        self.assertEqual((obs["stale_days"], obs["survey_stub_days"]), (90, 1))
        for key in ("next_number", "next_survey_number"):
            self.assertIsInstance(obs[key], int)
            self.assertGreaterEqual(obs[key], 1, key)
        self.assertEqual(m["schema_version"], "5")

    def test_dogfood_observations_index_count_matches_disk(self):
        path = REPO_ROOT / TREE / "observations" / "index.md"
        require_dev_surface(self, path, f"{TREE}/observations/index.md")
        t = path.read_text()
        # The count is read from disk, never pinned: the dogfood tree published
        # its first batch (SVY-0001, three records) on 2026-08-31.
        n = len(list((REPO_ROOT / TREE / "observations").glob("OBS-*.md")))
        self.assertIn(f"## Records ({n})", t)
        self.assertIn("§17", t)
        self.assertIn("visibly marked", t)

    def test_dogfood_master_index_carries_observations_between_invariants_and_arch(self):
        path = REPO_ROOT / TREE / "index.md"
        require_dev_surface(self, path, f"{TREE}/index.md")
        t = path.read_text()
        i = t.index("## Invariants (")
        # The count is DERIVED, never frozen: this test asserts the section's
        # POSITION, and freezing `(0)` made it fail the moment the tree
        # ratified its first observation — a true tree reported as a defect.
        m = re.search(r"^## Observations \(\d+\)$", t, re.M)
        self.assertIsNotNone(m, "master index carries no ## Observations section")
        o = m.start()
        a = t.index("## Arch")
        self.assertTrue(i < o < a, "## Observations must sit after Invariants and before Arch")

    def test_init_docs_skill_creates_observations_surfaces_eagerly(self):
        t = self.SKILL_MD.read_text()
        self.assertIn("${DOCS_DIR}/observations/index.md", t)
        self.assertIn("Records (0)", t)
        self.assertIn("## Observations (0)", t)
        self.assertIn("observation.next_number: 1", t)
        self.assertRegex(t, r"concerns_enabled: \[[^\]]*observations[^\]]*\]")
        self.assertIn("`concerns_enabled` includes `invariants`, `arch`, and `observations`", t)

    def test_init_docs_skill_adds_no_migration_rung_and_no_schema_bump(self):
        t = self.SKILL_MD.read_text()
        # The only `currently "X"` literal is the step-6 one, still "5".
        self.assertEqual(re.findall(r'currently `"([^"]+)"`', t), ["5"])
        i = t.index("Observations concern")
        block = t[i : i + 900]
        self.assertNotRegex(block, r"(?i)migrat")


class InstallDocsSkillsProseTests(unittest.TestCase):
    """The sibling detection surface: fresh-vs-upgrade detection must not
    key on the retired literal `docs/` absence (a bionic/-only repo is an
    existing installation)."""

    SKILL_MD = REPO_ROOT / "crux" / "skills" / "install-docs-skills" / "SKILL.md"
    DETAIL_MD = SKILL_MD.parent / "references" / "install-and-upgrade.md"

    def test_detection_never_keys_on_literal_docs_absence(self):
        self.assertIn("references/install-and-upgrade.md", self.SKILL_MD.read_text())
        t = self.DETAIL_MD.read_text()
        self.assertNotIn(
            "If `docs/` does NOT exist in the target repo (fresh install)", t)
        for marker in ("`.bionic.yml`", "`bionic/` tree", "legacy `docs/` tree"):
            self.assertIn(marker, t, f"detection marker {marker} missing")


class UserGuideSchemaLiteralTests(unittest.TestCase):
    """USER_GUIDE 'this project uses schema_version N' claims must match the
    shipped manifest template's value (the class that recurred across two
    bumps — a guide saying "3" into the v5 era)."""

    @unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
    def test_user_guides_name_the_current_schema_version(self):
        v = yaml.safe_load((TEMPLATES / "manifest.yml.tmpl").read_text())["schema_version"]
        for f in (REPO_ROOT / "USER_GUIDE.md", TEMPLATES / "USER_GUIDE.md"):
            self.assertIn(f"schema_version {v}", f.read_text(),
                          f"{f.name} does not name schema_version {v}")


class ReviewsSurfaceAtInitTests(unittest.TestCase):
    """`init-docs` creates the decision-review surface before the first review.

    Without it a fresh tree reaches the CLN-ADR-5 cadence nudge with nowhere
    to write (review finding `adr-review-init-docs-omits-reviews`).
    """

    def setUp(self) -> None:
        self.text = (REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md").read_text(encoding="utf-8")

    def test_the_directory_tree_names_adrs_reviews(self):
        self.assertIn("adrs/reviews/", self.text)

    def test_the_gitkeep_list_and_the_checklist_carry_it(self):
        keep = self.text[self.text.index("Add a `.gitkeep` file to each leaf directory"):]
        self.assertIn("`adrs/reviews/`", keep.split("\n", 1)[0])
        self.assertIn("`${DOCS_DIR}/adrs/reviews/`", self.text[self.text.index("## Verification checklist"):])


if __name__ == "__main__":
    unittest.main()


class FreshRepositoryInstructionNameTests(unittest.TestCase):
    """A fresh initialization yields ONE managed instruction filename.

    The failure this pins: a tree seeded with both names. Under the host's default
    mode the Claude-named sibling suppresses the canonical file outright, so a
    "harmless extra copy" silences the tree rather than duplicating it.
    """

    TEMPLATE = TEMPLATES / "AGENTS.md.tmpl"

    def test_the_init_template_is_named_for_the_canonical_file(self):
        self.assertTrue(self.TEMPLATE.exists(),
                        "init-docs seeds the tree schema from AGENTS.md.tmpl")
        self.assertFalse((TEMPLATES / "CLAUDE.md.tmpl").exists(),
                         "the legacy template name must not survive the migration")

    def test_the_template_names_no_claude_instruction_file_outside_section_18(self):
        """Section 18 names the legacy file deliberately; nothing else may."""
        text = self.TEMPLATE.read_text(encoding="utf-8")
        marker = "## 18. Repository instruction files"
        self.assertIn(marker, text, "the template carries the instruction contract")
        before = text.split(marker)[0]
        # Line 3 names a CLAUDE.md in exactly two sanctioned sentences.
        before = before.replace(
            "holds no `AGENTS.md` or `CLAUDE.md` in any letter case.", "").replace(
            "writes nothing to a `CLAUDE.md` in any letter case,", "")
        self.assertNotIn("CLAUDE.md", before,
                         "a CLAUDE.md reference survives outside section 18")

    def test_init_docs_writes_agents_md_and_forbids_a_claude_sibling(self):
        skill = (REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md").read_text()
        self.assertIn("${REPO_ROOT}/${DOCS_DIR}/AGENTS.md", skill)
        self.assertIn("Write no `CLAUDE.md` sibling", skill)
        self.assertNotIn("Write `${DOCS_DIR}/CLAUDE.md` from template", skill)

    def test_init_docs_reports_a_root_claude_md_rather_than_writing_to_it(self):
        skill = (REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md").read_text()
        flat = " ".join(skill.split())
        self.assertIn("It never renames, deletes or rewrites a `CLAUDE.md` or a variant.", flat)
        self.assertIn("The root holds a `CLAUDE.md` or a variant, with no exact `AGENTS.md`: it "
                      "creates nothing, appends nothing to that entry, and reports it.", flat)
        self.assertIn("audit-docs --migrate", skill)

    def test_the_verification_checklist_asserts_no_managed_claude_sibling(self):
        skill = (REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md").read_text()
        self.assertIn("No `${DOCS_DIR}/CLAUDE.md` sibling was written", skill)

    def test_the_parity_manifest_points_at_the_renamed_twin(self):
        manifest = json.loads(
            (REPO_ROOT / "crux" / "scripts" / "template_parity_manifest.json")
            .read_text(encoding="utf-8"))
        twins = {c["twin"] for c in manifest["clauses"]}
        self.assertIn("crux/templates/AGENTS.md.tmpl", twins)
        self.assertNotIn("crux/templates/CLAUDE.md.tmpl", twins)
        # The USER_GUIDE twin is a separate pair and is unaffected by the rename.
        self.assertTrue(twins <= {"crux/templates/AGENTS.md.tmpl",
                                  "crux/templates/USER_GUIDE.md"}, twins)
