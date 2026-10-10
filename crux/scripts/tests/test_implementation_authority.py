"""Read validation only; manually committed locators are synthetic, never apply proof."""
from __future__ import annotations

import json
import atexit
import functools
import copy
import os
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup
import implementation_migration as migration
import yaml
from test_implementation_cycles import WriterFixture, book_two
from test_implementation_migration import quoted_rule


@functools.lru_cache(maxsize=4)
def _writer_template(environment, cwd):
    """A real writer baseline per worker and environment; never a cached test result.

    The environment key includes Git's object format and every other inherited
    setting. Constructor tests still instantiate WriterFixture directly.
    """
    temp = tempfile.TemporaryDirectory()
    atexit.register(temp.cleanup)
    return WriterFixture(Path(temp.name) / "repo", migration=True)


class PublicationProof(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        template = _writer_template(tuple(sorted(sup.scrubbed_env().items())), os.getcwd())
        self.f = template.copy_to(Path(self.temp.name) / "repo")
        self.root = self.f.root
        adrs = self.root / "docs/adrs"
        (adrs / "ADR-0110-source.md").write_text("---\n" + yaml.safe_dump({
            "id": "ADR-0110", "status": "Accepted", "governs": [quoted_rule()]}) +
            "---\nThis reader refuses inconsistent reports.\n")
        ledger = adrs / "doctrine/reconciliations.yml"; ledger.parent.mkdir()
        ledger.write_text('config_version: "1"\nreconciliations: []\n')
        batch = yaml.safe_load(self.f.path.read_bytes())
        batch.update(format_version="2", signed_dispositions=[])
        batch["signed_dependencies"] = [{"path": ledger.relative_to(self.root).as_posix(),
            "sha256": migration._digest(ledger.read_bytes())}]
        self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False))
        self.f.retained.write_bytes(self.f.path.read_bytes())
        receipt = json.loads(self.f.receipt.read_bytes())
        receipt["subjects"][0]["sha256"] = migration._digest(self.f.path.read_bytes())
        sup.select_question(self.root, receipt)
        self.f.receipt.write_text(json.dumps(receipt))
        sup.commit_all(self.root, "synthetic exact batch dependencies before actual close")
        self.witness = adrs / "migrations/implementation-pilot-001.application.json"

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}

    def close(self):
        result = self.f.advance("--migration-batch", self.f.rel)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sup.commit_all(self.root, "synthetic actual producer close")
        self.proof = migration.ap.validate_historical_migration_binding(self.root, self.f.run_path,
            slot=self.f.slot, batch_path=self.f.rel, batch_sha256=migration._digest(self.f.path.read_bytes()))

    def locator(self):
        p = self.proof
        return {"record_type": "implementation-migration-publication", "format_version": "1",
            "publisher": "implementation-migration/1", "batch": p.binding["batch"],
            "book_id": p.binding["book_id"], "run_id": p.binding["run_id"],
            "book_content_hash": p.binding["book_content_hash"], "slot": p.binding["slot"],
            "gate_prompt": p.binding["gate_prompt"],
            "run_path": self.f.run_path.relative_to(self.root).as_posix(),
            "binding_sha256": migration._digest(json.dumps(p.binding, sort_keys=True,
                separators=(",", ":")).encode()),
            "declaration_sha256": migration._digest(json.dumps(p.slot, sort_keys=True,
                separators=(",", ":")).encode())}

    def publish(self, locator=None):
        self.witness.write_text(json.dumps(locator or self.locator(), sort_keys=True) + "\n")
        sup.commit_all(self.root, "synthetic manual witness; no apply execution")
        return migration._discover_publications(self.root)[0]

    def test_actual_close_then_synthetic_locator_proves_exact_binding(self):
        self.close(); row = self.publish(); before = self.snapshot()
        proof = migration._publication_proof(self.root, row)
        self.assertEqual(proof.binding, self.proof.binding)
        self.assertEqual(self.snapshot(), before)

    def test_closed_locator_identity_refuses_without_writes(self):
        self.close()
        faults = [("applied", True), ("publisher", "other"), ("slot", "implementation-2"),
            ("book_id", "PB-9998"), ("run_id", "RUN-999"), ("gate_prompt", 5),
            ("binding_sha256", "0" * 64), ("declaration_sha256", "0" * 64),
            ("batch", {"path": self.f.rel, "sha256": "0" * 64})]
        for key, value in faults:
            with self.subTest(key=key):
                locator = copy.deepcopy(self.locator()); locator[key] = value
                row = {"path": self.witness.relative_to(self.root).as_posix(),
                    "content": json.dumps(locator).encode(), "publication_commit": "HEAD"}
                before = self.snapshot()
                with self.assertRaises(migration.Refused): migration._publication_proof(self.root, row)
                self.assertEqual(self.snapshot(), before)


