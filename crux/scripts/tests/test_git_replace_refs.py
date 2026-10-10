"""A git replace ref never makes the cycle's git reads see uncommitted content.

`git replace <object> <replacement>` writes a ref under `refs/replace/`, and every later git
read of `<object>` returns `<replacement>` instead. Without a guard, a replace ref can make
the clean-path check read a dirty working-tree blob as the committed one, or make HEAD read
as a commit that holds a record never committed on the branch. The shared git helper in
`council_records` sets GIT_NO_REPLACE_OBJECTS and drops GIT_REPLACE_REF_BASE. The
base-commit pin and the blast-radius gate's own git helper do the same.

Every case carries two controls: a fixture-live control (raw git with replace refs honoured
does read the replacement) and a clean control (the same command without the replace ref
behaves the same way, or a committed version passes).

Reads only `crux/` and fixtures built in a temp directory. Never reads the documentation tree.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _council_gate_support as sup  # noqa: E402
import council_records as cr  # noqa: E402

SCRIPTS = sup.SCRIPTS
WRITER = SCRIPTS / "write-review-report.py"
ADVANCE = SCRIPTS / "advance-run.py"
REVIEW_GATE_PROMPT = 10
DIRTY = "# subject v2, never committed\n"
COMMITTER = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def env() -> dict[str, str]:
    """The fixture environment with replace refs honoured: GIT_NO_REPLACE_OBJECTS removed."""
    e = sup.scrubbed_env()
    e.pop("GIT_NO_REPLACE_OBJECTS", None)
    e.pop("GIT_REPLACE_REF_BASE", None)
    return e


def raw(root: Path, *args: str, stdin: str | None = None, extra: dict | None = None) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, text=True,
                       env=env() | (extra or {}))
    if r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r.stdout.strip()


class _Base(unittest.TestCase):
    KIND = "adr"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = sup.Env(Path(self._tmp.name).resolve() / "repo", kind=self.KIND)

    def out(self, proc: subprocess.CompletedProcess) -> dict:
        self.assertNotIn("Traceback", proc.stderr, proc.stderr)
        self.assertTrue(proc.stdout.strip(), "empty stdout reads as a crash: " + proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])


class _DirtySubjectBase(_Base):
    """The subject carries an unstaged change, and a replace ref maps its committed blob to the
    dirty blob, so a git read that honours replace refs calls the subject clean."""

    def setUp(self):
        super().setUp()
        self.env.set_run(REVIEW_GATE_PROMPT)
        sup.commit_all(self.env.root, "at the review gate")

    def replace_subject_blob(self) -> None:
        self.env.subject.write_text(DIRTY)
        old = raw(self.env.root, "rev-parse", f"HEAD:{sup.SUBJECT}")
        new = raw(self.env.root, "hash-object", "-w", "--stdin", stdin=DIRTY)
        raw(self.env.root, "replace", old, new)

    def assert_fixture_live(self) -> None:
        """With replace refs honoured, the committed and the staged blob read as the dirty text,
        which is what the clean-path check hashes; without, they read as the committed text."""
        for spec in (f"HEAD:{sup.SUBJECT}", f":0:{sup.SUBJECT}"):
            self.assertEqual(raw(self.env.root, "cat-file", "blob", spec) + "\n", DIRTY,
                             "the replace ref does not mask the change")
            self.assertNotEqual(raw(self.env.root, "--no-replace-objects", "cat-file", "blob", spec) + "\n",
                                DIRTY, "the fixture committed the dirty text")


class ReviewerReportWriterTests(_DirtySubjectBase):
    def run_writer(self) -> tuple[int, dict]:
        r = subprocess.run([sys.executable, str(WRITER), str(self.env.run_path), "--prompt",
                            str(REVIEW_GATE_PROMPT), "--path", sup.SUBJECT, "--verdict", "APPROVE"],
                           capture_output=True, text=True, cwd=str(self.env.root), env=env())
        return r.returncode, self.out(r)

    def reports(self) -> list[Path]:
        d = self.env.run_dir / "reviews"
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def test_the_writer_refuses_a_dirty_subject_under_a_replace_ref(self):
        self.replace_subject_blob()
        self.assert_fixture_live()
        code, out = self.run_writer()
        self.assertEqual(code, 1, out)
        self.assertIn("unstaged change", " ".join(out["problems"]))
        self.assertEqual(self.reports(), [])

    def test_clean_control_a_clean_subject_writes_a_report(self):
        code, out = self.run_writer()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.reports()), 1)


class ReviewGateTests(_DirtySubjectBase):
    def advance(self, report: Path) -> tuple[int, dict]:
        r = subprocess.run([sys.executable, str(ADVANCE), str(self.env.run_path), "--outcome", "done",
                            "--result", "reviewed", "--artifacts", self.env.rel(report)],
                           capture_output=True, text=True, cwd=str(self.env.root), env=env())
        return r.returncode, self.out(r)

    def report(self) -> Path:
        doc = self.env.report_doc(prompt=REVIEW_GATE_PROMPT)  # declares the subject's current bytes
        return self.env.write("report.json", doc, folder=self.env.run_dir / "reviews")

    def test_the_review_gate_refuses_a_dirty_subject_under_a_replace_ref(self):
        self.replace_subject_blob()
        report = self.report()
        self.assert_fixture_live()
        code, out = self.advance(report)
        self.assertEqual(code, 1, out)
        self.assertEqual(out["gate"]["verdict"], "refuse")
        self.assertEqual(self.env.load_run()["current_prompt"], REVIEW_GATE_PROMPT)

    def test_clean_control_a_clean_subject_passes_the_review_gate(self):
        code, out = self.advance(self.report())
        self.assertEqual(code, 0, out)
        self.assertEqual(out["gate"]["verdict"], "pass")


class CouncilGateTests(_Base):
    """A council record staged but never committed, with HEAD replaced by a commit that holds it."""

    def setUp(self):
        super().setUp()
        self.env.set_run(3)
        sup.commit_all(self.env.root, "at the council gate")
        self.record = self.env.write("r1.json", self.env.council_doc(prompt=3), commit=False)

    def advance(self) -> tuple[int, dict]:
        r = subprocess.run([sys.executable, str(ADVANCE), str(self.env.run_path), "--outcome", "done",
                            "--result", "council converged", "--artifacts", self.env.rel(self.record)],
                           capture_output=True, text=True, cwd=str(self.env.root), env=env())
        return r.returncode, self.out(r)

    def replace_head(self) -> str:
        head = raw(self.env.root, "rev-parse", "HEAD")
        raw(self.env.root, "add", "--", self.env.rel(self.record))
        tree = raw(self.env.root, "write-tree")
        fake = raw(self.env.root, "commit-tree", tree, "-p", head, "-m", "fake", extra=COMMITTER)
        raw(self.env.root, "replace", head, fake)
        return head

    def test_the_council_gate_refuses_a_staged_only_record_under_a_replace_ref(self):
        head = self.replace_head()
        rel = self.env.rel(self.record)
        # Fixture-live control: git with replace refs honoured finds the record in HEAD; the
        # branch tip and its real tree do not hold it.
        raw(self.env.root, "cat-file", "-e", f"HEAD:{rel}")
        self.assertEqual(raw(self.env.root, "--no-replace-objects", "rev-parse", "HEAD"), head)
        r = subprocess.run(["git", "-C", str(self.env.root), "--no-replace-objects", "cat-file", "-e",
                            f"HEAD:{rel}"], capture_output=True, env=env())
        self.assertNotEqual(r.returncode, 0, "the record is in the real HEAD tree")
        code, out = self.advance()
        self.assertEqual(code, 1, out)
        self.assertEqual(out["gate"]["verdict"], "refuse")
        self.assertEqual(self.env.load_run()["current_prompt"], 3)

    def test_clean_control_the_committed_record_passes_the_council_gate(self):
        self.env.commit_records(self.record)
        code, out = self.advance()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["gate"]["verdict"], "pass")


class SiblingGitHelperTests(_Base):
    """The base-commit pin and the blast-radius gate's git helper read the real HEAD."""

    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location("base_commit_pin_replace", SCRIPTS / "base_commit_pin.py")
        self.pin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pin)
        spec = importlib.util.spec_from_file_location("blast_radius_replace", SCRIPTS / "check-blast-radius.py")
        self.blast = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.blast)
        run = self.env.load_run()
        self.base = run["base_commit"] = sup.git(self.env.root, "rev-parse", "HEAD").strip()
        self.env.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.env.root, "pin the base")
        self.head = raw(self.env.root, "rev-parse", "HEAD")
        self.real_tree = raw(self.env.root, "rev-parse", "HEAD^{tree}")
        # A fake HEAD whose snapshot pins a different base.
        run["base_commit"] = "0" * 40
        self.env.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        raw(self.env.root, "add", "--", self.env.rel(self.env.run_path))
        tree = raw(self.env.root, "write-tree")
        fake = raw(self.env.root, "commit-tree", tree, "-p", self.head, "-m", "fake", extra=COMMITTER)
        raw(self.env.root, "reset", "-q", "--", self.env.rel(self.env.run_path))
        raw(self.env.root, "replace", self.head, fake)
        self.fake_tree = tree

    def test_fixture_live_control_replace_refs_change_heads_tree(self):
        self.assertEqual(raw(self.env.root, "rev-parse", "HEAD^{tree}"), self.fake_tree)
        self.assertNotEqual(self.fake_tree, self.real_tree)

    def test_the_pin_reads_the_real_committed_base_under_a_replace_ref(self):
        with mock.patch.dict(os.environ, env(), clear=True):
            self.assertEqual(self.pin.committed_base_commit(self.env.run_path), self.base)

    def test_the_blast_radius_helper_reads_the_real_tree_under_a_replace_ref(self):
        with mock.patch.dict(os.environ, env(), clear=True):
            self.assertEqual(self.blast._git(self.env.root, "rev-parse", "HEAD^{tree}"), [self.real_tree])

    def test_a_replace_ref_under_an_ambient_replace_ref_base_is_ignored(self):
        fake = raw(self.env.root, "rev-parse", f"refs/replace/{self.head}")
        raw(self.env.root, "update-ref", "-d", f"refs/replace/{self.head}")
        raw(self.env.root, "update-ref", f"refs/alt/{self.head}", fake)
        alt = {"GIT_REPLACE_REF_BASE": "refs/alt/"}
        # Fixture-live control: git honouring the ambient base reads the fake tree.
        self.assertEqual(raw(self.env.root, "rev-parse", "HEAD^{tree}", extra=alt), self.fake_tree)
        self.assertEqual(raw(self.env.root, "rev-parse", "HEAD^{tree}"), self.real_tree)
        with mock.patch.dict(os.environ, env() | alt, clear=True):
            self.assertEqual(self.blast._git(self.env.root, "rev-parse", "HEAD^{tree}"), [self.real_tree])
            self.assertEqual(self.pin.committed_base_commit(self.env.run_path), self.base)


