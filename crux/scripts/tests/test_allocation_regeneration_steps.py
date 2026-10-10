"""Contract-text pins: number-allocating skills regenerate summaries then doctrine
after the counter bump, and transition-adr regenerates the derived ADR outputs
(including the arch spine) instead of hand-editing them."""

import re
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2] / "skills"

# skill -> heading prefix of the counter-increment step
ALLOCATORS = {
    "author-promptbook": "### 6b. Increment",
    "dev-cycle": "### 8. Increment",
    "iterate": "### 8. Increment",
    "patch-cycle": "### 7. Increment",
    "propose-adr": "### 5. Increment",
    "propose-observation": "### 6. Increment",
}


def read(name):
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def section(text, heading_prefix):
    start = text.index(heading_prefix)
    m = re.search(r"^### ", text[start + 3:], re.M)
    end = start + 3 + m.start() if m else len(text)
    return text[start:end]


class AllocatorRegenerationTests(unittest.TestCase):
    def test_summaries_then_doctrine_after_counter_increment(self):
        for name, anchor in ALLOCATORS.items():
            with self.subTest(skill=name):
                text = read(name)
                tail = text[text.index(anchor):]
                s = tail.find("summarize-adrs.py")
                d = tail.find("compile-doctrine.py")
                self.assertGreaterEqual(s, 0, "names summarize-adrs.py after the counter bump")
                self.assertGreater(d, s, "names compile-doctrine.py after summarize-adrs.py")

    def test_allocator_step_states_window_commit_and_dry_run(self):
        for name, anchor in ALLOCATORS.items():
            with self.subTest(skill=name):
                text = read(name)
                tail = text[text.index(anchor):]
                self.assertIn("--dry-run", tail)
                self.assertRegex(tail, r"tester'?s? window")
                self.assertNotIn("one commit", tail)


WINDOW_SENTENCE = (
    "While a tester's window is open, commit nothing and run no regenerator. The "
    "window runs from the tester's dispatch until the tester returns; outside a run "
    "there is none."
)
OLD_WINDOW_SENTENCE = "Run no regenerator while a tester's window is open."
PATHS_SENTENCE = (
    "Stage only these paths (`git add -- <paths>`) and commit only them "
    "(`git commit -- <paths>`), so no change already staged is included. If a "
    "regenerator still exits 2 with `migration-input-not-committed`, another "
    "uncommitted input is in the tree: stop and name it; never commit a file this "
    "skill did not write."
)
COMMIT_STEPS = {
    "propose-adr": "### 9. Commit, then regenerate",
    "transition-adr": "### 9. Commit, then regenerate",
    "propose-observation": "### 10. Commit, then regenerate",
}
BUMP_SENTENCE = "Never commit the counter bump without the file it allocated."
COMMIT_THEN_REGENERATE = "Commit, then regenerate"


class CommitBeforeRegenerationTests(unittest.TestCase):
    """A regenerator refuses an uncommitted migration input with exit 2, so each
    skill commits its own writes before it runs one."""

    def test_no_allocator_lands_the_bump_and_projections_in_one_commit(self):
        for name in ALLOCATORS:
            with self.subTest(skill=name):
                text = read(name)
                self.assertNotIn("Land the counter bump", text)
                self.assertNotIn("one commit", text[text.index(ALLOCATORS[name]):])
                self.assertIn(WINDOW_SENTENCE, text)
                self.assertNotIn("while the tester's window is open", text)
                self.assertIn(BUMP_SENTENCE, text)

    def test_commit_step_precedes_every_regenerator(self):
        steps = {
            "propose-adr": "### 9. ",
            "transition-adr": "### 9. ",
            "propose-observation": "### 10. ",
        }
        for name, number in steps.items():
            with self.subTest(skill=name):
                text = read(name)
                heading = number + COMMIT_THEN_REGENERATE
                h = text.index(heading)
                self.assertNotIn("summarize-adrs.py", text[:h])
                self.assertNotIn("compile-doctrine.py", text[:h])
                step = section(text, heading)
                self.assertLess(step.index("Commit "), step.index("summarize-adrs.py"))
                self.assertIn("migration-input-not-committed", step)
                self.assertIn("exit 2", step)
                self.assertIn("commit the regenerated projections", step)

    def test_guard_precedes_the_commit_and_every_regenerator(self):
        # The window guard covers commits, so it comes before the step's first commit.
        steps = dict(COMMIT_STEPS)
        steps.update({name: ALLOCATORS[name] for name in
                      ("author-promptbook", "dev-cycle", "iterate", "patch-cycle")})
        for name, heading in steps.items():
            with self.subTest(skill=name):
                text = read(name)
                self.assertNotIn(OLD_WINDOW_SENTENCE, text)
                step = section(text, heading)
                g = step.index(WINDOW_SENTENCE)
                self.assertLess(g, step.index("commit"))
                self.assertLess(g, step.index("summarize-adrs.py"))

    def test_commit_names_only_its_own_paths(self):
        # `git commit -- <paths>` alone refuses an untracked new file, so the step
        # stages the same paths first; a regenerator that still refuses is named.
        steps = dict(COMMIT_STEPS)
        steps.update({name: ALLOCATORS[name] for name in
                      ("author-promptbook", "dev-cycle", "iterate", "patch-cycle")})
        for name, heading in steps.items():
            with self.subTest(skill=name):
                step = section(read(name), heading)
                p = step.index(PATHS_SENTENCE)
                self.assertLess(p, step.index("summarize-adrs.py"))

    def test_pipeline_allocators_commit_before_regenerating(self):
        for name in ("author-promptbook", "dev-cycle", "iterate", "patch-cycle"):
            with self.subTest(skill=name):
                tail = read(name)
                tail = tail[tail.index(ALLOCATORS[name]):]
                c = tail.index("commit the allocated file")
                self.assertLess(c, tail.index("summarize-adrs.py"))
                self.assertIn("commit the regenerated projections", tail)

    def test_propose_adr_regenerates_last_and_in_order(self):
        text = read("propose-adr")
        self.assertNotIn("summarize-adrs.py", section(text, "### 5. Increment"))
        step = section(text, "### 9. " + COMMIT_THEN_REGENERATE)
        pos = [step.find(n) for n in (
            "generate-lineage.py", "generate-index-rollup.py", "summarize-adrs.py",
            "compile-doctrine.py", "derive-arch.py")]
        self.assertTrue(all(p >= 0 for p in pos), pos)
        self.assertEqual(pos, sorted(pos))
        self.assertNotIn("Skip only when the ADR has no `governs` block", step)
        self.assertLess(text.index("### 8."), text.index("summarize-adrs.py"))

    def test_propose_adr_step_8_leaves_the_adr_section_to_the_rollup(self):
        text = read("propose-adr")
        s8 = section(text, "### 8.")
        self.assertNotIn("Bump the count", s8)
        self.assertIn("Step 9 regenerates the ADR section", s8)
        self.assertNotIn("ADR section count matches", text)

    def test_propose_observation_regenerates_after_its_own_writes(self):
        text = read("propose-observation")
        self.assertNotIn("summarize-adrs.py", section(text, "### 6. Increment"))
        self.assertLess(text.index("### 9. Append to `docs/log.md`"),
                        text.index("summarize-adrs.py"))


