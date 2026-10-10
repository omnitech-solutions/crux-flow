"""An empty architecture result grants no decision or migration authority."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import _council_gate_support as support
import implementation_migration as migration
from crux.arch import core


class DecisionApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.parent = Path(self.temporary)

    def shallow_code(self):
        original = support.init_repo(self.parent / "original")
        (original / "source.py").write_text("VALUE = 1\n")
        support.commit_all(original, "synthetic source")
        (original / "source.py").write_text("VALUE = 2\n")
        support.commit_all(original, "synthetic later source")
        clone = self.parent / "shallow"
        support.git(original, "clone", "-q", "--depth", "1", original.as_uri(), str(clone))
        self.assertEqual(support.git(clone, "rev-parse", "--is-shallow-repository").strip(), "true")
        return clone

    def assert_empty(self, root, docs_dir="bionic"):
        with mock.patch.object(migration, "authority_view", side_effect=AssertionError("authority was requested")):
            text, sources = core.extract_decision_index(root, docs_dir)
        self.assertEqual(text, core._canon(["# Decision index", "", core.NO_EXTRACTOR.rstrip("\n")]))
        self.assertEqual(sources, {})

    def test_plain_shallow_code_has_the_existing_empty_result(self):
        self.assert_empty(self.shallow_code())

    def test_valid_uninitialized_layout_has_only_an_empty_result(self):
        root = self.parent / "plain"; root.mkdir()
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: knowledge\n')
        self.assert_empty(root, "knowledge")

    def test_plain_non_git_code_does_not_request_authority(self):
        root = self.parent / "plain"; root.mkdir()
        (root / "source.py").write_text("VALUE = 1\n")
        self.assert_empty(root)

    def test_concern_containers_including_empty_ones_require_authority(self):
        root = self.parent / "plain"; root.mkdir()
        for concern in ("adrs", "promptbooks", "observations"):
            path = root / "bionic" / concern; path.mkdir(parents=True)
            try:
                with self.subTest(concern=concern), mock.patch.object(
                        migration, "authority_view", side_effect=migration.Refused("synthetic-strict-refusal")):
                    with self.assertRaisesRegex(migration.Refused, "synthetic-strict-refusal"):
                        core.extract_decision_index(root, "bionic")
            finally: path.rmdir()

    def test_current_evidence_in_each_finite_surface_stays_strict(self):
        root = self.shallow_code()
        paths = ("adrs/ADR-0001-constraint.md", "adrs/archive/ADR-0001-constraint.md",
                 "adrs/migrations/pilot.yaml", "adrs/migrations/pilot.application.json",
                 "adrs/summaries/resolver.json", "adrs/summaries/backfill-reviews.yml",
                 "adrs/doctrine/_meta.json", "promptbooks/active/DEMO-PB-0001.yaml",
                 "promptbooks/runs/DEMO-PB-0001/run-001.yaml", "observations/OBS-0001-source.md")
        for relative in paths:
            path = root / "bionic" / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic evidence\n")
            try:
                with self.subTest(relative=relative), self.assertRaisesRegex(migration.Refused, "history-unavailable"):
                    core.extract_decision_index(root, "bionic")
            finally: shutil.rmtree(root / "bionic")

    def test_head_evidence_deleted_from_worktree_stays_strict(self):
        root = self.shallow_code()
        path = root / "bionic/adrs/migrations/pilot.application.json"
        path.parent.mkdir(parents=True); path.write_text("{}\n")
        support.commit_all(root, "synthetic retained footprint")
        path.unlink(); path.parent.rmdir(); path.parent.parent.rmdir(); path.parent.parent.parent.rmdir()
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "bionic")

    def test_staged_evidence_deleted_from_worktree_stays_strict(self):
        root = self.shallow_code()
        path = root / "bionic/observations/OBS-0001-source.md"
        path.parent.mkdir(parents=True); path.write_text("synthetic evidence\n")
        support.git(root, "add", "bionic/observations/OBS-0001-source.md")
        path.unlink(); path.parent.rmdir(); path.parent.parent.rmdir()
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "bionic")

    def test_deleted_head_configuration_cannot_hide_its_selected_tree(self):
        root = self.shallow_code()
        path = root / ".bionic.yml"
        path.write_text('config_version: "1"\ndocs_dir: knowledge\n')
        support.commit_all(root, "synthetic selected layout")
        path.unlink()
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "bionic")

    def test_requested_and_conventional_surviving_roots_stay_strict(self):
        root = self.shallow_code()
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: knowledge\n')
        support.commit_all(root, "synthetic selected layout")
        for directory in ("requested", "docs", "bionic"):
            path = root / directory / "adrs/migrations/pilot.yaml"
            path.parent.mkdir(parents=True); path.write_text("synthetic evidence\n")
            try:
                with self.subTest(directory=directory), self.assertRaisesRegex(migration.Refused, "history-unavailable"):
                    core.extract_decision_index(root, "requested")
            finally: path.unlink(); path.parent.rmdir(); path.parent.parent.rmdir(); path.parent.parent.parent.rmdir()

    def test_manifest_or_config_declaration_stays_strict(self):
        root = self.shallow_code()
        (root / "bionic").mkdir()
        for relative, text in (("bionic/manifest.yml", 'schema_version: "5"\nmigration_batch: synthetic\n'),
                               (".bionic.yml", 'config_version: "1"\ndocs_dir: bionic\nmigration_batch: synthetic\n')):
            path = root / relative; path.write_text(text)
            try:
                with self.subTest(relative=relative), self.assertRaises(migration.Refused):
                    core.extract_decision_index(root, "bionic")
            finally: path.unlink()

    def test_malformed_layout_and_escaping_root_cannot_supply_absence(self):
        root = self.parent / "plain"; root.mkdir()
        for text in ('config_version: "1"\ndocs_dir: [\n', 'config_version: "1"\ndocs_dir: ../escape\n'):
            (root / ".bionic.yml").write_text(text)
            with self.subTest(text=text), self.assertRaises((migration.Refused, ValueError)):
                core.extract_decision_index(root, "bionic")

    def test_public_authority_view_still_refuses_plain_shallow_history(self):
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            migration.authority_view(self.shallow_code())

    def test_malformed_manifest_cannot_supply_absence_under_explicit_config(self):
        root = self.parent / "plain"; root.mkdir()
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n')
        (root / "bionic").mkdir()
        (root / "bionic/manifest.yml").write_text("scalar instead of a mapping\n")
        with self.assertRaises(migration.Refused):
            core.extract_decision_index(root, "bionic")

    def test_current_versionless_or_unsupported_configuration_cannot_supply_absence(self):
        root = self.parent / "plain"; root.mkdir()
        for text in ('docs_dir: knowledge\n', 'config_version: "999"\ndocs_dir: knowledge\n'):
            (root / ".bionic.yml").write_text(text)
            with self.subTest(text=text), self.assertRaises(migration.Refused):
                core.extract_decision_index(root, "knowledge")

    def test_canonical_configuration_precedence_ignores_lower_schema_values(self):
        root = self.parent / "plain"; root.mkdir()
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: knowledge\n')
        (root / ".crux").write_text('config_version: "999"\ndocs_dir: ../unused\n')
        self.assert_empty(root, "knowledge")

    def test_new_git_facts_use_the_existing_isolated_read_only_wrapper(self):
        root = self.shallow_code()
        with mock.patch.object(migration.cr, "git", side_effect=AssertionError("unhardened Git facts")), \
                mock.patch.object(migration, "_revision_git", wraps=migration._revision_git) as guarded:
            self.assert_empty(root)
        commands = [call.args[2] if call.args[1] == "--no-lazy-fetch" else call.args[1]
                    for call in guarded.call_args_list]
        self.assertIn("ls-tree", commands)
        self.assertIn("ls-files", commands)
        self.assertNotIn("diff", commands)
        for call in guarded.call_args_list:
            if call.args[1:] != ("rev-parse", "--show-toplevel"):
                self.assertEqual(call.args[1], "--no-lazy-fetch")

    def ordinary_selected_tree(self, directory="knowledge"):
        root = self.shallow_code()
        (root / ".bionic.yml").write_text(f'config_version: "1"\ndocs_dir: {directory}\n')
        tree = root / directory; tree.mkdir(parents=True)
        (tree / "note.md").write_text("ordinary source documentation\n")
        support.commit_all(root, "synthetic ordinary tree")
        self.assert_empty(root, directory)
        return root, tree

    def test_index_tree_object_does_not_prove_an_ordinary_ancestor_mode(self):
        import subprocess
        root, _ = self.ordinary_selected_tree()
        oid = support.git(root, "rev-parse", "HEAD:knowledge").strip().encode()
        original = migration._revision_git
        def indexed_ancestor(repo, *args):
            if args == ("--no-lazy-fetch", "rev-parse", "--verify", "--end-of-options", ":0:knowledge"):
                # An OID-only result cannot distinguish a sparse tree from a
                # non-tree index mode paired with that tree object. Mock the
                # metadata seam without installing a synthetic index entry.
                return subprocess.CompletedProcess(args, 0, oid + b"\n", b"")
            return original(repo, *args)
        with mock.patch.object(migration, "_revision_git", side_effect=indexed_ancestor):
            with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
                core.extract_decision_index(root, "knowledge")

    def test_head_selected_tree_symlink_replaced_by_empty_directory_stays_strict(self):
        root, tree = self.ordinary_selected_tree()
        (tree / "note.md").unlink(); tree.rmdir()
        target = root / "oldrecords/adrs"; target.mkdir(parents=True)
        (target / "ADR-0001-constraint.md").write_text("synthetic architectural source\n")
        tree.symlink_to("oldrecords", target_is_directory=True)
        support.commit_all(root, "synthetic selected-tree symlink")
        tree.unlink(); tree.mkdir()
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "knowledge")

    def test_head_ancestor_symlink_replaced_by_ordinary_directories_stays_strict(self):
        root, tree = self.ordinary_selected_tree("outer/knowledge")
        (tree / "note.md").unlink(); tree.rmdir(); tree.parent.rmdir()
        target = root / "oldrecords/knowledge/adrs"; target.mkdir(parents=True)
        (target / "ADR-0001-constraint.md").write_text("synthetic architectural source\n")
        (root / "outer").symlink_to("oldrecords", target_is_directory=True)
        support.commit_all(root, "synthetic ancestor symlink")
        (root / "outer").unlink(); tree.mkdir(parents=True)
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "outer/knowledge")

    def test_index_selected_tree_symlink_replaced_by_empty_directory_stays_strict(self):
        root, tree = self.ordinary_selected_tree()
        (tree / "note.md").unlink(); tree.rmdir()
        tree.symlink_to("oldrecords", target_is_directory=True)
        support.git(root, "add", "-A", "--", "knowledge")
        tree.unlink(); tree.mkdir()
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "knowledge")

    def test_head_gitlink_replaced_by_empty_directory_stays_strict(self):
        root, tree = self.ordinary_selected_tree()
        support.git(root, "rm", "-q", "-r", "knowledge")
        commit = support.git(root, "rev-parse", "HEAD").strip()
        support.git(root, "update-index", "--add", "--cacheinfo", f"160000,{commit},knowledge")
        support.git(root, "commit", "-q", "-m", "synthetic selected-tree gitlink")
        tree.mkdir(exist_ok=True)
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "knowledge")

    def test_index_ancestor_conflict_replaced_by_empty_directory_stays_strict(self):
        root, tree = self.ordinary_selected_tree()
        support.git(root, "rm", "-q", "-r", "knowledge")
        oid = support.git(root, "rev-parse", "HEAD:.bionic.yml").strip()
        # The ordinary index-info interface creates an unresolved file ancestor.
        import subprocess
        completed = subprocess.run(["git", "-C", str(root), "update-index", "--index-info"],
            input=f"100644 {oid} 1\tknowledge\n", text=True, capture_output=True,
            env=support.scrubbed_env())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        tree.mkdir(exist_ok=True)
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "knowledge")

    def test_unsupported_no_lazy_fetch_option_refuses_before_authority(self):
        import subprocess
        root = self.shallow_code()
        original = migration._revision_git
        def unsupported(repo, *args):
            if args[0] == "--no-lazy-fetch":
                return subprocess.CompletedProcess(args, 129, b"", b"unsupported option")
            return original(repo, *args)
        with mock.patch.object(migration, "_revision_git", side_effect=unsupported), \
                mock.patch.object(migration, "authority_view", side_effect=AssertionError("authority fallback")):
            with self.assertRaisesRegex(migration.Refused, "migration-git-capability-refused"):
                core.extract_decision_index(root, "bionic")

    def test_untracked_configuration_cannot_shadow_head_architectural_inputs(self):
        root = self.shallow_code()
        (root / ".crux").write_text('config_version: "1"\ndocs_dir: oldtree\n')
        support.commit_all(root, "synthetic clean layout")
        self.assert_empty(root, "oldtree")
        source = root / "oldtree/adrs/ADR-0001-constraint.md"
        source.parent.mkdir(parents=True)
        source.write_text("---\nid: ADR-0001\nstatus: Accepted\n---\nA constraint.\n")
        support.commit_all(root, "synthetic architectural source")
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: newtree\n')
        self.assertEqual(support.git(root, "ls-files", "--", ".bionic.yml"), "")
        with self.assertRaisesRegex(migration.Refused, "history-unavailable"):
            core.extract_decision_index(root, "newtree")
