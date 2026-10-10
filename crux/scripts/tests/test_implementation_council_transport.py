"""Synthetic runner transport controls, not real council or delivery evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import subprocess
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_run_council as runner
import test_implementation_cycles as cycles
import _council_gate_support as sup
import yaml
from crux.council.async_council import gate_vote_instruction


class Transport(runner._Base):
    BOOK = cycles.book_two()

    def setUp(self):
        super().setUp()
        self.install(self.BOOK)

    def install(self, book, *, prompt=2):
        for path in self.records(): path.unlink()
        (self.env.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        self.env.book = copy.deepcopy(book)
        self.env.write_book_and_run(current=prompt)
        self.env.run["format_version"] = "2"
        self.env.run["implementation_bindings"] = []
        self.env.run_path.write_text(yaml.safe_dump(self.env.run, sort_keys=False))
        slot = book["implementation_slots"][0] if book["implementation_slots"] else None
        if slot:
            self.decision = self.env.run_dir / "implementations" / sup.RUN_ID / "strategy" / "revision-001.yaml"
            self.decision.parent.mkdir(parents=True, exist_ok=True)
            self.doc = {"record_type": "implementation-decision", "format_version": "1",
                "book_id": sup.BOOK_ID, "run_id": sup.RUN_ID, "slug": slot["slug"], "revision": 1,
                "slot": slot["slot"], "display_title": "Synthetic strategy", "scope": slot["scope"],
                "constraint_refs": slot["constraint_refs"], "reasoning": "Synthetic fixture reasoning",
                "alternatives": ["Synthetic alternative"], "approach": "Synthetic strategy",
                "assumptions": [], "intended_evidence": ["Synthetic test"], "source_labels": []}
            self.decision.write_text(yaml.safe_dump(self.doc, sort_keys=False))
            self.subject.write_text("---\nid: ADR-0001\nstatus: Accepted\ngoverns:\n"
                "- handle: ADR-0001/fixture-rule\n  rule: Preserve synthetic constraints.\n---\n# Synthetic constraint\n")
            self.question_text(book["cycle_kind"] if slot["slot"] != "implementation-1" else "implementation")
        runner.commit_pending(self.env.root, "synthetic transport fixture")

    def question_text(self, kind):
        dimensions = {
            "implementation": "Completeness, Correctness, Consistency, Clarity, Security",
            "verify": "Evidence/Reproduction, Root-cause correctness, Approach soundness and minimality, Consistency, Security",
            "patch": "Evidence/Reproduction, Root-cause correctness, Approach soundness and minimality, Blast-radius proportion, Security",
        }[kind]
        digest = hashlib.sha256(self.decision.read_bytes()).hexdigest()
        self.question.write_text(f"Review the selected Implementation Decision {self.env.rel(self.decision)} "
            f"at SHA256 {digest}. Assess {dimensions}. Assess architectural conflict of the selected approach.\n")

    def formal(self, *, prompt=2, extra=(), subjects=None, gateway=None):
        return self.run_main(self.argv("--implementation-revision", self.env.rel(self.decision),
            "--retain-subjects", *extra, prompt=prompt, subjects=subjects or [self.decision]), gateway)

    def refused_without_call(self, result):
        code, out, err, gateway = result
        self.assertNotEqual(code, 0, (out, err))
        self.assertEqual(gateway.requests, [])

    def advance_current(self):
        result = subprocess.run([sys.executable, str(cycles.sup.SCRIPTS / 'advance-run.py'),
            str(self.env.run_path), '--outcome', 'done', '--book', str(self.env.book_path),
            '--artifacts', ','.join(self.env.rel(p) for p in self.records())],
            capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        runner.commit_pending(self.env.root, 'actual transport prompt advance')

    def test_dedicated_implementation_uses_module_kind_and_retains_exact_revision(self):
        code, out, err, gateway = self.formal()
        self.assertEqual(code, 0, (out, err))
        _, doc = self.only_record()
        self.assertEqual(doc["council_kind"], "implementation")
        self.assertEqual(doc["binding"], {"prompt": 2, "module_tag": "implementation-1"})
        subject = doc["subjects"][0]
        self.assertEqual(subject["sha256"], hashlib.sha256(self.decision.read_bytes()).hexdigest())
        self.assertEqual((self.env.run_dir / subject["retained_copy"]).read_bytes(), self.decision.read_bytes())
        self.assertEqual(len(gateway.requests), 3)

    def test_combined_architecture_uses_implementation_module_not_outer_kind(self):
        self.install(cycles.book_two("adr", combined=True), prompt=6)
        code, out, err, _ = self.formal(prompt=6)
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(self.only_record()[1]["council_kind"], "implementation")

    def test_dedicated_selection_is_required(self):
        self.refused_without_call(self.run_main(self.argv("--retain-subjects", prompt=2)))

    def test_formal_selection_requires_explicit_subject_and_retention(self):
        self.refused_without_call(self.formal(subjects=[self.subject]))
        for path in self.records(): path.unlink()
        self.refused_without_call(self.run_main(self.argv("--implementation-revision",
            self.env.rel(self.decision), prompt=2, subjects=[self.decision])))

    def test_question_requires_revision_dimensions_and_conflict(self):
        original = self.question.read_text()
        for token in (self.env.rel(self.decision), hashlib.sha256(self.decision.read_bytes()).hexdigest(),
                      "Completeness", "Correctness", "Consistency", "Clarity", "Security", "architectural conflict"):
            with self.subTest(token=token):
                for path in self.records(): path.unlink()
                self.question.write_text(original.replace(token, "omitted"))
                self.refused_without_call(self.formal())
        self.question.write_text(original)

    def test_identity_scope_constraints_and_schema_must_match_declared_slot(self):
        for field, value in (("book_id", "PB-0998"), ("run_id", "RUN-002"),
                ("slot", "implementation-2"), ("slug", "another"), ("scope", ["another.txt"]),
                ("constraint_refs", ["ADR-0001/unknown"]), ("governs", [])):
            with self.subTest(field=field):
                for path in self.records(): path.unlink()
                doc = copy.deepcopy(self.doc); doc[field] = value
                self.decision.write_text(yaml.safe_dump(doc, sort_keys=False))
                self.question_text("implementation")
                sup.commit_all(self.env.root, "synthetic invalid revision")
                self.refused_without_call(self.formal())

    def test_formal_verify_and_patch_preserve_their_dimensions(self):
        for kind in ("verify", "patch"):
            with self.subTest(kind=kind):
                self.install(cycles.book_two(kind), prompt=1 if kind == "patch" else 2)
                prompt = 1 if kind == "patch" else 2
                code, out, err, _ = self.formal(prompt=prompt)
                self.assertEqual(code, 0, (out, err))
                self.assertEqual(self.only_record()[1]["council_kind"], kind)
                for path in self.records(): path.unlink()

    def test_late_patch_selection_has_no_new_council_route(self):
        self.install(cycles.book_two("patch"), prompt=2)
        self.refused_without_call(self.formal(prompt=2))

    def test_unknown_book_or_run_version_refuses_before_spend(self):
        for surface in ("book", "run"):
            for version in (None, "3"):
                with self.subTest(surface=surface, version=version):
                    self.install(cycles.book_two())
                    doc = self.env.book if surface == "book" else self.env.run
                    if version is None: doc.pop("format_version")
                    else: doc["format_version"] = version
                    if surface == "book":
                        self.env.write_book_and_run(current=2)
                    else:
                        self.env.run_path.write_text(yaml.safe_dump(doc, sort_keys=False))
                    self.refused_without_call(self.formal())

    def test_unknown_discriminator_also_refuses_without_formal_selection(self):
        for surface in ("book", "run"):
            for version in (None, "3"):
                with self.subTest(surface=surface, version=version):
                    self.install(cycles.book_two("verify", formal=False))
                    if surface == "book":
                        if version is None: self.env.book.pop("format_version")
                        else: self.env.book["format_version"] = version
                        self.env.write_book_and_run(current=2)
                    else:
                        self.env.run["format_version"] = version
                        self.env.run_path.write_text(yaml.safe_dump(self.env.run, sort_keys=False))
                    self.refused_without_call(self.run_main(self.argv(prompt=2)))

    def test_format_one_does_not_acquire_implementation_kind(self):
        self.env.book = sup.make_book("adr", cycle_kind="implementation")
        self.env.write_book_and_run(current=2)
        self.refused_without_call(self.run_main(self.argv(prompt=2)))

    def test_all_formal_council_positions_are_explicit_and_other_positions_refuse(self):
        for prompt in (2, 3, 4):
            with self.subTest(prompt=prompt):
                code, out, err, _ = self.formal(prompt=prompt, extra=('--round', str(prompt - 1)))
                self.assertEqual(code, 0, (out, err))
                if prompt < 4: self.advance_current()
        for prompt in (1, 5, 12, 13):
            with self.subTest(prompt=prompt):
                self.refused_without_call(self.formal(prompt=prompt))

    def test_incidental_empty_slots_need_no_implementation_record(self):
        for kind in ("adr", "verify", "patch"):
            with self.subTest(kind=kind):
                self.install(cycles.book_two(kind, formal=False), prompt=1 if kind == "patch" else 2)
                code, out, err, _ = self.run_main(self.argv(prompt=1 if kind == "patch" else 2))
                self.assertEqual(code, 0, (out, err))
                self.assertEqual(self.only_record()[1]["council_kind"], kind)
                for path in self.records(): path.unlink()

    def test_new_round_retains_explicit_new_revision_preserving_old_bytes(self):
        code, out, err, _ = self.formal()
        self.assertEqual(code, 0, (out, err))
        prior, old_record = self.only_record()
        old_record_bytes = prior.read_bytes()
        old_copy = self.env.run_dir / old_record["subjects"][0]["retained_copy"]
        old_copy_bytes = old_copy.read_bytes()
        self.advance_current()
        self.decision = self.decision.with_name("revision-002.yaml")
        self.doc["revision"] = 2
        self.doc["reasoning"] = "Materially revised synthetic reasoning"
        self.decision.write_text(yaml.safe_dump(self.doc, sort_keys=False))
        self.question_text("implementation")
        sup.commit_all(self.env.root, "synthetic second revision")
        code, out, err, gateway = self.run_main(self.argv("--implementation-revision",
            self.env.rel(self.decision), "--retain-subjects", prompt=3, round_=2, subjects=[self.decision]))
        self.assertEqual(code, 0, (out, err))
        new_record = json.loads(next(p for p in self.records() if p != prior).read_text())
        self.assertEqual(new_record["round"], 2)
        self.assertEqual(new_record["subjects"][0]["sha256"], hashlib.sha256(self.decision.read_bytes()).hexdigest())
        self.assertEqual(prior.read_bytes(), old_record_bytes)
        self.assertEqual(old_copy.read_bytes(), old_copy_bytes)
        self.assertEqual(len(gateway.requests), 3)

    def test_stale_subject_changed_revision_and_duplicate_subject_refuse(self):
        stale = self.decision
        self.decision = stale.with_name("revision-002.yaml")
        self.doc["revision"] = 2
        self.decision.write_text(yaml.safe_dump(self.doc, sort_keys=False))
        self.question_text("implementation")
        sup.commit_all(self.env.root, "synthetic second revision")
        self.refused_without_call(self.formal(subjects=[stale]))
        self.refused_without_call(self.formal(subjects=[self.decision, self.decision]))
        self.decision.write_text(self.decision.read_text().replace("Synthetic fixture reasoning", "Changed after commit"))
        self.refused_without_call(self.formal())

    def test_diagnosis_only_verify_or_patch_question_cannot_approve_formal_revision(self):
        # Both routes: with the selector the question check refuses; without it the declared
        # slot itself requires the selector, so neither route reaches a council.
        for kind in ("verify", "patch"):
            for selector in (True, False):
                with self.subTest(kind=kind, selector=selector):
                    prompt = 1 if kind == "patch" else 2
                    self.install(cycles.book_two(kind), prompt=prompt)
                    self.question.write_text("Review the diagnosis, Evidence/Reproduction, Root-cause correctness, "
                        "Approach soundness and minimality, Consistency, Blast-radius proportion, Security.\n")
                    runner.commit_pending(self.env.root, "diagnosis-only question")
                    if selector:
                        self.refused_without_call(self.formal(prompt=prompt))
                    else:
                        self.refused_without_call(self.run_main(self.argv("--retain-subjects",
                            prompt=prompt, subjects=[self.decision])))
                    self.assertEqual(self.records(), [])

    def test_question_naming_a_second_subject_digest_refuses_before_spend(self):
        second = self.decision.with_name("revision-002.yaml")
        doc = copy.deepcopy(self.doc); doc["revision"] = 2
        second.write_text(yaml.safe_dump(doc, sort_keys=False))
        self.question_text("implementation")
        self.question.write_text(self.question.read_text() + "Also consider "
            + self.env.rel(second) + " at SHA256 " + hashlib.sha256(second.read_bytes()).hexdigest() + ".\n")
        runner.commit_pending(self.env.root, "question naming two revisions")
        self.refused_without_call(self.run_main(self.argv("--implementation-revision", self.env.rel(self.decision),
            "--retain-subjects", prompt=2, subjects=[self.decision, second])))

    def advance(self, *extra):
        return subprocess.run([sys.executable, str(cycles.sup.SCRIPTS / 'advance-run.py'),
            str(self.env.run_path), '--outcome', 'done', '--book', str(self.env.book_path),
            '--artifacts', ','.join(self.env.rel(p) for p in self.records()), *extra],
            capture_output=True, env=sup.scrubbed_env())

    def close_bindings(self):
        return [b["revision"]["path"].rsplit("/", 1)[-1]
                for b in yaml.safe_load(self.env.run_path.read_text()).get("implementation_bindings", [])]

    def test_close_refuses_a_deciding_council_that_never_selected_the_revision(self):
        # A record convened without a selector (as before the selector became mandatory) and
        # asking a diagnosis-only question supplies no approval for the revision it carried.
        self.install(cycles.book_two("verify"), prompt=2)
        self.question.write_text("Review the diagnosis. Evidence/Reproduction, Root-cause correctness.\n")
        runner.commit_pending(self.env.root, "diagnosis-only question")
        with mock.patch.object(runner.rc_mod, "_selector_required", return_value=False, create=True):
            code, out, err, gateway = self.run_main(self.argv("--retain-subjects", prompt=2,
                                                              subjects=[self.decision]))
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(len(gateway.requests), 3)
        runner.commit_pending(self.env.root, "unselected council record")
        self.advance_current(); self.advance_current()
        result = self.advance("--implementation-revision", self.env.rel(self.decision))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(b"deciding-question-revision-missing", result.stdout + result.stderr)
        self.assertEqual(self.close_bindings(), [])

    def test_close_binds_the_revision_its_verify_council_selected(self):
        self.install(cycles.book_two("verify"), prompt=2)
        code, out, err, _ = self.formal(prompt=2)
        self.assertEqual(code, 0, (out, err))
        runner.commit_pending(self.env.root, "selected council record")
        self.advance_current(); self.advance_current()
        result = self.advance("--implementation-revision", self.env.rel(self.decision))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.close_bindings(), ["revision-001.yaml"])

    def test_close_refuses_an_extra_subject_the_council_did_not_select(self):
        first = self.decision
        second = first.with_name("revision-002.yaml")
        doc = copy.deepcopy(self.doc); doc["revision"] = 2
        doc["reasoning"] = "Materially different reasoning never named in the question"
        second.write_text(yaml.safe_dump(doc, sort_keys=False))
        self.question_text("implementation")
        runner.commit_pending(self.env.root, "second revision as an extra subject")
        code, out, err, _ = self.run_main(self.argv("--implementation-revision", self.env.rel(first),
            "--retain-subjects", prompt=2, subjects=[first, second]))
        self.assertEqual(code, 0, (out, err))
        runner.commit_pending(self.env.root, "council selecting revision-001")
        self.advance_current(); self.advance_current()
        result = self.advance("--implementation-revision", self.env.rel(second))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(b"deciding-question-revision-missing", result.stdout + result.stderr)
        self.assertEqual(self.close_bindings(), [])
        result = self.advance("--implementation-revision", self.env.rel(first))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.close_bindings(), ["revision-001.yaml"])

    def test_valid_revision_owned_by_another_run_refuses(self):
        foreign = self.decision.parents[2] / "RUN-002" / "strategy" / "revision-001.yaml"
        foreign.parent.mkdir(parents=True)
        foreign_doc = copy.deepcopy(self.doc); foreign_doc["run_id"] = "RUN-002"
        foreign.write_text(yaml.safe_dump(foreign_doc, sort_keys=False))
        other_run = copy.deepcopy(self.env.run); other_run["run_id"] = "RUN-002"
        (self.env.run_dir / "run-RUN-002.yaml").write_text(yaml.safe_dump(other_run, sort_keys=False))
        self.decision = foreign
        self.question_text("implementation")
        sup.commit_all(self.env.root, "synthetic foreign-run revision")
        code, out, err, gateway = self.formal()
        self.assertEqual(code, 2, (out, err))
        self.assertIn("decision-identity-refused", err)
        self.assertEqual(gateway.requests, [])

    def test_direct_symlink_revision_selector_refuses_before_calls(self):
        link = self.env.root / "selection-link"
        link.symlink_to(self.decision.parent, target_is_directory=True)
        raw = "selection-link/" + self.decision.name
        self.refused_without_call(self.run_main(self.argv("--implementation-revision", raw,
            "--retain-subjects", prompt=2, subjects=[self.decision])))
        self.assertEqual(self.records(), [])

    def test_symlink_dotdot_revision_selector_refuses_before_normalization(self):
        link = self.env.root / "selection-link"
        link.symlink_to(self.decision.parent, target_is_directory=True)
        # Preserve the literal selector. env.rel() would resolve away the traversal.
        raw = "selection-link/../" + self.env.rel(self.decision)
        self.refused_without_call(self.run_main(self.argv("--implementation-revision", raw,
            "--retain-subjects", prompt=2, subjects=[self.decision])))
        self.assertEqual(self.records(), [])

    def test_implementation_architectural_vote_is_preserved_for_gate_routing(self):
        gateway = runner.Gateway(content=runner.vote_json(decision="ARCHITECTURAL",
            findings=[{"id": "F1", "dimension": "Consistency", "safety_adjacent": False,
                "kind": "blocking", "text": "Synthetic architectural conflict"}]))
        code, out, err, _ = self.formal(gateway=gateway)
        self.assertEqual(code, 0, (out, err))
        self.assertEqual([s["decision"] for s in self.only_record()[1]["seats"]], ["ARCHITECTURAL"] * 3)


class MigrationTransport(runner._Base):
    """Exact batch selector through the production runner and a synthetic gateway."""
    question_text = Transport.question_text
    refused_without_call = Transport.refused_without_call

    def setUp(self):
        super().setUp()
        Transport.install(self, cycles.book_two())
        declaration = self.env.book["implementation_slots"][0]
        declaration.update(scope=["docs/adrs/migrations"], migration_batch={"role": "migration-batch",
            "path": "docs/adrs/migrations/implementation-pilot-001.yaml"})
        self.env.write_book_and_run(current=2)
        self.env.run.update(format_version="2", implementation_bindings=[], migration_bindings=[])
        self.env.run_path.write_text(yaml.safe_dump(self.env.run, sort_keys=False))
        from test_implementation_migration import document
        self.doc = document(); self.doc["approval_slot"] = declaration["slot"]
        self.decision = self.env.root / declaration["migration_batch"]["path"]
        self.decision.parent.mkdir(parents=True)
        self.decision.write_text(yaml.safe_dump(self.doc, sort_keys=False))
        (self.subject.parent / "ADR-0146-authorizer.md").write_text("---\n" +
            yaml.safe_dump(cycles.migration_authorizer()) + "---\n")
        self.question_text("implementation")
        runner.commit_pending(self.env.root, "synthetic migration transport fixture")

    def selected(self, *, given=None, extra=(), subjects=None):
        return self.run_main(self.argv("--migration-batch", given or self.env.rel(self.decision),
            "--retain-subjects", *extra, prompt=2, subjects=subjects or [self.decision]))

    def snapshot(self):
        return {p.relative_to(self.env.root).as_posix(): p.read_bytes()
                for p in self.env.root.rglob("*") if p.is_file() and ".git" not in p.parts}

    def test_actual_batch_transport_retains_exact_subject_without_binding_or_application(self):
        run_before = self.env.run_path.read_bytes(); book_before = self.env.book_path.read_bytes()
        code, out, err, gateway = self.selected()
        self.assertEqual(code, 0, (out, err)); self.assertEqual(len(gateway.requests), 3)
        _, record = self.only_record()
        selected = record["subjects"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["path"], self.env.rel(self.decision))
        self.assertEqual(selected[0]["sha256"], hashlib.sha256(self.decision.read_bytes()).hexdigest())
        self.assertEqual((self.env.run_dir / selected[0]["retained_copy"]).read_bytes(), self.decision.read_bytes())
        self.assertEqual(self.env.run_path.read_bytes(), run_before)
        self.assertEqual(self.env.book_path.read_bytes(), book_before)

    def test_missing_current_replacement_refuses_before_transport_or_writes(self):
        path = self.subject.parent / "ADR-0146-authorizer.md"
        authorizer = cycles.migration_authorizer()
        authorizer["governs"] = authorizer["governs"][:1]
        path.write_text("---\n" + yaml.safe_dump(authorizer) + "---\n")
        runner.commit_pending(self.env.root, "synthetic missing current replacement")
        before = self.snapshot()
        result = self.selected()
        self.refused_without_call(result)
        self.assertIn("constraint-not-live-accepted", result[2])
        self.assertEqual(self.snapshot(), before)

    def test_competing_selectors_cross_role_wrong_path_and_duplicate_refuse_without_writes(self):
        cases = [self.selected(extra=("--implementation-revision", self.env.rel(self.decision))),
            self.run_main(self.argv("--implementation-revision", self.env.rel(self.decision),
                "--retain-subjects", prompt=2, subjects=[self.decision])),
            self.selected(given=self.env.rel(self.subject)), self.selected(subjects=[self.decision, self.decision])]
        for result in cases: self.refused_without_call(result)
        self.assertEqual(self.records(), [])

    def test_slot_digest_retention_dimensions_and_raw_traversal_refuse(self):
        original = self.decision.read_bytes(); question = self.question.read_bytes()
        link = self.env.root / "batch-link"; link.symlink_to(self.decision.parent, target_is_directory=True)
        for raw in ("batch-link/" + self.decision.name, "batch-link/../" + self.env.rel(self.decision)):
            before = self.snapshot(); self.refused_without_call(self.selected(given=raw))
            self.assertEqual(self.snapshot(), before)
        self.doc["approval_slot"] = "implementation-2"
        self.decision.write_text(yaml.safe_dump(self.doc)); self.question_text("implementation")
        runner.commit_pending(self.env.root, "synthetic wrong approval slot")
        before = self.snapshot(); self.refused_without_call(self.selected()); self.assertEqual(self.snapshot(), before)
        self.decision.write_bytes(original); self.question.write_bytes(question)
        runner.commit_pending(self.env.root, "synthetic restored slot")
        for omitted in (hashlib.sha256(original).hexdigest(), "Security", "architectural conflict"):
            self.question.write_text(question.decode().replace(omitted, "omitted"))
            before = self.snapshot(); self.refused_without_call(self.selected()); self.assertEqual(self.snapshot(), before)
        self.question.write_bytes(question)
        before = self.snapshot()
        self.refused_without_call(self.run_main(self.argv("--migration-batch", self.env.rel(self.decision),
            prompt=2, subjects=[self.decision])))
        self.assertEqual(self.snapshot(), before)


class Instructions(unittest.TestCase):
    def test_runner_and_close_share_one_dimension_table(self):
        import implementation_approval as approval
        self.assertEqual(runner.rc_mod._IMPLEMENTATION_DIMENSIONS, approval.QUESTION_DIMENSIONS)

    def test_implementation_includes_architectural_vote_and_five_dimensions(self):
        text = gate_vote_instruction("implementation")
        for token in ("ARCHITECTURAL", "Completeness", "Correctness", "Consistency", "Clarity", "Security"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
