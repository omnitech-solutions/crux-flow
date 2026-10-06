"""Tests for record_numbers.py and check-record-numbers.py -- duplicate record numbers.

The defect this closes: two promptbooks or two ADRs sharing a number. Allocation is
monotonic, so a duplicate means an allocation bug or a hand-copied file, and every
later lookup by number then resolves to the wrong record.

Namespaces, decided here and asserted below:

  books   files in promptbooks/active and promptbooks/archive (any extension)
  runs    directories in promptbooks/runs
  adrs    files in adrs and adrs/archive

A book and its run directory carry the same number and are ONE record, so books and
runs never collide with each other. promptbooks/legacy holds byte-preserved copies of
migrated books and is excluded, as every CHK-PB-* walk excludes it.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dev_surface import REPO_ROOT, TREE, require_dev_surface  # noqa: E402

SCRIPTS = REPO_ROOT / "crux" / "scripts"
CHECKER = SCRIPTS / "check-record-numbers.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rn = _load("record_numbers", SCRIPTS / "record_numbers.py")


def _touch(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TreeFixture(unittest.TestCase):
    """A tmp repo whose tree is `bionic/`, seeded per test."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.docs = self.repo / "bionic"
        pb = self.docs / "promptbooks"
        _touch(pb / "active" / "PB-0001-alpha.yaml")
        _touch(pb / "archive" / "PB-0002-beta.yaml")
        _touch(pb / "archive" / "PB-0003-gamma.md")
        _touch(pb / "index.md")
        _touch(pb / "runs" / "PB-0002-beta" / "run-RUN-001.yaml")
        _touch(pb / "runs" / "PB-0003-gamma" / "run-RUN-001.md")
        _touch(self.docs / "adrs" / "ADR-0001-one.md")
        _touch(self.docs / "adrs" / "index.md")
        _touch(self.docs / "adrs" / "archive" / "ADR-0002-two.md")

    def run_checker(self, *args):
        return subprocess.run(
            [sys.executable, str(CHECKER), "--repo-root", str(self.repo), *args],
            capture_output=True, text=True)


class FindDuplicatesTests(TreeFixture):
    def test_a_clean_tree_has_no_duplicates(self):
        self.assertEqual(rn.find_duplicate_numbers(self.docs), [])

    def test_a_book_number_held_by_active_and_archive_is_reported(self):
        _touch(self.docs / "promptbooks" / "active" / "PB-0002-beta-copy.yaml")
        found = rn.find_duplicate_numbers(self.docs)
        self.assertEqual(len(found), 1, found)
        self.assertIn("PB-0002", found[0])
        self.assertIn("PB-0002-beta-copy.yaml", found[0])
        self.assertIn("PB-0002-beta.yaml", found[0])

    def test_two_extensions_of_one_number_are_two_books(self):
        _touch(self.docs / "promptbooks" / "archive" / "PB-0002-beta.md")
        self.assertEqual(len(rn.find_duplicate_numbers(self.docs)), 1)

    def test_an_adr_number_held_by_both_tiers_is_reported(self):
        _touch(self.docs / "adrs" / "archive" / "ADR-0001-other.md")
        found = rn.find_duplicate_numbers(self.docs)
        self.assertEqual(len(found), 1, found)
        self.assertIn("ADR-0001", found[0])

    def test_a_run_directory_number_held_twice_is_reported(self):
        _touch(self.docs / "promptbooks" / "runs" / "PB-0002-beta-fork" / "run-RUN-001.yaml")
        found = rn.find_duplicate_numbers(self.docs)
        self.assertEqual(len(found), 1, found)
        self.assertIn("PB-0002", found[0])
        self.assertIn("run director", found[0])

    def test_a_book_and_its_run_directory_are_one_record_not_a_duplicate(self):
        """PB-0002 is a book in archive/ and a directory in runs/ in the clean tree."""
        pb = self.docs / "promptbooks"
        self.assertTrue((pb / "archive" / "PB-0002-beta.yaml").is_file())
        self.assertTrue((pb / "runs" / "PB-0002-beta").is_dir())
        self.assertEqual(rn.find_duplicate_numbers(self.docs), [])

    def test_a_book_and_an_adr_sharing_a_number_are_not_a_duplicate(self):
        _touch(self.docs / "adrs" / "ADR-0003-three.md")  # PB-0003 also exists
        self.assertEqual(rn.find_duplicate_numbers(self.docs), [])

    def test_index_files_are_not_records(self):
        names = [r.name for r in rn.scan_records(self.docs)]
        self.assertNotIn("index.md", names)
        self.assertIn("PB-0001-alpha.yaml", names, "positive control: books are scanned")

    def test_a_prefixed_spelling_of_a_held_number_is_a_duplicate(self):
        _touch(self.docs / "adrs" / "CRX-ADR-0001-prefixed.md")
        self.assertEqual(len(rn.find_duplicate_numbers(self.docs)), 1)

    def test_non_ascii_numerals_are_not_a_record_number(self):
        # `\d` matches Arabic-Indic numerals, so ADR-٠٠٠١ used to collide with ADR-0001.
        _touch(self.docs / "adrs" / "ADR-٠٠٠١-lookalike.md")
        self.assertEqual(rn.number_of("ADR-٠٠٠١-lookalike.md"), None)
        self.assertEqual(rn.find_duplicate_numbers(self.docs), [])
        self.assertEqual(rn.count_records(self.docs)["adrs"], 2)
        # positive control: the same name in ASCII numerals is a duplicate
        _touch(self.docs / "adrs" / "ADR-0001-lookalike.md")
        self.assertEqual(len(rn.find_duplicate_numbers(self.docs)), 1)

    def test_the_scan_counts_each_namespace(self):
        counts = rn.count_records(self.docs)
        self.assertEqual(counts, {"books": 3, "run_directories": 2, "adrs": 2})


