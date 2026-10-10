"""Contract (a) — pointer-until-archive: the ARCHIVE PRECONDITION, not the advance
writer.

`archive-promptbook` is a prose skill (crux/skills/archive-promptbook/SKILL.md), so
each test here builds a book + run fixture in a TemporaryDirectory, drives it with the
REAL `crux/scripts/advance-run.py` CLI (subprocess, matching `test_advance_run.py`'s
`_cli` helper), then evaluates a small reference implementation of the skill's
documented preconditions 2 (pointer resolution) and 3 (eligibility) — see SKILL.md
around lines 60-73.

The pointer-resolution helper below reads the book's `current_run` field ONLY. It
contains NO directory scan, no glob, no highest-RUN-NNN fallback — a second, terminal
run snapshot the pointer does not name is planted in every fixture's run directory
specifically to prove that property is real (not just asserted).

All fixtures are self-contained under a TemporaryDirectory; no test reads `bionic/`,
so `require_dev_surface` is not needed here (crux/scripts/tests/_dev_surface.py).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ADVANCE = REPO_ROOT / "crux" / "scripts" / "advance-run.py"

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

HAVE = HAVE_YAML and ADVANCE.is_file()


def _cli(args):
    """Invoke advance-run.py as a subprocess (the real CLI surface)."""
    return subprocess.run([sys.executable, str(ADVANCE), *args],
                          capture_output=True, text=True)


def _bare_book_hash() -> str:
    """The content hash of the bare fixture book. `--outcome` verifies the run's
    `book_content_hash` against the book before it writes."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_vp_for_archive_precondition", ADVANCE.parent / "validate-promptbook.py")
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp.compute_book_hash(vp.load_yaml("id: PB-9001\n"))


def _run_doc(run_id="RUN-001", n_prompts=2):
    return {
        "format_version": "1",
        "run_id": run_id,
        "book_id": "PB-9001",
        "book_content_hash": _bare_book_hash(),
        "started_at": "2026-07-22T00:00:00Z",
        "completed_at": None,
        "status": "in_progress",
        "current_prompt": 1,
        "prompts": [
            {"n": i, "title": f"P{i}", "state": ("running" if i == 1 else "pending"),
             "started": None, "completed": None, "result": "", "artifacts": []}
            for i in range(1, n_prompts + 1)
        ],
        "notes": "", "pr_draft": "", "summary": "",
    }


def _terminal_decoy_doc(run_id="RUN-999"):
    """A second, TERMINAL run snapshot the book's pointer never names. Planted
    alongside the real run in every fixture's run directory so the resolution
    helper's "pointer only, never a directory scan" property is verified rather
    than merely asserted: if the helper ever fell back to a highest-RUN-NNN scan,
    this decoy — lexically higher and already archive-eligible — would flip these
    tests green for the wrong reason."""
    doc = _run_doc(run_id=run_id, n_prompts=1)
    doc["status"] = "completed"
    doc["current_prompt"] = None
    doc["prompts"][0]["state"] = "done"
    doc["prompts"][0]["completed"] = "2026-07-22T00:00:00Z"
    return doc


