"""Tests for the capability-gap reflex micro-block embedding (ADR-0037 §4).

Covers all ten surfaces:
- 9 crux/agents/*.md files: each must contain the canonical reflex sentence
  EXACTLY ONCE and the canonical escape clause EXACTLY ONCE.
- docs/AGENTS.md: must have a '## 10.B' heading exactly once, the canonical
  reflex sentence EXACTLY ONCE, and the canonical escape clause EXACTLY ONCE.
- crux/templates/AGENTS.md.tmpl: same requirements as docs/AGENTS.md.

Stdlib unittest only (no third-party deps).
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    from ._dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface
except ImportError:  # unittest discover imports test modules top-level
    from _dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
AGENTS_DIR = REPO_ROOT / "crux" / "agents"
DOCS_CLAUDE_MD = TREE_AGENTS_MD
TMPL_CLAUDE_MD = REPO_ROOT / "crux" / "templates" / "AGENTS.md.tmpl"

# ---------------------------------------------------------------------------
# Canonical sentence — distinctive enough to pin the exact block without
# requiring a multi-line match.  Must appear byte-identical in every surface.
# ---------------------------------------------------------------------------

CANONICAL_SENTENCE = (
    "That's a capability gap — invoke the `forge-skill` skill"
)

# Canonical escape clause — must appear byte-identical in every surface.
CANONICAL_ESCAPE = (
    "If you lack either the Skill tool or file-write access, "
    "report the gap to your lead instead of working around it."
)

# The developer-only tail (ADR-0128 Decision 4). It governs where a developer
# sends a capability gap, and must live in developer.md alone: the precedence
# sentence, the routing sentence, and the forge-skill exception, each exactly
# once there and nowhere else.
DEVELOPER_TAIL = [
    "This tail governs where a gap goes; the block above still names the trigger.",
    "A capability gap outside your assigned unit goes to your lead: report it "
    "`BLOCKED` if it stops the unit, otherwise name it in your report and leave "
    "the status unchanged.",
    "Invoke `forge-skill` only when the unit you were assigned is to build that "
    "capability.",
]

# Heading for the §10.B section in docs/AGENTS.md and the tmpl.
SECTION_HEADING = "## 10.B"

# The eight agent files this ADR mandates.
AGENT_FILES = [
    "architect.md",
    "brainstormer.md",
    "commander.md",
    "dev-lead.md",
    "developer.md",
    "historian.md",
    "librarian.md",
    "night-gardener.md",
    "reviewer.md",
    "wayfinder.md",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _count(text: str, needle: str) -> int:
    return text.count(needle)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class AgentReflexBlockTests(unittest.TestCase):
    """Each of the 9 crux/agents/*.md files must embed the reflex sentence
    EXACTLY ONCE and the canonical escape clause EXACTLY ONCE.
    Per-file assertions so a duplicate cannot mask an omission.
    Uses AGENT_FILES with subTest so adding a 10th agent file to the list
    automatically adds coverage.
    """

    def test_agent_directory_completeness(self):
        """AGENT_FILES must match the actual *.md files in crux/agents/ exactly.

        Fails loudly if a new agent file is added without updating AGENT_FILES,
        so it cannot escape coverage.
        """
        actual = sorted(p.name for p in AGENTS_DIR.glob("*.md"))
        expected = sorted(AGENT_FILES)
        self.assertEqual(
            actual,
            expected,
            f"crux/agents/ on disk ({actual}) does not match AGENT_FILES "
            f"({expected}). Update AGENT_FILES in this test to match.",
        )

    def test_canonical_sentence_present_exactly_once(self):
        for filename in AGENT_FILES:
            with self.subTest(filename=filename):
                path = AGENTS_DIR / filename
                self.assertTrue(
                    path.exists(),
                    f"Expected agent file not found: {path}",
                )
                text = _read(path)
                count = _count(text, CANONICAL_SENTENCE)
                self.assertEqual(
                    count,
                    1,
                    f"{filename}: expected canonical reflex sentence exactly "
                    f"once, found {count} time(s).\n"
                    f"Canonical sentence: {CANONICAL_SENTENCE!r}",
                )

    def test_canonical_escape_present_exactly_once(self):
        for filename in AGENT_FILES:
            with self.subTest(filename=filename):
                path = AGENTS_DIR / filename
                self.assertTrue(
                    path.exists(),
                    f"Expected agent file not found: {path}",
                )
                text = _read(path)
                count = _count(text, CANONICAL_ESCAPE)
                self.assertEqual(
                    count,
                    1,
                    f"{filename}: expected canonical escape clause exactly "
                    f"once, found {count} time(s).\n"
                    f"Canonical escape: {CANONICAL_ESCAPE!r}",
                )


class DeveloperTailTests(unittest.TestCase):
    """The developer tail lives exactly once in developer.md and in no other
    agent file or instruction surface. Matching collapses whitespace so a
    re-wrapped tail still counts."""

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(text.replace("**", "").split())

    def _count_norm(self, text: str, needle: str) -> int:
        return self._norm(text).count(self._norm(needle))

    def test_tail_present_exactly_once_in_developer(self):
        text = _read(AGENTS_DIR / "developer.md")
        for sentence in DEVELOPER_TAIL:
            with self.subTest(sentence=sentence):
                self.assertEqual(self._count_norm(text, sentence), 1)

    def test_tail_absent_from_every_other_surface(self):
        others = [(AGENTS_DIR / f, f) for f in AGENT_FILES if f != "developer.md"]
        others.append((TMPL_CLAUDE_MD, "crux/templates/AGENTS.md.tmpl"))
        if DOCS_CLAUDE_MD.exists():
            others.append((DOCS_CLAUDE_MD, f"{TREE}/AGENTS.md"))
        for path, label in others:
            text = _read(path)
            for sentence in DEVELOPER_TAIL:
                with self.subTest(surface=label, sentence=sentence):
                    self.assertEqual(self._count_norm(text, sentence), 0)

    def test_absence_detector_control(self):
        """Positive control: the detector finds a re-wrapped tail sentence."""
        sample = "  " + DEVELOPER_TAIL[1].replace(" report it ", "\n  report it ")
        self.assertEqual(self._count_norm(sample, DEVELOPER_TAIL[1]), 1)


_DOC_SURFACES = [
    (DOCS_CLAUDE_MD, f"{TREE}/AGENTS.md"),
    (TMPL_CLAUDE_MD, "crux/templates/AGENTS.md.tmpl"),
]


class DocSurfaceReflexTests(unittest.TestCase):
    """docs/AGENTS.md and crux/templates/AGENTS.md.tmpl must each have the
    '## 10.B' heading exactly once, the canonical reflex sentence exactly
    once, and the canonical escape clause exactly once.

    The two surfaces share identical structural requirements so they are
    parameterized into a single test class via subTest.
    """

    def test_section_heading_present_exactly_once(self):
        for path, label in _DOC_SURFACES:
            with self.subTest(surface=label):
                if path == DOCS_CLAUDE_MD:
                    require_dev_surface(self, path, label)
                self.assertTrue(
                    path.exists(),
                    f"{label} not found at {path}",
                )
                text = _read(path)
                count = _count(text, SECTION_HEADING)
                self.assertEqual(
                    count,
                    1,
                    f"{label}: expected '{SECTION_HEADING}' heading exactly "
                    f"once, found {count} time(s).",
                )

    def test_canonical_sentence_present_exactly_once(self):
        for path, label in _DOC_SURFACES:
            with self.subTest(surface=label):
                if path == DOCS_CLAUDE_MD:
                    require_dev_surface(self, path, label)
                self.assertTrue(
                    path.exists(),
                    f"{label} not found at {path}",
                )
                text = _read(path)
                count = _count(text, CANONICAL_SENTENCE)
                self.assertEqual(
                    count,
                    1,
                    f"{label}: expected canonical reflex sentence exactly "
                    f"once, found {count} time(s).\n"
                    f"Canonical sentence: {CANONICAL_SENTENCE!r}",
                )

    def test_canonical_escape_present_exactly_once(self):
        for path, label in _DOC_SURFACES:
            with self.subTest(surface=label):
                if path == DOCS_CLAUDE_MD:
                    require_dev_surface(self, path, label)
                self.assertTrue(
                    path.exists(),
                    f"{label} not found at {path}",
                )
                text = _read(path)
                count = _count(text, CANONICAL_ESCAPE)
                self.assertEqual(
                    count,
                    1,
                    f"{label}: expected canonical escape clause exactly "
                    f"once, found {count} time(s).\n"
                    f"Canonical escape: {CANONICAL_ESCAPE!r}",
                )


if __name__ == "__main__":
    unittest.main()
