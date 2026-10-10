"""Synthetic cycle controls; not council or independent delivery evidence."""
from __future__ import annotations

import copy
import json
import shutil
import sys
import subprocess
import tempfile
import yaml
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup
import council_gate as cg
import implementation_approval as ap


def book_two(kind="implementation", *, combined=False, formal=True):
    source = sup.make_book("verify" if kind == "implementation" else kind)
    source["format_version"] = "2"
    source["cycle_kind"] = kind
    source.update(status="active", created_at="2026-10-01", forked_from=None)
    source["implementation_slots"] = []
    if kind != "patch":
        prep = source["prompts"].pop(0)
        source["prompts"].insert(len(source["prompts"]) - 1, prep)
        if kind == "implementation":
            for prompt in source["prompts"][:4]:
                prompt["module_tag"] = "implementation-1"
        if combined:
            inserted = copy.deepcopy(source["prompts"][:4])
            for prompt in inserted:
                prompt["module_tag"] = "implementation-1"
            source["prompts"][4:4] = inserted
        for n, prompt in enumerate(source["prompts"], 1):
            prompt["n"] = n
        source["total_prompts"] = len(source["prompts"])
        source["modules"] = {"adrs": int(kind == "adr"),
            "implementations": int(kind == "implementation" or combined),
            "verify": int(kind == "verify"), "dev_loops": 1, "review_cycles": 1}
    if kind == "patch":
        source["blast_radius"] = ["widget.txt"]
    if formal:
        slot = "patch" if kind == "patch" else "implementation-1" if kind == "implementation" or combined else "verify-1"
        source["implementation_slots"] = [{"slot": slot, "slug": "strategy", "scope": ["widget.txt"],
            "constraint_refs": ["ADR-0001/fixture-rule"]}]
    return source


class Structure(unittest.TestCase):
    def accepted(self, book):
        errors = []
        sup.vp.cycle_coverage_pass(book, errors, "synthetic")
        self.assertEqual(errors, [])
        ap.validate_format_two(book)

    def refused(self, book):
        errors = []
        sup.vp.cycle_coverage_pass(book, errors, "synthetic")
        self.assertTrue(errors)
        with self.assertRaises(ap.Refused):
            ap.validate_format_two(book)

    def test_all_kinds_and_combined_architecture(self):
        for kind in ("adr", "implementation", "verify", "patch"):
            with self.subTest(kind=kind):
                self.accepted(book_two(kind, formal=kind != "adr"))
        self.accepted(book_two("adr", combined=True))

    def test_incidental_slots_empty_but_dedicated_slot_required(self):
        for kind in ("adr", "verify", "patch"):
            with self.subTest(kind=kind):
                self.accepted(book_two(kind, formal=False))
        self.refused(book_two(formal=False))
        combined = book_two("adr", combined=True)
        combined["implementation_slots"] = []
        self.refused(combined)

    def test_any_grandfathering_and_unknown_kind_refuse(self):
        for value in (True, False):
            bad = book_two(); bad["cycle_grandfathered"] = value
            self.refused(bad)
        bad = book_two(); bad["cycle_kind"] = "unrecognized"
        self.refused(bad)

    def test_declared_counts_are_integers_and_match_modules(self):
        for key, value in (("implementations", 0), ("adrs", 1), ("dev_loops", 0),
                           ("review_cycles", 0), ("implementations", True), ("verify", -1)):
            with self.subTest(key=key, value=value):
                bad = book_two(); bad["modules"][key] = value
                self.refused(bad)

    def test_order_and_unknown_prefix_refuse(self):
        for mutation in ("prefix", "final", "sequence", "instance"):
            bad = book_two()
            if mutation == "prefix": bad["prompts"][0]["module_tag"] = "unknown-1"
            if mutation == "final": bad["prompts"][-1]["module_tag"] = "review-1"
            if mutation == "sequence": bad["prompts"][0]["n"] = 9
            if mutation == "instance":
                for prompt in bad["prompts"][:4]: prompt["module_tag"] = "implementation-2"
            with self.subTest(mutation=mutation): self.refused(bad)

    def test_patch_refuses_empty_or_nonempty_module_declarations(self):
        self.accepted(book_two("patch"))
        for declaration in ({}, book_two()["modules"]):
            with self.subTest(declaration=declaration):
                bad = book_two("patch"); bad["modules"] = declaration
                self.refused(bad)

    def test_hash_is_canonical_and_binds_kind_slots(self):
        source = book_two()
        self.assertEqual(sup.vp.compute_book_hash(source), ap.book_hash(source))
        for target in ("cycle_kind", "implementation_slots"):
            bad = copy.deepcopy(source)
            if target == "cycle_kind": bad[target] = "adr"
            else: bad[target][0]["scope"].append("other.txt")
            self.assertNotEqual(sup.vp.compute_book_hash(source), sup.vp.compute_book_hash(bad))
        legacy = sup.make_book()
        self.assertEqual(ap.book_hash(legacy), sup.vp.compute_book_hash(legacy))

    def test_selected_revision_is_not_hash_bound(self):
        source = book_two(); old = sup.vp.compute_book_hash(source)
        source["implementation_bindings"] = [{"revision": 99}]
        self.assertEqual(old, sup.vp.compute_book_hash(source))


class Classification(unittest.TestCase):
    def test_positions_and_actual_module_kind_with_unknown_start(self):
        for source, council, close, expected in ((book_two(), 2, 4, "implementation"),
                (book_two("adr", combined=True), 6, 8, "implementation"),
                (book_two("verify"), 2, 4, "verify")):
            for start in (cg.StartFields.of(source), cg.StartFields(False)):
                self.assertEqual(cg.classify(source, council, start).cls, "council")
                gate = cg.classify(source, close, start)
                self.assertEqual((gate.cls, gate.module_kind), ("module-close", expected))

    def test_bad_structure_cannot_escape_unknown_start(self):
        for start in (cg.StartFields(False), cg.StartFields(True, "implementation", True)):
            source = book_two(); source["cycle_kind"] = "unrecognized"
            with self.assertRaises(ValueError): cg.classify(source, 2, start)
            source = book_two(); source["prompts"][0]["module_tag"] = "unknown-1"
            with self.assertRaises(ValueError): cg.classify(source, 2, start)


class PolicyProfiles(unittest.TestCase):
    def test_production_dispatch_is_explicit_and_fixture_one_unsupported(self):
        self.assertEqual(ap.CONTRACT_VERSION, "4")
        profile = ap.supported_profile("2")
        self.assertEqual(profile.version, "2")
        self.assertEqual(ap.supported_profile("3").version, "3")
        self.assertEqual(ap.supported_profile("4").version, "4")
        for version in ("1", "5", "", None):
            with self.subTest(version=version), self.assertRaises(ap.Refused):
                ap.supported_profile(version)

    def test_complete_closure_binds_kernel_constants_and_shared_helpers(self):
        profile = ap.supported_profile("2")
        baseline = ap.gate_contract_bytes("2")
        with mock.patch.object(profile.kernel, "APPROVING", ()):
            self.assertNotEqual(baseline, ap.gate_contract_bytes("2"))
        with mock.patch.object(profile.kernel, "_removed_record_problem", lambda *args: None):
            with self.assertRaises(ap.Refused):
                ap.gate_contract_bytes("2")

    def test_current_authority_evolution_is_outside_historical_policy(self):
        baseline = ap.gate_contract_bytes()
        with mock.patch.object(ap, "live_constraints", lambda *args: None):
            self.assertEqual(baseline, ap.gate_contract_bytes())
        # Legacy routing globals are no longer policy-2's semantic globals.
        with mock.patch.object(cg, "APPROVING", ()):
            self.assertEqual(baseline, ap.gate_contract_bytes())

    def test_first_writer_cannot_issue_changed_semantics_under_profile_two(self):
        profile = ap.supported_profile("2")
        baseline = ap.production_contract_bytes("2")
        self.assertEqual(baseline, ap.gate_contract_bytes("2"))
        with mock.patch.object(profile.kernel, "APPROVING", ()):
            with self.assertRaises(ap.Refused): ap.production_contract_bytes("2")


class StartBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = sup.init_repo(Path(self.temp.name) / "repo")
        self.book = book_two()
        self.book.update(current_run=None, current_prompt=None)
        self.path = self.root / "docs/promptbooks/active/PB-0999-fixture.yaml"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(yaml.safe_dump(self.book, sort_keys=False))
        sup.commit_all(self.root, "synthetic initial book")
        self.output = self.root / "docs/promptbooks/runs/PB-0999-fixture/run-RUN-001.yaml"

    def start(self):
        return subprocess.run([sys.executable, str(sup.SCRIPTS / "start-run.py"), str(self.path),
            "--run-id", "RUN-001", "--output", str(self.output)], capture_output=True,
            env=sup.scrubbed_env())

    def test_actual_start_writes_valid_empty_binding_run(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        run = yaml.safe_load(self.output.read_bytes())
        self.assertEqual(run["format_version"], "2")
        self.assertEqual(run["implementation_bindings"], [])
        self.assertNotIn("migration_bindings", run)
        self.assertEqual(run["book_content_hash"], sup.vp.compute_book_hash(self.book))
        self.assertEqual(run["base_commit"], sup.git(self.root, "rev-parse", "HEAD").strip())
        errors = []; sup.vp.validate(run, sup.vp.load_schema(sup.vp.RUN_SCHEMA), "#", "#", errors, "synthetic")
        self.assertEqual(errors, [])
        self.assertFalse(__import__("json").loads(result.stdout)["book_pointer_updated"])

    def test_unquoted_date_book_starts_with_the_quoted_form_hash(self):
        # The validator's CLI loader normalizes an unquoted date to an ISO string, so start must too.
        text = self.path.read_text()
        self.assertIn("created_at: '2026-10-01'", text)
        self.path.write_text(text.replace("created_at: '2026-10-01'", "created_at: 2026-10-01"))
        self.assertIsInstance(yaml.safe_load(self.path.read_bytes())["created_at"], __import__("datetime").date)
        sup.commit_all(self.root, "synthetic unquoted date")
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        run = yaml.safe_load(self.output.read_bytes())
        self.assertEqual(run["book_content_hash"], sup.vp.compute_book_hash(self.book))

    def test_actual_batch_start_explicitly_initializes_only_its_migration_container(self):
        declaration = self.book["implementation_slots"][0]
        declaration["scope"] = ["docs/adrs/migrations"]
        declaration["migration_batch"] = {"role": "migration-batch",
            "path": "docs/adrs/migrations/implementation-pilot-001.yaml"}
        self.path.write_text(yaml.safe_dump(self.book, sort_keys=False))
        sup.commit_all(self.root, "synthetic explicit batch declaration")
        before = self.path.read_bytes()
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        run = yaml.safe_load(self.output.read_bytes())
        self.assertEqual(run["migration_bindings"], [])
        self.assertEqual(run["implementation_bindings"], [])
        sup.vp.validate_format_two_run(run, self.book)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(json.loads(result.stdout)["book_pointer_updated"])

    def test_original_format_one_start_preserves_shape_and_hash(self):
        legacy = sup.make_book('verify')
        legacy.update(status='active', created_at='2026-10-01', forked_from=None,
                      current_run=None, current_prompt=None)
        self.path.write_text(yaml.safe_dump(legacy, sort_keys=False))
        sup.commit_all(self.root, 'synthetic original format-one book')
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = yaml.safe_load(self.output.read_bytes())
        self.assertEqual(run['format_version'], '1')
        self.assertNotIn('implementation_bindings', run)
        self.assertNotIn('migration_bindings', run)
        self.assertEqual(run['book_content_hash'], sup.vp.compute_book_hash(legacy))

    def test_existing_output_preserved_and_malformed_book_refuses(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"owned elsewhere")
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b"owned elsewhere")
        self.output.unlink()
        self.book["cycle_kind"] = "unrecognized"
        self.path.write_text(yaml.safe_dump(self.book))
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())


def migration_authorizer():
    """Accepted synthetic constraints for the batch's authorizer and replacement."""
    return {"id": "ADR-0146", "status": "Accepted", "governs": [
        {"handle": "ADR-0146/authority-migration-is-reviewed-and-source-bound",
         "domain": "decision-migration", "rule": "Review exact source-bound migrations before application.",
         "scope": "migration", "provenance": "authored"},
        {"handle": "ADR-0146/rotation-preserves-assessment-outcomes",
         "domain": "decision-review", "rule": "Preserve assessments.",
         "scope": "Assessment", "provenance": "authored"}]}


