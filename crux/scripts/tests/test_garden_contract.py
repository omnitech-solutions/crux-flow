"""Contract tests for the garden log op and night-gardener surface.

Operative guarantees (per ADR-0039 §4, §5, and the SSOT mechanism defined by
ADR-0012):

  (a) The `garden` token appears in BOTH §6 SSOT regexes in BOTH
      docs/AGENTS.md and crux/templates/AGENTS.md.tmpl (4 assertions total:
      token-in-alternation check by extracting the regex lines from each file).

  (b) Both regexes still compile (re.compile) and match a synthetic heading
      of the canonical form:
        ## [2026-06-12] garden | morning note 2026-06-12 (3 headlines, 2 artifacts)

  (c) Lock-step: crux/skills/tend-garden/SKILL.md quotes the same op heading
      form (assert a distinctive substring like `] garden | morning note`),
      contains the four high_water field names (commit, dirty, log_head,
      journal_head), contains the op-before-note ordering marker (assert
      both ordering prose substrings appear), contains the exact kind enum as
      a contiguous pipe-delimited run, contains BOTH damping threshold lines,
      and has all high_water: occurrences validated (not just the first).

  (d) whiteboarding/SKILL.md contains the unattended-mode markers:
      "Unattended mode" heading, "ASSUMED:", and "mode: unattended".

  (e) The canonical reflex sentence appears exactly once in
      crux/agents/night-gardener.md (complements test_reflex_blocks).

Uses only stdlib (unittest, re, pathlib).  No mocks needed — everything
resolves from repo-relative paths so the test is cwd-independent.

Files tested by (a) and (b) (CLAUDE-surface pairs) must be kept in
lock-step; this test pins both.
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
DOCS_CLAUDE_MD = TREE_AGENTS_MD
TMPL_CLAUDE_MD = REPO_ROOT / "crux" / "templates" / "AGENTS.md.tmpl"
TEND_GARDEN_SKILL = REPO_ROOT / "crux" / "skills" / "tend-garden" / "SKILL.md"
WHITEBOARDING_SKILL = REPO_ROOT / "crux" / "skills" / "whiteboarding" / "SKILL.md"
NIGHT_GARDENER_AGENT = REPO_ROOT / "crux" / "agents" / "night-gardener.md"

# ─── canonical strings ───────────────────────────────────────────────────────

# The op token added to the §6 SSOT alternation groups.
GARDEN_TOKEN: str = "garden"

# Synthetic heading the regex must match (per ADR-0039 §5 heading form).
SYNTHETIC_HEADING: str = (
    "## [2026-06-12] garden | morning note 2026-06-12 (3 headlines, 2 artifacts)"
)

# Distinctive substring in tend-garden/SKILL.md pinning the op heading form.
TEND_GARDEN_HEADING_SUBSTR: str = "] garden | morning note"

# The four high_water field names the turn gate uses (ADR-0039 §2).
HIGH_WATER_FIELDS: tuple[str, ...] = ("commit", "dirty", "log_head", "journal_head")

# Op-before-note ordering markers in tend-garden/SKILL.md (ADR-0039 §5).
# The skill must contain both of these distinctive substrings, which pin
# the ordering contract (write the garden log op first, then write the note).
# Chosen to match the actual prose: the checklist line and the rationalization
# table entry that names the reversed-ordering failure mode.
OP_BEFORE_NOTE_MARKERS: tuple[str, str] = (
    "Garden log op was written before the note",
    "Op first, then re-read log head, then write note",
)

# Unattended-mode markers in whiteboarding/SKILL.md (ADR-0039 §7).
UNATTENDED_MODE_HEADING: str = "Unattended mode"
UNATTENDED_ASSUMED_MARKER: str = "ASSUMED:"
UNATTENDED_MODE_KEY: str = "mode: unattended"

# Canonical capability-gap reflex sentence that must appear exactly once in
# crux/agents/night-gardener.md.  This pins the "Doing something manually for
# the third time\u2026 invoke forge-skill" discipline that every crux agent embeds.
# Complements test_reflex_blocks (which checks structure); this checks that the
# sentence is present and unique.  We pin the most distinctive fragment \u2014
# brittle enough to detect deletion, stable enough to survive minor rewording.
NIGHT_GARDENER_REFLEX_SENTENCE: str = (
    "That's a capability gap \u2014 invoke the `forge-skill` skill"
)

# The never-push hard line \u2014 the load-bearing safety sentence in the Hard Lines
# section.  Deleting or rephrasing it to remove the prohibition must fail a
# test.  Pins the exact opening fragment that uniquely identifies the sentence.
NEVER_PUSH_HARD_LINE: str = "Never push or merge."

# The no-secret-values safety line.  Deleting this sentence must fail a test.
# Pins the opening fragment of the bullet that forbids embedding secret values
# in any written artifact (notes, inbox drops, branch content, etc.).
NO_SECRET_VALUES_LINE: str = "No secret value ever appears in any written artifact."

# The kind enum \u2014 the five tokens that must appear as a contiguous
# pipe-delimited run in tend-garden/SKILL.md.  The enum is the contract for
# item classification; adding or removing a kind must fail this test.
KIND_ENUM_CONTIGUOUS: str = (
    "idea | improvement | engineering-gap | research | news"
)

# The two damping threshold lines in tend-garden/SKILL.md.  Each is the
# verbatim-distinctive substring that uniquely identifies the threshold rule.
# These pin the weight\u22653 and weight\u22655 tier definitions so a rewording that
# loses the threshold numbers fails loudly.
DAMPING_THRESHOLD_WEIGHT3: str = (
    "weight \u2265 3** \u2192 that kind goes tail-only"
)
DAMPING_THRESHOLD_WEIGHT5: str = (
    "weight \u2265 5** \u2192 suppressed to a one-line count **in the tail**"
)

# ─── helpers ─────────────────────────────────────────────────────────────────

# The SSOT regex block in §6 has a specific pattern we can extract.
# Each line has the form:
#   ^## \[\d{4}-\d{2}-\d{2}\] (init|...|<token>) \|
# We extract the alternation group from each regex line for token-presence
# checking.  The two distinct regex lines are distinguished by whether they
# contain "cleanup|" (historical reader) or not (current writer).


def _extract_regex_lines(text: str) -> list[str]:
    """Return all lines in ``text`` that look like the §6 op-enum regex lines.

    These lines appear inside fenced code blocks and start with:
        ^## \\[\\d{4}-\\d{2}-\\d{2}\\] (alternation) \\|
    """
    matches = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(r"^## \[") and r"\d{4}" in stripped and "(" in stripped:
            matches.append(stripped)
    return matches


def _token_in_alternation(regex_line: str, token: str) -> bool:
    """Return True if ``token`` appears as a bare word in the alternation group.

    We look for the token surrounded by | or ( on the left and | or ) on
    the right (accounting for any spacing), so "garden" does not accidentally
    match "garden-bed".
    """
    # Extract everything between the first ( and the last )
    start = regex_line.find("(")
    end = regex_line.rfind(")")
    if start == -1 or end == -1:
        return False
    alternation = regex_line[start + 1:end]
    # Split on |
    parts = [p.strip() for p in alternation.split("|")]
    return token in parts


_CLAUDE_SURFACES = [
    (DOCS_CLAUDE_MD, f"{TREE}/AGENTS.md"),
    (TMPL_CLAUDE_MD, "crux/templates/AGENTS.md.tmpl"),
]


# ─── test classes ─────────────────────────────────────────────────────────────


class TestGardenTokenInSSOTRegexes(unittest.TestCase):
    """(a) The garden token appears in BOTH §6 SSOT regexes in BOTH CLAUDE surfaces."""

    def _assert_garden_in_both_regexes(self, path: Path, label: str) -> None:
        """Helper: extract both regex lines from path and assert garden is present."""
        if path == DOCS_CLAUDE_MD:
            require_dev_surface(self, path, label)
        self.assertTrue(
            path.exists(),
            msg=f"{label} not found at {path}",
        )
        text = path.read_text(encoding="utf-8")
        regex_lines = _extract_regex_lines(text)
        self.assertGreaterEqual(
            len(regex_lines),
            2,
            msg=(
                f"{label}: expected to find at least 2 op-enum regex lines "
                f"(current-writer and historical-reader) in §6, found {len(regex_lines)}.\n"
                f"  Extracted lines: {regex_lines!r}"
            ),
        )
        for i, rline in enumerate(regex_lines):
            with self.subTest(surface=label, regex_index=i):
                self.assertTrue(
                    _token_in_alternation(rline, GARDEN_TOKEN),
                    msg=(
                        f"{label}: regex line {i} does not contain '{GARDEN_TOKEN}' "
                        f"as a distinct alternation token.\n"
                        f"  regex line: {rline!r}"
                    ),
                )

    def test_docs_claude_md_both_regexes_contain_garden(self) -> None:
        """docs/AGENTS.md: both §6 SSOT regex lines must contain 'garden'."""
        self._assert_garden_in_both_regexes(DOCS_CLAUDE_MD, f"{TREE}/AGENTS.md")

    def test_tmpl_claude_md_both_regexes_contain_garden(self) -> None:
        """crux/templates/AGENTS.md.tmpl: both §6 SSOT regex lines must contain 'garden'."""
        self._assert_garden_in_both_regexes(TMPL_CLAUDE_MD, "crux/templates/AGENTS.md.tmpl")


class TestGardenRegexCompileAndMatch(unittest.TestCase):
    """(b) Both extracted regexes compile and match the synthetic heading."""

    def _regexes_from(self, path: Path, label: str) -> list[re.Pattern[str]]:
        """Extract and compile the op-enum regex lines from path."""
        if path == DOCS_CLAUDE_MD:
            require_dev_surface(self, path, label)
        self.assertTrue(
            path.exists(),
            msg=f"{label} not found at {path}",
        )
        text = path.read_text(encoding="utf-8")
        regex_lines = _extract_regex_lines(text)
        compiled = []
        for rline in regex_lines:
            try:
                compiled.append(re.compile(rline))
            except re.error as exc:
                self.fail(
                    f"{label}: failed to compile regex line {rline!r}: {exc}"
                )
        return compiled

    def test_docs_claude_md_regexes_compile_and_match(self) -> None:
        """docs/AGENTS.md: extracted regexes compile and match the synthetic heading."""
        patterns = self._regexes_from(DOCS_CLAUDE_MD, f"{TREE}/AGENTS.md")
        self.assertGreaterEqual(len(patterns), 2, msg="Expected at least 2 regex patterns")
        for i, pat in enumerate(patterns):
            with self.subTest(regex_index=i):
                self.assertIsNotNone(
                    pat.match(SYNTHETIC_HEADING),
                    msg=(
                        f"docs/AGENTS.md regex {i} did not match synthetic heading.\n"
                        f"  heading: {SYNTHETIC_HEADING!r}\n"
                        f"  pattern: {pat.pattern!r}"
                    ),
                )

    def test_tmpl_claude_md_regexes_compile_and_match(self) -> None:
        """crux/templates/AGENTS.md.tmpl: extracted regexes compile and match the synthetic heading."""
        patterns = self._regexes_from(TMPL_CLAUDE_MD, "crux/templates/AGENTS.md.tmpl")
        self.assertGreaterEqual(len(patterns), 2, msg="Expected at least 2 regex patterns")
        for i, pat in enumerate(patterns):
            with self.subTest(regex_index=i):
                self.assertIsNotNone(
                    pat.match(SYNTHETIC_HEADING),
                    msg=(
                        f"crux/templates/AGENTS.md.tmpl regex {i} did not match "
                        f"synthetic heading.\n"
                        f"  heading: {SYNTHETIC_HEADING!r}\n"
                        f"  pattern: {pat.pattern!r}"
                    ),
                )


class TestTendGardenSkillLockStep(unittest.TestCase):
    """(c) tend-garden/SKILL.md: op heading form, high_water fields, and op-before-note order."""

    def _read_skill(self) -> str:
        self.assertTrue(
            TEND_GARDEN_SKILL.exists(),
            msg=(
                f"tend-garden/SKILL.md not found at {TEND_GARDEN_SKILL}. "
                f"The parallel agent for this skill has not yet written it; "
                f"re-run after it lands."
            ),
        )
        return TEND_GARDEN_SKILL.read_text(encoding="utf-8")

    def test_skill_contains_op_heading_form(self) -> None:
        """tend-garden/SKILL.md must contain the canonical op heading substring."""
        text = self._read_skill()
        self.assertIn(
            TEND_GARDEN_HEADING_SUBSTR,
            text,
            msg=(
                f"tend-garden/SKILL.md does not contain the op heading form substring.\n"
                f"  expected: {TEND_GARDEN_HEADING_SUBSTR!r}\n"
                f"  This pins the '## [YYYY-MM-DD] garden | morning note ...' form."
            ),
        )

    def test_skill_contains_all_high_water_fields(self) -> None:
        """tend-garden/SKILL.md: all four high_water keys appear WITH colons within
        15 lines of EVERY 'high_water:' marker in the file.

        A bare substring search anywhere in the file is vacuous because field
        names like 'commit' and 'dirty' appear in prose.  This test locates EVERY
        YAML block that uses 'high_water:' (frontmatter schema, morning-note
        frontmatter, etc.) and asserts the four keys appear as ``key:`` entries
        within the 15-line window after each occurrence — so a schema rename
        actually fails, and partial updates that fix one block but not another are
        also caught.
        """
        text = self._read_skill()
        lines = text.splitlines()

        # Collect ALL occurrences of a line that is exactly "high_water:" (in
        # a YAML block, possibly indented).
        marker_indices: list[int] = []
        for i, line in enumerate(lines):
            if line.strip() == "high_water:":
                marker_indices.append(i)

        self.assertGreater(
            len(marker_indices),
            0,
            msg=(
                "tend-garden/SKILL.md does not contain any 'high_water:' YAML "
                "block.  The turn-gate schema (ADR-0039 §2) requires this block."
            ),
        )

        for marker_index in marker_indices:
            # Extract the 15-line window immediately after each marker.
            window_start = marker_index + 1
            window_end = min(window_start + 15, len(lines))
            window = "\n".join(lines[window_start:window_end])

            for field in HIGH_WATER_FIELDS:
                with self.subTest(field=field, marker_line=marker_index + 1):
                    self.assertIn(
                        f"{field}:",
                        window,
                        msg=(
                            f"tend-garden/SKILL.md: '{field}:' not found within 15 "
                            f"lines after 'high_water:' at line {marker_index + 1} "
                            f"(lines {window_start + 1}–{window_end}).\n"
                            f"  A bare-text match anywhere in the file is insufficient; "
                            f"the key must appear with a colon inside the YAML block.\n"
                            f"  Window inspected:\n{window}"
                        ),
                    )

    def test_skill_contains_op_before_note_ordering_markers(self) -> None:
        """tend-garden/SKILL.md must contain both op-before-note ordering markers."""
        text = self._read_skill()
        for marker in OP_BEFORE_NOTE_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    text,
                    msg=(
                        f"tend-garden/SKILL.md does not contain the ordering marker "
                        f"'{marker}'.\n"
                        f"  Both markers must appear to pin the 'write op first, "
                        f"then write note' contract (ADR-0039 §5)."
                    ),
                )

    def test_skill_contains_kind_enum_contiguous(self) -> None:
        """tend-garden/SKILL.md must contain the exact kind enum as a
        contiguous pipe-delimited run.

        KIND_ENUM_CONTIGUOUS pins the five item-classification tokens
        (idea | improvement | engineering-gap | research | news) in the
        exact order they appear in the enum definition.  Adding, removing,
        or reordering a kind must fail this test.
        """
        text = self._read_skill()
        self.assertIn(
            KIND_ENUM_CONTIGUOUS,
            text,
            msg=(
                f"tend-garden/SKILL.md does not contain the exact kind enum as a "
                f"contiguous run.\n"
                f"  expected substring: {KIND_ENUM_CONTIGUOUS!r}\n"
                f"  This pins the five item-classification tokens and their order; "
                f"changes to the enum must be reflected here and in the test."
            ),
        )

    def test_skill_contains_both_damping_thresholds(self) -> None:
        """tend-garden/SKILL.md must contain BOTH damping threshold lines.

        DAMPING_THRESHOLD_WEIGHT3 and DAMPING_THRESHOLD_WEIGHT5 pin the
        verbatim-distinctive substrings for the weight≥3 tail-only and
        weight≥5 suppressed-count tiers.  A rewording that drops the
        threshold numbers or tier names must fail this test.
        """
        text = self._read_skill()
        for marker in (DAMPING_THRESHOLD_WEIGHT3, DAMPING_THRESHOLD_WEIGHT5):
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    text,
                    msg=(
                        f"tend-garden/SKILL.md does not contain the damping threshold "
                        f"substring.\n"
                        f"  expected: {marker!r}\n"
                        f"  Both weight≥3 and weight≥5 tier definitions must appear "
                        f"verbatim to pin the preference-model contract."
                    ),
                )


class TestWhiteboardingUnattendedMode(unittest.TestCase):
    """(d) whiteboarding/SKILL.md contains the unattended-mode markers."""

    def _read_skill(self) -> str:
        self.assertTrue(
            WHITEBOARDING_SKILL.exists(),
            msg=f"whiteboarding/SKILL.md not found at {WHITEBOARDING_SKILL}",
        )
        return WHITEBOARDING_SKILL.read_text(encoding="utf-8")

    def test_skill_contains_unattended_mode_heading(self) -> None:
        """whiteboarding/SKILL.md must contain 'Unattended mode' as a heading marker."""
        text = self._read_skill()
        self.assertIn(
            UNATTENDED_MODE_HEADING,
            text,
            msg=(
                f"whiteboarding/SKILL.md does not contain the unattended-mode "
                f"heading.\n  expected substring: {UNATTENDED_MODE_HEADING!r}\n"
                f"  This section is required by ADR-0039 §7."
            ),
        )

    def test_skill_contains_assumed_marker(self) -> None:
        """whiteboarding/SKILL.md must contain 'ASSUMED:' (mandatory assumption flagging)."""
        text = self._read_skill()
        self.assertIn(
            UNATTENDED_ASSUMED_MARKER,
            text,
            msg=(
                f"whiteboarding/SKILL.md does not contain the mandatory assumption "
                f"flagging marker.\n  expected substring: {UNATTENDED_ASSUMED_MARKER!r}\n"
                f"  Per ADR-0039 §7, every assumed answer must be recorded as "
                f"'ASSUMED: <question> → <answer> (why)'."
            ),
        )

    def test_skill_contains_mode_unattended(self) -> None:
        """whiteboarding/SKILL.md must contain 'mode: unattended' (session header field)."""
        text = self._read_skill()
        self.assertIn(
            UNATTENDED_MODE_KEY,
            text,
            msg=(
                f"whiteboarding/SKILL.md does not contain the session-header field.\n"
                f"  expected substring: {UNATTENDED_MODE_KEY!r}\n"
                f"  Per ADR-0039 §7, unattended sessions carry 'mode: unattended' "
                f"in their header."
            ),
        )


class TestNightGardenerAgentReflexSentence(unittest.TestCase):
    """(e) The canonical reflex sentence appears exactly once in night-gardener.md."""

    def test_night_gardener_agent_exists(self) -> None:
        """crux/agents/night-gardener.md must exist."""
        self.assertTrue(
            NIGHT_GARDENER_AGENT.exists(),
            msg=(
                f"crux/agents/night-gardener.md not found at {NIGHT_GARDENER_AGENT}. "
                f"The parallel agent for this file has not yet written it; "
                f"re-run after it lands."
            ),
        )

    def test_reflex_sentence_appears_exactly_once(self) -> None:
        """The canonical capability-gap reflex sentence appears exactly once.

        NIGHT_GARDENER_REFLEX_SENTENCE pins the 'Doing something manually for
        the third time… invoke forge-skill' discipline embedded in the agent.
        Exactly-once semantics catch both deletion and accidental duplication.
        """
        self.assertTrue(
            NIGHT_GARDENER_AGENT.exists(),
            msg=f"crux/agents/night-gardener.md not found at {NIGHT_GARDENER_AGENT}.",
        )
        text = NIGHT_GARDENER_AGENT.read_text(encoding="utf-8")
        count = text.count(NIGHT_GARDENER_REFLEX_SENTENCE)
        self.assertEqual(
            count,
            1,
            msg=(
                f"Expected '{NIGHT_GARDENER_REFLEX_SENTENCE}' to appear exactly once "
                f"in night-gardener.md, but found {count} occurrences.\n"
                f"  This pins the canonical capability-gap reflex sentence; "
                f"deleting it or duplicating it must fail this test."
            ),
        )

    def test_never_push_hard_line_present(self) -> None:
        """The never-push hard line is present in night-gardener.md.

        NEVER_PUSH_HARD_LINE pins the load-bearing safety sentence in the Hard
        Lines section.  Deleting it must fail this test.
        """
        self.assertTrue(
            NIGHT_GARDENER_AGENT.exists(),
            msg=f"crux/agents/night-gardener.md not found at {NIGHT_GARDENER_AGENT}.",
        )
        text = NIGHT_GARDENER_AGENT.read_text(encoding="utf-8")
        self.assertIn(
            NEVER_PUSH_HARD_LINE,
            text,
            msg=(
                f"'{NEVER_PUSH_HARD_LINE}' not found in night-gardener.md.\n"
                f"  This is the load-bearing safety line that forbids pushing or "
                f"merging from an overnight unattended session.  Deleting it must "
                f"fail this test."
            ),
        )

    def test_no_secret_values_line_present(self) -> None:
        """The no-secret-values safety line is present in night-gardener.md.

        NO_SECRET_VALUES_LINE pins the sentence that forbids embedding secret
        values in any written artifact.  Deleting it must fail this test.
        """
        self.assertTrue(
            NIGHT_GARDENER_AGENT.exists(),
            msg=f"crux/agents/night-gardener.md not found at {NIGHT_GARDENER_AGENT}.",
        )
        text = NIGHT_GARDENER_AGENT.read_text(encoding="utf-8")
        self.assertIn(
            NO_SECRET_VALUES_LINE,
            text,
            msg=(
                f"'{NO_SECRET_VALUES_LINE}' not found in night-gardener.md.\n"
                f"  This pins the hard line that forbids embedding secret values "
                f"(tokens, credentials) in notes, inbox drops, or branch content.  "
                f"Deleting it must fail this test."
            ),
        )