def resolve_and_check_archive_eligibility(book_path: Path, runs_dir: Path):
    """Reference implementation of archive-promptbook SKILL.md preconditions 2 and 3.

    Precondition 2 (pointer resolution): resolve the run to check EXCLUSIVELY from
    the book's `current_run` field. No directory scan, no glob, no highest-RUN-NNN
    fallback — a non-null `current_run` means "this book's current run", in-progress
    or terminal, never "a run is in progress" (contract (a), P2).

    Precondition 3 (eligibility): Path 1 (DELIVERED) is `status: completed` with
    every prompt terminal; Path 2 (ABANDONED) is `status: abandoned` with
    `abandonment.kind: deliberate`. Everything else refuses.

    Returns a dict: {"precondition_2": "pass"|"fail", "reason": str|None,
    "run_id": str|None, "run": dict|None, "eligible": bool, "path": "1"|"2"|None}.
    """
    book = yaml.safe_load(book_path.read_text())
    current_run = book.get("current_run")

    if current_run is None:
        return {"precondition_2": "fail", "reason": "book.current_run is null",
                "run_id": None, "run": None, "eligible": False, "path": None}

    # Pointer resolution ONLY — construct the exact snapshot path the pointer names.
    # No iteration over runs_dir's contents.
    run_path = runs_dir / f"run-{current_run}.yaml"
    if not run_path.is_file():
        return {"precondition_2": "fail",
                "reason": f"no run snapshot at the pointed-to path: {run_path}",
                "run_id": current_run, "run": None, "eligible": False, "path": None}

    run = yaml.safe_load(run_path.read_text())

    result = {"precondition_2": "pass", "reason": None, "run_id": current_run,
              "run": run, "eligible": False, "path": None}

    status = run.get("status")
    if status == "completed":
        terminal = ("done", "skipped", "blocked")
        if all(p["state"] in terminal for p in run["prompts"]):
            result["eligible"] = True
            result["path"] = "1"
        else:
            result["reason"] = "status completed but a prompt is non-terminal"
        return result

    if status == "abandoned":
        abandonment = run.get("abandonment") or {}
        if abandonment.get("kind") == "deliberate":
            result["eligible"] = True
            result["path"] = "2"
        else:
            result["reason"] = f"abandonment.kind is {abandonment.get('kind')!r}, not 'deliberate'"
        return result

    result["reason"] = f"status is {status!r} (in progress); not archive-eligible"
    return result


