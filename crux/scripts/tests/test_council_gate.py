"""`council_gate`: positional classification, council-gate evaluation, review-gate evaluation.

Pure evaluation over temp repositories, no CLI. Each ADR-named fixture is a refusal or
stop paired with the passing variant it was derived from, so a gate that refused
everything would fail the positive case.

Adr book layout (13 prompts): 1 prep; 2-5 `adr-1` (3 is the council prompt, 4 addresses
findings, 5 closes); 6-9 `dev-1`; 10-12 `review-1`; 13 summary. A verify book is the same
with `verify-1`. A patch book has five prompts: verify (1), plan, implement, review (4),
summary.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402
from _council_gate_support import ts  # noqa: E402

import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402

COUNCIL_N, CLOSE_N = 3, 5
BLOCK = {"openai_top": [("F1", "Correctness", False, "blocking")]}
SECURITY_BLOCK = {"openai_top": [("S1", "Security", False, "blocking")]}
RC = "REQUEST_CHANGES"
OK = "APPROVE"


class _Base(unittest.TestCase):
    KIND = "adr"
    #: The fixture commit is the run's base_commit, so a format-1 record is admissible. A class that
    #: builds its own base history sets it false.
    BASE = True

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.env = sup.Env(Path(self._td.name) / "repo", kind=self.KIND, autocommit=True, base=self.BASE)

    def council(self, name, **kw):
        kw.setdefault("module_tag", None if self.KIND == "patch" else f"{self.KIND}-1")
        kw.setdefault("prompt", 1 if self.KIND == "patch" else COUNCIL_N)
        return self.env.write(name, self.env.council_doc(**kw))

    def gate(self, n, *artifacts, outcome="done"):
        env = self.env
        return cg.evaluate_gate(cg.classify(env.book, n), env.load_run(), env.run_path, env.root,
                                env.args_for(*artifacts), outcome)

    def one_commit(self, count):
        """Squash the last `count` commits into one: records committed together share a commit, so
        their order is a tie."""
        sup.git(self.env.root, "reset", "-q", "--soft", f"HEAD~{count}")
        sup.git(self.env.root, "commit", "-q", "-m", "records committed together")

    def fresh_env(self, name="control"):
        """A second repository for a positive control, so no record is rewritten after its commit."""
        self.env = sup.Env(Path(self._td.name) / name, kind=self.KIND, autocommit=True, base=self.BASE)

    def at_council(self, *artifacts, outcome="done"):
        return self.gate(1 if self.KIND == "patch" else COUNCIL_N, *artifacts, outcome=outcome)

    def at_close(self, *artifacts, outcome="done"):
        return self.gate(CLOSE_N, *artifacts, outcome=outcome)


class ClassifyTests(unittest.TestCase):
    def test_adr_book_classes_by_position(self):
        book = sup.make_book("adr")
        got = {n: cg.classify(book, n).cls for n in range(1, 14)}
        self.assertEqual(got, {1: "unclassified", 2: "unclassified", 3: "council", 4: "unclassified",
                               5: "module-close", 6: "unclassified", 7: "unclassified",
                               8: "unclassified", 9: "internal-review", 10: "independent-review",
                               11: "unclassified", 12: "unclassified", 13: "unclassified"})
        c = cg.classify(book, 3)
        self.assertEqual((c.module_tag, c.ordinal, c.ordinal3, c.module_kind), ("adr-1", 2, 4, "adr"))
        self.assertEqual(cg.classify(book, 5).ordinal, 4)

    def test_verify_book_classes_by_position(self):
        book = sup.make_book("verify")
        self.assertEqual(cg.classify(book, 3).cls, "council")
        self.assertEqual(cg.classify(book, 3).module_kind, "verify")
        self.assertEqual(cg.classify(book, 5).cls, "module-close")
        self.assertEqual(cg.classify(book, 10).cls, "independent-review")
        self.assertEqual(cg.classify(book, 9).cls, "internal-review")

    def test_patch_book_classes_by_phase(self):
        book = sup.make_book("patch")
        got = [cg.classify(book, n).cls for n in range(1, 6)]
        self.assertEqual(got, ["council", "unclassified", "unclassified", "independent-review",
                               "unclassified"])
        self.assertEqual(cg.classify(book, 1).phase, "verify")
        self.assertEqual(cg.classify(book, 1).ordinal3, 1)

    def test_a_grandfathered_book_classifies_nothing(self):
        # Positive control first: the same book without the flag has gates.
        self.assertEqual(cg.classify(sup.make_book("adr"), 3).cls, "council")
        book = sup.make_book("adr", cycle_grandfathered=True, grandfather_reason="old")
        self.assertEqual({cg.classify(book, n).cls for n in range(1, 14)}, {"unclassified"})

    def test_a_non_cycle_book_classifies_nothing(self):
        book = sup.make_book("adr", cycle_kind=None)
        self.assertEqual({cg.classify(book, n).cls for n in range(1, 14)}, {"unclassified"})
        del book["cycle_kind"]
        self.assertEqual({cg.classify(book, n).cls for n in range(1, 14)}, {"unclassified"})

    def test_classification_never_reads_prose(self):
        # A prompt whose text says "council" at the wrong position stays unclassified.
        book = sup.make_book("adr", prompt_text={4: "Convene the council and run the council gate."})
        self.assertEqual(cg.classify(book, 4).cls, "unclassified")
        self.assertEqual(cg.classify(book, 3).cls, "council")

    def test_an_unknown_prompt_is_unclassified(self):
        self.assertEqual(cg.classify(sup.make_book("adr"), 99).cls, "unclassified")

    def test_two_instances_of_one_module_family_count_ordinals_per_instance(self):
        book = sup.make_book("adr")
        # Retag the dev module as a second adr module: ordinals restart at its first prompt.
        for p in book["prompts"]:
            if p.get("module_tag") == "dev-1":
                p["module_tag"] = "adr-2"
        self.assertEqual(cg.classify(book, 7).cls, "council")
        self.assertEqual(cg.classify(book, 7).ordinal, 2)
        self.assertEqual(cg.classify(book, 9).cls, "module-close")


class BlockingFindingTests(unittest.TestCase):
    def test_a_nit_needs_kind_dimension_and_safety_all_in_order(self):
        base = {"id": "openai_top:F1", "dimension": "Clarity", "safety_adjacent": False, "kind": "nit"}
        self.assertFalse(cg.is_blocking(base))
        for change in ({"kind": "blocking"}, {"kind": None}, {"dimension": None}, {"dimension": ""},
                       {"dimension": "Security"}, {"dimension": "security"}, {"dimension": " SECURITY "},
                       {"safety_adjacent": True}, {"safety_adjacent": None}):
            with self.subTest(change=change):
                self.assertTrue(cg.is_blocking({**base, **change}))


class CouncilGateBasicTests(_Base):
    def test_a_converged_round_passes_at_the_council_prompt_and_at_close(self):
        r1 = self.council("r1.json")
        for v in (self.at_council(r1), self.at_close(r1)):
            self.assertEqual(v.verdict, "pass", v.reasons)
            self.assertEqual(v.stops, [])
            self.assertEqual(v.deciding_record, self.env.rel(r1))

    def test_degraded_round_with_quorum_passes_and_names_the_errored_seat(self):
        doc = self.env.council_doc(errored=("google_top",))
        r1 = self.env.write("r1.json", doc)
        self.assertTrue(doc["degraded"])
        self.assertEqual(doc["errored_seats"][0]["role"], "google_top")
        self.assertEqual(doc["errored_seats"][0]["requested_model"], "google/gemini-3.1-pro-preview")
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_a_round_with_fewer_than_two_responding_seats_is_refused(self):
        doc = self.env.council_doc(errored=("google_top", "openai_top"))
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("fewer than two seats responded", v.reasons[0])

    def test_no_council_record_is_refused_as_a_pre_record_close(self):
        v = self.at_close()
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("convened at module close with run-council.py", v.reasons[0])
        self.assertIn("self-asserted", v.reasons[0])
        # Old non-record JSON in the directory does not count as a record.
        self.env.write("old-synthesis.json", {"consensus": "UNANIMOUS_APPROVE"})
        self.assertEqual(self.at_close().verdict, "refuse")
        # Positive control: a runner record closes the same module.
        r1 = self.council("r1.json")
        self.assertEqual(self.at_close(r1).verdict, "pass")

    def test_another_modules_record_is_refused_at_module_close(self):
        other = self.council("other.json", module_tag="adr-2")
        v = self.at_close(other)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("no council record", v.reasons[0])
        own = self.council("own.json", module_tag="adr-1")
        self.assertEqual(self.at_close(own).verdict, "pass")

    def test_a_record_for_another_book_hash_or_run_is_out_of_scope(self):
        for field, value in (("book", {"id": sup.BOOK_ID, "content_hash": "sha256:" + "b" * 64}),
                             ("run_id", "RUN-002"),
                             ("book", {"id": "PB-0998", "content_hash": self.env.hash})):
            with self.subTest(field=field, value=value):
                doc = self.env.council_doc()
                doc[field] = value
                p = self.env.write("stale.json", doc)
                self.assertEqual(self.at_council(p).verdict, "refuse")
        self.assertEqual(self.at_council(self.council("ok.json")).verdict, "pass")

    def test_the_deciding_record_must_be_attached(self):
        r1 = self.council("r1.json", written_at=ts(1))
        r2 = self.council("r2.json", round=2, written_at=ts(2))
        v = self.at_council(r1)  # r2 is the latest and is not attached
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("not among the attached artifacts", v.reasons[0])
        self.assertEqual(self.at_council().verdict, "refuse")
        self.assertEqual(self.at_council(r1, r2).verdict, "pass")

    def test_an_invalid_record_anywhere_in_the_directory_refuses(self):
        r1 = self.council("r1.json")
        bad = self.env.council_doc(round=2, written_at=ts(2))
        bad["writer"] = "commander"
        self.env.write("forged.json", bad, commit=False)  # never committed, so deleting it is allowed
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("forged.json", v.reasons[0])
        (self.env.council / "forged.json").unlink()
        self.assertEqual(self.at_council(r1).verdict, "pass")

    def test_a_symlinked_record_refuses_the_gate(self):
        r1 = self.council("r1.json")
        link = self.env.council / "link.json"
        link.symlink_to(r1)
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("symlink", v.reasons[0])
        link.unlink()
        self.assertEqual(self.at_council(r1).verdict, "pass")

    def test_a_gate_outside_a_repository_refuses(self):
        r1 = self.council("r1.json")
        v = cg.evaluate_gate(cg.classify(self.env.book, COUNCIL_N), self.env.load_run(),
                             self.env.run_path, None, [self.env.rel(r1)], "done")
        self.assertEqual(v.verdict, "refuse")

    def test_admissibility_refuses_malformed_seat_sets(self):
        def mutate(doc, how):
            seats = doc["seats"]
            if how == "two-seats":
                doc["seats"] = seats[:2]
            elif how == "duplicate-role":
                seats[1]["role"] = "openai_top"
            elif how == "same-provider":
                seats[1]["provider_namespace"] = "openai"
            elif how == "null-provider":
                seats[1]["provider_namespace"] = None
            elif how == "null-served-model":
                seats[0]["served_model"] = None
            elif how == "null-registry-key":
                seats[0]["registry_key"] = None
            elif how == "null-requested-model":
                seats[2]["requested_model"] = None
            elif how == "null-decision":
                seats[2]["decision"] = None
        for how in ("two-seats", "duplicate-role", "same-provider", "null-provider",
                    "null-served-model", "null-registry-key", "null-requested-model", "null-decision"):
            with self.subTest(how=how):
                doc = self.env.council_doc()
                mutate(doc, how)
                v = self.at_council(self.env.write("r1.json", doc))
                self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")


class CouncilGateIdentityAndBindingTests(_Base):
    def test_provider_identity_is_the_requested_models_namespace_not_the_claimed_one(self):
        doc = self.env.council_doc()
        for seat, claimed in zip(doc["seats"], ("openai", "anthropic", "google")):
            seat["requested_model"] = "openai/gpt-6-astra"
            seat["served_model"] = "openai/gpt-6-astra"
            seat["provider_namespace"] = claimed
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("provider", v.reasons[0])
        # A single seat whose claimed namespace differs from its requested model refuses too.
        doc = self.env.council_doc()
        doc["seats"][1]["provider_namespace"] = "google"
        doc["seats"][2]["provider_namespace"] = "mistral"
        self.assertEqual(self.at_council(self.env.write("r1.json", doc)).verdict, "refuse")
        # Positive control: the fixture's consistent namespaces pass.
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")

    def test_a_record_of_another_council_kind_is_refused(self):
        for wrong in ("patch", "verify"):
            with self.subTest(record=wrong):
                r1 = self.council("r1.json", kind=wrong)
                v = self.at_council(r1)
                self.assertEqual(v.verdict, "refuse", v.reasons)
                self.assertIn("council_kind", v.reasons[0])
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")

    def test_a_record_bound_to_a_prompt_outside_its_module_is_refused(self):
        r1 = self.council("r1.json", prompt=12)
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("outside", v.reasons[0])
        # Positive control: every prompt of the module is a valid binding.
        for n in (2, 3, 4, 5):
            self.assertEqual(self.at_council(self.council("r1.json", prompt=n)).verdict, "pass", n)


class CouncilGateKindTests(_Base):
    KIND = "verify"

    def test_a_verify_gate_refuses_an_adr_record_and_accepts_its_own_kind(self):
        self.assertEqual(self.at_council(self.council("r1.json", kind="adr")).verdict, "refuse")
        self.assertEqual(self.at_council(self.council("r1.json", kind="verify")).verdict, "pass")


class CouncilGatePatchKindTests(_Base):
    KIND = "patch"

    def test_a_patch_gate_refuses_an_adr_kind_record(self):
        self.assertEqual(self.at_council(self.council("r1.json", kind="adr")).verdict, "refuse")
        self.assertEqual(self.at_council(self.council("r1.json", kind="patch")).verdict, "pass")


class CouncilGateHoldTests(_Base):
    def test_a_seat_with_no_approval_and_no_blocking_finding_holds(self):
        for decision in (RC, "REJECT", "DEFER_TO_HUMAN", "MAYBE"):
            with self.subTest(decision=decision):
                r1 = self.council("r1.json", decisions=(decision, OK, OK))
                v = self.at_council(r1)
                self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
                self.assertEqual(v.holds, [f"{self.env.rel(r1)}:openai_top"])

    def test_the_same_decision_with_a_blocking_finding_is_a_change_request_not_a_hold(self):
        r1 = self.council("r1.json", decisions=(RC, OK, OK), findings=BLOCK)
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "route", v.reasons)
        self.assertEqual(v.holds, [])

    def test_a_held_first_round_stops_a_converged_second_round(self):
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK))
        r2 = self.council("r2.json", round=2, written_at=ts(2))
        v = self.at_council(r2)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertEqual(len(v.holds), 1)
        # Positive control: the same first round with a blocking finding instead of a
        # hold is a change request, and the converged second round passes.
        self.fresh_env()
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.council("r2.json", round=2, written_at=ts(2))
        self.assertEqual(self.at_council(r2).verdict, "pass")

    def test_an_errored_seat_never_holds(self):
        r1 = self.council("r1.json", errored=("anthropic_top",))
        v = self.at_council(r1)
        self.assertEqual((v.verdict, v.holds), ("pass", []))

    def test_a_security_request_changes_never_passes_despite_a_majority_aggregate(self):
        doc = self.env.council_doc(decisions=(RC, OK, OK), findings=SECURITY_BLOCK)
        doc["aggregate"] = {"consensus": "MAJORITY_APPROVE", "action": "AUTO_EXECUTE"}
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual(v.verdict, "route", v.reasons)
        # Positive control: the same aggregate over three approvals passes.
        ok = self.env.council_doc()
        ok["aggregate"] = {"consensus": "MAJORITY_APPROVE", "action": "AUTO_EXECUTE"}
        self.assertEqual(self.at_council(self.env.write("r1.json", ok)).verdict, "pass")

    def test_a_unanimous_aggregate_cannot_pass_a_blocking_finding(self):
        doc = self.env.council_doc(decisions=(OK, OK, OK), findings=BLOCK)
        doc["aggregate"] = {"consensus": "UNANIMOUS_APPROVE", "action": "AUTO_EXECUTE"}
        self.assertEqual(self.at_council(self.env.write("r1.json", doc)).verdict, "route")

    def test_finding_tags_that_are_missing_or_unsafe_block(self):
        nit_ok = [("N1", "Clarity", False, "nit")]
        cases = {
            "valid nit": (nit_ok, "pass"),
            "missing dimension": ([("N1", None, False, "nit")], "route"),
            "security nit": ([("N1", "Security", False, "nit")], "route"),
            "safety-adjacent nit": ([("N1", "Clarity", True, "nit")], "route"),
            "missing safety tag": ([("N1", "Clarity", None, "nit")], "route"),
            "missing kind": ([("N1", "Clarity", False, None)], "route"),
        }
        for name, (items, want) in cases.items():
            with self.subTest(name=name):
                r1 = self.council("r1.json", decisions=(OK, "APPROVE_WITH_NITS", OK),
                                  findings={"anthropic_top": items})
                self.assertEqual(self.at_council(r1).verdict, want)

    def test_a_scan_refused_round_stops_as_a_contradicted_premise(self):
        reason = {"code": "secret-scan", "names": ["subjects"]}
        r1 = self.council("r1.json", refusal_reason=reason)
        v = self.at_council(r1)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        # Positive control: the same record without the refusal passes.
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")


class CouncilGateCouldNotRunTests(_Base):
    def test_a_deciding_could_not_run_record_stops_as_a_contradicted_premise(self):
        r1 = self.council("r1.json", written_at=ts(1))
        cnr = self.council("r2.json", written_at=ts(2), outcome="could-not-run", round=2)
        v = self.at_council(r1, cnr)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("could not run", v.reasons[0])
        # Deleting the committed stop record is not the remedy: the gate refuses the deletion.
        cnr.unlink()
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(cnr), v.reasons[0])
        # Positive control: the same converged round, with no could-not-run record ever
        # committed beside it, decides and passes.
        clean = sup.Env(Path(self._td.name) / "clean", kind=self.KIND, autocommit=True)
        rec = clean.write("r1.json", clean.council_doc(written_at=ts(1)))
        v = cg.evaluate_gate(cg.classify(clean.book, COUNCIL_N), clean.load_run(), clean.run_path,
                             clean.root, clean.args_for(rec), "done")
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_a_could_not_run_record_takes_no_place(self):
        cnr = self.council("r0.json", written_at=ts(1), outcome="could-not-run")
        r1 = self.council("r1.json", written_at=ts(2), round=1)
        v = self.at_council(cnr, r1)
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_a_could_not_run_alone_stops(self):
        cnr = self.council("r0.json", outcome="could-not-run")
        v = self.at_council(cnr)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]))
        self.assertEqual(v.changed_subjects, [])


class CouncilGateRoundBoundTests(_Base):
    def test_a_reused_round_number_stops(self):
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.council("r2.json", written_at=ts(2), round=1)
        v = self.at_council(r2)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertIn("numbered round 1 but takes place 2", " ".join(v.reasons))
        r2 = self.council("r2.json", written_at=ts(2), round=2)
        self.assertEqual(self.at_council(r2).verdict, "pass")

    def test_a_record_tie_stops(self):
        r1 = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.council("r2.json", written_at=ts(1), round=2)
        self.one_commit(2)
        v = self.at_council(r1, r2)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertIn("tie", v.reasons[0])
        # Positive control: the same two records on separate commits are ordered, not tied.
        self.fresh_env()
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.council("r2.json", written_at=ts(1), round=2)
        self.assertEqual(self.at_council(r2).verdict, "pass")

    def test_a_tie_stop_needs_its_tied_records_attached(self):
        r1 = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.council("r2.json", written_at=ts(1), round=2)
        self.one_commit(2)
        for attached in ((), (r1,), (r2,)):
            with self.subTest(attached=len(attached)):
                v = self.at_council(*attached)
                self.assertEqual(v.verdict, "refuse", v.reasons)
                self.assertIn("tie", " ".join(v.reasons))
        # Positive control: with both tied records attached the same tie stops.
        self.assertEqual(self.at_council(r1, r2).stops, [1])

    def test_a_third_adr_council_stops_without_an_owner_exception(self):
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        r3 = self.council("r3.json", round=3, written_at=ts(3))
        v = self.at_council(r3)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertIn("third adr council", v.reasons[0])

    def test_an_owner_exception_authorizes_a_third_adr_council_that_closes_on_convergence(self):
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        self.env.write("exception.json", self.env.owner_doc())
        # Closes only on convergence: the authorized round, unconverged, stops.
        r3 = self.council("r3.json", round=3, written_at=ts(3), decisions=(RC, OK, OK), findings=BLOCK)
        v = self.at_council(r3)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        r3 = self.council("r3.json", round=3, written_at=ts(3))
        self.assertEqual(self.at_council(r3).verdict, "pass")

    def test_an_exception_for_another_module_or_place_does_not_authorize(self):
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        r3 = self.council("r3.json", round=3, written_at=ts(3))
        self.env.write("exception.json", self.env.owner_doc(module_tag="adr-2"))
        self.assertEqual(self.at_council(r3).verdict, "stop")
        self.env.write("exception.json", self.env.owner_doc(kind="round-above-three", round=4))
        self.assertEqual(self.at_council(r3).verdict, "stop")
        self.env.write("exception.json", self.env.owner_doc())
        self.assertEqual(self.at_council(r3).verdict, "pass")

    def test_an_adr_round_above_three_needs_its_own_exception(self):
        for i in (1, 2):
            self.council(f"r{i}.json", round=i, written_at=ts(i), decisions=(RC, OK, OK), findings=BLOCK)
        self.council("r3.json", round=3, written_at=ts(3), decisions=(RC, OK, OK), findings=BLOCK)
        r4 = self.council("r4.json", round=4, written_at=ts(4))
        self.env.write("e3.json", self.env.owner_doc())
        v = self.at_council(r4)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.env.write("e4.json", self.env.owner_doc(kind="round-above-three", round=4))
        self.assertEqual(self.at_council(r4).verdict, "pass")


class CouncilGateRefutationTests(_Base):
    def adr_with_r1(self, **kw):
        kw.setdefault("decisions", (RC, OK, OK))
        kw.setdefault("findings", BLOCK)
        return self.council("r1.json", written_at=ts(1), **kw)

    def refute(self, council, name="ref.json", **kw):
        kw.setdefault("written_at", ts(5))
        doc = self.env.refutation_doc(council, **kw)
        return self.env.write(name, doc), doc

    def test_a_conductor_refutation_of_every_blocking_finding_passes(self):
        r1 = self.adr_with_r1()
        self.assertEqual(self.at_council(r1).verdict, "route")
        ref, _ = self.refute(r1)
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "pass", v.reasons)
        self.assertEqual(v.deciding_record, self.env.rel(ref))

    def test_an_unrefuted_confirmed_inconclusive_or_missing_entry_leaves_the_round_unclosed(self):
        r1 = self.adr_with_r1(findings={"openai_top": [("F1", "Correctness", False, "blocking"),
                                                       ("F2", "Clarity", False, "blocking")]})
        cases = {
            "missing entry": [("openai_top:F1", "exit:0", "exit:0")],
            "confirmed": [("openai_top:F1", "exit:0", "exit:0"), ("openai_top:F2", "exit:1", "exit:0")],
            "inconclusive": [("openai_top:F1", "exit:0", "exit:0"),
                             ("openai_top:F2", "verdict:INCONCLUSIVE", "exit:0")],
        }
        for name, entries in cases.items():
            with self.subTest(name=name):
                ref, _ = self.refute(r1, entries=entries)
                v = self.at_council(ref)
                self.assertEqual(v.verdict, "route", v.reasons)
                self.assertEqual(v.route_to, 4)
        both = [("openai_top:F1", "exit:0", "exit:0"), ("openai_top:F2", "exit:0", "exit:0")]
        ref, _ = self.refute(r1, entries=both)
        self.assertEqual(self.at_council(ref).verdict, "pass")

    def test_a_result_word_without_a_token_is_refused_by_the_schema(self):
        r1 = self.adr_with_r1()
        ref, doc = self.refute(r1)
        doc["entries"][0]["result_token"] = "refuted"
        self.env.write("ref.json", doc)
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("refutation-record schema", v.reasons[0])
        ref, _ = self.refute(r1)
        self.assertEqual(self.at_council(ref).verdict, "pass")

    def test_a_recorded_result_differing_from_the_derived_one_is_refused(self):
        r1 = self.adr_with_r1()
        ref, doc = self.refute(r1, entries=[("openai_top:F1", "exit:1", "exit:0")])
        self.assertEqual(doc["entries"][0]["result"], "confirmed")
        doc["entries"][0]["result"] = "refuted"
        self.env.write("ref.json", doc)
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("derives confirmed", v.reasons[0])
        ref, _ = self.refute(r1)
        self.assertEqual(self.at_council(ref).verdict, "pass")

    def test_unknown_and_duplicate_entries_are_refused(self):
        r1 = self.adr_with_r1()
        for name, entries, needle in (
                ("unknown", [("openai_top:F1", "exit:0", "exit:0"), ("google_top:Z9", "exit:0", "exit:0")],
                 "does not carry"),
                ("duplicate", [("openai_top:F1", "exit:0", "exit:0"), ("openai_top:F1", "exit:0", "exit:0")],
                 "two entries")):
            with self.subTest(name=name):
                ref, _ = self.refute(r1, entries=entries)
                v = self.at_council(ref)
                self.assertEqual(v.verdict, "refuse")
                self.assertIn(needle, v.reasons[0])

    def test_an_artifact_must_be_an_in_repo_regular_file(self):
        r1 = self.adr_with_r1()
        for name, mutate, needle in (
                ("missing", lambda d: d["entries"][0].update(artifact="docs/checks/none.txt"), "regular file"),
                ("escape", lambda d: d["entries"][0].update(artifact="../outside.txt"), "outside"),
                ("absolute", lambda d: d["entries"][0].update(artifact="/etc/hosts"), "outside")):
            with self.subTest(name=name):
                ref, doc = self.refute(r1)
                mutate(doc)
                self.env.write("ref.json", doc)
                v = self.at_council(ref)
                self.assertEqual(v.verdict, "refuse")
                self.assertIn(needle, v.reasons[0])
        ref, doc = self.refute(r1)
        link = self.env.root / "docs" / "checks" / "link.txt"
        link.symlink_to(self.env.root / doc["entries"][0]["artifact"])
        doc["entries"][0]["artifact"] = "docs/checks/link.txt"
        self.env.write("ref.json", doc)
        self.assertEqual(self.at_council(ref).verdict, "refuse")

    def test_a_stale_conductor_hash_is_refused(self):
        r1 = self.adr_with_r1()
        stale = [{"path": sup.SUBJECT, "sha256": "e" * 64}]
        ref, _ = self.refute(r1, subjects=stale)
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("stale conductor hash", v.reasons[0])
        ref, _ = self.refute(r1)  # positive control: the round's own subject hash
        self.assertEqual(self.at_council(ref).verdict, "pass")

    def test_a_refutation_naming_a_changed_council_record_is_refused(self):
        r1 = self.adr_with_r1()
        ref, doc = self.refute(r1)
        doc["council_record"]["sha256"] = "d" * 64
        self.env.write("ref.json", doc)
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("council_record.sha256", v.reasons[0])

    def test_a_refutation_naming_a_record_outside_the_repository_or_module_is_refused(self):
        r1 = self.adr_with_r1()
        other = self.council("other.json", module_tag="adr-2", written_at=ts(2))
        ref, _ = self.refute(other)
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("no counted round of this module", v.reasons[0])
        ref, doc = self.refute(r1)
        doc["council_record"]["path"] = "../escape.json"
        self.env.write("ref.json", doc)
        self.assertEqual(self.at_council(ref).verdict, "refuse")

    def test_a_conductor_refutation_must_name_the_latest_counted_round(self):
        r1 = self.adr_with_r1()
        self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        ref, _ = self.refute(r1)  # refutes round 1 after round 2 failed
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("not the latest counted round", v.reasons[0])

    def test_a_conductor_refutation_cannot_close_an_authorized_third_council(self):
        for i in (1, 2):
            self.council(f"r{i}.json", round=i, written_at=ts(i), decisions=(RC, OK, OK), findings=BLOCK)
        r3 = self.council("r3.json", round=3, written_at=ts(3), decisions=(RC, OK, OK), findings=BLOCK)
        self.env.write("e.json", self.env.owner_doc())
        ref, _ = self.refute(r3)
        v = self.at_council(ref)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)

    def adjudication(self, recorder="adjudicator-1", entries=None):
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        ref, _ = self.refute(r2, role="adjudicator", recorder=recorder, written_at=ts(3), entries=entries)
        return r2, ref

    def test_an_adjudicator_refutation_takes_place_three_and_closes_when_all_refuted(self):
        _, ref = self.adjudication()
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_a_confirmed_adjudication_stops(self):
        _, ref = self.adjudication(entries=[("openai_top:F1", "exit:1", "exit:0")])
        v = self.at_council(ref)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertIn("confirmed, inconclusive or unchecked", v.reasons[0])

    def test_an_adjudicator_with_an_inconclusive_check_stops(self):
        _, ref = self.adjudication(entries=[("openai_top:F1", "verdict:INCONCLUSIVE", "exit:0")])
        self.assertEqual(self.at_council(ref).stops, [1])

    def test_a_conductor_claiming_adjudicator_is_refused(self):
        r1 = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.env.write("conductor.json", self.env.refutation_doc(r1, recorder="alice", written_at=ts(1, 2)))
        r2 = self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        # The conductor who recorded for round 1 now claims to adjudicate round 2.
        ref = self.env.write("adj.json", self.env.refutation_doc(
            r2, recorder="alice", role="adjudicator", written_at=ts(3)))
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("conductor claiming adjudicator", v.reasons[0])
        # Positive control: a recorder no conductor record names is admitted.
        ref = self.env.write("adj.json", self.env.refutation_doc(
            r2, recorder="bob", role="adjudicator", written_at=ts(3)))
        self.assertEqual(self.at_council(ref).verdict, "pass")

    def test_an_adjudicator_must_name_the_place_two_record(self):
        r1 = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        ref = self.env.write("adj.json", self.env.refutation_doc(
            r1, recorder="bob", role="adjudicator", written_at=ts(3)))
        v = self.at_council(ref)  # only place 1 exists
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("place-2", v.reasons[0])

    def test_a_fourth_record_after_adjudication_stops_without_an_exception(self):
        _, ref = self.adjudication()
        r4 = self.council("r4.json", round=3, written_at=ts(6))
        v = self.at_council(ref, r4)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)

    def test_an_invalid_non_deciding_refutation_is_ignored_not_trusted(self):
        r1 = self.adr_with_r1()
        bad, _ = self.refute(r1, name="stale.json", written_at=ts(2), subjects=[
            {"path": sup.SUBJECT, "sha256": "e" * 64}])
        r2 = self.council("r2.json", round=2, written_at=ts(3))  # converged, later
        v = self.at_council(r2)
        self.assertEqual(v.verdict, "pass", v.reasons)
        self.assertTrue(any("not admitted" in r for r in v.reasons))


class VerifyGateTests(_Base):
    KIND = "verify"

    def weak(self, name, i, **kw):
        kw.setdefault("decisions", (RC, OK, OK))
        kw.setdefault("findings", BLOCK)
        return self.council(name, round=i, written_at=ts(i), **kw)

    def test_a_converged_verify_round_passes(self):
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")

    def test_places_one_and_two_route_to_ordinal_three_and_close_routes_the_same_way(self):
        r1 = self.weak("r1.json", 1)
        for gate in (self.at_council, self.at_close):
            v = gate(r1)
            self.assertEqual((v.verdict, v.route_to), ("route", 4), v.reasons)
        r2 = self.weak("r2.json", 2)
        self.assertEqual(self.at_council(r2).route_to, 4)

    def test_place_three_unconverged_spends_the_bound(self):
        self.weak("r1.json", 1)
        self.weak("r2.json", 2)
        r3 = self.weak("r3.json", 3)
        v = self.at_council(r3)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertIn("bound is spent", v.reasons[0])
        self.assertEqual(self.at_council(self.council("r3.json", round=3, written_at=ts(3))).verdict, "pass")

    def test_a_fourth_council_numbered_three_stops(self):
        for i in (1, 2, 3):
            self.weak(f"r{i}.json", i)
        r4 = self.council("r4.json", round=3, written_at=ts(4))
        v = self.at_council(r4)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertIn("numbered round 3 but takes place 4", " ".join(v.reasons))
        # Positive control: the same fourth council numbered 4 and authorized passes.
        r4 = self.council("r4.json", round=4, written_at=ts(4))
        self.env.write("e.json", self.env.owner_doc(kind="round-above-three", round=4,
                                                    module_tag="verify-1"))
        self.assertEqual(self.at_council(r4).verdict, "pass")

    def test_an_unauthorized_round_four_stops_even_when_converged(self):
        for i in (1, 2, 3):
            self.weak(f"r{i}.json", i)
        r4 = self.council("r4.json", round=4, written_at=ts(4))
        self.assertEqual(self.at_council(r4).stops, [1])

    def test_an_architectural_vote_stops_as_a_contradicted_premise_and_is_never_routed(self):
        r1 = self.council("r1.json", decisions=("ARCHITECTURAL", OK, OK), findings=BLOCK)
        v = self.at_council(r1)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertEqual(v.route_to, None)

    def test_an_architectural_vote_beside_a_hold_reports_both_stops(self):
        r1 = self.council("r1.json", decisions=("ARCHITECTURAL", OK, OK))
        v = self.at_council(r1)
        self.assertEqual((v.verdict, v.stops), ("stop", [1, 4]), v.reasons)
        self.assertEqual(len(v.holds), 1)
        # Positive control: the same record with approvals only passes.
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")

    def test_an_architectural_token_in_an_adr_round_is_only_a_hold(self):
        # The vote scale of an adr council has no ARCHITECTURAL token.
        env = sup.Env(Path(self._td.name) / "adr", kind="adr", autocommit=True)
        doc = env.council_doc(decisions=("ARCHITECTURAL", OK, OK))
        p = env.write("r1.json", doc)
        v = cg.evaluate_gate(cg.classify(env.book, 3), env.load_run(), env.run_path, env.root,
                             [env.rel(p)], "done")
        self.assertEqual((v.verdict, v.stops), ("stop", [1]))

    def test_an_adr_refutation_record_is_out_of_scope(self):
        r1 = self.weak("r1.json", 1)
        adr_ref = self.env.refutation_doc(r1, written_at=ts(5), module_tag="adr-1")
        ref = self.env.write("ref.json", adr_ref)
        v = self.at_council(r1, ref)
        self.assertEqual(v.verdict, "route", v.reasons)

    def test_a_refutation_in_scope_of_a_non_adr_gate_is_refused(self):
        # Defensive: the schema keeps a refutation record to adr modules, so build the
        # in-scope case by retagging after the schema, directly against the evaluator.
        r1 = self.weak("r1.json", 1)
        gate = cg.classify(self.env.book, COUNCIL_N)
        # The record's path is r1's committed file, so the evidence checks pass and the kind rule decides.
        rec = cr.Record(r1,
                        {"record_type": "refutation-record", **self.env.refutation_doc(
                            r1, written_at=ts(5), module_tag="verify-1")}, "refutation-record")
        orig = cr.discover_records
        cr.discover_records = lambda d: orig(d) + [rec]
        try:
            v = cg.evaluate_council_gate(gate, self.env.load_run(), self.env.run_path, self.env.root,
                                         [self.env.rel(r1)])
        finally:
            cr.discover_records = orig
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("only an adr module closes on one", v.reasons[0])


class PatchGateTests(_Base):
    KIND = "patch"

    def weak(self, name, i):
        return self.council(name, round=i, written_at=ts(i), decisions=(RC, OK, OK), findings=BLOCK)

    def test_a_converged_patch_round_passes(self):
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")

    def test_places_one_and_two_route_back_to_the_same_prompt(self):
        v = self.at_council(self.weak("r1.json", 1))
        self.assertEqual((v.verdict, v.route_to), ("route", 1), v.reasons)
        v = self.at_council(self.weak("r2.json", 2))
        self.assertEqual((v.verdict, v.route_to), ("route", 1), v.reasons)

    def test_place_three_unconverged_and_an_unauthorized_fourth_stop(self):
        for i in (1, 2):
            self.weak(f"r{i}.json", i)
        r3 = self.weak("r3.json", 3)
        self.assertEqual(self.at_council(r3).stops, [1])
        r3 = self.council("r3.json", round=3, written_at=ts(3))
        self.assertEqual(self.at_council(r3).verdict, "pass")
        r4 = self.council("r4.json", round=4, written_at=ts(4))
        self.assertEqual(self.at_council(r4).stops, [1])
        self.env.write("e.json", self.env.owner_doc(kind="round-above-three", round=4,
                                                    module_tag=None, prompt=1))
        self.assertEqual(self.at_council(r4).verdict, "pass")

    def test_a_record_bound_to_another_prompt_or_a_module_is_out_of_scope(self):
        other_prompt = self.council("r1.json", prompt=4)
        self.assertEqual(self.at_council(other_prompt).verdict, "refuse")
        in_module = self.env.write("r2.json", self.env.council_doc(module_tag="adr-1", prompt=1))
        self.assertEqual(self.at_council(in_module).verdict, "refuse")
        self.assertEqual(self.at_council(self.council("r3.json", prompt=1)).verdict, "pass")


class ChangedSubjectTests(_Base):
    def test_a_subject_that_changed_after_the_round_is_reported_not_refused(self):
        r1 = self.council("r1.json")
        self.assertEqual(self.at_council(r1).changed_subjects, [])
        recorded = self.env.sha(sup.SUBJECT)
        self.env.subject.write_text("# subject v2\n")
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "pass", v.reasons)
        self.assertEqual(v.changed_subjects, [{"path": sup.SUBJECT, "recorded": recorded,
                                               "current": self.env.sha(sup.SUBJECT)}])

    def test_a_deleted_subject_reports_a_null_current_hash(self):
        r1 = self.council("r1.json")
        self.env.subject.unlink()
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "pass")
        self.assertIsNone(v.changed_subjects[0]["current"])

    def test_a_changed_subject_is_reported_through_a_deciding_refutation(self):
        r1 = self.council("r1.json", decisions=(RC, OK, OK), findings=BLOCK)
        ref = self.env.write("ref.json", self.env.refutation_doc(r1, written_at=ts(5)))
        self.env.subject.write_text("# subject v2\n")
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "pass")
        self.assertEqual(len(v.changed_subjects), 1)


class VerdictShapeTests(_Base):
    def test_the_verdict_dict_carries_every_documented_key_and_sorted_stops(self):
        r1 = self.council("r1.json", decisions=("ARCHITECTURAL", OK, OK))
        d = self.at_council(r1).to_dict()
        self.assertEqual(sorted(d), ["changed_subjects", "deciding_record", "holds", "reasons", "route_to",
                                     "stops", "verdict", "withheld_subjects"])
        self.assertEqual(d["stops"], sorted(d["stops"]))


# ───────────────────────────── independent-review gate ─────────────────────────────

REVIEW_N = 10


class ReviewGateTests(_Base):
    def review(self, *artifacts, outcome="done", n=REVIEW_N):
        return self.gate(n, *artifacts, outcome=outcome)

    def report(self, name="report.json", **kw):
        kw.setdefault("prompt", REVIEW_N)
        return self.env.write(name, self.env.report_doc(**kw), folder=self.env.run_dir / "reviews")

    # -- paths form -------------------------------------------------------------

    def test_a_clean_paths_report_passes(self):
        v = self.review(self.report())
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_paths_form_refuses_a_staged_change(self):
        rep = self.report()  # records the committed hash
        self.env.subject.write_text("# changed\n")
        sup.git(self.env.root, "add", sup.SUBJECT)
        v = self.review(rep)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("staged change", v.reasons[0])

    def test_paths_form_refuses_a_staged_change_that_matches_the_declared_hash(self):
        # The report names the NEW bytes, which are staged but not committed.
        self.env.subject.write_text("# changed\n")
        sup.git(self.env.root, "add", sup.SUBJECT)
        v = self.review(self.report())
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("committed content differs", v.reasons[0])

    def test_paths_form_refuses_an_unstaged_change(self):
        rep = self.report()
        self.env.subject.write_text("# changed\n")
        v = self.review(rep)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("unstaged change", v.reasons[0])

    def test_paths_form_refuses_content_that_differs_from_what_was_reviewed(self):
        rep = self.report()
        self.env.subject.write_text("# changed and committed\n")
        sup.commit_all(self.env.root, "change")
        v = self.review(rep)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("differs from what the report reviewed", v.reasons[0])

    def test_paths_form_refuses_untracked_symlinked_and_escaping_paths(self):
        (self.env.root / "new.txt").write_text("x\n")
        untracked = self.report("a.json", paths=["new.txt"])
        self.assertIn("not tracked", self.review(untracked).reasons[0])
        (self.env.root / "link.md").symlink_to("docs/adrs/ADR-0001-fixture.md")
        sup.commit_all(self.env.root, "link")
        symlinked = self.report("b.json", paths=["link.md"])
        self.assertIn("symlink", self.review(symlinked).reasons[0])
        doc = self.env.report_doc(prompt=REVIEW_N)
        doc["subject"]["paths"][0]["path"] = "../escape.txt"
        escaping = self.env.write("c.json", doc, folder=self.env.run_dir / "reviews")
        self.assertIn("outside", self.review(escaping).reasons[0])
        self.assertEqual(self.review(self.report("ok.json")).verdict, "pass")

    # -- commit-range form ------------------------------------------------------

    def range_repo(self):
        root = self.env.root
        base = sup.git(root, "rev-parse", "HEAD").strip()
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("a = 1\n")
        (root / "src" / "b.py").write_text("b = 1\n")
        sup.commit_all(root, "range")
        end = sup.git(root, "rev-parse", "HEAD").strip()
        return base, end

    def test_a_clean_range_report_passes(self):
        base, end = self.range_repo()
        v = self.review(self.report(range_=f"{base}..{end}"))
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_range_form_refuses_a_staged_change(self):
        base, end = self.range_repo()
        rep = self.report(range_=f"{base}..{end}")
        (self.env.root / "src" / "a.py").write_text("a = 2\n")
        sup.git(self.env.root, "add", "src/a.py")
        v = self.review(rep)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("src/a.py: has a staged change", v.reasons[0])

    def test_range_form_refuses_an_unstaged_change(self):
        base, end = self.range_repo()
        rep = self.report(range_=f"{base}..{end}")
        (self.env.root / "src" / "a.py").write_text("a = 2\n")
        v = self.review(rep)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("src/a.py: has an unstaged change", v.reasons[0])

    def test_range_form_refuses_a_later_commit_that_changes_a_reviewed_path(self):
        base, end = self.range_repo()
        rep = self.report(range_=f"{base}..{end}")
        (self.env.root / "src" / "a.py").write_text("a = 2\n")
        sup.commit_all(self.env.root, "later")
        v = self.review(rep)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("a commit after the range end changes it", v.reasons[0])
        # A later commit that touches only an unreviewed path leaves the report valid.
        sup.git(self.env.root, "reset", "-q", "--hard", end)
        (self.env.root / "other.txt").write_text("o\n")
        sup.commit_all(self.env.root, "unrelated")
        again = self.report("again.json", range_=f"{base}..{end}")
        self.assertEqual(self.review(again).verdict, "pass")

    def test_range_form_refuses_an_end_that_is_not_an_ancestor_of_head(self):
        base, end = self.range_repo()
        sup.git(self.env.root, "checkout", "-q", "-b", "side", base)
        (self.env.root / "side.txt").write_text("s\n")
        sup.commit_all(self.env.root, "side")
        side = sup.git(self.env.root, "rev-parse", "HEAD").strip()
        sup.git(self.env.root, "checkout", "-q", "main")
        v = self.review(self.report(range_=f"{base}..{side}"))
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("not HEAD or an ancestor", v.reasons[0])

    def test_range_form_refuses_an_unresolvable_end_or_an_empty_range(self):
        base, end = self.range_repo()
        v = self.review(self.report(range_=f"{base}..{'f' * 40}"))
        self.assertIn("not a commit", v.reasons[0])
        v = self.review(self.report("e.json", range_=f"{end}..{end}"))
        self.assertIn("changes no path", v.reasons[0])

    def test_a_deleted_path_stays_absent_and_a_recreated_one_refuses(self):
        root = self.env.root
        base, end = self.range_repo()
        sup.git(root, "rm", "-q", "src/b.py")
        sup.commit_all(root, "delete b")
        end2 = sup.git(root, "rev-parse", "HEAD").strip()
        rep = self.report(range_=f"{end}..{end2}")
        self.assertEqual(self.review(rep).verdict, "pass")
        (root / "src" / "b.py").write_text("b = 1\n")  # present again, untracked
        v = self.review(rep)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("src/b.py: is not tracked", v.reasons[0])

    # -- gate-level rules -------------------------------------------------------

    def test_no_attached_report_is_refused(self):
        v = self.review()
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("no reviewer report", v.reasons[0])
        # Positive control: one valid report passes.
        self.assertEqual(self.review(self.report()).verdict, "pass")

    def test_an_attached_artifact_that_is_not_json_is_refused_not_skipped(self):
        notes = self.env.root / "notes.md"
        notes.write_text("# n\n")
        rep = self.report()
        # Positive control: the report alone passes.
        self.assertEqual(self.review(rep).verdict, "pass")
        # A report with a non-.json artifact beside it is refused, naming the artifact.
        v = self.review(rep, notes)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("notes.md is not a reviewer report (.json)", v.reasons[0])

    def test_a_council_record_never_satisfies_the_review_gate(self):
        rep = self.report()
        council = self.council("r1.json")
        v = self.review(council)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("a council verdict never satisfies", v.reasons[0])
        v = self.review(rep, council)  # attached beside a valid report
        self.assertEqual(v.verdict, "refuse")
        self.assertEqual(self.review(rep).verdict, "pass")

    def test_non_report_json_is_refused(self):
        rep = self.report()
        junk = self.env.write("consensus.json", {"consensus": "UNANIMOUS_APPROVE"}, folder=self.env.run_dir)
        v = self.review(rep, junk)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("not a reviewer report", v.reasons[0])
        listy = self.env.run_dir / "list.json"
        listy.write_text("[1, 2]")
        self.assertEqual(self.review(rep, listy).verdict, "refuse")

    def test_an_invalid_report_is_refused(self):
        doc = self.env.report_doc(prompt=REVIEW_N)
        doc["reviewer_role"] = "council"
        bad = self.env.write("bad.json", doc, folder=self.env.run_dir / "reviews")
        v = self.review(bad)
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("reviewer-report schema", v.reasons[0])

    def test_a_report_bound_to_another_prompt_book_or_run_is_refused(self):
        wrong_prompt = self.report("p.json", prompt=11)
        self.assertIn("not prompt 10", self.review(wrong_prompt).reasons[0])
        doc = self.env.report_doc(prompt=REVIEW_N)
        doc["book"]["content_hash"] = "sha256:" + "b" * 64
        stale = self.env.write("s.json", doc, folder=self.env.run_dir / "reviews")
        self.assertIn("not bound to this book and run", self.review(stale).reasons[0])
        doc = self.env.report_doc(prompt=REVIEW_N)
        doc["run_id"] = "RUN-009"
        self.assertEqual(self.review(self.env.write("r.json", doc, folder=self.env.run_dir / "reviews")).verdict,
                         "refuse")
        self.assertEqual(self.review(self.report("ok.json")).verdict, "pass")

    def test_every_attached_report_must_be_valid(self):
        good = self.report("good.json")
        self.env.subject.write_text("# changed\n")
        bad = self.report("bad.json")  # names bytes that are not committed
        self.assertEqual(self.review(good, bad).verdict, "refuse")
        self.env.subject.write_text("# subject v1\n")
        self.assertEqual(self.review(self.report("good.json")).verdict, "pass")

    def test_five_valid_reports_pass_an_independent_review_gate(self):
        reports = [self.report(f"r{i}.json", written_at=ts(30 + i)) for i in range(5)]
        v = self.review(*reports)
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_a_valid_report_with_outcome_blocked_is_admitted_with_no_stop_and_an_invalid_one_refuses(self):
        rep = self.report()
        v = self.review(rep, outcome="blocked")
        self.assertEqual((v.verdict, v.stops), ("pass", []), v.reasons)
        self.assertEqual(v.deciding_record, self.env.rel(rep))
        self.env.subject.write_text("# changed\n")
        self.assertEqual(self.review(rep, outcome="blocked").verdict, "refuse")

    def test_a_report_must_lie_inside_the_repository(self):
        outside = Path(self._td.name) / "outside.json"
        outside.write_text(json.dumps(self.env.report_doc(prompt=REVIEW_N)))
        v = cg.evaluate_gate(cg.classify(self.env.book, REVIEW_N), self.env.load_run(), self.env.run_path,
                             self.env.root, [str(outside)], "done")
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("outside", v.reasons[0])

    def test_a_report_attached_at_a_council_gate_does_not_pass_it(self):
        rep = self.report()
        v = self.gate(COUNCIL_N, rep)
        self.assertEqual(v.verdict, "refuse")

    def test_a_missing_attachment_is_refused(self):
        v = cg.evaluate_gate(cg.classify(self.env.book, REVIEW_N), self.env.load_run(), self.env.run_path,
                             self.env.root, ["docs/none.json"], "done")
        self.assertEqual(v.verdict, "refuse")
        self.assertIn("does not exist", v.reasons[0])


# ───────────────────────────── PB-0136 internal-review fixes ─────────────────────────────


class ClassifyFromRunStartTests(unittest.TestCase):
    """`cycle_grandfathered` and `cycle_kind` lie outside the content hash, so the execution
    boundary classifies from their run-start values (`StartFields`), never from the live book."""

    def test_unknown_run_start_classifies_from_the_hash_bound_fields_alone(self):
        unknown = cg.StartFields(known=False)
        # Positive control: the plain book has its gates with an unknown start too.
        self.assertEqual(cg.classify(sup.make_book("adr"), 3, unknown).cls, "council")
        grandfathered = sup.make_book("adr", cycle_grandfathered=True, grandfather_reason="x")
        self.assertEqual(cg.classify(grandfathered, 3, unknown).cls, "council")
        self.assertEqual(cg.classify(grandfathered, 5, unknown).cls, "module-close")
        self.assertEqual(cg.classify(grandfathered, 10, unknown).cls, "independent-review")
        no_kind = sup.make_book("adr")
        del no_kind["cycle_kind"]
        self.assertEqual(cg.classify(no_kind, 3, unknown).cls, "council")
        verify = sup.make_book("verify")
        del verify["cycle_kind"]
        self.assertEqual(cg.classify(verify, 3, unknown).module_kind, "verify")
        patch = sup.make_book("patch", cycle_grandfathered=True, grandfather_reason="x")
        del patch["cycle_kind"]
        self.assertEqual([cg.classify(patch, n, unknown).cls for n in range(1, 6)],
                         ["council", "unclassified", "unclassified", "independent-review",
                          "unclassified"])

    def test_known_run_start_values_classify_and_a_book_grandfathered_at_start_stays_unclassified(self):
        book = sup.make_book("adr", cycle_grandfathered=True, grandfather_reason="x")
        at_start = cg.StartFields(known=True, cycle_kind="adr", cycle_grandfathered=True)
        self.assertEqual({cg.classify(book, n, at_start).cls for n in range(1, 14)}, {"unclassified"})
        # Positive control: the run-start values decide, not the live book's.
        live_flag_only = cg.StartFields(known=True, cycle_kind="adr", cycle_grandfathered=None)
        self.assertEqual(cg.classify(book, 3, live_flag_only).cls, "council")
        no_kind_at_start = cg.StartFields(known=True, cycle_kind=None, cycle_grandfathered=None)
        self.assertEqual(cg.classify(sup.make_book("adr"), 3, no_kind_at_start).cls, "unclassified")

    def test_unbound_field_changes_names_each_field_that_moved_since_run_start(self):
        at_start = cg.StartFields(known=True, cycle_kind="adr", cycle_grandfathered=None)
        self.assertEqual(cg.unbound_field_changes(sup.make_book("adr"), at_start), [])
        gf = sup.make_book("adr", cycle_grandfathered=True, grandfather_reason="x")
        self.assertEqual(cg.unbound_field_changes(gf, at_start), ["cycle_grandfathered"])
        no_kind = sup.make_book("adr")
        del no_kind["cycle_kind"]
        self.assertEqual(cg.unbound_field_changes(no_kind, at_start), ["cycle_kind"])
        # An unknown start has nothing to compare against.
        self.assertEqual(cg.unbound_field_changes(gf, cg.StartFields(known=False)), [])


class StartFieldsFromHistoryTests(_Base):
    """`start_fields` reads the book at the snapshot's first committed `base_commit`, at the
    book's path or its active/archive counterpart."""

    BASE = False  # each case pins its own base_commit

    def head(self) -> str:
        return sup.git(self.env.root, "rev-parse", "HEAD").strip()

    def pin(self, base: str | None = None, **extra) -> dict:
        run = self.env.load_run()
        run["base_commit"] = base or self.head()
        run.update(extra)
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))
        return run

    def move_book(self, folder: str) -> Path:
        dest = self.env.docs / "promptbooks" / folder / self.env.book_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        sup.git(self.env.root, "mv", self.env.rel(self.env.book_path), self.env.rel(dest))
        sup.git(self.env.root, "commit", "-q", "-m", f"move the book to {folder}")
        return dest

    def test_a_book_archived_after_run_start_is_read_at_its_active_counterpart(self):
        run = self.pin()
        archived = self.move_book("archive")
        start = cg.start_fields(run, self.env.run_path, archived)
        self.assertEqual((start.known, start.cycle_kind), (True, "adr"))
        # Control: a move that is not an active/archive counterpart has no path at base_commit.
        elsewhere = self.env.docs / "promptbooks" / "elsewhere" / archived.name
        elsewhere.parent.mkdir()
        sup.git(self.env.root, "mv", self.env.rel(archived), self.env.rel(elsewhere))
        sup.git(self.env.root, "commit", "-q", "-m", "elsewhere")
        self.assertFalse(cg.start_fields(run, self.env.run_path, elsewhere).known)

    def test_a_book_active_again_after_run_start_is_read_at_its_archive_counterpart(self):
        archived = self.move_book("archive")
        run = self.pin()
        active = self.env.docs / "promptbooks" / "active" / archived.name
        sup.git(self.env.root, "mv", self.env.rel(archived), self.env.rel(active))
        sup.git(self.env.root, "commit", "-q", "-m", "back to active")
        start = cg.start_fields(run, self.env.run_path, active)
        self.assertEqual((start.known, start.cycle_kind), (True, "adr"))

    def test_the_first_committed_base_decides_and_a_later_rewrite_is_flagged(self):
        first = self.head()
        self.pin(first)
        sup.commit_all(self.env.root, "run start")
        later = self.head()
        run = self.pin(later)
        start = cg.start_fields(run, self.env.run_path, self.env.book_path)
        self.assertEqual((start.known, start.base, start.base_rewritten), (True, first, True))
        # Positive control: the untouched value is not flagged.
        run = self.pin(first)
        start = cg.start_fields(run, self.env.run_path, self.env.book_path)
        self.assertEqual((start.known, start.base, start.base_rewritten), (True, first, False))

    def test_before_the_first_commit_the_live_base_is_read(self):
        run = self.pin()  # the fixture commit holds the snapshot without a base_commit key
        start = cg.start_fields(run, self.env.run_path, self.env.book_path)
        self.assertEqual((start.known, start.base_rewritten), (True, False))

    def test_a_committed_version_that_does_not_parse_makes_the_start_unknown(self):
        good = self.env.run_path.read_bytes()
        self.env.run_path.write_text("prompts: [unclosed\n")
        sup.commit_all(self.env.root, "garbage snapshot")
        self.env.run_path.write_bytes(good)
        run = self.pin()
        sup.commit_all(self.env.root, "run start")
        self.assertFalse(cg.start_fields(run, self.env.run_path, self.env.book_path).known)

    def test_a_version_naming_another_run_is_skipped(self):
        other = self.env.load_run()
        other.update(run_id="RUN-777", base_commit="0" * 40)
        good = self.env.run_path.read_bytes()
        self.env.run_path.write_text(sup.yaml.safe_dump(other, sort_keys=False))
        sup.commit_all(self.env.root, "another run at this path")
        self.env.run_path.write_bytes(good)
        base = self.head()
        run = self.pin(base)
        sup.commit_all(self.env.root, "run start")
        start = cg.start_fields(run, self.env.run_path, self.env.book_path)
        self.assertEqual((start.known, start.base, start.base_rewritten), (True, base, False))


