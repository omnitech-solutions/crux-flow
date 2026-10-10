"""An ambient git environment never redirects the cycle's git reads.

Variables such as GIT_DIR and GIT_WORK_TREE, exported by a dotfiles alias or set by a hook,
make git read another repository's HEAD and index. The shared git helper in
`council_records` drops every redirecting variable, so the review gate, the reviewer-report
writer and the council runner judge the repository that holds the run.

Each case builds a project repository whose subject carries an unstaged change, and a decoy
repository whose HEAD and index hold that changed text. With GIT_DIR and GIT_WORK_TREE aimed
at the decoy, an unscrubbed git reports the subject clean. Every case carries two controls:
a fixture-live control (raw git under the same environment does read the decoy) and a
clean-environment control (the same command with no ambient variable behaves the same way).

Reads only `crux/` and fixtures built in a temp directory. Never reads the documentation tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _council_gate_support as sup  # noqa: E402

SCRIPTS = sup.SCRIPTS
WRITER = SCRIPTS / "write-review-report.py"
ADVANCE = SCRIPTS / "advance-run.py"
RUN_COUNCIL = SCRIPTS / "run-council.py"
REVIEW_GATE_PROMPT = 10
DIRTY = "# subject v2, never committed in the project\n"

import council_records as cr  # noqa: E402


def scrubbed() -> dict[str, str]:
    return sup.scrubbed_env()


class _AmbientBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name).resolve()
        self.env = sup.Env(tmp / "repo")
        self.decoy = sup.init_repo(tmp / "decoy")
        target = self.decoy / sup.SUBJECT
        target.parent.mkdir(parents=True)
        target.write_text(DIRTY)
        sup.commit_all(self.decoy, "decoy")

    def ambient(self, work_tree: Path | None = None) -> dict[str, str]:
        env = scrubbed()
        env.update(GIT_DIR=str(self.decoy / ".git"), GIT_WORK_TREE=str(work_tree or self.env.root))
        return env

    def dirty(self) -> None:
        self.env.subject.write_text(DIRTY)

    def assert_fixture_live(self) -> None:
        """Raw git under the ambient environment reads the decoy and calls the subject clean."""
        r = subprocess.run(["git", "-C", str(self.env.root), "status", "--porcelain", "--", sup.SUBJECT],
                           capture_output=True, text=True, env=self.ambient())
        self.assertEqual((r.returncode, r.stdout), (0, ""), "the decoy does not mask the change")
        r = subprocess.run(["git", "-C", str(self.env.root), "status", "--porcelain", "--", sup.SUBJECT],
                           capture_output=True, text=True, env=scrubbed())
        self.assertEqual(r.stdout.strip()[:1], "M", "the project does not see its own change")


class ReviewerReportWriterTests(_AmbientBase):
    def run_writer(self, env: dict[str, str]) -> tuple[int, dict | None]:
        r = subprocess.run([sys.executable, str(WRITER), str(self.env.run_path), "--prompt",
                            str(REVIEW_GATE_PROMPT), "--path", sup.SUBJECT, "--verdict", "APPROVE"],
                           capture_output=True, text=True, cwd=str(self.env.root), env=env)
        line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        return r.returncode, (json.loads(line) if line else None)

    def reports(self) -> list[Path]:
        d = self.env.run_dir / "reviews"
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def test_the_writer_refuses_a_dirty_subject_under_an_ambient_git_dir(self):
        self.dirty()
        self.assert_fixture_live()
        code, out = self.run_writer(self.ambient())
        self.assertEqual(code, 1, out)
        self.assertIn("unstaged change", " ".join(out["problems"]))
        self.assertEqual(self.reports(), [])

    def test_clean_environment_control_the_writer_refuses_the_same_dirty_subject(self):
        self.dirty()
        code, out = self.run_writer(scrubbed())
        self.assertEqual(code, 1, out)
        self.assertIn("unstaged change", " ".join(out["problems"]))

    def test_clean_environment_control_a_clean_subject_writes_a_report(self):
        code, out = self.run_writer(scrubbed())
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.reports()), 1)


class ReviewGateTests(_AmbientBase):
    def setUp(self):
        super().setUp()
        self.env.set_run(REVIEW_GATE_PROMPT)
        sup.commit_all(self.env.root, "at the review gate")

    def attach_report_for_the_dirty_text(self) -> Path:
        self.dirty()
        doc = self.env.report_doc(prompt=REVIEW_GATE_PROMPT)  # declares the dirty bytes' sha256
        return self.env.write("report.json", doc, folder=self.env.run_dir / "reviews")

    def advance(self, report: Path, env: dict[str, str]) -> tuple[int, dict]:
        r = subprocess.run([sys.executable, str(ADVANCE), str(self.env.run_path), "--outcome", "done",
                            "--result", "reviewed", "--artifacts", self.env.rel(report)],
                           capture_output=True, text=True, cwd=str(self.env.root), env=env)
        return r.returncode, json.loads(r.stdout)

    def test_the_review_gate_refuses_a_dirty_subject_under_an_ambient_git_dir(self):
        report = self.attach_report_for_the_dirty_text()
        self.assert_fixture_live()
        code, out = self.advance(report, self.ambient())
        self.assertEqual(code, 1, out)
        self.assertEqual(out["gate"]["verdict"], "refuse")
        self.assertEqual(self.env.load_run()["current_prompt"], REVIEW_GATE_PROMPT)

    def test_clean_environment_control_the_review_gate_refuses_the_same_dirty_subject(self):
        report = self.attach_report_for_the_dirty_text()
        code, out = self.advance(report, scrubbed())
        self.assertEqual(code, 1, out)
        self.assertEqual(out["gate"]["verdict"], "refuse")

    def test_clean_environment_control_a_clean_subject_passes_the_review_gate(self):
        doc = self.env.report_doc(prompt=REVIEW_GATE_PROMPT)
        report = self.env.write("report.json", doc, folder=self.env.run_dir / "reviews")
        code, out = self.advance(report, scrubbed())
        self.assertEqual(code, 0, out)
        self.assertEqual(out["gate"]["verdict"], "pass")


class CouncilRunnerTests(_AmbientBase):
    """Under GIT_DIR and GIT_WORK_TREE aimed wholly at the decoy, an unscrubbed runner binds the
    decoy as the repository and refuses the run snapshot as outside it."""

    def setUp(self):
        super().setUp()
        self.home = Path(self._tmp.name).resolve() / "crux-home"
        self.home.mkdir()
        self.env.set_run(3)
        self.question = self.env.council / "question.md"
        self.question.write_text("Is it sound? Review Completeness, Correctness, Consistency, "
                                 "Clarity and Security.\n")
        sup.commit_all(self.env.root, "question")

    def argv(self) -> list[str]:
        return [str(RUN_COUNCIL), str(self.env.run_path), "--prompt", "3", "--round", "1",
                "--question", str(self.question), "--subject", str(self.env.subject)]

    def cli(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        env = {k: v for k, v in env.items() if k != "OPENROUTER_API_KEY"}
        env.update(CRUX_HOME=str(self.home), HOME=str(self.home))
        return subprocess.run([sys.executable, *self.argv()], capture_output=True, text=True,
                              cwd=str(self.env.root), env=env)

    def records(self) -> list[Path]:
        """The council records the runner wrote; its attempt records (`*.attempt.json`) are not."""
        return sorted(p for p in self.env.council.glob("*.json") if not p.name.endswith(".attempt.json"))

    def attempts(self) -> list[Path]:
        return sorted(self.env.council.glob("*.attempt.json"))

    def test_fixture_live_control_raw_git_names_the_decoy_as_the_top_level(self):
        r = subprocess.run(["git", "-C", str(self.env.root), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, env=self.ambient(work_tree=self.decoy))
        self.assertEqual(Path(r.stdout.strip()).resolve(), self.decoy)

    def test_the_runner_binds_the_project_under_an_ambient_git_dir(self):
        # No gateway key: the runner stops at the key check, after binding and the subject checks.
        proc = self.cli(self.ambient(work_tree=self.decoy))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(self.attempts()), 1, "the runner claimed the round in the project, not the decoy")
        self.assertEqual(json.loads(proc.stdout)["refusal_reason"]["code"], "no-key")

    def test_clean_environment_control_the_runner_reaches_the_key_check(self):
        proc = self.cli(scrubbed())
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["refusal_reason"]["code"], "no-key")


class SiblingGitHelperTests(_AmbientBase):
    """The base-commit pin (read by advance-run.py and check-blast-radius.py) and the blast-radius
    gate's own git helper run git with the same shared list dropped."""

    def setUp(self):
        super().setUp()
        import importlib.util
        spec = importlib.util.spec_from_file_location("base_commit_pin_ambient", SCRIPTS / "base_commit_pin.py")
        self.pin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pin)
        spec = importlib.util.spec_from_file_location("blast_radius_ambient", SCRIPTS / "check-blast-radius.py")
        self.blast = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.blast)
        run = self.env.load_run()
        run["base_commit"] = sup.git(self.env.root, "rev-parse", "HEAD").strip()
        import yaml
        self.env.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.env.root, "pin the base")
        self.base = run["base_commit"]

    def test_the_pin_reads_the_projects_committed_base_under_an_ambient_git_dir(self):
        with mock.patch.dict(os.environ, self.ambient(work_tree=self.decoy), clear=True):
            got = self.pin.committed_base_commit(self.env.run_path)
        self.assertEqual(got, self.base)

    def test_clean_environment_control_the_pin_reads_the_same_base(self):
        with mock.patch.dict(os.environ, scrubbed(), clear=True):
            self.assertEqual(self.pin.committed_base_commit(self.env.run_path), self.base)

    def test_the_blast_radius_helper_names_the_project_under_an_ambient_git_dir(self):
        with mock.patch.dict(os.environ, self.ambient(work_tree=self.decoy), clear=True):
            top = self.blast._git(self.env.root, "rev-parse", "--show-toplevel")
        self.assertEqual(Path(top[0]).resolve(), self.env.root)

    def test_clean_environment_control_the_blast_radius_helper_names_the_project(self):
        with mock.patch.dict(os.environ, scrubbed(), clear=True):
            top = self.blast._git(self.env.root, "rev-parse", "--show-toplevel")
        self.assertEqual(Path(top[0]).resolve(), self.env.root)


