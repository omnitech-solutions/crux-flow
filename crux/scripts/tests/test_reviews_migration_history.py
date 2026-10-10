"""Historical review locators resolve evidence without supplying authority."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
import test_generate_reviews_index as reports
import _council_gate_support as sup
import implementation_migration as migration

spec = importlib.util.spec_from_file_location("reviews_history_driver", SCRIPTS / "generate-reviews-index.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class LegacyHistoryMaps(unittest.TestCase):
    def test_absent_key_empty_map_and_retired_empty_membership(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); tree = root / "docs"
            (root / ".bionic.yml").write_text("docs_dir: docs\n")
            resolver = tree / "adrs/summaries/resolver.json"
            resolver.parent.mkdir(parents=True)
            for extra in ({}, {"historical_slugs": {}}):
                resolver.write_text(json.dumps(dict(slugs={"live": "ADR-0001/live"},
                    retired_slugs={"retired": []}, **extra)))
                self.assertTrue(driver._handle_exists("rule:live", tree))
                self.assertTrue(driver._handle_exists("rule:retired", tree))
                self.assertFalse(driver._handle_exists("rule:missing", tree))

    def test_present_malformed_historical_map_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = Path(temporary) / "resolver.json"
            for malformed in ([], None, "history", 1):
                resolver.write_text(json.dumps({"historical_slugs": malformed}))
                with self.subTest(value=malformed), self.assertRaises(OSError):
                    driver._resolver_slugs(resolver)

    def test_historical_map_rows_are_evidence_references_not_rule_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = Path(temporary) / "resolver.json"
            for row in (None, [], "ADR-0001/rule", {"rule": "An invented obligation."},
                        {"source_handle": "ADR-0001/history"}):
                resolver.write_text(json.dumps({"historical_slugs": {"history": row}}))
                with self.subTest(row=row), self.assertRaises(OSError):
                    driver._resolver_slugs(resolver)


class PublishedHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PublishedConsumers.setUpClass.__func__(cls)
        for path, text in fixtures.driver.build(cls.root).items():
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        cls.tree = cls.root / "docs"
        cls.reviews = cls.adrs / "reviews"; cls.reviews.mkdir()
        cls.report = cls.reviews / "2026-10-03.md"
        cls.report.write_text(reports._lifecycle("2026-10-03", findings=["adr-review-history"],
            records=[("2026-10-03", "adr-review-history", 1, "resolved", "rule:rotation")]))
        sup.commit_all(cls.root, "synthetic historical report and summaries")

    def test_historical_locator_resolves_mixed_source_not_live_replacement(self):
        before = fixtures.snapshot(self.root)
        self.assertTrue(driver._handle_exists("rule:rotation", self.tree))
        path, text, _, _ = driver.build(self.root)
        self.assertIn("resolved", text)
        self.assertEqual(path, self.reviews / "index.md")
        self.assertEqual(fixtures.snapshot(self.root), before)
        maps = driver._resolver_slugs(self.adrs / "summaries/resolver.json")
        self.assertNotIn("rotation", maps["slugs"])
        self.assertNotIn("rotation", maps["retired_slugs"])
        self.assertEqual(maps["historical_slugs"]["rotation"]["source_handle"], "ADR-0110/rotation")

    def test_missing_publication_refuses_even_report_without_rule_locator(self):
        witness = self.fixture.witness; original = witness.read_bytes()
        report = self.report.read_bytes()
        try:
            witness.unlink()
            self.report.write_text(reports._lifecycle("2026-10-03"))
            before = fixtures.snapshot(self.root)
            result = subprocess.run([sys.executable, str(SCRIPTS / "generate-reviews-index.py"),
                "--repo-root", str(self.root)], env=sup.scrubbed_env(), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(fixtures.snapshot(self.root), before)
            with self.assertRaises(migration.Refused): driver.build(self.root)
        finally:
            witness.write_bytes(original); self.report.write_bytes(report)

    def test_forged_historical_destination_refuses_without_output(self):
        resolver = self.adrs / "summaries/resolver.json"; original = resolver.read_bytes()
        try:
            data = json.loads(original)
            data["historical_slugs"]["rotation"]["destination"]["source_adr"] = "ADR-9999"
            resolver.write_text(json.dumps(data)); before = fixtures.snapshot(self.root)
            with self.assertRaises((OSError, migration.Refused)):
                driver._handle_exists("rule:rotation", self.tree)
            self.assertEqual(fixtures.snapshot(self.root), before)
        finally: resolver.write_bytes(original)

    def test_missing_historical_source_is_refusal_not_existing_replacement(self):
        source = self.adrs / "ADR-0110-source.md"; original = source.read_bytes()
        try:
            source.unlink(); before = fixtures.snapshot(self.root)
            with self.assertRaises(migration.Refused):
                driver._handle_exists("rule:rotation", self.tree)
            self.assertEqual(fixtures.snapshot(self.root), before)
        finally: source.write_bytes(original)

    def test_current_signed_replacement_source_and_retained_context_drift_refuse(self):
        # The binding records the exact retained context locator.
        context = self.root / self.fixture.proof.binding["context"]["path"]
        mutations = [(self.adrs / "doctrine/reconciliations.yml", b"broken: ["),
            (self.adrs / "ADR-0146-authorizer.md", None),
            (self.adrs / "ADR-0110-source.md", None), (context, b"{}")]
        report_original = self.report.read_bytes()
        try:
            self.report.write_text(reports._lifecycle("2026-10-03"))
            for path, changed in mutations:
                original = path.read_bytes()
                if changed is None:
                    changed = original.replace(b"status: Accepted", b"status: Proposed") if "0146" in path.name else original.replace(b"inconsistent reports", b"different reports")
                try:
                    path.write_bytes(changed); before = fixtures.snapshot(self.root)
                    with self.subTest(path=path.name), self.assertRaises(migration.Refused):
                        driver.build(self.root)
                    self.assertEqual(fixtures.snapshot(self.root), before)
                finally: path.write_bytes(original)
        finally: self.report.write_bytes(report_original)
