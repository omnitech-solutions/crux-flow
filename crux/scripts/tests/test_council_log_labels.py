"""Aggregation log lines carry closed-set constants, never seat text.

The fixture key is a key SHAPE assembled at run time from a prefix and filler; it is not a credential, and no
test prints it.
"""
import ast
import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
try:
    import httpx  # noqa: F401
    import crux.council.async_council as ac
    from crux.council.async_council import AsyncCouncil
    from crux.core.data_classes import CouncilVote
    HAVE_COUNCIL = True
except ImportError:  # router deps unavailable: run under uv
    HAVE_COUNCIL = False
import secret_scan as ss  # noqa: E402

FAKE = "sk-or-v1-" + "ab" * 32
LOGGER = "crux.council.async_council"


def vote(seat, decision, confidence=0.9):
    return CouncilVote(model=f"{seat}/model-x", provider=seat, decision=decision, reasoning="",
                       confidence=confidence, dissenting_points=[])


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class Item8(unittest.TestCase):
    def aggregate(self, *decisions):
        with self.assertLogs(LOGGER, level="INFO") as cm:
            result = AsyncCouncil()._aggregate_votes(
                [vote(s, d) for s, d in zip(("a", "b", "c"), decisions)])
        return result, cm.output

    def test_fixture_is_key_shaped(self):  # positive control for the absence tests below
        self.assertTrue(ss.scan_text(FAKE))

    def test_key_shaped_decision_never_logged(self):
        _, lines = self.aggregate(FAKE, "APPROVE", "APPROVE")
        leaked = sum(1 for line in lines if FAKE in line or ss.scan_text(line))
        self.assertEqual(0, leaked, f"the key-shaped decision reached {leaked} INFO line(s)")
        self.assertTrue(any(": OFF_SCALE (" in line for line in lines), "no OFF_SCALE line")

    def test_known_labels_still_logged(self):
        _, lines = self.aggregate("APPROVE", "REQUEST_CHANGES", "APPROVE_WITH_NITS")
        for label in ("APPROVE", "REQUEST_CHANGES", "APPROVE_WITH_NITS"):
            self.assertTrue(any(f": {label} (" in line for line in lines), (label, lines))

    def test_non_string_decision_logs_off_scale(self):
        _, lines = self.aggregate({"note": FAKE}, "APPROVE", "APPROVE")
        leaked = sum(1 for line in lines if FAKE in line)
        self.assertEqual(0, leaked, f"the key-shaped value reached {leaked} INFO line(s)")
        self.assertTrue(any(": OFF_SCALE (" in line for line in lines), "no OFF_SCALE line")

    def test_echoed_unanimous_label_logs_a_constant_and_the_result_keeps_it(self):
        token = "ZZPROBE_TOKEN"
        result, lines = self.aggregate(token, token, token)
        self.assertEqual(result.consensus, "UNANIMOUS_" + token)  # the record keeps its label
        self.assertEqual([], [line for line in lines if token in line])
        # The log's own fallback, which no deliberation produces: UNANIMOUS_OFF_SCALE means
        # something narrower in the record, so the log never borrows it.
        self.assertTrue(any("Consensus: UNLISTED," in line for line in lines), lines)
        self.assertEqual([], [line for line in lines if "UNANIMOUS_OFF_SCALE" in line])

    def test_known_consensus_still_logged(self):
        _, lines = self.aggregate("APPROVE", "APPROVE", "REJECT")
        self.assertTrue(any("Consensus: MAJORITY_APPROVE," in line for line in lines), lines)



@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class LogTableSource(unittest.TestCase):
    """The log table holds every token a seat may legitimately return, so none logs as OFF_SCALE."""

    def test_gate_scale_and_run_council_tokens_are_listed(self):
        gate = set(re.findall(r'"([A-Z_]+)"', ac._GATE_DECISIONS)) | {"ARCHITECTURAL"}
        tree = ast.parse((SCRIPTS / "run-council.py").read_text(encoding="utf-8"))
        tokens = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(getattr(t, "id", None) == "_DECISION_TOKENS" for t in node.targets))
        self.assertTrue(gate and tokens)    # positive control: both sources parsed
        for token in gate | set(tokens) | set(ac._KNOWN_DECISIONS):
            self.assertEqual(ac._LOG_DECISIONS.get(token), token, token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
