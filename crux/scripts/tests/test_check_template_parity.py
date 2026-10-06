"""Test suite for check_template_parity.py (importable module) and its CLI.

Per ADR-0050 Test plan: 5 acceptance cases exercised against synthetic
tempdir-based fixtures — never the real repo root. One exception:
ShippedManifestLivenessTests binds the SHIPPED manifest to the real repo —
a STALE clause is a dead guard that still exits 0 by design, so liveness
must be asserted as a test.

Covers:
  (a) DRIFT fixture → a P2 finding naming the section
  (b) CLEAN inverse → zero findings (in-sync pair)
  (c) DOWNSTREAM simulation → no twin present → 0 findings, no crash
  (d) STALE-manifest → anchor/pattern gone from canonical (twin present) → P3 stale
  (e) Harness-conformance → CLI exit codes: 0 on parity, 1 on drift, 2 on bad manifest

Uses only stdlib (unittest, tempfile, json, os, sys, pathlib, subprocess).
Every test uses tempfile.TemporaryDirectory as a synthetic root — never this
repo's real root.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# tests/ -> scripts/ -> crux/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
CLI = SCRIPTS_DIR / "check_template_parity.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_template_parity as ctp  # noqa: E402


# ──────────────────────────────── helpers ────────────────────────────────────

def _make_canonical(root: Path, body: str) -> Path:
    """Write docs/AGENTS.md in a synthetic root."""
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    p = docs / "AGENTS.md"
    p.write_text(body, encoding="utf-8")
    return p


def _make_twin(root: Path, body: str) -> Path:
    """Write crux/templates/AGENTS.md.tmpl in a synthetic root."""
    tmpl_dir = root / "crux" / "templates"
    tmpl_dir.mkdir(parents=True, exist_ok=True)
    p = tmpl_dir / "AGENTS.md.tmpl"
    p.write_text(body, encoding="utf-8")
    return p


def _make_manifest(root: Path, clauses: list) -> Path:
    """Write a synthetic parity manifest alongside the checker location."""
    p = root / "test_manifest.json"
    p.write_text(
        json.dumps(
            {
                "_doc": "Synthetic manifest for testing.",
                "clauses": clauses,
            }
        ),
        encoding="utf-8",
    )
    return p


_CLAUSE_TEMPLATE = {
    "id": "test-clause",
    "section": "§test section",
    "canonical": "docs/AGENTS.md",
    "twin": "crux/templates/AGENTS.md.tmpl",
    "anchor": "## Test Heading",
    "pattern": "SENTINEL-VALUE",
}

_CANONICAL_WITH_VALUE = (
    "## Test Heading\n"
    "\nSome text with SENTINEL-VALUE embedded here.\n"
    "\n## Next Heading\n"
)

_TWIN_WITH_VALUE = (
    "## Test Heading\n"
    "\nSome text with SENTINEL-VALUE embedded here.\n"
    "\n## Next Heading\n"
)

_TWIN_WITHOUT_VALUE = (
    "## Test Heading\n"
    "\nSome text WITHOUT the critical pattern embedded here.\n"
    "\n## Next Heading\n"
)


# ──────────────────────────────── base class ─────────────────────────────────


class ParityTestCase(unittest.TestCase):
    """Each test gets a fresh tempdir as the synthetic repo root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="crux-parity-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


# ──────────────────────────── (a) DRIFT fixture ───────────────────────────────


class TestDriftFixture(ParityTestCase):
    """A canonical section that contains the pattern value, but the twin's
    section is missing it → a P2 DRIFT finding naming the section."""

    def test_drift_yields_p2_finding(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITHOUT_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["id"], "test-clause")
        self.assertEqual(r["status"], "DRIFT")
        self.assertEqual(r["severity"], "P2")
        # The finding must name the section so cleanup-campsite can surface it.
        self.assertIn("§test section", r["detail"])

    def test_drift_names_divergent_section(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITHOUT_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)
        self.assertTrue(
            any("§test section" in r.get("detail", "") for r in results),
            f"expected section name in detail; got {results}",
        )


# ──────────────────────────── (b) CLEAN inverse ───────────────────────────────


class TestCleanInverse(ParityTestCase):
    """An in-sync canonical + twin pair → zero findings."""

    def test_in_sync_pair_yields_zero_findings(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        actionable = [r for r in results if r["status"] not in ("OK",)]
        self.assertEqual(
            len(actionable),
            0,
            f"Expected zero actionable findings; got {results}",
        )

    def test_in_sync_status_is_ok(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "OK")


# ──────────────────────────── (c) DOWNSTREAM simulation ──────────────────────


class TestDownstreamSimulation(ParityTestCase):
    """No crux/templates/*.tmpl twin present → zero findings AND no crash.
    Validates the per-entry self-detection predicate (ADR-0047 §3)."""

    def test_no_twin_yields_zero_findings(self):
        # canonical exists, twin does NOT
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        # deliberately do NOT create crux/templates/AGENTS.md.tmpl
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(
            len(results),
            0,
            f"Expected 0 findings with no twin; got {results}",
        )

    def test_no_twin_no_crash_multiple_clauses(self):
        """Multiple entries, none of their twins present → clean 0-finding no-op."""
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        clause2 = {**_CLAUSE_TEMPLATE, "id": "test-clause-2", "section": "§other"}
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE, clause2])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 0)

    def test_canonical_also_absent_no_crash(self):
        """Neither canonical nor twin present → still a clean 0-finding no-op,
        because twin absence is checked first (per-entry self-detection fires
        before any file reads)."""
        # neither file created
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 0)


