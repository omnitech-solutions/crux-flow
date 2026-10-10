"""Journal evidence over synthetic committed migration histories, never real approvals."""
from __future__ import annotations

import copy
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
import _council_gate_support as support
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
import test_implementation_authority as authority_fixtures
from journal_reference import artifact_references

SCRIPT = SCRIPTS / "check-journal-reference.py"


def run(root, artifact="ADR-0110", *, isolated=False):
    command = (["uv", "run", "--offline", "--no-project", "--no-config", str(SCRIPT)] if isolated else
               [sys.executable, str(SCRIPT)])
    return subprocess.run(command + ["--repo-root", str(root), "--artifact", artifact,
        "--month", "2026-10"], cwd=SCRIPTS.parents[1], env=support.scrubbed_env(),
        capture_output=True, text=True, timeout=120)


class JournalPublished(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PublishedConsumers.setUpClass.__func__(cls)
        (cls.root / "docs/manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [adrs, journal]\n')
        support.commit_all(cls.root, "synthetic valid journal layout")
        cls.resolver_path = cls.adrs / "summaries/resolver.json"
        cls.resolver = json.loads(fixtures.driver.build(cls.root)[cls.resolver_path])
        cls.resolver_path.parent.mkdir(exist_ok=True)
        cls.resolver_path.write_text(json.dumps(cls.resolver))
        cls.month = cls.root / "docs/journal/2026-10.md"
        cls.month.parent.mkdir(exist_ok=True)
        cls.month.write_text("Refs: rule:rotation\n")

    def setUp(self):
        self.month.write_text("Refs: rule:rotation\n")
        self.resolver_path.write_text(json.dumps(self.resolver))

    def checked(self, artifact="ADR-0110", *, isolated=False):
        before = fixtures.snapshot(self.root)
        result = run(self.root, artifact, isolated=isolated)
        self.assertEqual(fixtures.snapshot(self.root), before)
        return result

    def test_historical_slug_reflects_original_source_with_no_authority(self):
        result = self.checked()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        row = json.loads(result.stdout)["evidence"][0]
        historical = self.resolver["historical_slugs"]["rotation"]
        self.assertEqual(row["kind"], "historical-slug")
        self.assertEqual(row["authority"], "none")
        self.assertEqual(row["source_handle"], historical["source_handle"])
        self.assertEqual(row["source_identity"], historical["source_identity"])
        self.assertEqual(row["destination"], historical["destination"])
        self.assertNotIn("rule", row)
        wrong = self.checked("ADR-0146")
        self.assertEqual(wrong.returncode, 1, wrong.stdout + wrong.stderr)
        self.assertEqual(json.loads(wrong.stdout)["rejected"][0]["resolves_to"], "ADR-0110")

    def test_live_and_ordinary_retired_successor_lanes_keep_their_meaning(self):
        self.month.write_text("Refs: rule:surviving-obligation\n")
        result = self.checked()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["evidence"][0]["kind"], "resolved-slug")
        old = artifact_references("rule:old", "ADR-0102", dict(slugs={},
            retired_slugs={"old": ["ADR-0102/new"]}))
        self.assertEqual(old["evidence"][0]["kind"], "retired-slug")

    def test_empty_retired_row_names_historical_displacer_not_target_or_replacement(self):
        self.assertEqual(self.resolver["retired_slugs"]["old-rotation"], [])
        self.month.write_text("Refs: rule:old-rotation\n")
        result = self.checked()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        row = json.loads(result.stdout)["evidence"][0]
        edge = self.resolver["historical_displacements"]["ADR-0109/old-rotation"][0]
        self.assertEqual(row["kind"], "historical-retired-slug")
        self.assertEqual(row["authority"], "none")
        for key in ("target_handle", "source_displacer", "source_identity", "source_ref"):
            self.assertEqual(row[key], edge[key])
        for artifact in ("ADR-0109", "ADR-0146"):
            self.assertEqual(self.checked(artifact).returncode, 1)

    def test_forged_maps_refuse_even_a_matching_wikilink(self):
        self.month.write_text("Refs: [[adrs/ADR-0110-source]] rule:rotation\n")
        for fault in ("identity", "destination", "live", "empty-retired", "edge", "edge-ref", "duplicate"):
            forged = copy.deepcopy(self.resolver)
            if fault == "identity": forged["historical_slugs"]["rotation"]["source_identity"] = "f" * 64
            if fault == "destination": forged["historical_slugs"]["rotation"]["destination"]["source_adr"] = "ADR-0146"
            if fault == "live": forged["slugs"]["rotation"] = "ADR-0146/rotation-preserves-assessment-outcomes"
            if fault == "empty-retired": forged["retired_slugs"]["invented"] = []
            if fault == "edge": forged["historical_displacements"] = {}
            if fault == "edge-ref": forged["historical_displacements"]["ADR-0109/old-rotation"][0]["source_ref"]["path"] = "docs/adrs/ADR-0146-authorizer.md"
            if fault == "duplicate": forged["historical_displacements"]["ADR-0109/old-rotation"] *= 2
            self.resolver_path.write_text(json.dumps(forged))
            with self.subTest(fault=fault):
                result = self.checked()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["authority"], "none")
                self.assertIn("limit", json.loads(result.stdout))

    def test_missing_or_malformed_published_resolver_never_falls_back_to_wiki(self):
        self.month.write_text("Refs: [[adrs/ADR-0110-source]]\n")
        for raw in (None, b"invalid", b'{"historical_slugs":{},"historical_slugs":{}}',
                    b"[" * 10000 + b"0" + b"]" * 10000):
            if raw is None: self.resolver_path.unlink()
            else: self.resolver_path.write_bytes(raw)
            with self.subTest(raw=None if raw is None else (len(raw), raw[:40])):
                result = self.checked()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertTrue(result.stdout, result.stderr)
                self.assertIn("limit", json.loads(result.stdout))

    def test_actual_proof_and_dependency_failures_refuse_without_writes(self):
        self.month.write_text("Refs: [[adrs/ADR-0110-source]] rule:rotation\n")
        paths = (self.fixture.witness, self.fixture.f.path, self.adrs / "ADR-0110-source.md",
                 self.adrs / "doctrine/reconciliations.yml")
        for path in paths:
            original = path.read_bytes()
            try:
                for raw in (None, original + b"changed\n"):
                    if raw is None: path.unlink()
                    else: path.write_bytes(raw)
                    with self.subTest(path=path.name, missing=raw is None):
                        result = self.checked()
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        self.assertEqual(json.loads(result.stdout)["authority"], "none")
                        self.assertIn("limit", json.loads(result.stdout))
            finally: path.write_bytes(original)

    def test_isolated_uv_metadata_supplies_actual_proof_dependencies(self):
        result = self.checked(isolated=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["evidence"][0]["authority"], "none")

    def test_archived_source_retains_exact_identity_and_resolves_its_actual_path(self):
        source = self.adrs / "ADR-0110-source.md"
        original = source.read_bytes()
        archive = self.adrs / "archive"; archive.mkdir(exist_ok=True)
        destination = archive / source.name
        try:
            source.unlink()
            destination.write_bytes(original.replace(b"status: Accepted", b"status: Superseded") + b"\n")
            support.commit_all(self.root, "synthetic historical lifecycle move")
            current = json.loads(fixtures.driver.build(self.root)[self.resolver_path])
            self.resolver_path.write_text(json.dumps(current))
            result = self.checked()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            row = json.loads(result.stdout)["evidence"][0]
            self.assertEqual(row["source_identity"], self.resolver["historical_slugs"]["rotation"]["source_identity"])
            self.assertEqual(row["source_ref"]["path"], destination.relative_to(self.root).as_posix())
            self.assertEqual(row["authority"], "none")
        finally:
            if destination.exists(): destination.unlink()
            source.write_bytes(original)
            support.commit_all(self.root, "restore synthetic historical lifecycle source")


