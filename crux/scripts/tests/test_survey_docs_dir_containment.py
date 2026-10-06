"""`--docs-dir` containment on the two batch-ratification entry points.

`scaffold-survey-sheet.py` and `signoff-survey.py` resolved their tree as

    tree = sp.resolve_tree(root) if docs_dir is None else root / docs_dir

The `resolve_tree` branch is guarded — textual refusal, then resolved
containment. The `else` branch joined an operator-supplied component onto a
validated root and validated nothing, so `--docs-dir ../victim-tree` scaffolded
a sheet and moved a counter in a tree OUTSIDE the repository root, and the
sign-off then read the same traversal back.

Every existing guard passed it, and the reason is the point of this file: every
write routes through `atomic_write_text(..., contained_under=ctx["tree"])`, and
`ctx["tree"]` IS the traversed path — the containment check was checking the
attack against itself. So the assertion here is never "the write path refused";
it is a **fingerprinted canary outside the root**, byte-compared, plus a
message discriminator, because the repo has already been bitten by "exit 2
either way".

Both legs of the shipped validator are exercised separately:

  * TEXTUAL — `../victim-tree` carries a `..` segment and dies before any
    filesystem access;
  * RESOLVED — `escape` is a spelling every textual rule admits, and only
    re-checking where it LANDS catches the symlink.

Each is paired with a positive control (`--docs-dir alt`, a real second tree
inside the repo) proving the flag still works and that the fixture would
otherwise have produced the write the canary denies.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SCAFFOLD = SCRIPTS / "scaffold-survey-sheet.py"
SIGNOFF = SCRIPTS / "signoff-survey.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import survey_sheet as SS  # noqa: E402
from crux.arch.recover import StateFile, candidate_id  # noqa: E402

DATE = "2026-08-30"

MANIFEST = """schema_version: 5
concerns_enabled: [adrs, observations, arch]

observation:
  next_number: 1
  next_survey_number: 1
  stale_days: 90
"""


def _fingerprint(root: Path) -> dict[str, str]:
    """{relative path: sha256} for every file under `root`. The canary: a
    traversal that wrote or clobbered anything moves one of these hashes."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


