"""A location selector cannot bypass the root's migration authority."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import implementation_migration as migration
import _council_gate_support as sup


class AlternateTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"; self.root.mkdir()
        (self.root / ".bionic.yml").write_text('docs_dir: bionic\n')
        for name in ("bionic", "alternate"):
            tree = self.root / name; tree.mkdir()
            (tree / "manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: [observations]\n')

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() if not p.is_symlink()
                else str(p.readlink()) for p in self.root.rglob("*")
                if (p.is_file() or p.is_symlink()) and ".git" not in p.parts}

    def read(self, selector="alternate"):
        before = self.snapshot()
        try: return migration.admission_authority_view(self.root, docs_dir=selector)
        finally: self.assertEqual(self.snapshot(), before)

    def refuse(self, selector="alternate", code=None):
        with self.assertRaises(migration.Refused) as caught: self.read(selector)
        if code: self.assertEqual(caught.exception.code, code)

    def write(self, rel, text):
        path = self.root / rel; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text); return path

    def git(self):
        sup.init_repo(self.root); sup.commit_all(self.root, "synthetic ordinary trees")

    def test_distinct_ordinary_tree_with_and_without_git(self):
        self.assertEqual(self.read(), migration._original_view())
        self.assertEqual(self.read("bionic"), migration.authority_view(self.root))
        self.git()
        self.assertEqual(self.read(), migration._original_view())
        self.assertEqual(self.read("bionic"), migration.authority_view(self.root))

    def test_same_tree_and_default_use_normal_authority(self):
        expected = migration._original_view()
        with patch.object(migration, "authority_view", return_value=expected) as normal:
            self.assertIs(self.read("bionic"), expected)
            self.assertIs(self.read(None), expected)
        self.assertEqual(normal.call_count, 2)
        with patch.object(migration, "authority_view", side_effect=migration.Refused("migration-publication-shape-refused")):
            self.refuse("bionic", "migration-publication-shape-refused")

    def test_root_publication_and_deleted_publication_refuse(self):
        self.git()
        pub = self.write("bionic/adrs/migrations/pilot.application.json", "{}")
        sup.commit_all(self.root, "synthetic discovery locator, no proof or apply")
        self.refuse(code="migration-alternate-tree-unsupported")
        pub.unlink(); sup.commit_all(self.root, "synthetic deletion")
        self.refuse(code="migration-alternate-tree-unsupported")

    def test_selected_current_and_deleted_history_evidence_refuse(self):
        self.git()
        paths = [("adrs/migrations/pilot.application.json", "{}"),
                 ("promptbooks/active/PB-0001-one.yaml", "migration_inputs: []\n"),
                 ("promptbooks/runs/PB-0001-one/run-RUN-001.yaml", "migration_bindings: [{}]\n"),
                 ("adrs/summaries/resolver.json", '{"historical_slugs":{"old":{}}}')]
        for rel, text in paths:
            with self.subTest(path=rel):
                branch = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
                path = self.write("alternate/" + rel, text)
                sup.commit_all(self.root, "synthetic selected evidence")
                self.refuse(code="migration-alternate-tree-unsupported")
                path.unlink(); sup.commit_all(self.root, "synthetic removed selected evidence")
                self.refuse(code="migration-alternate-tree-unsupported")
                migration.cr.git(self.root, "reset", "--hard", branch)

    def test_no_git_root_or_selected_footprint_refuses(self):
        for tree in ("bionic", "alternate"):
            path = self.write(tree + "/adrs/migrations/pilot.yaml", "entries: []\n")
            self.refuse(code="migration-alternate-tree-unsupported"); path.unlink()
        self.write("alternate/adrs/doctrine/_meta.json", '{"migration_sha256":"x"}')
        self.refuse(code="migration-alternate-tree-unsupported")

    def test_bad_paths_and_malformed_surface_refuse_and_unborn_git_reads_original(self):
        for selector in ("../outside", "/tmp/outside", ".github", "missing"):
            with self.subTest(selector=selector): self.refuse(selector)
        link = self.root / "alias"; link.symlink_to(self.root / "alternate", target_is_directory=True)
        self.refuse("alias")
        self.write("alternate/promptbooks/active/PB-0001-one.yaml", "{broken")
        self.refuse(); (self.root / "alternate/promptbooks/active/PB-0001-one.yaml").unlink()
        # A repository with no commits reads like one without Git.
        sup.init_repo(self.root)
        self.assertEqual(self.read(), migration._original_view())

    def test_history_is_branch_reachable_and_shallow_refuses(self):
        self.git(); base = migration.cr.git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        self.write("alternate/adrs/migrations/pilot.yaml", "entries: []")
        sup.commit_all(self.root, "synthetic unrelated published branch")
        migration.cr.git(self.root, "checkout", "-b", "ordinary", base)
        self.assertEqual(self.read()["state"], "original")
        (self.root / ".git/shallow").write_text(base + "\n")
        self.refuse(code="history-unavailable")

    def test_selected_generated_resolver_bound_and_unrelated_holdings(self):
        self.write("alternate/adrs/summaries/resolver.json", '{"slugs":{}}' + " " * (3 * 1024 * 1024))
        self.write("alternate/promptbooks/runs/captures/vendor/repo.yaml", "migration_inputs: []")
        self.assertEqual(self.read()["state"], "original")
        self.git(); self.assertEqual(self.read()["state"], "original")

    def test_malformed_historical_input_and_symlink_cannot_hide_footprints(self):
        self.git()
        path = self.write("alternate/promptbooks/active/PB-0001-one.yaml", "{broken")
        sup.commit_all(self.root, "synthetic malformed historical selected book")
        path.unlink(); sup.commit_all(self.root, "synthetic removed malformed book")
        self.refuse(code="migration-footprint-ambiguous")
        migration.cr.git(self.root, "reset", "--hard", "HEAD~2")
        (self.root / "alternate/adrs").symlink_to(self.root / "bionic")
        self.refuse(code="migration-symlink-refused")

    def test_head_change_between_root_and_selected_checks_refuses(self):
        self.git()
        real = migration._discover_publications
        def changed(repo):
            result = real(repo)
            migration.cr.git(repo, "commit", "--allow-empty", "-m", "synthetic concurrent history change")
            return result
        with patch.object(migration, "_discover_publications", side_effect=changed):
            self.refuse(code="migration-history-head-changed")

    def test_no_git_config_switch_cannot_reclassify_selected_footprints(self):
        config = self.root / ".bionic.yml"
        for boundary in ("classification", "delegation"):
            with self.subTest(boundary=boundary):
                config.write_text("docs_dir: alternate\n")
                evidence = self.write("alternate/adrs/migrations/pilot.yaml", "entries: []\n")
                before = self.snapshot()
                name = "_discovery_directory" if boundary == "classification" else "authority_view"
                real = getattr(migration, name); changed = False
                def actor(*args, **kwargs):
                    nonlocal changed
                    if boundary == "classification": result = real(*args, **kwargs)
                    if not changed:
                        config.write_text("docs_dir: bionic\n"); changed = True
                    return result if boundary == "classification" else real(*args, **kwargs)
                with patch.object(migration, name, side_effect=actor):
                    with self.assertRaises(migration.Refused) as caught:
                        migration.admission_authority_view(self.root, docs_dir="alternate")
                self.assertEqual(caught.exception.code, "migration-config-changed")
                # Only the explicitly modeled actor changed the config.
                after = self.snapshot(); after[".bionic.yml"] = before[".bionic.yml"]
                self.assertEqual(after, before); evidence.unlink()

    def test_selected_directory_replacement_refuses_even_with_identical_file_bytes(self):
        real = migration._discovery_directory
        for selector in ("bionic", "alternate"):
            with self.subTest(selector=selector):
                tree = self.root / selector
                def actor(*args, **kwargs):
                    result = real(*args, **kwargs)
                    parked = self.root / "parked"; tree.rename(parked); tree.mkdir()
                    (tree / "manifest.yml").write_bytes((parked / "manifest.yml").read_bytes())
                    (parked / "manifest.yml").unlink(); parked.rmdir()
                    return result
                with patch.object(migration, "_discovery_directory", side_effect=actor):
                    self.refuse(selector, "migration-selected-tree-changed")


class ActualPublication(unittest.TestCase):
    def test_actual_close_synthetic_witness_preserves_same_tree_and_blocks_distinct(self):
        from test_implementation_authority import PublicationProof
        fixture = PublicationProof(); fixture.setUp(); self.addCleanup(fixture.temp.cleanup)
        (fixture.root / "alternate").mkdir()
        fixture.close(); fixture.publish()
        before = fixture.snapshot()
        expected = migration.authority_view(fixture.root)
        self.assertEqual(expected["state"], "published")
        self.assertEqual(migration.admission_authority_view(fixture.root, docs_dir="docs"), expected)
        with self.assertRaises(migration.Refused) as caught:
            migration.admission_authority_view(fixture.root, docs_dir="alternate")
        self.assertEqual(caught.exception.code, "migration-alternate-tree-unsupported")
        self.assertEqual(fixture.snapshot(), before)

    def test_committed_config_switch_cannot_return_another_trees_published_maps(self):
        from test_implementation_authority import PublicationProof
        fixture = PublicationProof(); fixture.setUp(); self.addCleanup(fixture.temp.cleanup)
        (fixture.root / "alternate").mkdir(); fixture.close(); fixture.publish()
        config = fixture.root / ".bionic.yml"
        for boundary in ("classification", "delegation"):
            with self.subTest(boundary=boundary):
                config.write_text('config_version: "1"\ndocs_dir: alternate\n')
                sup.commit_all(fixture.root, "synthetic selected tree before concurrent actor")
                before = fixture.snapshot()
                name = "_discovery_directory" if boundary == "classification" else "authority_view"
                real = getattr(migration, name); changed = False
                def actor(*args, **kwargs):
                    nonlocal changed
                    if boundary == "classification": result = real(*args, **kwargs)
                    if not changed:
                        config.write_text('config_version: "1"\ndocs_dir: docs\n')
                        sup.commit_all(fixture.root, "synthetic concurrent config commit"); changed = True
                    return result if boundary == "classification" else real(*args, **kwargs)
                with patch.object(migration, name, side_effect=actor):
                    with self.assertRaises(migration.Refused) as caught:
                        migration.admission_authority_view(fixture.root, docs_dir="alternate")
                self.assertEqual(caught.exception.code, "migration-history-head-changed")
                after = fixture.snapshot(); after[".bionic.yml"] = before[".bionic.yml"]
                self.assertEqual(after, before)


if __name__ == "__main__": unittest.main()