class SharedHelperTests(unittest.TestCase):
    def test_the_git_helper_drops_every_redirecting_variable(self):
        ambient = {v: "/nowhere" for v in cr.GIT_REDIRECT_VARS}
        with mock.patch.dict(os.environ, ambient):
            env = cr._git_env()
        for var in cr.GIT_REDIRECT_VARS:
            self.assertNotIn(var, env)

    def test_the_list_names_each_variable_the_review_named(self):
        self.assertTrue({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                         "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                         "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM"}
                        <= set(cr.GIT_REDIRECT_VARS))

    def test_the_repository_probe_shares_the_list(self):
        self.assertIs(cr._DISCOVERY_VARS, cr.GIT_REDIRECT_VARS)

    def test_positive_control_an_unrelated_variable_survives(self):
        with mock.patch.dict(os.environ, {"CRUX_PROBE_VAR": "kept"}):
            self.assertEqual(cr._git_env().get("CRUX_PROBE_VAR"), "kept")


class HarnessTests(unittest.TestCase):
    def test_the_fixture_harness_commits_into_its_own_repository_under_an_ambient_git_dir(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            decoy = sup.init_repo(tmp / "decoy")
            (decoy / "f").write_text("x\n")
            sup.commit_all(decoy, "decoy")
            head = sup.git(decoy, "rev-parse", "HEAD").strip()
            with mock.patch.dict(os.environ, {"GIT_DIR": str(decoy / ".git"), "GIT_WORK_TREE": str(decoy)}):
                env = sup.Env(tmp / "repo", base=False)  # one commit: the fixture
            self.assertEqual(sup.git(decoy, "rev-parse", "HEAD").strip(), head)
            self.assertTrue((env.root / ".git").is_dir())
            self.assertEqual(sup.git(env.root, "log", "--format=%s").split(), ["fixture"])


if __name__ == "__main__":
    unittest.main()
