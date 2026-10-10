"""Synthetic human inputs exercise actual disposition CLI producers, never real consent."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dev_surface import IS_STAGED_ARTIFACT
from test_implementation_migration import document, quoted_rule
import doctrine_projection as dp
import summaries_projection as sp


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Producers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: knowledge\n')
        self.adrs = self.root / "knowledge/adrs"; self.adrs.mkdir(parents=True)
        self.batch = document(); self.batch.update(format_version="2", signed_dispositions=[])
        self.entry = self.batch["entries"][0]
        self.old = quoted_rule(); self.new = {**self.old, "handle": self.entry["replacement_handles"][0],
            "rule": "Preserve assessments.", "scope": "Assessment"}
        self.old.pop("retires", None); self.new.pop("retires", None)
        self.entry["affected_governs"] = [self.old]
        import implementation_migration as im
        self.entry["source_identity"] = im.source_identity(self.entry)
        for aid, rules, body in (("ADR-0110", [self.old], self.entry["clause"]["text"]),
                ("ADR-0146", [self.new, {**self.new, "handle": self.batch["authorizing_refs"][0]}], "")):
            (self.adrs / (aid + "-synthetic.md")).write_text("---\n" + yaml.safe_dump(
                {"id": aid, "status": "Accepted", "governs": rules}) + "---\n" + body)
        invdir = self.root / "knowledge/invariants"; invdir.mkdir()
        self.inv = invdir / "preserve.md"
        self.inv.write_text('---\nid: INV-0001\nratification: ratified\nrelated_adrs: [ADR-0110, ADR-0146]\n---\n## The invariant\nPreserve assessments.\n')
        self.rows = [{"invariant": "INV-0001", "handle": r["handle"], "verdict": "compatible",
            "signed": "2026-10-02", "rationale": None,
            "content_digest": dp.reconciliation_content_digest("Preserve assessments.", r["rule"])}
            for r in (self.old, self.new)]
        self.ledger = self.adrs / "doctrine/reconciliations.yml"; self.ledger.parent.mkdir()
        self.reviews = {"config_version": "1", "batches": [{"id": "synthetic-admission",
            "signed": "2026-10-02", "handles": [self.old["handle"]], "no_rule": []}],
            "receipts": [{"handle": self.old["handle"], "digest": sp.content_digest(self.old),
                "verdict": "pass", "anchor": "Synthetic span", "batch": "synthetic-admission"}], "no_rule": []}
        self.old["anchor"] = "Synthetic span"; self.entry["affected_governs"] = [self.old]
        self.entry["source_identity"] = im.source_identity(self.entry)
        source = self.adrs / "ADR-0110-synthetic.md"
        source.write_text("---\n" + yaml.safe_dump({"id": "ADR-0110", "status": "Accepted", "governs": [self.old]}) + "---\n" + self.entry["clause"]["text"])
        source.write_text(source.read_text() + "Synthetic span\n")
        self.reviews["receipts"][0]["digest"] = sp.content_digest(self.old)
        self.review_path = self.adrs / "summaries/backfill-reviews.yml"; self.review_path.parent.mkdir()
        self.batch_path = self.adrs / "migrations/implementation-pilot-001.yaml"; self.batch_path.parent.mkdir()
        self.manifest = self.root / "knowledge/manifest.yml"
        self.manifest.write_text(yaml.safe_dump({"adr": {"governs_from": 111,
            "governs_backfill_cohort": ["ADR-0110"], "governs_backfilled": {"ADR-0110": [self.old["handle"]]}}}))
        self.log = self.root / "knowledge/log.md"
        self.log.write_text("## [2026-10-02] backfill | synthetic-admission\n\nADRs: ADR-0110\n")
        self.journal = self.root / "knowledge/journal/2026-10.md"; self.journal.parent.mkdir()
        self.journal.write_text("## [2026-10-02 12:00] review | backfill sign-off synthetic-admission\n\nADRs: ADR-0110\n")
        self.save()

    def save(self):
        self.ledger.write_text(yaml.safe_dump({"config_version": "1", "reconciliations": self.rows}))
        self.review_path.write_text(yaml.safe_dump(self.reviews))
        self.batch["signed_dependencies"] = [{"path": p.relative_to(self.root).as_posix(), "sha256": sha(p.read_bytes())}
            for p in (self.ledger, self.review_path)]
        self.batch_path.write_text(yaml.safe_dump(self.batch, sort_keys=False))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def command(self, kind="doctrine", *, human=True, dry=False, extra=()):
        script = "signoff-reconciliation.py" if kind == "doctrine" else "signoff-backfill.py"
        args = [sys.executable, str(SCRIPTS / script), "--migration-disposition", str(self.batch_path),
            "--source-identity", self.entry["source_identity"], "--handle", self.old["handle"],
            "--repo-root", str(self.root), "--date", "2026-10-02"]
        if kind == "doctrine": args += ["--invariant", "INV-0001"]
        if human: args += ["--human-instruction", "Synthetic fixture: preserve this old pairing and migrate the entry.",
            "--rationale", "Synthetic rationale: replacement preserves the obligation."]
        if dry: args += ["--dry-run"]
        return subprocess.run(args + list(extra), capture_output=True, text=True)

    def receipts(self):
        return list((self.adrs / "migrations/dispositions").glob("*.json"))

    def test_two_actual_cli_producers_preserve_ledgers_and_replay(self):
        m = importlib.import_module("migration_disposition")
        initial = self.snapshot(); before = initial
        for kind in ("doctrine", "backfill"):
            dry = self.command(kind, human=False, dry=True)
            self.assertEqual(dry.returncode, 0, dry.stderr + dry.stdout)
            self.assertEqual(self.snapshot(), before)
            result = self.command(kind)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            receipt_path = self.root / json.loads(result.stdout)["receipt"]["path"]
            receipt = m.parse_receipt(receipt_path.read_text())
            self.assertEqual(receipt["producer"], m.PRODUCERS[
                "doctrine-pairing" if kind == "doctrine" else "backfill-receipt"])
            self.assertEqual(receipt["source_identity"], self.entry["source_identity"])
            self.assertEqual(receipt["entry_sha256"], m.canonical_digest(self.entry))
            saved = self.snapshot()
            replay = self.command(kind)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            self.assertEqual(self.snapshot(), saved)
            before = saved
        self.assertEqual(len(self.receipts()), 2)
        for p in (self.ledger, self.review_path, self.batch_path):
            self.assertEqual(p.read_bytes(), initial[p.relative_to(self.root).as_posix()])

    def test_absent_unsigned_stale_and_unseeded_pairings_refuse_without_writes(self):
        for fault in ("absent", "unsigned", "stale", "replacement-unsigned", "replacement-collision", "seed"):
            with self.subTest(fault=fault):
                oldrows = copy.deepcopy(self.rows); original_inv = self.inv.read_text()
                if fault == "absent": self.rows = self.rows[1:]
                if fault == "unsigned": self.rows[0]["signed"] = None
                if fault == "stale": self.rows[0]["content_digest"] = "0" * 64
                if fault == "replacement-unsigned": self.rows[1]["signed"] = None
                if fault == "replacement-collision": self.rows[1]["verdict"] = "collision"
                if fault == "seed": self.inv.write_text(original_inv.replace("ADR-0146", "ADR-0000"))
                self.save(); before = self.snapshot(); result = self.command()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(self.snapshot(), before)
                self.rows = oldrows; self.inv.write_text(original_inv)

    def test_backfill_unsigned_stale_removed_and_malformed_refuse(self):
        for fault in ("unsigned", "stale", "removed", "duplicate-batch", "wrong-binding"):
            with self.subTest(fault=fault):
                original = copy.deepcopy(self.reviews)
                if fault == "unsigned": self.reviews["batches"][0]["signed"] = None
                if fault == "stale": self.reviews["receipts"][0]["digest"] = "0" * 64
                if fault == "removed":
                    self.reviews["receipts"][0].update(verdict="removed", anchor=None)
                if fault == "duplicate-batch": self.reviews["batches"].append(copy.deepcopy(self.reviews["batches"][0]))
                if fault == "wrong-binding": self.reviews["batches"][0]["handles"] = []
                self.save(); before = self.snapshot(); result = self.command("backfill")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(self.snapshot(), before); self.reviews = original

    def test_write_requires_specific_human_fields_and_current_batch_dependency(self):
        for kind in ("doctrine", "backfill"):
            before = self.snapshot(); result = self.command(kind, human=False)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(self.snapshot(), before)
        self.batch["signed_dependencies"][0]["sha256"] = "0" * 64
        self.batch_path.write_text(yaml.safe_dump(self.batch))
        before = self.snapshot(); result = self.command()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_pure_closed_receipt_parser_and_discriminated_subjects(self):
        m = importlib.import_module("migration_disposition")
        for kind in ("doctrine", "backfill"):
            result = self.command(kind); self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for path in self.receipts():
            doc = m.parse_receipt(path.read_text())
            for target in (doc, doc["dependency"], doc["subject"]):
                target["applied"] = True
                with self.assertRaises(m.Refused): m.parse_receipt(json.dumps(doc))
                del target["applied"]
            wrong = copy.deepcopy(doc); wrong["producer"] = "self-asserted"
            with self.assertRaises(m.Refused): m.parse_receipt(json.dumps(wrong))
            with self.assertRaises(m.Refused): m.parse_receipt(path.read_text() + "\nproducer: forged\n")
            if doc["subject"]["kind"] == "backfill-receipt":
                doc["replacement_pairings"] = []
                with self.assertRaises(m.Refused): m.parse_receipt(json.dumps(doc))
            else:
                wrong = copy.deepcopy(doc); wrong["subject"].update(invariant="XYZ-INV-0001", member_kind="observation")
                for pairing in wrong["replacement_pairings"]: pairing["invariant"] = "XYZ-INV-0001"
                with self.assertRaises(m.Refused): m.parse_receipt(json.dumps(wrong))

    def test_conflicting_replay_and_existing_content_change_refuse(self):
        result = self.command(); self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before = self.snapshot()
        result = self.command(extra=("--rationale", "Different synthetic disposition rationale."))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)
        path = self.receipts()[0]; path.write_text(path.read_text().replace("Synthetic rationale", "Modified rationale"))
        before = self.snapshot(); result = self.command()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_exact_replay_checks_conflicting_receipts_in_both_filename_orders(self):
        m = importlib.import_module("migration_disposition")
        for kind in ("doctrine", "backfill"):
            result = self.command(kind)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            original = self.root / json.loads(result.stdout)["receipt"]["path"]
            doc = m.parse_receipt(original.read_text())
            for position in ("before", "after"):
                with self.subTest(kind=kind, conflict_position=position):
                    # Content-addressed names determine traversal order. Choose an
                    # ordinary synthetic rationale yielding the requested order.
                    conflict = None
                    for number in range(4096):
                        changed = {**doc, "rationale": f"Synthetic conflicting rationale {number}."}
                        raw = (json.dumps(changed, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
                        candidate = original.with_name(sha(raw) + ".json")
                        if (candidate.name < original.name) == (position == "before"):
                            conflict = candidate; break
                    self.assertIsNotNone(conflict, "fixture must establish the requested filename order")
                    self.assertEqual(m.parse_receipt(raw.decode())["subject"], doc["subject"])
                    conflict.write_bytes(raw)
                    names = sorted((original.name, conflict.name))
                    self.assertEqual(names.index(conflict.name), 0 if position == "before" else 1)
                    before = self.snapshot()
                    try:
                        for dry in (False, True):
                            replay = self.command(kind, dry=dry)
                            self.assertEqual(replay.returncode, 1, replay.stdout + replay.stderr)
                            self.assertIn("migration-disposition-subject-conflict", replay.stdout)
                            self.assertEqual(self.snapshot(), before)
                    finally:
                        conflict.unlink()
            before = self.snapshot(); replay = self.command(kind)
            self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
            self.assertEqual(json.loads(replay.stdout)["receipt"]["state"], "no-op")
            self.assertEqual(self.snapshot(), before)

    def test_source_identity_replacement_status_and_raw_ledger_refuse(self):
        source = self.adrs / "ADR-0110-synthetic.md"; replacement = self.adrs / "ADR-0146-synthetic.md"
        for fault in ("source", "replacement", "raw-ledger", "wrong-subject", "absent-ledger", "duplicate-ledger"):
            originals = {p: p.read_bytes() for p in (source, replacement, self.ledger)}
            if fault == "source": source.write_text(source.read_text().replace("reports.\n", "other reports.\n"))
            if fault == "replacement": replacement.write_text(replacement.read_text().replace("Accepted", "Proposed"))
            if fault == "raw-ledger": self.ledger.write_text(self.ledger.read_text() + "# Edited raw dependency\n")
            if fault == "absent-ledger": self.ledger.unlink()
            if fault == "duplicate-ledger": self.ledger.write_text(self.ledger.read_text() + "config_version: '1'\n")
            extra = ("--handle", "ADR-0110/wrong") if fault == "wrong-subject" else ()
            before = self.snapshot(); result = self.command(extra=extra)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(self.snapshot(), before)
            for p, content in originals.items(): p.write_bytes(content)

    def test_observation_pairing_uses_canonical_evidence_seed(self):
        manifest = yaml.safe_load(self.manifest.read_text()); manifest["concerns_enabled"] = ["observations"]
        self.manifest.write_text(yaml.safe_dump(manifest))
        obsdir = self.root / "knowledge/observations"; obsdir.mkdir()
        obs = {"id": "OBS-0001", "status": "ratified", "provenance": "recovered",
            "evidence": ["src/review.py:1-2"], "governs": [{"handle": "OBS-0001/assessments",
                "domain": self.old["domain"], "rule": "Preserve assessments.", "scope": "src",
                "provenance": "recovered"}]}
        (obsdir / "OBS-0001-assessments.md").write_text("---\n" + yaml.safe_dump(obs) + "---\n")
        self.old["scope"] = "src/"; self.new["scope"] = "src/"
        for aid, rules, body in (("ADR-0110", [self.old], self.entry["clause"]["text"]),
                ("ADR-0146", [self.new, {**self.new, "handle": self.batch["authorizing_refs"][0], "scope": "elsewhere"}], "")):
            (self.adrs / (aid + "-synthetic.md")).write_text("---\n" + yaml.safe_dump(
                {"id": aid, "status": "Accepted", "governs": rules}) + "---\n" + body)
        import implementation_migration as im
        self.entry["source_identity"] = im.source_identity(self.entry)
        for row in self.rows: row["invariant"] = "OBS-0001/assessments"
        self.save(); result = self.command(extra=("--invariant", "OBS-0001/assessments"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = importlib.import_module("migration_disposition").parse_receipt(self.receipts()[0].read_text())
        self.assertEqual(receipt["subject"]["member_kind"], "observation")
        manifest.pop("concerns_enabled"); self.manifest.write_text(yaml.safe_dump(manifest))
        before = self.snapshot(); result = self.command(extra=("--invariant", "OBS-0001/assessments"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_ordinary_help_and_required_flags_remain_byte_equivalent(self):
        if IS_STAGED_ARTIFACT:
            self.skipTest('reads HEAD through git; the staged artifact is not a git checkout')
        for filename in ("signoff-reconciliation.py", "signoff-backfill.py"):
            original = subprocess.run(["git", "show", "HEAD:crux/scripts/" + filename],
                cwd=SCRIPTS.parents[1], capture_output=True, text=True, check=True).stdout
            olddir = self.root / "old"; olddir.mkdir(exist_ok=True); path = olddir / filename
            path.write_text(original)
            import os
            env = dict(os.environ, PYTHONPATH=str(SCRIPTS))
            for flags in (("--help",), ()):
                old = subprocess.run([sys.executable, str(path), *flags], env=env, capture_output=True, text=True)
                new = subprocess.run([sys.executable, str(SCRIPTS / filename), *flags], env=env, capture_output=True, text=True)
                self.assertEqual((old.returncode, old.stdout, old.stderr), (new.returncode, new.stdout, new.stderr))

    def test_backfill_requires_existing_corroboration_and_preserves_unrelated_appends(self):
        for fault in ("log-absent", "log-date", "log-ids", "journal-absent", "journal-ids", "admission"):
            originals = {p: p.read_bytes() for p in (self.log, self.journal, self.manifest)}
            if fault == "log-absent": self.log.unlink()
            if fault == "log-date": self.log.write_text(self.log.read_text().replace("2026-10-02", "2026-10-01"))
            if fault == "log-ids": self.log.write_text(self.log.read_text().replace("ADR-0110", "ADR-0100"))
            if fault == "journal-absent": self.journal.unlink()
            if fault == "journal-ids": self.journal.write_text(self.journal.read_text().replace("ADR-0110", "ADR-0100"))
            if fault == "admission": self.manifest.write_text("adr: {}\n")
            before = self.snapshot(); result = self.command("backfill")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(self.snapshot(), before)
            for p, content in originals.items(): p.write_bytes(content)
        result = self.command("backfill"); self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.log.write_text(self.log.read_text() + "\n## [2026-10-03] adr | unrelated work\n\nDetails.\n")
        self.journal.write_text(self.journal.read_text() + "\n## [2026-10-03 12:01] build | unrelated work\n\nDetails.\n")
        before = self.snapshot(); result = self.command("backfill")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_member_frontmatter_refuses(self):
        self.inv.write_text(self.inv.read_text().replace("id: INV-0001\n", "id: INV-0002\nid: INV-0001\n"))
        before = self.snapshot(); result = self.command()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