class ServedProviderAdmissibilityTests(_Base):
    def test_a_responding_seat_without_a_served_provider_is_refused(self):
        doc = self.env.council_doc()
        doc["seats"][2]["served_provider"] = None
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("carries no served_provider", v.reasons[0])
        # Positive control: the fixture's served provider passes.
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")


class ScanReducedDecisionTests(_Base):
    SCAN = {"code": "secret-scan", "names": ["seats[0].decision"]}

    def test_a_scan_reduced_null_decision_stops_at_four_and_never_approves(self):
        doc = self.env.council_doc(refusal_reason=self.SCAN)
        doc["seats"][0]["decision"] = None
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertEqual(v.holds, [])
        # Control: the same null decision without the scan refusal is inadmissible.
        doc = self.env.council_doc()
        doc["seats"][0]["decision"] = None
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("carries no decision", v.reasons[0])
        # Control: an unreduced converged record passes.
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")


class WithheldSubjectTests(_Base):
    def test_a_withheld_subject_path_is_reported_withheld_not_changed(self):
        doc = self.env.council_doc()
        recorded = doc["subjects"][0]["sha256"]
        doc["subjects"].append({"path": "[withheld: matched the secret scan]", "sha256": recorded,
                                "retained_copy": None})
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual(v.verdict, "pass", v.reasons)
        self.assertEqual(v.changed_subjects, [])
        self.assertEqual(v.withheld_subjects, [{"index": 1, "recorded": recorded}])
        # Positive control: a real changed subject still reports as changed.
        self.env.subject.write_text("# subject v2\n")
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual([c["path"] for c in v.changed_subjects], [sup.SUBJECT])