class AuthorityView(PublicationProof):
    def view(self):
        before = self.snapshot()
        try: return migration.authority_view(self.root)
        finally: self.assertEqual(self.snapshot(), before)

    def test_draft_and_successful_close_without_publication_leave_original_authority(self):
        self.assertEqual(self.view()["state"], "original")
        self.close()
        self.assertEqual(self.view()["state"], "original")

    def test_inventory_reports_the_committed_locator_and_proves_neither_close_nor_application(self):
        before = migration.inventory(self.root, self.f.rel)
        self.assertEqual(before["publication"]["state"], "ABSENT")
        self.close(); row = self.publish()
        after = migration.inventory(self.root, self.f.rel)
        self.assertEqual(after["publication"], {"state": "COMMITTED", "path": row["path"],
            "sha256": row["sha256"], "publication_commit": row["publication_commit"]})
        for key, limit in (("approval", "inventory-proves-no-close"),
                           ("application", "inventory-proves-no-application")):
            self.assertEqual(after[key], {"state": "UNOBSERVED", "limit": limit})

    def test_published_overlay_has_references_and_retirement_edges_without_mapping_rule_text(self):
        self.close(); self.publish()
        view = self.view()
        self.assertEqual(view["state"], "published")
        historical = view["historical_handles"]["ADR-0110/rotation"]
        self.assertEqual(historical["replacements"], ["ADR-0146/rotation-preserves-assessment-outcomes"])
        self.assertEqual(view["reserved_slugs"], ["rotation"])
        self.assertEqual(view["retired_handles"], ["ADR-0109/old-rotation"])
        self.assertNotIn("Preserve assessments.", json.dumps(view))
        source = self.root / "docs/adrs/ADR-0110-source.md"
        archive = source.parent / "archive"; archive.mkdir()
        destination = archive / source.name; source.rename(destination)
        destination.write_text(destination.read_text().replace("Accepted", "Superseded"))
        sup.commit_all(self.root, "synthetic selected source lifecycle and archive")
        later = self.view()
        self.assertEqual(later["historical_handles"], view["historical_handles"])
        self.assertEqual(later["retired_handles"], view["retired_handles"])

    def test_published_source_replacement_signature_citation_and_missing_batch_refuse_whole_view(self):
        self.close(); self.publish()
        head = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        for fault in ("clause", "governs", "replacement", "signature", "citation", "missing-batch"):
            with self.subTest(fault=fault):
                migration.cr.git(self.root, "reset", "--hard", head)
                source = self.root / "docs/adrs/ADR-0110-source.md"
                if fault == "clause": source.write_text(source.read_text().replace("reports.\n", "changed.\n"))
                if fault == "governs": source.write_text(source.read_text().replace("old-rotation", "changed-retirement"))
                if fault == "replacement":
                    host = self.root / "docs/adrs/ADR-0146-authorizer.md"
                    host.write_text(host.read_text().replace("Accepted", "Superseded"))
                if fault == "signature":
                    ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
                    ledger.write_text(ledger.read_text() + "# changed signed dependency\n")
                if fault == "citation":
                    (self.root / "AGENTS.md").write_text("Follow rule:rotation.\n")
                if fault == "missing-batch": self.f.path.unlink()
                sup.commit_all(self.root, "synthetic current published input refusal")
                with self.assertRaises(migration.Refused): self.view()
                (self.root / "AGENTS.md").unlink(missing_ok=True)

    def test_public_context_registry_and_candidate_injection_are_not_parameters(self):
        for key in ("context", "registry", "candidate", "assume_active", "proof"):
            with self.subTest(key=key):
                with self.assertRaises(TypeError): migration.authority_view(self.root, **{key: {}})

    def test_named_revision_uses_isolated_reachable_history_and_leaves_checkout_unchanged(self):
        before_publication = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        self.close(); self.publish(); before = self.snapshot()
        self.assertEqual(migration.authority_view(self.root, revision=before_publication)["state"], "original")
        head = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual(migration.authority_view(self.root, revision=head)["state"], "published")
        self.assertEqual(self.snapshot(), before)

    def test_named_revision_child_drops_all_ambient_git_runtime_configuration(self):
        revision = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        before = self.snapshot(); calls = []; original = subprocess.run
        ambient = {"GIT_TEMPLATE_DIR": "/inert-template-not-created", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/inert-hooks-not-created",
            "GIT_CONFIG_PARAMETERS": "inert-config-not-parsed"}
        def checked(args, **kwargs):
            env = kwargs.get("env", {})
            self.assertFalse(set(ambient).intersection(env), args)
            calls.append(args)
            return original(args, **kwargs)
        with mock.patch.dict(os.environ, ambient), mock.patch.object(subprocess, "run", side_effect=checked):
            self.assertEqual(migration.authority_view(self.root, revision=revision)["state"], "original")
        self.assertTrue(calls)
        self.assertEqual(self.snapshot(), before)
        for invalid in ("--invalid", "not-a-committed-revision"):
            with self.assertRaises(migration.Refused): migration.authority_view(self.root, revision=invalid)
            self.assertEqual(self.snapshot(), before)

    def test_revision_materialization_disables_templates_hooks_and_filter_configuration(self):
        revision = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        before = self.snapshot(); calls = []; original = subprocess.run
        def checked(args, **kwargs):
            if args[0] == "git" and any(command in args for command in ("clone", "checkout")):
                env = kwargs["env"]
                self.assertNotIn("GIT_CONFIG_COUNT", env)
                self.assertNotIn("GIT_TEMPLATE_DIR", env)
                self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
                self.assertEqual(env["GIT_CONFIG_SYSTEM"], os.devnull)
                self.assertIn("core.hooksPath=" + os.devnull, args)
                self.assertIn("core.fsmonitor=false", args)
                self.assertIn("core.attributesFile=" + os.devnull, args)
                if "clone" in args:
                    self.assertIn("--template=", args)
                    self.assertIn("--no-hardlinks", args)
                    self.assertNotIn("--no-local", args)
                calls.append(args)
            return original(args, **kwargs)
        with mock.patch.object(subprocess, "run", side_effect=checked), \
             mock.patch.object(migration, "authority_view", return_value={"state": "original"}):
            self.assertEqual(migration._revision_view(self.root, revision)["state"], "original")
        self.assertTrue(any("clone" in args for args in calls))
        self.assertTrue(any("checkout" in args for args in calls))
        self.assertEqual(self.snapshot(), before)

    def test_historical_context_does_not_load_current_registry_or_live_constraint_projection(self):
        self.close(); self.publish()
        with mock.patch.object(migration.ap, "live_constraints", side_effect=AssertionError("eligibility recursion")), \
             mock.patch.object(migration.ap.cg, "REGISTRY_PATH", self.root / "missing-current-registry"):
            self.assertEqual(self.view()["state"], "published")

    def test_whole_authority_read_refuses_head_movement_after_discovery(self):
        self.close(); self.publish(); original = migration._validated_inputs
        def moved(*args):
            result = original(*args)
            migration.cr.git(self.root, "commit", "--allow-empty", "-m", "synthetic movement after proof")
            return result
        with mock.patch.object(migration, "_validated_inputs", side_effect=moved):
            with self.assertRaisesRegex(migration.Refused, "history-head-changed"): self.view()

    def refresh_reviewed_batch(self):
        """Fixture council inputs are synthetic; actual production close remains mandatory."""
        self.f.retained.write_bytes(self.f.path.read_bytes())
        receipt = json.loads(self.f.receipt.read_bytes())
        receipt["subjects"][0]["sha256"] = migration._digest(self.f.path.read_bytes())
        sup.select_question(self.root, receipt)
        self.f.receipt.write_text(json.dumps(receipt))
        sup.commit_all(self.root, "synthetic exact reviewed fixture input")

    def test_signed_shaped_affected_pairing_cannot_supply_human_migration_disposition(self):
        ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
        ledger.write_text(yaml.safe_dump({"config_version": "1", "reconciliations": [{
            "invariant": "INV-0001", "handle": "ADR-0110/rotation", "verdict": "reconciled",
            "content_digest": "0" * 64, "signed": "2026-10-02",
            "rationale": "Self-asserted migration permission is not an owning human disposition."}]}))
        batch = yaml.safe_load(self.f.path.read_bytes())
        batch["signed_dependencies"][0]["sha256"] = migration._digest(ledger.read_bytes())
        self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False)); self.refresh_reviewed_batch()
        self.close(); self.publish()
        with self.assertRaisesRegex(migration.Refused, "human-disposition-pending"): self.view()

    def test_unreviewed_backfill_dependency_refuses_instead_of_claiming_empty_removed_lane(self):
        ledger = self.root / "docs/adrs/summaries/backfill-reviews.yml"
        ledger.parent.mkdir(); ledger.write_text('config_version: "1"\nbatches: []\nreceipts: []\n')
        sup.commit_all(self.root, "synthetic unadapted backfill surface")
        self.close(); self.publish()
        with self.assertRaisesRegex(migration.Refused, "signed-dependency-drift"): self.view()

    def test_reviewed_empty_backfill_surface_is_bound_without_projection_recursion(self):
        ledger = self.root / "docs/adrs/summaries/backfill-reviews.yml"
        ledger.parent.mkdir(); ledger.write_text('config_version: "1"\nbatches: []\nreceipts: []\n')
        batch = yaml.safe_load(self.f.path.read_bytes())
        batch["signed_dependencies"].append({"path": ledger.relative_to(self.root).as_posix(),
            "sha256": migration._digest(ledger.read_bytes())})
        self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False)); self.refresh_reviewed_batch()
        self.close(); self.publish()
        import summaries_projection as summaries
        with mock.patch.object(summaries, "collect_records", side_effect=AssertionError("projection recursion")), \
             mock.patch.object(summaries, "live_records", side_effect=AssertionError("projection recursion")):
            self.assertEqual(self.view()["state"], "published")

    def test_format_one_draft_and_close_do_not_implicitly_activate_new_format(self):
        batch = yaml.safe_load(self.f.path.read_bytes())
        batch["format_version"] = "1"; del batch["signed_dispositions"]
        self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False)); self.refresh_reviewed_batch()
        self.assertEqual(self.view()["state"], "original")
        self.close(); self.publish()
        with self.assertRaisesRegex(migration.Refused, "explicit-activation-format-required"): self.view()

    def test_missing_context_and_forged_close_refuse_after_publication(self):
        self.close(); self.publish()
        context = self.root / self.proof.binding["context"]["path"]
        context.unlink(); sup.commit_all(self.root, "synthetic missing published close context")
        with self.assertRaises(migration.Refused): self.view()

    def test_dirty_source_and_raw_publication_symlink_refuse(self):
        self.close(); self.publish()
        source = self.root / "docs/adrs/ADR-0110-source.md"
        source.write_text(source.read_text() + "\nUncommitted unrelated lifecycle prose.\n")
        with self.assertRaisesRegex(migration.Refused, "input-not-committed"): self.view()
        migration.cr.git(self.root, "checkout", "--", source.relative_to(self.root).as_posix())
        original = self.witness.read_bytes()
        target = Path(self.temp.name) / "outside.json"; target.write_bytes(original)
        self.witness.unlink(); self.witness.symlink_to(target)
        with self.assertRaisesRegex(migration.Refused, "symlink"): self.view()

    def ratified_member(self, related="ADR-0001"):
        path = self.root / "docs/invariants/member.md"; path.parent.mkdir(exist_ok=True)
        path.write_text("---\n" + yaml.safe_dump({"id": "INV-0001", "ratification": "ratified",
            "related_adrs": [related]}) + "---\n## The invariant\nPreserve output.\n")
        sup.commit_all(self.root, "synthetic ratified member source")
        return path

    def test_member_source_omission_cannot_be_a_complete_signed_inventory(self):
        self.ratified_member(); self.close(); self.publish()
        with self.assertRaisesRegex(migration.Refused, "signed-dependency-drift"): self.view()

    def test_unaffected_ratified_member_is_bound_and_changed_bytes_refuse(self):
        member = self.ratified_member()
        batch = yaml.safe_load(self.f.path.read_bytes())
        batch["signed_dependencies"].append({"path": member.relative_to(self.root).as_posix(),
            "sha256": migration._digest(member.read_bytes())})
        self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False)); self.refresh_reviewed_batch()
        self.close(); self.publish()
        self.assertIn(member.relative_to(self.root).as_posix(),
            [item["path"] for item in self.view()["dependency_fingerprints"]])
        member.write_text(member.read_text().replace("Preserve output.", "Changed output."))
        sup.commit_all(self.root, "synthetic member drift")
        with self.assertRaisesRegex(migration.Refused, "signed-dependency-drift"): self.view()


