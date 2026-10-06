"""The promptbook index is checked for completeness, and the check cannot pass on an empty read.

WHY THESE TESTS EXIST. The index carried 107 archived rows against 109 books on disk, and the rule
that forbids exactly that (CHK-PB-11) had existed the whole time. The audit has two execution paths
and the delegated one — the path any tree this size takes — enumerated seven other promptbook items
and none for CHK-PB-11. A rule whose only executor can drop it is a rule with no executor, so the
checker below is the executor that cannot be dropped.

EVERY FIXTURE IS BUILT HERE. These run under the staged `unittest` gate where `bionic/` does not
exist, so a test reading a live tree path would skip exactly where it most needs to run, and a skip
is no evidence about the thing that ships.

EVERY DEFECT TEST HAS A PAIRED CONTROL. Each one starts from a fixture the checker calls clean, then
introduces one defect, so a red result can never be an artifact of a fixture that was never clean.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
CHECKER = SCRIPTS / "check-promptbook-index.py"


def _load():
    spec = importlib.util.spec_from_file_location("_pb_index_probe", CHECKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_pb_index_probe", module)
    spec.loader.exec_module(module)
    return module


CK = _load()

INDEX = """# Promptbooks

_Last updated: 2026-09-17_

## Active ({n_active})

| id | title | status | current_run | progress | tags | created_at |
|---|---|---|---|---|---|---|
{active_rows}
## Recent runs (last 20)

## Archived ({n_archived})

