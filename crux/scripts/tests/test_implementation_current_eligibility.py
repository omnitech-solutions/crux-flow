"""Current eligibility uses completed authority; historical review stays separate."""
from pathlib import Path
import json
import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import implementation_approval as approval
import implementation_migration as migration
import yaml
import test_implementation_authority as fixtures
import _council_gate_support as support
import implementation_decisions as decisions
import test_implementation_cycles as cycles
import test_implementation_decisions as decision_fixtures
import test_run_council as runner
import test_implementation_migration_apply as apply_fixtures


class CurrentConstraints(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuthorityView("runTest")
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def published(self):
        self.fixture.close(); self.fixture.publish()

    def nonretiring_source(self):
        batch = migration.load_batch(self.root, self.fixture.f.path)
        entry = batch["entries"][0]; entry["affected_governs"][0]["retires"] = []
        entry["source_identity"] = migration.source_identity(entry)
        source = self.root / "docs/adrs/ADR-0110-source.md"
        source.write_text("---\n" + yaml.safe_dump({"id": "ADR-0110", "status": "Accepted",
                          "governs": entry["affected_governs"]}) + "---\n" + entry["clause"]["text"])
        self.fixture.f.path.write_text(yaml.safe_dump(batch, sort_keys=False))
        self.fixture.refresh_reviewed_batch()

    def test_demoted_accepted_handle_refuses_and_accepted_replacement_remains_eligible(self):
        self.nonretiring_source()
        self.published()
        before = self.fixture.snapshot()
        self.assertEqual(migration.authority_view(self.root)["state"], "published")
        with self.assertRaisesRegex(approval.Refused, "^constraint-not-live-accepted$"):
            approval.live_constraints(self.root, ["ADR-0110/rotation"])
        approval.live_constraints(self.root, ["ADR-0146/rotation-preserves-assessment-outcomes"])
        self.assertEqual(self.fixture.snapshot(), before)


class RecoveryMechanisms(unittest.TestCase):
    """Actual disposable callers; receipts are synthetic and transport is local."""

    @classmethod
    def setUpClass(cls):
        apply_fixtures.ActualCloseTests.setUpClass()
        cls.parent_fixture = apply_fixtures.ActualCloseTests.fixture
        cls.addClassCleanup(apply_fixtures.ActualCloseTests.doClassCleanups)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        shutil.copytree(self.parent_fixture.root, self.root)
        self.writer = apply_fixtures.writer()
        self.initial_batch = self.parent_fixture.f.rel
        self.witness = self.root / self.parent_fixture.witness.relative_to(self.parent_fixture.root)

    def snapshot(self):
        return apply_fixtures.inventory(self.root)

    @classmethod
    def initial_publication(cls):
        """The initial batch published and refreshed once per class from the parent fixture.

        A test that starts from that state copies this repository; none writes to it."""
        if "_initial" not in cls.__dict__:
            temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(temp.cleanup)
            root = Path(temp.name).resolve() / "repo"
            shutil.copytree(cls.parent_fixture.root, root)
            writer, batch_path = apply_fixtures.writer(), cls.parent_fixture.f.rel
            first = writer.apply(root, batch_path)["state"]
            support.commit_all(root, "synthetic actual recovery application witness")
            second = writer.apply(root, batch_path)["state"]
            support.commit_all(root, "synthetic actual refreshed recovery caches")
            if (first, second) != ("pending_commit", "refreshed"):
                raise AssertionError("initial publication fixture: %r" % ((first, second),))
            cls._initial = root
            cls.addClassCleanup(delattr, cls, "_initial")
        return cls._initial

    def publish_initial(self):
        """`publish(self.initial_batch)` on a repository still in its setUp state."""
        shutil.rmtree(self.root); shutil.copytree(self.initial_publication(), self.root)
        return self.selected_publication(self.initial_batch)

    def selected_publication(self, batch_path):
        proof, _ = self.writer._select_close(self.root, batch_path)
        publications = migration._discover_publications(self.root)
        selected = [row for row in publications if json.loads(row["content"])["batch"] == proof.binding["batch"]]
        self.assertEqual(len(selected), 1)
        return selected[0], proof

    def publish(self, batch_path):
        result = self.writer.apply(self.root, batch_path)
        self.assertEqual(result["state"], "pending_commit")
        support.commit_all(self.root, "synthetic actual recovery application witness")
        self.assertEqual(self.writer.apply(self.root, batch_path)["state"], "refreshed")
        support.commit_all(self.root, "synthetic actual refreshed recovery caches")
        return self.selected_publication(batch_path)

    def subject(self, parent, parent_proof, *, book_id="PB-0998", at_review=False,
                change_dependency=True, start_only=False, fault=None):
        batch = copy.deepcopy(migration.load_batch(self.root, parent_proof.binding["batch"]["path"]))
        ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
        if change_dependency:
            ledger.write_text(ledger.read_text() + "# synthetic independently reviewed dependency change\n")
        link = {"witness": {key: parent[key] for key in ("path", "sha256")},
            "batch": parent_proof.binding["batch"],
            "source_identities": sorted(entry["source_identity"] for entry in batch["entries"])}
        if fault == "parent":
            link["witness"]["sha256"] = "0" * 64
        elif fault == "source":
            entry = batch["entries"][0]
            entry["clause"]["text"] += "Changed reasoning.\n"
            entry["clause"]["sha256"] = migration._digest(entry["clause"]["text"].encode())
            entry["historical_destination"]["clause_sha256"] = entry["clause"]["sha256"]
            entry["source_identity"] = migration.source_identity(entry)
            link["source_identities"] = sorted(e["source_identity"] for e in batch["entries"])
        elif fault == "disposition":
            batch["entries"][0]["disposition"] = "historical-fact"
        elif fault in ("authorizer", "replacement"):
            host = self.root / "docs/adrs/ADR-0146-authorizer.md"
            text = host.read_text(); front, body = text[4:].split("---\n", 1)
            record = yaml.safe_load(front)
            if fault == "authorizer": record["status"] = "Superseded"
            else:
                replacements = {h for entry in batch["entries"] for h in entry["replacement_handles"]}
                record["governs"] = [r for r in record["governs"] if r["handle"] not in replacements]
            host.write_text("---\n" + yaml.safe_dump(record) + "---\n" + body)
        batch.update(recovery_from=link,
            batch_id="implementation-pilot-001-recovery-" + migration._canonical_digest(link))
        for ref in batch["signed_dependencies"]:
            if ref["path"] == ledger.relative_to(self.root).as_posix():
                ref["sha256"] = migration._digest(ledger.read_bytes())
        path = (self.root / self.initial_batch).with_name(batch["batch_id"] + ".yaml")
        path.write_text(yaml.safe_dump(batch, sort_keys=False))
        return self.start_subject(path, book_id=book_id, at_review=at_review,
            start_only=start_only, demoted_constraint=fault == "constraint")

    def start_subject(self, path, *, book_id="PB-0998", at_review=False,
                      start_only=False, demoted_constraint=False):
        book = cycles.book_two(); book.update(id=book_id, current_run=None, current_prompt=None)
        declaration = book["implementation_slots"][0]
        declaration.update(scope=["docs/adrs/migrations"],
            constraint_refs=self.parent_fixture.f.book["implementation_slots"][0]["constraint_refs"],
            migration_batch={"role": "migration-batch", "path": path.relative_to(self.root).as_posix()})
        if demoted_constraint: declaration["constraint_refs"] = ["ADR-0110/rotation"]
        book_path = self.root / f"docs/promptbooks/active/{book_id}-fixture.yaml"
        run_path = self.root / f"docs/promptbooks/runs/{book_id}-fixture/run-RUN-001.yaml"
        book_path.write_text(yaml.safe_dump(book, sort_keys=False))
        support.commit_all(self.root, "synthetic separately reviewed recovery book and batch")
        started = subprocess.run([sys.executable, str(support.SCRIPTS / "start-run.py"),
            str(book_path), "--run-id", "RUN-001", "--output", str(run_path)],
            capture_output=True, env=support.scrubbed_env())
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        book.update(current_run="RUN-001", current_prompt=1)
        book_path.write_text(yaml.safe_dump(book, sort_keys=False))
        subject = (path, run_path, book_path, run_path.parent / "council/round.json")
        if start_only:
            support.commit_all(self.root, "synthetic actual recovery start before parent publication")
            return subject
        return self.review(subject, at_review=at_review)

    def review(self, subject, *, at_review=False):
        path, run_path, book_path, receipt = subject
        book = yaml.safe_load(book_path.read_bytes())
        council = run_path.parent / "council"; council.mkdir()
        retained = council / "subjects/batch.yaml"; retained.parent.mkdir()
        retained.write_bytes(path.read_bytes())
        env = object.__new__(support.Env); env.root = self.root; env.hash = support.vp.compute_book_hash(book)
        doc = env.council_doc(module_tag="implementation-1", prompt=2, subjects=[{
            "path": path.relative_to(self.root).as_posix(), "sha256": migration._digest(path.read_bytes()),
            "retained_copy": retained.relative_to(self.root).as_posix()}])
        doc["book"]["id"] = book["id"]; receipt.write_text(json.dumps(doc))
        support.commit_all(self.root, "synthetic exact recovery council fixture")
        for _ in range(1 if at_review else 3):
            response = self.advance(subject)
            self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
            support.commit_all(self.root, "synthetic actual recovery prior prompt")
        return subject

    def refresh_dependency(self, path):
        ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
        ledger.write_text(ledger.read_text() + "# synthetic independently reviewed dependency change\n")
        batch = migration.load_batch(self.root, path)
        for ref in batch["signed_dependencies"]:
            if ref["path"] == ledger.relative_to(self.root).as_posix():
                ref["sha256"] = migration._digest(ledger.read_bytes())
        path.write_text(yaml.safe_dump(batch, sort_keys=False))

    def advance(self, subject, *, close=False):
        path, run_path, book_path, receipt = subject
        return subprocess.run([sys.executable, str(support.SCRIPTS / "advance-run.py"),
            str(run_path), "--book", str(book_path), "--outcome", "done", "--artifacts",
            receipt.relative_to(self.root).as_posix(),
            *(["--migration-batch", path.relative_to(self.root).as_posix()] if close else [])],
            capture_output=True, env=support.scrubbed_env())

    def close(self, subject):
        response = self.advance(subject, close=True)
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        support.commit_all(self.root, "synthetic actual separately reviewed recovery close")

    def test_actual_recovery_runner_allows_stale_parent_dependency_before_transport(self):
        parent, proof = self.publish_initial()
        subject = self.subject(parent, proof, at_review=True)
        with self.assertRaises(migration.Refused): migration.authority_view(self.root)
        path, run_path, book_path, _receipt = subject
        actor = CurrentMechanisms.runner_actor(self, path, run_path, book_path)
        code, out, err, gateway = actor.run_main(actor.argv("--retain-subjects", "--migration-batch",
            path.relative_to(self.root).as_posix(), prompt=2, round_=2, subjects=[path]))
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(json.loads(out)["outcome"], "ran")
        self.assertEqual(len(gateway.requests), 3)

    def test_actual_recovery_close_and_next_advance_allow_stale_parent_dependency(self):
        parent, proof = self.publish_initial()
        subject = self.subject(parent, proof)
        original = self.witness.read_bytes()
        with self.assertRaises(migration.Refused): migration.authority_view(self.root)
        self.close(subject)
        response = self.advance(subject)
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        self.assertEqual(json.loads(response.stdout)["current_prompt"], 6)
        self.assertEqual(self.witness.read_bytes(), original)

    def test_published_recovery_ancestor_run_can_advance_after_later_child_publication(self):
        parent, proof = self.publish_initial()
        older = self.subject(parent, proof); self.close(older)
        parent_b, proof_b = self.publish(older[0].relative_to(self.root).as_posix())
        original_a = self.witness.read_bytes()
        original_b = (self.root / parent_b["path"]).read_bytes()
        later = self.subject(parent_b, proof_b, book_id="PB-0997"); self.close(later)
        self.publish(later[0].relative_to(self.root).as_posix())
        response = self.advance(older)
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        self.assertEqual(json.loads(response.stdout)["current_prompt"], 6)
        self.assertEqual(self.witness.read_bytes(), original_a)
        self.assertEqual((self.root / parent_b["path"]).read_bytes(), original_b)

    def test_recovery_run_started_before_parent_publication_can_close_after_its_publication(self):
        proof, run_rel = self.writer._select_close(self.root, self.initial_batch)
        batch = migration.load_batch(self.root, self.initial_batch)
        raw, expected = self.writer._locator(proof, run_rel, batch, repo_root=self.root)
        self.assertFalse(self.witness.exists())
        subject = self.subject(expected, proof, change_dependency=False, start_only=True)
        start_commit = support.git(self.root, "rev-parse", "HEAD").strip()
        parent, _proof = self.publish(self.initial_batch)
        self.assertEqual(parent["sha256"], migration._digest(raw))
        self.assertEqual(approval.cr.git(self.root, "merge-base", "--is-ancestor",
            start_commit, parent["publication_commit"]).returncode, 0)
        self.assertNotEqual(start_commit, parent["publication_commit"])
        self.refresh_dependency(subject[0]); self.review(subject)
        self.close(subject)
        self.assertEqual(self.witness.read_bytes(), raw)

    def test_ordinary_actual_close_still_refuses_stale_published_dependency_before_writes(self):
        self.publish_initial()
        ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
        ledger.write_text(ledger.read_text() + "# synthetic stale current dependency\n")
        support.commit_all(self.root, "synthetic stale ordinary current state")
        ordinary = CurrentMechanisms("runTest"); ordinary.root = self.root
        path, run_path, book_path, receipt = ordinary.ordinary_subject("ADR-0001/fixture-rule")
        before = self.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
        response = ordinary.advance(run_path, book_path, receipt, path.relative_to(self.root).as_posix())
        self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
        self.assertEqual(json.loads(response.stdout), {
            "error": "migration-signed-dependency-drift; nothing was written"})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)

    def test_recovery_faults_refuse_actual_runner_before_transport_and_close_before_writes(self):
        parent, proof = self.publish_initial()
        with tempfile.TemporaryDirectory() as scratch:
            template = Path(scratch) / "published-parent"
            shutil.copytree(self.root, template)
            for fault, code in (("parent", "migration-recovery-link-refused"),
                ("source", "migration-recovery-source-refused"),
                ("disposition", "migration-recovery-source-refused"),
                ("authorizer", "constraint-not-live-accepted"),
                ("replacement", "constraint-not-live-accepted"),
                ("constraint", "constraint-not-live-accepted")):
                with self.subTest(fault=fault):
                    shutil.rmtree(self.root); shutil.copytree(template, self.root)
                    subject = self.subject(parent, proof, at_review=True, fault=fault)
                    path, run_path, book_path, _ = subject
                    actor = CurrentMechanisms.runner_actor(self, path, run_path, book_path)
                    before = self.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
                    result, out, err, gateway = actor.run_main(actor.argv("--retain-subjects",
                        "--migration-batch", path.relative_to(self.root).as_posix(),
                        prompt=2, round_=2, subjects=[path]))
                    self.assertEqual(result, 2, (out, err))
                    self.assertEqual(out, "")
                    self.assertEqual(err, "run-council: the selected migration batch was refused: " + code + "\n")
                    self.assertEqual(gateway.requests, [])
                    self.assertFalse(list(run_path.parent.glob("council/attempts/*")))
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)
                    for _ in range(2):
                        response = self.advance(subject)
                        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
                        support.commit_all(self.root, "synthetic refused recovery close setup")
                    before = self.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
                    response = self.advance(subject, close=True)
                    self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
                    self.assertEqual(json.loads(response.stdout), {"error": code + "; nothing was written"})
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)
                    historical = approval.validate_historical_migration_binding(self.root,
                        self.root / self.parent_fixture.f.run_path.relative_to(self.parent_fixture.root),
                        slot=proof.binding["slot"], batch_path=proof.binding["batch"]["path"],
                        batch_sha256=proof.binding["batch"]["sha256"])
                    self.assertEqual(historical.binding, proof.binding)

    def test_new_run_cannot_borrow_exact_published_recovery_subject(self):
        parent, proof = self.publish_initial()
        owning = self.subject(parent, proof); self.close(owning)
        self.publish(owning[0].relative_to(self.root).as_posix())
        borrowed = self.start_subject(owning[0], book_id="PB-0997", at_review=True)
        path, run_path, book_path, _ = borrowed
        actor = CurrentMechanisms.runner_actor(self, path, run_path, book_path)
        before = self.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
        code, out, err, gateway = actor.run_main(actor.argv("--retain-subjects",
            "--migration-batch", path.relative_to(self.root).as_posix(),
            prompt=2, round_=2, subjects=[path]))
        self.assertEqual(code, 2, (out, err)); self.assertEqual(out, "")
        self.assertEqual(err, "run-council: the selected migration batch was refused: migration-recovery-link-refused\n")
        self.assertEqual(gateway.requests, [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)
        for _ in range(2):
            response = self.advance(borrowed)
            self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
            support.commit_all(self.root, "synthetic borrowed subject close setup")
        before = self.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
        response = self.advance(borrowed, close=True)
        self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
        self.assertEqual(json.loads(response.stdout), {
            "error": "migration-recovery-link-refused; nothing was written"})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)


