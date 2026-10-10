"""Original-only projects never inherit migration history from a parent repository."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import implementation_migration as migration

SCRIPTS = Path(migration.__file__).parent


def driver(name):
    spec = importlib.util.spec_from_file_location("no_git_" + name.replace("-", "_"), SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory(root):
    return {p.relative_to(root).as_posix(): ("link", os.readlink(p)) if p.is_symlink()
            else ("file", p.read_bytes()) for p in root.rglob("*") if p.is_file() or p.is_symlink()}


class NoGitAuthority(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "bionic"
        (self.tree / "adrs").mkdir(parents=True)
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n')
        (self.tree / "manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: [adrs]\n')
        (self.tree / "adrs/ADR-0001-one.md").write_text('''---
id: ADR-0001
title: Ordinary architecture
status: Accepted
date: 2026-01-01
governs:
- handle: ADR-0001/ordinary
  domain: ordinary
  rule: Preserve the ordinary rule.
  scope: repo
  provenance: authored
---
Ordinary source.
''')

    def read(self, **kwargs):
        before = inventory(self.root)
        try:
            return migration.authority_view(self.root, **kwargs)
        finally:
            self.assertEqual(inventory(self.root), before)

    def refuse(self, code=None, **kwargs):
        with self.assertRaises(migration.Refused) as caught:
            self.read(**kwargs)
        if code is not None:
            self.assertEqual(caught.exception.code, code)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_original_project_and_empty_evidence_directories(self):
        for path in ("adrs/migrations", "adrs/migrations/dispositions", "promptbooks/runs"):
            (self.tree / path).mkdir(parents=True, exist_ok=True)
        with patch.object(migration.cr, "git", side_effect=AssertionError("no ancestor Git query")):
            view = self.read()
        self.assertEqual(view["state"], "original")
        self.assertEqual(view["publications"], [])
        self.assertEqual(view["historical_handles"], {})
        self.assertEqual(view["historical_displacements"], [])

    def test_minimal_config_and_legacy_layout_modes(self):
        configs = [('.bionic.yml', 'docs_dir: bionic\n'), ('.crux', 'docs_dir: bionic\n'),
                   ('.bionic.yml', 'config_version: "1"\n'), (None, None)]
        for name, content in configs:
            with self.subTest(config=name, content=content):
                for old in (".bionic.yml", ".crux"):
                    (self.root / old).unlink(missing_ok=True)
                if name: self.write(name, content)
                self.assertEqual(self.read()["state"], "original")
        self.tree.rename(self.root / "docs")
        self.assertEqual(self.read()["state"], "original")

    def test_real_summary_build_uses_authority_admission(self):
        summarize = driver("summarize-adrs")
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n")
        before = inventory(self.root)
        with patch.object(migration, "authority_view", wraps=migration.authority_view) as reader:
            wanted = summarize.build(self.root)
        reader.assert_called_once_with(self.root)
        self.assertEqual(len(wanted), 4)
        resolver = json.loads(wanted[self.tree / "adrs/summaries/resolver.json"])
        self.assertIn("ordinary", resolver["slugs"])
        self.assertEqual(inventory(self.root), before)

    def test_nested_project_never_borrows_parent_git_or_evidence(self):
        parent = self.root / "parent"
        parent.mkdir()
        result = migration.cr.git(parent, "init", "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        (parent / "parent.application.json").write_text("{}")
        nested = parent / "child"
        nested.mkdir()
        (nested / "bionic").mkdir()
        original = self.root
        self.root = nested
        try:
            with patch.object(migration.cr, "git", side_effect=AssertionError("must not borrow parent")):
                self.assertEqual(self.read()["state"], "original")
        finally:
            self.root = original

    def test_broken_local_git_marker_does_not_fall_back(self):
        self.write(".git", "gitdir: missing\n")
        self.refuse()

    def test_local_git_missing_objects_is_not_original_only(self):
        self.assertEqual(migration.cr.git(self.root, "init", "--quiet").returncode, 0)
        self.assertEqual(migration.cr.git(self.root, "add", ".").returncode, 0)
        result = migration.cr.git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                  "commit", "--quiet", "-m", "fixture")
        self.assertEqual(result.returncode, 0, result.stderr)
        head = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        (self.root / ".git/objects" / head[:2] / head[2:]).unlink()
        self.refuse("migration-config-not-committed")

    def test_revision_and_apply_still_refuse_without_git(self):
        with patch.object(migration, "_read_revision", side_effect=AssertionError("no borrowed revision")):
            self.refuse("migration-git-required", revision="HEAD")
        apply = driver("implementation-migration")
        before = inventory(self.root)
        output = io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(output):
            rc = apply.main(["apply", "--batch", "absent.yaml", "--repo-root", str(self.root)])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(output.getvalue()), {"authority": "none", "limit": "history-unavailable"})
        self.assertEqual(inventory(self.root), before)

    def test_publication_and_nonempty_evidence_footprints_refuse(self):
        for rel in ("bionic/adrs/migrations/pilot.yaml", "bionic/adrs/migrations/dispositions/receipt.json",
                    "bionic/adrs/migrations/pilot.application.json"):
            with self.subTest(path=rel):
                self.write(rel, "{}")
                self.refuse("migration-git-required")
                (self.root / rel).unlink()

    def test_other_explicit_layout_or_qualifying_crux_tree_cannot_hide_evidence(self):
        self.write(".crux", "docs_dir: old/custom\n")
        self.write("old/custom/adrs/migrations/pilot.application.json", "{}")
        self.refuse("migration-git-required")
        (self.root / ".crux").unlink()
        self.write("docs/manifest.yml", 'schema_version: "5"\nconcerns_enabled: [adrs]\n')
        self.write("docs/adrs/migrations/pilot.application.json", "{}")
        self.refuse("migration-git-required")

    def test_unrelated_vendor_fixtures_and_symlinks_do_not_supply_authority_inputs(self):
        self.write("vendor/fixture/adrs/migrations/pilot.application.json", "{}")
        self.write("docs/generic.txt", "Ordinary unrelated documentation")
        self.write("docs/adrs/migrations/fixture.json", "{}")
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "vendor/external").symlink_to(outside, target_is_directory=True)
            (self.tree / "promptbooks/runs").mkdir(parents=True)
            (self.tree / "promptbooks/runs/vendor").symlink_to(outside, target_is_directory=True)
            self.assertEqual(self.read()["state"], "original")
            (self.root / "docs").rename(self.root / "generic-docs")
            (self.root / "docs").symlink_to(outside, target_is_directory=True)
            self.assertEqual(self.read()["state"], "original")

    def test_missing_declared_locator_and_canonical_nested_run_bindings_refuse(self):
        self.write("bionic/promptbooks/runs/PB-0001-one/run-RUN-001.yaml",
                   'migration_bindings: [{batch: {path: absent.yaml}}]\n')
        self.refuse("migration-git-required")

    def test_surviving_project_tree_reference_refuses_after_evidence_move(self):
        self.write(".crux", "docs_dir: moved\n")
        self.write("moved/adrs/summaries/resolver.json", '{"historical_clauses": [{"source_identity": "old"}]}')
        self.refuse("migration-git-required")
        (self.root / "moved").rename(self.root / "renamed")
        self.refuse("migration-layout-refused")

    def test_declaration_and_recorded_history_refuse_without_git(self):
        cases = [
            ("bionic/promptbooks/active/PB-0001-one.yaml", {"approval_slots": [{"migration_batch": "batch.yaml"}]}),
            ("bionic/promptbooks/runs/run.yaml", {"migration_bindings": [{"batch": {"path": "batch.yaml"}}]}),
            ("bionic/manifest.yml", {"migration_inputs": ["batch.yaml"]}),
            ("bionic/adrs/summaries/_meta.json", {"input_domain": ["adrs", "migration"]}),
            ("bionic/adrs/summaries/resolver.json", {"historical_slugs": {"old": {"source_handle": "ADR-0001/old"}}}),
            ("bionic/adrs/summaries/resolver.json", {"historical_displacements": {"ADR-0001/old": []}}),
            ("bionic/adrs/doctrine/_meta.json", {"migration_sha256": "0" * 64}),
        ]
        for rel, doc in cases:
            with self.subTest(path=rel, doc=doc):
                prior = (self.root / rel).read_bytes() if (self.root / rel).exists() else None
                self.write(rel, json.dumps(doc))
                self.refuse("migration-git-required")
                if prior is None: (self.root / rel).unlink()
                else: (self.root / rel).write_bytes(prior)

    def test_empty_new_projection_maps_do_not_claim_migration(self):
        self.write("bionic/adrs/summaries/resolver.json", json.dumps({"historical_slugs": {}, "historical_displacements": {}}))
        self.assertEqual(self.read()["state"], "original")

    def test_malformed_or_escaping_layout_and_ambiguous_footprints_refuse(self):
        for text in ("docs_dir: ../outside\n", "docs_dir: /tmp\n", "docs_dir: .git\n",
                     "config_version: 1\ndocs_dir: bionic\n", "docs_dir: [bionic]\n",
                     "docs_dir: bionic\ndocs_dir: other\n", "not: [valid\n"):
            with self.subTest(config=text):
                self.write(".bionic.yml", text)
                self.refuse()
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: bionic\n')
        self.write("bionic/adrs/summaries/resolver.json", "{")
        self.refuse()

    def test_symlink_layout_or_evidence_cannot_hide_footprints(self):
        with tempfile.TemporaryDirectory() as outside:
            self.write(".bionic.yml", "docs_dir: link\n")
            (self.root / "link").symlink_to(outside, target_is_directory=True)
            self.refuse()
            (self.root / "link").unlink()
            self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: bionic\n')
            (self.tree / "adrs/migrations").symlink_to(outside, target_is_directory=True)
            self.refuse()

    def test_unreadable_projection_refuses_without_changes(self):
        self.write("bionic/adrs/summaries/_meta.json", "{}")
        original = migration._read
        def blocked(repo, given):
            if str(given).endswith("_meta.json"):
                raise migration.Refused("migration-source-unreadable")
            return original(repo, given)
        with patch.object(migration, "_read", side_effect=blocked):
            self.refuse("migration-source-unreadable")

    def test_recursive_declaration_is_ambiguous_not_original(self):
        self.write("bionic/promptbooks/active/PB-0001-one.yaml", "cycle: &row {repeat: *row}\n")
        self.refuse("migration-footprint-ambiguous")

    def test_existing_migration_marker_selects_its_contained_source(self):
        self.write("bionic/.migrating", "source: old\nstep: 1\ninventory:\n")
        self.write("old/adrs/summaries/resolver.json", '{"historical_slugs": {"old": {}}}')
        self.refuse("migration-git-required")

    def _plain_damage_regenerates_from_source(self, filename):
        summarize = driver("summarize-adrs")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(summarize.main(["--repo-root", str(self.root)]), 0)
        path = self.tree / "adrs/summaries" / filename
        baseline = path.read_text()
        for tail in ("ordinary words\n", "scratch_note 42-item\n", "changed.bytes\tlog_item \t\n", "tampered\n"):
            with self.subTest(filename=filename, tail=tail):
                path.write_text(baseline + "\n" + tail)
                self.assertEqual(self.read()["state"], "original")
                before = inventory(self.root)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(summarize.main(["--repo-root", str(self.root), "--dry-run"]), 1)
                self.assertIn(str(path.relative_to(self.root)), json.loads(output.getvalue())["paths"])
                self.assertEqual(inventory(self.root), before)
                source = self.tree / "adrs/ADR-0001-one.md"
                source.write_text(source.read_text().replace("Preserve the ordinary rule.", "Preserve the revised source rule."))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(summarize.main(["--repo-root", str(self.root)]), 0)
                    self.assertEqual(summarize.main(["--repo-root", str(self.root), "--dry-run"]), 0)
                repaired = path.read_text()
                self.assertFalse(repaired.endswith(tail))
                self.assertIn("Preserve the revised source rule.", (path.parent / "rule-table.md").read_text())
                source.write_text(source.read_text().replace("Preserve the revised source rule.", "Preserve the ordinary rule."))

    def test_generated_resolver_plain_tail_is_drift_and_source_repairable(self):
        self._plain_damage_regenerates_from_source("resolver.json")

    def test_generated_metadata_plain_tail_is_drift_and_source_repairable(self):
        self._plain_damage_regenerates_from_source("_meta.json")

    def test_plain_tail_never_hides_migration_in_leading_object_or_other_surfaces(self):
        path = "bionic/adrs/summaries/resolver.json"
        for leading in ({"historical_slugs": {"old": {}}}, {"row": {"migration": {"path": "absent"}}},
                        {"historical_clauses": [{"source_identity": "old"}]}):
            with self.subTest(leading=leading):
                self.write(path, json.dumps(leading) + "\nordinary damage\n")
                self.refuse("migration-git-required")
        self.write(path, '{"slugs": {}}\nordinary damage\n')
        self.write("bionic/promptbooks/active/PB-0001-one.yaml", "slots: [{migration_batch: absent.yaml}]\n")
        self.refuse("migration-git-required")

    def test_generated_json_tail_rejects_structure_indentation_migration_and_locators(self):
        for tail in ('{}', '[]', 'field: value', '- entry', ' ordinary words', '\tordinary words',
                     '"quoted"', '[1]', 'migration_inputs_sha256', 'historical_slugs',
                     'implementation-migration-publication', 'pilot.application.json',
                     'implementation-pilot-001.yaml', 'a' * 64 + '.json'):
            with self.subTest(tail=tail):
                self.write("bionic/adrs/summaries/resolver.json", '{}\n' + tail + '\n')
                self.refuse()

    def test_generated_json_leading_object_requires_complete_unique_bounded_shape(self):
        for text in ('{"slugs":', '{"slugs": {}, "slugs": {}}', '{"row": {"key": 1, "key": 2}}',
                     '[]\nordinary damage\n', '[{}]', '{"row": NaN}',
                     '{"row":' * 80 + '{}' + '}' * 80):
            with self.subTest(text=text[:60]):
                self.write("bionic/adrs/summaries/resolver.json", text)
                self.refuse()
        path = self.root / "bionic/adrs/summaries/resolver.json"
        path.write_bytes(b'{}\n\xff\n')
        self.refuse()

    def test_plain_json_tail_tolerance_never_applies_to_authored_declarations(self):
        for rel in (".bionic.yml", "bionic/manifest.yml", "bionic/promptbooks/active/PB-0001-one.yaml",
                    "bionic/promptbooks/runs/run.yaml"):
            with self.subTest(path=rel):
                before = (self.root / rel).read_bytes() if (self.root / rel).exists() else None
                self.write(rel, '{}\nordinary damage\n')
                self.refuse()
                if before is None: (self.root / rel).unlink()
                else: (self.root / rel).write_bytes(before)


    def test_ordinary_alias_dag_admission_and_hidden_migration_refusal(self):
        rel = "bionic/promptbooks/active/PB-0001-alias.yaml"
        self.write(rel, NoGitAliasBounds.dag(8))
        self.assertEqual(self.read()["state"], "original")
        self.write(rel, NoGitAliasBounds.dag(4, "{migration_bindings: [{batch: absent}]}"))
        self.refuse("migration-git-required")
        for text in ("a: &a [*a]\n", "a: &a {key: 1, key: 2}\nb: *a\n",
                     "a: &a {ordinary: 1}\nb: {<<: [*a, *a]}\n"):
            with self.subTest(text=text):
                self.write(rel, text)
                self.refuse()

    def test_config_aliases_refuse_before_canonical_normalization(self):
        self.write(".bionic.yml", 'docs_dir: bionic\n' + NoGitAliasBounds.dag(8))
        with patch.object(migration.bionic_config, "load_yaml",
                          side_effect=AssertionError("aliased config reached canonical normalization")):
            self.refuse("migration-authority-input-invalid")

    def padded_resolver(self, size):
        doc = {"slugs": {}, "retired_slugs": {}, "_pad": ""}
        doc["_pad"] = "x" * (size - len(json.dumps(doc).encode()))
        raw = json.dumps(doc).encode()
        self.assertEqual(len(raw), size)
        path = self.tree / "adrs/summaries/resolver.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_generated_resolver_exact_established_reader_bound_is_original(self):
        bound = driver("generate-reviews-index").RESOLVER_LIMIT
        self.assertEqual(bound, migration._reference_reader().MAX_FILE_BYTES)
        self.assertEqual(bound, 4 * 1024 * 1024)
        self.padded_resolver(bound)
        self.assertEqual(self.read()["state"], "original")

    def test_generated_resolver_over_bound_refuses_unchanged(self):
        self.padded_resolver(driver("generate-reviews-index").RESOLVER_LIMIT + 1)
        self.refuse()

    def test_larger_resolver_is_not_a_general_authored_or_proof_read_allowance(self):
        path = self.padded_resolver(migration.MAX_SOURCE_BYTES + 1)
        with self.assertRaisesRegex(migration.Refused, "migration-source-oversize"):
            migration._read(self.root, path)
        path.unlink()
        for rel in ("adrs/summaries/_meta.json", "adrs/doctrine/_meta.json", "promptbooks/active/PB-0001-one.yaml"):
            with self.subTest(path=rel):
                target = self.tree / rel; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'{"_pad":"' + b'x' * migration.MAX_SOURCE_BYTES + b'"}')
                self.refuse("migration-source-oversize")
                target.unlink()

    def git_fixture_commit(self):
        if not (self.root / ".git").exists():
            self.assertEqual(migration.cr.git(self.root, "init", "--quiet").returncode, 0)
            # A detached auto-maintenance run writes .git/objects/maintenance.lock at an
            # arbitrary moment, which the byte inventory around each read would catch.
            for key, value in (("gc.auto", "0"), ("maintenance.auto", "false")):
                self.assertEqual(migration.cr.git(self.root, "config", key, value).returncode, 0)
        self.assertEqual(migration.cr.git(self.root, "add", "-A").returncode, 0)
        result = migration.cr.git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                  "commit", "--quiet", "-m", "synthetic compatibility fixture")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_git_minimal_current_config_and_historical_snapshot_remain_original(self):
        for filename in (".bionic.yml", ".crux"):
            with self.subTest(config=filename):
                for name in (".bionic.yml", ".crux"): (self.root / name).unlink(missing_ok=True)
                self.write(filename, "docs_dir: bionic\nartifact_prefix: DEMO\n")
                self.git_fixture_commit()
                self.assertEqual(self.read()["state"], "original")
                # The canonical authoring/activation loader remains strict.
                with self.assertRaises(migration.bionic_config.BionicConfigError):
                    migration.bionic_config.load_config(self.root)
        (self.root / ".crux").unlink()
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: bionic\n')
        self.git_fixture_commit()
        self.assertEqual(self.read()["state"], "original")

    def test_git_config_dirty_invalid_duplicate_and_escape_still_refuse(self):
        self.write(".bionic.yml", "docs_dir: bionic\n")
        self.git_fixture_commit()
        self.write(".bionic.yml", "docs_dir: bionic\n# dirty\n")
        self.refuse("migration-config-not-committed")
        for text in ('config_version: "9"\ndocs_dir: bionic\n', 'docs_dir: [\n',
                     'docs_dir: bionic\ndocs_dir: docs\n', 'docs_dir: ../outside\n'):
            with self.subTest(text=text):
                self.write(".bionic.yml", text); self.git_fixture_commit()
                self.refuse()

    def test_git_minimal_history_cannot_hide_publication_or_deleted_locator(self):
        self.write(".bionic.yml", "docs_dir: bionic\n")
        locator = "bionic/adrs/migrations/implementation-pilot-001.application.json"
        self.write(locator, '{}\n'); self.git_fixture_commit()
        self.refuse("migration-publication-shape-refused")
        # Discoverable locator without complete close/binding/proof never activates.
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: bionic\n')
        self.git_fixture_commit()
        self.refuse("migration-publication-shape-refused")
        (self.root / locator).unlink(); self.git_fixture_commit()
        self.refuse("migration-publication-deleted")

    def test_git_minimal_historical_tree_and_shallow_refusal_preserve_order(self):
        self.write(".bionic.yml", "docs_dir: bionic\n")
        self.git_fixture_commit()
        base = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: renamed\n')
        self.tree.rename(self.root / "renamed"); self.git_fixture_commit()
        self.assertEqual(self.read()["state"], "original")
        (self.root / ".git/shallow").write_text(base + "\n")
        self.refuse("history-unavailable")

    def test_unborn_head_reads_original_like_a_repository_with_one_commit(self):
        # A fresh `git init` has no commits, so it has no publication to read.
        self.assertEqual(migration.cr.git(self.root, "init", "--quiet").returncode, 0)
        for key, value in (("gc.auto", "0"), ("maintenance.auto", "false")):
            self.assertEqual(migration.cr.git(self.root, "config", key, value).returncode, 0)
        unborn = self.read()
        self.assertEqual(unborn["state"], "original")
        self.assertEqual(unborn["publications"], [])
        self.git_fixture_commit()
        self.assertEqual(self.read(), unborn)

    def test_git_discovery_buffer_requires_exact_current_committed_raw_bytes(self):
        self.write(".bionic.yml", "docs_dir: bionic\n")
        self.git_fixture_commit()
        read = migration._read
        def stale(repo, given):
            text, rel = read(repo, given)
            return ("docs_dir: wrong\n", rel) if rel == ".bionic.yml" else (text, rel)
        with patch.object(migration, "_read", side_effect=stale):
            self.refuse("migration-config-not-committed")

    @staticmethod
    def document_mapping_forms():
        return ("---\ndocs_dir: bionic\nartifact_prefix: DEMO\n",
                "%YAML 1.1\n---\ndocs_dir: bionic\n...\n",
                "{docs_dir: bionic, artifact_prefix: DEMO}", "{}",
                "--- {}\n...\n", "--- {docs_dir: bionic}\n...\n",
                "---\n? docs_dir\n: bionic\n? artifact_prefix\n: DEMO\n",
                "!!map\n  docs_dir: bionic\n", "# empty commented config\n---\n...\n",
                "---\n  docs_dir: bionic\n  legacy_unknown: [one, two]\n")

    def test_document_mapping_forms_in_no_git_buffer(self):
        for text in self.document_mapping_forms():
            with self.subTest(text=text):
                self.write(".bionic.yml", text)
                self.assertEqual(self.read()["state"], "original")
                original = migration._no_git_document(text) or {}
                normalized = migration._legacy_config_text(text)
                self.assertEqual(migration._no_git_document(normalized), dict(original, config_version="1"))
                self.assertEqual((self.root / ".bionic.yml").read_text(), text)

    def test_document_mapping_forms_in_git_current_and_history(self):
        self.git_fixture_commit()
        for text in self.document_mapping_forms():
            with self.subTest(text=text):
                self.write(".bionic.yml", text); self.git_fixture_commit()
                self.assertEqual(self.read()["state"], "original")
                self.assertEqual((self.root / ".bionic.yml").read_text(), text)
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: bionic\n')
        self.git_fixture_commit()
        self.assertEqual(self.read()["state"], "original")

    def test_document_buffer_preserves_explicit_versions_and_strict_refusals(self):
        explicit = '%YAML 1.1\n--- {config_version: "1", docs_dir: bionic}\n...\n'
        self.assertEqual(migration._legacy_config_text(explicit), explicit)
        self.write(".bionic.yml", explicit)
        self.assertEqual(self.read()["state"], "original")
        for text in ('--- {config_version: "9", docs_dir: bionic}',
                     '--- {docs_dir: bionic, docs_dir: docs}',
                     '---\ndocs_dir: bionic\n---\ndocs_dir: docs\n',
                     '---\ndocs_dir: &a bionic\nunknown: *a\n',
                     '--- {docs_dir: ../outside}', '--- [bionic]', '--- null'):
            with self.subTest(text=text):
                self.write(".bionic.yml", text); self.refuse()


class NoGitAliasBounds(unittest.TestCase):
    @staticmethod
    def dag(depth, leaf="ordinary"):
        lines = [f"a0: &a0 [{leaf}]"]
        lines += [f"a{i}: &a{i} [*a{i-1}, *a{i-1}]" for i in range(1, depth + 1)]
        return "\n".join(lines) + "\n"

    def test_private_parser_never_calls_unbounded_shared_duplicate_walker(self):
        for depth in (1, 4, 8):
            with self.subTest(depth=depth), patch.object(migration, "_refuse_duplicate_keys_bounded",
                    side_effect=AssertionError("unbounded shared walker reached")):
                doc = migration._no_git_document(self.dag(depth))
                self.assertIs(doc[f"a{depth}"][0], doc[f"a{depth}"][1])
                migration._no_git_declarations(doc)

    def test_declarations_visit_shared_mappings_once_and_find_migration_fields(self):
        class Counted(dict):
            visits = 0
            def items(self):
                type(self).visits += 1
                return super().items()
        value = Counted(ordinary=True)
        for _ in range(8): value = Counted(left=value, right=value)
        migration._no_git_declarations(value)
        self.assertEqual(Counted.visits, 9)
        hidden = migration._no_git_document(self.dag(4, "{migration_batch: missing.yaml}"))
        with self.assertRaises(migration.Refused) as caught:
            migration._no_git_declarations(hidden)
        self.assertEqual(caught.exception.code, "migration-git-required")

    def test_duplicate_cycle_and_merge_are_refused_before_construction(self):
        for text, code in (("a: &a {same: 1, same: 2}\nb: *a\n", "migration-duplicate-key-refused"),
                           ("a: &a [*a]\n", "migration-footprint-ambiguous"),
                           ("a: &a {ordinary: 1}\nb: {<<: [*a, *a]}\n", "migration-footprint-ambiguous")):
            with self.subTest(text=text), patch.object(migration.yaml.SafeLoader, "construct_document",
                    side_effect=AssertionError("refused graph was constructed")):
                with self.assertRaises(migration.Refused) as caught:
                    migration._no_git_document(text)
                self.assertEqual(caught.exception.code, code)

    def test_private_total_work_budgets_and_declaration_cycles_refuse(self):
        with patch.object(migration, "_NO_GIT_WORK_LIMIT", 12):
            for operation in (lambda: migration._no_git_document(self.dag(4)),
                              lambda: migration._no_git_declarations({str(i): {} for i in range(13)})):
                with self.assertRaises(migration.Refused) as caught: operation()
                self.assertEqual(caught.exception.code, "migration-footprint-ambiguous")
        cycle = {}; cycle["again"] = cycle
        with self.assertRaises(migration.Refused): migration._no_git_declarations(cycle)


if __name__ == "__main__":
    unittest.main()