class WriterFixture:
    """Actual start/advance writer; council records are explicitly synthetic."""
    def __init__(self, root, kind="implementation", combined=False, migration=False):
        from test_implementation_decisions import decision
        self.root = sup.init_repo(root)
        self.book = book_two(kind, combined=combined)
        if migration:
            declaration = self.book["implementation_slots"][0]
            declaration["scope"] = ["docs/adrs/migrations"]
            declaration["constraint_refs"] += ["ADR-0146/authority-migration-is-reviewed-and-source-bound"]
            declaration["migration_batch"] = {"role": "migration-batch",
                "path": "docs/adrs/migrations/implementation-pilot-001.yaml"}
        self.book.update(current_run=None, current_prompt=None)
        self.book_path = self.root / "docs/promptbooks/active/PB-0999-fixture.yaml"
        self.run_dir = self.root / "docs/promptbooks/runs/PB-0999-fixture"
        self.run_path = self.run_dir / "run-RUN-001.yaml"
        self.book_path.parent.mkdir(parents=True)
        self.book_path.write_text(yaml.safe_dump(self.book, sort_keys=False))
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        constraint = self.root / "docs/adrs/ADR-0001-constraint.md"
        constraint.parent.mkdir(parents=True)
        constraint.write_text('---\nid: ADR-0001\nstatus: Accepted\ngoverns:\n'
                              '  - handle: ADR-0001/fixture-rule\n    rule: Preserve output.\n---\n')
        if migration:
            authorizer = migration_authorizer()
            (constraint.parent / "ADR-0146-authorizer.md").write_text("---\n" + yaml.safe_dump(authorizer) + "---\n")
        (self.root / "widget.txt").write_text("before\n")
        sup.commit_all(self.root, "synthetic initial sources")
        started = subprocess.run([sys.executable, str(sup.SCRIPTS / "start-run.py"), str(self.book_path),
            "--run-id", "RUN-001", "--output", str(self.run_path)], capture_output=True, env=sup.scrubbed_env())
        if started.returncode: raise AssertionError(started.stdout + started.stderr)
        self.book.update(current_run="RUN-001", current_prompt=1)
        self.book_path.write_text(yaml.safe_dump(self.book, sort_keys=False))
        self.slot = self.book["implementation_slots"][0]["slot"]
        if migration:
            from test_implementation_migration import document
            chosen = document(); chosen["approval_slot"] = self.slot
            self.path = self.root / self.book["implementation_slots"][0]["migration_batch"]["path"]
        else:
            chosen = decision(); chosen["slot"] = self.slot
            self.path = self.run_dir / "implementations/RUN-001/strategy/revision-001.yaml"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(yaml.safe_dump(chosen, sort_keys=False))
        self.rel = self.path.relative_to(self.root).as_posix()
        self.council = self.run_dir / "council"; self.council.mkdir()
        self.retained = self.council / "subjects/revision.yaml"; self.retained.parent.mkdir()
        self.retained.write_bytes(self.path.read_bytes())
        # Use the existing record builder without invoking its state writer.
        env = object.__new__(sup.Env); env.root = self.root; env.hash = sup.vp.compute_book_hash(self.book)
        self.env = env
        self.receipt = self.council / "round.json"
        self.receipt.write_text(json.dumps(env.council_doc(module_tag=None if kind == "patch" else self.slot,
            prompt=1 if kind == "patch" else 6 if combined else 2,
            subjects=[{"path": self.rel, "sha256": ap.cr.sha256_file(self.path),
                       "retained_copy": self.retained.relative_to(self.root).as_posix()}])))
        self.artifacts = self.receipt.relative_to(self.root).as_posix()
        if combined:
            adr = self.council / "architectural-round.json"
            adr.write_text(json.dumps(env.council_doc(module_tag="adr-1", prompt=2,
                subjects=[{"path": constraint.relative_to(self.root).as_posix(),
                           "sha256": ap.cr.sha256_file(constraint), "retained_copy": None}])))
            self.artifacts += "," + adr.relative_to(self.root).as_posix()
        sup.commit_all(self.root, "synthetic retained council fixture after actual start")
        self.close = 1 if kind == "patch" else 8 if combined else 4
        # Execute actual earlier positions; no hand-authored close metadata.
        for _ in range(self.close - 1):
            result = self.advance()
            if result.returncode: raise AssertionError(result.stdout + result.stderr)
            sup.commit_all(self.root, "synthetic earlier actual advance")

    def advance(self, *extra):
        return subprocess.run([sys.executable, str(sup.SCRIPTS / "advance-run.py"), str(self.run_path),
            "--outcome", "done", "--artifacts", self.artifacts, "--book", str(self.book_path), *extra],
            capture_output=True, env=sup.scrubbed_env())

    def copy_to(self, root):
        """Copy a prepared fixture, including Git history, with independent mutable state."""
        root = Path(root).resolve()
        shutil.copytree(self.root, root)
        result = object.__new__(type(self))
        result.__dict__ = copy.deepcopy(self.__dict__)
        for name, value in vars(result).items():
            if isinstance(value, Path):
                setattr(result, name, root / value.relative_to(self.root))
        result.env.root = root
        return result

    def proof(self):
        return ap.validate_implementation_binding(self.root, self.run_path, slot=self.slot,
            revision_path=self.rel, revision_sha256=ap.cr.sha256_file(self.path))



class MigrationClosePreparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.f = WriterFixture(Path(self.temp.name) / "repo", migration=True)

    def snapshot(self):
        return {p.relative_to(self.f.root).as_posix(): p.read_bytes() for p in self.f.root.rglob("*")
                if p.is_file() and ".git" not in p.parts}

    def prepare(self, selected=None):
        gate = cg.classify(self.f.book, self.f.close)
        return ap.prepare_migration_close(self.f.root, self.f.run_path, gate,
            self.f.rel if selected is None else selected, self.f.artifacts.split(","))

    def test_exact_batch_prepare_reuses_full_gate_and_retains_read_only_context(self):
        before = self.snapshot(); binding, files = self.prepare()
        self.assertEqual(binding["batch"], {"path": self.f.rel, "sha256": ap.cr.sha256_file(self.f.path)})
        self.assertNotIn("revision", binding)
        self.assertEqual(binding["slot"], self.f.slot)
        self.assertEqual(len(files), 3)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(binding["retained_subject"]["sha256"], binding["batch"]["sha256"])
        with self.assertRaises(ap.Refused):
            ap.prepare_implementation_close(self.f.root, self.f.run_path,
                cg.classify(self.f.book, self.f.close), self.f.rel, self.f.artifacts.split(","))
        self.assertEqual(self.snapshot(), before)

    def test_batch_parse_and_digest_share_one_safe_descriptor_read(self):
        import implementation_migration as migrations
        from admission_source_io import SourceIO
        original_read = migrations._read; original_load = migrations._load_batch
        original_descriptor_read = SourceIO.read_bytes
        changed = yaml.safe_load(self.f.path.read_bytes())
        changed["batch_id"] = "implementation-pilot-002"
        active = []; loads = []; descriptor_buffers = []
        def load(repo, path):
            calls = []; active.append(calls)
            try:
                result = original_load(repo, path)
                loads.append((calls, result))
                return result
            finally:
                active.pop()
        def read(repo, path):
            path = Path(path); path = path if path.is_absolute() else Path(repo) / path
            if active and path == self.f.path:
                if active[-1]: self.f.path.write_text(yaml.safe_dump(changed))
                result = original_read(repo, path)
                active[-1].append(result[0].encode("utf-8"))
                return result
            return original_read(repo, path)
        def descriptor_read(source_io, path, **kwargs):
            result = original_descriptor_read(source_io, path, **kwargs)
            if Path(path) == self.f.path: descriptor_buffers.append(result)
            return result
        before = self.snapshot()
        with mock.patch.object(migrations, "_read", side_effect=read), \
             mock.patch.object(migrations, "_load_batch", side_effect=load), \
             mock.patch.object(SourceIO, "read_bytes", new=descriptor_read):
            binding, _ = self.prepare()
        self.assertGreaterEqual(len(loads), 2)  # Preparation and current eligibility independently recheck.
        self.assertTrue(descriptor_buffers)
        for calls, (parsed, raw) in loads:
            self.assertEqual(calls, [raw])  # One safe buffer supplies both parsing and the retained digest.
            self.assertEqual(parsed, yaml.safe_load(raw))
            self.assertIn(raw, descriptor_buffers)
            self.assertEqual(binding["batch"]["sha256"], ap.cr.sha256_bytes(raw))
        self.assertEqual(binding["batch"]["sha256"], ap.cr.sha256_file(self.f.path))
        self.assertEqual(self.snapshot(), before)

    def test_synthetic_published_context_uses_shared_historical_proof_not_id_alias(self):
        # Component fixture only. Actual advance-run publication is exercised separately.
        binding, files = self.prepare()
        for rel, content in files.items():
            path = self.f.root / rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        run = yaml.safe_load(self.f.run_path.read_bytes())
        run["migration_bindings"].append(binding)
        prompt = next(p for p in run["prompts"] if p["n"] == self.f.close)
        prompt.update(state="done", completed=sup.ts(3), artifacts=self.f.artifacts.split(","))
        self.f.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.f.root, "explicitly synthetic atomic close fixture")
        before = self.snapshot()
        proof = ap.validate_historical_migration_binding(self.f.root, self.f.run_path,
            slot=self.f.slot, batch_path=self.f.rel, batch_sha256=binding["batch"]["sha256"])
        self.assertEqual(proof.binding, binding)
        with self.assertRaises(ap.Refused):
            ap.validate_historical_implementation_binding(self.f.root, self.f.run_path, slot=self.f.slot,
                revision_path=self.f.rel, revision_sha256=binding["batch"]["sha256"])
        with self.assertRaises(TypeError):
            ap.validate_historical_migration_binding(self.f.root, self.f.run_path, slot=self.f.slot,
                batch_path=self.f.rel, batch_sha256=binding["batch"]["sha256"], registry={})
        self.assertEqual(self.snapshot(), before)

    def test_wrong_batch_path_slot_retained_subject_and_latest_failure_refuse_without_writes(self):
        original_batch = self.f.path.read_bytes(); original_receipt = self.f.receipt.read_bytes()
        for fault in ("path", "slot", "retained", "subject-duplicate", "failed"):
            with self.subTest(fault=fault):
                self.f.path.write_bytes(original_batch); self.f.receipt.write_bytes(original_receipt)
                self.f.retained.write_bytes(original_batch)
                selected = self.f.rel
                if fault == "path": selected = self.f.retained.relative_to(self.f.root).as_posix()
                if fault == "slot":
                    batch = yaml.safe_load(original_batch); batch["approval_slot"] = "implementation-2"
                    self.f.path.write_text(yaml.safe_dump(batch))
                if fault == "retained": self.f.retained.write_bytes(b"changed retained batch")
                if fault in ("subject-duplicate", "failed"):
                    receipt = json.loads(original_receipt)
                    if fault == "subject-duplicate": receipt["subjects"] *= 2
                    else:
                        receipt = self.f.env.council_doc(module_tag=self.f.slot, prompt=2,
                            round=2, decisions=("REJECT", "REJECT", "REJECT"),
                            subjects=receipt["subjects"])
                    self.f.receipt.write_text(json.dumps(receipt))
                if sup.git(self.f.root, "status", "--porcelain").strip():
                    sup.commit_all(self.f.root, "explicitly synthetic damaged batch input")
                before = self.snapshot()
                with self.assertRaises(ap.Refused): self.prepare(selected)
                self.assertEqual(self.snapshot(), before)
                self.f.retained.write_bytes(original_batch)

    def test_batch_raw_direct_and_dotdot_symlinks_refuse_before_selected_read(self):
        alias = self.f.path.parent / "alias.yaml"; alias.symlink_to(self.f.path)
        deep = self.f.path.parent / "deep"; deep.mkdir()
        link = self.f.path.parent / "link"; link.symlink_to(deep, target_is_directory=True)
        for raw in (alias, link / ".." / self.f.path.name):
            before = self.snapshot()
            with self.assertRaises(ap.Refused): self.prepare(str(raw))
            self.assertEqual(self.snapshot(), before)

