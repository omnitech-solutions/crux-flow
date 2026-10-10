"""Both decision-index input paths validate authority before returning rows."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[1] / "tools/tests"))
try:  # package-relative when run as a module, flat when run by discovery
    from ._dev_surface import IS_STAGED_ARTIFACT
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dev_surface import IS_STAGED_ARTIFACT
try:
    import test_summarize_migration_consumers as fixtures
except ModuleNotFoundError:
    if IS_STAGED_ARTIFACT:  # tools/tests is dev-repo-only and never crosses the sync boundary
        raise unittest.SkipTest("tools/tests/test_summarize_migration_consumers.py is absent "
                                "in the staged artifact")
    raise
import _council_gate_support as sup
import implementation_migration as migration
import yaml
from crux.arch import core

spec = importlib.util.spec_from_file_location("history_adr_index", SCRIPTS / "generate-adr-index.py")
index = importlib.util.module_from_spec(spec); spec.loader.exec_module(index)


class OrdinaryArchitecture(unittest.TestCase):
    def test_typed_implementation_evidence_cannot_be_an_accepted_adr(self):
        for kind in ("implementation-decision", "implementation-result", "implementation-migration"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); adrs = fixtures.ordinary(root)
                (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
                source = adrs / "ADR-0001-valid.md"
                source.write_text(source.read_text().replace("id: ADR-0001", f"title: A constraint\ndate: '2026-10-03'\nrecord_type: {kind}\nid: ADR-0001"))
                for projected in (False, True):
                    if projected:
                        path, text = index.build(root); path.write_text(text)
                    before = fixtures.snapshot(root)
                    with self.subTest(projected=projected), self.assertRaises((ValueError, migration.Refused)):
                        core.extract_decision_index(root, "docs")
                    self.assertEqual(fixtures.snapshot(root), before)


class PublishedArchitecture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PublishedConsumers.setUpClass.__func__(cls)
        for source in cls.adrs.glob("ADR-*.md"):
            text = source.read_text(); doc = fixtures.sp.read_frontmatter(text)
            doc.update(title=doc["id"] + " constraint", date="2026-10-03")
            source.write_text("---\n" + yaml.safe_dump(doc) + "---\n" + fixtures.sp._adr_body(text))
        sup.commit_all(cls.root, "synthetic display metadata")
        path, text = index.build(cls.root); path.write_text(text)
        sup.commit_all(cls.root, "synthetic ordinary architectural index")

    def test_mixed_and_body_only_history_annotates_both_real_input_paths(self):
        index_path = self.adrs / "index.md"; original = index_path.read_bytes()
        try:
            for projected in (True, False):
                if not projected: index_path.unlink()
                before = fixtures.snapshot(self.root)
                text, sources = core.extract_decision_index(self.root, "docs")
                self.assertIn("ADR-0110", text)
                self.assertIn("historical implementation clauses", text.lower())
                self.assertIn("ADR-0093", text)
                self.assertIn("ADR-0146", text)
                self.assertNotIn("Preserve assessments.", text)
                self.assertIn("docs/adrs/ADR-0110-source.md", sources)
                self.assertIn("projected from" if projected else "derived from", text)
                self.assertEqual(fixtures.snapshot(self.root), before)
        finally: index_path.write_bytes(original)

    def test_invalid_authority_does_not_fall_back_and_writes_nothing(self):
        witness = self.fixture.witness; original = witness.read_bytes()
        index_path = self.adrs / "index.md"; index_original = index_path.read_bytes()
        try:
            witness.unlink()
            for projected in (True, False):
                if not projected: index_path.unlink()
                before = fixtures.snapshot(self.root)
                with self.subTest(projected=projected), self.assertRaises(migration.Refused):
                    core.extract_decision_index(self.root, "docs")
                self.assertEqual(fixtures.snapshot(self.root), before)
            with mock.patch.object(core, "_adr_index_projection", side_effect=AssertionError("fallback reached")):
                with self.assertRaises(migration.Refused): core.extract_decision_index(self.root, "docs")
            before = fixtures.snapshot(self.root)
            result = subprocess.run([sys.executable, str(SCRIPTS / "derive-arch.py"),
                "--repo-root", str(self.root), "--docs-dir", "docs"], env=sup.scrubbed_env(),
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(fixtures.snapshot(self.root), before)
        finally:
            witness.write_bytes(original); index_path.write_bytes(index_original)

    def test_archive_lifecycle_preserves_clause_history_without_current_row(self):
        source = self.adrs / "ADR-0110-source.md"; original = source.read_bytes()
        archive = self.adrs / "archive/ADR-0110-source.md"; archive.parent.mkdir(exist_ok=True)
        index_path = self.adrs / "index.md"; index_original = index_path.read_bytes()
        try:
            source.write_bytes(original.replace(b"status: Accepted", b"status: Superseded"))
            source.rename(archive)
            sup.commit_all(self.root, "synthetic archived mixed source")
            path, text = index.build(self.root); path.write_text(text)
            for projected in (True, False):
                if not projected: index_path.unlink()
                text, sources = core.extract_decision_index(self.root, "docs")
                self.assertIn("historical implementation clauses", text.lower())
                self.assertIn("docs/adrs/archive/ADR-0110-source.md", text)
                self.assertNotIn(": ADR-0110\n", text)
        finally:
            archive.unlink(); source.write_bytes(original); index_path.write_bytes(index_original)
            sup.commit_all(self.root, "synthetic restore source")

    def test_current_dependencies_refuse_on_both_paths(self):
        index_path = self.adrs / "index.md"; index_original = index_path.read_bytes()
        context = self.root / self.fixture.proof.binding["context"]["path"]
        mutations = [(self.adrs / "doctrine/reconciliations.yml", b"broken: ["),
            (self.adrs / "ADR-0146-authorizer.md", None),
            (self.adrs / "ADR-0110-source.md", None), (context, b"{}")]
        for path, changed in mutations:
            original = path.read_bytes()
            if changed is None:
                changed = original.replace(b"status: Accepted", b"status: Proposed") if "0146" in path.name else original.replace(b"inconsistent reports", b"different reports")
            try:
                path.write_bytes(changed)
                for projected in (True, False):
                    if projected: index_path.write_bytes(index_original)
                    else: index_path.unlink()
                    before = fixtures.snapshot(self.root)
                    with self.subTest(path=path.name, projected=projected), self.assertRaises(migration.Refused):
                        core.extract_decision_index(self.root, "docs")
                    self.assertEqual(fixtures.snapshot(self.root), before)
            finally:
                path.write_bytes(original); index_path.write_bytes(index_original)
