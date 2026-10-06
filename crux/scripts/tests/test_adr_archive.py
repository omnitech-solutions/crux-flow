"""Tests for the ADR archival cold tier (ADR-0063, SP-4)."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


GAI = _load_module("gen_adr_index", "generate-adr-index.py")


def _adr(d: Path, num: int, status: str, **fm):
    body = ["---", f"id: ADR-{num:04d}", f'title: "T{num}"', f"status: {status}",
            "date: 2026-01-01"]
    for k, v in fm.items():
        body.append(f"{k}: {v}")
    body += ["---", "", "# body", ""]
    (d / f"ADR-{num:04d}-x.md").write_text("\n".join(body))


class AdrIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n")
        self.adrs = self.root / "bionic" / "adrs"
        self.archive = self.adrs / "archive"
        self.archive.mkdir(parents=True)
        _adr(self.adrs, 10, "Accepted")
        _adr(self.adrs, 9, "Proposed")
        _adr(self.archive, 2, "Superseded", superseded_by="ADR-0010")
        _adr(self.archive, 1, "Deprecated")

    def test_active_excludes_archived(self):
        path, text = GAI.build(self.root)
        # active table has the two active ADRs...
        head = text.split("## Archived")[0]
        self.assertIn("| ADR-0010 |", head)
        self.assertIn("| ADR-0009 |", head)
        self.assertNotIn("| ADR-0002 |", head)   # archived, not in active
        self.assertNotIn("| ADR-0001 |", head)

    def test_archived_roster(self):
        _, text = GAI.build(self.root)
        self.assertIn("## Archived (2)", text)   # header with count
        roster = text.split("## Archived")[1]
        self.assertIn("| ADR-0002 | T2 | Superseded |", roster)
        self.assertIn("| ADR-0001 | T1 | Deprecated |", roster)
        # ordered by id descending: 0002 before 0001
        self.assertLess(roster.index("ADR-0002"), roster.index("ADR-0001"))

    def test_byte_stable(self):
        _, a = GAI.build(self.root)
        _, b = GAI.build(self.root)
        self.assertEqual(a, b)
        self.assertTrue(a.endswith("\n"))
        self.assertNotIn("Last updated", a)   # no date → byte-stable

    def test_dry_run_detects_drift(self):
        path, want = GAI.build(self.root)
        path.write_text(want)
        self.assertEqual(GAI.main(["--repo-root", str(self.root), "--dry-run"]), 0)
        path.write_text(want + "\ntamper\n")
        self.assertEqual(GAI.main(["--repo-root", str(self.root), "--dry-run"]), 1)


class TwoTierRollupTests(unittest.TestCase):
    # AC-5 — rollup + lineage walk both tiers (an archived ADR stays in the graph).
    def test_rollup_load_walks_both_tiers(self):
        rollup = _load_module("gen_rollup", "generate-index-rollup.py")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adrs = Path(tmp.name) / "adrs"
        (adrs / "archive").mkdir(parents=True)
        _adr(adrs, 5, "Accepted")
        _adr(adrs / "archive", 3, "Superseded")
        recs = rollup.load_adrs(adrs)
        nums = {r["num"] for r in recs}
        self.assertEqual(nums, {5, 3})   # both tiers present in the master rollup


if __name__ == "__main__":
    unittest.main(verbosity=2)