class RangeMergeTests(_Base):
    REVIEW_N = 10

    def report(self, rng, name="report.json"):
        return self.env.write(name, self.env.report_doc(prompt=self.REVIEW_N, range_=rng),
                              folder=self.env.run_dir / "reviews")

    def merge_after(self, rewrite_reviewed):
        """A range commit that changes src/a.py, then a merge from a side branch that changes
        only side.txt. With `rewrite_reviewed`, the merge commit itself also rewrites src/a.py."""
        root = self.env.root
        base = sup.git(root, "rev-parse", "HEAD").strip()
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("a = 1\n")
        sup.commit_all(root, "range")
        end = sup.git(root, "rev-parse", "HEAD").strip()
        sup.git(root, "checkout", "-q", "-b", "side")
        (root / "side.txt").write_text("s\n")
        sup.commit_all(root, "side")
        sup.git(root, "checkout", "-q", "main")
        (root / "main.txt").write_text("m\n")
        sup.commit_all(root, "main moves on")
        sup.git(root, "merge", "-q", "--no-ff", "--no-commit", "side")
        if rewrite_reviewed:
            (root / "src" / "a.py").write_text("a = 'rewritten by the merge'\n")
            sup.git(root, "add", "src/a.py")
        sup.git(root, "commit", "-q", "-m", "merge side")
        return base, end

    def test_a_merge_commit_that_rewrites_a_reviewed_path_is_refused(self):
        base, end = self.merge_after(rewrite_reviewed=True)
        v = self.gate(self.REVIEW_N, self.report(f"{base}..{end}"))
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("src/a.py: a commit after the range end changes it", v.reasons[0])

    def test_an_unrelated_merge_after_the_range_passes(self):
        base, end = self.merge_after(rewrite_reviewed=False)
        v = self.gate(self.REVIEW_N, self.report(f"{base}..{end}"))
        self.assertEqual(v.verdict, "pass", v.reasons)