class MigrationWriter(unittest.TestCase):
    """Production close controls; all council signatures and repositories are synthetic."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.f = WriterFixture(Path(self.temp.name) / "repo", migration=True)

    def snapshot(self):
        return {p.relative_to(self.f.root).as_posix(): p.read_bytes() for p in self.f.root.rglob("*")
                if p.is_file() and ".git" not in p.parts}

    def proof(self):
        return ap.validate_historical_migration_binding(self.f.root, self.f.run_path, slot=self.f.slot,
            batch_path=self.f.rel, batch_sha256=ap.cr.sha256_file(self.f.path))

    def close(self):
        result = self.f.advance("--migration-batch", self.f.rel)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_actual_close_atomically_binds_batch_and_done_then_requires_commit(self):
        self.close()
        run = yaml.safe_load(self.f.run_path.read_bytes())
        self.assertEqual(run["implementation_bindings"], [])
        self.assertEqual(len(run["migration_bindings"]), 1)
        self.assertNotIn("revision", run["migration_bindings"][0])
        self.assertEqual(run["prompts"][self.f.close - 1]["state"], "done")
        with self.assertRaises(ap.Refused): self.proof()
        sup.commit_all(self.f.root, "synthetic actual batch close publication")
        self.assertEqual(self.proof().binding, run["migration_bindings"][0])
        result = self.f.advance()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_competing_cross_role_wrong_path_and_raw_selectors_preserve_bytes(self):
        link = self.f.root / "batch-link"; link.symlink_to(self.f.path.parent, target_is_directory=True)
        for args in ((), ("--implementation-revision", self.f.rel),
            ("--migration-batch", self.f.rel, "--implementation-revision", self.f.rel),
            ("--migration-batch", self.f.retained.relative_to(self.f.root).as_posix()),
            ("--migration-batch", "batch-link/" + self.f.path.name),
            ("--migration-batch", "batch-link/../" + self.f.rel)):
            before = self.snapshot(); result = self.f.advance(*args)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.snapshot(), before)

    def test_forged_done_or_deleted_binding_and_context_cannot_advance(self):
        for fault in ("forged-done", "binding", "context"):
            with self.subTest(fault=fault):
                self.f = WriterFixture(Path(self.temp.name) / fault, migration=True)
                if fault != "forged-done":
                    self.close(); sup.commit_all(self.f.root, "synthetic actual committed close")
                run = yaml.safe_load(self.f.run_path.read_bytes())
                if fault == "forged-done":
                    run["prompts"][self.f.close - 1].update(state="done", completed=sup.ts(3))
                    run["prompts"][self.f.close].update(state="running", started=sup.ts(3))
                    run["current_prompt"] = self.f.close + 1
                if fault == "binding": run["migration_bindings"] = []
                if fault == "context": (self.f.root / run["migration_bindings"][0]["context"]["path"]).unlink()
                self.f.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
                sup.commit_all(self.f.root, "synthetic damaged prior batch close")
                before = self.snapshot(); result = self.f.advance()
                self.assertNotEqual(result.returncode, 0); self.assertEqual(self.snapshot(), before)

    def test_actual_close_uses_latest_retained_batch_and_full_holds_exception_history(self):
        for fault in ("later-other-subject", "later-failed", "duplicate", "retained", "changed-batch",
                      "spent-bound", "wrong-exception", "valid-exception"):
            with self.subTest(fault=fault):
                self.f = WriterFixture(Path(self.temp.name) / fault, migration=True)
                receipt = json.loads(self.f.receipt.read_bytes())
                if fault.startswith("later-"):
                    later = self.f.env.council_doc(module_tag=self.f.slot, prompt=2, round=2,
                        decisions=("REJECT",) * 3 if fault == "later-failed" else ("APPROVE",) * 3,
                        subjects=receipt["subjects"] if fault == "later-failed" else [{"path": "widget.txt",
                            "sha256": ap.cr.sha256_file(self.f.root / "widget.txt"), "retained_copy": None}])
                    path = self.f.council / "later.json"; path.write_text(json.dumps(later))
                    self.f.artifacts += "," + path.relative_to(self.f.root).as_posix()
                if fault == "duplicate":
                    receipt["subjects"] *= 2; self.f.receipt.write_text(json.dumps(receipt))
                if fault == "retained": self.f.retained.write_bytes(b"changed retained batch")
                if fault == "changed-batch":
                    batch = yaml.safe_load(self.f.path.read_bytes()); batch["entries"][0]["rationale"] += " changed"
                    self.f.path.write_text(yaml.safe_dump(batch))
                if fault in ("spent-bound", "wrong-exception", "valid-exception"):
                    BoundHistoryWriters.history(self, self.f, 4,
                        exception=4 if fault == "valid-exception" else 5 if fault == "wrong-exception" else None)
                if sup.git(self.f.root, "status", "--porcelain").strip():
                    sup.commit_all(self.f.root, "synthetic batch gate negative input")
                before = self.snapshot(); result = self.f.advance("--migration-batch", self.f.rel)
                if fault == "valid-exception":
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    sup.commit_all(self.f.root, "synthetic actual exception-authorized batch close")
                    self.assertEqual(self.proof().binding["gate_prompt"], self.f.close)
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.snapshot(), before)

    def test_lifecycle_change_preserves_historical_batch_proof_but_refuses_new_writer(self):
        self.close(); sup.commit_all(self.f.root, "synthetic actual batch close")
        before_proof = self.proof()
        authorizer = self.f.root / "docs/adrs/ADR-0146-authorizer.md"
        authorizer.write_bytes(authorizer.read_bytes().replace(b"status: Accepted", b"status: Superseded"))
        sup.commit_all(self.f.root, "synthetic manual host lifecycle fixture")
        self.assertEqual(self.proof().binding, before_proof.binding)
        before = self.snapshot(); result = self.f.advance()
        self.assertNotEqual(result.returncode, 0); self.assertEqual(self.snapshot(), before)


class PriorCloseEntryContracts(unittest.TestCase):
    """Ordinary source-contract unit tests, not dynamic security diagnostics."""
    def test_binding_only_entry_is_checked_even_when_declared_close_is_not_done(self):
        from test_advance_run_gate import _ar
        from test_implementation_cycle_contracts import binding
        book = book_two(); run = {"implementation_bindings": [binding()],
            "prompts": [{"n": n, "state": "running" if n == 4 else "pending"} for n in range(1, 14)]}
        with mock.patch.object(ap, "validate_implementation_binding", side_effect=ap.Refused("close-not-done")) as proof:
            with self.assertRaises(SystemExit):
                _ar._prior_implementation_closes_or_fail(run, book, Path("unused-run.yaml"))
        proof.assert_called_once()

    def test_each_entry_requires_its_exact_returned_proof_and_distinct_slots_are_visited(self):
        from test_advance_run_gate import _ar
        from test_implementation_cycle_contracts import binding
        first = binding(); other = copy.deepcopy(first); other.update(slot="implementation-2", gate_prompt=8)
        book = {"implementation_slots": [{"slot": "implementation-1"}, {"slot": "implementation-2"}],
                "prompts": [{"n": n, "module_tag": "implementation-1" if n <= 4 else "implementation-2"}
                            for n in range(1, 9)]}
        run = {"implementation_bindings": [first, other], "prompts": [{"n": 4, "state": "done"},
                                                                      {"n": 8, "state": "done"}]}
        with mock.patch.object(ap.cr, "repo_root", return_value=Path.cwd()), \
             mock.patch.object(ap, "validate_implementation_binding", side_effect=[
                 mock.Mock(binding=first), mock.Mock(binding=other)]) as proof:
            _ar._prior_implementation_closes_or_fail(run, book, Path("unused-run.yaml"))
            self.assertEqual(proof.call_count, 2)
        with mock.patch.object(ap.cr, "repo_root", return_value=Path.cwd()), \
             mock.patch.object(ap, "validate_implementation_binding", return_value=mock.Mock(binding=other)):
            with self.assertRaises(SystemExit):
                _ar._prior_implementation_closes_or_fail(run, book, Path("unused-run.yaml"))


class WriterControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.f = WriterFixture(Path(self.temp.name) / "repo")

    def test_actual_close_then_superseded_constraint_preserves_history_and_source_but_refuses_new_result(self):
        import implementation_decisions as ids
        close = self.f.advance("--implementation-revision", self.f.rel)
        self.assertEqual(close.returncode, 0, close.stdout + close.stderr)
        sup.commit_all(self.f.root, "synthetic fixture with actual successful close")
        binding = yaml.safe_load(self.f.run_path.read_bytes())["implementation_bindings"][0]
        preimage = sup.git(self.f.root, "rev-parse", "HEAD").strip()
        (self.f.root / "widget.txt").write_text("after\n")
        sup.commit_all(self.f.root, "synthetic delivered source")
        delivered = sup.git(self.f.root, "rev-parse", "HEAD").strip()
        report_path = self.f.run_dir / "reviews/synthetic-independent.json"
        report_path.parent.mkdir()
        def ref(path):
            return {"path": path.relative_to(self.f.root).as_posix(), "sha256": ap.cr.sha256_file(path)}
        report = sup.fixture("reviewer-report-paths.json")
        report.update(book={"id": binding["book_id"], "content_hash": binding["book_content_hash"]},
                      run_id=binding["run_id"], subject={"form": "paths",
                      "paths": [ref(self.f.path), ref(self.f.root / "widget.txt")]})
        report_path.write_text(json.dumps(report))
        sup.commit_all(self.f.root, "explicitly synthetic delivery review fixture")
        result = {"record_type": "implementation-result", "format_version": "1", "decision": ref(self.f.path),
                  "delivery_state": "complete", "source_revision": delivered, "preimage_revision": preimage,
                  "scope": ["widget.txt"], "sources": [{"path": "widget.txt",
                  "preimage": ids.source_hash(self.f.root, preimage, "widget.txt"),
                  "delivered": ids.source_hash(self.f.root, delivered, "widget.txt")}],
                  "reviews": [ref(report_path)], "annotations": []}
        ids.write_result(self.f.root, self.f.path, result)
        sup.commit_all(self.f.root, "synthetic source-bound result through actual result writer")
        accepted = ids.query(self.f.root, self.f.path)
        self.assertTrue(accepted["reviewed_intent"]["approved"])
        self.assertEqual(accepted["current_state"]["state"], "delivered")
        receipt_sha = ap.cr.sha256_file(self.f.receipt); review_sha = ap.cr.sha256_file(report_path)
        constraint = self.f.root / "docs/adrs/ADR-0001-constraint.md"
        constraint.write_text(constraint.read_text().replace("status: Accepted", "status: Superseded"))
        sup.commit_all(self.f.root, "synthetic Accepted to Superseded fixture transition")
        answer = ids.query(self.f.root, self.f.path)
        self.assertTrue(answer["reviewed_intent"]["approved"])
        self.assertEqual(answer["current_state"]["state"], "delivered")
        self.assertFalse(answer["current_eligibility"]["eligible"])
        self.assertEqual(answer["current_eligibility"]["limit"], "constraint-not-live-accepted")
        self.assertEqual(ap.cr.sha256_file(self.f.receipt), receipt_sha)
        self.assertEqual(ap.cr.sha256_file(report_path), review_sha)
        self.assertEqual(yaml.safe_load(self.f.run_path.read_bytes())["implementation_bindings"][0], binding)
        before = {p.relative_to(self.f.root).as_posix(): p.read_bytes() for p in self.f.root.rglob("*")
                  if p.is_file() and ".git" not in p.parts}
        with self.assertRaisesRegex(ap.Refused, "constraint-not-live-accepted"):
            ids.write_result(self.f.root, self.f.path, result)
        after = {p.relative_to(self.f.root).as_posix(): p.read_bytes() for p in self.f.root.rglob("*")
                 if p.is_file() and ".git" not in p.parts}
        self.assertEqual(after, before)

    def test_actual_close_writes_binding_and_done_together_then_commit_is_required(self):
        result = self.f.advance("--implementation-revision", self.f.rel)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = yaml.safe_load(self.f.run_path.read_bytes())
        self.assertEqual(run["prompts"][3]["state"], "done")
        self.assertEqual(len(run["implementation_bindings"]), 1)
        context = json.loads((self.f.root / run["implementation_bindings"][0]["context"]["path"]).read_bytes())
        self.assertEqual(context["contract"]["version"], "4")
        self.assertEqual(context["format_version"], "3")
        with self.assertRaises(ap.Refused): self.f.proof()
        sup.commit_all(self.f.root, "synthetic actual successful close")
        self.assertEqual(self.f.proof().binding["gate_prompt"], 4)

    def test_historical_reader_refuses_ambiguous_ordinary_close_after_owner_admission(self):
        self.assertEqual(self.f.advance("--implementation-revision", self.f.rel).returncode, 0)
        sup.commit_all(self.f.root, "synthetic canonical ordinary close")
        canonical = self.f.proof()
        admitted = copy.deepcopy(canonical.run)
        admitted["implementation_bindings"].append(copy.deepcopy(canonical.binding))
        before = {p.relative_to(self.f.root).as_posix(): p.read_bytes() for p in self.f.root.rglob("*")
                  if p.is_file() and ".git" not in p.parts}
        # Isolate exact-entry selection from cross-document admission; no source/evidence mutation.
        with mock.patch.object(ap, "_owner", return_value=(admitted, canonical.book,
                                  self.f.run_path.relative_to(self.f.root).as_posix())):
            with self.assertRaises(ap.Refused): self.f.proof()
        after = {p.relative_to(self.f.root).as_posix(): p.read_bytes() for p in self.f.root.rglob("*")
                 if p.is_file() and ".git" not in p.parts}
        self.assertEqual(after, before)

    def test_selected_revision_required_and_changed_reasoning_refuses_without_writes(self):
        before = self.f.run_path.read_bytes(); book_before = self.f.book_path.read_bytes()
        missing = self.f.advance()
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(self.f.run_path.read_bytes(), before)
        self.assertEqual(self.f.book_path.read_bytes(), book_before)
        chosen = yaml.safe_load(self.f.path.read_bytes()); chosen["reasoning"] = "Different reasoning."
        self.f.path.write_text(yaml.safe_dump(chosen, sort_keys=False))
        sup.commit_all(self.f.root, "synthetic material subject change")
        changed = self.f.advance("--implementation-revision", self.f.rel)
        self.assertNotEqual(changed.returncode, 0)
        self.assertEqual(self.f.run_path.read_bytes(), before)
        self.assertEqual(self.f.book_path.read_bytes(), book_before)


class MoreWriterControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.f = WriterFixture(Path(self.temp.name) / "repo")

    def local_advance(self):
        from test_advance_run_gate import _ar
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                code = _ar.main([str(self.f.run_path), "--outcome", "done", "--artifacts", self.f.artifacts,
                    "--book", str(self.f.book_path), "--implementation-revision", self.f.rel])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_truncated_run_cannot_omit_development_and_independent_review(self):
        run = yaml.safe_load(self.f.run_path.read_bytes())
        run["prompts"] = run["prompts"][:4]
        self.f.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.f.root, "synthetic truncated run")
        before = self.f.run_path.read_bytes()
        result = self.f.advance("--implementation-revision", self.f.rel)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.f.run_path.read_bytes(), before)

    def test_unowned_temp_collision_and_replace_failure_preserve_all_snapshots(self):
        from test_advance_run_gate import _ar
        before = self.f.run_path.read_bytes(); book_before = self.f.book_path.read_bytes()
        temp = self.f.run_path.with_name(self.f.run_path.name + ".advance-tmp")
        temp.write_bytes(b"other owner")
        code, output = self.local_advance()
        self.assertEqual(code, 1, output)
        self.assertEqual(temp.read_bytes(), b"other owner")
        self.assertEqual(self.f.run_path.read_bytes(), before)
        self.assertEqual(self.f.book_path.read_bytes(), book_before)
        temp.unlink()
        target = self.f.root / "widget.txt"
        target_before = target.read_bytes()
        temp.symlink_to(target)
        code, output = self.local_advance()
        self.assertEqual(code, 1, output)
        self.assertTrue(temp.is_symlink())
        self.assertEqual(target.read_bytes(), target_before)
        self.assertEqual(self.f.run_path.read_bytes(), before)
        self.assertEqual(self.f.book_path.read_bytes(), book_before)
        temp.unlink()
        with mock.patch.object(_ar.os, "replace", side_effect=OSError("synthetic replace failure")):
            code, output = self.local_advance()
        self.assertEqual(code, 1, output)
        self.assertFalse(temp.exists())
        self.assertEqual(self.f.run_path.read_bytes(), before)
        self.assertEqual(self.f.book_path.read_bytes(), book_before)
        self.assertEqual(list((self.f.run_dir / "gate-contexts").rglob("*.json")), [])

    def test_successful_replace_preserves_a_recreated_temporary_owned_by_another_writer(self):
        from test_advance_run_gate import _ar
        temporary = self.f.run_path.with_name(self.f.run_path.name + ".advance-tmp")
        other_bytes = b"temporary owned by another writer"
        replace = _ar.os.replace

        def replace_then_recreate(source, target):
            replace(source, target)
            Path(source).write_bytes(other_bytes)

        with mock.patch.object(_ar.os, "replace", side_effect=replace_then_recreate):
            code, output = self.local_advance()
        self.assertEqual(code, 0, output)
        run = yaml.safe_load(self.f.run_path.read_bytes())
        self.assertEqual(run["prompts"][3]["state"], "done")
        self.assertEqual(len(run["implementation_bindings"]), 1)
        self.assertTrue(temporary.exists(), "another writer's temporary was deleted")
        self.assertEqual(temporary.read_bytes(), other_bytes)

    def test_raw_selected_revision_symlinks_refuse_before_decision_read(self):
        import implementation_decisions as ids
        for mode in ("direct", "dotdot"):
            with self.subTest(mode=mode):
                self.f = WriterFixture(Path(self.temp.name) / mode)
                canonical = self.f.rel
                if mode == "direct":
                    link = self.f.run_dir / "revision-link.yaml"
                    link.symlink_to(self.f.path)
                    self.f.rel = link.relative_to(self.f.root).as_posix()
                else:
                    target = self.f.root / "other/deeper"
                    target.mkdir(parents=True)
                    (self.f.root / "selector-link").symlink_to(target, target_is_directory=True)
                    self.f.rel = "selector-link/../" + canonical
                    self.assertFalse((self.f.root / self.f.rel).is_file())
                before = self.f.run_path.read_bytes()
                book_before = self.f.book_path.read_bytes()
                with mock.patch.object(ids, "_decision", wraps=ids._decision) as selected:
                    code, output = self.local_advance()
                selected.assert_not_called()
                self.assertEqual(code, 1, output)
                self.assertIn("selected-revision-symlink-refused", output)
                self.assertEqual(self.f.run_path.read_bytes(), before)
                self.assertEqual(self.f.book_path.read_bytes(), book_before)
                self.assertEqual(list((self.f.run_dir / "gate-contexts").rglob("*.json")), [])

    def test_preclose_registry_rotation_refuses_postclose_rotation_preserves_proof(self):
        reg = sup.registry(); key = reg["model_roles"]["openai_top"]
        reg["models"][key]["accepted_served_models"] = ["different"]
        rotated = self.f.root / "rotated.json"; rotated.write_text(json.dumps(reg))
        before = self.f.run_path.read_bytes()
        with mock.patch.object(cg, "REGISTRY_PATH", rotated):
            code, output = self.local_advance()
        self.assertEqual(code, 1, output)
        self.assertEqual(self.f.run_path.read_bytes(), before)
        passed = self.f.advance("--implementation-revision", self.f.rel)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        sup.commit_all(self.f.root, "synthetic issued close")
        with mock.patch.object(cg, "REGISTRY_PATH", rotated):
            self.assertEqual(self.f.proof().binding["gate_prompt"], 4)

    def test_held_then_converged_and_architectural_stop_refuse(self):
        before = self.f.run_path.read_bytes()
        original = json.loads(self.f.receipt.read_bytes())
        held = copy.deepcopy(original); held["seats"][0]["decision"] = "DEFER_TO_HUMAN"
        self.f.receipt.write_text(json.dumps(held))
        later = copy.deepcopy(original); later.update(round=2, written_at=sup.ts(2))
        second = self.f.council / "later.json"; second.write_text(json.dumps(later))
        self.f.artifacts += "," + second.relative_to(self.f.root).as_posix()
        sup.commit_all(self.f.root, "synthetic held then converged")
        result = self.f.advance("--implementation-revision", self.f.rel)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(b"stop", result.stdout)
        self.assertEqual(self.f.run_path.read_bytes(), before)
        held["seats"][0]["decision"] = "ARCHITECTURAL"
        self.f.receipt.write_text(json.dumps(held))
        sup.commit_all(self.f.root, "synthetic architecture conflict")
        result = self.f.advance("--implementation-revision", self.f.rel)
        self.assertEqual(result.returncode, 1)
        self.assertIn(4, json.loads(result.stdout)["gate"]["stops"])
        self.assertEqual(self.f.run_path.read_bytes(), before)

    def test_forged_done_or_missing_context_cannot_advance(self):
        initial = self.f.run_path.read_bytes()
        run = yaml.safe_load(initial); run["prompts"][3].update(state="done", completed="fake")
        self.f.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        before = self.f.run_path.read_bytes()
        failed = self.f.advance("--implementation-revision", self.f.rel)
        self.assertEqual(failed.returncode, 1)
        self.assertIn(b"no successful close binding", failed.stdout)
        self.assertEqual(self.f.run_path.read_bytes(), before)
        self.f.run_path.write_bytes(initial)
        passed = self.f.advance("--implementation-revision", self.f.rel)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        sup.commit_all(self.f.root, "synthetic successful committed close")
        run = yaml.safe_load(self.f.run_path.read_bytes())
        (self.f.root / run["implementation_bindings"][0]["context"]["path"]).unlink()
        before = self.f.run_path.read_bytes()
        failed = self.f.advance()
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(self.f.run_path.read_bytes(), before)


class OtherCycleWriters(unittest.TestCase):
    def test_patch_verify_combined_adr_actual_writer_close(self):
        for kind, combined in (("patch", False), ("verify", False), ("adr", True)):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                fixture = WriterFixture(Path(temp) / "repo", kind, combined)
                result = fixture.advance("--implementation-revision", fixture.rel)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                sup.commit_all(fixture.root, "synthetic actual other-kind close")
                self.assertEqual(fixture.proof().binding["gate_prompt"], fixture.close)

class BoundHistoryWriters(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        return WriterFixture(Path(temp.name) / 'repo')

    def history(self, f, count, *, converge=True, exception=None, committed=True):
        original = json.loads(f.receipt.read_bytes())
        for number in range(1, count):
            earlier = copy.deepcopy(original)
            earlier.update(round=number, written_at=sup.ts(number))
            earlier['seats'][0]['decision'] = 'REQUEST_CHANGES'
            earlier['seats'][0]['findings'] = [{'id': 'openai_top:F1', 'dimension': 'Correctness',
                'safety_adjacent': False, 'kind': 'blocking', 'text': 'synthetic blocker'}]
            path = f.council / f'earlier-{number}.json'
            path.write_text(json.dumps(earlier))
            sup.commit_all(f.root, f'synthetic earlier council round {number}')
            f.artifacts += ',' + path.relative_to(f.root).as_posix()
        original.update(round=count, written_at=sup.ts(count))
        if not converge:
            original['seats'][0]['decision'] = 'REQUEST_CHANGES'
            original['seats'][0]['findings'] = earlier['seats'][0]['findings']
        f.receipt.write_text(json.dumps(original))
        sup.commit_all(f.root, 'synthetic counted council history',
                       reintroduce=(f.receipt.relative_to(f.root).as_posix(),))
        if exception is not None:
            owner = f.council / 'owner.json'
            owner.write_text(json.dumps(f.env.owner_doc(kind='round-above-three',
                round=exception, module_tag=f.slot)))
            f.artifacts += ',' + owner.relative_to(f.root).as_posix()
            if committed: sup.commit_all(f.root, 'synthetic owner authorization fixture')

    def test_third_counted_implementation_council_needs_no_exception(self):
        f = self.fixture(); self.history(f, 3)
        result = f.advance('--implementation-revision', f.rel)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sup.commit_all(f.root, 'synthetic actual third-place close')
        self.assertEqual(f.proof().binding['deciding_record']['path'], f.receipt.relative_to(f.root).as_posix())

    def test_spent_bound_only_lawful_committed_converged_exception_recovers(self):
        for exception, committed, converge, passes in ((None, True, True, False),
                (5, True, True, False), (4, False, True, False),
                (4, True, False, False), (4, True, True, True)):
            with self.subTest(exception=exception, committed=committed, converge=converge):
                f = self.fixture(); self.history(f, 4, exception=exception,
                                               committed=committed, converge=converge)
                before = f.run_path.read_bytes(); book_before = f.book_path.read_bytes()
                result = f.advance('--implementation-revision', f.rel)
                self.assertEqual(result.returncode, 0 if passes else 1, result.stdout + result.stderr)
                if passes:
                    sup.commit_all(f.root, 'synthetic actual exception-authorized close')
                    self.assertEqual(f.proof().binding['gate_prompt'], 4)
                else:
                    self.assertEqual(f.run_path.read_bytes(), before)
                    self.assertEqual(f.book_path.read_bytes(), book_before)

    def test_latest_deciding_revision_mandatory_retention_and_receipt_attachment(self):
        for fault in ('earlier-only', 'retained-missing', 'unattached', 'foreign-run', 'wrong-scope'):
            with self.subTest(fault=fault):
                f = self.fixture()
                receipt = json.loads(f.receipt.read_bytes())
                if fault == 'earlier-only':
                    later = copy.deepcopy(receipt); later.update(round=2, written_at=sup.ts(2))
                    later['subjects'] = [{'path': 'widget.txt', 'sha256': ap.cr.sha256_file(f.root / 'widget.txt'),
                                          'retained_copy': None}]
                    path = f.council / 'later.json'; path.write_text(json.dumps(later))
                    f.artifacts += ',' + path.relative_to(f.root).as_posix()
                elif fault == 'retained-missing':
                    receipt['subjects'][0]['retained_copy'] = None
                    f.receipt.write_text(json.dumps(receipt))
                elif fault == 'unattached': f.artifacts = ''
                else:
                    chosen = yaml.safe_load(f.path.read_bytes())
                    chosen['run_id' if fault == 'foreign-run' else 'scope'] = 'RUN-002' if fault == 'foreign-run' else ['other.txt']
                    f.path.write_text(yaml.safe_dump(chosen))
                if fault != 'unattached':
                    sup.commit_all(f.root, 'synthetic invalid bound evidence')
                before = f.run_path.read_bytes(); book_before = f.book_path.read_bytes()
                result = f.advance('--implementation-revision', f.rel)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(f.run_path.read_bytes(), before)
                self.assertEqual(f.book_path.read_bytes(), book_before)

    def test_actual_writer_proof_does_not_infer_current_state_from_approval(self):
        import implementation_decisions as ids
        f = self.fixture()
        result = f.advance('--implementation-revision', f.rel)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sup.commit_all(f.root, 'synthetic actual committed writer proof')
        answer = ids.query(f.root, f.path)
        self.assertTrue(answer['reviewed_intent']['approved'])
        self.assertEqual(answer['current_state']['state'], 'UNOBSERVED')
        self.assertEqual(answer['authority'], 'none')
        preimage = sup.git(f.root, 'rev-parse', 'HEAD').strip()
        (f.root / 'widget.txt').write_text('after\n')
        sup.commit_all(f.root, 'synthetic delivered source')
        delivered = sup.git(f.root, 'rev-parse', 'HEAD').strip()
        reference = lambda path: {'path': path.relative_to(f.root).as_posix(), 'sha256': ap.cr.sha256_file(path)}
        report_path = f.run_dir / 'reviews/independent.json'; report_path.parent.mkdir()
        report = sup.fixture('reviewer-report-paths.json')
        report.update(book={'id': f.book['id'], 'content_hash': sup.vp.compute_book_hash(f.book)},
                      run_id='RUN-001', subject={'form': 'paths',
                      'paths': [reference(f.path), reference(f.root / 'widget.txt')]})
        report_path.write_text(json.dumps(report))
        sup.commit_all(f.root, 'synthetic independent delivery report, no real reviewer')
        result = {'record_type': 'implementation-result', 'format_version': '1',
                  'decision': reference(f.path), 'delivery_state': 'complete',
                  'source_revision': delivered, 'preimage_revision': preimage,
                  'scope': ['widget.txt'], 'sources': [{'path': 'widget.txt',
                    'preimage': ids.source_hash(f.root, preimage, 'widget.txt'),
                    'delivered': ids.source_hash(f.root, delivered, 'widget.txt')}],
                  'reviews': [reference(report_path)], 'annotations': []}
        self.assertTrue(ids.write_result(f.root, f.path, result).is_file())
        sup.commit_all(f.root, 'synthetic source-bound result after actual writer proof')
        self.assertEqual(ids.query(f.root, f.path)['current_state']['state'], 'delivered')
        (f.root / 'widget.txt').write_text('before\n')
        sup.commit_all(f.root, 'synthetic source reversion')
        self.assertEqual(ids.query(f.root, f.path)['current_state']['state'], 'reverted')

    def test_pointer_failure_reports_published_run_without_duplicate_binding(self):
        from test_advance_run_gate import _ar
        import io
        from contextlib import redirect_stdout
        f = self.fixture(); original = Path.write_text
        def refused_book(path, *args, **kwargs):
            if path == f.book_path: raise OSError('synthetic book pointer failure')
            return original(path, *args, **kwargs)
        output = io.StringIO()
        with mock.patch.object(Path, 'write_text', refused_book), redirect_stdout(output):
            with self.assertRaises(SystemExit) as caught:
                _ar.main([str(f.run_path), '--outcome', 'done', '--artifacts', f.artifacts,
                          '--book', str(f.book_path), '--implementation-revision', f.rel])
        self.assertEqual(caught.exception.code, 1)
        result = json.loads(output.getvalue())
        self.assertTrue(result['run_written']); self.assertFalse(result['book_written'])
        run = yaml.safe_load(f.run_path.read_bytes())
        self.assertEqual(run['prompts'][3]['state'], 'done')
        self.assertEqual(len(run['implementation_bindings']), 1)
        f.book['current_prompt'] = run['current_prompt']
        f.book_path.write_text(yaml.safe_dump(f.book, sort_keys=False))
        sup.commit_all(f.root, 'synthetic reconcile published pointer')
        self.assertEqual(f.proof().binding['gate_prompt'], 4)

class UnavailableStartControls(unittest.TestCase):
    # Only the start-history boundary is unavailable, never approval.
    fixture = BoundHistoryWriters.fixture
    def test_structure_and_history_refuse_with_known_and_unknown_start(self):
        from test_advance_run_gate import _ar
        import io
        from contextlib import redirect_stdout
        for unknown in (False, True):
            for fault in ('missing', 'hash', 'prefix', 'held', 'unattached', 'refutation'):
                with self.subTest(unknown=unknown, fault=fault):
                    f = self.fixture()
                    if fault == 'missing': f.receipt.unlink()
                    elif fault in ('hash', 'prefix'):
                        book = yaml.safe_load(f.book_path.read_bytes())
                        if fault == 'hash': book['implementation_slots'][0]['scope'] = ['other.txt']
                        else: book['prompts'][0]['module_tag'] = 'unknown-1'
                        f.book_path.write_text(yaml.safe_dump(book, sort_keys=False))
                    elif fault == 'held':
                        receipt = json.loads(f.receipt.read_bytes())
                        receipt['seats'][0]['decision'] = 'DEFER_TO_HUMAN'
                        f.receipt.write_text(json.dumps(receipt))
                    elif fault == 'unattached': f.artifacts = ''
                    else:
                        refutation = f.env.refutation_doc(f.receipt, written_at=sup.ts(2), module_tag=f.slot)
                        path = f.council / 'refutation.json'; path.write_text(json.dumps(refutation))
                        f.artifacts += ',' + path.relative_to(f.root).as_posix()
                    if fault != 'unattached': sup.commit_all(f.root, 'synthetic refusal control')
                    before = f.run_path.read_bytes(); book_before = f.book_path.read_bytes()
                    output = io.StringIO()
                    start = mock.patch.object(cg, 'start_fields', return_value=cg.StartFields(False)) if unknown else __import__('contextlib').nullcontext()
                    with start, redirect_stdout(output):
                        with self.assertRaises(SystemExit) as caught:
                            _ar.main([str(f.run_path), '--outcome', 'done', '--artifacts', f.artifacts,
                                      '--book', str(f.book_path), '--implementation-revision', f.rel])
                    self.assertEqual(caught.exception.code, 1, output.getvalue())
                    self.assertEqual(f.run_path.read_bytes(), before)
                    self.assertEqual(f.book_path.read_bytes(), book_before)

class IssuedProofControls(unittest.TestCase):
    fixture = BoundHistoryWriters.fixture

    def test_issued_history_uses_profile_scope_instead_of_live_routing_alias(self):
        f = self.fixture()
        result = f.advance('--implementation-revision', f.rel)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sup.commit_all(f.root, 'synthetic actual profile proof')
        with mock.patch.object(cg, '_scope', side_effect=AssertionError('live routing consulted')):
            self.assertEqual(f.proof().binding['gate_prompt'], 4)

    def test_previous_binding_immutable_and_completed_close_cannot_reopen(self):
        f = self.fixture()
        result = f.advance('--implementation-revision', f.rel)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sup.commit_all(f.root, 'synthetic actual issued proof')
        binding = copy.deepcopy(yaml.safe_load(f.run_path.read_bytes())['implementation_bindings'][0])
        result = f.advance()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(yaml.safe_load(f.run_path.read_bytes())['implementation_bindings'], [binding])
        sup.commit_all(f.root, 'synthetic actual next prompt')
        self.assertEqual(f.proof().binding, binding)
        run = yaml.safe_load(f.run_path.read_bytes()); run['current_prompt'] = 4
        f.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        f.book['current_prompt'] = 4; f.book_path.write_text(yaml.safe_dump(f.book, sort_keys=False))
        sup.commit_all(f.root, 'synthetic forged re-open pointer')
        before = f.run_path.read_bytes()
        result = f.advance('--implementation-revision', f.rel)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(f.run_path.read_bytes(), before)

    def test_actual_binding_requires_supported_retained_profile_and_exact_contract(self):
        for fault in ('version', 'contract'):
            with self.subTest(fault=fault):
                f = self.fixture()
                result = f.advance('--implementation-revision', f.rel)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                sup.commit_all(f.root, 'synthetic actual issued proof')
                run = yaml.safe_load(f.run_path.read_bytes()); binding = run['implementation_bindings'][0]
                context_path = f.root / binding['context']['path']
                context = json.loads(context_path.read_bytes())
                if fault == 'version': context['contract']['version'] = 'unsupported'
                else:
                    contract = f.root / context['contract']['path']; contract.write_bytes(b'unsupported policy')
                    context['contract']['sha256'] = ap.cr.sha256_file(contract)
                context_path.write_text(json.dumps(context))
                binding['context']['sha256'] = ap.cr.sha256_file(context_path)
                f.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
                sup.commit_all(f.root, 'synthetic corrupted retained proof')
                before = f.run_path.read_bytes()
                with self.assertRaises(ap.Refused): f.proof()
                result = f.advance()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(f.run_path.read_bytes(), before)
