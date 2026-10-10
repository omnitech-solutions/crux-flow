"""Allocation counters cannot change authority or break a published survey resume."""
import copy
import importlib.util
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml
import implementation_migration as migration
import _council_gate_support as sup

SCRIPTS = Path(migration.__file__).parent


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts}


class CounterCommitment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = sup.init_repo(Path(self.temp.name) / "repo")
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        self.path = self.root / "docs/manifest.yml"; self.path.parent.mkdir()
        self.base = {"schema_version": 5, "concerns_enabled": ["adrs", "observations"],
                     "observation": {"next_number": 1, "next_survey_number": 1},
                     "adr": {"next_number": 1}, "promptbook": {"next_number": 1},
                     "backfill": {"signed_batches": ["fixed"]}, "exact_type": 1}
        self.save(self.base); sup.commit_all(self.root, "synthetic canonical manifest")
        self.head = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()

    def save(self, document): self.path.write_text(yaml.safe_dump(document, sort_keys=False))

    def read(self):
        before = snapshot(self.root)
        try: return migration.authority_view(self.root)
        finally: self.assertEqual(snapshot(self.root), before)

    def test_only_four_monotonic_leaves_in_staged_and_worktree_versions_pass(self):
        staged = copy.deepcopy(self.base); work = copy.deepcopy(self.base)
        for block, key in (("adr", "next_number"), ("promptbook", "next_number"),
                           ("observation", "next_number"), ("observation", "next_survey_number")):
            staged[block][key] = 2; work[block][key] = 3
        self.save(staged); migration.cr.git(self.root, "add", "docs/manifest.yml")
        self.save(work)
        self.assertEqual(self.read()["state"], "original")
        current = migration._digest(self.path.read_bytes())
        before = snapshot(self.root)
        migration._unchanged_inputs(self.root, [{"path": "docs/manifest.yml", "sha256": current}])
        self.assertEqual(snapshot(self.root), before)
        with self.assertRaises(migration.Refused):
            migration._unchanged_inputs(self.root, [{"path": "docs/manifest.yml", "sha256":
                migration.cr.path_state(self.root, "docs/manifest.yml").head}])

    def test_missing_default_one_and_counter_only_new_parent_pass(self):
        self.path.write_text("schema_version: 5\nconcerns_enabled: [adrs]\n")
        sup.commit_all(self.root, "synthetic absent documented allocation defaults")
        self.path.write_text(self.path.read_text() + "observation:\n  next_number: 1\n  next_survey_number: 2\n")
        self.assertEqual(self.read()["state"], "original")

    def test_authority_fields_bad_counter_shapes_removal_and_index_reversal_refuse(self):
        faults = []
        for key, value in (("schema_version", "5"), ("concerns_enabled", ["adrs"]),
                           ("backfill", {"signed_batches": []}), ("exact_type", True),
                           ("unknown", "new authority"), ("brief", {"next_number": 2})):
            doc = copy.deepcopy(self.base); doc[key] = value; faults.append(doc)
        for value in (0, -1, True, "2", 2.0):
            doc = copy.deepcopy(self.base); doc["observation"]["next_number"] = value; faults.append(doc)
        for field in ("next_number", "next_survey_number"):
            doc = copy.deepcopy(self.base); del doc["observation"][field]; faults.append(doc)
        doc = copy.deepcopy(self.base); del doc["observation"]; faults.append(doc)
        for value in (None, "not a counter parent", [], {"next_number": 2, "new_sibling": True}):
            doc = copy.deepcopy(self.base); doc["observation"] = value; faults.append(doc)
        for n, doc in enumerate(faults):
            with self.subTest(fault=n):
                self.save(doc)
                with self.assertRaises(migration.Refused): self.read()
        staged = copy.deepcopy(self.base); staged["observation"]["next_number"] = 3
        self.save(staged); migration.cr.git(self.root, "add", "docs/manifest.yml")
        self.save(self.base)
        with self.assertRaises(migration.Refused): self.read()

    def test_malformed_duplicates_layout_and_untracked_manifest_refuse(self):
        for content in ("{broken", "schema_version: 5\nschema_version: 5\nconcerns_enabled: [adrs]\n"):
            self.path.write_text(content)
            with self.assertRaises(migration.Refused): self.read()
        self.save(self.base)
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: alternate\n')
        with self.assertRaises(migration.Refused): self.read()
        migration.cr.git(self.root, "reset", "--hard", self.head)
        migration.cr.git(self.root, "rm", "--cached", "docs/manifest.yml")
        with self.assertRaises(migration.Refused): self.read()

    def test_unsafe_head_index_and_changed_textual_qualification_refuse(self):
        for place in ("head", "index"):
            with self.subTest(place=place):
                migration.cr.git(self.root, "reset", "--hard", self.head)
                self.path.write_text("schema_version: 5\nschema_version: 5\nconcerns_enabled: [adrs]\n")
                migration.cr.git(self.root, "add", "docs/manifest.yml")
                if place == "head": sup.commit_all(self.root, "synthetic malformed committed manifest")
                self.save(self.base)
                with self.assertRaises(migration.Refused): self.read()
        migration.cr.git(self.root, "reset", "--hard", self.head)
        doc = copy.deepcopy(self.base); doc["observation"]["next_number"] = 2
        self.path.write_text(yaml.safe_dump(doc, default_flow_style=True, width=10000))
        with self.assertRaises(migration.Refused): self.read()

    def test_current_raw_operation_fingerprint_stays_exact_after_counter_change(self):
        import observation_admission as admission
        context = admission._load_context(self.root)
        doc = copy.deepcopy(self.base); doc["observation"]["next_number"] = 2; self.save(doc)
        self.assertEqual(self.read()["state"], "original")
        before = snapshot(self.root)
        with self.assertRaises(ValueError): admission._revalidate(context)
        self.assertEqual(snapshot(self.root), before)

    def test_canonical_publication_discriminator_constant_preserves_value(self):
        self.assertEqual(migration.PUBLICATION_RECORD_TYPE, "implementation-migration-publication")