class CommittedEvidenceTests(_Base):
    """The gate reads a record only when it is committed, and refuses a run whose committed
    records were removed or changed in the working tree (M5)."""

    def uncommitted(self, name="r1.json", **kw):
        kw.setdefault("module_tag", "adr-1")
        kw.setdefault("prompt", COUNCIL_N)
        return self.env.write(name, self.env.council_doc(**kw), commit=False)

    def snapshot_names(self, *paths):
        """Record `paths` as the artifacts of the current prompt in the run snapshot, as a blocked advance does."""
        run = self.env.load_run()
        run["prompts"][COUNCIL_N - 1]["artifacts"] = [self.env.rel(p) for p in paths]
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))

    def test_an_uncommitted_council_record_is_refused_and_the_committed_one_passes(self):
        r1 = self.uncommitted()
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("is not committed", v.reasons[0])
        # A format-1 record was written by a runner that did not commit it, and recovery cannot
        # resolve one, so the remedy stays the conductor's commit. A format-2 record names recovery
        # (test_council_gate_attempts.UncommittedAndRemovedTests).
        self.assertIn("commit the record before the advance", v.reasons[0])
        self.assertNotIn("run-council.py --recover", v.reasons[0])
        self.assertIn(self.env.rel(r1), v.reasons[0])
        # Positive control: the same bytes, committed, pass.
        self.env.commit_records(r1)
        self.assertEqual(self.at_council(r1).verdict, "pass")

    def test_a_staged_or_modified_record_is_refused_until_it_is_committed(self):
        r1 = self.uncommitted()
        sup.git(self.env.root, "add", "--", self.env.rel(r1))  # staged, not committed
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("is not committed", v.reasons[0])
        self.env.commit_records(r1)
        self.assertEqual(self.at_council(r1).verdict, "pass")
        # A committed record rewritten in the working tree is refused too.
        self.env.write("r1.json", self.env.council_doc(decisions=(RC, OK, OK), findings=BLOCK), commit=False)
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("is not committed", v.reasons[0])

    def test_an_uncommitted_owner_exception_is_refused_and_the_committed_one_passes(self):
        self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        r3 = self.council("r3.json", round=3, written_at=ts(3))
        exc = self.env.write("exception.json", self.env.owner_doc(), commit=False)
        v = self.at_council(r3)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(exc), v.reasons[0])
        self.env.commit_records(exc)
        self.assertEqual(self.at_council(r3).verdict, "pass")

    def test_an_uncommitted_refutation_record_is_refused_and_the_committed_one_passes(self):
        r1 = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        ref = self.env.write("ref.json", self.env.refutation_doc(r1, written_at=ts(5)), commit=False)
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(ref), v.reasons[0])
        self.env.commit_records(ref)
        self.assertEqual(self.at_council(ref).verdict, "pass")

    def test_a_committed_record_deleted_from_the_working_tree_is_refused(self):
        r1 = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.council("r2.json", round=2, written_at=ts(2))
        # Positive control: both records present, the converged round passes.
        self.assertEqual(self.at_council(r1, r2).verdict, "pass")
        r1.unlink()
        v = self.at_council(r2)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("committed in HEAD", v.reasons[0])
        self.assertIn(self.env.rel(r1), v.reasons[0])

    def test_a_committed_record_modified_in_the_working_tree_is_refused_even_out_of_scope(self):
        other = self.council("other.json", module_tag="adr-2", prompt=7, written_at=ts(1))
        r1 = self.council("r1.json", written_at=ts(2))
        self.assertEqual(self.at_council(r1).verdict, "pass")
        doc = json.loads(other.read_text())
        doc["round"] = 2
        other.write_text(json.dumps(doc, indent=2) + "\n")
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(other), v.reasons[0])
        # The deletion of the same out-of-scope record is refused the same way.
        other.unlink()
        self.assertEqual(self.at_council(r1).verdict, "refuse")

    def test_a_deleted_file_that_is_no_record_of_this_run_is_not_refused(self):
        r1 = self.council("r1.json")
        note = self.env.write("old-synthesis.json", {"consensus": "UNANIMOUS_APPROVE"})
        foreign = self.env.council_doc()
        foreign["run_id"] = "RUN-099"
        gone = self.env.write("other-run.json", foreign)
        self.assertEqual(self.at_council(r1).verdict, "pass")
        note.unlink()
        gone.unlink()
        self.assertEqual(self.at_council(r1).verdict, "pass")

    def test_a_snapshot_named_council_record_that_is_missing_is_refused(self):
        r1 = self.council("r1.json")
        ghost = self.env.council / "ghost.json"  # named in the snapshot, never on disk
        self.snapshot_names(r1, ghost)
        v = self.at_council(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("names a record that is missing", v.reasons[0])
        self.assertIn(self.env.rel(ghost), v.reasons[0])
        # Positive controls: a present record, a missing non-record file under council/, and a
        # missing .json outside council/ are not named by this rule.
        self.snapshot_names(r1, self.env.council / "question.md", self.env.run_dir / "reviews" / "gone.json")
        self.assertEqual(self.at_council(r1).verdict, "pass")

    def test_a_committed_record_with_a_non_utf8_name_deleted_from_the_working_tree_is_refused(self):
        r2 = self.council("r2.json", round=1, written_at=ts(2))
        self.assertEqual(self.at_council(r2).verdict, "pass")  # control: alone, the round passes
        held = json.dumps(self.env.council_doc(module_tag="adr-1", prompt=COUNCIL_N, written_at=ts(1),
                                               decisions=(RC, OK, OK)), indent=2) + "\n"
        blob = subprocess.run(["git", "-C", str(self.env.root), "hash-object", "-w", "--stdin"],
                              input=held, capture_output=True, text=True, check=True,
                              env=sup.scrubbed_env()).stdout.strip()
        name = os.fsencode(self.env.rel(self.env.council)) + b"/held-\xff.json"
        # The tree holds a name the working tree cannot (APFS refuses invalid UTF-8), so the
        # record is committed and absent from the working tree at once.
        subprocess.run([b"git", b"-C", os.fsencode(self.env.root), b"update-index", b"--add", b"--cacheinfo",
                        b"100644," + blob.encode() + b"," + name], check=True, capture_output=True,
                       env=sup.scrubbed_env())
        sup.git(self.env.root, "commit", "-q", "-m", "held record with a non-UTF-8 name")
        listing = subprocess.run(["git", "-C", str(self.env.root), "ls-tree", "-z", "--name-only", "HEAD",
                                  self.env.rel(self.env.council) + "/"], capture_output=True, check=True,
                                 env=sup.scrubbed_env()).stdout
        self.assertIn(name, listing.split(b"\x00"), "the fixture did not commit the record")
        v = self.at_council(r2)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("committed in HEAD", v.reasons[0])

    def test_disclosed_window_a_held_record_deleted_before_its_first_commit_leaves_no_trace(self):
        """Pins the discard window gates.md section 10 discloses. If this starts refusing, the
        window has closed: update that disclosure."""
        held = self.env.write("r1.json", self.env.council_doc(module_tag="adr-1", prompt=COUNCIL_N,
                                                              written_at=ts(1), decisions=(RC, OK, OK)),
                              commit=False)
        held.unlink()
        r1 = self.council("r1b.json", round=1, written_at=ts(2))
        self.assertEqual(self.at_council(r1).verdict, "pass")

    def test_disclosed_tampering_a_committed_deletion_alone_clears_a_stop_no_advance_named(self):
        """The committed-tampering case gates.md section 10 disclosed under the stamp order: a
        committed deletion alone cleared a held record's stop. The committed order closes it, so
        the deletion is a permanent stop at 4, and gates.md section 10 no longer discloses the
        window."""
        held = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK))
        self.assertEqual(self.at_council(held).verdict, "stop")
        sup.git(self.env.root, "rm", "-q", "--", self.env.rel(held))
        sup.git(self.env.root, "commit", "-q", "-m", "drop the held record")
        r1 = self.council("r1b.json", round=1, written_at=ts(2))
        # Profile four closes this window: the removed record is a permanent stop at 4.
        v = self.at_council(r1)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn(self.env.rel(held), " ".join(v.reasons))
        # Control: once a blocked advance names the record, the same deletion is refused.
        self.snapshot_names(held)
        self.assertEqual(self.at_council(r1).verdict, "refuse")

    def test_deleting_a_held_record_then_writing_a_converged_round_does_not_clear_the_stop(self):
        held = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK))
        v = self.at_council(held)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.snapshot_names(held)  # the blocked advance recorded the held round
        held.unlink()
        r2 = self.council("r2.json", round=1, written_at=ts(2))
        v = self.at_council(r2)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(held), v.reasons[0])
        # Positive control: with the held record restored the stop stands, so the refusal
        # above is the deletion and not the round.
        sup.git(self.env.root, "checkout", "--", self.env.rel(held))
        v = self.at_council(held, r2)
        self.assertEqual(v.verdict, "stop", v.reasons)

    def test_a_could_not_run_record_followed_by_a_converged_round_still_passes(self):
        cnr = self.council("r0.json", written_at=ts(1), outcome="could-not-run")
        v = self.at_council(cnr)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.snapshot_names(cnr)  # the blocked advance recorded it; the record stays committed
        r1 = self.council("r1.json", written_at=ts(2), round=1)
        v = self.at_council(cnr, r1)
        self.assertEqual(v.verdict, "pass", v.reasons)


