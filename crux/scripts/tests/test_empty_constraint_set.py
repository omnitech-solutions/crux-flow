"""An explicitly declared-empty constraint set: structure, acceptance, refusal and question.

B1 structure, B2 acceptance, B3 refusal, B4 undeclared behaviour unchanged, B5 question
words, B6 slot/decision mismatch, B7 owner stop at preflight, B8 a later path-less rule,
B9 the shipped template question, B10 the unchecked-rules subject generator.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import _council_gate_support as sup  # noqa: E402
from _dev_surface import require_dev_surface  # noqa: E402
import test_run_council as runner  # noqa: E402
import test_implementation_cycles as cycles  # noqa: E402
import test_implementation_council_transport as transport  # noqa: E402
import implementation_approval as ap  # noqa: E402
import implementation_decisions as ids  # noqa: E402
import yaml  # noqa: E402

REPO = HERE.parents[2]
REASON = "No architectural rule governs this tool scope."
MARKER = "no_governing_constraint"
SCOPE = ["tools/ungoverned.py"]
DECLARED = "declared-empty constraint set"


def empty_book(reason=REASON, scope=None):
    book = cycles.book_two()
    book["implementation_slots"][0].update(scope=list(scope or SCOPE), constraint_refs=[],
                                           **{MARKER: {"reason": reason}})
    return book


def slot_errors(book):
    errors = []
    schema = sup.vp.load_schema(sup.vp.PROMPTBOOK_SCHEMA)
    sup.vp.validate(book, schema, "#", "#", errors, "book")
    return [e for e in errors if "implementation_slots" in e["instance_path"]]


def code_of(book):
    try:
        ap.validate_format_two(book)
    except Exception as exc:  # noqa: BLE001
        return getattr(exc, "code", type(exc).__name__)
    return None


def decision_doc(**over):
    from test_implementation_decisions import decision
    doc = decision()
    doc.update(over)
    return doc


class Structure(unittest.TestCase):
    """B1."""

    def test_slot_without_declaration_and_empty_list_is_refused(self):
        book = cycles.book_two()
        book["implementation_slots"][0]["constraint_refs"] = []
        self.assertEqual(code_of(book), "slot-scope-refused")
        self.assertTrue(slot_errors(book))

    def test_slot_with_declaration_and_reason_validates(self):
        book = empty_book()
        self.assertIsNone(code_of(book))
        self.assertEqual(slot_errors(book), [])

    def test_slot_declaration_beside_non_empty_list_is_refused(self):
        book = empty_book()
        book["implementation_slots"][0]["constraint_refs"] = ["ADR-0001/fixture-rule"]
        self.assertEqual(code_of(book), "slot-declaration-refused")
        self.assertTrue(slot_errors(book))

    def test_slot_declaration_reason_shape_is_refused(self):
        for reason in ("", "   ", "two\nlines", "x" * 501, 5, None):
            with self.subTest(reason=reason):
                book = empty_book(reason=reason)
                self.assertEqual(code_of(book), "slot-declaration-refused")
                self.assertTrue(slot_errors(book))
        book = empty_book(reason="x" * 500)
        self.assertIsNone(code_of(book))
        self.assertEqual(slot_errors(book), [])

    def test_slot_declaration_extra_key_is_refused(self):
        book = empty_book()
        book["implementation_slots"][0][MARKER]["extra"] = "x"
        self.assertEqual(code_of(book), "slot-declaration-refused")
        self.assertTrue(slot_errors(book))

    def test_migration_slot_declaration_is_refused(self):
        book = empty_book(scope=["docs/adrs/migrations"])
        book["implementation_slots"][0]["migration_batch"] = {
            "role": "migration-batch", "path": "docs/adrs/migrations/x.yaml"}
        self.assertEqual(code_of(book), "migration-slot-declaration-refused")
        self.assertTrue(slot_errors(book))

    def test_decision_record_schema_cases(self):
        self.assertTrue(ids.schema_errors(decision_doc(constraint_refs=[])))
        self.assertEqual(ids.schema_errors(decision_doc(constraint_refs=[], **{MARKER: {"reason": REASON}})), [])
        self.assertTrue(ids.schema_errors(decision_doc(**{MARKER: {"reason": REASON}})))
        self.assertTrue(ids.schema_errors(decision_doc(constraint_refs=[], **{MARKER: {"reason": ""}})))
        self.assertTrue(ids.schema_errors(decision_doc(constraint_refs=[], **{MARKER: {"reason": "a\nb"}})))
        self.assertTrue(ids.schema_errors(decision_doc(constraint_refs=[], **{MARKER: {"reason": REASON, "x": 1}})))

    def test_decision_record_marker_without_list_is_refused(self):
        doc = decision_doc(**{MARKER: {"reason": REASON}})
        del doc["constraint_refs"]
        self.assertTrue(ids.schema_errors(doc))


class DeclaredEmpty(runner._Base):
    """B2-B10 through the real runner, advance-run and query."""
    KIND = "adr"
    BOOK = cycles.book_two()
    install = transport.Transport.install
    question_text = transport.Transport.question_text
    formal = transport.Transport.formal
    refused_without_call = transport.Transport.refused_without_call
    advance_current = transport.Transport.advance_current
    advance = transport.Transport.advance
    close_bindings = transport.Transport.close_bindings

    def setUp(self):
        super().setUp()
        self.install(cycles.book_two())

    def attempts(self):
        return sorted(self.env.council.glob("*.attempt.json"))

    def adr(self, number, rule_scope, name="rule"):
        path = self.env.root / f"docs/adrs/ADR-{number:04d}-fixture.md"
        scope = f"  scope: {rule_scope}\n" if rule_scope else ""
        path.write_text(f"---\nid: ADR-{number:04d}\nstatus: Accepted\ngoverns:\n"
                        f"- handle: ADR-{number:04d}/{name}\n  rule: Preserve synthetic constraints.\n{scope}---\n# x\n")
        return path

    def install_empty(self, *, rule_scope="crux/scripts/governed.py", reason=REASON, words=True,
                      decision_reason=None, scope=None):
        self.install(empty_book(reason, scope))
        doc = copy.deepcopy(self.doc)
        doc["constraint_refs"] = []
        doc[MARKER] = {"reason": decision_reason or reason}
        doc["scope"] = list(scope or SCOPE)
        self.doc = doc
        self.decision.write_text(yaml.safe_dump(doc, sort_keys=False))
        self.adr(1, rule_scope, "fixture-rule")
        self.question_text("implementation")
        if words:
            self.question.write_text(self.question.read_text() + "The slot makes a " + DECLARED +
                                     " claim; its reason is in the fenced subject.\n")
        runner.commit_pending(self.env.root, "declared-empty fixture")

    def close_ready(self):
        code, out, err, _ = self.formal()
        self.assertEqual(code, 0, (out, err))
        runner.commit_pending(self.env.root, "council record")
        self.advance_current()
        self.advance_current()

    def query(self):
        runner.commit_pending(self.env.root, "pre-query state")
        return ids.query(self.env.root, self.decision)["current_eligibility"]

    def close(self):
        return self.advance("--implementation-revision", self.env.rel(self.decision))

    def test_programming_error_in_the_authoring_overlap_check_propagates(self):
        """Only an unreadable source reads as no finding; a defect never reads as a clean check.

        The book sits in the fixture repository, which holds a committed Accepted rule whose path
        scope the slot overlaps, so the check reaches the overlap call whatever tree hosts this file."""
        from unittest import mock
        import council_records as cr
        vp = cr._validator()
        self.adr(1, "tools/ungoverned.py", "fixture-rule")
        runner.commit_pending(self.env.root, "overlapping rule")
        doc = {"format_version": "2", "implementation_slots": [
            {"slot": "implementation-1", "slug": "x", "scope": list(SCOPE), "constraint_refs": [],
             MARKER: {"reason": "nothing governs it"}}]}
        book = self.env.root / "authoring-book.yaml"
        book.write_text("placeholder: true\n")
        # Positive control: the fixture reaches the overlap finding, so the patched run below is
        # stopped by the injected defect and not by an unreadable source.
        errors = vp.declared_empty_overlap_errors(book, doc, "book.yaml")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("ADR-0001/fixture-rule", errors[0]["error"])
        with mock.patch.object(ap, "undeclared_governing_constraints", side_effect=AttributeError("defect")):
            with self.assertRaises(AttributeError):
                vp.declared_empty_overlap_errors(book, doc, "book.yaml")

    def test_b2_declared_empty_without_overlap_is_accepted_end_to_end(self):
        self.install_empty()
        self.close_ready()
        result = self.close()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.close_bindings(), ["revision-001.yaml"])
        runner.commit_pending(self.env.root, "close")
        eligibility = self.query()
        self.assertTrue(eligibility["eligible"], eligibility)
        self.assertNotIn("unscoped_unchecked_refs", eligibility)

    def test_b3_overlap_refused_at_preflight(self):
        self.install_empty(rule_scope="tools/ungoverned.py")
        code, out, err, gateway = self.formal()
        self.assertEqual(code, 1, (out, err))
        self.assertEqual(gateway.requests, [])
        self.assertEqual(self.attempts(), [])

    def test_b3_overlap_refused_at_close(self):
        self.install_empty()
        self.close_ready()
        self.adr(1, "tools/ungoverned.py", "fixture-rule")
        runner.commit_pending(self.env.root, "rule now overlaps")
        before = self.env.run_path.read_bytes()
        result = self.close()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(b"undeclared-governing-constraint", result.stdout + result.stderr)
        self.assertEqual(self.close_bindings(), [])
        self.assertEqual(self.env.run_path.read_bytes(), before)

    def test_b3_overlap_makes_eligibility_false(self):
        self.install_empty()
        self.close_ready()
        self.assertEqual(self.close().returncode, 0)
        runner.commit_pending(self.env.root, "close")
        self.assertTrue(self.query()["eligible"])
        self.adr(1, "tools/ungoverned.py", "fixture-rule")
        eligibility = self.query()
        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["limit"], "undeclared-governing-constraint")

    def test_b4_undeclared_slot_and_decision_keep_today_behaviour(self):
        code, out, err, gateway = self.formal()
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(len(gateway.requests), 3)
        # The PB-0141 book and run are dev-repo surfaces; the gateway assertions above run staged too.
        book_path = REPO / "bionic/promptbooks/archive/PB-0141-implementation-migration-pilot.yaml"
        run_path = (REPO / "bionic/promptbooks/runs/PB-0141-implementation-migration-pilot/"
                    "run-RUN-001.yaml")
        require_dev_surface(self, book_path, "PB-0141 archived book")
        require_dev_surface(self, run_path, "PB-0141 run-RUN-001")
        run = yaml.safe_load(run_path.read_text())
        self.assertEqual(sup.vp.compute_book_hash(yaml.safe_load(book_path.read_text())),
                         run["book_content_hash"])

    def test_b5_question_without_the_declaration_words_is_refused_at_preflight(self):
        self.install_empty(words=False)
        self.refused_without_call(self.formal())
        self.assertEqual(self.records(), [])

    def test_b5_selector_function_takes_the_declaration(self):
        selected = {"path": "p", "sha256": "a" * 64}
        question = "p " + "a" * 64 + " Completeness Correctness Consistency Clarity Security architectural conflict"
        self.assertIsNone(ap.selected_question_refusal(question, "implementation", selected, []))
        self.assertEqual(ap.selected_question_refusal(question, "implementation", selected, [], {"reason": "r"}),
                         "deciding-question-declaration-missing")
        self.assertIsNone(ap.selected_question_refusal(question + " Declared-Empty Constraint Set",
                                                       "implementation", selected, [], {"reason": "r"}))

    def test_b5_close_refuses_a_sealed_question_without_the_words(self):
        self.install_empty()
        self.close_ready()
        doc = json.loads(self.records()[0].read_text())
        question = self.env.root / doc["question"]["path"]
        self.assertIn(DECLARED, question.read_text())
        question.write_text(question.read_text().replace(DECLARED, "empty set"))
        runner.commit_pending(self.env.root, "question edited")
        result = self.close()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.close_bindings(), [])

    def test_b6_reason_mismatch_refuses_at_preflight(self):
        self.install_empty(decision_reason="A different reason.")
        self.refused_without_call(self.formal())
        self.assertEqual(self.records(), [])

    def test_b6_reason_mismatch_refuses_at_close(self):
        self.install_empty()
        self.close_ready()
        doc = copy.deepcopy(self.doc)
        doc[MARKER] = {"reason": "A different reason."}
        self.decision.write_text(yaml.safe_dump(doc, sort_keys=False))
        runner.commit_pending(self.env.root, "decision reason changed")
        result = self.close()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.close_bindings(), [])

    def test_b6_marker_missing_on_the_decision_refuses(self):
        self.install_empty()
        doc = copy.deepcopy(self.doc)
        del doc[MARKER]
        self.decision.write_text(yaml.safe_dump(doc, sort_keys=False))
        self.question_text("implementation")
        runner.commit_pending(self.env.root, "marker removed")
        self.refused_without_call(self.formal())

    def test_b7_overlap_at_preflight_commits_preflight_needs_owner(self):
        self.install_empty(rule_scope="tools/ungoverned.py")
        head = sup.git(self.env.root, "rev-parse", "HEAD").strip()
        code, out, err, gateway = self.formal()
        self.assertEqual((code, gateway.requests), (1, []), out + err)
        records = self.records()
        self.assertEqual(len(records), 1)
        doc = json.loads(records[0].read_text())
        self.assertEqual(doc["outcome"], "could-not-run")
        self.assertEqual(doc["refusal_reason"]["code"], "preflight-needs-owner")
        self.assertEqual(self.attempts(), [])
        self.assertNotEqual(sup.git(self.env.root, "rev-parse", "HEAD").strip(), head)
        self.assertEqual(sup.git(self.env.root, "status", "--porcelain", "--", self.env.rel(records[0])).strip(), "")

    def test_b8_later_path_less_rule_leaves_eligible_and_is_listed(self):
        self.install_empty()
        self.close_ready()
        self.assertEqual(self.close().returncode, 0)
        runner.commit_pending(self.env.root, "close")
        self.adr(2, None, "path-less")
        eligibility = self.query()
        self.assertTrue(eligibility["eligible"], eligibility)
        self.assertEqual(eligibility["unscoped_unchecked_refs"], ["ADR-0002/path-less"])

    def test_b9_the_template_question_passes_the_selector_check(self):
        text = (REPO / "crux/templates/cycle-module-implementation.yaml").read_text()
        flat = " ".join(text.split())
        self.assertIn(DECLARED, flat)
        self.assertIn("unchecked-rules", flat)
        self.assertIn("never pasted", flat)
        # The declared-empty sentence the template tells the conductor to put in the question
        # satisfies the check that the runner and the close share.
        selected = {"path": "p", "sha256": "a" * 64}
        question = ("p " + "a" * 64 + " Completeness Correctness Consistency Clarity Security "
                    "architectural conflict " + DECLARED)
        self.assertIsNone(ap.selected_question_refusal(question, "implementation", selected, [], {"reason": "r"}))

    def test_b10_unchecked_rules_subject_lists_each_path_less_rule(self):
        self.install_empty()
        self.adr(2, None, "path-less")
        self.adr(3, "docs/elsewhere.md", "scoped")
        runner.commit_pending(self.env.root, "rules")
        out = self.env.root / "unchecked.md"
        result = subprocess.run([sys.executable, str(sup.SCRIPTS / "implementation-decisions.py"),
                                 "unchecked-rules", "--decision", str(self.decision), "--output", str(out),
                                 "--repo-root", str(self.env.root)], capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = out.read_text()
        self.assertIn("ADR-0002/path-less", text)
        self.assertIn("Preserve synthetic constraints.", text)
        self.assertNotIn("ADR-0003/scoped", text)
        self.assertNotIn("ADR-0001/fixture-rule", text)


if __name__ == "__main__":
    unittest.main()