class LegacyExclusionTests(TreeFixture):
    """Positive control: the same copy is invisible under legacy/ and a duplicate
    under archive/, so the exclusion is real and not an artefact of the fixture."""

    def test_a_copy_under_legacy_is_not_a_duplicate(self):
        pb = self.docs / "promptbooks"
        _touch(pb / "legacy" / "PB-0002-beta.md")
        _touch(pb / "legacy" / "runs" / "PB-0002-beta" / "run-RUN-001.md")
        self.assertEqual(rn.find_duplicate_numbers(self.docs), [])

    def test_the_same_copy_under_archive_is_a_duplicate(self):
        pb = self.docs / "promptbooks"
        _touch(pb / "archive" / "PB-0002-beta.md")
        self.assertEqual(len(rn.find_duplicate_numbers(self.docs)), 1)


class CheckerEntryPointTests(TreeFixture):
    def test_a_clean_tree_exits_zero_and_reports_its_coverage(self):
        r = self.run_checker()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["validation_errors"], [])
        self.assertGreaterEqual(payload["checked"]["books"], 1,
                                "a green with nothing scanned is not evidence")
        self.assertEqual(payload["checked"]["adrs"], 2)

    def test_a_seeded_book_duplicate_exits_one_naming_it(self):
        _touch(self.docs / "promptbooks" / "active" / "PB-0003-gamma-copy.yaml")
        r = self.run_checker()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        errors = json.loads(r.stdout)["validation_errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("PB-0003", errors[0])

    def test_a_seeded_adr_duplicate_exits_one(self):
        _touch(self.docs / "adrs" / "archive" / "ADR-0001-copy.md")
        r = self.run_checker()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ADR-0001", json.loads(r.stdout)["validation_errors"][0])

    def test_a_seeded_run_directory_duplicate_exits_one(self):
        _touch(self.docs / "promptbooks" / "runs" / "PB-0003-other" / "run-RUN-001.yaml")
        r = self.run_checker()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_legacy_copy_stays_clean_while_the_archive_copy_fails(self):
        pb = self.docs / "promptbooks"
        _touch(pb / "legacy" / "PB-0002-beta.md")
        self.assertEqual(self.run_checker().returncode, 0)
        _touch(pb / "archive" / "PB-0002-beta.md")
        self.assertEqual(self.run_checker().returncode, 1)

    def test_docs_dir_flag_names_a_tree_other_than_the_default(self):
        moved = self.repo / "notes"
        shutil.copytree(self.docs, moved)
        shutil.rmtree(self.docs)
        _touch(moved / "adrs" / "archive" / "ADR-0001-copy.md")
        r = self.run_checker("--docs-dir", "notes")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_a_tree_with_no_promptbooks_or_adrs_is_surface_absent(self):
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        (empty / "bionic").mkdir()
        r = subprocess.run([sys.executable, str(CHECKER), "--repo-root", str(empty)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["surface_absent"])

    def test_an_unreadable_adrs_directory_is_a_capability_error(self):
        import os
        if os.geteuid() == 0:
            self.skipTest("root reads a mode-000 directory, so the fault cannot be seeded")
        adrs = self.docs / "adrs"
        adrs.chmod(0)
        self.addCleanup(adrs.chmod, 0o755)
        r = self.run_checker()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("check-record-numbers: cannot read the tree", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_missing_repo_root_is_a_capability_error(self):
        r = subprocess.run(
            [sys.executable, str(CHECKER), "--repo-root", str(self.repo / "nope")],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip(), "")
        self.assertTrue(r.stderr.strip())

    def test_the_checker_declares_no_third_party_dependencies(self):
        head = CHECKER.read_text(encoding="utf-8")
        self.assertIn("# /// script", head)
        self.assertIn("dependencies = []", head)


AUDIT_SKILL = REPO_ROOT / "crux" / "skills" / "audit-docs" / "SKILL.md"


def _audit_gaps(text: str) -> list[str]:
    """What the audit skill text lacks for the duplicate-number delegation."""
    gaps = []
    rule = next((ln for ln in text.splitlines() if ln.startswith("- **CHK-NUM-1**")), "")
    if "check-record-numbers.py" not in rule:
        gaps.append("CHK-NUM-1 does not name check-record-numbers.py")
    if "BROKEN" not in rule:
        gaps.append("CHK-NUM-1 does not state BROKEN")
    item = next((ln for ln in text.splitlines() if ln.startswith("> 40e.")), "")
    if "check-record-numbers.py" not in item:
        gaps.append("procedure item 40e does not name check-record-numbers.py")
    adr4 = next((ln for ln in text.splitlines() if ln.startswith("- **CHK-ADR-4**")), "")
    if "CHK-NUM-1" not in adr4:
        gaps.append("CHK-ADR-4 does not point at CHK-NUM-1")
    item22 = next((ln for ln in text.splitlines() if ln.startswith("> 22.")), "")
    if "CHK-NUM-1" not in item22:
        gaps.append("procedure item 22 does not point at CHK-NUM-1")
    return gaps


class AuditSkillDelegationTests(unittest.TestCase):
    """The audit skill names the checker, so an audit on the delegated path runs it."""

    def test_the_skill_delegates_duplicate_numbers_to_the_checker(self):
        self.assertEqual(_audit_gaps(AUDIT_SKILL.read_text(encoding="utf-8")), [])

    def test_positive_control_a_skill_without_the_delegation_is_flagged(self):
        text = AUDIT_SKILL.read_text(encoding="utf-8")
        mutated = text.replace("check-record-numbers.py", "check-nothing.py")
        self.assertNotEqual(mutated, text, "the mutation must change the text")
        self.assertEqual(len(_audit_gaps(mutated)), 2, _audit_gaps(mutated))
        self.assertEqual(len(_audit_gaps(text.replace("CHK-NUM-1", "CHK-XXX-9"))), 4)


class LiveCheckoutTests(unittest.TestCase):
    """Pre-check E: this checkout holds no duplicate number. Dev checkout only."""

    def test_the_live_tree_holds_no_duplicate_number(self):
        docs = REPO_ROOT / TREE
        require_dev_surface(self, docs / "promptbooks", f"{TREE}/promptbooks")
        counts = rn.count_records(docs)
        self.assertGreaterEqual(counts["books"], 100, counts)
        self.assertGreaterEqual(counts["adrs"], 100, counts)
        self.assertEqual(rn.find_duplicate_numbers(docs), [])

    def test_the_live_pair_pb_0119_and_pb_0120_is_distinct(self):
        docs = REPO_ROOT / TREE
        require_dev_surface(self, docs / "promptbooks", f"{TREE}/promptbooks")
        books = [r for r in rn.scan_records(docs)
                 if r.kind == "PB" and r.namespace == "books" and r.number in (119, 120)]
        self.assertEqual(sorted(r.number for r in books), [119, 120])


if __name__ == "__main__":
    unittest.main()