# ──────────────────────────── (d) STALE manifest ─────────────────────────────


class TestStaleManifest(ParityTestCase):
    """A manifest entry whose anchor/pattern no longer resolves in the canonical
    (twin IS present) → a P3 STALE finding; the entry must NOT report OK.
    (ADR-0047 §3 resolved-question: stale replaces parity — never a vacuous green.)"""

    def test_anchor_absent_from_canonical_only_is_drift(self):
        """One-sided anchor loss is DRIFT, not stale (rule:one-sided-anchor-is-drift-absent-file-is-stale).

        The canonical lost the heading and the twin still carries it, so the two
        surfaces disagree. Reporting this as a P3 stale exited 0 on exactly the
        deletion the check exists to catch.
        """
        stale_canonical = "## Some Other Heading\n\nContent here.\n"
        _make_canonical(self.root, stale_canonical)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["status"], "DRIFT")
        self.assertEqual(r["severity"], "P2")

    def test_anchor_absent_from_both_sides_is_stale(self):
        """Two-sided anchor loss stays STALE/P3 — the manifest names a heading
        neither file carries, which is a signal about the checker rather than
        evidence a shipped surface drifted."""
        gone = "## Some Other Heading\n\nContent here.\n"
        _make_canonical(self.root, gone)
        _make_twin(self.root, gone)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["status"], "STALE")
        self.assertEqual(r["severity"], "P3")

    def test_stale_entry_is_not_ok(self):
        stale_canonical = "## Some Other Heading\n\nContent here.\n"
        _make_canonical(self.root, stale_canonical)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        ok_results = [r for r in results if r["status"] == "OK"]
        self.assertEqual(
            len(ok_results),
            0,
            f"Stale entry must NOT read as OK; got {results}",
        )

    def test_pattern_matching_nothing_in_canonical_only_is_drift(self):
        """A pattern matching nothing in the canonical while the twin still
        carries it is a one-sided loss, so it is DRIFT and exits non-zero. It
        was STALE/P3 at exit 0 until ADR-0117, which is how a canonical-side
        deletion passed the gate it was supposed to trip."""
        canonical_no_pattern = (
            "## Test Heading\n"
            "\nContent with no sentinel here.\n"
            "\n## Next Heading\n"
        )
        _make_canonical(self.root, canonical_no_pattern)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["status"], "DRIFT")
        self.assertEqual(r["severity"], "P2")

    def test_pattern_matching_nothing_on_either_side_is_stale(self):
        """Two-sided: the manifest names text neither file carries. That stays
        STALE/P3 and does not flip the exit, which is the predecessor's
        reasoning kept intact."""
        no_pattern = (
            "## Test Heading\n"
            "\nContent with no sentinel here.\n"
            "\n## Next Heading\n"
        )
        _make_canonical(self.root, no_pattern)
        _make_twin(self.root, no_pattern)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["status"], "STALE")
        self.assertEqual(r["severity"], "P3")

    def test_stale_guard_does_not_fire_without_twin(self):
        """Stale guard must be gated behind twin presence — downstream (no twin)
        it cannot fire (ADR-0047 §3)."""
        stale_canonical = "## No Anchor Here\n\nContent.\n"
        _make_canonical(self.root, stale_canonical)
        # NO twin
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        # Per-entry self-detection fires: twin absent → skip entirely, 0 results
        self.assertEqual(len(results), 0)


# ──────────────────── (e) Harness-conformance — CLI exit codes ────────────────


