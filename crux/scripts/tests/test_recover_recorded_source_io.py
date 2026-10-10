"""Recorded recovery uses canonical parsing on the operation's source transport."""
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml
from admission_source_io import SourceIO, SourceIORefusal
from crux.arch import recover

_TRUSTED_SUMMARY_DRIVER = Path(recover.__file__).resolve().parents[2] / "summarize-adrs.py"
_TRUSTED_MIGRATION_SCHEMA = _TRUSTED_SUMMARY_DRIVER.parents[1] / "schemas/implementation-migration.schema.json"


class RecordedSourceIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.obs = self.root / "docs/observations"
        self.obs.mkdir(parents=True)
        (self.root / "docs/adrs").mkdir()
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        (self.root / "docs/manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [adrs, observations]\n')
        (self.root / "source.py").write_text("VALUE = 1\n")

    def record(self, name="OBS-0001-fact.md", *, anchor="a" * 16):
        path = self.obs / name
        doc = dict(id="OBS-0001", title="An observed fact", status="ratified",
                   date="2026-10-03", provenance="recovered", anchor_id=anchor,
                   recovered_id=anchor, evidence=["source.py:1-1"], governs=[
                       dict(handle="OBS-0001/fact", domain="testing", scope="source",
                            provenance="recovered", rule="A separate source fact.")])
        path.write_text("---\n" + yaml.safe_dump(doc) + "---\n\nRecord.\n")
        return path

    def io(self, *, admission=False):
        if admission:
            from observation_admission import _AdmissionIO
            source_io = _AdmissionIO(self.root)
        else:
            source_io = SourceIO(self.root)
        self.addCleanup(source_io.close)
        return source_io

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def no_raw_reads(self, *, allow_root=False):
        stack = contextlib.ExitStack()
        for name in ("read_text", "read_bytes", "resolve", "is_dir", "is_file", "glob"):
            original = getattr(Path, name)
            def checked(path, *args, _name=name, _original=original, **kwargs):
                # Canonicalizing the explicit repository root is the entry contract.
                if allow_root and _name in {"resolve", "is_dir"} and path == self.root:
                    return _original(path, *args, **kwargs)
                if allow_root and _name == "resolve" and path == _TRUSTED_SUMMARY_DRIVER:
                    return _original(path, *args, **kwargs)
                if allow_root and _name == "read_text" and path == _TRUSTED_MIGRATION_SCHEMA:
                    return _original(path, *args, **kwargs)
                raise AssertionError("unguarded Path." + _name + ": " + str(path))
            stack.enter_context(mock.patch.object(Path, name, checked))
        return stack

    def test_guarded_matches_default_normalization_without_raw_path_reads(self):
        self.record()
        (self.obs / "OBS-0002-no-block.md").write_text("A record without frontmatter.\n")
        (self.obs / "OBS-0003-no-anchor.md").write_text("---\nid: OBS-0003\n---\n")
        expected = recover.read_recorded_observations(self.obs)
        before = self.inventory()
        source_io = self.io()
        with self.no_raw_reads():
            actual = recover.read_recorded_observations(self.obs, _source_io=source_io)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["a" * 16]["rule"], "A separate source fact.")
        source_io.revalidate()
        self.assertEqual(self.inventory(), before)

    def test_missing_directory_empty_and_numeric_anchor_preserved(self):
        source_io = self.io()
        with self.no_raw_reads():
            self.assertEqual(recover.read_recorded_observations(
                self.obs / "missing", _source_io=source_io), {})
        path = self.record()
        path.write_text("---\nanchor_id: 0000000000000002\nstatus: rejected\n---\n")
        with self.no_raw_reads():
            actual = recover.read_recorded_observations(self.obs, _source_io=source_io)
        self.assertEqual(set(actual), {"2"})
        self.assertEqual(actual, recover.read_recorded_observations(self.obs))

    def test_contained_alias_preserves_default_record_identity(self):
        target = self.record("stored.md")
        (self.obs / "OBS-0001-fact.md").symlink_to(target.name)
        expected = recover.read_recorded_observations(self.obs)
        source_io = self.io()
        with self.no_raw_reads():
            self.assertEqual(recover.read_recorded_observations(
                self.obs, _source_io=source_io), expected)

    def test_outside_alias_refuses_without_mutation(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "private.md"
        target.write_text("PRIVATE-BYTES\n")
        (self.obs / "OBS-0001-fact.md").symlink_to(target)
        source_io = self.io()
        with self.no_raw_reads(), self.assertRaises(ValueError) as raised:
            recover.read_recorded_observations(self.obs, _source_io=source_io)
        self.assertNotIn("PRIVATE-BYTES", str(raised.exception))
        self.assertIn("OBS-0001-fact.md", str(raised.exception))
        self.assertIn("admission-source-io-containment-refused", str(raised.exception))
        self.assertEqual(target.read_text(), "PRIVATE-BYTES\n")

    def test_malformed_frontmatter_refuses_without_quoting_source(self):
        path = self.obs / "OBS-0001-fact.md"
        path.write_text("---\nanchor_id: [PRIVATE-BYTES\n---\n")
        source_io = self.io()
        before = self.inventory()
        with self.no_raw_reads(), self.assertRaises(ValueError) as raised:
            recover.read_recorded_observations(self.obs, _source_io=source_io)
        self.assertNotIn("PRIVATE-BYTES", str(raised.exception))
        self.assertEqual(self.inventory(), before)

    def test_guarded_snapshot_rejects_record_bytes_and_directory_membership_changes(self):
        path = self.record()
        source_io = self.io()
        recover.read_recorded_observations(self.obs, _source_io=source_io)
        path.write_text(path.read_text() + "Changed body.\n")
        with self.assertRaises(SourceIORefusal):
            source_io.revalidate()
        fresh = self.io()
        recover.read_recorded_observations(self.obs, _source_io=fresh)
        self.record("OBS-0002-fact.md", anchor="b" * 16)
        with self.assertRaises(SourceIORefusal):
            fresh.revalidate()

    def test_guarded_missing_directory_captures_absence(self):
        missing = self.obs / "missing"
        source_io = self.io()
        self.assertEqual(recover.read_recorded_observations(missing, _source_io=source_io), {})
        missing.mkdir()
        with self.assertRaises(SourceIORefusal):
            source_io.revalidate()

    def test_rooted_canonical_preflight_and_records_share_admission_io(self):
        self.record()
        expected = recover.read_recorded_observations(self.obs, repo_root=self.root)
        source_io = self.io(admission=True)
        before = self.inventory()
        with self.no_raw_reads(allow_root=True):
            actual = recover.read_recorded_observations(
                self.obs, repo_root=self.root, _source_io=source_io)
        self.assertEqual(actual, expected)
        source_io.revalidate()
        self.assertEqual(self.inventory(), before)

    def test_explicit_tree_and_wrong_correlation_directory(self):
        self.record()
        alternate = self.root / "notes"
        self.obs.rename(alternate)
        source_io = self.io(admission=True)
        with self.assertRaisesRegex(ValueError, "differs"):
            recover.read_recorded_observations(
                alternate, repo_root=self.root, _source_io=source_io)
        # Select a proper alternate tree; it must use its own manifest and corpus.
        (self.root / "notes").rename(self.root / "saved-records")
        (self.root / "notes/observations").mkdir(parents=True)
        (self.root / "notes/adrs").mkdir()
        (self.root / "notes/manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [adrs, observations]\n')
        record = self.root / "saved-records/OBS-0001-fact.md"
        record.rename(self.root / "notes/observations/OBS-0001-fact.md")
        fresh = self.io(admission=True)
        expected = recover.read_recorded_observations(
            self.root / "notes/observations", repo_root=self.root, docs_dir="notes")
        with self.no_raw_reads(allow_root=True):
            self.assertEqual(recover.read_recorded_observations(
                self.root / "notes/observations", repo_root=self.root, docs_dir="notes",
                _source_io=fresh), expected)


if __name__ == "__main__":
    unittest.main()
