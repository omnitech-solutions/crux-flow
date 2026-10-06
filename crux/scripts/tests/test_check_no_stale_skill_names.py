"""Kebab-boundary behavior of check-no-stale-skill-names' default patterns.

A stale skill name embedded inside a longer kebab token (the path slugs
cleanup-campsite mints, e.g. a kebabized test filename) is NOT a reference to
the skill and must not match; genuine references (backticked, path segments,
plain prose) must keep matching. The probe name is built from parts so this
file never contains a banned literal (the checker's own self-allowlist rule).
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

try:
    from ._dev_surface import IS_STAGED_ARTIFACT, REPO_ROOT
except ImportError:  # unittest discover imports test modules top-level
    from _dev_surface import IS_STAGED_ARTIFACT, REPO_ROOT

_SCRIPT = Path(__file__).resolve().parents[1] / "check-no-stale-skill-names.py"
_RENAME_MAPPING = REPO_ROOT / "tools" / "rename-skills.py"

# The checker imports its rename mapping from tools/rename-skills.py at
# module load, and tools/ never crosses the sync boundary (ADR-0036 §3). The
# checker script itself is ALSO excluded from the crux/** staging grant via
# tools/sync_stage.py's DENY_FILES (a shipped copy would hard-crash on import
# for anyone outside this dev repo). Either absence is independently
# sufficient reason to skip: checking BOTH (rather than only the checker
# script, which is what the DENY_FILES mechanism happens to remove today)
# means this guard keeps working even if the exclusion mechanism changes —
# tools/rename-skills.py not crossing is the older, independent invariant.
if IS_STAGED_ARTIFACT and (not _SCRIPT.is_file() or not _RENAME_MAPPING.is_file()):
    raise unittest.SkipTest(
        "check-no-stale-skill-names.py and/or its tools/rename-skills.py "
        "import target are dev-repo-only (ADR-0036 boundary) — sync.sh runs "
        "the checker dev-side against the staged root instead"
    )
# Belt-and-braces: outside a staged artifact the script should always be
# present (it's a tracked dev-repo file), but skip cleanly rather than
# raising a raw FileNotFoundError out of exec_module below if it somehow
# isn't. Note `spec_from_file_location` does NOT return None for a merely
# missing file — it happily builds a spec whose loader fails later at
# `exec_module` time — so an `assert _spec is not None` after this point
# would never fire on a missing-file condition; this is_file() check is the
# actual guard, not that assert.
if not _SCRIPT.is_file():
    raise unittest.SkipTest(f"checker script absent: {_SCRIPT}")
_spec = importlib.util.spec_from_file_location("check_no_stale_skill_names", _SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

_NAME = "crux-" + "spin"  # built from parts — never written literally here


class KebabBoundaryTests(unittest.TestCase):
    def _matches(self, line: str) -> bool:
        return any(p.search(line) for p in checker._patterns_for_name(_NAME))

    def test_embedded_kebab_slug_does_not_match(self) -> None:
        slug = "cleanup-CLN-ADR-3-ADR-0037-crux-scripts-tests-test-" + _NAME + "-py"
        self.assertFalse(self._matches("- id: " + slug))

    def test_backticked_reference_matches(self) -> None:
        self.assertTrue(self._matches("use the `" + _NAME + "` skill"))

    def test_path_segment_matches(self) -> None:
        self.assertTrue(self._matches("crux/skills/" + _NAME + "/SKILL.md"))

    def test_plain_prose_matches(self) -> None:
        self.assertTrue(self._matches("the " + _NAME + " orchestrator"))


# ── allowlist SCOPE: three tree surfaces exempt, everything else still red ──
#
# `{tree}/garden/**`, `{tree}/briefs/**` and the single file
# `{tree}/adrs/summaries/backfill-reviews.yml` are allowlisted. Each absence
# assertion below is paired with a positive control that seeds the IDENTICAL
# line at a NON-allowlisted path and proves the gate still turns red — so a
# green can never come from a malformed seed.

# One seed line, used verbatim at every probe path. Every pair of assertions
# in this class differs ONLY in the path the line is written to.
_SEED_LINE = "anchor: use the `" + _NAME + "` skill"


class AllowlistScopeTests(unittest.TestCase):
    def _scan_with_seed(self, rel: str) -> list[str]:
        """Build a throwaway root, write _SEED_LINE at `rel`, scan, return hits."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".bionic.yml").write_text(
                'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8"
            )
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_SEED_LINE + "\n", encoding="utf-8")
            return checker.scan(root)

    def _assert_red(self, rel: str) -> None:
        hits = self._scan_with_seed(rel)
        self.assertTrue(
            any(h.startswith(rel + ":") for h in hits),
            f"expected a stale hit at {rel}, got {hits!r}",
        )

    def _assert_green(self, rel: str) -> None:
        hits = self._scan_with_seed(rel)
        self.assertEqual([], hits, f"expected no stale hit at {rel}")

    # ── positive control for the whole class: the seed line IS detectable ──
    def test_seed_line_on_a_live_skill_surface_is_red(self) -> None:
        self._assert_red("crux/skills/some-skill/SKILL.md")

    def test_seed_line_on_a_live_agent_surface_is_red(self) -> None:
        self._assert_red("crux/agents/some-agent.md")

    # ── the decision-review size vocabulary: `cycle` as a size token ──
    def _scan_with_line(self, rel: str, line: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bionic.yml").write_text(
                'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(line + "\n", encoding="utf-8")
            return checker.scan(root)

    def test_size_vocabulary_prose_form_is_allowlisted_on_a_live_skill(self) -> None:
        line = "Size is one of `direct-fix`, `patch`, `cycle` or `ADR`, where `ADR` is any act whose write reaches an ADR file."
        self.assertEqual([], self._scan_with_line("crux/skills/review-decisions/SKILL.md", line))

    def test_size_vocabulary_table_form_is_allowlisted_on_the_template(self) -> None:
        line = "| <id> | Propose \\| Amend | OBJ-N | <act> | direct-fix \\| patch \\| cycle \\| ADR |"
        self.assertEqual([], self._scan_with_line("crux/templates/adr-review-template.md", line))

    def test_a_bare_cycle_reference_beside_the_vocabulary_is_still_red(self) -> None:
        # Positive control: the allowlist admits the vocabulary phrase only,
        # never the file. A stale reference on another line stays a hit.
        line = ("Size is one of `direct-fix`, `patch`, `cycle` or `ADR`.\n"
                "Then run the `cycle` skill to build it.")
        hits = self._scan_with_line("crux/skills/review-decisions/SKILL.md", line)
        self.assertEqual(1, len(hits), hits)
        self.assertIn(":2:", hits[0])

    # ── garden/**: allowlisted; its non-allowlisted sibling is not ──
    def test_garden_note_is_allowlisted(self) -> None:
        self._assert_green("bionic/garden/2026-06-25.md")

    def test_garden_lookalike_outside_the_tree_is_red(self) -> None:
        self._assert_red("garden/2026-06-25.md")

    # ── briefs/**: allowlisted; its non-allowlisted sibling is not ──
    def test_brief_body_is_allowlisted(self) -> None:
        self._assert_green("bionic/briefs/BRIEF-probe.md")

    def test_brief_lookalike_outside_the_tree_is_red(self) -> None:
        self._assert_red("briefs/BRIEF-probe.md")

    # ── the receipts manifest: ONE file, not the summaries directory ──
    def test_backfill_reviews_manifest_is_allowlisted(self) -> None:
        self._assert_green("bionic/adrs/summaries/backfill-reviews.yml")

    def test_other_summaries_file_is_still_red(self) -> None:
        self._assert_red("bionic/adrs/summaries/rule-table.yml")

    # ── the three globs did not widen to the tree or to adrs/ at large ──
    def test_tree_at_large_is_still_red(self) -> None:
        self._assert_red("bionic/observations/OBS-0001-probe.md")

    def test_adrs_at_large_is_still_red(self) -> None:
        self._assert_red("bionic/adrs/ADR-9999-probe.md")

    # ── legacy `docs` spelling of the tree gets the same three exemptions ──
    def test_legacy_docs_spelling_garden_is_allowlisted(self) -> None:
        self._assert_green("docs/garden/2026-06-25.md")

    def test_legacy_docs_spelling_adrs_at_large_is_still_red(self) -> None:
        self._assert_red("docs/adrs/ADR-9999-probe.md")

    # ── nested-checkout prune: pruned, and the SAME path unpruned is red ──
    def _scan_with_seed_and_nested_git(self, rel: str) -> list[str]:
        """As _scan_with_seed, but mark the seed's own directory a checkout."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".bionic.yml").write_text(
                'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8"
            )
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_SEED_LINE + "\n", encoding="utf-8")
            (target.parent / ".git").write_text("gitdir: ../elsewhere\n", encoding="utf-8")
            return checker.scan(root)

    def test_a_nested_checkout_is_pruned_from_the_walk(self) -> None:
        self.assertEqual([], self._scan_with_seed_and_nested_git("vendor/copy/SKILL.md"))

    def test_positive_control_the_same_path_without_a_git_entry_is_red(self) -> None:
        # Without the `.git` marker the identical seed at the identical path
        # turns the gate red, so the green above is the prune and not a
        # path the scanner never reached.
        self._assert_red("vendor/copy/SKILL.md")


if __name__ == "__main__":
    unittest.main()
