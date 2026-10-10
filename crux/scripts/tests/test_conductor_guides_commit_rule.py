"""No guide, skill, agent or template tells the conductor to commit the council record.

The council runner commits its own attempt record and its council record, so a
sentence such as "Commit each council record before you advance." is stale. The scan
collapses every run of whitespace, newlines and markdown line wraps included, to one
space, so a wrapped sentence cannot hide. It covers:

* `crux/skills/**/references/*.md` and `crux/skills/**/SKILL.md`
* every text file under `crux/templates/`
* `crux/agents/*.md`
* every repo-root `*.md` guide except `CHANGELOG.md`, whose dated entries record what
  earlier releases told the conductor.

The repo-root files are dev-only surfaces; their cases skip in the staged artifact
(`require_dev_surface`) and the `crux/` cases keep running there.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from _dev_surface import REPO_ROOT, require_dev_surface

# Copied from test_adr_path_templates.py (class CouncilRunnerRoundRuleTests,
# CONDUCTOR_COMMIT_COUNCIL), which owns the pattern; keep the two in step.
CONDUCTOR_COMMIT_COUNCIL = re.compile(
    r"\b[Cc]ommit (?:the|each|every|this|both) (?:new )?(?:council|attempt) records?\b")

CRUX = REPO_ROOT / "crux"
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".tmpl", ".txt", ".json"}


def norm(text: str) -> str:
    """Collapse every run of whitespace, line wraps included, to one space."""
    return re.sub(r"\s+", " ", text)


def hits(text: str) -> list[str]:
    return [m.group(0) for m in CONDUCTOR_COMMIT_COUNCIL.finditer(norm(text))]


def crux_files() -> list[Path]:
    found = set(CRUX.glob("skills/**/references/*.md"))
    found |= set(CRUX.glob("skills/**/SKILL.md"))
    found |= {p for p in (CRUX / "templates").rglob("*")
              if p.is_file() and p.suffix in TEXT_SUFFIXES}
    found |= set(CRUX.glob("agents/*.md"))
    return sorted(found)


def root_guides() -> list[Path]:
    return sorted(p for p in REPO_ROOT.glob("*.md") if p.name != "CHANGELOG.md")


class ScanControlTests(unittest.TestCase):

    def test_a_sentence_wrapped_across_lines_is_caught(self):
        self.assertEqual(hits("Commit each council\nrecord before you advance."),
                         ["Commit each council record"])
        self.assertEqual(hits("and then commit the\n  attempt records first"),
                         ["commit the attempt records"])

    def test_the_three_original_sentences_are_caught(self):
        for sentence in ("Commit each council record before you advance.",
                         "Commit each council record before you advance. The gate check refuses",
                         "Before a run's next gate prompt, store the key. Commit each council record before you advance."):
            with self.subTest(sentence=sentence):
                self.assertEqual(len(hits(sentence)), 1)

    def test_benign_sentences_are_not_caught(self):
        for fine in ("Commit the refutation record before the advance.",
                     "Commit both records before the advance.",
                     "The council runner commits its council record itself.",
                     "Do not commit either record by hand."):
            with self.subTest(fine=fine):
                self.assertEqual(hits(fine), [])


class ConductorCommitRuleTests(unittest.TestCase):

    def test_the_scan_reads_files(self):
        self.assertGreater(len(crux_files()), 50)

    def test_no_shipped_surface_tells_the_conductor_to_commit_the_council_record(self):
        stale = {str(p.relative_to(REPO_ROOT)): hits(p.read_text(encoding="utf-8"))
                 for p in crux_files()}
        self.assertEqual({k: v for k, v in stale.items() if v}, {})

    def test_no_repo_root_guide_tells_the_conductor_to_commit_the_council_record(self):
        require_dev_surface(self, REPO_ROOT / "CODEX.md", "CODEX.md")
        guides = root_guides()
        self.assertIn("CODEX.md", {p.name for p in guides})
        stale = {p.name: hits(p.read_text(encoding="utf-8")) for p in guides}
        self.assertEqual({k: v for k, v in stale.items() if v}, {})

    def test_neither_the_trees_instruction_file_nor_the_opencode_agents_tell_the_conductor_to(self):
        """`bionic/AGENTS.md` mirrors the AGENTS template for this repository's own runs, and
        `opencode/agents/` projects the agent definitions; a conductor reads both. Positive
        control: each file names the council runner, so the scan read real text."""
        tree_file = REPO_ROOT / "bionic" / "AGENTS.md"
        require_dev_surface(self, tree_file, "bionic/AGENTS.md")
        files = [tree_file, *sorted((REPO_ROOT / "opencode" / "agents").glob("*.md"))]
        self.assertGreater(len(files), 5)
        self.assertIn("council runner", tree_file.read_text(encoding="utf-8"))
        stale = {str(p.relative_to(REPO_ROOT)): hits(p.read_text(encoding="utf-8")) for p in files}
        self.assertEqual({k: v for k, v in stale.items() if v}, {})


# A sentence that sends a council runner exit 2 to recovery. Each must name the owner's remedy
# (a `timeout` or moved work: restore the set-aside work, then a stale `index.lock`) before its
# first recovery step, so the conductor's one next step after such an exit 2 is the owner stop.
EXIT2_TRIGGER = re.compile(r"\b(?:When the council runner exits 2|When it exits 2|[Oo]n exit 2)\b")
RECOVERY_STEP = re.compile(r"process check|--recover|\bruns? recovery\b")
#: How far after a trigger its recovery step may sit and still belong to it.
EXIT2_WINDOW = 700
#: Wording the remedy sweep retired: a fixer that only touches files outside the run directory
#: never fails a council commit, the lock lives in the git directory (which need not be `.git`),
#: and the remedy sentence was split and reworded.
RETIRED_REMEDY_WORDING = ("end-of-file fixer", "trailing-whitespace fixer", "stale `.git/index.lock`",
                          "names work outside the commit that moved", "work outside it that moved",
                          "who first restores", "hook that rewrites files")
#: The surfaces that route a council runner exit 2 in their own words. The scan must check at
#: least one trigger in each, so a reworded trigger cannot make the check vacuous.
EXIT2_SURFACES = ("CODEX.md", "OPENCODE.md", "OPENCODE_GUIDE.md", "USER_GUIDE.md", "bionic/AGENTS.md",
                  "crux/templates/AGENTS.md.tmpl", "crux/templates/USER_GUIDE.md",
                  "crux/skills/install-docs-skills/references/install-and-upgrade.md",
                  "crux/skills/dev-cycle/SKILL.md", "crux/skills/iterate/SKILL.md",
                  "crux/skills/patch-cycle/SKILL.md", "crux/agents/commander.md", "crux/agents/dev-lead.md",
                  "crux/agents/reviewer.md", "opencode/agents/commander.md")


def exit2_sites(text: str) -> list[tuple[str, bool]]:
    """Each trigger with a recovery step in its window, and whether the owner's remedy comes first."""
    t = norm(text)
    out = []
    for m in EXIT2_TRIGGER.finditer(t):
        r = RECOVERY_STEP.search(t, m.end(), m.end() + EXIT2_WINDOW)
        if r is None:
            continue
        between = t[m.start():r.start()]
        out.append((t[m.start():m.start() + 100], "`timeout`" in between and "index.lock" in between))
    return out