class TestCLIExitCodes(ParityTestCase):
    """Validates CLI main() exit-code contract:
      0 = parity / inert no-op
      1 = drift found
      2 = missing / invalid manifest
    """

    def run_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            cwd=str(self.root),
            timeout=60,
        )

    def test_exit_0_on_parity(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exit_1_on_drift(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITHOUT_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        self.assertEqual(result.returncode, 1, result.stderr)

    def test_exit_2_on_missing_manifest(self):
        result = self.run_cli(
            "--manifest", str(self.root / "nonexistent_manifest.json"),
            "--root", str(self.root),
        )
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_exit_2_on_invalid_json_manifest(self):
        bad_manifest = self.root / "bad_manifest.json"
        bad_manifest.write_text("{not valid json", encoding="utf-8")

        result = self.run_cli(
            "--manifest", str(bad_manifest), "--root", str(self.root)
        )

        self.assertEqual(result.returncode, 2, result.stderr)

    def test_exit_0_on_downstream_no_twin(self):
        """No twin → inert no-op → exit 0 (validated by CLI contract)."""
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        # no twin
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exit_0_on_stale_only(self):
        """P3-stale alone does NOT flip exit to 1.
        Rationale: stale is a maintenance signal about the checker manifest
        itself, not evidence of shipped-template drift. P1/P2 actionable
        findings (drift) exit 1; P3-only (stale) exits 0 so downstream CI that
        runs on non-dev repos does not break on a stale-manifest condition that
        can only be fixed by a crux developer."""
        # TWO-SIDED stale: the anchor is absent from BOTH files. A one-sided
        # absence is DRIFT after ADR-0117 and exits 1, so a one-sided fixture
        # here would assert the opposite of the contract.
        stale_both = "## No Matching Anchor\n\nContent here.\n"
        _make_canonical(self.root, stale_both)
        _make_twin(self.root, stale_both)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        self.assertEqual(result.returncode, 0, result.stderr)


# ──────────────────── stale token visible in output ──────────────────────────


class TestStaleOutputVisibility(ParityTestCase):
    """A stale entry must appear visibly in CLI output and must NOT read
    as 'in parity' (ADR-0047: stale must be VISIBLE in output)."""

    def run_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            cwd=str(self.root),
            timeout=60,
        )

    def test_stale_visible_in_output(self):
        stale_canonical = "## No Matching Anchor\n\nContent here.\n"
        _make_canonical(self.root, stale_canonical)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        combined = result.stdout + result.stderr
        self.assertIn("stale", combined.lower(), f"STALE token absent in output:\n{combined}")

    def test_stale_not_printed_as_parity(self):
        stale_canonical = "## No Matching Anchor\n\nContent here.\n"
        _make_canonical(self.root, stale_canonical)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        # Must not say "parity" in the per-finding line for the stale entry
        for line in (result.stdout + result.stderr).splitlines():
            if "test-clause" in line:
                self.assertNotIn("parity", line.lower(), f"stale entry reads as parity: {line!r}")


# ──────────────────── malformed-manifest robustness (regression) ─────────────


class TestMalformedEntry(ParityTestCase):
    """A clause entry missing a required key must surface as a usage error
    (exit 2 / ValueError), NOT an uncaught KeyError traceback — and the
    validation must run even when the twin is absent (a malformed manifest is a
    real error everywhere, including downstream)."""

    def run_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            cwd=str(self.root),
            timeout=60,
        )

    def test_entry_missing_twin_key_raises_valueerror(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        bad = {k: v for k, v in _CLAUSE_TEMPLATE.items() if k != "twin"}
        manifest = _make_manifest(self.root, [bad])

        with self.assertRaises(ValueError):
            ctp.check_parity(manifest, self.root)

    def test_entry_missing_anchor_key_cli_exit_2(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        bad = {k: v for k, v in _CLAUSE_TEMPLATE.items() if k != "anchor"}
        manifest = _make_manifest(self.root, [bad])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        self.assertEqual(result.returncode, 2, result.stderr)
        # Must be a clean usage error, not a Python traceback.
        self.assertNotIn("Traceback", result.stderr)

    def test_malformed_entry_errors_even_with_no_twin(self):
        """Validation runs before the twin-presence skip — a malformed entry is
        a loud error even downstream (no twin), never a silent skip."""
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        # NO twin
        bad = {k: v for k, v in _CLAUSE_TEMPLATE.items() if k != "canonical"}
        manifest = _make_manifest(self.root, [bad])

        with self.assertRaises(ValueError):
            ctp.check_parity(manifest, self.root)


# ──────────────────── grouped-pattern safety (regression) ────────────────────


class TestGroupedPattern(ParityTestCase):
    """A manifest pattern carrying capture group(s) must not crash: re.findall
    would return tuples and `.lower()` would raise AttributeError, so the
    checker compares whole-match strings instead."""

    def test_multi_group_pattern_does_not_crash(self):
        canon = "## Test Heading\n\nvalue: SENTINEL-VALUE here.\n\n## Next\n"
        twin = "## Test Heading\n\nvalue: SENTINEL-VALUE here.\n\n## Next\n"
        _make_canonical(self.root, canon)
        _make_twin(self.root, twin)
        clause = {**_CLAUSE_TEMPLATE, "pattern": r"(SENTINEL)-(VALUE)"}
        manifest = _make_manifest(self.root, [clause])

        # Must not raise AttributeError; whole-match "SENTINEL-VALUE" present in
        # both → OK.
        results = ctp.check_parity(manifest, self.root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "OK")


# ──────────────────── symmetric normalization (regression) ───────────────────


class TestSymmetricNormalization(ParityTestCase):
    """A captured canonical value containing collapsible whitespace must not
    read as a false DRIFT: both sides are normalized before the substring test."""

    def test_whitespace_value_is_not_false_drift(self):
        # canonical and twin are byte-identical; the pattern captures a value
        # with a double space / tab that _normalize collapses.
        body = "## Test Heading\n\nstate:   done now\n\n## Next\n"
        _make_canonical(self.root, body)
        _make_twin(self.root, body)
        clause = {**_CLAUSE_TEMPLATE, "pattern": r"state:\s+done"}
        manifest = _make_manifest(self.root, [clause])

        results = ctp.check_parity(manifest, self.root)
        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]["status"], "OK", f"identical sources flagged drift: {results}"
        )


# ──────────────────── fence-aware anchor search (regression) ─────────────────


class TestFenceAwareAnchorSearch(ParityTestCase):
    """The anchor SEARCH (not just heading termination) must skip fenced
    blocks: a fenced example that merely quotes the anchor text must not be
    mistaken for the real heading."""

    def test_anchor_inside_fence_is_not_matched(self):
        # The anchor text appears first inside a code fence (as an example),
        # then later as the real heading whose section actually contains the
        # value. If the search matched the fenced occurrence, the value would
        # appear "missing" → false DRIFT.
        canon = (
            "# Doc\n\n"
            "```\n"
            "## Test Heading\n"   # fenced example — must be ignored
            "```\n\n"
            "## Test Heading\n"   # the real heading
            "\nSENTINEL-VALUE lives here.\n"
            "\n## Next Heading\n"
        )
        twin = canon
        _make_canonical(self.root, canon)
        _make_twin(self.root, twin)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)
        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]["status"], "OK", f"fenced anchor mis-matched: {results}"
        )


