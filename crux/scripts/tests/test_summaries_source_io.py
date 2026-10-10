"""Private source I/O must reach every admission summaries read."""
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import summaries_projection as sp

_PATH_METHODS = {name: getattr(Path, name) for name in
                 ("read_text", "resolve", "glob", "is_file", "is_dir", "exists")}


class RecordingIO:
    """Test callback; descriptor/race protection belongs to admission I/O tests."""
    def __init__(self, root):
        self.root = root.resolve()
        self.calls = []

    def resolve(self, path):
        self.calls.append(("resolve", path))
        target = _PATH_METHODS["resolve"](path)
        if target != self.root and self.root not in target.parents:
            raise ValueError("outside test root")
        return target

    def read_text(self, path):
        self.calls.append(("read_text", path))
        return _PATH_METHODS["read_text"](self.resolve(path), encoding="utf-8")

    def kind(self, path):
        self.calls.append(("kind", path))
        target = self.resolve(path)
        if _PATH_METHODS["is_file"](target):
            return "file"
        if _PATH_METHODS["is_dir"](target):
            return "directory"
        return None

    def glob(self, path, pattern):
        self.calls.append(("glob", path))
        return [path / entry.name for entry in _PATH_METHODS["glob"](self.resolve(path), pattern)]


def record(identity, status="Accepted", decided_by=None):
    observation = identity.startswith("OBS-")
    provenance = "recovered" if observation else "authored"
    return (f"---\nid: {identity}\nstatus: {status}\nprovenance: {provenance}\n"
            + (f"decided_by: {decided_by}\n" if decided_by else "")
            + f"governs:\n- handle: {identity}/{identity.lower()}\n  domain: test\n"
            + f"  rule: Preserve the constraint.\n  scope: source\n  provenance: {provenance}\n"
            + "---\n\nBody.\n")


class SummariesSourceIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.tree = self.root / "nested" / "knowledge"
        self.adrs = self.tree / "adrs"
        self.obs = self.tree / "observations"
        for directory in (self.adrs / "archive", self.adrs / "summaries",
                          self.adrs / "doctrine", self.obs):
            directory.mkdir(parents=True)
        (self.root / ".bionic.yml").write_text("docs_dir: nested/knowledge\nartifact_prefix: DEMO\n")
        (self.tree / "manifest.yml").write_text("concerns_enabled: [adrs, observations]\n")
        (self.adrs / "ADR-0001-rule.md").write_text(record("ADR-0001"))
        (self.adrs / "archive" / "ADR-0002-rule.md").write_text(record("ADR-0002"))
        (self.obs / "OBS-0001-rule.md").write_text(record("OBS-0001", "ratified"))
        (self.obs / "OBS-0002-rule.md").write_text(record("OBS-0002", "decided", "ADR-0001"))
        (self.obs / "index.md").write_text("Not a source record.")
        sp.reviews_path(self.adrs).write_text('config_version: "1"\nbatches: []\nreceipts: []\n')
        (self.adrs / "doctrine" / "reconciliations.yml").write_text(
            "reconciliations:\n- handle: ADR-0001/rule\n  signed: 2026-10-03\n")
        (self.adrs / "summaries" / "_meta.json").write_text(json.dumps({"input_domain": ["unknown"]}))

    def exercise(self, source_io=None):
        kwargs = {} if source_io is None else {"_source_io": source_io}
        tree = sp.resolve_tree(self.root, **kwargs)
        obs = sp.observations_root(tree, **kwargs)
        records = sp.collect_records(self.adrs, observations=obs, **kwargs)
        return {
            "tree": tree, "prefix": sp.artifact_prefix(self.root, **kwargs),
            "adrs": sp.adrs_dir(self.root, **kwargs), "runs": sp.runs_dir(self.root, **kwargs),
            "obs": sp.observations_dir(self.root, **kwargs),
            "manifest": sp.read_manifest(self.root, **kwargs),
            "records": records, "reviews": sp.read_reviews(self.adrs, **kwargs),
            "aliases": sp.collect_observation_alias_rows(obs, records, **kwargs),
            "declared": sp.declared_input_domain(self.adrs / "summaries", **kwargs),
            "signed": sp.signed_reconciliation_handles(self.adrs, **kwargs),
            "archived": sp.archived_handles(self.adrs, **kwargs),
            "maps": sp._summary_authority_records(records, {"state": "original",
                        "historical_handles": {}, "retired_handles": [], "historical_displacements": [],
                        "reserved_slugs": []}, {},
                        sp.collect_observation_alias_rows(obs, records, **kwargs)),
        }

    def test_callbacks_match_default_and_never_use_unguarded_path_operations(self):
        expected = self.exercise()
        guard = RecordingIO(self.root)
        with contextlib.ExitStack() as stack:
            for name in _PATH_METHODS:
                stack.enter_context(patch.object(Path, name, side_effect=AssertionError("unsafe " + name)))
            actual = self.exercise(guard)
        self.assertEqual(expected, actual)
        reads = {path for method, path in guard.calls if method == "read_text"}
        self.assertTrue({self.root / ".bionic.yml", self.tree / "manifest.yml",
                         sp.reviews_path(self.adrs), self.adrs / "doctrine" / "reconciliations.yml",
                         self.adrs / "summaries" / "_meta.json"}.issubset(reads))
        self.assertNotIn(self.obs / "index.md", reads)
        self.assertEqual(["unknown"], actual["declared"])

    def test_contained_leaf_aliases_preserve_canonical_results(self):
        for directory, name in ((self.adrs, "ADR-0001-rule.md"),
                                (self.adrs / "archive", "ADR-0002-rule.md"),
                                (self.obs, "OBS-0001-rule.md")):
            source = directory / name
            target = directory / "stored-source.txt"
            source.rename(target)
            source.symlink_to(target.name)
        self.assertEqual(self.exercise(), self.exercise(RecordingIO(self.root)))

    def test_contained_directory_alias_preserves_original_paths_and_records(self):
        moved = self.obs.with_name("stored-observations")
        self.obs.rename(moved)
        self.obs.symlink_to(moved.name, target_is_directory=True)
        guard = RecordingIO(self.root)
        self.assertEqual(self.exercise(), self.exercise(guard))
        self.assertEqual(sp.observation_paths(self.obs), sp.observation_paths(self.obs, _source_io=guard))

    def test_callbacks_refuse_outside_config_directory_and_leaf(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)
            (target / "secret.md").write_text(record("ADR-0001"))
            cases = [self.root / ".bionic.yml", self.adrs / "ADR-0001-rule.md",
                     sp.reviews_path(self.adrs), self.adrs / "doctrine" / "reconciliations.yml",
                     self.adrs / "summaries" / "_meta.json"]
            for path in cases:
                with self.subTest(path=path):
                    content = path.read_bytes()
                    path.unlink()
                    path.symlink_to(target / "secret.md")
                    try:
                        with self.assertRaisesRegex(ValueError, "outside test root"):
                            self.exercise(RecordingIO(self.root))
                    finally:
                        path.unlink()
                        path.write_bytes(content)
            directory = self.obs
            moved = directory.with_name("stored-observations")
            directory.rename(moved)
            directory.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "outside test root"):
                self.exercise(RecordingIO(self.root))

    def test_absent_optional_files_and_directory_match_default(self):
        for path in (sp.reviews_path(self.adrs), self.adrs / "doctrine" / "reconciliations.yml",
                     self.adrs / "summaries" / "_meta.json", self.tree / "manifest.yml"):
            path.unlink()
        (self.adrs / "archive" / "ADR-0002-rule.md").unlink()
        (self.adrs / "archive").rmdir()
        self.assertEqual(self.exercise(), self.exercise(RecordingIO(self.root)))

    def test_guard_refusal_during_declared_metadata_read_is_not_absence(self):
        class RefusingRead(RecordingIO):
            def read_text(self, path):
                raise ValueError("guard refused changed ancestor")
        with self.assertRaisesRegex(ValueError, "guard refused changed ancestor"):
            sp.declared_input_domain(self.adrs / "summaries", _source_io=RefusingRead(self.root))

    def test_canonical_parser_refusals_and_metadata_shapes_are_unchanged(self):
        mutations = [
            (self.adrs / "ADR-0001-rule.md", record("ADR-0001").replace("authored", "unknown")),
            (sp.reviews_path(self.adrs), 'config_version: "1"\nconfig_version: "1"\n'),
            (self.adrs / "doctrine" / "reconciliations.yml", 'reconciliations: []\nreconciliations: []\n'),
        ]
        for path, content in mutations:
            with self.subTest(path=path):
                original = path.read_text()
                path.write_text(content)
                try:
                    with self.assertRaises(sp.GovernsValidationError) as default:
                        self.exercise()
                    with self.assertRaises(sp.GovernsValidationError) as guarded:
                        self.exercise(RecordingIO(self.root))
                    self.assertEqual(str(default.exception), str(guarded.exception))
                finally:
                    path.write_text(original)
        meta = self.adrs / "summaries" / "_meta.json"
        for raw in ('{"input_domain": 123}', '{broken', '[1]', '{}'):
            meta.write_text(raw)
            self.assertEqual(sp.declared_input_domain(meta.parent),
                             sp.declared_input_domain(meta.parent, _source_io=RecordingIO(self.root)))

    def test_callbacks_do_not_write_sources_or_outputs(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.exercise(RecordingIO(self.root))
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_real_descriptor_io_matches_canonical_records_and_contained_aliases(self):
        from admission_source_io import SourceIO
        moved = self.obs.with_name("stored-observations")
        self.obs.rename(moved)
        self.obs.symlink_to(moved.name, target_is_directory=True)
        leaf = self.adrs / "ADR-0001-rule.md"
        target = self.adrs / "stored-source.txt"
        leaf.rename(target)
        leaf.symlink_to(target.name)
        guard = SourceIO(self.root)
        try:
            self.assertEqual(self.exercise(), self.exercise(guard))
            guard.revalidate()
        finally:
            guard.close()

    def test_real_descriptor_io_refuses_outside_config_and_source_before_parsing(self):
        from admission_source_io import SourceIO, SourceIORefusal
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "source.md"
            target.write_text(record("ADR-0001"))
            for path in (self.root / ".bionic.yml", self.adrs / "ADR-0001-rule.md",
                         sp.reviews_path(self.adrs)):
                with self.subTest(path=path):
                    original = path.read_bytes()
                    path.unlink()
                    path.symlink_to(target)
                    guard = SourceIO(self.root)
                    try:
                        with self.assertRaises(SourceIORefusal):
                            self.exercise(guard)
                    finally:
                        guard.close()
                        path.unlink()
                        path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