class JournalOriginal(unittest.TestCase):
    def test_exact_uncommitted_witness_refuses_even_matching_wiki(self):
        fixture = authority_fixtures.PublicationProof("runTest"); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.root / "docs/manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [adrs, journal]\n')
        support.commit_all(fixture.root, "synthetic committed journal layout")
        fixture.close()
        fixture.witness.write_text(json.dumps(fixture.locator(), sort_keys=True) + "\n")
        month = fixture.root / "docs/journal/2026-10.md"; month.parent.mkdir(exist_ok=True)
        month.write_text("[[adrs/ADR-0110-source]]")
        before = fixtures.snapshot(fixture.root)
        result = run(fixture.root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["limit"], "migration-publication-not-committed")
        self.assertEqual(fixtures.snapshot(fixture.root), before)

    def test_never_published_absence_keeps_wiki_and_unverifiable_slug_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
            month = root / "docs/journal/2026-10.md"; month.parent.mkdir(parents=True)
            (root / "docs/manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: [journal]\n')
            for text, code in (("[[adrs/ADR-0110-source]]", 0), ("rule:rotation", 1)):
                month.write_text(text); before = fixtures.snapshot(root)
                result = run(root)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertFalse(json.loads(result.stdout)["resolver_available"])
                self.assertEqual(fixtures.snapshot(root), before)

    def test_unproved_historical_projection_is_not_history_evidence(self):
        # The pure legacy interface never trusts a raw historical map as proof.
        forged = dict(slugs={}, retired_slugs={}, historical_slugs={"rotation": dict(
            source_handle="ADR-0110/rotation", source_identity="f" * 64,
            destination=dict(source_adr="ADR-0110", clause_sha256="e" * 64), replacements=[])})
        result = artifact_references("rule:rotation", "ADR-0110", forged)
        self.assertFalse(result["referenced"])
        self.assertEqual(result["rejected"][0]["kind"], "unresolved-slug")

    def test_never_published_projection_corruption_uses_shared_refusal_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
            month = root / "docs/journal/2026-10.md"; month.parent.mkdir(parents=True)
            month.write_text("[[adrs/ADR-0110-source]]")
            (root / "docs/manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: [journal]\n')
            resolver = root / "docs/adrs/summaries/resolver.json"; resolver.parent.mkdir(parents=True)
            original = json.dumps(dict(slugs={}, retired_slugs={}))
            for raw, code in ((original, 0), (original + "\nplain corrupt tail", 0), ("ambiguous", 1),
                              (json.dumps(dict(historical_slugs={"rotation": {}})), 1)):
                resolver.write_text(raw); before = fixtures.snapshot(root)
                result = run(root)
                with self.subTest(raw=raw):
                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                    if code == 1: self.assertIn("limit", json.loads(result.stdout))
                    self.assertEqual(fixtures.snapshot(root), before)