class OrchestrationPointerTests(unittest.TestCase):
    """dev-cycle, iterate and run-promptbook name each orchestration rule."""

    CLAUSES = (
        "One designated tester runs the full suite; the dev-lead may run its own",
        "This section summarizes each rule; those sources carry the full text.",
        "reuse a matching passing record",
        "A run has one commit lane",
        "The tester's window runs from the tester's dispatch until the tester returns.",
        "A hand-back with work in flight states that first",
        "lists each running dispatch with its result file",
        "Developer worktrees start from the run's current HEAD or are rebased before integration",
        "An owner-exception record is written only from the owner's own instruction",
        "references/gates.md",
    )

    def test_each_skill_names_every_rule(self):
        for name in ("dev-cycle", "iterate", "run-promptbook"):
            with self.subTest(skill=name):
                text = read(name)
                body = section(text.replace("\n## ", "\n### "), "### Orchestration rules a run follows")
                for clause in self.CLAUSES:
                    self.assertIn(clause, body, clause)


class TransitionAdrTests(unittest.TestCase):
    def setUp(self):
        self.text = read("transition-adr")

    def test_section_6_regenerates_the_index(self):
        s6 = section(self.text, "### 6.")
        self.assertIn("generate-adr-index.py", s6)
        self.assertNotIn("Update `status` column", s6)

    def test_section_9_names_the_derived_generators_in_order(self):
        s9 = section(self.text, "### 9.")
        pos = [s9.find(n) for n in (
            "generate-lineage.py", "generate-index-rollup.py",
            "summarize-adrs.py", "compile-doctrine.py")]
        self.assertTrue(all(p >= 0 for p in pos), pos)
        self.assertEqual(pos, sorted(pos))

    def test_summaries_regenerate_on_every_transition(self):
        # The summaries input hash covers every active ADR's frontmatter, so a
        # transition of an ADR with no governs block still moves it.
        s9 = section(self.text, "### 9.")
        self.assertNotIn("Skip only when the ADR has no `governs` block", s9)
        self.assertNotIn("(when the ADR carries a `governs` block)", s9)
        self.assertIn("every active ADR's frontmatter", s9)

    def test_checklist_does_not_require_a_generated_index_date_line(self):
        checklist = self.text[self.text.index("## Verification checklist"):]
        self.assertNotIn("`docs/adrs/index.md` `_Last updated:_`", checklist)
        self.assertIn("generate-adr-index.py", checklist)

    def test_step_8_leaves_the_adr_count_to_the_rollup(self):
        s8 = section(self.text, "### 8.")
        self.assertNotIn("verify the count", s8)
        self.assertIn("Step 9 regenerates the ADR section", s8)

    def test_derive_arch_runs_last_because_it_reads_the_other_outputs(self):
        s9 = section(self.text, "### 9.")
        self.assertIn("refuses a stale input", s9)

    def test_checklist_covers_step_9_and_the_commit(self):
        checklist = self.text[self.text.index("## Verification checklist"):]
        for needle in ("generate-lineage.py --dry-run", "generate-index-rollup.py --dry-run",
                       "summarize-adrs.py --dry-run", "compile-doctrine.py --dry-run",
                       "derive-arch.py --dry-run", "committed"):
            self.assertIn(needle, checklist, needle)

    def test_derive_arch_is_last_and_conditioned(self):
        i9 = self.text.index("### 9.")
        body = self.text[i9:self.text.index("Hand off to the user")]
        d = body.find("derive-arch.py")
        self.assertGreaterEqual(d, 0)
        for earlier in ("generate-lineage.py", "generate-index-rollup.py",
                        "summarize-adrs.py", "compile-doctrine.py"):
            self.assertLess(body.rfind(earlier), d, earlier)
        window = body[max(0, d - 400):d + 400]
        self.assertIn("concerns_enabled", window)
        self.assertIn("Accepted", window)


if __name__ == "__main__":
    unittest.main()