class _RewrittenBaseBase(_Base):
    """A committed rewrite of `base_commit` to an off-branch commit whose book carries
    `cycle_grandfathered` (a field outside the content hash). The first committed version that
    records `base_commit` is `pin`; anything that hides `pin` from history re-binds the fields."""

    def setUp(self):
        super().setUp()
        self.env.set_run(REVIEW_GATE_PROMPT)
        sup.commit_all(self.env.root, "at the review gate")
        self.start = raw(self.env.root, "rev-parse", "HEAD")
        run = self.env.load_run()
        run["base_commit"] = self.start
        self.env.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.env.root, "run start pins base_commit")
        self.pin = raw(self.env.root, "rev-parse", "HEAD")
        self.env.book_path.write_text(self.env.book_path.read_text() + "cycle_grandfathered: true\n")
        raw(self.env.root, "add", "-A")
        off = raw(self.env.root, "commit-tree", raw(self.env.root, "write-tree"), "-p", self.pin,
                  "-m", "off-branch grandfathered book", extra=COMMITTER)
        run["base_commit"] = off
        self.env.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.env.root, "rewrite base_commit")
        self.rewrite = raw(self.env.root, "rev-parse", "HEAD")

    def advance(self, extra: dict | None = None) -> tuple[int, dict]:
        r = subprocess.run([sys.executable, str(ADVANCE), str(self.env.run_path), "--outcome", "done",
                            "--result", "no evidence"], capture_output=True, text=True,
                           cwd=str(self.env.root), env=env() | (extra or {}))
        return r.returncode, self.out(r)

    def snapshot_history(self, extra: dict | None = None) -> list[str]:
        return raw(self.env.root, "log", "--format=%H", "--", self.env.rel(self.env.run_path),
                   extra=extra).split()

    def assert_refused_unadvanced(self, code: int, out: dict, needle: str) -> None:
        self.assertEqual(code, 1, out)
        self.assertIn(needle, json.dumps(out))
        self.assertEqual(self.env.load_run()["current_prompt"], REVIEW_GATE_PROMPT)


