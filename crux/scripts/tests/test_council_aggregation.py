"""ADR-0054: council aggregation excludes a single errored provider seat.

Exercises `AsyncCouncil._aggregate_votes` directly (no API calls — the ctor
resolves keys via crux_env and tolerates their absence; `_aggregate_votes` is
pure). Covers the responding-seat confidence mean, the quorum gate, the
`errored_seats`/`degraded` surface, the widened approval set, and the
`has_critical_dissent` scoping. Runs under the uv lane (importing the council
pulls in the LLM router's httpx transport); skips cleanly where it's absent.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "crux" / "scripts"

try:
    sys.path.insert(0, str(SCRIPTS))
    from crux.council.async_council import AsyncCouncil
    from crux.core.data_classes import CouncilVote
    HAVE_COUNCIL = True
except Exception:  # the router transport (httpx) unavailable
    HAVE_COUNCIL = False


def vote(provider, decision, confidence, errored=False, dissents=None):
    return CouncilVote(
        model=(f"{provider}/ERROR" if errored else f"{provider}/model-x"),
        provider=provider,
        decision=decision,
        reasoning="",
        confidence=confidence,
        dissenting_points=dissents or [],
        errored=errored,
    )


@unittest.skipUnless(HAVE_COUNCIL, "council import (httpx transport) unavailable")
class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.c = AsyncCouncil()

    def agg(self, votes):
        return self.c._aggregate_votes(votes)

    def test_all_respond_no_regression_auto_execute(self):
        r = self.agg([vote("a", "APPROVE", 0.9), vote("b", "APPROVE", 0.88), vote("c", "APPROVE", 0.86)])
        self.assertEqual(r.consensus, "UNANIMOUS_APPROVE")
        self.assertFalse(r.degraded)
        self.assertEqual(r.errored_seats, "0/3")
        self.assertEqual(r.final_recommendation["action"], "AUTO_EXECUTE")

    def test_one_errored_seat_degraded_execute_with_monitoring(self):
        # The motivating case: 2 clean approvals + 1 errored seat. Old code
        # reported SPLIT/0.57 -> DEFER; new code -> UNANIMOUS over responders,
        # degraded ceiling -> EXECUTE_WITH_MONITORING.
        r = self.agg([
            vote("a", "APPROVE", 0.86),
            vote("b", "APPROVE_WITH_NITS", 0.85),
            vote("g", "DEFER_TO_HUMAN", 0.0, errored=True, dissents=["Provider g failed: timeout"]),
        ])
        self.assertEqual(r.consensus, "UNANIMOUS_APPROVE")
        self.assertTrue(r.degraded)
        self.assertEqual(r.errored_seats, "1/3")
        self.assertAlmostEqual(r.consensus_confidence, 0.855, places=3)
        self.assertEqual(r.final_recommendation["action"], "EXECUTE_WITH_MONITORING")
        self.assertEqual(r.final_recommendation["errored_seats"], "1/3")
        self.assertTrue(r.final_recommendation["degraded"])

    def test_full_quorum_high_conf_reaches_auto_execute_but_degraded_does_not(self):
        clean = self.agg([vote("a", "APPROVE", 0.9), vote("b", "APPROVE", 0.9), vote("c", "APPROVE", 0.9)])
        self.assertEqual(clean.final_recommendation["action"], "AUTO_EXECUTE")
        degraded = self.agg([vote("a", "APPROVE", 0.9), vote("b", "APPROVE", 0.9),
                             vote("g", "DEFER_TO_HUMAN", 0.0, errored=True)])
        self.assertTrue(degraded.degraded)
        self.assertEqual(degraded.final_recommendation["action"], "EXECUTE_WITH_MONITORING")

    def test_errored_seat_dissent_excluded(self):
        r = self.agg([
            vote("a", "APPROVE", 0.9), vote("b", "APPROVE", 0.9),
            vote("g", "DEFER_TO_HUMAN", 0.0, errored=True, dissents=["Provider g failed: critical error"]),
        ])
        self.assertEqual(r.dissent_count, 0)
        self.assertFalse(r.has_critical_dissent)

    def test_responding_reject_critical_dissent_trips(self):
        r = self.agg([
            vote("a", "APPROVE", 0.9),
            vote("b", "REJECT", 0.8, dissents=["critical: unsafe path"]),
            vote("c", "APPROVE", 0.7),
        ])
        self.assertTrue(r.has_critical_dissent)

    def test_split_two_responding_defers(self):
        r = self.agg([
            vote("a", "APPROVE", 0.9), vote("b", "REJECT", 0.8),
            vote("g", "DEFER_TO_HUMAN", 0.0, errored=True),
        ])
        self.assertEqual(r.consensus, "SPLIT")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")

    def test_all_errored_is_no_quorum(self):
        r = self.agg([
            vote("a", "DEFER_TO_HUMAN", 0.0, errored=True),
            vote("b", "DEFER_TO_HUMAN", 0.0, errored=True),
            vote("c", "DEFER_TO_HUMAN", 0.0, errored=True),
        ])
        self.assertEqual(r.consensus, "NO_QUORUM")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(r.errored_seats, "3/3")

    def test_one_responding_below_quorum(self):
        r = self.agg([
            vote("a", "APPROVE", 0.99),
            vote("b", "DEFER_TO_HUMAN", 0.0, errored=True),
            vote("c", "DEFER_TO_HUMAN", 0.0, errored=True),
        ])
        self.assertEqual(r.consensus, "NO_QUORUM")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")

    def test_approve_with_nits_counts_as_approval(self):
        r = self.agg([vote("a", "APPROVE_WITH_NITS", 0.9), vote("b", "APPROVE_WITH_NITS", 0.9),
                      vote("c", "APPROVE", 0.9)])
        self.assertEqual(r.consensus, "UNANIMOUS_APPROVE")

    def test_error_vote_is_marked_errored(self):
        # The INV-0001-relevant property of an error vote: it is flagged
        # `errored=True` so the aggregator can exclude it. The REDACTION
        # behaviour of _create_error_vote is deliberately NOT tested here —
        # it lives in test_council_redaction.py.
        #
        # Why the split (PB-0052): this class is INV-0001's executable check
        # (`check_id: council_aggregation`). Two redaction tests used to live
        # here, so a redaction failure set reconciliation `last_result: fail`
        # and reported the *aggregation* invariant as violated — a false signal
        # about a ratified pin. Keep this file about aggregation only.
        v = self.c._create_error_vote("openai", RuntimeError("boom"))
        self.assertTrue(v.errored)
        self.assertEqual(v.model, "openai/ERROR")
        self.assertEqual(v.decision, "DEFER_TO_HUMAN")
        self.assertEqual(v.confidence, 0.0)

    def test_votes_retains_all_seats(self):
        r = self.agg([vote("a", "APPROVE", 0.9), vote("b", "APPROVE", 0.9),
                      vote("g", "DEFER_TO_HUMAN", 0.0, errored=True)])
        self.assertEqual(len(r.votes), 3)
        self.assertEqual(r.errored_seats, "1/3")

    def test_empty_votes_no_quorum_not_degraded(self):
        # The empty-votes early return: NO_QUORUM/DEFER, and the mirrored
        # final_recommendation must agree with the .degraded property (no seats
        # errored -> not degraded, just under-provisioned).
        r = self.agg([])
        self.assertEqual(r.consensus, "NO_QUORUM")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertFalse(r.degraded)
        self.assertEqual(r.final_recommendation["degraded"], r.degraded)
        self.assertEqual(r.errored_seats, "0/0")

    # ---- Unanimity over any decision token, and the off_scale surface ------
    # Positive controls for the tests below: test_all_respond_no_regression_auto_execute
    # and test_approve_with_nits_counts_as_approval (approval unanimity unchanged).

    def three(self, decision, conf=0.8):
        return self.agg([vote("a", decision, conf), vote("b", decision, conf), vote("c", decision, conf)])

    def test_unanimous_off_scale_decision_is_not_split(self):
        r = self.three("REVISE")
        self.assertEqual(r.consensus, "UNANIMOUS_REVISE")
        self.assertEqual(r.final_recommendation["reason"], "UNANIMOUS_REVISE")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(r.final_recommendation["off_scale"], ["a:REVISE", "b:REVISE", "c:REVISE"])

    def test_unanimous_request_changes_is_not_split(self):
        r = self.three("REQUEST_CHANGES")
        self.assertEqual(r.consensus, "UNANIMOUS_REQUEST_CHANGES")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")

    def test_unanimous_defer_to_human_is_not_split(self):
        r = self.three("DEFER_TO_HUMAN")
        self.assertEqual(r.consensus, "UNANIMOUS_DEFER_TO_HUMAN")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(r.final_recommendation["off_scale"], [])

    def test_unanimity_counts_responding_seats_only(self):
        # Condition (c): the generic branch carries the degradation marker.
        r = self.agg([
            vote("a", "REVISE", 0.8), vote("b", "REVISE", 0.8),
            vote("g", "DEFER_TO_HUMAN", 0.0, errored=True),
        ])
        self.assertEqual(r.consensus, "UNANIMOUS_REVISE")
        self.assertTrue(r.degraded)
        self.assertTrue(r.final_recommendation["degraded"])
        self.assertEqual(r.final_recommendation["errored_seats"], "1/3")
        self.assertEqual(r.errored_seats, "1/3")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(r.final_recommendation["off_scale"], ["a:REVISE", "b:REVISE"])

    def test_unrecognised_token_stays_visible_in_majority(self):
        r = self.agg([vote("a", "APPROVE", 0.8), vote("b", "APPROVE", 0.8), vote("c", "FOO", 0.8)])
        self.assertEqual(r.consensus, "MAJORITY_APPROVE")
        self.assertEqual(r.final_recommendation["action"], "EXECUTE_WITH_MONITORING")
        self.assertEqual(r.final_recommendation["off_scale"], ["c:FOO"])

    def test_free_text_decision_does_not_enter_consensus(self):
        r = self.three("approve now!")
        self.assertEqual(r.consensus, "UNANIMOUS_OFF_SCALE")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(len(r.final_recommendation["off_scale"]), 3)

    def test_lowercase_approve_is_off_scale_and_defers(self):
        # Approval membership is case-sensitive; `approve` fails the ^[A-Z] label guard.
        r = self.three("approve", conf=0.95)
        self.assertEqual(r.consensus, "UNANIMOUS_OFF_SCALE")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(r.final_recommendation["off_scale"], ["a:approve", "b:approve", "c:approve"])

    def test_approved_is_not_approval(self):
        # Routing is never keyed on a UNANIMOUS_ prefix or an APPROVE substring,
        # and the label never echoes an approval lookalike.
        r = self.three("APPROVED", conf=0.95)
        self.assertEqual(r.consensus, "UNANIMOUS_OFF_SCALE")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(r.final_recommendation["off_scale"], ["a:APPROVED", "b:APPROVED", "c:APPROVED"])

    def test_approval_reject_and_action_lookalikes_are_never_echoed(self):
        """A token containing APPROVE or REJECT, or equal to an execute action,
        would read as a verdict to a caller matching the label by prefix. It maps
        to UNANIMOUS_OFF_SCALE, stays in off_scale, and defers."""
        for token in ("APPROVE_ALL", "APPROVE_", "APPROVE_WITH_CAVEATS", "PRE_APPROVED",
                      "REJECTED", "REJECT_ALL", "AUTO_EXECUTE", "EXECUTE_WITH_MONITORING"):
            with self.subTest(token=token):
                r = self.three(token, conf=0.95)
                self.assertEqual(r.consensus, "UNANIMOUS_OFF_SCALE")
                self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
                self.assertEqual(r.final_recommendation["off_scale"],
                                 [f"a:{token}", f"b:{token}", f"c:{token}"])

    def test_other_valid_tokens_still_echo(self):
        """Positive control for the lookalike guard: it narrows only lookalikes."""
        for token in ("REVISE", "DEFER_TO_HUMAN", "REQUEST_CHANGES"):
            with self.subTest(token=token):
                r = self.three(token)
                self.assertEqual(r.consensus, f"UNANIMOUS_{token}")
                self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")

    def test_non_string_decision_is_off_scale(self):
        r = self.three(["APPROVE"])
        self.assertEqual(r.consensus, "UNANIMOUS_OFF_SCALE")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")

    def test_one_responding_plus_two_errored_is_no_quorum_with_off_scale(self):
        r = self.agg([
            vote("a", "REVISE", 0.9),
            vote("b", "DEFER_TO_HUMAN", 0.0, errored=True),
            vote("c", "DEFER_TO_HUMAN", 0.0, errored=True),
        ])
        self.assertEqual(r.consensus, "NO_QUORUM")
        self.assertEqual(r.final_recommendation["off_scale"], ["a:REVISE"])

    def test_off_scale_key_present_on_every_return_path(self):
        self.assertEqual(self.agg([]).final_recommendation["off_scale"], [])
        r = self.agg([vote(p, "DEFER_TO_HUMAN", 0.0, errored=True) for p in "abc"])
        self.assertEqual(r.final_recommendation["off_scale"], [])
        self.assertEqual(self.three("APPROVE").final_recommendation["off_scale"], [])
        self.assertEqual(self.three("REJECT").final_recommendation["off_scale"], [])

    def test_mixed_decisions_still_split(self):
        r = self.agg([vote("a", "APPROVE", 0.8), vote("b", "REVISE", 0.8), vote("c", "REJECT", 0.8)])
        self.assertEqual(r.consensus, "SPLIT")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(r.final_recommendation["off_scale"], ["b:REVISE"])

    def test_label_guard_rejects_unbounded_or_odd_tokens(self):
        for token in ("R" * 41, "REV ISE", "REVISE1", "R-E"):
            with self.subTest(token=token):
                self.assertEqual(self.three(token).consensus, "UNANIMOUS_OFF_SCALE")
        # boundary: 40 characters is a valid label
        self.assertEqual(self.three("R" * 40).consensus, "UNANIMOUS_" + "R" * 40)

    def test_routing_action_matches_the_prior_rule_for_every_input(self):
        """The action chain is unchanged: recompute it from the prior rule
        (approve/reject counts only) and compare over a table of inputs."""
        import itertools

        approve = ("APPROVE", "APPROVE_WITH_CONDITIONS", "APPROVE_WITH_NITS")

        def prior_action(decisions, conf, degraded):
            n = len(decisions)
            ap = sum(1 for d in decisions if d in approve)
            rj = sum(1 for d in decisions if d == "REJECT")
            if ap == n:
                c = "UNANIMOUS_APPROVE"
            elif rj == n:
                c = "UNANIMOUS_REJECT"
            elif ap > n / 2:
                c = "MAJORITY_APPROVE"
            elif rj > n / 2:
                c = "MAJORITY_REJECT"
            else:
                c = "SPLIT"
            if c == "UNANIMOUS_APPROVE" and conf >= 0.85 and not degraded:
                return "AUTO_EXECUTE"
            if c in ("UNANIMOUS_APPROVE", "MAJORITY_APPROVE") and conf >= 0.70:
                return "EXECUTE_WITH_MONITORING"
            if c == "UNANIMOUS_REJECT":
                return "ABORT"
            return "DEFER_TO_HUMAN"

        tokens = ["APPROVE", "APPROVE_WITH_NITS", "REJECT", "DEFER_TO_HUMAN", "REVISE", "APPROVED", "approve", "x y"]
        seen = set()
        for decisions in itertools.product(tokens, repeat=3):
            for conf in (0.75, 0.9):
                for errored in (False, True):
                    votes = [vote(p, d, conf) for p, d in zip("abc", decisions)]
                    if errored:
                        votes[2] = vote("c", "DEFER_TO_HUMAN", 0.0, errored=True)
                    resp = [v.decision for v in votes if not v.errored]
                    want = prior_action(resp, conf, errored) if len(resp) >= 2 else "DEFER_TO_HUMAN"
                    got = self.agg(votes).final_recommendation["action"]
                    self.assertEqual(got, want, (decisions, conf, errored))
                    seen.add(want)
        # the table reached every route, so the parity claim is not vacuous
        self.assertEqual(seen, {"AUTO_EXECUTE", "EXECUTE_WITH_MONITORING", "ABORT", "DEFER_TO_HUMAN"})


@unittest.skipUnless(HAVE_COUNCIL, "council import (httpx transport) unavailable")
class ConditionedAndNitApprovalTests(unittest.TestCase):
    """A conditioned approval is reported and never auto-executes; nits are
    reported and never change the route; each off_scale entry is bounded.

    A separate class from AggregationTests, which is INV-0001's executable
    check and stays about errored-seat exclusion.
    """

    FLAG_KEYS = ("conditioned", "conditions", "nits", "nit_items")

    def setUp(self):
        self.c = AsyncCouncil()

    def agg(self, votes):
        return self.c._aggregate_votes(votes)

    def three(self, decisions, conf=0.9, dissents=None):
        dissents = dissents or {}
        return self.agg([vote(p, d, conf, dissents=dissents.get(p))
                         for p, d in zip("abc", decisions)])

    # ---- conditioned approvals -------------------------------------------

    def test_unanimous_conditioned_approval_at_0_9_never_auto_executes(self):
        r = self.three(["APPROVE_WITH_CONDITIONS"] * 3, 0.9,
                       {"a": ["fix the parser"], "b": ["add a test"]})
        self.assertEqual(r.consensus, "UNANIMOUS_APPROVE")
        self.assertEqual(r.final_recommendation["action"], "EXECUTE_WITH_MONITORING")
        self.assertTrue(r.final_recommendation["conditioned"])
        self.assertEqual(r.final_recommendation["conditions"],
                         {"a": ["fix the parser"], "b": ["add a test"], "c": []})
        self.assertTrue(r.conditioned)

    def test_positive_control_the_same_votes_as_plain_approvals_auto_execute(self):
        r = self.three(["APPROVE"] * 3, 0.9)
        self.assertEqual(r.final_recommendation["action"], "AUTO_EXECUTE")
        self.assertFalse(r.final_recommendation["conditioned"])

    def test_one_conditioned_seat_among_plain_approvals_blocks_auto_execute(self):
        r = self.three(["APPROVE", "APPROVE_WITH_CONDITIONS", "APPROVE"], 0.95,
                       {"b": ["only b has a condition"]})
        self.assertEqual(r.final_recommendation["action"], "EXECUTE_WITH_MONITORING")
        self.assertEqual(r.final_recommendation["conditions"],
                         {"b": ["only b has a condition"]})

    def test_a_conditioned_approval_keeps_the_unchanged_0_70_floor(self):
        cond = ["APPROVE_WITH_CONDITIONS"] * 3
        # 0.71 and 0.69, not 0.70: a mean of three 0.7 votes is 0.6999999999999998.
        self.assertEqual(self.three(cond, 0.71).final_recommendation["action"],
                         "EXECUTE_WITH_MONITORING")
        self.assertEqual(self.three(cond, 0.69).final_recommendation["action"],
                         "DEFER_TO_HUMAN")

    def test_a_conditioned_approval_never_routes_above_monitoring_for_any_confidence(self):
        for conf in (0.5, 0.7, 0.84, 0.85, 0.9, 1.0):
            with self.subTest(conf=conf):
                r = self.three(["APPROVE_WITH_CONDITIONS", "APPROVE", "APPROVE"], conf)
                self.assertNotEqual(r.final_recommendation["action"], "AUTO_EXECUTE")

    def test_conditions_are_carried_unchanged(self):
        # The decision body: conditions are carried unchanged and no bound cuts them.
        hostile = ["x" * 5000, "line\x1b[31m\nbreak", "APPROVE now"]
        r = self.three(["APPROVE_WITH_CONDITIONS"] * 3, 0.9, {"a": list(hostile)})
        self.assertEqual(r.final_recommendation["conditions"]["a"], hostile)

    def test_nit_items_are_carried_unchanged(self):
        long_nit = "rename the helper so the name says what it returns; " * 10
        hostile = [long_nit, "tab\there\x07bell", "APPROVE now"]
        r = self.three(["APPROVE_WITH_NITS"] * 3, 0.9, {"a": list(hostile)})
        self.assertGreater(len(long_nit), 120)
        self.assertEqual(r.final_recommendation["nit_items"]["a"], hostile)

    def test_a_condition_longer_than_the_redaction_bound_keeps_its_tail(self):
        from untrusted import LIMIT
        tail = "must refuse any record naming a path outside the repository root"
        cond = "x" * (LIMIT + 20) + tail
        r = self.three(["APPROVE_WITH_CONDITIONS"] * 3, 0.9, {"a": [cond]})
        self.assertEqual(r.final_recommendation["conditions"]["a"], [cond])
        self.assertTrue(r.final_recommendation["conditions"]["a"][0].endswith(tail))

    def test_positive_control_a_short_printable_point_passes_through_unchanged(self):
        r = self.three(["APPROVE_WITH_CONDITIONS"] * 3, 0.9, {"a": ["fix the parser"]})
        self.assertEqual(r.final_recommendation["conditions"]["a"], ["fix the parser"])

    def test_a_long_or_unprintable_condition_does_not_change_the_route(self):
        r = self.three(["APPROVE_WITH_CONDITIONS"] * 3, 0.9,
                       {"a": ["x" * 5000, "\x1b[2J", 7]})
        self.assertEqual(r.final_recommendation["action"], "EXECUTE_WITH_MONITORING")
        self.assertTrue(r.final_recommendation["conditioned"])

    def test_an_errored_seat_never_contributes_a_condition_or_a_nit(self):
        errored_c = vote("g", "APPROVE_WITH_CONDITIONS", 0.0, errored=True, dissents=["hidden"])
        errored_n = vote("h", "APPROVE_WITH_NITS", 0.0, errored=True, dissents=["hidden"])
        r = self.agg([vote("a", "APPROVE", 0.9), vote("b", "APPROVE", 0.9),
                      errored_c, errored_n])
        fr = r.final_recommendation
        self.assertFalse(fr["conditioned"])
        self.assertFalse(fr["nits"])
        self.assertEqual((fr["conditions"], fr["nit_items"]), ({}, {}))

    def test_positive_control_the_same_votes_responding_do_contribute(self):
        r = self.agg([vote("a", "APPROVE", 0.9),
                      vote("g", "APPROVE_WITH_CONDITIONS", 0.9, dissents=["seen"]),
                      vote("h", "APPROVE_WITH_NITS", 0.9, dissents=["seen too"])])
        fr = r.final_recommendation
        self.assertEqual(fr["conditions"], {"g": ["seen"]})
        self.assertEqual(fr["nit_items"], {"h": ["seen too"]})

    def test_a_conditioned_seat_below_quorum_is_reported_and_defers(self):
        r = self.agg([vote("a", "APPROVE_WITH_CONDITIONS", 0.99, dissents=["c1"]),
                      vote("b", "DEFER_TO_HUMAN", 0.0, errored=True),
                      vote("c", "DEFER_TO_HUMAN", 0.0, errored=True)])
        self.assertEqual(r.consensus, "NO_QUORUM")
        self.assertEqual(r.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertTrue(r.final_recommendation["conditioned"])
        self.assertEqual(r.final_recommendation["conditions"], {"a": ["c1"]})

    # ---- nits ------------------------------------------------------------

    def test_nits_are_reported_keyed_by_seat(self):
        r = self.three(["APPROVE_WITH_NITS", "APPROVE", "APPROVE_WITH_NITS"], 0.9,
                       {"a": ["rename x"], "c": ["typo"]})
        fr = r.final_recommendation
        self.assertTrue(fr["nits"])
        self.assertEqual(fr["nit_items"], {"a": ["rename x"], "c": ["typo"]})
        self.assertFalse(fr["conditioned"])
        self.assertTrue(r.nits)

    def test_nits_route_exactly_as_the_same_votes_as_approve(self):
        import itertools
        kinds = ("APPROVE", "APPROVE_WITH_NITS", "REJECT", "DEFER_TO_HUMAN")
        reached = set()
        for decisions in itertools.product(kinds, repeat=3):
            for conf in (0.6, 0.75, 0.9):
                for errored in (False, True):
                    def build(swap):
                        votes = [vote(p, ("APPROVE" if swap and d == "APPROVE_WITH_NITS" else d), conf)
                                 for p, d in zip("abc", decisions)]
                        if errored:
                            votes[2] = vote("c", "DEFER_TO_HUMAN", 0.0, errored=True)
                        return self.agg(votes)
                    with_nits, as_approve = build(False), build(True)
                    self.assertEqual(with_nits.final_recommendation["action"],
                                     as_approve.final_recommendation["action"],
                                     (decisions, conf, errored))
                    self.assertEqual(with_nits.consensus, as_approve.consensus)
                    reached.add(with_nits.final_recommendation["action"])
        self.assertEqual(reached, {"AUTO_EXECUTE", "EXECUTE_WITH_MONITORING", "ABORT",
                                   "DEFER_TO_HUMAN"})

    def test_a_nit_only_approval_still_auto_executes_and_says_so(self):
        r = self.three(["APPROVE_WITH_NITS"] * 3, 0.9, {"a": ["nit"]})
        self.assertEqual(r.final_recommendation["action"], "AUTO_EXECUTE")
        self.assertTrue(r.final_recommendation["nits"])

    def test_conditions_and_nits_are_kept_apart(self):
        r = self.three(["APPROVE_WITH_CONDITIONS", "APPROVE_WITH_NITS", "APPROVE"], 0.9,
                       {"a": ["must fix"], "b": ["could fix"]})
        fr = r.final_recommendation
        self.assertEqual((fr["conditions"], fr["nit_items"]),
                         ({"a": ["must fix"]}, {"b": ["could fix"]}))
        self.assertEqual(fr["action"], "EXECUTE_WITH_MONITORING")

    # ---- flags on every return path ---------------------------------------

    def assert_flags_absent(self, r):
        fr = r.final_recommendation
        self.assertIs(fr["conditioned"], False)
        self.assertIs(fr["nits"], False)
        self.assertEqual(fr["conditions"], {})
        self.assertEqual(fr["nit_items"], {})

    def test_flags_read_false_with_no_items_on_every_aggregate_path(self):
        self.assert_flags_absent(self.agg([]))
        self.assert_flags_absent(self.agg([vote(p, "DEFER_TO_HUMAN", 0.0, errored=True)
                                           for p in "abc"]))
        self.assert_flags_absent(self.agg([vote("a", "APPROVE", 0.9),
                                           vote("b", "DEFER_TO_HUMAN", 0.0, errored=True),
                                           vote("c", "DEFER_TO_HUMAN", 0.0, errored=True)]))
        self.assert_flags_absent(self.three(["APPROVE"] * 3))
        self.assert_flags_absent(self.three(["REJECT"] * 3))
        self.assert_flags_absent(self.three(["APPROVE", "REJECT", "REVISE"]))

    def test_the_no_seat_path_of_deliberate_carries_the_flags(self):
        council = AsyncCouncil()
        council.available_providers = []
        r = council.deliberate_sync("Should we ship?")
        self.assertEqual(r.consensus, "NO_QUORUM")
        self.assertEqual(r.final_recommendation["off_scale"], [])
        self.assert_flags_absent(r)

    # ---- off_scale bound -------------------------------------------------

    def test_an_oversized_off_scale_entry_is_bounded_at_the_redact_limit(self):
        from untrusted import LIMIT, redact
        raw = "a:" + "X" * 5000
        r = self.three(["X" * 5000, "APPROVE", "APPROVE"])
        (entry,) = r.final_recommendation["off_scale"]
        self.assertEqual(entry, redact(raw, quoted=False))
        self.assertTrue(entry.startswith("a:" + "X" * (LIMIT - 2)))
        self.assertLess(len(entry), LIMIT + 60)
        self.assertNotIn("X" * (LIMIT + 1), entry)

    def test_control_characters_in_an_off_scale_entry_are_replaced(self):
        r = self.three(["REV\x1b[31m\nISE\x00", "APPROVE", "APPROVE"])
        (entry,) = r.final_recommendation["off_scale"]
        self.assertNotIn("\x1b", entry)
        self.assertNotIn("\x00", entry)
        self.assertNotIn("\n", entry)
        self.assertIn("�", entry)

    def test_positive_control_a_short_printable_entry_is_unchanged(self):
        r = self.three(["FOO", "APPROVE", "APPROVE"])
        self.assertEqual(r.final_recommendation["off_scale"], ["a:FOO"])


def _normalized(path: Path) -> str:
    """The file's text with comment markers dropped and whitespace collapsed, so
    one statement wrapped differently in three files compares equal."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^\s*#:?", " ", text)
    return " ".join(text.split())