class PublishedSurveyCounters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_observation_migration_admission as fixtures
        fixtures.PublishedAdmission.setUpClass()
        cls.addClassCleanup(fixtures.PublishedAdmission.doClassCleanups)
        cls.template = fixtures.PublishedAdmission.root

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        shutil.copytree(self.template, self.root)
        self.tree = self.root / "docs"
        (self.tree / "log.md").write_text("# Log\n")
        (self.root / "factual.py").write_text("x = 1\n")
        from crux.arch.recover import StateFile, candidate_id
        state = StateFile(self.tree / "arch/_recovered/state.yml")
        cid = candidate_id("external-dependency", "synthetic")
        state.rows[cid] = dict(id=cid, anchor_kind="external-dependency", canonical_anchor="synthetic",
            rule="The source assigns one.", evidence=["factual.py:1-1"], domain="testing", state="observed")
        state.save(); self.before_view = migration.authority_view(self.root)

    def cli(self, name, *extra):
        return subprocess.run([sys.executable, str(SCRIPTS / (name + ".py")),
            "--repo-root", str(self.root), "--date", "2026-08-30", *extra],
            cwd=SCRIPTS.parents[1], env=sup.scrubbed_env(), capture_output=True, text=True)

    def prepare(self):
        result = self.cli("scaffold-survey-sheet")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        batch = json.loads(result.stdout)["batch_id"]
        import survey_sheet as ss
        path = self.tree / "observations" / ("survey-" + batch + ".yml")
        sheet = ss.read_sheet(path)
        for row in sheet["rows"]:
            row.update(verdict="ratify", domain="testing", rationale="Synthetic human fixture only.")
        ss.write_sheet(path, sheet, contained_under=self.tree)
        return batch

    def assert_authority(self):
        after = migration.authority_view(self.root)
        for key in ("state", "historical_handles", "reserved_slugs", "source_refs", "publications"):
            self.assertEqual(after[key], self.before_view[key])

    def assert_cli_success(self, result):
        if result.returncode:
            import observation_admission as admission
            try: admission._load_context(self.root)
            except Exception as error:
                self.fail(result.stdout + result.stderr + "\nCONTEXT_BOUNDARY " + str(error))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_published_scaffold_then_signoff_without_intermediate_commit(self):
        batch = self.prepare(); result = self.cli("signoff-survey", "--batch", batch)
        self.assert_cli_success(result)
        self.assertIn('"state": "S9"', result.stdout)
        self.assert_authority()

    def test_actual_published_s1_allocation_then_fresh_context_resume(self):
        batch = self.prepare()
        spec = importlib.util.spec_from_file_location("counter_signoff", SCRIPTS / "signoff-survey.py")
        signoff = importlib.util.module_from_spec(spec); spec.loader.exec_module(signoff)
        ctx = signoff.make_context(self.root, None, batch, "2026-08-30")
        self.assertEqual(signoff.advance(ctx)[0], "S1")
        self.assertEqual(signoff.advance(ctx)[0], "S2")
        self.assertIn("next_number: 2", (self.tree / "manifest.yml").read_text())
        result = self.cli("signoff-survey", "--batch", batch)
        self.assert_cli_success(result)
        self.assertIn('"state": "S9"', result.stdout)
        self.assert_authority()


if __name__ == "__main__": unittest.main()