class CurrentMechanisms(unittest.TestCase):
    setUp = CurrentConstraints.setUp
    published = CurrentConstraints.published
    nonretiring_source = CurrentConstraints.nonretiring_source

    def ordinary_subject(self, handle, *, at_review=False, archive_book=False):
        book = cycles.book_two()
        book.update(id="PB-0998", current_run=None, current_prompt=None)
        declaration = book["implementation_slots"][0]
        declaration["constraint_refs"] = [handle]
        book_path = self.root / "docs/promptbooks/active/PB-0998-fixture.yaml"
        run_path = self.root / "docs/promptbooks/runs/PB-0998-fixture/run-RUN-001.yaml"
        book_path.write_text(yaml.safe_dump(book, sort_keys=False))
        support.commit_all(self.root, "synthetic new ordinary book")
        response = subprocess.run([sys.executable, str(support.SCRIPTS / "start-run.py"),
            str(book_path), "--run-id", "RUN-001", "--output", str(run_path)],
            capture_output=True, env=support.scrubbed_env())
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        book.update(current_run="RUN-001", current_prompt=1)
        book_path.write_text(yaml.safe_dump(book, sort_keys=False))
        doc = decision_fixtures.decision()
        doc.update(book_id=book["id"], slot=declaration["slot"], constraint_refs=[handle])
        path = run_path.parent / "implementations/RUN-001/strategy/revision-001.yaml"
        path.parent.mkdir(parents=True); path.write_text(yaml.safe_dump(doc, sort_keys=False))
        rel = path.relative_to(self.root).as_posix()
        witnessed = subprocess.run([sys.executable, str(support.SCRIPTS / "run-work-witness.py"),
            "record", str(run_path), "--prompt", "1", "--path", str(path)],
            capture_output=True, env=support.scrubbed_env())
        self.assertEqual(witnessed.returncode, 0, witnessed.stdout + witnessed.stderr)
        council = run_path.parent / "council"; council.mkdir()
        retained = council / "subjects/revision.yaml"; retained.parent.mkdir()
        retained.write_bytes(path.read_bytes())
        env = object.__new__(support.Env); env.root = self.root; env.hash = support.vp.compute_book_hash(book)
        receipt = council / "round.json"
        record = env.council_doc(module_tag=declaration["slot"], prompt=2, subjects=[{
            "path": rel, "sha256": approval.cr.sha256_file(path),
            "retained_copy": retained.relative_to(self.root).as_posix()}])
        record["book"]["id"] = book["id"]
        receipt.write_text(json.dumps(record))
        support.commit_all(self.root, "synthetic exact ordinary council fixture")
        for _ in range(1 if at_review else 3):
            response = self.advance(run_path, book_path, receipt)
            self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
            support.commit_all(self.root, "actual ordinary fixture advance")
        if archive_book:
            archive = book_path.parent.parent / "archive"
            archive.mkdir(exist_ok=True)
            destination = archive / book_path.name
            book_path.rename(destination); book_path = destination
            support.commit_all(self.root, "synthetic started book retained in supported archive tier")
        return path, run_path, book_path, receipt

    def advance(self, run_path, book_path, receipt, selected=None):
        return subprocess.run([sys.executable, str(support.SCRIPTS / "advance-run.py"),
            str(run_path), "--book", str(book_path), "--outcome", "done", "--artifacts",
            receipt.relative_to(self.root).as_posix(),
            *(["--implementation-revision", selected] if selected else [])],
            capture_output=True, env=support.scrubbed_env())

    def decision_cli(self, command, path, evidence=None, output=None):
        return subprocess.run([sys.executable, str(support.SCRIPTS / "implementation-decisions.py"),
            command, "--decision", str(path), "--repo-root", str(self.root),
            *(["--evidence", str(evidence)] if evidence else []),
            *(["--output", str(output)] if output else [])],
            capture_output=True, env=support.scrubbed_env())

    def runner_actor(self, path, run_path, book_path):
        actor = runner._Base("runTest"); actor.setUp(); self.addCleanup(actor.doCleanups)
        actor.env.root = self.root; actor.env.run_path = run_path; actor.env.book_path = book_path
        actor.env.run_dir = run_path.parent; actor.env.council = run_path.parent / "council"
        actor.question = self.root / "ordinary-question.md"
        rel = path.relative_to(self.root).as_posix(); digest = approval.cr.sha256_file(path)
        actor.question.write_text(f'Assess {rel} SHA256 {digest}, Completeness, Correctness, '
                                  'Consistency, Clarity, Security and architectural conflict.\n')
        support.commit_all(self.root, "synthetic exact ordinary review question")
        return actor

    def test_actual_new_close_refuses_demoted_constraint_before_any_write(self):
        self.nonretiring_source()
        path, run_path, book_path, receipt = self.ordinary_subject(
            "ADR-0110/rotation", archive_book=True)
        self.published()
        before = self.fixture.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
        response = self.advance(run_path, book_path, receipt, path.relative_to(self.root).as_posix())
        self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
        self.assertEqual(json.loads(response.stdout), {
            "error": "constraint-not-live-accepted; nothing was written"})
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)

    def test_actual_runner_refuses_demoted_constraint_before_transport_or_attempt(self):
        self.nonretiring_source()
        path, run_path, book_path, _receipt = self.ordinary_subject(
            "ADR-0110/rotation", at_review=True, archive_book=True)
        self.published()
        actor = self.runner_actor(path, run_path, book_path)
        rel = path.relative_to(self.root).as_posix()
        before = self.fixture.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
        code, out, err, gateway = actor.run_main(actor.argv("--retain-subjects",
            "--implementation-revision", rel, prompt=2, round_=2, subjects=[path]))
        self.assertEqual(code, 2, (out, err))
        self.assertEqual(out, "")
        self.assertEqual(err, "run-council: the selected Implementation Decision was refused: "
                             "constraint-not-live-accepted\n")
        self.assertEqual(gateway.requests, [])
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)

    def test_accepted_replacement_actual_close_is_eligible(self):
        self.nonretiring_source(); self.published()
        path, run_path, book_path, receipt = self.ordinary_subject(
            "ADR-0146/rotation-preserves-assessment-outcomes")
        response = self.advance(run_path, book_path, receipt, path.relative_to(self.root).as_posix())
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        support.commit_all(self.root, "synthetic successful actual replacement close")
        proof = approval.validate_implementation_binding(self.root, run_path,
            slot="implementation-1", revision_path=path.relative_to(self.root).as_posix(),
            revision_sha256=approval.cr.sha256_file(path))
        self.assertEqual(proof.slot["constraint_refs"], [
            "ADR-0146/rotation-preserves-assessment-outcomes"])

    def test_accepted_replacement_actual_runner_reaches_stub_transport(self):
        self.nonretiring_source(); self.published()
        path, run_path, book_path, _receipt = self.ordinary_subject(
            "ADR-0146/rotation-preserves-assessment-outcomes", at_review=True)
        actor = self.runner_actor(path, run_path, book_path)
        rel = path.relative_to(self.root).as_posix()
        code, out, err, gateway = actor.run_main(actor.argv("--retain-subjects",
            "--implementation-revision", rel, prompt=2, round_=2, subjects=[path]))
        self.assertEqual(code, 0, (out, err))
        answer = json.loads(out)
        self.assertEqual(answer["outcome"], "ran")
        self.assertTrue(answer["quorum_met"])
        self.assertIsNone(answer["refusal_reason"])
        self.assertEqual(len(gateway.requests), 3)

    def published_delivered_subject(self):
        self.nonretiring_source()
        path, run_path, book_path, receipt = self.ordinary_subject(
            "ADR-0110/rotation", archive_book=True)
        response = self.advance(run_path, book_path, receipt, path.relative_to(self.root).as_posix())
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        support.commit_all(self.root, "synthetic successful actual close before demotion")
        preimage = support.git(self.root, "rev-parse", "HEAD").strip()
        (self.root / "widget.txt").write_text("synthetic delivered implementation\n")
        support.commit_all(self.root, "synthetic actual source change")
        delivery = support.git(self.root, "rev-parse", "HEAD").strip()
        def ref(p):
            return {"path": p.relative_to(self.root).as_posix(), "sha256": approval.cr.sha256_file(p)}
        report = run_path.parent / "reviews/synthetic-independent.json"
        report.parent.mkdir()
        review = support.fixture("reviewer-report-paths.json")
        run = yaml.safe_load(run_path.read_bytes())
        review.update(book={"id": run["book_id"], "content_hash": run["book_content_hash"]},
            run_id=run["run_id"], subject={"form": "paths", "paths": [ref(path), ref(self.root / "widget.txt")]})
        report.write_text(json.dumps(review))
        support.commit_all(self.root, "synthetic independent report, no human verdict")
        result = {"record_type": "implementation-result", "format_version": "1",
            "decision": ref(path), "delivery_state": "complete", "source_revision": delivery,
            "preimage_revision": preimage, "scope": ["widget.txt"], "sources": [{"path": "widget.txt",
                "preimage": decisions.source_hash(self.root, preimage, "widget.txt"),
                "delivered": decisions.source_hash(self.root, delivery, "widget.txt")}],
            "reviews": [ref(report)], "annotations": []}
        candidate = run_path.parent / "synthetic-result.yaml"
        candidate.write_text(yaml.safe_dump(result))
        response = self.decision_cli("result", path, candidate)
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        support.commit_all(self.root, "synthetic actual result before demotion")
        annotation = {"record_type": "implementation-annotation", "format_version": "1",
            "decision": ref(path), "operation": "correct-spelling", "target": "display_title",
            "original_sha256": approval.cr.sha256_bytes(b"Choose stategy"), "correction": "Choose strategy"}
        annotation_path = run_path.parent / "synthetic-annotation.yaml"
        annotation_path.write_text(yaml.safe_dump(annotation))
        support.commit_all(self.root, "synthetic candidate correction")
        self.published()
        return path, candidate, annotation_path

    def test_new_result_and_annotation_refuse_demoted_constraint_before_any_write(self):
        path, candidate, annotation_path = self.published_delivered_subject()
        before = self.fixture.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
        for command, evidence, output in (("result", candidate, None),
                ("annotate", annotation_path, path.with_name("annotation-001.yaml"))):
            with self.subTest(command=command):
                response = self.decision_cli(command, path, evidence, output)
                self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
                self.assertEqual(json.loads(response.stdout), {"refused": "constraint-not-live-accepted"})
                self.assertEqual(self.fixture.snapshot(), before)
                self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)

    def test_historical_intent_result_and_source_facts_readable_after_constraint_demotion(self):
        path, _candidate, _annotation_path = self.published_delivered_subject()
        before = self.fixture.snapshot()
        response = self.decision_cli("query", path)
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        answer = json.loads(response.stdout)
        self.assertTrue(answer["reviewed_intent"]["approved"])
        self.assertEqual(answer["reviewed_intent"]["original"], yaml.safe_load(path.read_bytes()))
        self.assertEqual(answer["current_eligibility"], {"eligible": False,
            "constraint_refs": ["ADR-0110/rotation"], "limit": "constraint-not-live-accepted"})
        self.assertEqual(answer["historical_delivery"][0]["state"], "complete")
        self.assertEqual(answer["current_state"]["state"], "delivered")
        self.assertEqual(self.fixture.snapshot(), before)

    def test_active_citation_invalid_view_refuses_actual_close_before_any_write(self):
        self.nonretiring_source()
        path, run_path, book_path, receipt = self.ordinary_subject("ADR-0110/rotation")
        self.published()
        before = self.fixture.snapshot(); head = support.git(self.root, "rev-parse", "HEAD")
        response = self.advance(run_path, book_path, receipt, path.relative_to(self.root).as_posix())
        self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
        self.assertEqual(json.loads(response.stdout), {
            "error": "migration-citation-repair-required; nothing was written"})
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(support.git(self.root, "rev-parse", "HEAD"), head)

    def test_historical_retirement_survives_displacer_archive(self):
        predecessor = self.root / "docs/adrs/ADR-0109-predecessor.md"
        predecessor.write_text('---\nid: ADR-0109\nstatus: Accepted\ngoverns:\n'
                               '- handle: ADR-0109/old-rotation\n  rule: Old rotation.\n---\n')
        support.commit_all(self.root, "synthetic prior architectural handle")
        self.published()
        source = self.root / "docs/adrs/ADR-0110-source.md"
        source.write_text(source.read_text().replace("status: Accepted", "status: Superseded"))
        archive = source.parent / "archive"; archive.mkdir(); source.rename(archive / source.name)
        support.commit_all(self.root, "synthetic source archive preserves frozen retirement")
        before = self.fixture.snapshot()
        self.assertIn("ADR-0109/old-rotation", migration.authority_view(self.root)["retired_handles"])
        with self.assertRaisesRegex(approval.Refused, "^constraint-not-live-accepted$"):
            approval.live_constraints(self.root, ["ADR-0109/old-rotation"])
        self.assertEqual(self.fixture.snapshot(), before)

    def test_invalid_completed_view_propagates_instead_of_raw_accepted_fallback(self):
        self.published()
        ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
        ledger.write_text(ledger.read_text() + "# stale signed dependency\n")
        support.commit_all(self.root, "synthetic stale dependency")
        before = self.fixture.snapshot()
        with self.assertRaises(approval.Refused) as expected:
            migration.authority_view(self.root)
        with self.assertRaisesRegex(approval.Refused, "^" + expected.exception.code + "$"):
            approval.live_constraints(self.root, ["ADR-0001/fixture-rule"])
        self.assertEqual(self.fixture.snapshot(), before)

    def test_raw_constraint_refusal_precedes_authority_projection(self):
        with mock.patch.object(migration, "authority_view", side_effect=AssertionError("raw checks skipped")):
            with self.assertRaisesRegex(approval.Refused, "^constraint-identity-refused$"):
                approval.live_constraints(self.root, ["ADR-9998/absent"])

    def test_historical_proof_does_not_reenter_current_eligibility_and_both_seals_hold(self):
        self.published()
        before = self.fixture.snapshot()
        with mock.patch.object(approval, "live_constraints", side_effect=AssertionError("eligibility recursion")):
            self.assertEqual(migration.authority_view(self.root)["state"], "published")
            proof = approval.validate_historical_migration_binding(self.root, self.fixture.f.run_path,
                slot=self.fixture.f.slot, batch_path=self.fixture.f.rel,
                batch_sha256=approval.cr.sha256_file(self.fixture.f.path))
        self.assertEqual(proof.binding, self.fixture.proof.binding)
        for version in ("2", "3"):
            self.assertEqual(approval.cr.sha256_bytes(approval.production_contract_bytes(version)),
                             approval.supported_profile(version).contract_sha256)
        self.assertEqual(self.fixture.snapshot(), before)