class GraftAndShallowTests(_RewrittenBaseBase):
    """A legacy grafts file, an ambient GIT_GRAFT_FILE, or a shallow boundary hides the run-start
    version of the snapshot. The cycle's git reads turn grafts off and treat a shallow repository
    as one whose run start cannot be read (S5 SC-1)."""

    def test_control_the_rewrite_alone_is_refused(self):
        self.assertIn(self.pin, self.snapshot_history())
        code, out = self.advance()
        self.assert_refused_unadvanced(code, out, "first committed version")

    def test_a_legacy_grafts_file_does_not_hide_the_run_start_version(self):
        info = self.env.root / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "grafts").write_text(f"{self.rewrite} {self.start}\n")
        # Fixture-live control: plain git honours the grafts file and loses the pinning commit.
        self.assertNotIn(self.pin, self.snapshot_history())
        code, out = self.advance()
        self.assert_refused_unadvanced(code, out, "first committed version")

    def test_an_ambient_graft_file_does_not_hide_the_run_start_version(self):
        graft = Path(self._tmp.name) / "grafts"
        graft.write_text(f"{self.rewrite} {self.start}\n")
        ambient = {"GIT_GRAFT_FILE": str(graft)}
        self.assertNotIn(self.pin, self.snapshot_history(ambient))  # fixture-live control
        code, out = self.advance(ambient)
        self.assert_refused_unadvanced(code, out, "first committed version")

    def test_a_shallow_boundary_makes_the_run_start_unknown(self):
        (self.env.root / ".git" / "shallow").write_text(f"{self.rewrite}\n")
        # Fixture-live control: plain git stops history at the rewrite.
        self.assertEqual(self.snapshot_history(), [self.rewrite])
        code, out = self.advance()
        # The start is unknown, so the hash-bound module tag classifies the review gate.
        self.assert_refused_unadvanced(code, out, "independent-review")
        self.assertEqual((out["gate"]["verdict"], out["gate"]["cycle_fields"]), ("refuse", "unbound"))

    def test_the_shared_environment_points_grafts_at_a_path_that_cannot_exist(self):
        with mock.patch.dict(os.environ, {"GIT_GRAFT_FILE": "/tmp/ambient-grafts"}):
            e = cr.git_isolated_env()
        self.assertEqual(e["GIT_GRAFT_FILE"], cr.NO_GRAFT_FILE)
        self.assertFalse(os.path.exists(cr.NO_GRAFT_FILE))
        # Control: git reads that value silently, with no grafts-deprecation hint on stderr.
        r = subprocess.run(["git", "-C", str(self.env.root), "log", "-1", "--format=%H"],
                           capture_output=True, text=True, env=env() | {"GIT_GRAFT_FILE": cr.NO_GRAFT_FILE})
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, self.rewrite, ""))