class _DocsDirCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outer = Path(self.tmp.name)
        self.root = self.outer / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        self.tree = self._make_tree(self.root / "bionic")

    # ── fixture ───────────────────────────────────────────────────────────

    def _make_tree(self, tree: Path) -> Path:
        """A crux tree with two mined candidates — enough that a scaffold
        pointed here writes a sheet and moves the counter."""
        (tree / "observations").mkdir(parents=True)
        (tree / "manifest.yml").write_text(MANIFEST, encoding="utf-8")
        (tree / "log.md").write_text("# Log\n", encoding="utf-8")
        state = StateFile(tree / "arch" / "_recovered" / "state.yml")
        for name, rule, evidence in (
                ("httpx", "The gateway depends on httpx", "src/a.py:1-1"),
                ("pyyaml", "The loader pins pyyaml exactly", "src/b.py:1-1")):
            cid = candidate_id("external-dependency", name)
            state.rows[cid] = {
                "id": cid, "anchor_kind": "external-dependency",
                "canonical_anchor": name, "rule": rule,
                "evidence": [evidence], "state": "observed",
                "domain": "runtime"}
        state.save()
        return tree

    def _victim(self) -> Path:
        """A second crux tree OUTSIDE the repository root — the thing the
        traversal reached. A sibling of `repo/`, so `../victim-tree` names it."""
        return self._make_tree(self.outer / "victim-tree")

    def _escape_symlink(self, victim: Path) -> str:
        """An in-repo name whose SPELLING is unimpeachable and whose resolved
        location is outside the root. Isolates the containment leg."""
        (self.root / "escape").symlink_to(victim, target_is_directory=True)
        return "escape"

    # ── invocations ───────────────────────────────────────────────────────

    def _scaffold(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCAFFOLD), "--repo-root", str(self.root),
             "--date", DATE, *extra], capture_output=True, text=True)

    def _signoff(self, *extra):
        return subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(self.root),
             "--date", DATE, *extra], capture_output=True, text=True)

    def _sign_sheet(self, tree: Path, batch_id: str) -> None:
        path = tree / "observations" / f"survey-{batch_id}.yml"
        sheet = SS.read_sheet(path)
        for row in sheet["rows"]:
            row["verdict"] = "ratify"
            row["domain"] = ""
            row["rationale"] = "Reviewed against the cited lines."
        SS.write_sheet(path, sheet, contained_under=tree)

    def _assert_refused(self, proc, docs_dir: str, phrase: str):
        self.assertNotEqual(proc.returncode, 0,
                            f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
        self.assertIn("docs_dir", proc.stderr)
        self.assertIn(docs_dir, proc.stderr)
        self.assertIn(phrase, proc.stderr)


class ScaffoldDocsDirContainmentTests(_DocsDirCase):

    def test_a_traversing_docs_dir_is_refused_and_the_victim_is_untouched(self):
        victim = self._victim()
        before = _fingerprint(victim)
        proc = self._scaffold("--docs-dir", "../victim-tree")
        self._assert_refused(proc, "../victim-tree", "'..' segments")
        self.assertEqual(_fingerprint(victim), before,
                         "the traversal wrote into a tree outside the root")
        self.assertEqual(list(victim.glob("observations/survey-*.yml")), [])

    def test_a_symlink_docs_dir_that_escapes_the_root_is_refused(self):
        victim = self._victim()
        before = _fingerprint(victim)
        name = self._escape_symlink(victim)
        proc = self._scaffold("--docs-dir", name)
        self._assert_refused(proc, name, "not contained under")
        self.assertEqual(_fingerprint(victim), before)
        self.assertEqual(list(victim.glob("observations/survey-*.yml")), [])

    def test_a_legitimate_in_repo_docs_dir_still_scaffolds(self):
        """The positive control. Without it the two refusals above are also
        true of a scaffold that never writes anything anywhere."""
        alt = self._make_tree(self.root / "alt")
        proc = self._scaffold("--docs-dir", "alt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["batch_id"], "SVY-0001")
        self.assertTrue((alt / "observations" / "survey-SVY-0001.yml").is_file())
        self.assertIn("next_survey_number: 2",
                      (alt / "manifest.yml").read_text(encoding="utf-8"))

    def test_the_refusal_lane_is_distinguishable_from_the_other_exit_2(self):
        """`exit 2 either way` insurance: an absent tree exits 2 as well and
        says something else entirely."""
        proc = self._scaffold("--docs-dir", "nowhere")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertNotIn("not contained under", proc.stderr)
        self.assertNotIn("'..' segments", proc.stderr)


class SignoffDocsDirContainmentTests(_DocsDirCase):

    def _plant_signed_sheet(self, victim: Path) -> None:
        """Scaffold into an in-repo staging tree, then move the sheet across.
        The sheet has to be produced by the real scaffold so the sign-off sees
        the shape it expects; it is planted rather than scaffolded in place
        because scaffolding into the victim is the very thing under test."""
        staging = self._make_tree(self.root / "staging")
        proc = self._scaffold("--docs-dir", "staging")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        batch_id = json.loads(proc.stdout)["batch_id"]
        self._sign_sheet(staging, batch_id)
        src = staging / "observations" / f"survey-{batch_id}.yml"
        (victim / "observations" / src.name).write_bytes(src.read_bytes())

    def test_a_traversing_docs_dir_is_refused_and_the_victim_is_untouched(self):
        victim = self._victim()
        self._plant_signed_sheet(victim)
        before = _fingerprint(victim)
        proc = self._signoff("--docs-dir", "../victim-tree")
        self._assert_refused(proc, "../victim-tree", "'..' segments")
        self.assertEqual(_fingerprint(victim), before,
                         "the sign-off published into a tree outside the root")
        self.assertEqual(list(victim.glob("observations/OBS-*.md")), [])

    def test_a_symlink_docs_dir_that_escapes_the_root_is_refused(self):
        victim = self._victim()
        self._plant_signed_sheet(victim)
        before = _fingerprint(victim)
        name = self._escape_symlink(victim)
        proc = self._signoff("--docs-dir", name)
        self._assert_refused(proc, name, "not contained under")
        self.assertEqual(_fingerprint(victim), before)
        self.assertEqual(list(victim.glob("observations/OBS-*.md")), [])

    def test_a_legitimate_in_repo_docs_dir_still_publishes(self):
        """The positive control: a signed sheet in a tree the flag may
        legitimately name does publish records.

        Scaffolded INTO `alt` rather than planted from `staging`, because a
        batch is now bound to the tree it was scaffolded into
        (`survey_sheet.tree_identity`) — the plant was a fixture shortcut, and
        moving a signed sheet between two trees is the very thing that binding
        refuses. The two refusal cases above keep the plant: their refusal
        fires in `validate_docs_dir_override`, before any sheet is read."""
        alt = self._make_tree(self.root / "alt")
        proc = self._scaffold("--docs-dir", "alt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._sign_sheet(alt, json.loads(proc.stdout)["batch_id"])
        proc = self._signoff("--docs-dir", "alt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(sorted(alt.glob("observations/OBS-*.md")),
                        "the control published nothing, so the canaries prove nothing")

    def test_the_refusal_lane_is_distinguishable_from_the_other_exit_2(self):
        proc = self._signoff("--docs-dir", "nowhere")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertNotIn("not contained under", proc.stderr)
        self.assertNotIn("'..' segments", proc.stderr)


if __name__ == "__main__":
    unittest.main()
