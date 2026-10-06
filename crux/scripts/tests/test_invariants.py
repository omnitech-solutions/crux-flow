"""Tests for the invariants concern (ADR-0045 first slice).

Covers: the pin-level aggregation precedence and the five CHK-INV rules via the
reference checker `check_invariants.py`, plus the concern wiring (schema 4,
concern enabled, surfaces present) and the docs contract (§15 + the CHK-INV
rule names in audit-docs).

Runs under the uv lane (PyYAML). Uses tempdirs for the checker fixtures; never
mutates the real tree.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"

try:
    from ._dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface
except ImportError:  # unittest discover imports test modules top-level
    from _dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

_spec = importlib.util.spec_from_file_location("check_invariants", SCRIPTS_DIR / "check_invariants.py")
_ci = importlib.util.module_from_spec(_spec)
if HAVE_YAML:
    _spec.loader.exec_module(_ci)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class AggregationTests(unittest.TestCase):
    def test_precedence_fail_wins(self):
        self.assertEqual(_ci.aggregate(["pass", "fail", "stale"]), "fail")

    def test_precedence_stale_over_pass(self):
        self.assertEqual(_ci.aggregate(["pass", "stale", "pass"]), "stale")

    def test_pass_when_only_pass_and_none(self):
        self.assertEqual(_ci.aggregate(["pass", "none"]), "pass")

    def test_none_when_empty_or_all_none(self):
        self.assertEqual(_ci.aggregate([]), "none")
        self.assertEqual(_ci.aggregate(["none", "none"]), "none")


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class CheckRuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # ADR-0059 unified layout: the ledger and the reconciliation surface both
        # live inside <tree>/invariants/, with checks under its checks/ subdir.
        (self.root / "bionic" / "invariants" / "checks").mkdir(parents=True)
        # The schema gate refuses an unmigrated or unrecognizable tree, so the
        # fixture must be a real v5 tree rather than a bare directory.
        (self.root / "bionic" / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled:\n  - invariants\n', encoding="utf-8"
        )

    def _pin(self, pid, ratification="observed", checks=None):
        checks = checks or []
        body = (
            f"---\nid: {pid}\nclass: shape\nprovenance: recovered\n"
            f"ratification: {ratification}\nverification: {{last_result: none}}\n"
            f"related_adrs: []\nrelated_briefs: []\nchecks: {checks}\n---\n\n# {pid}\n"
        )
        (self.root / "bionic" / "invariants" / f"{pid.lower()}.md").write_text(body)

    def _recon(self, entries):
        import yaml as y
        (self.root / "bionic" / "invariants" / "reconciliation.yml").write_text(
            y.dump({"config_version": "1", "checks": entries})
        )

    def test_empty_concern_is_clean(self):
        self._recon([])
        r = _ci.check(self.root)
        self.assertEqual(r["broken"], [])
        self.assertEqual(r["warning"], [])
        self.assertEqual(r["survey_debt"], 0)

    def test_bijection_duplicate_id(self):
        self._pin("INV-0001")
        # second page, same id
        (self.root / "bionic" / "invariants" / "dup.md").write_text(
            "---\nid: INV-0001\nratification: observed\n---\n"
        )
        self._recon([])
        r = _ci.check(self.root)
        self.assertTrue(any("BIJECTION" in b and "duplicate" in b for b in r["broken"]))

    def test_orphan_check(self):
        self._recon([{"check_id": "c1", "pin_id": "INV-0099", "last_result": "pass"}])
        r = _ci.check(self.root)
        self.assertTrue(any("ORPHAN-CHECK" in b for b in r["broken"]))

    def test_decoration_ratified_no_check(self):
        self._pin("INV-0001", ratification="ratified")
        self._recon([])
        r = _ci.check(self.root)
        self.assertTrue(any("DECORATION" in b for b in r["broken"]))

    def test_failing_ratified_fail_is_broken(self):
        self._pin("INV-0001", ratification="ratified")
        self._recon([{"check_id": "c1", "pin_id": "INV-0001", "last_result": "fail"}])
        r = _ci.check(self.root)
        self.assertTrue(any("FAILING" in b and "fail" in b for b in r["broken"]))

    def test_failing_ratified_stale_is_warning(self):
        self._pin("INV-0001", ratification="ratified")
        self._recon([{"check_id": "c1", "pin_id": "INV-0001", "last_result": "stale"}])
        r = _ci.check(self.root)
        self.assertTrue(any("FAILING" in w and "stale" in w for w in r["warning"]))
        self.assertEqual(r["broken"], [])

    def test_dangerous_observed_pass_is_warning(self):
        self._pin("INV-0001", ratification="observed")
        self._recon([{"check_id": "c1", "pin_id": "INV-0001", "last_result": "pass"}])
        r = _ci.check(self.root)
        self.assertTrue(any("DANGEROUS" in w for w in r["warning"]))

    def test_observed_no_check_is_clean(self):
        # the normal known-unknown state — must NOT be flagged
        self._pin("INV-0001", ratification="observed")
        self._recon([])
        r = _ci.check(self.root)
        self.assertEqual(r["broken"], [])
        self.assertEqual(r["warning"], [])
        self.assertEqual(r["survey_debt"], 1)


class ConcernWiringTests(unittest.TestCase):
    """The real tree is at schema 4 with the concern enabled + surfaces present."""

    def test_manifest_schema_4_and_concern_enabled(self):
        p = REPO_ROOT / TREE / "manifest.yml"
        require_dev_surface(self, p, "docs/manifest.yml")
        m = p.read_text()
        self.assertIn('schema_version: "5"', m)
        self.assertIn("- invariants", m)

    def test_ledger_and_suite_surfaces_exist(self):
        led = REPO_ROOT / TREE / "invariants" / "index.md"
        require_dev_surface(self, led, "docs/invariants/index.md")
        self.assertTrue(led.is_file())
        self.assertTrue((REPO_ROOT / "bionic" / "invariants" / "reconciliation.yml").is_file())

    def test_master_index_has_invariants_section(self):
        p = REPO_ROOT / TREE / "index.md"
        require_dev_surface(self, p, "docs/index.md")
        idx = p.read_text()
        self.assertIn("## Invariants", idx)

    def test_audit_docs_defines_the_five_chk_inv_rules(self):
        a = (REPO_ROOT / "crux" / "skills" / "audit-docs" / "SKILL.md").read_text()
        for rule in ("CHK-INV-BIJECTION", "CHK-INV-ORPHAN-CHECK", "CHK-INV-DECORATION",
                     "CHK-INV-FAILING", "CHK-INV-DANGEROUS"):
            self.assertIn(rule, a, f"{rule} missing from audit-docs")

    def test_audit_docs_supports_schema_4(self):
        a = (REPO_ROOT / "crux" / "skills" / "audit-docs" / "SKILL.md").read_text()
        self.assertIn('schema_version == "5"', a)

    def test_claude_md_defines_invariants_contract(self):
        p = REPO_ROOT / TREE / "AGENTS.md"
        require_dev_surface(self, p, f"{TREE}/AGENTS.md")
        c = p.read_text()
        self.assertIn("## 15. The invariants concern", c)
        self.assertIn("machine proposes, human disposes", c)


if __name__ == "__main__":
    unittest.main()