class RegistryScopeTests(_Base):
    """The registry check reads the record that decides: the deciding council record, or the council
    record a deciding refutation names. An earlier round can only add stops, never a pass, so a
    registry rotation mid-module leaves the module closable on a round seated on the new assignment."""

    def old_assignment(self, doc):
        # Round 1 seated on an assignment the registry no longer holds (a rotated, now-retired key).
        seat = next(s for s in doc["seats"] if s["role"] == "openai_top")
        seat.update(registry_key="gpt-6-sol", requested_model="openai/gpt-6-sol", served_model="openai/gpt-6-sol")
        return doc

    def test_a_rotation_mid_module_does_not_strand_the_module(self):
        r1 = self.old_assignment(self.env.council_doc(
            round=1, written_at=sup.ts(1), decisions=("REQUEST_CHANGES", "APPROVE", "APPROVE"),
            findings={"openai_top": [("F1", "Correctness", False, "blocking")]}))
        self.env.write("r1.json", r1)
        r2 = self.council("r2.json", round=2, written_at=sup.ts(2))
        v = self.at_council(r2)
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_the_deciding_record_off_the_registry_is_still_refused(self):
        self.council("r1.json", round=1, written_at=sup.ts(1),
                     decisions=("REQUEST_CHANGES", "APPROVE", "APPROVE"),
                     findings={"openai_top": [("F1", "Correctness", False, "blocking")]})
        r2 = self.env.write("r2.json", self.old_assignment(self.env.council_doc(round=2, written_at=sup.ts(2))))
        v = self.at_council(r2)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("r2.json", v.reasons[0])
        self.assertIn("the registry assigns", v.reasons[0])

    def test_a_council_record_a_deciding_refutation_names_is_checked(self):
        doc = self.old_assignment(self.env.council_doc(
            round=1, written_at=sup.ts(1), decisions=("REQUEST_CHANGES", "APPROVE", "APPROVE"),
            findings={"openai_top": [("F1", "Correctness", False, "blocking")]}))
        r1 = self.env.write("r1.json", doc)
        ref = self.env.write("ref.json", self.env.refutation_doc(r1, written_at=sup.ts(2)))
        v = self.at_council(ref)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("the registry assigns", v.reasons[0])
        # Control: the same refutation over a record on the current assignment passes.
        ok_env = sup.Env(Path(self._td.name) / "repo2", kind=self.KIND, autocommit=True)
        clean = ok_env.write("r1.json", ok_env.council_doc(
            round=1, written_at=sup.ts(1), decisions=("REQUEST_CHANGES", "APPROVE", "APPROVE"),
            findings={"openai_top": [("F1", "Correctness", False, "blocking")]}))
        ref2 = ok_env.write("ref.json", ok_env.refutation_doc(clean, written_at=sup.ts(2)))
        v2 = cg.evaluate_gate(cg.classify(ok_env.book, COUNCIL_N), ok_env.load_run(), ok_env.run_path,
                              ok_env.root, ok_env.args_for(ref2), "done")
        self.assertEqual(v2.verdict, "pass", v2.reasons)

    def test_an_errored_seat_naming_an_unregistered_model_is_refused(self):
        doc = self.env.council_doc(errored=("openai_top",))
        seat = next(s for s in doc["seats"] if s["role"] == "openai_top")
        seat.update(registry_key="gpt-9-ghost", requested_model="openai/gpt-9-ghost")
        doc["errored_seats"][0].update(registry_key="gpt-9-ghost", requested_model="openai/gpt-9-ghost")
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("gpt-9-ghost", v.reasons[0])

    def test_control_an_errored_seat_on_its_assignment_passes(self):
        v = self.at_council(self.env.write("r1.json", self.env.council_doc(errored=("openai_top",))))
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_a_scan_reduced_record_is_checked_against_the_registry(self):
        doc = self.env.council_doc(refusal_reason={"code": "secret-scan", "names": ["seats[0].reasoning"]})
        next(s for s in doc["seats"] if s["role"] == "anthropic_top")["served_model"] = "anthropic/claude-x"
        v = self.at_council(self.env.write("r1.json", doc))
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("served_model", v.reasons[0])
        ok = self.env.council_doc(refusal_reason={"code": "secret-scan", "names": ["seats[0].reasoning"]})
        v = self.at_council(self.env.write("r1.json", ok))
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)  # control


