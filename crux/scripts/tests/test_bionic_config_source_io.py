"""Guarded configuration capture preserves canonical lazy selection."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from admission_source_io import SourceIO, SourceIORefusal
import bionic_config as config


class GuardedConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ("docs", "bionic"):
            (self.root / name).mkdir()
        self.io = SourceIO(self.root)
        self.addCleanup(self.io.close)

    def manifest(self, name):
        (self.root / name / "manifest.yml").write_text("schema_version: 5\nconcerns_enabled: [adrs]\n")

    def test_explicit_primary_selection_does_not_read_lower_tiers(self):
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\nartifact_prefix: TEAM\n')
        (self.root / ".crux").write_text("malformed: [\n")
        (self.root / "bionic/.migrating").write_text("invalid marker\n")
        expected = config.load_config(self.root)
        read = self.io.read_text
        observed = []
        def capture(path, **kwargs):
            observed.append(path.relative_to(self.root).as_posix())
            return read(path, **kwargs)
        with mock.patch.object(self.io, "read_text", capture), mock.patch.object(Path, "read_text", side_effect=AssertionError("unguarded read")):
            actual = config.load_config(self.root, _source_io=self.io)
        self.assertEqual([getattr(actual, key) for key in actual.__slots__],
                         [getattr(expected, key) for key in expected.__slots__])
        self.assertEqual(observed, [".bionic.yml"])
        self.io.revalidate()

    def test_legacy_keyless_and_bare_discovery_match_defaults(self):
        for tier in (".crux", ".bionic.yml", None):
            with self.subTest(tier=tier):
                self.setUp()
                self.manifest("docs")
                if tier: (self.root / tier).write_text('config_version: "1"\nartifact_prefix: TEAM\n')
                expected = config.load_config(self.root, require_tree=True)
                with mock.patch.object(Path, "read_text", side_effect=AssertionError("unguarded read")):
                    actual = config.load_config(self.root, require_tree=True, _source_io=self.io)
                self.assertEqual([getattr(actual, key) for key in actual.__slots__],
                                 [getattr(expected, key) for key in expected.__slots__])
                self.io.revalidate()

    def test_marker_symlink_and_directory_config_preserve_canonical_refusals(self):
        self.manifest("docs"); self.manifest("bionic")
        marker = self.root / "bionic/.migrating"
        marker.symlink_to("missing")
        with self.assertRaisesRegex(config.BionicConfigError, "symlink"):
            config.load_config(self.root, _source_io=self.io)
        self.setUp()
        (self.root / ".bionic.yml").mkdir()
        with self.assertRaisesRegex(config.BionicConfigError, "directory"):
            config.load_config(self.root, _source_io=self.io)

    def test_guarded_collector_returns_only_canonical_captured_inputs(self):
        (self.root / ".crux").write_text('config_version: "1"\ndocs_dir: docs\n')
        inputs = config._collect_layout_inputs(self.root, _source_io=self.io)
        self.assertEqual(inputs, {".crux": 'config_version: "1"\ndocs_dir: docs\n'})
        self.assertEqual(config.select_layout_inputs(inputs)[0][1], "docs")

    def test_contained_config_alias_and_escaping_config_refusal(self):
        actual = self.root / "layout.yml"
        actual.write_text('config_version: "1"\ndocs_dir: docs\n')
        (self.root / ".bionic.yml").symlink_to("layout.yml")
        self.assertEqual(config.load_config(self.root, _source_io=self.io).docs_root, self.root / "docs")
        self.setUp()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        external = Path(outside.name).resolve() / "layout.yml"
        external.write_text('config_version: "1"\ndocs_dir: docs\n')
        (self.root / ".bionic.yml").symlink_to(external)
        with self.assertRaises(SourceIORefusal):
            config.load_config(self.root, _source_io=self.io)

    def test_guarded_read_refusal_propagates_without_discovery_fallback(self):
        self.manifest("docs")
        with mock.patch.object(self.io, "read_text", side_effect=SourceIORefusal()):
            with self.assertRaises(SourceIORefusal):
                config.load_config(self.root, _source_io=self.io)
