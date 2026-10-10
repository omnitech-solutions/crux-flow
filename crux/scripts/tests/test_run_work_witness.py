"""`run-work-witness.py`: the run-work witness writer, driven through its command line.

Each test builds a temporary repository with a book and a run snapshot (`_council_gate_support.Env`),
under an isolated git configuration and a fake CRUX_HOME whose env file holds a dummy key-shaped
value. Each test names, in its docstring, the mutation of the writer that turns it red. Nothing
reads the documentation tree or a real key.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402

import council_commit as cc  # noqa: E402
import council_records as cr  # noqa: E402

SCRIPT = sup.SCRIPTS / "run-work-witness.py"
FILE_KEY = "sk-or-v1-" + "0123456789abcdef" * 4
WORK = "docs/work.md"


class _Witness(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.home = self.tmp / "cruxhome"
        self.home.mkdir()
        (self.home / "env").write_text(f"OPENROUTER_API_KEY={FILE_KEY}\nFAKE_CRUX_NAME=fake-value-1\n")
        env = {k: v for k, v in os.environ.items() if k not in cr.GIT_REDIRECT_VARS}
        env.update(sup.isolated_git_config(self.tmp / "gitcfg"))
        env["CRUX_HOME"] = str(self.home)
        env.pop("OPENROUTER_API_KEY", None)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = sup.Env(self.tmp / "repo")
        self.repo = self.env.root
        self.witness = self.env.run_dir / cc.WITNESS_FILE
        # WORK is a run-work candidate in every test unless it says otherwise: `commit` re-checks
        # the runner's candidacy reading, and an uncommitted new file is a candidate only through
        # a prompt's artifacts.
        self.candidate(WORK)

    def candidate(self, rel: str) -> None:
        """Name `rel` in the run snapshot's artifacts, which makes it a run-work candidate."""
        run = self.env.load_run()
        run["prompts"][1]["artifacts"] = [*run["prompts"][1]["artifacts"], rel]
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo, capture_output=True,
                              text=True, env=dict(os.environ), timeout=120)

    def record(self, rel: str = WORK, prompt: str = "2") -> subprocess.CompletedProcess:
        return self.run_cli("record", str(self.env.run_path), "--prompt", prompt, "--path", rel)

    def commit(self, rel: str = WORK) -> subprocess.CompletedProcess:
        return self.run_cli("commit", str(self.env.run_path), "--path", rel)

    def write(self, rel: str, text: str) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def load_module(self):
        spec = importlib.util.spec_from_file_location("_rww_under_test", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def in_process(self, mod, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mod.main(list(argv))
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def head(self) -> str:
        return sup.git(self.repo, "rev-parse", "HEAD").strip()

    def refusal(self, r: subprocess.CompletedProcess) -> dict:
        self.assertEqual(r.returncode, 1, r.stderr)
        payload = json.loads(r.stdout)
        self.assertIn("refused", payload)
        return payload


class RecordTests(_Witness):
    def test_record_creates_a_schema_valid_witness_bound_to_the_run(self):
        """Mutation: bind the witness to a constant book or skip the entry's digest -> the
        binding or sha256 assertion fails; an invalid document fails `witness_errors`."""
        data = "# work v1\n"
        self.write(WORK, data)
        r = self.record()
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(self.witness.read_text())
        self.assertEqual(cc.witness_errors(doc), [])
        self.assertEqual(doc["book"], {"id": sup.BOOK_ID, "content_hash": self.env.hash})
        self.assertEqual(doc["run_id"], sup.RUN_ID)
        self.assertEqual(len(doc["entries"]), 1)
        entry = doc["entries"][0]
        self.assertEqual(entry["path"], WORK)
        self.assertEqual(entry["sha256"], cr.sha256_bytes(data.encode()))
        self.assertEqual(entry["prompt"], 2)
        self.assertRegex(entry["written_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
        self.assertEqual(json.loads(r.stdout)["recorded"]["sha256"], entry["sha256"])

    def test_a_second_record_appends_and_witness_sha_reads_the_latest(self):
        """Mutation: overwrite the entries instead of appending, or read the first entry -> the
        count or the latest digest is wrong."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        self.write(WORK, "v2\n")
        self.assertEqual(self.record(prompt="3").returncode, 0)
        doc = cc.read_witness(self.env.run_dir)
        self.assertEqual([e["prompt"] for e in doc["entries"]], [2, 3])
        self.assertEqual(cc.witness_sha(self.env.run_dir, WORK), cr.sha256_bytes(b"v2\n"))
        self.assertIsNone(cc.witness_sha(self.env.run_dir, "docs/other.md"))

    def test_a_path_outside_the_repository_is_refused(self):
        """Mutation: skip the input-path check and the containment check -> the outside file is
        witnessed as `../outside.md`."""
        outside = self.tmp / "outside.md"
        outside.write_text("x\n")
        payload = self.refusal(self.record(str(outside)))
        self.assertEqual(payload["refused"], "outside-repo")
        self.assertFalse(self.witness.exists())

    def test_a_symlinked_subject_and_an_env_file_are_refused(self):
        """Mutation: skip the input-path check -> a symlink, or a `.env` file, is witnessed and
        could later be committed as run work."""
        self.write("docs/real.md", "x\n")
        (self.repo / "docs" / "link.md").symlink_to(self.repo / "docs" / "real.md")
        self.assertEqual(self.refusal(self.record("docs/link.md"))["refused"], "symlink")
        self.write("docs/.env", "A=b\n")
        self.assertEqual(self.refusal(self.record("docs/.env"))["refused"], "env-file")
        self.assertFalse(self.witness.exists())

    def test_a_symlinked_witness_file_is_refused_and_its_target_is_untouched(self):
        """Mutation: drop the symlink checks in `read_witness` and in the atomic write -> the
        witness is read through the link and the record exits 0. The target is a valid witness
        for this run, so only the symlink check can refuse it."""
        target = self.tmp / "target.json"
        valid = json.dumps({"record_type": "run-work-witness", "format_version": "1",
                            "book": {"id": sup.BOOK_ID, "content_hash": self.env.hash},
                            "run_id": sup.RUN_ID, "entries": []}) + "\n"
        target.write_text(valid)
        self.witness.symlink_to(target)
        self.write(WORK, "v1\n")
        payload = self.refusal(self.record())
        self.assertEqual(payload["refused"], "witness-invalid")
        self.assertEqual(target.read_text(), valid)
        self.assertTrue(self.witness.is_symlink())

    def test_a_refusal_whose_text_matches_a_key_shape_is_withheld_and_stays_json(self):
        """Mutation: print the refusal payload unscanned -> the key-shaped path text reaches
        stdout. The path does not exist, so the refusal is `missing` from either subcommand; `record`'s
        `secret-scan` refusal applies only to a path that exists. Positive control: the refusal code
        survives in parseable JSON."""
        name = f"docs/{FILE_KEY}.md"
        payload = self.refusal(self.commit(name))
        self.assertEqual(payload["refused"], "missing")
        self.assertIn("openrouter-key", payload["withheld"])
        self.assertNotIn(FILE_KEY, json.dumps(payload))

    def test_a_path_in_the_git_directory_is_refused(self):
        """Mutation: drop the git-directory check from `_resolve_subject` -> `.git/description`
        is witnessed (exit 0), and its later `commit` dies in a traceback."""
        gd = self.repo / ".git"
        for arg in (".git/description", str(gd / "description"), ".git/hooks/../description",
                    "docs/../.git/config"):
            with self.subTest(path=arg):
                self.assertIn(self.refusal(self.record(arg))["refused"], ("git-dir", "outside-repo"))
        r = self.record(".git/description")
        self.assertEqual(self.refusal(r), {"refused": "git-dir", "path": ".git/description"})
        r = self.record(str(gd / "description"))
        self.assertEqual(self.refusal(r)["refused"], "git-dir")
        self.assertFalse(self.witness.exists())
        r = self.commit(".git/description")
        self.assertEqual(self.refusal(r)["refused"], "git-dir")

    def test_a_value_error_from_commit_owned_is_a_json_refusal(self):
        """Mutation: let `commit_owned`'s ValueError escape `main` -> a traceback and no JSON.
        Driven in-process so `commit_owned` can be made to raise; the command-line cases above
        cover the path refusal that makes this unreachable for a `.git` path."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        mod = self.load_module()
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(mod.council_commit, "commit_owned",
                               side_effect=ValueError("owned path refused")), \
                contextlib.redirect_stdout(out):
            code = mod.main(["commit", str(self.env.run_path), "--path", WORK])
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["refused"], "invalid")
        self.assertEqual(payload["path"], WORK)

    def test_an_unknown_prompt_is_refused(self):
        """Mutation: accept any prompt number -> prompt 99 is recorded."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.refusal(self.record(prompt="99"))["refused"], "prompt")
        self.assertFalse(self.witness.exists())

    def test_a_malformed_env_file_is_exit_two_on_commit_and_prints_no_value(self):
        """Mutation: let the env parse error reach stdout or stderr -> the key-shaped text is
        printed. Positive control: the error names the env file problem on stderr."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        (self.home / "env").write_text(f"BROKEN LINE {FILE_KEY}\n")
        r = self.commit()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("env file", r.stderr)
        self.assertNotIn(FILE_KEY, r.stderr)


class CommitTests(_Witness):
    def test_commit_commits_exactly_the_witnessed_path_and_leaves_staged_work_staged(self):
        """Mutation: commit through `git add -A`/`commit -a` instead of `commit_owned` -> the
        staged unrelated file lands in the run-work commit."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        self.write("docs/unrelated.md", "staged\n")
        sup.git(self.repo, "add", "--", "docs/unrelated.md")
        before = self.head()
        r = self.commit()
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["commit"], self.head())
        self.assertEqual(sup.git(self.repo, "rev-parse", "HEAD^").strip(), before)
        changed = sup.git(self.repo, "diff-tree", "-r", "--no-commit-id", "--name-only", "HEAD").split()
        self.assertEqual(changed, [WORK])
        self.assertEqual(sup.git(self.repo, "log", "-1", "--format=%s").strip(),
                         f"crux run work: {sup.BOOK_ID} {sup.RUN_ID} prompt 2")
        self.assertEqual(sup.git(self.repo, "diff", "--cached", "--name-only").split(), ["docs/unrelated.md"])

    def test_bytes_edited_after_the_witness_are_refused_and_not_committed(self):
        """Mutation: commit without re-checking the working-tree bytes against the latest
        witness entry -> the edited bytes are committed as run work."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        self.write(WORK, "edited by someone else\n")
        before = self.head()
        payload = self.refusal(self.commit())
        self.assertEqual(payload["refused"], "witness-mismatch")
        self.assertEqual(payload["path"], WORK)
        self.assertEqual(self.head(), before)
        self.assertNotIn(WORK, sup.git(self.repo, "ls-files").split())

    def test_an_unwitnessed_path_is_refused(self):
        """Mutation: treat a missing witness entry as a match -> an unwitnessed file is
        committed."""
        self.write(WORK, "v1\n")
        before = self.head()
        self.assertEqual(self.refusal(self.commit())["refused"], "unwitnessed")
        self.assertEqual(self.head(), before)

    def test_a_user_staged_version_of_the_path_is_refused_as_mixed_and_survives(self):
        """`git commit --only -- P` resets P's index entry. Mutation: skip the mixed check before
        `commit_owned` -> exit 0 (before the commit_owned guard) or a `commit` refusal, never
        `mixed`. Positive control: with nothing staged the same path commits (test above)."""
        self.write(WORK, "base\n")
        sup.git(self.repo, "add", "--", WORK)
        sup.git(self.repo, "commit", "-q", "-m", "base work")
        self.write(WORK, "USER STAGED WIP\n")
        sup.git(self.repo, "add", "--", WORK)
        self.write(WORK, "run wrote this\n")
        self.assertEqual(self.record().returncode, 0)
        before = self.head()
        payload = self.refusal(self.commit())
        self.assertEqual(payload, {"refused": "mixed", "path": WORK})
        self.assertEqual(self.head(), before)
        self.assertEqual(sup.git(self.repo, "show", ":" + WORK), "USER STAGED WIP\n")
        self.assertEqual((self.repo / WORK).read_text(), "run wrote this\n")

    def test_a_second_commit_after_the_first_landed_is_already_committed(self):
        """Mutation: always call `commit_owned` -> git's 'nothing to commit' surfaces as a
        `hook-or-commit-failed` refusal. Positive control: the first commit moves HEAD."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        before = self.head()
        first = self.commit()
        self.assertEqual(first.returncode, 0, first.stderr)
        landed = self.head()
        self.assertNotEqual(landed, before)
        second = self.commit()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        payload = json.loads(second.stdout)
        self.assertEqual(payload["result"], "already-committed")
        self.assertEqual(payload["path"], WORK)
        self.assertEqual(self.head(), landed)

    def test_commit_refuses_a_witness_bound_to_another_run(self):
        """Mutation: skip the binding check in `commit` -> the foreign witness's entry authorises
        the commit and HEAD moves."""
        self.write(WORK, "v1\n")
        foreign = {"record_type": "run-work-witness", "format_version": "1",
                   "book": {"id": sup.BOOK_ID, "content_hash": "sha256:" + "0" * 64},
                   "run_id": sup.RUN_ID,
                   "entries": [{"path": WORK, "sha256": cr.sha256_bytes(b"v1\n"), "prompt": 2,
                                "written_at": "2026-10-02T00:00:00.000000Z"}]}
        self.assertEqual(cc.witness_errors(foreign), [])
        self.witness.write_text(json.dumps(foreign) + "\n")
        before = self.head()
        self.assertEqual(self.refusal(self.commit())["refused"], "witness-binding")
        self.assertEqual(self.head(), before)

    def test_record_replaces_a_witness_left_by_another_run_of_the_book(self):
        """Every run of a book shares the run directory, so a new run finds the last run's witness.
        `record` starts this run's witness in its place and names the run it replaced. Red when
        `record` refuses `witness-binding` for any other run: the new run can never witness its
        work. Positive control: a witness bound to another book still refuses, unchanged."""
        self.write(WORK, "v1\n")
        old = {"record_type": "run-work-witness", "format_version": "1",
               "book": {"id": sup.BOOK_ID, "content_hash": "sha256:" + "0" * 64},
               "run_id": "RUN-000",
               "entries": [{"path": "docs/other.md", "sha256": "0" * 64, "prompt": 2,
                            "written_at": "2026-10-02T00:00:00.000000Z"}]}
        self.assertEqual(cc.witness_errors(old), [])
        other_book = dict(old, book={"id": "PB-0998", "content_hash": "sha256:" + "0" * 64})
        self.witness.write_text(json.dumps(other_book) + "\n")
        before = self.witness.read_bytes()
        self.assertEqual(self.refusal(self.record())["refused"], "witness-binding")
        self.assertEqual(self.witness.read_bytes(), before)
        self.witness.write_text(json.dumps(old) + "\n")
        r = self.record()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout).get("replaced_run_id"), "RUN-000")
        doc = json.loads(self.witness.read_text())
        self.assertEqual((doc["run_id"], [e["path"] for e in doc["entries"]]), (sup.RUN_ID, [WORK]))
        self.assertEqual(self.commit().returncode, 0)

    def test_a_hook_rejection_is_a_refusal_naming_the_commit_code(self):
        """Mutation: report success without checking `CommitRefused` -> exit 0 with no commit."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        before = self.head()
        payload = self.refusal(self.commit())
        self.assertEqual(payload["refused"], "commit")
        self.assertEqual(payload["code"], "hook-or-commit-failed")
        self.assertEqual(self.head(), before)


class CommitCandidacyTests(_Witness):
    """`commit` re-checks the runner's candidacy reading and never commits under `council/`."""

    OTHER = "docs/other.md"

    def test_a_witnessed_path_that_is_not_a_run_work_candidate_is_refused_as_unattributed(self):
        """Mutation: skip the candidacy re-check in `cmd_commit` -> the witnessed non-candidate
        commits (exit 0) and HEAD moves. Positive control: the candidate WORK commits."""
        self.write(WORK, "v1\n")
        self.write(self.OTHER, "other\n")
        self.assertEqual(self.record(WORK).returncode, 0)
        self.assertEqual(self.record(self.OTHER).returncode, 0)
        before = self.head()
        self.assertEqual(self.refusal(self.commit(self.OTHER)), {"refused": "unattributed", "path": self.OTHER})
        self.assertEqual(self.head(), before)
        control = self.commit(WORK)
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertNotEqual(self.head(), before)

    def test_a_path_under_the_runs_council_directory_is_refused(self):
        """Mutation: drop the council-path check -> the witnessed record under `council/` commits.
        The path is a candidate and witnessed, so only that check refuses it. Positive control:
        the same path outside `council/` commits."""
        under = self.env.rel(self.env.council) + "/rogue.json"
        beside = self.env.rel(self.env.run_dir) + "/beside.json"
        for rel in (under, beside):
            self.write(rel, "{}\n")
            self.candidate(rel)
        # `record` refuses a council path too, so the commit check is driven without a witness.
        self.assertEqual(self.record(beside).returncode, 0)
        before = self.head()
        self.assertEqual(self.refusal(self.commit(under)), {"refused": "council-path", "path": under})
        self.assertEqual(self.head(), before)
        control = self.commit(beside)
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertNotEqual(self.head(), before)

    def test_record_refuses_a_path_under_the_runs_council_directory(self):
        """Mutation: drop the council-path check in `record` -> a council path is witnessed, and
        the runner would offer `commit-run-work` for a path `commit` always refuses. Positive
        control: the same path beside `council/` records."""
        under = self.env.rel(self.env.council) + "/rogue.json"
        beside = self.env.rel(self.env.run_dir) + "/beside.json"
        for rel in (under, beside):
            self.write(rel, "{}\n")
        self.assertEqual(self.refusal(self.record(under)), {"refused": "council-path", "path": under})
        self.assertFalse(self.witness.exists())
        ok = self.record(beside)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([e["path"] for e in json.loads(self.witness.read_text())["entries"]], [beside])

    def test_commit_binds_the_book_named_by_book(self):
        """Mutation: no `--book` on `commit` -> a run whose book the layout cannot find exits 2
        although the council runner, given the same `--book`, offered the repair. Positive
        control: without `--book` the same commit exits 2 naming the book."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        parked = self.tmp / "elsewhere" / self.env.book_path.name
        parked.parent.mkdir()
        self.env.book_path.rename(parked)
        control = self.commit()
        self.assertEqual(control.returncode, 2, control.stdout + control.stderr)
        self.assertIn("book", control.stderr)
        before = self.head()
        r = self.run_cli("commit", str(self.env.run_path), "--path", WORK, "--book", str(parked))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["committed"], WORK)
        self.assertNotEqual(self.head(), before)

    def test_a_run_whose_book_cannot_be_bound_is_exit_two(self):
        """Mutation: read candidacy without binding the book -> no exit 2. Candidacy cannot be
        read without the book, so this is an environment fault. Positive control: with the book
        present the same commit succeeds."""
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        self.env.book_path.rename(self.tmp / "book.parked")
        r = self.commit()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("book", r.stderr)
        self.assertEqual(r.stdout, "")
        (self.tmp / "book.parked").rename(self.env.book_path)
        self.assertEqual(self.commit().returncode, 0)


class RecordSecretScanTests(_Witness):
    def test_a_key_shaped_subject_path_is_refused_nothing_stored_and_never_echoed(self):
        """Mutation: drop the scan of the path -> the key-shaped path is stored in the witness.
        Positive control: a normal path records."""
        rel = f"docs/{FILE_KEY}.md"
        self.write(rel, "x\n")
        r = self.record(rel)
        self.assertEqual(self.refusal(r), {"refused": "secret-scan"})
        self.assertFalse(self.witness.exists())
        self.assertNotIn("sk-or-v1", r.stdout + r.stderr)
        self.write(WORK, "v1\n")
        ok = self.record(WORK)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([e["path"] for e in json.loads(self.witness.read_text())["entries"]], [WORK])


class FailClosedTests(_Witness):
    def test_a_missing_pyyaml_exits_2_with_a_capability_message(self):
        """Mutation: no import guard -> the unguarded script, given `--help`, exits 0 (and a run
        read goes through the minimal fallback parser). Control: the same harness with PyYAML
        importable reaches argparse and exits 0, so the blocked run's exit 2 is the guard's."""
        harness = ("import runpy, sys\n"
                   "if sys.argv[1] == 'block':\n"
                   "    sys.modules['yaml'] = None\n"
                   "sys.argv = [{path!r}, '--help']\n"
                   "runpy.run_path({path!r}, run_name='__main__')\n").format(path=str(SCRIPT))

        def run(mode):
            return subprocess.run([sys.executable, "-c", harness, mode], cwd=self.repo, env=sup.scrubbed_env(),
                                  capture_output=True, text=True, timeout=120)

        control = run("allow")
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertIn("run-work-witness.py", control.stdout)
        blocked = run("block")
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertIn("requires PyYAML", blocked.stderr)
        self.assertNotIn("Traceback", blocked.stderr)
        self.assertEqual(blocked.stdout, "")

    def test_a_usage_error_never_echoes_a_key_shaped_value(self):
        """Mutation: argparse's own `error` -> the bad `--prompt` value, a key shape, reaches
        stderr. Positive control: a non-key bad value still produces argparse's usage error."""
        r = self.run_cli("record", str(self.env.run_path), "--prompt", FILE_KEY, "--path", WORK)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("sk-or-v1", r.stderr)
        self.assertIn("error:", r.stderr)
        control = self.run_cli("record", str(self.env.run_path), "--prompt", "not-a-number", "--path", WORK)
        self.assertEqual(control.returncode, 2)
        self.assertIn("not-a-number", control.stderr)

    def test_a_stale_temp_file_with_the_pid_name_no_longer_stops_record(self):
        """Mutation: name the temp file by pid again -> the stale `.<pid>.tmp` makes the O_EXCL
        create fail and `record` exits 2. The stale file is not the witness and survives."""
        self.write(WORK, "v1\n")
        mod = self.load_module()
        stale = self.env.run_dir / f".{cc.WITNESS_FILE}.4242.tmp"
        stale.write_text("stale")
        with mock.patch.object(mod.os, "getpid", return_value=4242):
            code, out, err = self.in_process(mod, "record", str(self.env.run_path), "--prompt", "2", "--path", WORK)
        self.assertEqual(code, 0, err)
        self.assertEqual(stale.read_text(), "stale")
        self.assertEqual(len(json.loads(self.witness.read_text())["entries"]), 1)

    def test_record_holds_the_run_directory_lock_for_its_read_modify_write(self):
        """Mutation: no `flock` -> the readiness event is never set. The test holds the lock, waits
        for the record to reach `flock`, shows it has not written, releases the lock, and shows
        the record then lands."""
        import fcntl
        import threading
        self.write(WORK, "v1\n")
        mod = self.load_module()
        about_to_block = threading.Event()
        real_flock = mod.fcntl.flock

        def spy(fd, op):
            about_to_block.set()
            return real_flock(fd, op)

        results: list = []
        fd = os.open(self.env.run_dir, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(mod.fcntl, "flock", spy), contextlib.redirect_stdout(io.StringIO()):
            t = threading.Thread(target=lambda: results.append(
                mod.main(["record", str(self.env.run_path), "--prompt", "2", "--path", WORK])), daemon=True)
            t.start()
            self.assertTrue(about_to_block.wait(60), "record never asked for the lock")
            t.join(0.5)
            self.assertTrue(t.is_alive(), "record finished while the lock was held")
            self.assertFalse(self.witness.exists())
            fcntl.flock(fd, fcntl.LOCK_UN)
            t.join(60)
        self.assertFalse(t.is_alive())
        self.assertEqual(results, [0])
        self.assertEqual(len(json.loads(self.witness.read_text())["entries"]), 1)

    def test_two_concurrent_records_both_land(self):
        """Both processes exit 0 and the witness holds both entries. This checks the outcome
        only: removing the lock need not turn it red, because the race window is small. The lock's
        mutation test is `test_record_holds_the_run_directory_lock_for_its_read_modify_write`."""
        self.write(WORK, "v1\n")
        self.write("docs/second.md", "v2\n")
        self.candidate("docs/second.md")
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "record", str(self.env.run_path), "--prompt", "2",
                                   "--path", rel], cwd=self.repo, env=dict(os.environ), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for rel in (WORK, "docs/second.md")]
        for p in procs:
            out, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, out + err)
        doc = json.loads(self.witness.read_text())
        self.assertEqual(sorted(e["path"] for e in doc["entries"]), sorted([WORK, "docs/second.md"]))

    def test_record_without_fcntl_exits_2_with_one_line(self):
        """Mutation: record without the lock when `fcntl` is absent -> exit 0 and a witness.
        Positive control: with `fcntl` present the same record succeeds (tests above)."""
        self.write(WORK, "v1\n")
        mod = self.load_module()
        with mock.patch.object(mod, "fcntl", None):
            code, out, err = self.in_process(mod, "record", str(self.env.run_path), "--prompt", "2", "--path", WORK)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("fcntl", err)
        self.assertFalse(self.witness.exists())


class CommitRefusedConsumerTests(_Witness):
    def _commit_with(self, exc):
        self.write(WORK, "v1\n")
        self.assertEqual(self.record().returncode, 0)
        mod = self.load_module()
        with mock.patch.object(mod.council_commit, "commit_owned", side_effect=exc):
            return self.in_process(mod, "commit", str(self.env.run_path), "--path", WORK)

    def test_a_timeout_refusal_carries_moved_and_staged(self):
        """Mutation: drop `moved`/`staged` from the refusal -> the owner is not told what the
        timed-out call moved or left staged. Positive control: with both empty the keys are absent."""
        code, out, _ = self._commit_with(cc.CommitRefused("timeout", "t", moved=("a.txt", "refs/stash"),
                                                          staged=(WORK,)))
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual((payload["refused"], payload["code"]), ("commit", "timeout"))
        self.assertEqual(payload["moved"], ["a.txt", "refs/stash"])
        self.assertEqual(payload["staged"], [WORK])
        code, out, _ = self._commit_with(cc.CommitRefused("timeout", "t"))
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["code"], "timeout")
        self.assertNotIn("moved", payload)
        self.assertNotIn("staged", payload)


if __name__ == "__main__":
    unittest.main()