# ──────────── (MF-2a) no-pattern whole-section comparison — both arms ─────────


class TestNoPatternWholeSection(ParityTestCase):
    """A clause that OMITS `pattern` entirely → the checker compares the whole
    normalized section bodies. Exercises BOTH arms: identical → OK, differing
    → P2 DRIFT whose detail names the section."""

    def _no_pattern_clause(self) -> dict:
        return {k: v for k, v in _CLAUSE_TEMPLATE.items() if k != "pattern"}

    def test_no_pattern_identical_sections_ok(self):
        body = "## Test Heading\n\nIdentical body text.\n\n## Next Heading\n"
        _make_canonical(self.root, body)
        _make_twin(self.root, body)
        manifest = _make_manifest(self.root, [self._no_pattern_clause()])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "OK")

    def test_no_pattern_differing_bodies_drift_p2(self):
        canon = "## Test Heading\n\nCanonical body text.\n\n## Next Heading\n"
        twin = "## Test Heading\n\nDIFFERENT twin body text.\n\n## Next Heading\n"
        _make_canonical(self.root, canon)
        _make_twin(self.root, twin)
        manifest = _make_manifest(self.root, [self._no_pattern_clause()])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["status"], "DRIFT")
        self.assertEqual(r["severity"], "P2")
        self.assertIn("§test section", r["detail"])


# ──────────────── (MF-2b) twin section absent → DRIFT P2 ──────────────────────


class TestTwinSectionAbsent(ParityTestCase):
    """The twin FILE exists but LACKS the anchor heading → DRIFT P2 with detail
    'missing entirely from twin'. Distinct from the pattern-value-missing DRIFT
    path: here the whole section is gone from the twin."""

    def test_twin_missing_anchor_yields_drift_p2(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        # twin file present but has no "## Test Heading" anchor
        _make_twin(self.root, "## Some Other Heading\n\nUnrelated content.\n")
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["status"], "DRIFT")
        self.assertEqual(r["severity"], "P2")
        self.assertIn("is absent from", r["detail"])
        self.assertIn("the two surfaces disagree", r["detail"])


# ──────────────────── (MF-2c) non-UTF-8 file → exit 2 ────────────────────────