{archived_rows}"""


class Tree:
    """A miniature documentation tree with promptbook directories and an index."""

    def __init__(self, root: Path):
        self.root = root
        self.books = root / "tree" / "promptbooks"
        for sub in ("active", "archive", "legacy"):
            (self.books / sub).mkdir(parents=True)
        (root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: tree\n', encoding="utf-8")
        self.active: list[str] = []
        self.archived: list[str] = []

    def add_active(self, stem: str) -> None:
        (self.books / "active" / f"{stem}.yaml").write_text("id: x\n", encoding="utf-8")
        self.active.append(stem)

    def add_archived(self, stem: str, suffix: str = ".yaml") -> None:
        (self.books / "archive" / f"{stem}{suffix}").write_text("id: x\n", encoding="utf-8")
        self.archived.append(stem)

    def add_legacy(self, stem: str) -> None:
        """A preserved pre-migration original. It is NOT a concern surface and must not be
        enumerated — doing so would demand a second row for every migrated book."""
        (self.books / "legacy" / f"{stem}.md").write_text("id: x\n", encoding="utf-8")

    def write_index(self, active=None, archived=None,
                    n_active=None, n_archived=None, rollup=True) -> None:
        active = self.active if active is None else active
        archived = self.archived if archived is None else archived
        arows = "".join(
            f"| [[promptbooks/{s}]] | t | active | RUN-001 | 0/1 | x | 2026-09-17 |\n"
            for s in active)
        rrows = "".join(
            f"- [[promptbooks/archive/{s}]] — completed 2026-09-17\n" for s in archived)
        (self.books / "index.md").write_text(
            INDEX.format(n_active=len(active) if n_active is None else n_active,
                         n_archived=len(archived) if n_archived is None else n_archived,
                         active_rows=arows, archived_rows=rrows),
            encoding="utf-8")
        if rollup:
            (self.root / "tree" / "index.md").write_text(
                f"# Tree\n\n## Promptbooks ({len(self.active)} active, "
                f"{len(self.archived)} archived)\n", encoding="utf-8")

    def run(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CHECKER), "--root", str(self.root)],
                              capture_output=True, text=True)

    def errors(self) -> list[dict]:
        r = self.run()
        return json.loads(r.stdout)["validation_errors"]


def _tree(case: unittest.TestCase, active=1, archived=2) -> Tree:
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    t = Tree(Path(tmp.name))
    for i in range(active):
        t.add_active(f"PB-90{i:02d}-active-book")
    for i in range(archived):
        t.add_archived(f"PB-80{i:02d}-archived-book")
    t.write_index()
    return t


class CleanTreeTests(unittest.TestCase):
    """The control every defect test below starts from."""

    def test_a_complete_index_passes(self):
        t = _tree(self)
        r = t.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["validation_errors"], [])

    def test_the_check_counted_something(self):
        """Non-vacuity: a checker that walked an empty tree would pass everything above."""
        t = _tree(self)
        checked = json.loads(t.run().stdout)["checked"]
        self.assertEqual(checked["active_on_disk"], 1)
        self.assertEqual(checked["archived_on_disk"], 2)

    def test_a_legacy_book_is_not_enumerated(self):
        """legacy/ holds byte-preserved duplicates; walking it would demand a second row
        for every migrated book. The paired control is the clean pass above."""
        t = _tree(self)
        # A stem that matches NO archived book. An earlier version reused an archived stem, so
        # the set difference absorbed it and the test would have passed even if legacy/ were
        # walked — it could not fail for the reason it names.
        t.add_legacy("PB-7000-legacy-only-never-archived")
        r = t.run()
        self.assertEqual(r.returncode, 0, r.stdout)
        errs = json.loads(r.stdout)["validation_errors"]
        self.assertEqual(errs, [])
        self.assertNotIn("PB-7000-legacy-only-never-archived",
                         [e.get("book") for e in errs],
                         "a legacy original was enumerated as a book owing a row")

    def test_a_markdown_book_is_enumerated_too(self):
        """The walk is extension-agnostic by contract: .yaml and .md books both count."""
        t = _tree(self)
        t.add_archived("PB-8099-legacy-format-book", suffix=".md")
        t.write_index()
        self.assertEqual(t.run().returncode, 0)


class DefectDetectionTests(unittest.TestCase):
    """The three defect classes the task names, each against a clean starting fixture."""

    def test_a_book_on_disk_with_no_index_row_is_detected(self):
        t = _tree(self)
        self.assertEqual(t.run().returncode, 0, "control: the fixture starts clean")
        t.add_archived("PB-8099-book-with-no-row")
        t.write_index(archived=[s for s in t.archived if "no-row" not in s],
                      n_archived=len(t.archived) - 1)
        errs = t.errors()
        missing = [e for e in errs if e["defect"] == "missing-row"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["book"], "PB-8099-book-with-no-row")
        self.assertEqual(missing[0]["lane"], "archived")
        self.assertEqual(t.run().returncode, 1)

    def test_a_duplicate_index_row_is_detected(self):
        t = _tree(self)
        self.assertEqual(t.run().returncode, 0, "control: the fixture starts clean")
        t.write_index(archived=t.archived + [t.archived[0]])
        dupes = [e for e in t.errors() if e["defect"] == "duplicate-row"]
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["book"], t.archived[0])
        self.assertEqual(t.run().returncode, 1)

    def test_a_row_naming_a_book_that_is_not_there_is_detected(self):
        t = _tree(self)
        self.assertEqual(t.run().returncode, 0, "control: the fixture starts clean")
        t.write_index(archived=t.archived + ["PB-8999-never-existed"])
        dangling = [e for e in t.errors() if e["defect"] == "dangling-row"]
        self.assertEqual(len(dangling), 1)
        self.assertEqual(dangling[0]["book"], "PB-8999-never-existed")
        self.assertIn("confirm", dangling[0]["remedy"],
                      "removing a row is the destructive repair and must say so")

    def test_the_active_lane_is_checked_too(self):
        t = _tree(self)
        t.add_active("PB-9099-active-with-no-row")
        t.write_index(active=[s for s in t.active if "no-row" not in s],
                      n_active=len(t.active) - 1)
        missing = [e for e in t.errors() if e["defect"] == "missing-row"]
        self.assertEqual([m["lane"] for m in missing], ["active"])

    def test_a_heading_count_that_disagrees_with_its_rows_is_detected(self):
        t = _tree(self)
        self.assertEqual(t.run().returncode, 0, "control: the fixture starts clean")
        t.write_index(n_archived=99)
        counts = [e for e in t.errors() if e["defect"] == "count-disagrees"]
        self.assertEqual(len(counts), 1)
        self.assertIn("99", counts[0]["problem"])

    def test_the_tree_rollup_is_a_third_surface_and_is_checked(self):
        """Measured on the live tree: disk 109, roster 107, rollup 108 — three surfaces,
        three numbers. A checker reading only two of them would have called one pair clean."""
        t = _tree(self)
        self.assertEqual(t.run().returncode, 0, "control: the fixture starts clean")
        (t.root / "tree" / "index.md").write_text(
            "# Tree\n\n## Promptbooks (0 active, 77 archived)\n", encoding="utf-8")
        rollup = [e for e in t.errors() if e["defect"] == "rollup-disagrees"]
        self.assertEqual({e["lane"] for e in rollup}, {"active", "archived"})

    def test_several_defects_are_all_reported_in_one_run(self):
        t = _tree(self)
        t.add_archived("PB-8099-book-with-no-row")
        t.write_index(archived=[t.archived[0], t.archived[0], "PB-8999-never-existed"])
        defects = {e["defect"] for e in t.errors()}
        self.assertIn("missing-row", defects)
        self.assertIn("duplicate-row", defects)
        self.assertIn("dangling-row", defects)


class ApplicabilityTests(unittest.TestCase):
    """An absent optional concern is not a defect, and an unreadable tree is not a pass."""

    def test_a_tree_with_no_promptbooks_concern_reports_surface_absent(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "tree").mkdir()
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: tree\n',
                                          encoding="utf-8")
        r = subprocess.run([sys.executable, str(CHECKER), "--root", str(root)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertTrue(payload["surface_absent"])
        self.assertIn("reason", payload)

    def test_an_unresolvable_tree_fails_loud_rather_than_reporting_clean(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: "../escape"\n',
                                          encoding="utf-8")
        r = subprocess.run([sys.executable, str(CHECKER), "--root", str(root)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, "a malformed config must not read as clean")
        self.assertEqual(r.stdout.strip(), "")

    def test_a_missing_index_file_is_not_reported_as_incomplete(self):
        """A promptbooks directory with no index yet owes rows to nothing. The checker
        reports no findings rather than one per book."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = Tree(Path(tmp.name))
        t.add_archived("PB-8000-book")
        self.assertEqual(CK.findings(t.root, t.root / "tree"), [])


if __name__ == "__main__":
    unittest.main()
