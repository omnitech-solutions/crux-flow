"""Read-only foundation controls in isolated, explicitly synthetic pilot trees."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup


def digest(value):
    return hashlib.sha256(value).hexdigest()


def quoted_rule():
    return {"handle": "ADR-0110/rotation", "domain": "decision-review",
            "rule": "Preserve assessments. This reader refuses inconsistent reports.",
            "scope": "Assessment", "provenance": "authored", "retires": ["ADR-0109/old-rotation"]}


def document():
    text = "This reader refuses inconsistent reports.\n"
    governs = [quoted_rule()]
    identity = digest(json.dumps({"source_adr": "ADR-0110", "clause": text,
                                 "affected_governs": governs}, sort_keys=True,
                                separators=(",", ":"), ensure_ascii=False).encode())
    return {"record_type": "implementation-migration", "format_version": "1",
            "batch_id": "implementation-pilot-001", "approval_slot": "implementation-1",
            "authorizing_refs": ["ADR-0146/authority-migration-is-reviewed-and-source-bound"],
            "entries": [{"source_adr": "ADR-0110", "clause": {"text": text, "sha256": digest(text.encode())},
                         "affected_governs": governs, "source_identity": identity,
                         "disposition": "historical-implementation", "rationale": "Separate the reader mechanism.",
                         "historical_destination": {"source_adr": "ADR-0110", "clause_sha256": digest(text.encode())},
                         "replacement_handles": ["ADR-0146/rotation-preserves-assessment-outcomes"]}],
            "signed_dependencies": [], "citation_dependencies": []}


class Foundation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = sup.init_repo(Path(self.temp.name) / "repo")
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: knowledge\n')
        self.adrs = self.root / "knowledge/adrs"; self.adrs.mkdir(parents=True)
        self.source = self.adrs / "ADR-0110-source.md"
        self.source.write_text("---\n" + yaml.safe_dump({"id": "ADR-0110", "status": "Accepted",
                                                     "governs": [quoted_rule()]}) +
                               "---\n# Synthetic source\nThis reader refuses inconsistent reports.\n")
        self.batch = document()
        self.path = self.adrs / "migrations/implementation-pilot-001.yaml"
        self.path.parent.mkdir(); self.save()

    def module(self):
        return importlib.import_module("implementation_migration")

    def save(self):
        self.path.write_text(yaml.safe_dump(self.batch, sort_keys=False))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}

    def test_closed_schema_and_duplicate_keys_refuse(self):
        m = self.module()
        self.assertEqual(m.load_batch(self.root, self.path), self.batch)
        for target in (self.batch, self.batch["entries"][0], self.batch["entries"][0]["clause"],
                       self.batch["entries"][0]["affected_governs"][0]):
            for key in ("authority", "governs", "applied", "wildcard"):
                with self.subTest(key=key, target=target):
                    target[key] = True; self.save()
                    with self.assertRaises(m.Refused): m.load_batch(self.root, self.path)
                    del target[key]
        self.save()
        self.path.write_text(self.path.read_text() + "batch_id: replacement\n")
        with self.assertRaisesRegex(m.Refused, "duplicate"): m.load_batch(self.root, self.path)

    def test_explicit_format_two_dependency_refs_and_source_bound_recovery_identity(self):
        m = self.module(); initial = copy.deepcopy(self.batch)
        self.batch.update(format_version="2", signed_dispositions=[]); self.save()
        self.assertEqual(m.load_batch(self.root, self.path), self.batch)
        recovery = {"witness": {"path": "knowledge/adrs/migrations/implementation-pilot-001.application.json",
            "sha256": "1" * 64}, "batch": {"path": self.path.relative_to(self.root).as_posix(),
            "sha256": "2" * 64}, "source_identities": [self.batch["entries"][0]["source_identity"]]}
        self.batch["recovery_from"] = recovery
        self.batch["batch_id"] = "implementation-pilot-001-recovery-" + m._canonical_digest(recovery)
        self.save(); self.assertEqual(m.load_batch(self.root, self.path), self.batch)
        for key, value in (("batch_id", "implementation-pilot-001-recovery-" + "0" * 64),
                           ("signed_dispositions", [{"path": "../bad", "sha256": "0" * 64}])):
            original = self.batch[key]; self.batch[key] = value; self.save()
            with self.assertRaises(m.Refused): m.load_batch(self.root, self.path)
            self.batch[key] = original
        self.batch = initial; self.batch["signed_dispositions"] = []; self.save()
        with self.assertRaises(m.Refused): m.load_batch(self.root, self.path)

    def test_exact_clause_and_governs_identity_survives_archive_and_lifecycle(self):
        m = self.module(); entry = self.batch["entries"][0]
        initial = m.resolve_clause(self.root, entry)
        archive = self.adrs / "archive"; archive.mkdir()
        self.source.rename(archive / self.source.name); self.source = archive / self.source.name
        self.source.write_text(self.source.read_text().replace("status: Accepted", "status: Superseded")
                               .replace("id: ADR-0110", "date: 2027-01-01\nid: ADR-0110"))
        later = m.resolve_clause(self.root, entry)
        self.assertEqual(initial["source_identity"], later["source_identity"])
        self.assertEqual(later["affected_governs"][0]["retires"], ["ADR-0109/old-rotation"])
        self.assertEqual(later["tier"], "archive")

    def test_changed_missing_duplicate_and_ambiguous_sources_refuse(self):
        m = self.module(); entry = self.batch["entries"][0]; original = self.source.read_bytes()
        for fault in ("clause", "governs", "missing", "duplicate", "ambiguous"):
            with self.subTest(fault=fault):
                self.source.write_bytes(original)
                duplicate = self.adrs / "ADR-0110-duplicate.md"
                if fault == "clause": self.source.write_text(self.source.read_text().replace("reports.\n", "reports differently.\n"))
                if fault == "governs": self.source.write_text(self.source.read_text().replace("Preserve assessments.", "Drop assessments."))
                if fault == "missing": self.source.unlink()
                if fault == "duplicate": duplicate.write_bytes(original)
                if fault == "ambiguous": self.source.write_bytes(original + entry["clause"]["text"].encode())
                with self.assertRaises(m.Refused): m.resolve_clause(self.root, entry)
                duplicate.unlink(missing_ok=True)

    def test_identity_is_not_a_caller_assertion_or_whole_file_classification(self):
        m = self.module()
        for field, value in (("source_identity", "0" * 64), ("source_adr", "ADR-0100")):
            bad = copy.deepcopy(self.batch["entries"][0]); bad[field] = value
            with self.assertRaises(m.Refused): m.resolve_clause(self.root, bad)
        self.batch["entries"].append(copy.deepcopy(self.batch["entries"][0])); self.save()
        with self.assertRaises(m.Refused): m.load_batch(self.root, self.path)

    def test_raw_symlink_and_dotdot_refuse_before_reading(self):
        m = self.module()
        deep = self.path.parent / "deep"; deep.mkdir()
        link = self.path.parent / "link"; link.symlink_to(deep, target_is_directory=True)
        for raw in (link / ".." / self.path.name, self.path.parent / "alias.yaml"):
            if raw.name == "alias.yaml": raw.symlink_to(self.path)
            with self.assertRaises(m.Refused): m.load_batch(self.root, raw)

    def prepare_dependencies(self):
        authorizer = {"id": "ADR-0146", "status": "Accepted", "governs": [
            {"handle": ref} for ref in self.batch["authorizing_refs"] +
            self.batch["entries"][0]["replacement_handles"]]}
        (self.adrs / "ADR-0146-authorizer.md").write_text("---\n" + yaml.safe_dump(authorizer) + "---\n")
        self.ledger = self.adrs / "doctrine/reconciliations.yml"; self.ledger.parent.mkdir()
        self.ledger.write_text(yaml.safe_dump({"config_version": "1", "reconciliations": [{"invariant": "INV-0001",
            "handle": "ADR-0110/rotation", "signed": "2026-10-02", "verdict": "compatible",
            "content_digest": "0" * 64, "rationale": None}]}))
        self.citing = self.root / "crux/skills/reader/SKILL.md"; self.citing.parent.mkdir(parents=True)
        self.citing.write_text("Follow rule:rotation.\n")

    def test_inventory_reports_actual_dependencies_and_pending_proof_without_writes(self):
        self.prepare_dependencies(); m = self.module(); before = self.snapshot()
        report = m.inventory(self.root, self.path)
        self.assertEqual(report["authority"], "none")
        self.assertEqual(report["approval"]["state"], "UNOBSERVED")
        self.assertEqual(report["application"]["state"], "UNOBSERVED")
        self.assertEqual(report["dependencies"]["signed"][0]["path"], self.ledger.relative_to(self.root).as_posix())
        self.assertEqual(report["dependencies"]["citations"][0]["path"], self.citing.relative_to(self.root).as_posix())
        self.assertEqual(report["signed_pairings"][0]["handle"], "ADR-0110/rotation")
        self.assertEqual(report["dependency_drift"], ["signed_dependencies", "citation_dependencies"])
        self.assertFalse(m.dry_run(self.root, self.path)["ready_to_apply"])
        self.assertEqual(self.snapshot(), before)

    def test_signed_inventory_and_citations_are_not_caller_narrowed(self):
        self.prepare_dependencies(); m = self.module()
        observed = m.inventory(self.root, self.path)["dependencies"]
        self.batch["signed_dependencies"] = observed["signed"]
        self.batch["citation_dependencies"] = observed["citations"]; self.save()
        self.assertEqual(m.inventory(self.root, self.path)["dependency_drift"], [])
        self.ledger.write_text(self.ledger.read_text().replace("compatible", "collision"))
        self.citing.write_text(self.citing.read_text() + "More context.\n")
        self.assertEqual(m.inventory(self.root, self.path)["dependency_drift"],
                         ["signed_dependencies", "citation_dependencies"])
        self.ledger.write_text("reconciliations: []\nreconciliations: []\n")
        with self.assertRaises(m.Refused): m.inventory(self.root, self.path)

    def test_authorizing_and_replacement_hosts_must_be_live_accepted(self):
        self.prepare_dependencies(); m = self.module()
        host = self.adrs / "ADR-0146-authorizer.md"
        host.write_text(host.read_text().replace("Accepted", "Superseded"))
        with self.assertRaisesRegex(m.Refused, "not-live-accepted"): m.inventory(self.root, self.path)

    def test_inventory_and_dry_run_cli_are_read_only_and_unapproved_apply_refuses(self):
        self.prepare_dependencies(); before = self.snapshot()
        script = sup.SCRIPTS / "implementation-migration.py"
        for command, expected in (("inventory", 0), ("dry-run", 1), ("apply", 1)):
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, str(script), command, "--batch", str(self.path),
                                         "--repo-root", str(self.root)], capture_output=True,
                                        env=sup.scrubbed_env())
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                report = json.loads(result.stdout or result.stderr)
                if command == "apply":
                    self.assertEqual(report["authority"], "none")
                    self.assertEqual(report["limit"], "migration-apply-history-unavailable")
                else: self.assertEqual(report["approval"]["state"], "UNOBSERVED")
        self.assertEqual(self.snapshot(), before)


    def sentinels(self):
        for name, content in {"knowledge/adrs/summaries/resolver.json": b'{"fixture": true}\n',
                              "knowledge/manifest.yml": b'adr:\n  next_number: 77\n',
                              "knowledge/promptbooks/runs/PB-0999-fixture/run-RUN-001.yaml":
                              b'fixture-run: unchanged\n'}.items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def test_fifo_refuses_promptly_without_changing_fixture_inventory(self):
        self.prepare_dependencies(); self.sentinels(); self.path.unlink(); os.mkfifo(self.path)
        before = self.snapshot()
        try:
            try:
                result = subprocess.run([sys.executable, str(sup.SCRIPTS / "implementation-migration.py"),
                    "inventory", "--batch", str(self.path), "--repo-root", str(self.root)],
                    capture_output=True, timeout=2, env=sup.scrubbed_env())
            except subprocess.TimeoutExpired:
                self.fail("FIFO must refuse before the subprocess timeout")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["authority"], "none")
            self.assertEqual(self.snapshot(), before)
        finally:
            self.path.unlink()

    def test_post_check_external_leaf_swap_refuses_the_opened_file(self):
        self.prepare_dependencies(); self.sentinels(); m = self.module()
        self.assertEqual(m.load_batch(self.root, self.path), self.batch)
        outside = Path(self.temp.name) / "outside-batch.yaml"; outside.write_bytes(self.path.read_bytes())
        from admission_source_io import SourceIO
        original = SourceIO.metadata; injected = []; reads = []
        original_read = SourceIO.read_bytes
        def swapping(source_io, given):
            metadata = original(source_io, given)
            if Path(given) == self.path and not injected:
                self.path.unlink(); self.path.symlink_to(outside)
                injected.append(self.snapshot())
            return metadata
        def reading(source_io, given, **kwargs):
            reads.append(Path(given))
            return original_read(source_io, given, **kwargs)
        with mock.patch.object(SourceIO, "metadata", swapping), mock.patch.object(SourceIO, "read_bytes", reading):
            with self.assertRaises(m.Refused): m.load_batch(self.root, self.path)
        self.assertTrue(injected)
        self.assertNotIn(self.path, reads)
        self.assertNotIn(outside, reads)
        self.assertEqual(self.snapshot(), injected[0])
        self.assertEqual(outside.read_bytes(), yaml.safe_dump(self.batch, sort_keys=False).encode())

    def test_required_ledger_validates_signed_unsigned_and_duplicate_pairings(self):
        self.prepare_dependencies(); self.sentinels(); m = self.module()
        original = yaml.safe_load(self.ledger.read_text())
        self.assertEqual(m.inventory(self.root, self.path)["signed_pairings"][0]["invariant"], "INV-0001")
        faults = [None, {"reconciliations": []}]
        for signed in (None, "never", "2026-10-02"):
            for key, value in (("invariant", None), ("handle", "ADR-0110"), ("verdict", "invented"),
                               ("content_digest", "bad"), ("verdict", "reconciled")):
                doc = copy.deepcopy(original); row = doc["reconciliations"][0]
                row["signed"] = signed; row[key] = value; faults.append(doc)
        bad_date = copy.deepcopy(original); bad_date["reconciliations"][0]["signed"] = "never"; faults.append(bad_date)
        duplicate = copy.deepcopy(original); duplicate["reconciliations"].append(copy.deepcopy(duplicate["reconciliations"][0]))
        faults.append(duplicate)
        for index, doc in enumerate(faults):
            with self.subTest(index=index):
                if doc is None: self.ledger.unlink(missing_ok=True)
                else: self.ledger.write_text(yaml.safe_dump(doc))
                before = self.snapshot()
                with self.assertRaises(m.Refused): m.inventory(self.root, self.path)
                self.assertEqual(self.snapshot(), before)
        unsigned = copy.deepcopy(original); unsigned["reconciliations"][0]["signed"] = None
        self.ledger.write_text(yaml.safe_dump(unsigned)); before = self.snapshot()
        report = m.inventory(self.root, self.path)
        self.assertEqual(report["signed_pairings"], [])
        self.assertEqual(len(report["dependencies"]["signed"]), 1)
        self.assertEqual(self.snapshot(), before)

    def test_selected_authorizing_and_replacement_filename_identity_refuse(self):
        self.prepare_dependencies(); self.sentinels(); m = self.module()
        host = self.adrs / "ADR-0146-authorizer.md"
        for original in (self.source, host):
            with self.subTest(path=original.name):
                wrong = original.with_name("ADR-0999-wrong.md"); original.rename(wrong)
                before = self.snapshot()
                try:
                    with self.assertRaisesRegex(m.Refused, "filename-identity"): m.inventory(self.root, self.path)
                    self.assertEqual(self.snapshot(), before)
                finally:
                    wrong.rename(original)
        self.batch["entries"][0]["replacement_handles"] = ["ADR-0147/replacement"]
        wrong = self.adrs / "ADR-0999-replacement.md"
        wrong.write_text("---\n" + yaml.safe_dump({"id": "ADR-0147", "status": "Accepted",
                          "governs": [{"handle": "ADR-0147/replacement"}]}) + "---\n")
        self.save(); before = self.snapshot()
        with self.assertRaisesRegex(m.Refused, "filename-identity"): m.inventory(self.root, self.path)
        self.assertEqual(self.snapshot(), before)

    def test_overlapping_clause_offsets_are_ambiguous_without_writes(self):
        self.prepare_dependencies(); self.sentinels(); m = self.module()
        entry = self.batch["entries"][0]
        entry["clause"] = {"text": "aaa", "sha256": digest(b"aaa")}
        entry["historical_destination"]["clause_sha256"] = entry["clause"]["sha256"]
        entry["source_identity"] = m.source_identity(entry); self.save()
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"aaa\n")
        self.assertEqual(m.resolve_clause(self.root, entry)["source_identity"], entry["source_identity"])
        for suffix in (b"aaaa\n", b"aaa\naaa\n", b"absent\n"):
            with self.subTest(suffix=suffix):
                self.source.write_bytes(original + suffix); before = self.snapshot()
                with self.assertRaises(m.Refused): m.resolve_clause(self.root, entry)
                self.assertEqual(self.snapshot(), before)

    def test_canonical_ledger_parser_and_wrapper_agree_without_authority(self):
        self.prepare_dependencies(); import doctrine_projection as doctrine
        original = yaml.safe_load(self.ledger.read_text())
        cases = [original]
        unsigned = copy.deepcopy(original); unsigned["reconciliations"][0]["signed"] = None; cases.append(unsigned)
        for signed in (None, "never", "2026-10-02"):
            invalid = copy.deepcopy(original); invalid["reconciliations"][0].update(signed=signed, verdict="invented")
            cases.append(invalid)
        duplicate = copy.deepcopy(original); duplicate["reconciliations"] *= 2; cases.append(duplicate)
        for index, doc in enumerate(cases):
            with self.subTest(index=index):
                text = yaml.safe_dump(doc); self.ledger.write_text(text)
                if index < 2:
                    self.assertEqual(doctrine.parse_reconciliations(text), doctrine.read_reconciliations(self.root))
                else:
                    with self.assertRaises(doctrine.DoctrineValidationError): doctrine.parse_reconciliations(text)
                    with self.assertRaises(doctrine.DoctrineValidationError): doctrine.read_reconciliations(self.root)


if __name__ == "__main__":
    unittest.main()