class ReplacedBookAtBaseTests(_Base):
    """A replace ref on the book blob at `base_commit` that adds `cycle_grandfathered` does not
    grandfather the run (S5 NIT-1)."""

    def test_a_replace_ref_on_the_base_book_does_not_disable_the_gate(self):
        # This case pins its own base_commit, so its fixture records none.
        self.env = sup.Env(Path(self._tmp.name).resolve() / "own-base", kind=self.KIND, base=False)
        self.env.set_run(REVIEW_GATE_PROMPT)
        run = self.env.load_run()
        base = run["base_commit"] = raw(self.env.root, "rev-parse", "HEAD")
        self.env.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.env.root, "run start pins base_commit")
        grandfathered = self.env.book_path.read_text() + "cycle_grandfathered: true\n"
        rel = self.env.rel(self.env.book_path)
        old = raw(self.env.root, "rev-parse", f"{base}:{rel}")
        new = raw(self.env.root, "hash-object", "-w", "--stdin", stdin=grandfathered)
        raw(self.env.root, "replace", old, new)
        self.env.book_path.write_text(grandfathered)
        # Fixture-live control: git honouring replace refs reads the base book as grandfathered.
        self.assertIn("cycle_grandfathered: true", raw(self.env.root, "cat-file", "blob", f"{base}:{rel}"))
        r = subprocess.run([sys.executable, str(ADVANCE), str(self.env.run_path), "--outcome", "done",
                            "--result", "no evidence"], capture_output=True, text=True,
                           cwd=str(self.env.root), env=env())
        out = self.out(r)
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("cycle_grandfathered", out["error"])
        self.assertEqual(self.env.load_run()["current_prompt"], REVIEW_GATE_PROMPT)


class SharedHelperTests(unittest.TestCase):
    def test_the_git_helper_disables_replace_refs_and_drops_their_base(self):
        with mock.patch.dict(os.environ, {"GIT_REPLACE_REF_BASE": "refs/heads/"}):
            e = cr._git_env()
        self.assertEqual(e.get("GIT_NO_REPLACE_OBJECTS"), "1")
        self.assertNotIn("GIT_REPLACE_REF_BASE", e)

    def test_positive_control_an_unrelated_variable_survives(self):
        with mock.patch.dict(os.environ, {"CRUX_PROBE_VAR": "kept"}):
            self.assertEqual(cr._git_env().get("CRUX_PROBE_VAR"), "kept")


if __name__ == "__main__":
    unittest.main()
