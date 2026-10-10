"""Council attempts, end to end: the runner's command line and the gate check at advance.

Every case builds a temporary git repository with `_council_gate_support.Env` (an adr cycle book,
a run snapshot at the real layout, the fixture commit pinned as the run's `base_commit`), then:

- drives `run-council.py` through `_council_cli_shim.py`, a child process that calls the runner's
  `main(argv, transport=...)` with an `httpx.MockTransport` that counts every request in a file;
- drives `advance-run.py` through its real command line, and asserts the gate verdict and stops,
  the prompt's written state and `current_prompt` in the run snapshot, never an exit code alone;
- installs pre-commit hooks into the temporary repository's `.git/hooks` to reject, reformat,
  stash and restore, or dump their environment.

Isolation. Every child gets `isolated_git_config` (a temporary `GIT_CONFIG_GLOBAL` and
`GIT_CONFIG_NOSYSTEM=1`), a temporary `CRUX_HOME` and `HOME`, and a dummy gateway key that is not
a real key. Only the runner receives the key. Recovery runs with no key and an empty `CRUX_HOME`,
through the shim. The runner hands recovery no transport, so the shim routes every httpx client
the process builds through the counting transport, and counts and refuses any other internet
socket connection; a request recovery made would be counted. No case reaches the network,
reads the machine's git configuration or a real key, or reads anything outside `crux/` and its
temporary directory.

The council gate under test is prompt 3, the adr module's council prompt (module `adr-1`, prompts
2 to 5). Each test's docstring names its positive control and the mutation that would turn it green
wrongly. The test names are the behavioural test map's names, referenced by the run record.

Contract assumptions taken literally from the implementation plan's runner contract (section 2.2)
are listed in `CONTRACT_ASSUMPTIONS` below, so integration can reconcile them in one place.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _council_gate_support as sup  # noqa: E402

SCRIPTS = HERE.parent
SHIM = HERE / "_council_cli_shim.py"
ADVANCE = SCRIPTS / "advance-run.py"
WITNESS = SCRIPTS / "run-work-witness.py"
WITNESS_SCHEMA = SCRIPTS.parent / "schemas" / "run-work-witness.schema.json"
sys.path.insert(0, str(SCRIPTS))

try:
    import httpx  # noqa: F401
    import crux.council.async_council  # noqa: F401
    HAVE_COUNCIL = True
except Exception:  # router deps unavailable: run under uv
    HAVE_COUNCIL = False

import council_records as cr  # noqa: E402

CONTRACT_ASSUMPTIONS = (
    "exit 0 prints a summary with repository-relative `record` and `attempt` paths",
    "a retryable preflight refusal prints {refused: preflight, record: null, causes: [{name, code, "
    "repair}], refusals_at_prompt, bound: 3} and exits 1",
    "an attempt-open refusal prints {refused: attempt-open, record: null, attempt: <path>} and exits 1",
    "the diagnostics log is <run_dir>/council-preflight.jsonl, one object per line with event, prompt, codes",
    "the runner's two commits carry the subjects `crux council attempt: ...` and `crux council record: ...`",
    "recovery is `run-council.py --recover <run> --prompt N [--owner-commit-pending]` and prints a JSON "
    "object whose `recovery` value is one of recognised, committed, released, nothing-open, mismatch, "
    "unproven, live-runner, index-locked, commit-refused, probe; recognised and committed exit with the "
    "record's code",
    "a recovery commit council_commit refused reports `commit-refused` with its `code` and exits 1; "
    "`commit_landed: true` is present only when the commit landed and its verification refused it",
    "`--probe` writes nothing and reports `probe` with a `lock` value",
    "the witness writer is `run-work-witness.py commit <run> --path <repo-relative path>`",
    "a pending copy keeps the council record's file name under council_records.pending_dir",
    "the attempt record is not pending-copied, so the first remove_pending call follows the record commit",
    "commit_owned calls verify_commit once per commit, so its second call verifies the record commit",
    "a stderr line for an exit 2 after a claim names `--recover`; a hook-altered commit's line says mismatch",
)

DUMMY_KEY = "zz-gateway-dummy-key-7c1e9a2b4d6f"
EXTRA_NAME = "CRUX_BEHAVIOUR_EXTRA_SECRET"
EXTRA_VALUE = "zz-extra-dummy-value-31f0c8d2"
MARKER = "CRUX_BEHAVIOUR_HOOK_MARKER"
TIMEOUT = 180
DEADLINE = 60.0
PROMPT = 3
TAG = "adr-1"
SUBJECT = sup.SUBJECT
QUESTION_REL = "docs/questions/council-question.md"
TYPO = "docs/adrs/ADR-0001-fixtur.md"
HELD = {"openai": {"decision": "REQUEST_CHANGES", "findings": []}}
CRASHED = 137

SRC_STAGED = "src/staged.txt"        # X: a new file, staged
SRC_UNSTAGED = "src/unstaged.txt"    # Y: a tracked file with an unstaged change
SRC_PARTIAL = "src/partial.txt"      # Z: staged v2, working tree v3
SRC_UNTRACKED = "src/untracked.txt"  # W: untracked
SRC_FILES = (SRC_STAGED, SRC_UNSTAGED, SRC_PARTIAL, SRC_UNTRACKED)

HOOK_PY = r'''
import json, os, subprocess, sys

mode = sys.argv[1]


def staged():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"], capture_output=True).stdout
    return [n.decode("utf-8", "replace") for n in out.split(b"\0") if n]


def council_record(name):
    return ("/council/" in "/" + name and name.endswith(".json") and not name.endswith(".attempt.json")
            and "/council/subjects/" not in "/" + name)


if mode == "reject-record":
    if any(council_record(n) for n in staged()):
        print("hook: refusing a council record", file=sys.stderr)
        sys.exit(1)
elif mode == "reject-attempt":
    if any(n.endswith(".attempt.json") for n in staged()):
        print("hook: refusing an attempt record", file=sys.stderr)
        sys.exit(1)
elif mode == "reformat":
    for n in staged():
        if council_record(n):
            with open(n, encoding="utf-8") as fh:
                doc = json.load(fh)
            with open(n, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc, indent=4) + "\n")
            subprocess.run(["git", "add", "--", n], check=True)
elif mode == "env-dump":
    with open(sys.argv[2], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(os.environ)) + "\n")
elif mode == "stash":
    # What a pre-commit framework does around its hooks: set unstaged changes aside, run, restore.
    diff = subprocess.run(["git", "diff", "--binary"], capture_output=True).stdout
    if diff:
        subprocess.run(["git", "checkout", "--", "."], check=True)
        subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], input=diff, check=True)
elif mode == "stash-drop":
    # A framework that sets unstaged changes aside and never restores them (it died in between).
    if subprocess.run(["git", "diff", "--binary"], capture_output=True).stdout:
        subprocess.run(["git", "checkout", "--", "."], check=True)
sys.exit(0)
'''


def json_docs(text: str) -> list:
    """Every JSON document on `text`, in order (recovery may reprint a summary after its report)."""
    dec, out, i = json.JSONDecoder(), [], 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except ValueError:
            break
        out.append(obj)
    return out


@dataclass
class Result:
    rc: int
    stdout: str
    stderr: str
    counter: Path

    @property
    def requests(self) -> int:
        if not self.counter.exists():
            return 0
        return sum(1 for line in self.counter.read_text(encoding="utf-8").splitlines() if line.strip())

    def show(self) -> str:
        return f"exit {self.rc}\nstdout: {self.stdout}\nstderr: {self.stderr}"


@dataclass
class Commit:
    sha: str
    subject: str
    files: list


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class _Behaviour(unittest.TestCase):

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.git_cfg = sup.isolated_git_config(self.tmp / "gitcfg")
        self.home = self.tmp / "crux-home"
        self.home.mkdir()
        self.empty_home = self.tmp / "crux-home-empty"
        self.empty_home.mkdir()
        (self.tmp / "home").mkdir()
        self.env = sup.Env(self.tmp / "repo", base=True)
        self.root = self.env.root
        self.run_rel = self.env.rel(self.env.run_dir)
        # One setup commit after the run's base: the run points at the council prompt, the question
        # exists, the subject changed in base_commit..HEAD (the run's own earlier work), and two
        # tracked files exist for the unrelated-change fixture.
        self.env.set_run(PROMPT)
        self.question = self.root / QUESTION_REL
        self.question.parent.mkdir(parents=True)
        self.question.write_text("Is the proposal sound? Review Completeness, Correctness, Consistency, "
                                 "Clarity and Security.\n")
        self.env.subject.write_text("# subject v1, revised by the run before the council prompt\n")
        (self.root / "src").mkdir()
        (self.root / SRC_UNSTAGED).write_text("unstaged v1\n")
        (self.root / SRC_PARTIAL).write_text("partial v1\n")
        sup.commit_all(self.root, "setup")
        self.n = 0
        self.barriers: list[Path] = []

    # -- environment ------------------------------------------------------------------------

    def base_env(self) -> dict:
        env = sup.scrubbed_env()
        for name in ("OPENROUTER_API_KEY", EXTRA_NAME, MARKER):
            env.pop(name, None)
        env.update(self.git_cfg)
        env.update(CRUX_HOME=str(self.home), HOME=str(self.tmp / "home"))
        return env

    # -- the runner, through the shim -------------------------------------------------------

    def council_argv(self, round_=1, subjects=None, extra=(), prompt=PROMPT) -> list[str]:
        argv = [str(self.env.run_path), "--prompt", str(prompt), "--round", str(round_),
                "--question", str(self.question)]
        for s in subjects or [self.env.subject]:
            argv += ["--subject", str(s)]
        return argv + list(extra)

    def recover_argv(self, *extra) -> list[str]:
        return ["--recover", str(self.env.run_path), "--prompt", str(PROMPT), *extra]

    def _shim_cmd(self, runner_argv, *, decisions=None, crash_at=(), raise_after=(), exit_at=None,
                  block_at=None, barrier=None, start_barrier=None, self_check=False) -> tuple[list[str], Path]:
        self.n += 1
        counter = self.tmp / f"requests-{self.n}.jsonl"
        cmd = [sys.executable, str(SHIM), "--counter", str(counter), "--deadline", str(DEADLINE)]
        if self_check:
            cmd.append("--self-check")
        if decisions:
            path = self.tmp / f"decisions-{self.n}.json"
            path.write_text(json.dumps(decisions), encoding="utf-8")
            cmd += ["--decisions", str(path)]
        for t in crash_at:
            cmd += ["--crash-at", t]
        for t in raise_after:
            cmd += ["--raise-after", t]
        if exit_at is not None:
            cmd += ["--exit-at-request", str(exit_at)]
        if block_at is not None:
            self.barriers.append(barrier)
            cmd += ["--block-at-request", str(block_at), "--barrier", str(barrier)]
        if start_barrier is not None:
            self.barriers.append(start_barrier)
            cmd += ["--start-barrier", str(start_barrier)]
        return cmd + ["--", *runner_argv], counter

    def _child_env(self, key: bool, env_extra: dict | None) -> dict:
        env = self.base_env()
        if key:
            env["OPENROUTER_API_KEY"] = DUMMY_KEY
        else:
            env["CRUX_HOME"] = str(self.empty_home)
        env.update(env_extra or {})
        return env

    def shim(self, runner_argv, *, key=True, env_extra=None, **opts) -> Result:
        cmd, counter = self._shim_cmd(runner_argv, **opts)
        proc = subprocess.run(cmd, cwd=self.root, env=self._child_env(key, env_extra), capture_output=True,
                              text=True, timeout=TIMEOUT)
        self.assert_no_key(proc.stdout, proc.stderr)
        return Result(proc.returncode, proc.stdout, proc.stderr, counter)

    def popen(self, runner_argv, *, key=True, **opts) -> tuple[subprocess.Popen, Path]:
        cmd, counter = self._shim_cmd(runner_argv, **opts)
        proc = subprocess.Popen(cmd, cwd=self.root, env=self._child_env(key, None), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        self.addCleanup(self._reap, proc)
        return proc, counter

    def _reap(self, proc: subprocess.Popen) -> None:
        """Release every barrier, then wait for the child, killing it past the deadline."""
        for b in self.barriers:
            if b is not None:
                b.touch()
        if proc.poll() is None:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def convene(self, round_=1, subjects=None, extra=(), prompt=PROMPT, **opts) -> Result:
        return self.shim(self.council_argv(round_, subjects, extra, prompt), **opts)

    def recover(self, *extra, **opts) -> Result:
        return self.shim(self.recover_argv(*extra), key=False, **opts)

    def doc(self, r: Result) -> dict:
        docs = json_docs(r.stdout)
        self.assertTrue(docs and isinstance(docs[0], dict), "no JSON object on stdout:\n" + r.show())
        return docs[0]

    def recovery(self, r: Result) -> dict:
        found = [d for d in json_docs(r.stdout) if isinstance(d, dict) and "recovery" in d]
        self.assertTrue(found, "recovery printed no {\"recovery\": ...} report:\n" + r.show())
        return found[0]

    @staticmethod
    def count(counter: Path) -> int:
        return Result(0, "", "", counter).requests

    def wait_until(self, predicate, what: str) -> None:
        end = time.monotonic() + DEADLINE
        while not predicate():
            if time.monotonic() >= end:
                self.fail(f"timed out after {DEADLINE:g}s waiting until {what}")
            time.sleep(0.02)

    # -- advance-run ------------------------------------------------------------------------

    def rel(self, p) -> str:
        return p if isinstance(p, str) else self.env.rel(p)

    def advance(self, outcome: str, *artifacts):
        cmd = [sys.executable, str(ADVANCE), str(self.env.run_path), "--outcome", outcome,
               "--result", f"behaviour test: {outcome}"]
        if artifacts:
            cmd += ["--artifacts", ",".join(self.rel(a) for a in artifacts)]
        proc = subprocess.run(cmd, cwd=self.root, env=self.base_env(), capture_output=True, text=True,
                              timeout=TIMEOUT)
        self.assertNotIn("Traceback", proc.stderr, proc.stderr)
        docs = json_docs(proc.stdout)
        return proc, (docs[0] if docs and isinstance(docs[0], dict) else {})

    def states(self) -> tuple[dict, object]:
        run = self.env.load_run()
        return {p["n"]: p["state"] for p in run["prompts"]}, run["current_prompt"]

    def assert_passes(self, *artifacts) -> dict:
        """`--outcome done` passes the gate: prompt 3 is `done` and the pointer moves to 4."""
        proc, out = self.advance("done", *artifacts)
        self.assertEqual(proc.returncode, 0, "the gate did not pass:\n" + proc.stdout + proc.stderr)
        self.assertEqual(out["gate"]["verdict"], "pass", out)
        states, current = self.states()
        self.assertEqual((states[PROMPT], current), ("done", PROMPT + 1))
        return out["gate"]

    def assert_does_not_pass(self, *artifacts) -> dict:
        """`--outcome done` is refused and writes nothing; returns the gate verdict."""
        before = self.env.run_path.read_bytes()
        proc, out = self.advance("done", *artifacts)
        self.assertEqual(proc.returncode, 1, "the gate passed or crashed:\n" + proc.stdout + proc.stderr)
        self.assertNotEqual(out.get("gate", {}).get("verdict"), "pass", out)
        self.assertEqual(self.env.run_path.read_bytes(), before, "a refused advance wrote the run snapshot")
        return out.get("gate", {})

    def assert_stops(self, stops: list[int], *artifacts) -> dict:
        """`--outcome blocked` is accepted at a `stop` verdict with `stops`: prompt 3 is `blocked`
        and the pointer stays on it."""
        proc, out = self.advance("blocked", *artifacts)
        self.assertEqual(proc.returncode, 0, "the blocked advance was refused:\n" + proc.stdout + proc.stderr)
        self.assertEqual((out["gate"]["verdict"], out["gate"]["stops"]), ("stop", stops), out["gate"])
        states, current = self.states()
        self.assertEqual((states[PROMPT], current), ("blocked", PROMPT))
        return out["gate"]

    # -- git --------------------------------------------------------------------------------

    def git_bytes(self, *args, check=True) -> bytes:
        proc = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, env=self.base_env(),
                              timeout=TIMEOUT)
        if check and proc.returncode != 0:
            raise AssertionError(f"git {args} failed: {proc.stderr!r}")
        return proc.stdout

    def head(self) -> str:
        return self.git_bytes("rev-parse", "HEAD").decode().strip()

    def head_blob(self, rel: str) -> bytes | None:
        proc = subprocess.run(["git", "-C", str(self.root), "cat-file", "blob", f"HEAD:{rel}"],
                              capture_output=True, env=self.base_env(), timeout=TIMEOUT)
        return proc.stdout if proc.returncode == 0 else None

    def assert_committed(self, rel: str, why: str = "") -> None:
        """HEAD, the index and the working tree hold the same bytes for `rel`."""
        blob = self.head_blob(rel)
        self.assertIsNotNone(blob, f"{rel} is not in HEAD. {why}")
        self.assertEqual(blob, (self.root / rel).read_bytes(), f"{rel}: HEAD differs from the working tree")
        self.assertEqual(self.git_bytes("status", "--porcelain", "--", rel), b"", f"{rel} is not clean")

    def commits_since(self, base: str) -> list[Commit]:
        shas = self.git_bytes("rev-list", "--reverse", f"{base}..HEAD").decode().split()
        out = []
        for sha in shas:
            subject = self.git_bytes("log", "-1", "--format=%s", sha).decode().strip()
            names = self.git_bytes("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", sha)
            out.append(Commit(sha, subject, sorted(n.decode() for n in names.split(b"\0") if n)))
        return out

    def snapshot(self) -> dict:
        """The index, the status and the bytes of everything outside the run directory."""
        prefix = self.run_rel + "/"
        index = [e for e in self.git_bytes("ls-files", "-s", "-z").split(b"\0")
                 if e and not e.split(b"\t", 1)[-1].decode().startswith(prefix)]
        status = [e for e in self.git_bytes("status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
                  if e and not e[3:].decode().startswith(prefix)]
        files = {}
        for rel in (*SRC_FILES, SUBJECT):
            p = self.root / rel
            files[rel] = p.read_bytes() if p.exists() else None
        return {"index": index, "status": status, "files": files}

    def make_unrelated(self) -> None:
        """X staged (new), Y unstaged, Z partially staged, W untracked."""
        (self.root / SRC_STAGED).write_text("staged new\n")
        sup.git(self.root, "add", "--", SRC_STAGED)
        (self.root / SRC_UNSTAGED).write_text("unstaged edit\n")
        (self.root / SRC_PARTIAL).write_text("partial v2 staged\n")
        sup.git(self.root, "add", "--", SRC_PARTIAL)
        (self.root / SRC_PARTIAL).write_text("partial v3 unstaged\n")
        (self.root / SRC_UNTRACKED).write_text("untracked\n")
        snap = self.snapshot()
        # Positive control on the fixture: the four kinds of change are really present.
        status = b"\0".join(snap["status"]).decode()
        for needle in ("A  " + SRC_STAGED, " M " + SRC_UNSTAGED, "MM " + SRC_PARTIAL, "?? " + SRC_UNTRACKED):
            self.assertIn(needle, status)

    # -- council files ----------------------------------------------------------------------

    def council_files(self) -> list[str]:
        if not self.env.council.exists():
            return []
        return sorted(self.env.rel(p) for p in self.env.council.rglob("*") if p.is_file())

    def council_records(self) -> list[str]:
        return sorted(self.env.rel(p) for p in self.env.council.glob("*.json")
                      if not p.name.endswith(".attempt.json"))

    def attempts(self) -> list[str]:
        return sorted(self.env.rel(p) for p in self.env.council.glob("*.attempt.json"))

    def attempt_rel(self, round_=1, ordinal=1) -> str:
        return f"{self.run_rel}/council/{cr.attempt_file_name(sup.RUN_ID, TAG, PROMPT, round_, ordinal)}"

    def diagnostics_files(self) -> list[str]:
        """The run directory's diagnostics log and run-work witness that exist: what the diagnostics
        commit after a council record holds."""
        return sorted(f"{self.run_rel}/{n}" for n in ("council-preflight.jsonl", "run-work-witness.json")
                      if (self.env.run_dir / n).is_file())

    def assert_diagnostics_commit(self, commit: Commit, files: list[str] | None = None) -> None:
        """The one best-effort commit after a council record, with its fixed message and exactly
        the diagnostics files that exist (or `files`)."""
        self.assertEqual(commit.subject, f"crux council diagnostics: {sup.BOOK_ID} {sup.RUN_ID} prompt {PROMPT}")
        self.assertEqual(commit.files, self.diagnostics_files() if files is None else files)

    def pending_files(self) -> list[Path]:
        d = cr.pending_dir(self.root, sup.BOOK_ID, sup.RUN_ID)
        if d is None or not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if p.is_file())

    def diagnostics(self) -> list[dict]:
        path = self.env.run_dir / "council-preflight.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record_doc(self, rel: str) -> dict:
        return json.loads((self.root / rel).read_text(encoding="utf-8"))

    def assert_no_key(self, *texts) -> None:
        """No key value in `texts` or in any file under the run directory or the pending copies.
        Each check is a boolean with a fixed message, so a failure never prints the text it read."""
        for i, t in enumerate(texts):
            self.assertFalse(DUMMY_KEY in t or EXTRA_VALUE in t, f"a key value reached output stream {i}")
        roots = [self.env.run_dir]
        d = cr.pending_dir(self.root, sup.BOOK_ID, sup.RUN_ID)
        if d is not None and d.is_dir():
            roots.append(d)
        for base in roots:
            for p in base.rglob("*"):
                if p.is_file() and not p.is_symlink():
                    data = p.read_bytes()
                    self.assertFalse(DUMMY_KEY.encode() in data or EXTRA_VALUE.encode() in data,
                                     f"a key value reached {p.name}")

    # -- hooks and the witness ----------------------------------------------------------------

    def install_hook(self, mode: str, *args: str) -> None:
        hook_py = self.tmp / "hook.py"
        hook_py.write_text(HOOK_PY, encoding="utf-8")
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        words = " ".join(shlex.quote(w) for w in (sys.executable, str(hook_py), mode, *args))
        hook.write_text(f"#!/bin/sh\nexec {words}\n", encoding="utf-8")
        hook.chmod(0o755)

    def remove_hook(self) -> None:
        (self.root / ".git" / "hooks" / "pre-commit").unlink()

    def write_witness(self, rel: str, data: bytes, prompt: int = 2) -> None:
        """The witness entry the run's writing step records: path and sha256 of the bytes it wrote.
        Built to the committed witness schema and validated against it here."""
        doc = {"record_type": "run-work-witness", "format_version": "1",
               "book": {"id": sup.BOOK_ID, "content_hash": self.env.hash}, "run_id": sup.RUN_ID,
               "entries": [{"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "prompt": prompt,
                            "written_at": "2026-10-02T12:00:00.000001Z"}]}
        errors: list = []
        sup.vp.validate(doc, sup.vp.load_schema(WITNESS_SCHEMA), "#", "#", errors, "run-work-witness")
        self.assertEqual(errors, [])
        (self.env.run_dir / "run-work-witness.json").write_text(json.dumps(doc, indent=2) + "\n")

    def witness_commit(self, rel: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(WITNESS), "commit", str(self.env.run_path), "--path", rel],
                              cwd=self.root, env=self.base_env(), capture_output=True, text=True,
                              timeout=TIMEOUT)

    # -- shared shapes ----------------------------------------------------------------------

    def assert_preflight_refusal(self, r: Result, *, at_prompt: int | None = None) -> dict:
        out = self.doc(r)
        self.assertEqual(out.get("refused"), "preflight",
                         "no structured preflight refusal (the runner wrote a council record instead):\n"
                         + r.show())
        self.assertEqual(r.rc, 1, r.show())
        self.assertIn("record", out)
        self.assertIsNone(out["record"])
        self.assertEqual(out.get("bound"), 3, out)
        if at_prompt is not None:
            self.assertEqual(out.get("refusals_at_prompt"), at_prompt, out)
        self.assertEqual(r.requests, 0, "a preflight refusal made a gateway request")
        return out

    def assert_attempt_open(self, r: Result, attempt: str) -> None:
        out = self.doc(r)
        self.assertEqual(out.get("refused"), "attempt-open",
                         "a second invocation was not refused while the attempt is open:\n" + r.show())
        self.assertEqual(r.rc, 1, r.show())
        self.assertIsNone(out["record"])
        self.assertEqual(out.get("attempt"), attempt, out)
        self.assertEqual(r.requests, 0, "an attempt-open refusal made a gateway request")

    def assert_ran_and_committed(self, r: Result, *, round_=1, before: str | None = None) -> dict:
        """Exit 0, three requests, and the attempt and record committed byte-identical."""
        self.assertEqual(r.rc, 0, r.show())
        self.assertEqual(r.requests, 3, r.show())
        out = self.doc(r)
        self.assertEqual(out.get("attempt"), self.attempt_rel(round_), out)
        self.assert_committed(self.attempt_rel(round_), "The runner claimed no attempt before its requests.")
        self.assert_committed(out["record"], "The runner did not commit its council record.")
        if before is not None:
            commits = self.commits_since(before)
            self.assertEqual([c.files for c in commits[:2]], [[out["attempt"]], [out["record"]]],
                             [(c.subject, c.files) for c in commits])
            self.assertEqual(len(commits), 3, [(c.subject, c.files) for c in commits])
            self.assert_diagnostics_commit(commits[2])
        return out


# ───────────────────────────── 1: preflight refusals ─────────────────────────────


class PreflightRetryTests(_Behaviour):

    def test_a_mistyped_subject_is_refused_before_any_request_and_the_retyped_run_passes(self):
        """Evidence 1. A mistyped subject is refused before any request with no record, no commit
        and one diagnostics line; the retyped run executes, commits twice and passes the gate, and
        no prompt is ever blocked.

        Positive control: the retyped run (same setup, correct path) passes.
        Mutation: write a could-not-run record for `missing` (the old path) -> a record exists in
        council/ and the gate stops instead of the retry passing."""
        before = self.head()
        r = self.convene(subjects=[self.root / TYPO])
        out = self.assert_preflight_refusal(r, at_prompt=1)
        self.assertEqual([(c.get("code"), c.get("repair")) for c in out["causes"]], [("missing", "retype")])
        self.assertEqual(self.council_files(), [], "a refused preflight left a file in council/")
        self.assertEqual(self.head(), before, "a refused preflight committed")
        diag = self.diagnostics()
        self.assertEqual(len(diag), 1, diag)
        self.assertEqual((diag[0].get("event"), diag[0].get("prompt"), diag[0].get("codes")),
                         ("refusal", PROMPT, ["missing"]))
        self.assertNotIn("fixtur", json.dumps(diag[0]), "the diagnostics line carries a path value")

        r2 = self.convene()
        out2 = self.assert_ran_and_committed(r2, before=before)
        subjects = [c.subject for c in self.commits_since(before)]
        self.assertTrue(subjects[0].startswith("crux council attempt:"), subjects)
        self.assertTrue(subjects[1].startswith("crux council record:"), subjects)
        self.assert_passes(out2["record"])
        states, _ = self.states()
        self.assertNotIn("blocked", states.values())

    def test_a_run_written_subject_with_a_matching_witness_is_repaired_and_retried_without_the_owner(self):
        """ADR case. A subject the run wrote and left uncommitted, whose bytes equal its witness
        entry, is refused with the `commit-run-work` repair; the witness writer commits exactly it,
        and the retry passes the gate with no owner stop.

        Positive control: the witness writer's commit lands the run's bytes in HEAD.
        Mutation: ignore the witness -> a `preflight-needs-owner` record instead of the repair."""
        v2 = b"# subject v2, written by the run at prompt 2\n"
        self.env.subject.write_bytes(v2)
        self.write_witness(SUBJECT, v2)
        before = self.head()
        r = self.convene()
        out = self.assert_preflight_refusal(r, at_prompt=1)
        self.assertEqual([c.get("repair") for c in out["causes"]], ["commit-run-work"], out)
        self.assertEqual(self.council_files(), [])
        self.assertEqual(self.head(), before)

        w = self.witness_commit(SUBJECT)
        self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
        self.assertEqual(self.head_blob(SUBJECT), v2)
        self.assertEqual([c.files for c in self.commits_since(before)], [[SUBJECT]])

        out2 = self.assert_ran_and_committed(self.convene())
        self.assert_passes(out2["record"])
        states, _ = self.states()
        self.assertNotIn("blocked", states.values())

    def test_a_run_modified_subject_edited_again_by_someone_else_goes_to_the_owner_and_is_not_committed(self):
        """ADR case. The run wrote the subject (witnessed), then someone else edited it again.
        The runner commits a `preflight-needs-owner` record, never the subject; the second edit
        stays unstaged; the gate stops at 4 and the prompt is blocked with its pointer unchanged.

        Positive control: the subject is a repair candidate by membership (changed in
        base_commit..HEAD and witnessed), so only the byte comparison sends it to the owner.
        Mutation: path membership alone (drop the byte compare) -> `commit-run-work` returned."""
        v2 = b"# subject v2, written by the run at prompt 2\n"
        self.env.subject.write_bytes(v2)
        self.write_witness(SUBJECT, v2)
        v3 = b"# subject v3, edited again by someone else\n"
        self.env.subject.write_bytes(v3)
        head_subject = self.head_blob(SUBJECT)
        before = self.head()
        r = self.convene()
        out = self.doc(r)
        self.assertNotIn("refused", out, "a change outside the conductor's authority was returned as "
                                         "retryable:\n" + r.show())
        self.assertEqual(r.rc, 1, r.show())
        self.assertEqual(r.requests, 0)
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        doc = self.record_doc(recs[0])
        self.assertEqual((doc.get("format_version"), doc["outcome"], doc["refusal_reason"]["code"],
                          doc.get("attempt", "absent")), ("2", "could-not-run", "preflight-needs-owner", None))
        self.assert_committed(recs[0])
        commits = self.commits_since(before)
        self.assertEqual([c.files for c in commits[:1]], [[recs[0]]])
        self.assertEqual(len(commits), 2, [(c.subject, c.files) for c in commits])
        self.assert_diagnostics_commit(commits[1], [f"{self.run_rel}/run-work-witness.json"])
        self.assertEqual(self.head_blob(SUBJECT), head_subject, "the subject was committed")
        self.assertEqual(self.env.subject.read_bytes(), v3)
        self.assertIn(SUBJECT, self.git_bytes("diff", "--name-only").decode())
        self.assertNotIn(SUBJECT, self.git_bytes("diff", "--cached", "--name-only").decode())
        self.assert_stops([4], recs[0])

    def test_the_third_refusal_at_one_prompt_takes_the_escalation_loop_stop(self):
        """Evidence 1, bound. Refusals one and two return `record: null`; a claim between refusals
        resets the count; the third refusal after it commits `preflight-retries-spent`, which the
        gate maps to stop 1 as the deciding record.

        Positive control: the converged round between the refusals runs and resets the count.
        Mutation: skip the count -> the third returns `refused: preflight`; never reset it -> the
        first refusal after the claim already writes the record."""
        typo = self.root / TYPO
        for i in (1, 2):
            self.assert_preflight_refusal(self.convene(subjects=[typo]), at_prompt=i)
        self.assertEqual(self.council_records(), [])
        first = self.assert_ran_and_committed(self.convene())["record"]
        for i in (1, 2):
            self.assert_preflight_refusal(self.convene(round_=2, subjects=[typo]), at_prompt=i)
        r = self.convene(round_=2, subjects=[typo])
        out = self.doc(r)
        self.assertNotIn("refused", out, "the third refusal at the prompt was returned as retryable:\n" + r.show())
        self.assertEqual(r.rc, 1, r.show())
        self.assertEqual(r.requests, 0)
        recs = [p for p in self.council_records() if p != first]
        self.assertEqual(len(recs), 1, recs)
        doc = self.record_doc(recs[0])
        self.assertEqual((doc.get("format_version"), doc["refusal_reason"]["code"], doc.get("attempt", "absent")),
                         ("2", "preflight-retries-spent", None))
        self.assert_committed(recs[0])
        gate = self.assert_stops([1], recs[0])
        self.assertEqual(gate["deciding_record"], recs[0])


# ───────────────────────────── 2: a held result ─────────────────────────────


class HeldResultTests(_Behaviour):

    def test_a_held_round_stops_the_gate_and_a_later_converged_round_cannot_pass_over_it(self):
        """Evidence 2. Round 1 is held (a REQUEST_CHANGES seat with no blocking finding) and stops
        the gate at 1; round 2 converges, and advancing `done` with round 2 attached still stops at 1.

        Positive control: round 2 is a converged record that alone would pass.
        Mutation: count only the deciding record -> round 2 passes over the held round."""
        r1 = self.assert_ran_and_committed(self.convene(decisions=HELD))
        gate = self.assert_does_not_pass(r1["record"])
        self.assertEqual((gate.get("verdict"), gate.get("stops")), ("stop", [1]), gate)
        r2 = self.assert_ran_and_committed(self.convene(round_=2), round_=2)
        gate = self.assert_does_not_pass(r2["record"])
        self.assertEqual((gate.get("verdict"), gate.get("stops")), ("stop", [1]), gate)
        self.assertTrue(any(h.startswith(r1["record"]) for h in gate["holds"]), gate)
        states, current = self.states()
        self.assertEqual((states[PROMPT], current), ("running", PROMPT))

    def test_a_held_result_deleted_before_its_first_commit_leaves_an_open_attempt_no_replacement_can_pass(self):
        """Evidence 2, discard window. A hook rejects the held record's commit; `git clean` deletes
        the working-tree record; the pending copy survives. A replacement round 1 is refused
        `attempt-open`; the gate stops at 4 on the attempt; once the hook is gone, recovery commits
        the held record and the gate stops at 1.

        Positive control: recovery commits the original held bytes, which then hold the gate.
        Mutations: drop the runner's attempt-open refusal and the gate's open-attempt check -> the
        replacement passes; keep the pending copy under the working tree -> recovery `unproven`."""
        self.install_hook("reject-record")
        r = self.convene(decisions=HELD)
        self.assertEqual(r.rc, 2, r.show())
        self.assertEqual(r.requests, 3)
        self.assertIn("--recover", r.stderr)
        att = self.attempt_rel()
        self.assert_committed(att, "No attempt record in HEAD.")
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        self.assertIsNone(self.head_blob(recs[0]))
        pending = self.pending_files()
        self.assertEqual(len(pending), 1, pending)
        held = pending[0].read_bytes()
        self.assertEqual(held, (self.root / recs[0]).read_bytes())

        self.git_bytes("clean", "-fq", "--", self.env.rel(self.env.council))
        self.assertEqual(self.council_records(), [], "git clean did not delete the held record")
        self.assertEqual(self.pending_files(), pending, "the pending copy did not survive git clean")

        r2 = self.convene()
        self.assert_attempt_open(r2, att)
        self.assertEqual(self.council_records(), [])
        self.assert_stops([4], att)

        self.remove_hook()
        rr = self.recover()
        rep = self.recovery(rr)
        self.assertEqual((rep["recovery"], rr.rc, rr.requests), ("committed", 0, 0), rr.show())
        self.assertEqual(self.head_blob(recs[0]), held)
        self.assertEqual((self.root / recs[0]).read_bytes(), held)
        gate = self.assert_does_not_pass(recs[0])
        self.assertEqual((gate.get("verdict"), gate.get("stops")), ("stop", [1]), gate)


# ───────────────────────────── 3: execution stopped after a request ─────────────────────────────


class StoppedAfterRequestTests(_Behaviour):

    def test_a_runner_killed_after_its_first_request_leaves_an_open_attempt(self):
        """Evidence 3. The runner dies on its first request. The attempt is in HEAD with no council
        record; `done` is refused; a rerun is refused `attempt-open`; no diagnostics refusal line
        is written; recovery reports `unproven` and the attempt stays open; `blocked` with the
        attempt stops at 4.

        Positive control: exit 137 with exactly one counted request proves the crash fired; the
        same setup without the crash is the retyped run of the first test, which passes, and that
        test also proves a preflight refusal writes its line to the diagnostics log read here.
        Mutation: claim after the first request, or leave it uncommitted -> no attempt in HEAD and
        the rerun proceeds."""
        before = self.head()
        r = self.convene(exit_at=1)
        self.assertEqual((r.rc, r.requests), (CRASHED, 1), r.show())
        att = self.attempt_rel()
        self.assert_committed(att, "No attempt record in HEAD: nothing was claimed before the first request.")
        self.assertEqual([c.files for c in self.commits_since(before)], [[att]])
        self.assertEqual(self.council_records(), [])
        gate = self.assert_does_not_pass(att)
        self.assertEqual((gate.get("verdict"), gate.get("stops")), ("stop", [4]), gate)

        r2 = self.convene()
        self.assert_attempt_open(r2, att)
        self.assertEqual([d for d in self.diagnostics() if d.get("event") == "refusal"], [],
                         "an attempt-open refusal was counted as a preflight refusal")

        rr = self.recover()
        rep = self.recovery(rr)
        self.assertEqual((rep["recovery"], rr.rc, rr.requests), ("unproven", 1, 0), rr.show())
        self.assert_committed(att)
        self.assertEqual(self.council_records(), [])
        self.assert_stops([4], att)

    def test_a_council_that_raises_during_deliberation_exits_2_with_its_attempt_open(self):
        """ADR case. Deliberation raises after its three requests: exit 2, the attempt committed and
        open, no council record, and the gate stops at 4.

        Positive control: three counted requests prove deliberation ran before it raised.
        Mutation: write nothing before deliberation -> no attempt, and the gate reads nothing."""
        r = self.convene(raise_after=["crux.council.async_council.AsyncCouncil.deliberate"])
        self.assertEqual((r.rc, r.requests), (2, 3), r.show())
        att = self.attempt_rel()
        self.assert_committed(att, "No attempt record in HEAD.")
        self.assertIn("--recover", r.stderr)
        self.assertEqual(self.council_records(), [])
        self.assert_stops([4], att)


# ───────────────────────────── 4: the result commit fails ─────────────────────────────


class PersistenceFailureTests(_Behaviour):

    def test_a_failed_result_commit_is_recovered_from_the_pending_copy_without_a_new_verdict(self):
        """Evidence 4. A hook rejects the record commit: exit 2 naming recovery, the pending copy
        present, the gate does not pass. With the hook removed, recovery (no key) commits the
        pending bytes, makes no request, removes the pending copy, and the gate passes.

        Positive control: the same record passes once recovery commits it.
        Mutation: recovery reconvenes -> six requests (and, with no key, it cannot)."""
        self.install_hook("reject-record")
        r = self.convene()
        self.assertEqual((r.rc, r.requests), (2, 3), r.show())
        self.assertIn("--recover", r.stderr)
        self.assert_committed(self.attempt_rel(), "No attempt record in HEAD.")
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        self.assertIsNone(self.head_blob(recs[0]))
        pending = self.pending_files()
        self.assertEqual(len(pending), 1, pending)
        original = pending[0].read_bytes()
        self.assert_does_not_pass(recs[0])

        self.remove_hook()
        head0 = self.head()
        rr = self.recover()
        rep = self.recovery(rr)
        self.assertEqual((rep["recovery"], rr.rc, rr.requests), ("committed", 0, 0), rr.show())
        self.assertEqual(self.head_blob(recs[0]), original)
        self.assertEqual(self.pending_files(), [])
        self.assertEqual([c.files for c in self.commits_since(head0)], [[recs[0]]])
        self.assert_passes(recs[0])


# ───────────────────────────── 5: the commit landed, the runner died ─────────────────────────────


class CommitLandedTests(_Behaviour):

    def test_a_runner_that_died_after_its_result_commit_is_recognised_without_deliberating(self):
        """Evidence 5. The runner dies after its verified record commit, before it reports.
        Recovery recognises the commit, reprints the summary, exits 0, makes no commit and no
        request, and removes the pending copy; the gate passes.

        Positive control: exit 137 with the record already in HEAD and its pending copy still
        present proves the crash fell between the commit and the report.
        Mutation: recovery re-commits or reconvenes -> HEAD moves or the count grows."""
        r = self.convene(crash_at=["council_commit.remove_pending:1"])
        self.assertEqual((r.rc, r.requests), (CRASHED, 3), r.show())
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        self.assert_committed(self.attempt_rel())
        self.assert_committed(recs[0])
        self.assertEqual(len(self.pending_files()), 1)

        head0 = self.head()
        rr = self.recover()
        rep = self.recovery(rr)
        self.assertEqual((rep["recovery"], rr.rc, rr.requests), ("recognised", 0, 0), rr.show())
        self.assertIn(recs[0], rr.stdout, "recovery did not reprint the summary naming the record")
        self.assertEqual(self.head(), head0, "recovery committed again")
        self.assertEqual(self.pending_files(), [])
        self.assert_passes(recs[0])


# ───────────────────────────── 6: two invocations of one round ─────────────────────────────


class DuplicateInvocationTests(_Behaviour):

    def test_two_invocations_of_one_round_execute_once(self):
        """Evidence 6. A waits on a barrier at its first request; B, the same round, is refused
        `attempt-open` with no request; A, released, exits 0. One attempt and one council record
        are in scope and the gate passes.

        Positive control: B at round 2, after A completes, runs.
        Mutation: drop the attempt-open refusal and the exclusive create -> six requests."""
        barrier = self.tmp / "release-a"
        a, a_counter = self.popen(self.council_argv(), block_at=1, barrier=barrier)
        self.wait_until(lambda: self.count(a_counter) >= 1 or a.poll() is not None, "A makes its first request")
        self.assertIsNone(a.poll(), "A ended before it reached its first request")
        b = self.convene()
        self.assert_attempt_open(b, self.attempt_rel())
        barrier.touch()
        a_out, a_err = a.communicate(timeout=TIMEOUT)
        self.assert_no_key(a_out, a_err)
        self.assertEqual((a.returncode, self.count(a_counter)), (0, 3), a_out + a_err)
        self.assertEqual(self.attempts(), [self.attempt_rel()])
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        self.assertEqual(json_docs(a_out)[0]["record"], recs[0])
        self.assert_passes(recs[0])

        # The gate passed, so the run is at prompt 4, the module's address-findings prompt, and
        # `--prompt` names the prompt the run is at.
        control = self.assert_ran_and_committed(self.convene(round_=2, prompt=PROMPT + 1), round_=2)
        self.assertNotEqual(control["record"], recs[0])

    def test_two_simultaneous_invocations_race_and_one_executes(self):
        """Evidence 6, race. Two invocations of round 1 start together from one barrier: exactly
        one exits 0 and the other is refused `attempt-open`; three requests in total.

        Positive control: the winner's exit 0 with three requests.
        Mutation: drop the attempt-open refusal and the exclusive create -> two exits 0, six requests."""
        start, release = self.tmp / "start", self.tmp / "release"
        a, ca = self.popen(self.council_argv(), start_barrier=start, block_at=1, barrier=release)
        b, cb = self.popen(self.council_argv(), start_barrier=start, block_at=1, barrier=release)
        start.touch()
        self.wait_until(lambda: a.poll() is not None or b.poll() is not None
                        or (self.count(ca) >= 1 and self.count(cb) >= 1), "one invocation ends or both request")
        release.touch()
        results = []
        for proc, counter in ((a, ca), (b, cb)):
            out, err = proc.communicate(timeout=TIMEOUT)
            self.assert_no_key(out, err)
            docs = json_docs(out)
            results.append((proc.returncode, docs[0] if docs else {}, self.count(counter), out + err))
        winners = [x for x in results if x[0] == 0]
        losers = [x for x in results if x[0] == 1 and x[1].get("refused") == "attempt-open"]
        self.assertEqual((len(winners), len(losers)), (1, 1), [(x[0], x[3]) for x in results])
        self.assertIsNone(losers[0][1]["record"])
        self.assertEqual((winners[0][2], losers[0][2]), (3, 0))
        self.assertEqual(self.attempts(), [self.attempt_rel()])
        self.assertEqual(len(self.council_records()), 1)


# ───────────────────────────── 7: a hook alters the record ─────────────────────────────


class HookAlteredRecordTests(_Behaviour):

    def test_a_hook_that_reformats_the_record_is_a_mismatch_that_cannot_pass(self):
        """Evidence 7. A hook rewrites the record with indent 4 and re-adds it. The runner exits 2
        on the mismatch; the committed and pending records parse equal and differ in bytes; `done`
        is refused; `blocked` with the attempt stops at 4; recovery reports `mismatch`.

        The gate is asked twice with the record's index and working tree synced to HEAD, which a
        conductor may do with `git checkout HEAD -- <record>`, so only the committed bytes are
        under test. First
        with the pending copy present: the seal and the pending-copy comparison each refuse. Then
        with the pending copy set aside, as in a clone, which never carries one: the seal alone
        refuses.

        Positive control: the committed blob exists and parses equal to the pending copy, so the
        hook ran and only the bytes differ; after the checkout HEAD, index and working tree agree.
        Mutations: verify by git exit code -> exit 0; drop the gate's seal check -> `pass` with
        no pending copy; drop the seal check and the pending-copy comparison -> `pass`."""
        self.install_hook("reformat")
        r = self.convene()
        self.assertEqual((r.rc, r.requests), (2, 3), r.show())
        self.assertIn("mismatch", r.stderr.lower())
        self.assertIn("--recover", r.stderr)
        att = self.attempt_rel()
        self.assert_committed(att)
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        committed = self.head_blob(recs[0])
        self.assertIsNotNone(committed, "the hook-altered commit did not land")
        pending = self.pending_files()
        self.assertEqual(len(pending), 1, pending)
        original = pending[0].read_bytes()
        self.assertEqual(json.loads(committed), json.loads(original))
        self.assertNotEqual(committed, original)
        self.assert_does_not_pass(recs[0])

        # `checkout HEAD --`: the index still holds the runner's bytes, so a plain `checkout --`
        # would restore those instead of HEAD's.
        self.git_bytes("checkout", "HEAD", "--", recs[0])
        self.assert_committed(recs[0], "the checkout did not sync the record to HEAD")
        self.assertEqual((self.root / recs[0]).read_bytes(), committed)
        self.assert_does_not_pass(recs[0])
        aside = self.tmp / "pending-aside.json"
        os.replace(pending[0], aside)
        try:
            self.assertEqual(self.pending_files(), [])
            self.assert_does_not_pass(recs[0])
        finally:
            os.replace(aside, pending[0])
        self.assertEqual(pending[0].read_bytes(), original)

        self.assert_stops([4], att)
        rr = self.recover()
        rep = self.recovery(rr)
        self.assertEqual((rep["recovery"], rr.rc, rr.requests), ("mismatch", 1, 0), rr.show())

    def test_a_hook_reformat_and_a_crash_before_verification_reports_a_mismatch_and_stops_at_four(self):
        """ADR case. The reformatting hook runs and the runner dies at the record commit's
        verification. Recovery reports `mismatch`; the gate stops at 4 and is advanced `blocked`;
        once the hook is gone, `--owner-commit-pending` commits the pending bytes and the gate passes.

        Positive control: the owner's remedy makes the original bytes pass.
        Mutation: recovery trusts HEAD over the pending copy -> `recognised`."""
        self.install_hook("reformat")
        r = self.convene(crash_at=["council_commit.verify_commit:2"])
        self.assertEqual((r.rc, r.requests), (CRASHED, 3), r.show())
        att = self.attempt_rel()
        self.assert_committed(att)
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        pending = self.pending_files()
        self.assertEqual(len(pending), 1, pending)
        original = pending[0].read_bytes()
        self.assertNotEqual(self.head_blob(recs[0]), original)

        rr = self.recover()
        rep = self.recovery(rr)
        self.assertEqual((rep["recovery"], rr.rc, rr.requests), ("mismatch", 1, 0), rr.show())
        self.assert_stops([4], att)

        self.remove_hook()
        rr2 = self.recover("--owner-commit-pending")
        rep2 = self.recovery(rr2)
        self.assertEqual((rep2["recovery"], rr2.rc, rr2.requests), ("committed", 0, 0), rr2.show())
        self.assertEqual(self.head_blob(recs[0]), original)
        self.assert_passes(recs[0])


# ───────────────────────────── 8: unrelated changes ─────────────────────────────


class UnrelatedChangesTests(_Behaviour):

    def test_unrelated_changes_stay_outside_both_council_commits(self):
        """Evidence 8. Staged X, unstaged Y, partially staged Z and untracked W exist before the
        run. The attempt commit holds only the attempt; the record commit holds only the record and
        its retained copies; X, Y, Z and W are exactly as before; the gate passes.

        Positive control: `make_unrelated` asserts the four kinds of change are present, and the
        record carries at least one retained copy.
        Mutation: `git add -A` or `commit -a` -> X lands in a council commit."""
        self.make_unrelated()
        snap = self.snapshot()
        before = self.head()
        r = self.convene(extra=["--retain-subjects"])
        self.assertEqual((r.rc, r.requests), (0, 3), r.show())
        out = self.doc(r)
        commits = self.commits_since(before)
        self.assertEqual(len(commits), 3, [(c.subject, c.files) for c in commits])
        self.assertEqual(commits[0].files, [self.attempt_rel()])
        self.assert_diagnostics_commit(commits[2], [f"{self.run_rel}/council-preflight.jsonl"])
        doc = self.record_doc(out["record"])
        retained = {f"{self.run_rel}/{s['retained_copy']}" for s in doc["subjects"] if s["retained_copy"]}
        self.assertTrue(retained, "the record carries no retained copy")
        self.assertEqual(set(commits[1].files), {out["record"]} | retained)
        self.assertEqual(self.snapshot(), snap)
        self.assert_passes(out["record"])

    def test_a_failed_attempt_commit_makes_no_request_and_moves_nothing_else(self):
        """ADR case. A hook rejects the attempt commit: no request, exit 2, HEAD unchanged, no
        council record, and the index and working tree outside the attempt as they were.

        Positive control: the hook rejects only attempt records, and the first test's run shows
        the same setup committing an attempt when no hook refuses it.
        Mutation: proceed after a failed attempt commit -> requests are made."""
        self.make_unrelated()
        self.install_hook("reject-attempt")
        snap = self.snapshot()
        before = self.head()
        r = self.convene()
        self.assertEqual(r.requests, 0, "requests were made without a committed attempt:\n" + r.show())
        self.assertEqual(r.rc, 2, r.show())
        self.assertEqual(self.head(), before)
        self.assertEqual(self.council_records(), [])
        self.assertEqual(self.snapshot(), snap)

    def test_a_preflight_record_commit_holds_only_its_record(self):
        """ADR case. An unwitnessed dirty subject goes to the owner: the `preflight-needs-owner`
        commit holds only its record; the subject and X, Y, Z, W are as they were.

        Positive control: the record is committed (the commit happened and was isolated).
        Mutation: `git add -A` or `commit -a` -> the subject or X lands in the preflight commit."""
        self.make_unrelated()
        self.env.subject.write_bytes(b"# subject edited, with no witness\n")
        snap = self.snapshot()
        head_subject = self.head_blob(SUBJECT)
        before = self.head()
        r = self.convene()
        out = self.doc(r)
        self.assertNotIn("refused", out, r.show())
        self.assertEqual((r.rc, r.requests), (1, 0), r.show())
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        self.assertEqual(self.record_doc(recs[0])["refusal_reason"]["code"], "preflight-needs-owner")
        self.assert_committed(recs[0])
        self.assertEqual([c.files for c in self.commits_since(before)], [[recs[0]]])
        self.assertEqual(self.snapshot(), snap)
        self.assertEqual(self.head_blob(SUBJECT), head_subject)

    def test_recoverys_commit_holds_only_the_original_record_and_its_failure_moves_nothing(self):
        """ADR case. Recovery's commit fails while the hook still refuses: HEAD, the index and the
        working tree outside the record are unchanged and the pending copy stays. With the hook
        removed, recovery's commit holds only the original record and moves nothing else.

        Positive control: the second recovery commits, so the first one's failure is the hook's.
        Mutation: `git add -A` or `commit -a` in recovery -> X lands in its commit."""
        self.make_unrelated()
        self.install_hook("reject-record")
        r = self.convene()
        self.assertEqual((r.rc, r.requests), (2, 3), r.show())
        recs = self.council_records()
        self.assertEqual(len(recs), 1, recs)
        pending = self.pending_files()
        self.assertEqual(len(pending), 1, pending)
        original = pending[0].read_bytes()
        snap = self.snapshot()
        head0 = self.head()

        rr = self.recover()
        rep = self.recovery(rr)
        self.assertEqual((rep["recovery"], rep.get("code"), rep.get("commit_landed", False), rr.rc, rr.requests),
                         ("commit-refused", "hook-or-commit-failed", False, 1, 0), rr.show())
        self.assertEqual(self.head(), head0)
        self.assertEqual(self.snapshot(), snap)
        self.assertEqual(self.pending_files(), pending)
        self.assertIsNone(self.head_blob(recs[0]))

        self.remove_hook()
        rr2 = self.recover()
        rep = self.recovery(rr2)
        self.assertEqual((rep["recovery"], rr2.rc, rr2.requests), ("committed", 0, 0), rr2.show())
        self.assertEqual([c.files for c in self.commits_since(head0)], [[recs[0]]])
        self.assertEqual(self.head_blob(recs[0]), original)
        self.assertEqual(self.snapshot(), snap)

    def test_a_hook_that_stashes_unstaged_changes_keeps_them(self):
        """ADR risk. A hook sets unstaged changes aside and restores them around each commit, as a
        pre-commit framework does. Either both commits land and X, Y, Z, W are intact and the gate
        passes, or the runner exits 2 naming the mismatch; never a silent loss.

        Positive control: three requests and two runner commits in the passing branch.
        Mutation: no outside-change verification -> a lost change with exit 0."""
        self.make_unrelated()
        self.install_hook("stash")
        snap = self.snapshot()
        before = self.head()
        r = self.convene()
        self.assertEqual(r.requests, 3, r.show())
        if r.rc == 0:
            out = self.doc(r)
            commits = self.commits_since(before)
            self.assertEqual([c.files for c in commits[:2]], [[self.attempt_rel()], [out["record"]]],
                             "the runner did not make its two commits")
            self.assertEqual(len(commits), 3, [(c.subject, c.files) for c in commits])
            self.assert_diagnostics_commit(commits[2])
            self.assertEqual(self.snapshot(), snap, "an unrelated change was lost with exit 0")
            self.assert_passes(out["record"])
        else:
            self.assertEqual(r.rc, 2, r.show())
            self.assertIn("mismatch", r.stderr.lower())
            self.assertIn("--recover", r.stderr)

    def test_a_hook_that_drops_an_unstaged_change_is_reported_never_silent(self):
        """ADR risk, the loss branch of the stash hook. The hook sets unstaged changes aside and
        never restores them, as a framework that died between the two steps would. The runner
        observes the loss at its first commit and reports it: exit 2 naming the mismatch and the
        recovery step, and no request. It never exits 0 over a lost change.

        Positive control: the hook really dropped Y's unstaged change (the working tree lost it).
        Mutation: no outside-change verification -> exit 0 or a request made over the loss."""
        self.make_unrelated()
        self.install_hook("stash-drop")
        before = self.head()
        r = self.convene()
        self.assertEqual((self.root / SRC_UNSTAGED).read_text(), "unstaged v1\n",
                         "control: the hook did not drop the unstaged change")
        self.assertEqual(r.rc, 2, "a lost outside change was not reported:\n" + r.show())
        self.assertIn("mismatch", r.stderr.lower())
        self.assertIn("working-tree changes outside the owned paths moved", r.stderr)
        self.assertIn("--recover", r.stderr)
        self.assertIn(self.attempt_rel(), r.stderr)
        self.assertEqual(r.requests, 0, "a request was made after the attempt commit lost a change")
        self.assertEqual(self.council_records(), [])
        self.assertEqual([c.files for c in self.commits_since(before)], [[self.attempt_rel()]])

    def test_the_request_counter_sees_clients_built_without_the_runners_transport(self):
        """The counter's positive control for every `requests == 0` assertion on recovery, which
        the runner calls with no transport. `--self-check` makes three requests that name no
        transport: `httpx.get`, an `httpx.AsyncClient` post and a raw socket connect.

        All three name the loopback discard port, so none can leave the machine even when a
        guard is missing.
        Mutation: drop the shim's client routing -> `httpx.get` is refused at the socket and the
        shim exits 1; drop the socket count -> two requests counted."""
        r = self.shim([], key=False, self_check=True)
        self.assertEqual((r.rc, r.requests), (0, 3), r.show())
        models = [json.loads(line)["model"] for line in r.counter.read_text().splitlines() if line.strip()]
        self.assertEqual(models, ["<GET 127.0.0.1>", "self-check/one", "<socket>"])

    def test_a_hooks_environment_carries_no_key(self):
        """ADR case. A hook dumps its environment at each runner commit: no key value, no
        OPENROUTER_API_KEY name, and no name from the crux env file.

        Positive control: the dump exists for both commits and carries a marker variable set in
        the runner's environment, so the hook inherits that environment; the runner's environment
        holds the key and the env-file name.
        Mutation: build the git child environment with `git_isolated_env()` -> the key is present."""
        dump = self.tmp / "hook-env.jsonl"
        self.install_hook("env-dump", str(dump))
        (self.home / "env").write_text(f"{EXTRA_NAME}={EXTRA_VALUE}\n", encoding="utf-8")
        r = self.convene(env_extra={EXTRA_NAME: EXTRA_VALUE, MARKER: "present"})
        self.assertEqual((r.rc, r.requests), (0, 3), r.show())
        self.assertTrue(dump.is_file(), "the hook never ran: the runner made no commit")
        text = dump.read_text(encoding="utf-8")
        envs = [json.loads(line) for line in text.splitlines() if line.strip()]
        self.assertGreaterEqual(len(envs), 2, "the hook did not run at both runner commits")
        # Names only and booleans only: a failure here must never print the hook's environment,
        # which is the developer's environment minus what the scrub removed.
        for i, e in enumerate(envs):
            self.assertTrue(e.get(MARKER) == "present", f"dump {i}: the hook did not inherit the runner's environment")
            self.assertEqual(sorted({"OPENROUTER_API_KEY", EXTRA_NAME} & set(e)), [],
                             f"dump {i}: a key name reached the hook")
        self.assertFalse(DUMMY_KEY in text or EXTRA_VALUE in text, "a key value reached the hook")


if __name__ == "__main__":
    unittest.main()