class HistoricalDisplacements(PublicationProof):
    view = AuthorityView.view
    refresh_reviewed_batch = AuthorityView.refresh_reviewed_batch

    def test_exact_duplicate_edge_is_benign_but_conflicting_provenance_refuses(self):
        batch = migration.load_batch(self.root, self.f.path)
        entry = batch["entries"][0]; rule = entry["affected_governs"][0]
        rule["retires"] *= 2
        entry["source_identity"] = migration.source_identity(entry)
        source = self.root / "docs/adrs/ADR-0110-source.md"
        source.write_text("---\n" + yaml.safe_dump({"id": "ADR-0110", "status": "Accepted",
            "governs": [rule]}) + "---\n" + entry["clause"]["text"])
        self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False)); self.refresh_reviewed_batch()
        self.close(); self.publish()
        self.assertEqual(len(self.view()["historical_displacements"]), 1)

    def test_same_displacer_cannot_collapse_distinct_frozen_provenance(self):
        batch = migration.load_batch(self.root, self.f.path)
        first = batch["entries"][0]; first["disposition"] = "architecture-retained"
        other = copy.deepcopy(first); text = "Second synthetic frozen clause.\n"
        other["clause"] = {"text": text, "sha256": migration._digest(text.encode())}
        other["historical_destination"]["clause_sha256"] = other["clause"]["sha256"]
        other["source_identity"] = migration.source_identity(other)
        batch["entries"].append(other)
        source = self.root / "docs/adrs/ADR-0110-source.md"
        source.write_text(source.read_text() + text)
        self.f.path.write_text(yaml.safe_dump(batch, sort_keys=False)); self.refresh_reviewed_batch()
        self.close(); self.publish()
        with self.assertRaisesRegex(migration.Refused, "displacement-conflict"): self.view()

    def test_proven_selected_displacer_survives_archive_lifecycle(self):
        self.assertEqual(self.view()["historical_displacements"], [])
        self.close(); self.assertEqual(self.view()["historical_displacements"], [])
        publication = self.publish()
        before = self.view(); facts = before["historical_displacements"]
        self.assertEqual(len(facts), 1)
        fact = facts[0]
        entry = migration.load_batch(self.root, self.f.path)["entries"][0]
        self.assertEqual(fact, {"target_handle": "ADR-0109/old-rotation", "source_displacer": "ADR-0110/rotation",
            "source_identity": entry["source_identity"], "source_ref": {
                "path": "docs/adrs/ADR-0110-source.md", "source_adr": "ADR-0110",
                "clause_sha256": entry["clause"]["sha256"]},
            "publication_ref": {k: publication[k] for k in ("path", "sha256", "publication_commit")}})
        self.assertNotIn("Preserve assessments.", json.dumps(facts))
        source = self.root / fact["source_ref"]["path"]
        source.write_text(source.read_text().replace("status: Accepted", "status: Superseded\ndate: 2026-10-03"))
        archive = source.parent / "archive"; archive.mkdir(); source.rename(archive / source.name)
        sup.commit_all(self.root, "synthetic lifecycle fixture; source identity remains frozen")
        after = self.view(); later = after["historical_displacements"][0]
        self.assertEqual(later, {**fact, "source_ref": {**fact["source_ref"],
            "path": "docs/adrs/archive/ADR-0110-source.md"}})
        self.assertEqual(after["historical_handles"], before["historical_handles"])
        self.assertEqual(after["retired_handles"], before["retired_handles"])

    def test_changed_displacement_sources_and_conflicting_lane_refuse_whole_view(self):
        self.close(); self.publish(); self.view()
        baseline = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        for fault in ("retires", "clause", "missing", "duplicate", "lane"):
            with self.subTest(fault=fault):
                migration.cr.git(self.root, "reset", "--hard", baseline)
                source = self.root / "docs/adrs/ADR-0110-source.md"
                if fault == "retires": source.write_text(source.read_text().replace("ADR-0109/old-rotation", "ADR-0108/other"))
                elif fault == "clause": source.write_text(source.read_text().replace("inconsistent reports.", "changed reports."))
                elif fault == "missing": source.unlink()
                elif fault == "duplicate":
                    archive = source.parent / "archive"; archive.mkdir(exist_ok=True)
                    (archive / source.name).write_bytes(source.read_bytes())
                else:
                    (source.parent / "ADR-0997-conflict.md").write_text("---\n" + yaml.safe_dump({
                        "id": "ADR-0997", "status": "Accepted", "governs": [{"handle": "ADR-0997/conflict",
                            "rule": "Conflict.", "domain": "decision-review", "scope": "Assessment",
                            "retires": ["ADR-0110/rotation"]}]}) + "---\n")
                sup.commit_all(self.root, "synthetic displacement fault " + fault)
                with self.assertRaises(migration.Refused): self.view()