@unittest.skipUnless(HAVE, "advance-run.py or PyYAML unavailable")
class ArchivePreconditionTests(unittest.TestCase):
    def _fixture(self, tmp: Path):
        runs_dir = tmp / "runs"
        runs_dir.mkdir()
        run_path = runs_dir / "run-RUN-001.yaml"
        run_path.write_text(yaml.safe_dump(_run_doc(), sort_keys=False))
        # Plant the decoy so a directory-scan fallback would be provable.
        decoy_path = runs_dir / "run-RUN-999.yaml"
        decoy_path.write_text(yaml.safe_dump(_terminal_decoy_doc(), sort_keys=False))
        book_path = tmp / "PB-9001-x.yaml"
        book_path.write_text("id: PB-9001\ncurrent_run: RUN-001\ncurrent_prompt: 1\n")
        return book_path, run_path, runs_dir

    def test_patch_final_advance_refuses_before_writing_if_containment_unproven(self):
        with tempfile.TemporaryDirectory() as td:
            book_path, run_path, _ = self._fixture(Path(td))
            run_path.write_text(yaml.safe_dump(_run_doc(n_prompts=1), sort_keys=False))
            book_path.write_text("id: PB-9001\ncycle_kind: patch\ncurrent_run: RUN-001\ncurrent_prompt: 1\n")
            before_run, before_book = run_path.read_bytes(), book_path.read_bytes()
            proc = _cli([str(run_path), "--outcome", "done", "--book", str(book_path)])
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("patch completion preflight", proc.stdout)
            self.assertEqual(run_path.read_bytes(), before_run)
            self.assertEqual(book_path.read_bytes(), before_book)

    def test_patch_final_advance_cannot_omit_book_to_bypass_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "bionic" / "promptbooks"
            book_dir = root / "active"
            run_dir = root / "runs" / "PB-9001-x"
            book_dir.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            book = book_dir / "PB-9001-x.yaml"
            run_path = run_dir / "run-RUN-001.yaml"
            book.write_text("id: PB-9001\ncycle_kind: patch\ncurrent_run: RUN-001\ncurrent_prompt: 1\n")
            run_path.write_text(yaml.safe_dump(_run_doc(n_prompts=1), sort_keys=False))
            before = run_path.read_bytes()
            proc = _cli([str(run_path), "--outcome", "done"])
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("requires --book", proc.stdout)
            self.assertEqual(run_path.read_bytes(), before)

    def test_completed_run_archives_via_the_books_current_run_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            book_path, run_path, runs_dir = self._fixture(tmp)

            # 1-prompt run driven to completion.
            run_path.write_text(yaml.safe_dump(_run_doc(n_prompts=1), sort_keys=False))
            proc = _cli([str(run_path), "--outcome", "done", "--book", str(book_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            # The book's current_run STILL names RUN-001 (contract (a), P1).
            book = yaml.safe_load(book_path.read_text())
            self.assertEqual(book["current_run"], "RUN-001")

            result = resolve_and_check_archive_eligibility(book_path, runs_dir)
            self.assertEqual(result["precondition_2"], "pass")
            self.assertEqual(result["run_id"], "RUN-001")
            # Resolved the REAL run, not the decoy.
            self.assertEqual(result["run"]["run_id"], "RUN-001")
            self.assertEqual(result["path"], "1")
            self.assertTrue(result["eligible"], "Path 1 (DELIVERED) must hold")

    def test_in_progress_run_refuses_the_archive(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            book_path, run_path, runs_dir = self._fixture(tmp)

            # 2-prompt run advanced ONCE — still in_progress.
            proc = _cli([str(run_path), "--outcome", "done", "--book", str(book_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            result = resolve_and_check_archive_eligibility(book_path, runs_dir)

            # Mandatory false-green discipline: precondition 2 must RESOLVE first —
            # this is the positive control shared with the POSITIVE test above,
            # proving the refusal below comes from precondition 3, not from the
            # fixture never reaching the check.
            self.assertEqual(result["precondition_2"], "pass",
                              "the pointer must resolve before the eligibility "
                              "refusal is meaningful")
            self.assertEqual(result["run"]["status"], "in_progress")

            self.assertFalse(result["eligible"])
            self.assertIsNone(result["path"])
            self.assertIn("in progress", result["reason"])

    def test_deliberately_abandoned_run_archives_via_the_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            book_path, run_path, runs_dir = self._fixture(tmp)

            # 2-prompt run, advanced once, then deliberately abandoned.
            proc = _cli([str(run_path), "--outcome", "done", "--book", str(book_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            proc = _cli([str(run_path), "--abandon", "--reason", "changed approach",
                        "--book", str(book_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            book = yaml.safe_load(book_path.read_text())
            self.assertEqual(book["current_run"], "RUN-001")

            result = resolve_and_check_archive_eligibility(book_path, runs_dir)
            self.assertEqual(result["precondition_2"], "pass")
            self.assertEqual(result["run"]["abandonment"]["kind"], "deliberate")
            self.assertEqual(result["path"], "2")
            self.assertTrue(result["eligible"], "Path 2 (ABANDONED) must hold")

    def test_superseded_abandonment_confers_no_eligibility(self):
        # Guard case: abandonment.kind: superseded is written by a later run's
        # start over a stale run. It must NOT be green for the wrong reason.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            book_path, run_path, runs_dir = self._fixture(tmp)

            doc = _run_doc()
            doc["status"] = "abandoned"
            doc["current_prompt"] = None
            doc["abandonment"] = {"kind": "superseded", "at": "2026-07-22T00:00:00Z",
                                   "reason": "superseded by a later run start"}
            run_path.write_text(yaml.safe_dump(doc, sort_keys=False))

            result = resolve_and_check_archive_eligibility(book_path, runs_dir)
            self.assertEqual(result["precondition_2"], "pass")
            self.assertFalse(result["eligible"])
            self.assertIsNone(result["path"])
            self.assertIn("superseded", result["reason"])

    def test_pointer_resolution_never_reaches_the_decoy_run(self):
        # The property claim made explicit: a run directory containing a SECOND,
        # already-eligible terminal run that the pointer does not name must never
        # be the one resolved. Point the book at a run_id that has NO snapshot on
        # disk at all — if resolution ever fell back to scanning the directory, it
        # would find and return the eligible decoy; the pointer-only implementation
        # must instead fail precondition 2.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            book_path, run_path, runs_dir = self._fixture(tmp)
            book_path.write_text("id: PB-9001\ncurrent_run: RUN-002\ncurrent_prompt: 1\n")

            result = resolve_and_check_archive_eligibility(book_path, runs_dir)
            self.assertEqual(result["precondition_2"], "fail")
            self.assertFalse(result["eligible"])
            self.assertIsNone(result["run"])


if __name__ == "__main__":
    unittest.main()