class RegistryAdmissibilityTests(_Base):
    """A counted record's responding seats must match the CURRENT router registry (M6)."""

    def setUp(self):
        super().setUp()
        self.reg = sup.registry()

    def patched(self, reg):
        path = Path(self._td.name) / "registry.json"
        path.write_text(reg if isinstance(reg, str) else json.dumps(reg))
        patcher = mock.patch.object(cg, "REGISTRY_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def seat_record(self, role="openai_top", **changes):
        doc = self.env.council_doc()
        seat = next(s for s in doc["seats"] if s["role"] == role)
        seat.update(changes)
        return self.env.write("r1.json", doc)

    def refused_for(self, rec, needle):
        v = self.at_council(rec)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(needle, v.reasons[0])

    def assert_control_passes(self):
        self.assertEqual(self.at_council(self.council("r1.json")).verdict, "pass")

    def test_the_registry_path_is_the_one_the_router_reads(self):
        # Read llm_caller's CONFIG_PATH from its source, so the check needs no httpx import.
        import re
        src = (Path(cg.__file__).resolve().parent / "crux" / "core" / "llm_caller.py").read_text()
        m = re.search(r'^CONFIG_PATH = _PACKAGE_ROOT / "([^"]+)" / "([^"]+)"$', src, re.M)
        self.assertIsNotNone(m, "llm_caller.CONFIG_PATH changed shape; re-derive this check")
        router = Path(cg.__file__).resolve().parent / "crux" / m.group(1) / m.group(2)
        self.assertEqual(cg.REGISTRY_PATH.resolve(), router.resolve())
        self.assertTrue(cg.REGISTRY_PATH.is_file())

    def test_an_unregistered_model_is_refused(self):
        rec = self.seat_record(registry_key="gpt-9-ghost", requested_model="openai/gpt-9-ghost",
                               served_model="openai/gpt-9-ghost")
        self.refused_for(rec, "the registry assigns")
        self.assert_control_passes()

    def test_the_wrong_key_for_the_role_is_refused(self):
        other = self.reg["models"]["gemini-3.5-flash"]
        rec = self.seat_record("google_top", registry_key="gemini-3.5-flash",
                               requested_model=other["api_string"], served_model=other["api_string"])
        self.refused_for(rec, "the registry assigns")
        self.assert_control_passes()

    def test_a_requested_model_that_is_not_the_keys_api_string_is_refused(self):
        rec = self.seat_record(requested_model="openai/gpt-6.1-sol", served_model="openai/gpt-6.1-sol")
        self.refused_for(rec, "requested_model")
        self.assert_control_passes()

    def test_a_served_model_outside_the_accepted_set_is_refused(self):
        rec = self.seat_record(served_model="openai/gpt-6.1-sol")
        self.refused_for(rec, "served_model")
        self.assert_control_passes()

    def test_a_served_provider_outside_the_accepted_set_is_refused(self):
        rec = self.seat_record("anthropic_top", served_provider="Amazon Bedrock")
        self.refused_for(rec, "served_provider")
        self.assert_control_passes()

    def test_a_tombstoned_key_is_refused(self):
        rec = self.council("r1.json")
        key = self.reg["model_roles"]["openai_top"]
        self.assertEqual(self.at_council(rec).verdict, "pass")  # control, before the patch
        self.reg["retired_models"] = [*self.reg["retired_models"], key]
        self.patched(self.reg)
        self.refused_for(rec, "retired")

    def test_a_registry_edit_after_the_council_makes_an_older_record_inadmissible(self):
        rec = self.council("r1.json")
        self.assertEqual(self.at_council(rec).verdict, "pass")
        self.reg["model_roles"]["openai_top"] = "gpt-6.1-sol"
        self.patched(self.reg)
        self.refused_for(rec, "the registry assigns")

    def test_three_seats_on_one_provider_namespace_are_refused(self):
        doc = self.env.council_doc()
        for seat in doc["seats"]:
            seat["requested_model"] = "openai/gpt-6-astra"
            seat["provider_namespace"] = "openai"
        self.refused_for(self.env.write("r1.json", doc), "three distinct providers")
        self.assert_control_passes()

    def test_an_unreadable_or_malformed_registry_fails_closed(self):
        rec = self.council("r1.json")
        self.assertEqual(self.at_council(rec).verdict, "pass")
        broken = {"not json": "{", "not an object": "[]", "no models": json.dumps({"model_roles": {}}),
                  "no roles": json.dumps({"models": {}, "retired_models": []}),
                  "no tombstones": json.dumps({"models": {}, "model_roles": {}})}
        for i, (name, text) in enumerate(broken.items()):
            with self.subTest(registry=name):
                path = Path(self._td.name) / f"reg-{i}.json"
                path.write_text(text)
                with mock.patch.object(cg, "REGISTRY_PATH", path):
                    self.refused_for(rec, "registry")
        with mock.patch.object(cg, "REGISTRY_PATH", Path(self._td.name) / "absent.json"):
            self.refused_for(rec, "registry")

    def test_a_could_not_run_record_needs_no_registry(self):
        cnr = self.council("r0.json", outcome="could-not-run")
        with mock.patch.object(cg, "REGISTRY_PATH", Path(self._td.name) / "absent.json"):
            v = self.at_council(cnr)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)


class FixtureRepoMaintenanceTests(unittest.TestCase):
    """A fixture repo must not let git's detached auto-gc repack objects while a test copies it."""

    def assert_maintenance_off(self, root):
        self.assertEqual(sup.git(root, "config", "--local", "--get", "gc.auto").strip(), "0")
        self.assertEqual(sup.git(root, "config", "--local", "--get", "maintenance.auto").strip(), "false")

    def test_init_repo_disables_auto_gc_and_maintenance(self):
        with tempfile.TemporaryDirectory() as td:
            self.assert_maintenance_off(sup.init_repo(Path(td) / "repo"))

    def test_writer_template_repo_disables_auto_gc_and_maintenance(self):
        from test_implementation_cycles import WriterFixture
        with tempfile.TemporaryDirectory() as td:
            self.assert_maintenance_off(WriterFixture(Path(td) / "repo", migration=True).root)


if __name__ == "__main__":
    unittest.main()