class TestNonUtf8File(ParityTestCase):
    """A canonical (twin present) with non-UTF-8 bytes → exit 2 with the
    'file is not valid UTF-8' message, no traceback."""

    def run_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            cwd=str(self.root),
            timeout=60,
        )

    def test_non_utf8_canonical_exit_2_no_traceback(self):
        # canonical written with non-UTF-8 bytes; twin present so the entry is
        # NOT skipped and the canonical read is reached.
        docs = self.root / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "AGENTS.md").write_bytes(b"\xff\xfe## Test Heading\n")
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("file is not valid UTF-8", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


# ──────────── (MF-2d) canonical absent but twin present → STALE P3 ────────────


class TestCanonicalAbsentTwinPresent(ParityTestCase):
    """Twin present, canonical FILE missing → P3 STALE whose detail says
    'canonical file not found ... (twin ... present; manifest stale?)'.
    Distinct from the anchor-absent and pattern-matches-nothing stale arms;
    distinct from test_canonical_also_absent_no_crash (there the twin is also
    absent → per-entry skip fires first)."""

    def test_canonical_missing_twin_present_stale_p3(self):
        # do NOT create the canonical; DO create the twin
        _make_twin(self.root, _TWIN_WITH_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["status"], "STALE")
        self.assertEqual(r["severity"], "P3")
        self.assertIn("canonical file not found", r["detail"])
        self.assertIn("manifest stale?", r["detail"])


# ──────────────── (MF-3) invalid manifest regex → exit 2, no traceback ────────


class TestInvalidPatternRegex(ParityTestCase):
    """An INVALID manifest regex `pattern` raises re.error; the checker must map
    it to a clean schema error → exit 2 with no traceback, consistent with the
    other manifest-error paths (regression for the exit-code-contract break)."""

    def run_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            cwd=str(self.root),
            timeout=60,
        )

    def test_bad_regex_pattern_raises_valueerror(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        clause = {**_CLAUSE_TEMPLATE, "pattern": "(unclosed"}
        manifest = _make_manifest(self.root, [clause])

        with self.assertRaises(ValueError):
            ctp.check_parity(manifest, self.root)

    def test_bad_regex_pattern_cli_exit_2_no_traceback(self):
        _make_canonical(self.root, _CANONICAL_WITH_VALUE)
        _make_twin(self.root, _TWIN_WITH_VALUE)
        clause = {**_CLAUSE_TEMPLATE, "pattern": "(unclosed"}
        manifest = _make_manifest(self.root, [clause])

        result = self.run_cli("--manifest", str(manifest), "--root", str(self.root))

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class ShippedManifestLivenessTests(unittest.TestCase):
    """The shipped manifest must resolve against this repo. A STALE clause is
    a dead guard, and STALE does not flip the CLI exit code (by design) — so
    this test is the enforcement that the guard is alive."""

    def test_shipped_manifest_has_no_stale_clauses(self):
        # The canonical dogfood file is a dev-only surface: absent in the
        # staged public artifact (ADR-0036 boundary), the manifest's entries
        # are inert by design (twin-absent skip), so liveness is asserted only
        # where the canonical surface lives.
        try:
            from ._dev_surface import require_dev_surface
        except ImportError:  # unittest discover imports test modules top-level
            from _dev_surface import require_dev_surface
        require_dev_surface(self, REPO_ROOT / "bionic" / "AGENTS.md", "bionic/AGENTS.md")
        results = ctp.check_parity(SCRIPTS_DIR / "template_parity_manifest.json", REPO_ROOT)
        self.assertTrue(results, "shipped manifest produced zero clauses — twins missing?")
        stale = [r for r in results if r["status"] == "STALE"]
        self.assertEqual(stale, [], f"dead parity clauses: {[r['id'] for r in stale]}")


# ─────────── the CommonMark indent bound on the fence marker (A1) ────────────


class TestFenceIndentBound(ParityTestCase):
    """A fence opener and closer are bounded at THREE leading columns.

    CommonMark reads four or more leading spaces as an indented code block, so
    an indented run is not a fence marker at all. Without the bound a
    four-space-indented run OPENED a phantom fence here, and every later line
    read as fenced content — which swallowed the real anchor heading and turned
    a live clause into a silent STALE. There is no longer a twin of this bound
    to keep in step: the subset lives in `crux/scripts/md_fences.py`, both
    this checker and `adr-signals.py` import it, and its enumerated contract
    is pinned by `crux/scripts/tests/test_md_fences.py`. The cases below stay
    here because they drive the bound THROUGH this checker's own fence
    tracking, which the shared suite does not exercise.
    """

    def test_a_four_space_indented_run_opens_no_phantom_fence(self):
        # The indented run is an indented code block, never a fence opener, so
        # the REAL anchor below it is still found and the clause resolves.
        # Fence-blind, the run opens a fence that never closes, the anchor is
        # swallowed as content, and the clause reports STALE instead.
        body = (
            "# Doc\n\n"
            "    ```\n"
            "\n## Test Heading\n"
            "\nSome text with SENTINEL-VALUE embedded here.\n"
            "\n## Next Heading\n"
        )
        _make_canonical(self.root, body)
        _make_twin(self.root, body)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])

        results = ctp.check_parity(manifest, self.root)
        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]["status"], "OK",
            f"indented run was read as a fence: {results}")

    def test_the_bound_is_three_columns_and_a_tab_counts_as_four(self):
        # PAIRED POSITIVE CONTROL first: at three columns the identical run IS
        # a marker, so the refusals below are the indent and not the shape.
        self.assertIsNotNone(ctp._fence_marker("```"))
        self.assertIsNotNone(ctp._fence_marker("   ```"))
        # Refused: four spaces, and a tab, which expands to four columns.
        self.assertIsNone(ctp._fence_marker("    ```"))
        self.assertIsNone(ctp._fence_marker("\t```"))
        # The tilde spelling takes the same bound.
        self.assertIsNotNone(ctp._fence_marker("   ~~~"))
        self.assertIsNone(ctp._fence_marker("    ~~~"))

    def test_a_non_space_prefix_does_not_zero_the_indent_for_this_checker(self):
        """The defect the extraction closed, driven through this file's own
        binding: the indent was measured on SPACES while the run was matched
        after stripping ALL Unicode whitespace, so one U+00A0 or U+2028 in
        front of a four-space indent made it measure as zero."""
        for prefix in ("\xa0", "\u2028", "\x0b", "\r"):
            with self.subTest(prefix=repr(prefix)):
                self.assertIsNone(ctp._fence_marker(prefix + "```"))
                self.assertIsNone(ctp._fence_marker(prefix + "    ```"))
        # PAIRED POSITIVE CONTROL: the same run with nothing in front of it
        # is still a marker, so the refusals above are the prefix and not a
        # marker that stopped recognizing anything.
        self.assertIsNotNone(ctp._fence_marker("```"))


