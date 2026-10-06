"""The fix-directly sizing test must mean what its preamble says.

The preamble reads "A defect is a direct fix when every answer is yes. One no
routes it to a tier." Question 5 used to ask "Does the fix need an independent
review gate?", where the yes answer routed to `patch-cycle`: a defect meeting
every condition answered one question no, and a reader following the preamble
sent a gate-wanting fix down the direct rung. Each question must be phrased so
that yes keeps the defect on the direct rung.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL = PLUGIN_ROOT / "skills" / "fix-directly" / "SKILL.md"


def _sizing_questions(text: str) -> dict[int, str]:
    section = text.split("## The sizing test", 1)[1].split("\n## ", 1)[0]
    items = re.findall(r"^(\d)\. (.*?)(?=^\d\. |^\*\*A security label|\Z)",
                       section, re.M | re.S)
    return {int(n): " ".join(body.split()) for n, body in items}


class SizingTestPolarityTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")
        self.questions = _sizing_questions(self.text)

    def test_preamble_says_every_yes_is_a_direct_fix(self):
        self.assertIn("A defect is a direct fix when every answer is yes.", self.text)
        self.assertEqual(sorted(self.questions), [1, 2, 3, 4, 5])

    def test_question_five_yes_means_ship_without_a_review_gate(self):
        q5 = self.questions[5]
        self.assertTrue(
            q5.startswith("**Can the fix ship without an independent review gate?**"),
            q5)
        self.assertNotIn("Does the fix need an independent review gate", q5)

    def test_question_five_no_still_routes_to_patch_cycle(self):
        q5 = self.questions[5]
        self.assertIn("signed or digest-bound surface", q5)
        self.assertRegex(q5, r"If not → `patch-cycle`")


if __name__ == "__main__":
    unittest.main()