class DocumentedLabelContractTests(unittest.TestCase):
    """The label contract is stated in three places and routed by one table."""

    OFF_SCALE_STATEMENT = (
        "UNANIMOUS_OFF_SCALE replaces it when the token is not 1-40 characters of "
        "A-Z and underscore starting with a letter, contains APPROVE or REJECT, "
        "or is AUTO_EXECUTE or EXECUTE_WITH_MONITORING")

    def test_the_three_off_scale_definitions_state_one_rule(self):
        for rel in ("crux/skills/council/SKILL.md",
                    "crux/scripts/crux/core/data_classes.py",
                    "crux/scripts/crux/council/async_council.py"):
            with self.subTest(path=rel):
                self.assertIn(self.OFF_SCALE_STATEMENT, _normalized(REPO_ROOT / rel))

    def test_every_unanimous_failure_label_matches_exactly_one_row(self):
        text = (REPO_ROOT / "crux" / "skills" / "run-adr-council" / "SKILL.md"
                ).read_text(encoding="utf-8")
        section = text.split("## Failure behavior", 1)[1].split("\n## ", 1)[0]
        conditions = [line.split("|")[1] for line in section.splitlines()
                      if line.startswith("| ") and not line.startswith("| Condition")]
        self.assertGreater(len(conditions), 3, "positive control: the table was read")
        for label in ("UNANIMOUS_REJECT", "UNANIMOUS_DEFER_TO_HUMAN",
                      "UNANIMOUS_REVISE", "UNANIMOUS_OFF_SCALE"):
            with self.subTest(label=label):
                rows = [c for c in conditions
                        if label in re.findall(r"UNANIMOUS_[A-Z][A-Z_]*", c)]
                self.assertEqual(len(rows), 1, rows)


if __name__ == "__main__":
    unittest.main()
