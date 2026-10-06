"""Round-trip tests for the CLN-RETRO-1 retrospective marker.

Operative guarantee (per ADR-0038 §3, §7, and the cadence cross-reference
section of retrospective/SKILL.md):

  (a) The heading template that log-work emits for a Retrospective journal
      entry MUST match the detection regex that cleanup-campsite/SKILL.md
      and retrospective/SKILL.md quote.  The test literal (RETRO_DETECTION_REGEX
      below) is the oracle: both SKILL.md surfaces MUST contain that canonical
      regex string verbatim — any drift between the test constant and a SKILL.md
      surface is what this test catches.

  (b) cleanup-campsite/SKILL.md MUST quote the identical regex string (lock-step
      with the retrospective skill once it exists).

  (c) The archived-op pattern used by CLN-RETRO-1 MUST match real headings
      in docs/log.md.

  (d) docs/AGENTS.md and crux/templates/AGENTS.md.tmpl MUST both carry
      `retro_due_runs` (the manifest key governing CLN-RETRO-1's threshold)
      and the `retro-cadence` category token (the §5.A cleanup category
      that CLN-RETRO-1 falls under).  These are the CLAUDE-surface mirrors
      of the operational contract embedded in the skill files.

  (e) retrospective/SKILL.md MUST contain the future-dated-marker validity
      rule and the same-day `journal |` corroboration rule (planted anchor
      guards against corrupt or adversarially-authored cadence anchors).
      log-work/references/journal.md MUST contain the body-lines-must-not-begin-with-`## [`
      rule (the prose validity guard whose necessity is documented in (f)).

  (f) A line whose content happens to start with `## [` DOES match the
      line-anchored detection regex — this documents exactly why (e)'s
      prose validity guards exist: the regex alone cannot distinguish a real
      heading from body text planted with the heading prefix, so the guards
      are load-bearing, not redundant.

Uses only stdlib (unittest, re, pathlib).  No mocks needed — everything
resolves from repo-relative paths so the test is cwd-independent.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from ._dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface
except ImportError:  # unittest discover imports test modules top-level
    from _dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface

# ─── repo paths ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CLEANUP_SKILL = REPO_ROOT / "crux" / "skills" / "cleanup-campsite" / "SKILL.md"
RETRO_SKILL = REPO_ROOT / "crux" / "skills" / "retrospective" / "SKILL.md"
LOG_WORK_SKILL = REPO_ROOT / "crux" / "skills" / "log-work" / "references" / "journal.md"
LOG_MD = REPO_ROOT / TREE / "log.md"
DOCS_CLAUDE_MD = TREE_AGENTS_MD
TMPL_CLAUDE_MD = REPO_ROOT / "crux" / "templates" / "AGENTS.md.tmpl"

# ─── canonical strings (single source of truth for THIS test) ────────────────
#
# Detection regex for Retrospective journal headings.
# This string MUST appear verbatim (as a raw Python string or equivalent)
# in both cleanup-campsite/SKILL.md and retrospective/SKILL.md — the test
# enforces that all three copies are byte-identical.
RETRO_DETECTION_REGEX: str = (
    r"^## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] learning \| Retrospective: "
)

# Heading template that log-work emits when recording a retrospective.
# The window-summary suffix is variable; the prefix is fixed.
RETRO_HEADING_TEMPLATE: str = "## [YYYY-MM-DD HH:MM] learning | Retrospective: "

# Pattern used by CLN-RETRO-1 to detect archived promptbook ops in log.md.
ARCHIVED_OP_REGEX: str = r"^## \[\d{4}-\d{2}-\d{2}\] promptbook \| archived "

# ─── CLAUDE-surface token constants ──────────────────────────────────────────
#
# The manifest key governing CLN-RETRO-1's threshold and the first-run window
# default.  Must appear in both CLAUDE-surface files.
RETRO_DUE_RUNS_TOKEN: str = "retro_due_runs"

# The §5.A cleanup category token for CLN-RETRO-1.  Must appear in both
# CLAUDE-surface files.
RETRO_CADENCE_TOKEN: str = "retro-cadence"

# ─── Planted-anchor guard substrings ─────────────────────────────────────────
#
# Distinctive substrings that pin the future-dated-marker validity rule in
# retrospective/SKILL.md (Phase 1, validity check #1).
RETRO_FUTURE_DATED_GUARD: str = "Not future-dated."

# Distinctive substring for the same-day journal-op corroboration rule
# (Phase 1, validity check #2).  The phrase is distinctive enough that it
# cannot collide with unrelated prose.
RETRO_SAME_DAY_JOURNAL_GUARD: str = "Corroborated by the operations log."

# Distinctive substring for the log-work body-line guard: body lines must not
# begin with `## [` because such a line is regex-indistinguishable from a real
# heading and would corrupt the window-detection anchor.
LOG_WORK_BODY_HEADING_GUARD: str = "Body lines must not begin with `## [`"


class TestRetroHeadingRoundTrip(unittest.TestCase):
    """(a) A concrete heading built from the template MUST match the regex."""

    def test_template_matches_detection_regex(self) -> None:
        """A real-looking Retrospective heading matches RETRO_DETECTION_REGEX."""
        regex = re.compile(RETRO_DETECTION_REGEX)
        # Construct a plausible heading from the template (substitute fixed
        # placeholder tokens with valid values; append a sample window summary).
        concrete_heading = "## [2026-06-11 14:30] learning | Retrospective: Q2 books — 3 archived"
        self.assertIsNotNone(
            regex.match(concrete_heading),
            msg=(
                f"Concrete heading built from the template did not match the "
                f"detection regex.\n  heading: {concrete_heading!r}\n"
                f"  regex:   {RETRO_DETECTION_REGEX!r}"
            ),
        )

    def test_template_prefix_is_regex_prefix(self) -> None:
        """The non-variable prefix of the template MUST be the literal start
        of what the regex matches — so the regex is anchored to the template."""
        # The template's literal portion (before the window-summary slot)
        # must itself match the regex when a trivial suffix is appended.
        regex = re.compile(RETRO_DETECTION_REGEX)
        heading_with_minimal_suffix = RETRO_HEADING_TEMPLATE + "X"
        # Replace placeholder tokens with valid values for matching.
        concrete = heading_with_minimal_suffix.replace(
            "YYYY-MM-DD", "2026-06-11"
        ).replace("HH:MM", "09:00")
        self.assertIsNotNone(
            regex.match(concrete),
            msg=(
                f"Template prefix with a trivial suffix did not match the "
                f"detection regex after placeholder substitution.\n"
                f"  concrete: {concrete!r}\n  regex:    {RETRO_DETECTION_REGEX!r}"
            ),
        )

    def test_body_mention_does_not_match(self) -> None:
        """A body-level mention of 'Retrospective:' without the heading prefix
        MUST NOT match (confirms the regex is anchored to the heading form)."""
        regex = re.compile(RETRO_DETECTION_REGEX)
        body_mention = "See the Retrospective: summary below."
        self.assertIsNone(
            regex.match(body_mention),
            msg=(
                f"Body mention of 'Retrospective:' incorrectly matched the "
                f"detection regex.\n  mention: {body_mention!r}\n"
                f"  regex:   {RETRO_DETECTION_REGEX!r}"
            ),
        )


class TestCleanupSkillContainsRetroRegex(unittest.TestCase):
    """(b) cleanup-campsite/SKILL.md MUST quote the identical regex string."""

    def _read_skill(self) -> str:
        self.assertTrue(
            CLEANUP_SKILL.exists(),
            msg=f"cleanup-campsite/SKILL.md not found at {CLEANUP_SKILL}",
        )
        return CLEANUP_SKILL.read_text(encoding="utf-8")

    def test_cleanup_skill_contains_retro_detection_regex(self) -> None:
        """The exact RETRO_DETECTION_REGEX string must appear in the skill."""
        text = self._read_skill()
        self.assertIn(
            RETRO_DETECTION_REGEX,
            text,
            msg=(
                f"cleanup-campsite/SKILL.md does not contain the exact "
                f"RETRO_DETECTION_REGEX string.\n"
                f"  expected to find: {RETRO_DETECTION_REGEX!r}"
            ),
        )

    def test_cleanup_skill_contains_archived_op_predicate(self) -> None:
        """The full ARCHIVED_OP_REGEX string must appear verbatim in the skill
        (CLN-RETRO-1 reads log.md headings matching the archived op form)."""
        text = self._read_skill()
        self.assertIn(
            ARCHIVED_OP_REGEX,
            text,
            msg=(
                f"cleanup-campsite/SKILL.md does not contain the exact "
                f"ARCHIVED_OP_REGEX string.\n"
                f"  expected to find: {ARCHIVED_OP_REGEX!r}"
            ),
        )


class TestRetrospectiveSkillContainsRetroRegex(unittest.TestCase):
    """(b) retrospective/SKILL.md MUST quote the identical regex string."""

    def test_retro_skill_exists(self) -> None:
        """retrospective/SKILL.md must exist; its absence is a regression."""
        self.assertTrue(
            RETRO_SKILL.exists(),
            msg=(
                f"retrospective/SKILL.md not found at {RETRO_SKILL}. "
                f"The skill has been landed; its absence is a regression."
            ),
        )

    def test_retro_skill_contains_detection_regex(self) -> None:
        """retrospective/SKILL.md must contain the exact RETRO_DETECTION_REGEX."""
        self.assertTrue(
            RETRO_SKILL.exists(),
            msg=f"retrospective/SKILL.md not found at {RETRO_SKILL}.",
        )
        text = RETRO_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            RETRO_DETECTION_REGEX,
            text,
            msg=(
                f"retrospective/SKILL.md does not contain the exact "
                f"RETRO_DETECTION_REGEX string.\n"
                f"  expected to find: {RETRO_DETECTION_REGEX!r}"
            ),
        )


class TestArchivedOpPatternAgainstLogMd(unittest.TestCase):
    """(c) The archived-op pattern MUST match real headings from docs/log.md."""

    def _read_log(self) -> str:
        require_dev_surface(self, LOG_MD, f"{TREE}/log.md")
        self.assertTrue(
            LOG_MD.exists(),
            msg=f"docs/log.md not found at {LOG_MD}",
        )
        return LOG_MD.read_text(encoding="utf-8")

    def test_archived_regex_matches_at_least_one_real_heading(self) -> None:
        """At least one line in docs/log.md must match ARCHIVED_OP_REGEX."""
        text = self._read_log()
        regex = re.compile(ARCHIVED_OP_REGEX, re.MULTILINE)
        matches = regex.findall(text)
        self.assertGreater(
            len(matches),
            0,
            msg=(
                f"ARCHIVED_OP_REGEX matched no lines in docs/log.md.\n"
                f"  regex: {ARCHIVED_OP_REGEX!r}\n"
                f"  Ensure the log contains at least one "
                f"'## [YYYY-MM-DD] promptbook | archived ...' heading."
            ),
        )

    def test_archived_regex_matches_known_concrete_example(self) -> None:
        """A known concrete heading from the live log must match."""
        # Taken from docs/log.md under the '## [2026-06-11] promptbook | archived' heading.
        concrete = (
            "## [2026-06-11] promptbook | archived "
            "PB-0031-forge-skill-and-spin-decommission"
        )
        regex = re.compile(ARCHIVED_OP_REGEX)
        self.assertIsNotNone(
            regex.match(concrete),
            msg=(
                f"Known concrete archived heading did not match ARCHIVED_OP_REGEX.\n"
                f"  heading: {concrete!r}\n  regex:   {ARCHIVED_OP_REGEX!r}"
            ),
        )

    def test_archived_regex_does_not_match_non_archived_promptbook_op(
        self,
    ) -> None:
        """A 'promptbook | authored' heading MUST NOT match — the predicate is
        specific to the 'archived' op form."""
        regex = re.compile(ARCHIVED_OP_REGEX)
        non_archived = "## [2026-06-11] promptbook | authored PB-0032-retrospective-skill (cycle)"
        self.assertIsNone(
            regex.match(non_archived),
            msg=(
                f"Non-archived promptbook op incorrectly matched ARCHIVED_OP_REGEX.\n"
                f"  heading: {non_archived!r}\n  regex:   {ARCHIVED_OP_REGEX!r}"
            ),
        )


_CLAUDE_SURFACES = [
    (DOCS_CLAUDE_MD, "bionic/AGENTS.md"),
    (TMPL_CLAUDE_MD, "crux/templates/AGENTS.md.tmpl"),
]


class TestClaudeSurfaceRetroTokens(unittest.TestCase):
    """(d) Both CLAUDE-surface files MUST carry the retro_due_runs manifest key
    and the retro-cadence category token — the operational contract for
    CLN-RETRO-1 is expressed in skill prose AND the CLAUDE-surface files that
    govern downstream installations.

    Follows the test_reflex_blocks precedent: surface-level structural tokens
    that are defined in both docs/AGENTS.md and crux/templates/AGENTS.md.tmpl
    are asserted on both surfaces simultaneously so drift between the two is
    caught immediately.
    """

    def test_retro_due_runs_present_in_both_claude_surfaces(self) -> None:
        """RETRO_DUE_RUNS_TOKEN must appear in both CLAUDE-surface files."""
        for path, label in _CLAUDE_SURFACES:
            with self.subTest(surface=label):
                if path == DOCS_CLAUDE_MD:
                    require_dev_surface(self, path, label)
                self.assertTrue(
                    path.exists(),
                    msg=f"{label} not found at {path}",
                )
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    RETRO_DUE_RUNS_TOKEN,
                    text,
                    msg=(
                        f"{label} does not contain the retro_due_runs manifest "
                        f"key token.\n  expected to find: {RETRO_DUE_RUNS_TOKEN!r}\n"
                        f"  This token governs CLN-RETRO-1's threshold and the "
                        f"first-run window default."
                    ),
                )

    def test_retro_cadence_category_present_in_both_claude_surfaces(self) -> None:
        """RETRO_CADENCE_TOKEN (the §5.A category) must appear in both surfaces."""
        for path, label in _CLAUDE_SURFACES:
            with self.subTest(surface=label):
                if path == DOCS_CLAUDE_MD:
                    require_dev_surface(self, path, label)
                self.assertTrue(
                    path.exists(),
                    msg=f"{label} not found at {path}",
                )
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    RETRO_CADENCE_TOKEN,
                    text,
                    msg=(
                        f"{label} does not contain the retro-cadence category "
                        f"token.\n  expected to find: {RETRO_CADENCE_TOKEN!r}\n"
                        f"  This is the §5.A cleanup category for CLN-RETRO-1."
                    ),
                )


class TestPlantedAnchorGuards(unittest.TestCase):
    """(e) retrospective/SKILL.md MUST contain the future-dated-marker validity
    rule and the same-day journal-op corroboration rule.
    log-work/references/journal.md MUST contain the body-line guard against `## [` prefixes.

    These are the prose validity guards that protect window detection from
    corrupt or adversarially-authored cadence anchors.  The regex alone cannot
    distinguish a real heading from planted body text (see TestRegexBehaviorDoc
    below), so the guards are load-bearing.
    """

    def test_retro_skill_contains_future_dated_guard(self) -> None:
        """retrospective/SKILL.md must contain the future-dated-marker rule."""
        self.assertTrue(
            RETRO_SKILL.exists(),
            msg=f"retrospective/SKILL.md not found at {RETRO_SKILL}.",
        )
        text = RETRO_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            RETRO_FUTURE_DATED_GUARD,
            text,
            msg=(
                f"retrospective/SKILL.md does not contain the future-dated "
                f"validity rule.\n  expected substring: {RETRO_FUTURE_DATED_GUARD!r}\n"
                f"  This guard rejects fabricated anchors dated in the future."
            ),
        )

    def test_retro_skill_contains_same_day_journal_guard(self) -> None:
        """retrospective/SKILL.md must contain the same-day journal-op corroboration rule."""
        self.assertTrue(
            RETRO_SKILL.exists(),
            msg=f"retrospective/SKILL.md not found at {RETRO_SKILL}.",
        )
        text = RETRO_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            RETRO_SAME_DAY_JOURNAL_GUARD,
            text,
            msg=(
                f"retrospective/SKILL.md does not contain the same-day "
                f"journal-op corroboration rule.\n"
                f"  expected substring: {RETRO_SAME_DAY_JOURNAL_GUARD!r}\n"
                f"  This guard requires a same-calendar-day `journal |` op in "
                f"docs/log.md to corroborate each anchor candidate."
            ),
        )

    def test_log_work_skill_contains_body_heading_guard(self) -> None:
        """The selected journal reference must guard against body headings."""
        self.assertTrue(
            LOG_WORK_SKILL.exists(),
            msg=f"log-work/references/journal.md not found at {LOG_WORK_SKILL}.",
        )
        text = LOG_WORK_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            LOG_WORK_BODY_HEADING_GUARD,
            text,
            msg=(
                f"log-work/references/journal.md lacks the body-line guard "
                f"against `## [` prefixes.\n"
                f"  expected substring: {LOG_WORK_BODY_HEADING_GUARD!r}\n"
                f"  A body line beginning with `## [` is regex-indistinguishable "
                f"from a real heading and would corrupt the window-detection "
                f"anchor used by retrospective and cleanup-campsite."
            ),
        )


class TestRegexBehaviorDoc(unittest.TestCase):
    """(f) Documents the regex limitation that makes (e)'s prose guards necessary.

    A line whose content starts with `## [` followed by the full heading
    pattern DOES match the line-anchored detection regex — even when that
    line appears in entry body text.  This is exactly why the prose validity
    guards in retrospective/SKILL.md and log-work/references/journal.md are load-bearing:
    the regex cannot distinguish a real heading from planted body text with
    the heading prefix.

    The assertion here is intentional: it asserts the MATCH (not a non-match).
    This pin documents the assumption the guards rest on.  If someone ever
    changes the regex to scope-match only at document structural positions,
    this test will fail, which is the correct signal to also re-evaluate
    whether the prose guards remain necessary.
    """

    def test_full_heading_line_in_body_position_matches_regex(self) -> None:
        """A planted full heading line (matching the detection regex) inside
        body text DOES match the line-anchored regex.

        This confirms: the regex alone cannot distinguish a real cadence
        anchor from an adversarially-planted heading-prefix line in a body.
        The prose validity guards (future-dated check, same-day journal-op
        check) are therefore the actual security controls, not the regex.
        """
        regex = re.compile(RETRO_DETECTION_REGEX)
        # This is a body line (not a real heading) that nonetheless starts
        # with the full heading prefix.  An attacker could plant this in a
        # journal entry body to shift the retrospective window.
        planted_body_line = (
            "## [2020-01-01 00:00] learning | Retrospective: "
            "fake anchor planted in body text"
        )
        self.assertIsNotNone(
            regex.match(planted_body_line),
            msg=(
                f"Expected planted body line to match the regex — this is the "
                f"documented vulnerability the prose guards close.\n"
                f"  planted line: {planted_body_line!r}\n"
                f"  regex:        {RETRO_DETECTION_REGEX!r}\n"
                f"  If the regex no longer matches this, re-evaluate whether "
                f"the prose validity guards in retrospective/SKILL.md and "
                f"log-work/references/journal.md are still necessary."
            ),
        )