class TestForgedLineBoundaries(ParityTestCase):
    """A hostile character MUST NOT be able to disable a live clause.

    THE FAILURE MODE IS A GREEN RUN, WHICH IS WHY THIS CLASS EXISTS. When
    `_section_after` cannot resolve a section it reports P3 STALE, and P3 does
    NOT flip the exit code — by design, because stale is a maintenance signal
    about the manifest rather than evidence of shipped-template drift. So a
    character that makes the canonical section unresolvable does not merely
    degrade the check: it turns a real P2 DRIFT into an exit-0 run, and the
    drifted clause ships. No attacker is needed for either vector; a stray
    U+00A0 pasted into either AGENTS.md twin is enough.

    TWO INDEPENDENT VECTORS, both closed, both pinned here:

      * THE LINE SPLIT. `str.splitlines()` ends a line on nine terminators no
        Markdown reader ends a line on, so a fence run spelled mid-sentence
        after one of them became a real fence and swallowed the anchor. Closed
        by `_lines`, the shared splitter in `md_fences`.
      * THE HEADING TEST. A bare `str.lstrip()` before `startswith("#")`
        strips the same over-wide class, so a `#` spelled after U+00A0 became
        a real heading and terminated the section early. Closed by
        `lstrip(" \\t")`. U+00A0 is NOT a `splitlines()` terminator, which is
        how these two are known to be independent rather than one defect seen
        twice.

    EVERY CASE CARRIES A POSITIVE CONTROL. The twin in these fixtures has
    genuinely dropped the governed value, so the honest reading of every
    document below is a P2 DRIFT at exit 1. A checker that reported STALE for
    everything, or OK for everything, fails `test_the_control_pair_really_drifts`
    below and then fails every row here.
    """

    #: Every character `str.splitlines()` treats as a line terminator and no
    #: Markdown reader does, plus U+00A0, which `splitlines()` does NOT split
    #: on and `str.lstrip()` DOES strip.
    HOSTILE = (
        ("U+000B", "\v"), ("U+000C", "\f"), ("U+000D", "\r"),
        ("U+001C", "\x1c"), ("U+001D", "\x1d"), ("U+001E", "\x1e"),
        ("U+0085", "\x85"), ("U+2028", "\u2028"), ("U+2029", "\u2029"),
        ("U+00A0", "\xa0"),
    )

    def _check(self, canonical: str):
        _make_canonical(self.root, canonical)
        _make_twin(self.root, _TWIN_WITHOUT_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])
        results = ctp.check_parity(manifest, self.root)
        self.assertEqual(len(results), 1, results)
        return results[0]

    def _assert_real_drift(self, result, note: str):
        """DRIFT at P2, naming the value — not merely 'not STALE'.

        Asserting the absence of STALE would pass on a checker that returned
        nothing at all, which is the vacuous shape this round is closing.
        """
        self.assertEqual(result["status"], "DRIFT", f"{note}: {result}")
        self.assertEqual(result["severity"], "P2", f"{note}: {result}")
        self.assertIn("SENTINEL-VALUE", result["detail"], f"{note}: {result}")

    def test_the_control_pair_really_drifts(self):
        """The positive control for every row below.

        Without it, a checker that reported DRIFT unconditionally would pass
        the whole class, and so would one whose fixtures never triggered the
        drift in the first place.
        """
        self._assert_real_drift(self._check(_CANONICAL_WITH_VALUE), "control")

    def test_a_forged_fence_run_before_the_anchor_does_not_hide_the_drift(self):
        for name, ch in self.HOSTILE:
            with self.subTest(character=name):
                canonical = ("# Doc\n\nordinary prose" + ch + "```\n\n"
                             + _CANONICAL_WITH_VALUE)
                self._assert_real_drift(self._check(canonical), name)

    def test_a_forged_heading_inside_the_section_does_not_hide_the_drift(self):
        for name, ch in self.HOSTILE:
            with self.subTest(character=name):
                canonical = ("## Test Heading\n\n"
                             + ch + "# not really a heading\n"
                             "\nSome text with SENTINEL-VALUE embedded here.\n"
                             "\n## Next Heading\n")
                self._assert_real_drift(self._check(canonical), name)

    def test_a_forged_heading_before_the_anchor_does_not_hide_the_drift(self):
        for name, ch in self.HOSTILE:
            with self.subTest(character=name):
                canonical = ("# Doc\n\n" + ch + "# not really a heading\n\n"
                             + _CANONICAL_WITH_VALUE)
                self._assert_real_drift(self._check(canonical), name)

    def test_a_real_fence_and_a_real_heading_still_do_their_jobs(self):
        """The anti-vacuity pair. `lstrip(" \\t")` and `_lines` must still
        SEE the constructs they bound — a guard that stopped recognizing
        fences and headings would pass every row above."""
        # A real fenced block still hides an anchor look-alike inside it, so
        # the REAL anchor further down is the one that resolves.
        fenced = ("# Doc\n\n```\n## Test Heading\nSENTINEL-VALUE\n```\n\n"
                  + _CANONICAL_WITH_VALUE)
        self._assert_real_drift(self._check(fenced), "real fence")
        # A real heading, indented up to three spaces, still terminates the
        # section — so the value below it is NOT part of the canonical section
        # and the clause reads STALE rather than DRIFT.
        truncated = ("## Test Heading\n\n   # a real heading\n"
                     "\nSome text with SENTINEL-VALUE embedded here.\n")
        result = self._check(truncated)
        self.assertEqual(result["status"], "STALE", result)

    def test_a_crlf_document_reads_exactly_as_its_lf_twin_does(self):
        """The paired control for reading untranslated.

        Disabling universal-newline translation is only safe if CRLF still
        reads correctly, and it does because `_lines` strips the one trailing
        `\\r` each CRLF line leaves. That strip was UNREACHABLE while the files
        were read through `read_text()` — translation removed every `\\r`
        before it ran — so this is the row that makes it live. Byte-for-byte
        the same document in both line endings must produce the same verdict.
        """
        lf = _CANONICAL_WITH_VALUE
        crlf = lf.replace("\n", "\r\n")
        self.assertNotEqual(lf, crlf)          # the fixture really differs

        _make_canonical(self.root, lf)
        _make_twin(self.root, _TWIN_WITHOUT_VALUE)
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])
        lf_result = ctp.check_parity(manifest, self.root)[0]

        _make_canonical(self.root, crlf)
        crlf_result = ctp.check_parity(manifest, self.root)[0]

        self._assert_real_drift(lf_result, "LF")
        self._assert_real_drift(crlf_result, "CRLF")
        self.assertEqual(lf_result, crlf_result)

    def test_a_crlf_document_in_sync_with_its_twin_is_still_clean(self):
        """The other sign of the CRLF control: an in-sync CRLF pair is OK, not
        drift. Without it, a checker that reported DRIFT for every CRLF file
        would pass the row above."""
        crlf = _CANONICAL_WITH_VALUE.replace("\n", "\r\n")
        _make_canonical(self.root, crlf)
        _make_twin(self.root, _TWIN_WITH_VALUE.replace("\n", "\r\n"))
        manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])
        result = ctp.check_parity(manifest, self.root)[0]
        self.assertEqual(result["status"], "OK", result)

    def test_the_exit_code_survives_the_forgery_end_to_end(self):
        """The findings above are asserted through `check_parity`; the thing
        that actually ships is the EXIT CODE, and the whole point of the
        defect is that it reached 0. Driven through the CLI so the P3-does-
        not-flip-the-exit rule is exercised rather than assumed."""
        for name, ch in (("U+000B", "\v"), ("U+2028", "\u2028"), ("U+00A0", "\xa0")):
            for label, canonical in (
                ("fence", "# Doc\n\nprose" + ch + "```\n\n" + _CANONICAL_WITH_VALUE),
                ("heading", "## Test Heading\n\n" + ch + "# forged\n"
                            "\nSome text with SENTINEL-VALUE embedded here.\n"
                            "\n## Next Heading\n"),
            ):
                with self.subTest(character=name, vector=label):
                    _make_canonical(self.root, canonical)
                    _make_twin(self.root, _TWIN_WITHOUT_VALUE)
                    manifest = _make_manifest(self.root, [_CLAUSE_TEMPLATE])
                    proc = subprocess.run(
                        [sys.executable, str(CLI),
                         "--manifest", str(manifest), "--root", str(self.root)],
                        capture_output=True, text=True, cwd=str(self.root), timeout=60,
                    )
                    self.assertEqual(proc.returncode, 1,
                                     f"{name}/{label}: {proc.stdout}{proc.stderr}")
                    self.assertIn("DRIFT", proc.stdout)