class Exit2RemedyOrderTests(unittest.TestCase):
    """After a council runner exit 2 whose stderr names `timeout` or moved work, every harness's
    guidance gives one next step: the owner's remedy, then recovery. The root guides (`CODEX.md`,
    `OPENCODE.md`, `OPENCODE_GUIDE.md`) and the user guides are pinned with the shipped surfaces."""

    def surfaces(self) -> dict[str, str]:
        require_dev_surface(self, REPO_ROOT / "CODEX.md", "CODEX.md")
        require_dev_surface(self, REPO_ROOT / "bionic" / "AGENTS.md", "bionic/AGENTS.md")
        files = set(crux_files()) | set(root_guides()) | {REPO_ROOT / "bionic" / "AGENTS.md"}
        files |= set((REPO_ROOT / "opencode" / "agents").glob("*.md"))
        return {str(p.relative_to(REPO_ROOT)): p.read_text(encoding="utf-8") for p in sorted(files)}

    def test_every_exit_2_route_names_the_owners_remedy_before_recovery(self):
        texts = self.surfaces()
        late = {name: [s for s, ok in exit2_sites(text) if not ok] for name, text in texts.items()}
        self.assertEqual({k: v for k, v in late.items() if v}, {})
        for name in EXIT2_SURFACES:
            with self.subTest(surface=name):
                self.assertTrue(exit2_sites(texts[name]), "no exit-2 route checked, so nothing was")

    def test_no_surface_keeps_the_retired_remedy_wording(self):
        stale = {name: [w for w in RETIRED_REMEDY_WORDING if w in norm(text)]
                 for name, text in self.surfaces().items()}
        self.assertEqual({k: v for k, v in stale.items() if v}, {})

    def test_control_the_order_check_reports_recovery_before_the_remedy(self):
        old = ("When the council runner exits 2 or leaves an open attempt, run the process check. "
               "A slow hook times out; restore the stash, then remove a stale `.git/index.lock`.")
        self.assertEqual([ok for _s, ok in exit2_sites(old)], [False])
        good = ("On exit 2 whose stderr names `timeout`, the owner restores the set-aside work and removes "
                "a stale `index.lock`. Then, and on every other exit 2, run the process check.")
        self.assertEqual([ok for _s, ok in exit2_sites(good)], [True])
        self.assertEqual(exit2_sites("On exit 2 (absent key), stop."), [])


if __name__ == "__main__":
    unittest.main()