class ReceiptAuthority(PublicationProof):
    """Actual disposition writers and close; human inputs and publication are synthetic."""
    view = AuthorityView.view
    refresh_reviewed_batch = AuthorityView.refresh_reviewed_batch

    def setUp(self):
        PublicationProof.setUp(self)
        import doctrine_projection as dp
        import summaries_projection as sp
        self.batch = yaml.safe_load(self.f.path.read_bytes())
        self.entry = self.batch["entries"][0]
        self.old = self.entry["affected_governs"][0]
        self.old["anchor"] = "Synthetic span"
        self.entry["source_identity"] = migration.source_identity(self.entry)
        source = self.root / "docs/adrs/ADR-0110-source.md"
        source.write_text("---\n" + yaml.safe_dump({"id": "ADR-0110", "status": "Accepted",
            "governs": [self.old]}) + "---\n" + self.entry["clause"]["text"] + "Synthetic span\n")
        host = self.root / "docs/adrs/ADR-0146-authorizer.md"
        fm = yaml.safe_load(host.read_text().split("---")[1])
        # Canonical seed inspection validates every governs record, including the authorizer.
        for rule in fm["governs"]:
            rule.update(rule=rule.get("rule", "Authorize reviewed source-bound migration."),
                        domain="decision-review", scope="Assessment", provenance="authored")
        host.write_text("---\n" + yaml.safe_dump(fm) + "---\n")
        constraint = self.root / "docs/adrs/ADR-0001-constraint.md"
        constraint.write_text("---\n" + yaml.safe_dump({"id": "ADR-0001", "status": "Accepted",
            "governs": [{"handle": "ADR-0001/fixture-rule", "rule": "Preserve output.",
                "domain": "decision-review", "scope": "Assessment", "provenance": "authored"}]}) + "---\n")
        replacement = next(r for r in fm["governs"] if r["handle"] in self.entry["replacement_handles"])
        self.member = self.root / "docs/invariants/member.md"; self.member.parent.mkdir()
        self.member.write_text('---\nid: INV-0001\nratification: ratified\nrelated_adrs: [ADR-0110, ADR-0146]\n---\n## The invariant\nPreserve assessments.\n')
        self.ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
        self.rows = [{"invariant": "INV-0001", "handle": r["handle"], "verdict": "compatible",
            "signed": "2026-10-02", "rationale": None,
            "content_digest": dp.reconciliation_content_digest("Preserve assessments.", r["rule"])}
            for r in (self.old, replacement)]
        self.invariant = "INV-0001"
        self.ledger.write_text(yaml.safe_dump({"config_version": "1", "reconciliations": self.rows}))
        self.reviews = self.root / "docs/adrs/summaries/backfill-reviews.yml"; self.reviews.parent.mkdir()
        self.reviews.write_text(yaml.safe_dump({"config_version": "1", "batches": [{
            "id": "synthetic-admission", "signed": "2026-10-02", "handles": [self.old["handle"]], "no_rule": []}],
            "receipts": [{"handle": self.old["handle"], "digest": sp.content_digest(self.old),
                "verdict": "pass", "anchor": "Synthetic span", "batch": "synthetic-admission"}], "no_rule": []}))
        (self.root / "docs/manifest.yml").write_text(yaml.safe_dump({"adr": {"governs_from": 111,
            "governs_backfill_cohort": ["ADR-0110"], "governs_backfilled": {"ADR-0110": [self.old["handle"]]}}}))
        self.log = self.root / "docs/log.md"
        self.log.write_text("## [2026-10-02] backfill | synthetic-admission\n\nADRs: ADR-0110\n")
        self.journal = self.root / "docs/journal/2026-10.md"; self.journal.parent.mkdir()
        self.journal.write_text("## [2026-10-02 12:00] review | backfill sign-off synthetic-admission\n\nADRs: ADR-0110\n")
        self.batch["signed_dependencies"] = migration._signed_inventory(self.root, self.root / "docs")[0]
        self.save_batch()

    def save_batch(self):
        self.f.path.write_text(yaml.safe_dump(self.batch, sort_keys=False)); self.refresh_reviewed_batch()

    def produce_refs(self, batch, path):
        refs = []
        for script in ("signoff-reconciliation.py", "signoff-backfill.py"):
            args = [sys.executable, str(sup.SCRIPTS / script), "--migration-disposition", str(path),
                "--source-identity", self.entry["source_identity"], "--handle", self.old["handle"],
                "--repo-root", str(self.root), "--date", "2026-10-02", "--human-instruction",
                "Synthetic fixture: preserve the signed history and migrate this exact entry.",
                "--rationale", "Synthetic fixture: the signed replacement preserves the obligation."]
            if script == "signoff-reconciliation.py": args += ["--invariant", self.invariant]
            result = subprocess.run(args, capture_output=True, text=True, env=sup.scrubbed_env())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            published = json.loads(result.stdout)["receipt"]
            refs.append({k: published[k] for k in ("path", "sha256")})
        return refs

    def produce(self):
        self.batch["signed_dispositions"] = self.produce_refs(self.batch, self.f.path)
        self.save_batch()

    def test_actual_receipts_close_and_committed_synthetic_publication_validate(self):
        signed_bytes = {p: p.read_bytes() for p in (self.ledger, self.reviews, self.member)}
        self.produce()
        self.assertEqual(self.view()["state"], "original")
        self.close(); self.assertEqual(self.view()["state"], "original")
        self.publish()
        with mock.patch.object(migration.ap, "live_constraints", side_effect=AssertionError("recursion")):
            self.assertEqual(self.view()["state"], "published")
        self.assertEqual({p: p.read_bytes() for p in signed_bytes}, signed_bytes)
        for path in (self.log, self.journal): path.write_text(path.read_text() + "\nUnrelated append.\n")
        sup.commit_all(self.root, "synthetic unrelated corroboration append")
        self.assertEqual(self.view()["state"], "published")

    def test_corroboration_files_bind_only_before_publication(self):
        self.produce(); self.close()
        corroboration = {p.relative_to(self.root).as_posix() for p in (self.log, self.journal)}
        candidate = migration._validated_inputs(self.root, self.proof)
        self.assertTrue(corroboration <= {r["path"] for r in candidate["dependency_fingerprints"]})
        self.publish(); published = self.view()
        self.assertEqual(published["state"], "published")
        # A published reader binds no whole-file digest of the log or a journal month.
        self.assertFalse(corroboration & {r["path"] for r in published["dependency_fingerprints"]})
        for path in (self.log, self.journal): path.write_text(path.read_text() + "\nUnrelated append.\n")
        sup.commit_all(self.root, "synthetic corroboration growth after publication")
        self.assertEqual(self.view(), published)

    def test_receipt_semantic_mismatch_refuses_despite_exact_new_reference(self):
        for key, value in (("batch_id", "implementation-pilot-001-recovery-" + "0" * 64),
                ("entry_sha256", "0" * 64), ("source_identity", "0" * 64),
                ("producer", "backfill-signoff/migration-disposition-1"), ("human_instruction", " ")):
            with self.subTest(key=key):
                fixture = ReceiptAuthority(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
                fixture.produce(); ref = fixture.batch["signed_dispositions"][0]
                path = fixture.root / ref["path"]; changed = json.loads(path.read_text()); changed[key] = value
                raw = (json.dumps(changed, sort_keys=True, indent=2) + "\n").encode()
                altered = path.with_name(migration._digest(raw) + ".json"); path.unlink(); altered.write_bytes(raw)
                ref.update(path=altered.relative_to(fixture.root).as_posix(), sha256=migration._digest(raw))
                fixture.save_batch(); fixture.close(); fixture.publish()
                with self.assertRaises(migration.Refused): fixture.view()

    def test_missing_duplicate_and_unreferenced_conflicting_receipts_refuse(self):
        self.produce(); self.close(); self.publish()
        self.assertEqual(self.view()["state"], "published")
        reference = self.batch["signed_dispositions"][0]
        path = self.root / reference["path"]; original = path.read_bytes()
        path.unlink(); sup.commit_all(self.root, "synthetic missing receipt")
        with self.assertRaises(migration.Refused): self.view()
        path.write_bytes(original)
        changed = json.loads(original); changed["rationale"] = "Synthetic conflicting rationale."
        raw = (json.dumps(changed, sort_keys=True, indent=2) + "\n").encode()
        conflict = path.with_name(migration._digest(raw) + ".json"); conflict.write_bytes(raw)
        sup.commit_all(self.root, "synthetic unreferenced conflict")
        with self.assertRaisesRegex(migration.Refused, "subject-conflict"): self.view()

    def test_subject_pairing_and_replacement_drift_refuse_without_projection(self):
        self.produce(); self.close(); self.publish()
        import migration_disposition as md
        original = md._doctrine_subject
        for part in ("subject", "replacement"):
            def changed(*args):
                subject, replacements, detail = original(*args)
                if part == "subject": subject["row_sha256"] = "0" * 64
                else: replacements[0]["row_sha256"] = "0" * 64
                return subject, replacements, detail
            with mock.patch.object(md, "_doctrine_subject", side_effect=changed):
                with self.assertRaisesRegex(migration.Refused, "subject-refused"): self.view()

    def test_archive_lifecycle_preserves_seeded_receipt_and_retirement_edges(self):
        self.produce(); self.close(); self.publish()
        before = self.view()
        source = self.root / "docs/adrs/ADR-0110-source.md"
        source.write_text(source.read_text().replace("status: Accepted", "status: Superseded"))
        archive = source.parent / "archive"; archive.mkdir()
        source.rename(archive / source.name)
        sup.commit_all(self.root, "synthetic source lifecycle fixture, not native transition")
        after = self.view()
        self.assertEqual(after["historical_handles"], before["historical_handles"])
        self.assertEqual(after["retired_handles"], before["retired_handles"])


class RecoveryView(PublicationProof):
    view = AuthorityView.view
    def prepare_successor(self, batch, path):
        pass
    def successor(self, *, linked=True):
        """Second actual close under a distinct synthetic owner; earlier history stays untouched."""
        predecessor = migration._discover_publications(self.root)[0]
        ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
        ledger.write_text(ledger.read_text() + "# independently reviewed successor dependency\n")
        batch = yaml.safe_load(self.f.path.read_bytes())
        link = {"witness": {k: predecessor[k] for k in ("path", "sha256")},
            "batch": self.proof.binding["batch"],
            "source_identities": sorted(e["source_identity"] for e in batch["entries"])}
        batch["recovery_from"] = link
        batch["batch_id"] = "implementation-pilot-001-recovery-" + migration._canonical_digest(link)
        batch["signed_dependencies"][0]["sha256"] = migration._digest(ledger.read_bytes())
        if not linked:
            batch["recovery_from"]["witness"]["sha256"] = "0" * 64
            batch["batch_id"] = "implementation-pilot-001-recovery-" + migration._canonical_digest(link)
        batch_path = self.f.path.with_name(batch["batch_id"] + ".yaml")
        batch_path.write_text(yaml.safe_dump(batch, sort_keys=False))
        self.prepare_successor(batch, batch_path)
        book = book_two(); book["id"] = "PB-0998"
        declaration = book["implementation_slots"][0]
        declaration.update(scope=["docs/adrs/migrations"], constraint_refs=self.f.book["implementation_slots"][0]["constraint_refs"])
        declaration["migration_batch"] = {"role": "migration-batch", "path": batch_path.relative_to(self.root).as_posix()}
        book.update(current_run=None, current_prompt=None)
        book_path = self.root / "docs/promptbooks/active/PB-0998-fixture.yaml"
        run_path = self.root / "docs/promptbooks/runs/PB-0998-fixture/run-RUN-001.yaml"
        book_path.write_text(yaml.safe_dump(book, sort_keys=False))
        sup.commit_all(self.root, "synthetic separately bound successor declaration")
        started = subprocess.run([sys.executable, str(sup.SCRIPTS / "start-run.py"), str(book_path),
            "--run-id", "RUN-001", "--output", str(run_path)], capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        book.update(current_run="RUN-001", current_prompt=1); book_path.write_text(yaml.safe_dump(book, sort_keys=False))
        receipt = run_path.parent / "council/round.json"; receipt.parent.mkdir()
        retained = receipt.parent / "subjects/batch.yaml"; retained.parent.mkdir(); retained.write_bytes(batch_path.read_bytes())
        env = object.__new__(sup.Env); env.root = self.root; env.hash = sup.vp.compute_book_hash(book)
        doc = env.council_doc(module_tag="implementation-1", prompt=2, subjects=[{
            "path": batch_path.relative_to(self.root).as_posix(), "sha256": migration._digest(batch_path.read_bytes()),
            "retained_copy": retained.relative_to(self.root).as_posix()}])
        doc["book"]["id"] = book["id"]; receipt.write_text(json.dumps(doc))
        sup.commit_all(self.root, "synthetic exact successor council fixture after actual start")
        for n in range(1, 5):
            extra = ["--migration-batch", batch_path.relative_to(self.root).as_posix()] if n == 4 else []
            before = self.snapshot() if not linked and n == 4 else None
            advanced = subprocess.run([sys.executable, str(sup.SCRIPTS / "advance-run.py"), str(run_path),
                "--book", str(book_path), "--outcome", "done", "--artifacts", receipt.relative_to(self.root).as_posix(),
                *extra], capture_output=True, env=sup.scrubbed_env())
            if before is not None:
                self.assertEqual(advanced.returncode, 1, advanced.stdout + advanced.stderr)
                self.assertIn(b"migration-recovery-link-refused", advanced.stdout + advanced.stderr)
                self.assertEqual(self.snapshot(), before)
                return
            self.assertEqual(advanced.returncode, 0, advanced.stdout + advanced.stderr)
            sup.commit_all(self.root, "synthetic actual successor advance")
        proof = migration.ap.validate_historical_migration_binding(self.root, run_path, slot="implementation-1",
            batch_path=batch_path.relative_to(self.root).as_posix(), batch_sha256=migration._digest(batch_path.read_bytes()))
        locator = self.locator()
        locator.update({k: proof.binding[k] for k in ("batch", "book_id", "run_id", "book_content_hash", "slot", "gate_prompt")})
        locator.update(run_path=run_path.relative_to(self.root).as_posix(),
            binding_sha256=migration._canonical_digest(proof.binding), declaration_sha256=migration._canonical_digest(proof.slot))
        witness = batch_path.with_name(batch["batch_id"] + ".application.json")
        witness.write_text(json.dumps(locator, sort_keys=True) + "\n")
        sup.commit_all(self.root, "synthetic successor witness, not an apply producer")
        return proof

    def test_valid_source_bound_separately_closed_successor_replaces_stale_dependency_view(self):
        self.close(); self.publish(); old_receipt = self.f.receipt.read_bytes()
        historical = self.view()["historical_handles"]
        self.successor()
        view = self.view()
        self.assertEqual(len(view["publications"]), 2)
        self.assertEqual(view["historical_handles"], historical)
        self.assertEqual(self.f.receipt.read_bytes(), old_receipt)

    def test_unlinked_successor_refuses_whole_view(self):
        self.close(); self.publish(); self.successor(linked=False)
        with self.assertRaises(migration.Refused): self.view()


class ReceiptRecovery(PublicationProof):
    setUp = ReceiptAuthority.setUp
    refresh_reviewed_batch = AuthorityView.refresh_reviewed_batch
    save_batch = ReceiptAuthority.save_batch
    produce = ReceiptAuthority.produce
    produce_refs = ReceiptAuthority.produce_refs
    view = AuthorityView.view
    successor = RecoveryView.successor
    def prepare_successor(self, batch, path):
        batch["signed_dispositions"] = []
        path.write_text(yaml.safe_dump(batch, sort_keys=False))
        batch["signed_dispositions"] = self.produce_refs(batch, path)
        path.write_text(yaml.safe_dump(batch, sort_keys=False))

    def test_receipt_bound_successor_has_own_producers_and_close(self):
        self.produce(); self.close(); self.publish()
        original = {p: p.read_bytes() for p in (self.ledger, self.reviews, self.member)}
        old_receipts = {self.root / r["path"]: (self.root / r["path"]).read_bytes()
                        for r in self.batch["signed_dispositions"]}
        self.successor()
        self.assertEqual(self.view()["state"], "published")
        self.assertEqual(len(self.view()["publications"]), 2)
        self.assertEqual({p: p.read_bytes() for p in old_receipts}, old_receipts)
        self.assertEqual(self.reviews.read_bytes(), original[self.reviews])
        self.assertEqual(self.member.read_bytes(), original[self.member])


class ObservationReceipt(ReceiptRecovery):
    """A separate seeded observation fixture uses the same real receipt/close chain."""
    def setUp(self):
        super().setUp()
        import summaries_projection as sp
        self.member.unlink()
        obsdir = self.root / "docs/observations"; obsdir.mkdir()
        self.member = obsdir / "OBS-0001-assessments.md"
        self.member.write_text("---\n" + yaml.safe_dump({"id": "OBS-0001", "status": "ratified",
            "provenance": "recovered", "evidence": ["src/review.py:1-2"], "governs": [{
                "handle": "OBS-0001/assessments", "domain": "decision-review", "scope": "src",
                "rule": "Preserve assessments.", "provenance": "recovered"}]}) + "---\n")
        self.invariant = "OBS-0001/assessments"
        self.old["scope"] = "src/"
        source = self.root / "docs/adrs/ADR-0110-source.md"
        source.write_text("---\n" + yaml.safe_dump({"id": "ADR-0110", "status": "Accepted",
            "governs": [self.old]}) + "---\n" + self.entry["clause"]["text"] + "Synthetic span\n")
        host = self.root / "docs/adrs/ADR-0146-authorizer.md"
        fm = yaml.safe_load(host.read_text().split("---")[1])
        for rule in fm["governs"]:
            if rule["handle"] in self.entry["replacement_handles"]: rule["scope"] = "src/"
        host.write_text("---\n" + yaml.safe_dump(fm) + "---\n")
        for row in self.rows: row["invariant"] = self.invariant
        self.ledger.write_text(yaml.safe_dump({"config_version": "1", "reconciliations": self.rows}))
        reviews = yaml.safe_load(self.reviews.read_text())
        reviews["receipts"][0]["digest"] = sp.content_digest(self.old)
        self.reviews.write_text(yaml.safe_dump(reviews))
        manifest_path = self.root / "docs/manifest.yml"
        manifest = yaml.safe_load(manifest_path.read_text()); manifest["concerns_enabled"] = ["observations"]
        manifest_path.write_text(yaml.safe_dump(manifest))
        self.entry["source_identity"] = migration.source_identity(self.entry)
        self.batch["signed_dependencies"] = migration._signed_inventory(self.root, self.root / "docs")[0]
        self.save_batch()


class ArchitecturalReplacementRefs(unittest.TestCase):
    """A live Accepted entry that carries retires is itself a live replacement rule."""
    def setUp(self):
        self.f = PublicationProof(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        (self.root / "docs/adrs/ADR-0150-replacement.md").write_text("---\n" + yaml.safe_dump({
            "id": "ADR-0150", "status": "Accepted", "governs": [
                {"handle": "ADR-0150/new-rule", "retires": ["ADR-0150/old-rule"]},
                {"handle": "ADR-0150/old-rule"}]}) + "---\nA synthetic replacement.\n")

    def test_live_retiring_entry_is_declarable_and_its_retired_target_is_not(self):
        migration._architectural_refs(self.root, ["ADR-0150/new-rule"])
        with self.assertRaisesRegex(migration.Refused, "migration-constraint-not-live-accepted"):
            migration._architectural_refs(self.root, ["ADR-0150/old-rule"])
        with self.assertRaisesRegex(migration.Refused, "migration-constraint-not-live-accepted"):
            migration._architectural_refs(self.root, ["ADR-0150/new-rule", "ADR-0150/old-rule"])


class RevisionEnvironment(unittest.TestCase):
    """Git children of a migration read inherit no gateway key and no global config."""
    def test_revision_and_history_children_drop_secret_names(self):
        names = {"OPENROUTER_API_KEY": "synthetic-not-a-key", "CRUX_SYNTHETIC_ENV_NAME": "synthetic"}
        with mock.patch.dict(os.environ, names), mock.patch.object(
                migration.cr, "_secret_names", return_value=set(names)):
            for env in (migration._revision_environment(), migration._history_env()):
                self.assertFalse(set(names) & set(env))
                self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
                self.assertEqual(env["GIT_CONFIG_SYSTEM"], os.devnull)

    def test_batched_object_reads_run_under_the_history_environment(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = sup.init_repo(Path(temp.name) / "repo")
        (root / "a.yml").write_text("a: 1\n"); sup.commit_all(root, "synthetic one file")
        seen = []; original = subprocess.run
        def capture(args, **kwargs):
            if args[:1] == ["git"] and "cat-file" in args: seen.append(kwargs.get("env"))
            return original(args, **kwargs)
        migration._OBJECT_MEMO.clear()
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-not-a-key"}), \
             mock.patch.object(subprocess, "run", side_effect=capture):
            migration._InvocationHistory(root).metadata(("a.yml",))
        self.assertTrue(seen)
        for env in seen:
            self.assertNotIn("OPENROUTER_API_KEY", env)
            self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)


class PublicationDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = sup.init_repo(Path(self.temp.name) / "repo")
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: knowledge\n')
        self.directory = self.root / "knowledge/adrs/migrations"
        self.directory.mkdir(parents=True)
        sup.commit_all(self.root, "synthetic configured original authority")
        self.base = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        self.witness = self.directory / "implementation-pilot-001.application.json"

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}

    def discover(self):
        before = self.snapshot()
        try:
            return migration._discover_publications(self.root)
        finally:
            self.assertEqual(self.snapshot(), before)

    def publish(self):
        self.witness.write_text('{"synthetic": "discovery only"}\n')
        sup.commit_all(self.root, "synthetic manually committed locator")

    def test_never_published_lineage_and_committed_discovery_are_distinct(self):
        self.assertEqual(self.discover(), [])
        self.publish()
        rows = self.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], self.witness.relative_to(self.root).as_posix())
        self.assertEqual(rows[0]["content"], self.witness.read_bytes())
        migration.cr.git(self.root, "checkout", "-b", "never-published", self.base)
        self.assertEqual(self.discover(), [])

    def test_immutable_configuration_metadata_is_batched_and_memoized_per_call(self):
        for n in range(8):
            migration.cr.git(self.root, "commit", "--allow-empty", "-m", "synthetic same config " + str(n))
        commits = int(migration.cr.git(self.root, "rev-list", "--count", "HEAD").stdout)
        original = subprocess.run; calls = []; loader = migration.bionic_config.load_config
        def counted(args, **kwargs):
            if args[0] == "git" and "-C" in args: calls.append(args[args.index("-C") + 2:])
            return original(args, **kwargs)
        with mock.patch.object(subprocess, "run", side_effect=counted), \
             mock.patch.object(migration.bionic_config, "load_config", wraps=loader) as configs:
            self.assertEqual(self.discover(), [])
            self.assertLessEqual(sum(c[0] == "ls-tree" for c in calls), commits)
            # Current HEAD/index safety checks remain fresh, outside immutable batching.
            self.assertLessEqual(sum(c[0] == "cat-file" for c in calls), 2 + 2 * len(migration._config_inputs()))
            # Commits, two tree levels and the blobs: at most one check/body pair each,
            # however many commits the lineage holds. No per-commit ls-tree remains.
            self.assertLessEqual(sum(c[0] == "cat-file" and c[1].startswith("--batch") for c in calls), 8)
            self.assertEqual(sum(c[0] == "ls-tree" for c in calls), 0)
            self.assertLessEqual(configs.call_count, 3)
        # A fresh call must read a new commit and cannot reuse an activation cache.
        self.publish()
        self.assertEqual(len(self.discover()), 1)

    def test_batched_metadata_equals_per_commit_ls_tree(self):
        """Every mode and shape ls-tree reports, read from batched objects, cold and warm."""
        files = {"a.yml": "one\n", "knowledge/manifest.yml": "two\n", "knowledge/x/deep.yml": "three\n"}
        for rel, text in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True); (self.root / rel).write_text(text)
        sup.commit_all(self.root, "synthetic regular files")
        os.chmod(self.root / "a.yml", 0o755); sup.commit_all(self.root, "synthetic executable")
        (self.root / "knowledge/manifest.yml").unlink(); (self.root / "knowledge/manifest.yml").mkdir()
        (self.root / "knowledge/manifest.yml/inner").write_text("dir\n"); sup.commit_all(self.root, "synthetic directory")
        paths = ("a.yml", "knowledge/manifest.yml", "knowledge/x/deep.yml", "absent/never.yml")
        history = migration._InvocationHistory(self.root)
        expected = {}
        for commit in history.commits:
            rows = migration.cr.git(self.root, "ls-tree", "-z", commit, "--", *paths).stdout.split(b"\0")
            expected[commit] = {row.split(b"\t", 1)[1].decode(): tuple(row.split(b"\t", 1)[0].split())
                                for row in rows if row}
        # The directory named like a path refuses, exactly as the per-commit ls-tree read did.
        self.assertEqual(expected[history.commits[-1]]["knowledge/manifest.yml"][1], b"tree")
        with self.assertRaisesRegex(migration.Refused, "migration-config-history-refused"):
            history.metadata(paths)
        regular = ("a.yml", "knowledge/x/deep.yml", "absent/never.yml")
        for warm in (False, True):
            if not warm: migration._OBJECT_MEMO.clear()
            observed = migration._InvocationHistory(self.root).metadata(regular)
            self.assertEqual(observed, {commit: {rel: (mode.decode(), oid.decode())
                for rel, (mode, kind, oid) in rows.items() if rel in regular}
                for commit, rows in expected.items()})
        # An executable bit and a regular file read as distinct canonical modes.
        self.assertEqual({row[0] for rows in observed.values() for row in rows.values()}, {"100644", "100755"})

    @staticmethod
    def alias_chain(levels, width=10):
        """A locator whose nested aliases expand to width ** levels nodes."""
        lines = ["l0: &l0 [x]"]
        lines += [f"l{i}: &l{i} [" + ", ".join([f"*l{i-1}"] * width) + "]" for i in range(1, levels + 1)]
        return "\n".join(lines) + "\n"

    def test_committed_small_alias_locator_passes_the_bounded_parse(self):
        # Positive control: the same anchored grammar at a small expansion parses, and
        # the locator then refuses on its shape, after the bounded parse admitted it.
        self.witness.write_text(self.alias_chain(2)); sup.commit_all(self.root, "synthetic small aliases")
        with self.assertRaisesRegex(migration.Refused, "migration-publication-shape-refused"):
            migration.authority_view(self.root)

    def test_committed_alias_chain_locator_refuses_fast_on_the_git_path(self):
        import time
        # Ten levels expand to 10 ** 10 nodes. The unmemoized duplicate walk never finishes.
        self.witness.write_text(self.alias_chain(10)); sup.commit_all(self.root, "synthetic alias chain")
        self.assertEqual([row["path"] for row in self.discover()], [self.witness.relative_to(self.root).as_posix()])
        started = time.monotonic()
        with self.assertRaisesRegex(migration.Refused, "migration-yaml-expansion-refused"):
            migration.authority_view(self.root)
        self.assertLess(time.monotonic() - started, 10)

    def test_receipt_parser_refuses_an_alias_chain_fast(self):
        import time
        import migration_disposition
        started = time.monotonic()
        with self.assertRaisesRegex(migration.Refused, "migration-disposition-parse-refused"):
            migration_disposition.parse_receipt(self.alias_chain(10))
        self.assertLess(time.monotonic() - started, 5)

    def test_head_change_during_discovery_refuses_without_reader_writes(self):
        original = migration.cr.git; changed = False
        def moved(repo, *args, **kwargs):
            nonlocal changed
            result = original(repo, *args, **kwargs)
            if args[0] == "rev-list" and not changed:
                changed = True
                original(repo, "commit", "--allow-empty", "-m", "synthetic concurrent head movement")
            return result
        with mock.patch.object(migration.cr, "git", side_effect=moved):
            with self.assertRaisesRegex(migration.Refused, "history-head-changed"): self.discover()

    def test_uncommitted_and_dirty_publication_refuse(self):
        self.witness.write_text('{}\n')
        with self.assertRaises(migration.Refused): self.discover()
        self.publish(); self.witness.write_text('{"different": true}\n')
        with self.assertRaises(migration.Refused): self.discover()

    def test_publication_deletion_rename_and_committed_edit_refuse(self):
        self.publish()
        head = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        for fault in ("delete", "rename", "edit"):
            with self.subTest(fault=fault):
                migration.cr.git(self.root, "reset", "--hard", head)
                if fault == "delete": self.witness.unlink()
                elif fault == "rename": self.witness.rename(self.directory / "renamed.json")
                else: self.witness.write_text('{"edited": true}\n')
                sup.commit_all(self.root, "synthetic descendant publication fault")
                with self.assertRaises(migration.Refused): self.discover()
                (self.directory / "renamed.json").unlink(missing_ok=True)

    def test_full_history_required_before_never_published_classification(self):
        shallow = self.root / ".git/shallow"; shallow.write_text(self.base + "\n")
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"): self.discover()

    def test_changed_configured_tree_cannot_hide_prior_publication(self):
        self.publish()
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: renamed\n')
        sup.commit_all(self.root, "synthetic docs root change after publication")
        with self.assertRaises(migration.Refused): self.discover()

    def test_canonical_supported_configuration_modes_retain_never_published_authority(self):
        for mode in ("absent", "legacy", "keyless", "explicit"):
            with self.subTest(mode=mode):
                root = sup.init_repo(Path(self.temp.name) / mode)
                docs = root / "docs"; docs.mkdir()
                (docs / "manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: []\n')
                if mode == "legacy":
                    (root / ".crux").write_text('config_version: "1"\ndocs_dir: docs\n')
                elif mode == "keyless":
                    (root / ".bionic.yml").write_text('config_version: "1"\n')
                elif mode == "explicit":
                    (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
                sup.commit_all(root, "synthetic canonical supported configuration")
                self.assertEqual(migration.bionic_config.load_config(root).docs_dir, "docs")
                before = {p.relative_to(root).as_posix(): p.read_bytes()
                    for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}
                self.assertEqual(migration.authority_view(root)["state"], "original")
                self.assertEqual({p.relative_to(root).as_posix(): p.read_bytes()
                    for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}, before)

    def test_supported_config_transitions_cannot_hide_previous_publication(self):
        self.publish()
        head = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        for mode in ("absent", "legacy", "keyless", "explicit"):
            with self.subTest(mode=mode):
                migration.cr.git(self.root, "reset", "--hard", head)
                (self.root / ".crux").unlink(missing_ok=True)
                config = self.root / ".bionic.yml"; config.unlink()
                docs = self.root / "docs"; docs.mkdir(exist_ok=True)
                (docs / "manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: []\n')
                if mode == "legacy":
                    (self.root / ".crux").write_text('config_version: "1"\ndocs_dir: docs\n')
                elif mode == "keyless": config.write_text('config_version: "1"\n')
                elif mode == "explicit": config.write_text('config_version: "1"\ndocs_dir: docs\n')
                sup.commit_all(self.root, "synthetic supported config transition after publication")
                self.assertEqual(migration.bionic_config.load_config(self.root).docs_dir, "docs")
                with self.assertRaises(migration.Refused): self.discover()

    def test_publication_from_each_canonical_mode_remains_visible_after_tree_transition(self):
        for mode in ("absent", "legacy", "keyless", "explicit"):
            with self.subTest(mode=mode):
                root = sup.init_repo(Path(self.temp.name) / ("historical-" + mode))
                folder = root / "docs/adrs/migrations"; folder.mkdir(parents=True)
                (root / "docs/manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: []\n')
                if mode == "legacy":
                    (root / ".crux").write_text('config_version: "1"\ndocs_dir: docs\n')
                elif mode == "keyless":
                    (root / ".bionic.yml").write_text('config_version: "1"\n')
                elif mode == "explicit":
                    (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
                (folder / "implementation-pilot-001.application.json").write_text('{}\n')
                sup.commit_all(root, "synthetic publication under supported configuration")
                self.assertEqual(len(migration._discover_publications(root)), 1)
                (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: renamed\n')
                sup.commit_all(root, "synthetic later explicit tree transition")
                before = {p.relative_to(root).as_posix(): p.read_bytes()
                    for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}
                with self.assertRaises(migration.Refused): migration.authority_view(root)
                self.assertEqual({p.relative_to(root).as_posix(): p.read_bytes()
                    for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}, before)


if __name__ == "__main__":
    unittest.main()