class CountComparisonTests(unittest.TestCase):
    """rule:parity-counts-match-on-both-sides — an alternation must match the
    same number of times on both sides.

    The presence test these replace could not see a deletion when another
    occurrence of the same alternation survived, which is how the shipped
    `adrs-append-only-freeze` clause guarded nothing: its `append-only`
    alternation matched the section heading and its `frozen` alternation matched
    an unrelated bullet 8.6 kB away.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _run(self, canon_body, twin_body, pattern):
        _make_canonical(self.root, f"## Test Heading\n{canon_body}\n## Next Heading\n")
        _make_twin(self.root, f"## Test Heading\n{twin_body}\n## Next Heading\n")
        clause = dict(_CLAUSE_TEMPLATE, pattern=pattern)
        manifest = _make_manifest(self.root, [clause])
        return ctp.check_parity(manifest, self.root)[0]

    def test_equal_counts_pass(self):
        r = self._run("alpha and alpha\n", "alpha and alpha\n", "alpha")
        self.assertEqual(r["status"], "OK")

    def test_one_of_two_occurrences_removed_from_canonical_is_drift(self):
        """The defect that made a clause vacuous: a surviving occurrence answered
        the presence question yes while the governed one was gone."""
        r = self._run("alpha once\n", "alpha and alpha\n", "alpha")
        self.assertEqual(r["status"], "DRIFT")
        self.assertEqual(r["severity"], "P2")
        self.assertIn("canonical=1", r["detail"])
        self.assertIn("twin=2", r["detail"])

    def test_one_of_two_occurrences_removed_from_twin_is_drift(self):
        r = self._run("alpha and alpha\n", "alpha once\n", "alpha")
        self.assertEqual(r["status"], "DRIFT")
        self.assertIn("canonical=2", r["detail"])
        self.assertIn("twin=1", r["detail"])

    def test_each_alternation_names_its_own_short_side(self):
        """Two alternations drifting in OPPOSITE directions.

        One label was derived from the first record and applied to the whole clause,
        so the finding sent a maintainer chasing the second alternation to the wrong
        file. `rule:parity-counts-match-on-both-sides` requires the side that holds
        fewer, per alternation.
        """
        r = self._run("alpha beta beta\n", "alpha alpha beta\n", "alpha|beta")
        self.assertEqual(r["status"], "DRIFT")
        d = r["detail"]
        self.assertIn("'alpha' canonical=1 twin=2 (canonical holds fewer)", d)
        self.assertIn("'beta' canonical=2 twin=1 (twin holds fewer)", d)

    def test_a_dead_alternation_is_still_named_when_a_sibling_drifts(self):
        """A dead-on-both alternation was dropped from the message entirely.

        Alone it reports STALE/P3; beside a drifting sibling it vanished at every
        severity, so a stale manifest entry was hidden by an unrelated finding.
        """
        r = self._run("alpha\n", "alpha alpha\n", "alpha|gamma")
        self.assertEqual(r["status"], "DRIFT")
        self.assertIn("'alpha' canonical=1 twin=2", r["detail"])
        self.assertIn("gamma", r["detail"])
        self.assertIn("manifest stale?", r["detail"])

    def test_equal_counts_of_zero_are_stale_not_a_pass(self):
        """0 == 0 is not parity: the manifest names text neither file carries."""
        r = self._run("nothing here\n", "nothing here\n", "alpha")
        self.assertEqual(r["status"], "STALE")
        self.assertEqual(r["severity"], "P3")

    def test_a_surviving_sibling_alternation_does_not_mask_a_loss(self):
        """The positive control for the whole change: `beta` is intact on both
        sides, and that must not hide `alpha` going one-sided."""
        r = self._run("beta only\n", "alpha and beta\n", "alpha|beta")
        self.assertEqual(r["status"], "DRIFT")
        self.assertIn("'alpha'", r["detail"])


class SplitterTests(unittest.TestCase):
    """The alternation reader MOVED into the shipped checker rather than being
    copied, so it needs its own cases there."""

    def test_nested_groups_character_classes_and_escapes(self):
        f = ctp.split_top_level_alternatives
        self.assertEqual(f("a|b"), ["a", "b"])
        self.assertEqual(f("a(b|c)|d"), ["a(b|c)", "d"])
        self.assertEqual(f("a[b|c]|d"), ["a[b|c]", "d"])
        self.assertEqual(f(r"a\|b"), [r"a\|b"])
        self.assertEqual(f("solo"), ["solo"])

    def test_a_leading_inline_flag_scopes_every_fragment(self):
        """A flag group at the head of a pattern applies to the whole pattern.

        This previously asserted `["(?i)a", "b"]` — it pinned the lossy behaviour as
        intended rather than flagging it, so the checker's own suite agreed that a
        later fragment should compile under different flags than its pattern.
        """
        f = ctp.split_top_level_alternatives
        self.assertEqual(f("(?i)a|b"), ["(?i)a", "(?i)b"])
        self.assertEqual(f("(?im)a|b"), ["(?im)a", "(?im)b"])

    def test_an_anchored_alternation_in_a_multiline_pattern_still_matches(self):
        """The consequence the flag loss produced, on the shape a manifest clause uses.

        Without the flag carried over, `^beta$` matched 0 times on BOTH sides, which
        this checker reports as a stale manifest entry — a clean exit for a clause
        whose text is present and governed.
        """
        import re as _re
        text = "alpha\nbeta\n"
        for frag in ctp.split_top_level_alternatives(r"(?m)^alpha$|^beta$"):
            self.assertEqual(len(_re.findall(frag, text)), 1,
                             f"fragment {frag!r} lost its pattern's flags")

    def test_every_fragment_compiles(self):
        import json as _json
        man = _json.loads(
            (SCRIPTS_DIR / "template_parity_manifest.json").read_text(encoding="utf-8")
        )
        clauses = man["clauses"] if isinstance(man, dict) and "clauses" in man else man
        n = 0
        for c in clauses:
            for alt in ctp.split_top_level_alternatives(c.get("pattern") or ""):
                re.compile(alt)
                n += 1
        self.assertGreater(n, 100, "splitter produced too few fragments to be measuring anything")



class ManifestAlternativeLivenessTests(unittest.TestCase):
    """Every TOP-LEVEL alternative of every clause pattern must resolve in its
    canonical section.

    Why this is separate from `ShippedManifestLivenessTests`: that test asks
    whether a clause resolves AT ALL, and a clause with one live alternative
    and three dead ones resolves fine. `check_parity` compares only the values
    that matched the canonical, so a dead alternative contributes no compared
    value and gates nothing — the clause still reports `ok` while the sentence
    it advertises is unguarded. That is not hypothetical: it is how a reworded
    canonical sentence silently disarmed its own clause, which the clause's
    `section` field still claimed to cover."""

    def test_every_alternative_resolves_in_canonical(self):
        try:
            from ._dev_surface import require_dev_surface
        except ImportError:  # unittest discover imports test modules top-level
            from _dev_surface import require_dev_surface
        require_dev_surface(self, REPO_ROOT / "bionic" / "AGENTS.md", "bionic/AGENTS.md")
        manifest = json.loads(
            (SCRIPTS_DIR / "template_parity_manifest.json").read_text(encoding="utf-8")
        )
        dead: list[str] = []
        checked = 0
        for entry in manifest["clauses"]:
            pattern = entry.get("pattern")
            if not pattern:
                continue  # whole-section comparison: no alternatives to check
            canonical = REPO_ROOT / entry["canonical"]
            if not canonical.exists():
                continue
            body = ctp._section_after(canonical.read_text(encoding="utf-8"), entry["anchor"])
            if body is None:
                continue  # a STALE anchor is the other test's finding, not this one
            for alt in ctp.split_top_level_alternatives(pattern):
                try:
                    compiled = re.compile(alt)
                except re.error:
                    continue  # a fragment that is not independently compilable
                checked += 1
                if not compiled.search(body):
                    dead.append(f"{entry['id']}: {alt[:70]}")
        self.assertGreater(checked, 0, "no alternatives checked — manifest empty or unreadable?")
        self.assertEqual(
            dead, [],
            "parity clause alternatives that match nothing in their canonical section, "
            "so they gate nothing while the clause still passes:\n  " + "\n  ".join(dead),
        )


if __name__ == "__main__":
    unittest.main()
